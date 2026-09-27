"""One compact relation per source node; no configuration/path worklist."""
from dataclasses import dataclass, replace

import z3

from .lazy_expressions import Equations, Value, Status, Term, scalar, text_id, failure, boolean_value
from .lazy_scalar_store import StoreEquations
from .lazy_message_operations import MessageOperationCompiler, truth
from .lazy_constraints import ConstraintEquations, ErrorList
from .lazy_storage import source_storage_schema


@dataclass(frozen=True)
class State:
    store: object
    messages: object
    control: object
    machines: dict
    engine_time: object
    pending_completion: object
    inputs: dict
    identity_counter: object
    error: object = Status.Success


@dataclass(frozen=True)
class NodeRelation:
    state: State
    successor: object
    status: object
    outcome: str
    events: tuple
    decision_inputs: tuple = ()


class NodeCompiler:
    def __init__(self, execution, dt, prefix='source', schema=None):
        self.execution, self.dt = execution, dt
        self.graph = execution['decision_transition']
        self.schema = schema or source_storage_schema(self.graph)
        self.eq = Equations(prefix)
        self.store = StoreEquations(self.eq, self.graph)
        self.messaging = MessageOperationCompiler(self.eq, self.graph, self.schema)
        self.constraints = ConstraintEquations(self.eq, self.store, self.messaging.messages)
        self.ids = self.messaging.node_ids
        self.input_names = sorted({key for node in self.graph['nodes'].values()
                                   if node['operation']=='decision' for key in node['data']['inputs']})

    def empty(self):
        return State(self.store.empty(), self.messaging.messages.empty(), self.messaging.control.empty(),
                     {}, z3.FPVal(0.0,z3.Float64()), z3.BoolVal(False), {}, z3.IntVal(0))

    def symbolic(self, prefix, facts=None):
        return State(self.store.symbolic(prefix + '_store', facts), self.messaging.messages.symbolic(prefix + '_messages'),
                     self.messaging.control.symbolic(prefix + '_control'),
                     {owner:z3.Int(prefix+'_machine_'+str(i)) for i,owner in enumerate(self.graph['machine_states'])},
                     z3.FP(prefix+'_time',z3.Float64()),z3.Bool(prefix+'_pending'),
                     {name:(z3.Bool(prefix+'_input_present_'+str(i)),Term(z3.Const(prefix+'_input_'+str(i),Value)))
                      for i,name in enumerate(self.input_names)},z3.Int(prefix+'_identity_counter'),
                     z3.Const(prefix+'_error',Status))

    def initialize(self, state, data, overrides):
        store=self.store; value=state.store
        for parameter in data['parameters']:
            key=parameter['qualified_name']
            term=overrides.get(parameter['cli_name'],overrides.get(key,Term(scalar(parameter['value']))))
            value=store.write(value,key,term)
        machines={name:z3.IntVal(text_id(label)) if label is not None else z3.IntVal(0)
                  for name,label in data['machine_initial_states'].items()}
        for owner, attributes in self.graph['part_attribute_types'].items():
            for name,kind in attributes.items():
                key=owner+'::'+name
                if kind in ('Boolean','Real'):
                    value=store.write(value,key,Term(scalar(False if kind=='Boolean' else 0.0)),
                                      z3.Not(store.cell(value,key).present))
        for owner,labels in self.graph['machine_states'].items():
            for label in labels:
                value=store.write(value,owner+'::behavior.'+label,
                                  Term(Value.Boolean(machines[owner]==text_id(label))))
        for key in data['stored_aliases']:
            value=store.write(value,key,Term(Value.Absent),kind=1)
        for key in self.graph['initial_transition_targets']:
            value=store.write(value,key,Term(scalar(0.0)),z3.Not(store.cell(value,key).present))
        for row in data['constraints']['bindings']:
            value=store.write(value,row['target'],Term(Value.Absent),kind=2)
        rows=[r for r in data['values'] if r['kind']=='initial' and 'expression' in r]
        for row in rows: value=store.write(value,row['target'],Term(Value.Absent),present=False)
        pending=[z3.BoolVal(True) for _ in rows]
        status=Status.Success
        for _ in rows:
            if z3.is_false(z3.simplify(z3.Or(*pending))): break
            before=list(pending)
            for i,row in enumerate(rows):
                enabled=z3.And(pending[i],status==Status.Success)
                term=store.evaluate(value,row['expression'],row['target'].rsplit('::',1)[0],strict=True)
                missing=z3.And(Status.is_Failure(term.status),
                               Status.exception_type(term.status)==text_id('MissingReference'))
                success=z3.And(enabled,term.status==Status.Success)
                value=store.write(value,row['target'],term,success)
                pending[i]=self.eq.native(z3.And(pending[i],z3.Not(success)))
                status=self.eq.native(z3.If(z3.And(enabled,term.status!=Status.Success,z3.Not(missing)),term.status,status))
            stuck=z3.And(status==Status.Success,z3.Or(*pending),*[a==b for a,b in zip(before,pending)])
            errors=ErrorList.empty()
            for row,waiting in zip(rows,pending):
                errors=self.constraints.error_add(errors,row['target'],z3.IntVal(0),waiting,len(rows),False)
            status=self.eq.native(z3.If(stuck,self.constraints.error_status(errors,3,len(rows)),status))
        value,later=self.constraints.solve(value,machines,data['constraints'],status==Status.Success)
        return replace(state,store=value,machines=machines),self.eq.native(z3.If(status==Status.Success,later,status))

    def lower(self, identity, state, *, action=None, overrides=None):
        node=self.graph['nodes'][identity];op,data,edges=node['operation'],node['data'],node['successors']
        self.eq.identity_counter=state.identity_counter
        status=Status.Success;events=();outcome='continue'
        successor=z3.IntVal(self.ids[edges['next']]) if 'next' in edges else z3.IntVal(self.ids[identity])
        store=self.store
        if op in self.messaging.supported:
            def raw(key):
                cell=store.cell(state.store,key)
                return z3.And(cell.present,z3.Or(cell.kind!=0,truth(cell.value)))
            result=self.messaging.lower(identity,state.messages,state.control,state.machines,raw)
            value=state.store
            for key,field,guard in result.scalar_writes:
                value=store.write(value,key,Term(field.value,identity=field.identity),guard)
            state=replace(state,store=value,messages=result.messages,control=result.control,machines=result.machines)
            successor,status=result.successor,result.status
        elif op=='initialize_existing_runtime':
            state,status=self.initialize(state,data,overrides or {})
        elif op in ('no_op','perform','begin_cycle'):
            pass
        elif op in ('assign','declare_attribute'):
            if data['expression'] is not None:
                term=store.evaluate(state.store,data['expression'],data['context'])
                enabled=z3.And(term.status==Status.Success,z3.Not(Value.is_Absent(term.value)))
                status=term.status
                key=data.get('source_storage_target',data['target'])
                if op=='declare_attribute':
                    state=replace(state,store=store.write(state.store,key,term,enabled))
                else:
                    path=data['source_target']
                    local=z3.BoolVal(False)
                    if path[0] in self.schema.local_names:
                        messages,local=self.messaging.write_local_attribute(state.messages,state.control,
                            path[0],'.'.join(path[1:]),term.value,term.identity,enabled)
                        state=replace(state,messages=messages)
                    targets,error=store.canonical_targets(state.store,key,enabled)
                    status=self.eq.native(z3.If(status==Status.Success,error,status))
                    value=state.store
                    for guard,target in targets:
                        capacity=target.rsplit('::',1)[0]+'::capacity'
                        params=self.graph['nodes'][self.graph['initial_entry']]['data']['parameters']
                        clamp=any(p['qualified_name']==capacity and p['value'] is not None for p in params)
                        cell=store.cell(value,target)
                        error=z3.If(z3.And(clamp,z3.Not(local)),failure('implicit capacity clamp is not a source assignment'),
                            z3.If(z3.And(cell.present,cell.kind==2),failure('assignment to bound feature '+target),Status.Success))
                        status=self.eq.native(z3.If(z3.And(guard,status==Status.Success),error,status))
                        value=store.write(value,target,term,z3.And(guard,status==Status.Success))
                    state=replace(state,store=value)
        elif op in ('branch','machine_guard'):
            term=store.evaluate(state.store,data['condition'],data.get('context',''))
            status=z3.If(z3.And(term.status==Status.Success,op=='branch',z3.Not(Value.is_Boolean(term.value))),
                         failure('source action guard is not Boolean'),term.status)
            successor=z3.If(truth(term.value),self.ids[edges['true']],self.ids[edges['false']])
        elif op=='solve_source_constraints':
            value,status=self.constraints.solve(state.store,state.machines,data)
            state=replace(state,store=value)
        elif op=='set_dt':
            state=replace(state,store=store.write(state.store,'dt',Term(scalar(self.dt))))
        elif op=='advance_engine_time':
            state=replace(state,engine_time=self.eq.native(z3.fpAdd(z3.RNE(),state.engine_time,z3.FPVal(self.dt,z3.Float64()))))
        elif op=='check_all_requirements':
            rows=[]
            for name in data['properties']:
                term=store.evaluate(state.store,data['expressions'][name],strict=True)
                error=z3.If(z3.And(term.status==Status.Success,z3.Not(Value.is_Boolean(term.value))),
                            failure('source requirement result is not Boolean'),term.status)
                caught=z3.And(Status.is_Failure(error),z3.Or(*[
                    Status.exception_type(error)==text_id(kind)
                    for kind in ('ValueError','MissingReference','TypeError','KeyError','ZeroDivisionError')]))
                status=self.eq.native(z3.If(z3.And(status==Status.Success,error!=Status.Success,z3.Not(caught)),error,status))
                rows.append((name,Term(term.value,error,term.identity)))
            events=((data['boundary'],status==Status.Success,tuple(rows)),)
        elif op=='decision':
            inputs={}
            for name,expr in data['inputs'].items():
                term=store.evaluate(state.store,expr,data['context'])
                status=self.eq.native(z3.If(status==Status.Success,term.status,status));inputs[name]=term
            invalid=set(inputs)!=set(data['expected_inputs'])
            missing=z3.Or(invalid,*[Value.is_Absent(v.value) for v in inputs.values()])
            status=self.eq.native(z3.If(z3.And(status==Status.Success,missing),failure('missing, extra or undefined source Neural input'),status))
            completion=inputs.get(data['completion_input'],Term(Value.Absent))
            status=self.eq.native(z3.If(z3.And(status==Status.Success,z3.Not(Value.is_Boolean(completion.value))),
                                       failure('source Completion input is not Boolean'),status))
            merged={}
            for name in self.input_names:
                present,old=state.inputs.get(name,(z3.BoolVal(False),Term(Value.Absent)))
                new=inputs.get(name,Term(Value.Absent))
                merged[name]=(self.eq.native(z3.If(status==Status.Success,z3.BoolVal(name in inputs),present)),
                    self.eq.bind(z3.If(status==Status.Success,new.value,old.value)))
            state=replace(state,inputs=merged,pending_completion=self.eq.native(z3.If(status==Status.Success,
                                                  boolean_value(completion.value),state.pending_completion)))
            outcome='decision'
        elif op=='apply_executed_action':
            if action is None: status=failure('missing executed controller action')
            else:
                value=state.store
                for name,target in data['outputs'].items():
                    if name in action: value=store.write(value,target,action[name])
                state=replace(state,store=value)
        elif op=='completion_test':
            successor=z3.If(state.pending_completion,self.ids[edges['true']],self.ids[edges['false']])
        elif op=='outcome':
            outcome='error' if data['outcome']=='execution_error' else data['outcome']
        else:
            raise ValueError('missing compact source operation: '+op)
        successor=self.eq.native(z3.If(status==Status.Success,successor,self.ids[node['on_exception']]))
        state=replace(state,identity_counter=self.eq.identity_counter,
                      error=self.eq.native(z3.If(status==Status.Success,state.error,status)))
        return NodeRelation(state,successor,status,outcome,events,
                            tuple(inputs.items()) if op == 'decision' else ())

    @property
    def representation_obligations(self):
        return (*self.messaging.messages.obligations,*self.messaging.control.obligations,*self.constraints.obligations)
