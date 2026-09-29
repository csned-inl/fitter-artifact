"""Compile the finite source graph once, with symbolic storage at each node."""
import hashlib
import json
import time

import z3

from .lazy_expressions import Value, Term, scalar
from .lazy_program import NodeCompiler
from .lazy_storage import source_storage_schema


def fields(compiler, state):
    """Stable source-derived relation signature, including explicit frame fields."""
    result = []
    def add(path, value): result.append((path, value))
    for key in compiler.store.keys:
        cell = state.store.cells[key]
        for name in ('present','value','identity','kind','order'):
            if name == 'value' and hasattr(compiler, 'scalar_layout'):
                from .type_facts import packed_value
                for suffix, expression in packed_value(cell.value, compiler.scalar_layout[key]):
                    add('store/'+key+'/'+suffix, expression)
            else:
                add('store/'+key+'/'+name,getattr(cell,name))
    add('store/next_order',state.store.next_order)
    for name in ('types','present','values','identities','orders','lengths'):
        add('messages/'+name,getattr(state.messages,name))
    for port in compiler.schema.mailbox_types:
        box=state.messages.queues[port[0]]
        add('queue/'+port[0]+'/length',box.length)
        add('queue/'+port[0]+'/references',box.references)
    for name in ('depth','locals_id','local_values','saved_locals','returns','current','matched','ports'):
        add('control/'+name,getattr(state.control,name))
    for owner in compiler.graph['machine_states']:
        add('machine/'+owner,state.machines.get(owner,z3.IntVal(0)))
    add('engine_time',state.engine_time);add('pending_completion',state.pending_completion)
    for name in compiler.input_names:
        present,term=state.inputs.get(name,(z3.BoolVal(False),Term(Value.Absent)))
        add('input/'+name+'/present',present);add('input/'+name+'/value',term.value)
    add('identity_counter',state.identity_counter);add('error',state.error)
    return result


def actions(graph):
    kinds={}
    for node in graph['nodes'].values():
        if node['operation']!='apply_executed_action':continue
        for name,kind in node['data']['output_types'].items():
            if name in kinds and kinds[name]!=kind:raise ValueError('conflicting action types: '+name)
            kinds[name]=kind
    constructors={'Boolean':(Value.Boolean,z3.BoolSort()),'Integer':(Value.Integer,z3.IntSort()),
                  'Real':(Value.Float,z3.Float64())}
    return {name:Term(constructors[kind][0](z3.Const('executed_action_'+str(i),constructors[kind][1])))
            for i,(name,kind) in enumerate(sorted(kinds.items()))}


def scenario_overrides(compiler):
    profile=compiler.graph.get('scenario_profile',{})
    if profile.get('error'):raise ValueError('unsupported source scenario profile: '+profile['error'])
    bounds=profile.get('bounds',{})
    if not bounds:return {},[]
    enabled=z3.Bool('source_sampled_scenario')
    values={};premises=[]
    for i,(key,bound) in enumerate(bounds.items()):
        lo,hi=bound['lower'],bound['upper']
        if lo==hi:term=Term(scalar(lo))
        else:
            integer=type(lo) is int and type(hi) is int
            value=z3.Int('scenario_'+str(i)) if integer else z3.FP('scenario_'+str(i),z3.Float64())
            term=Term(Value.Integer(value) if integer else Value.Float(value))
            if integer:condition=z3.And(value>=lo,value<=hi)
            else:condition=z3.And(z3.fpGEQ(value,z3.FPVal(lo,z3.Float64())),z3.fpLEQ(value,z3.FPVal(hi,z3.Float64())))
            premises.append(z3.Implies(enabled,condition))
        values[key]=term
    for target,source in profile.get('state_bindings',[]):values[target]=values[source]
    parameters=compiler.graph['nodes'][compiler.graph['initial_entry']]['data']['parameters']
    overrides={}
    for parameter in parameters:
        key=parameter['qualified_name'];cli=parameter['cli_name']
        term=values.get(cli,values.get(key))
        if term is not None:
            overrides[key]=Term(z3.If(enabled,term.value,scalar(parameter['value'])))
    return overrides,premises


def compile_graph(execution, dt, progress=None, *, specialize=False):
    if specialize:
        from .type_facts import derive_type_facts, check_type_facts, choose_scalar_layout
        generic = compile_graph(execution, dt, progress)
        facts = derive_type_facts(generic)
        check_type_facts(generic, facts)
        layout = choose_scalar_layout(generic, facts)
        evidence = {'method': 'checked_constructor_dataflow_v1',
                    'generic_equations': generic['equations'],
                    'scalar_layout': {key: list(kinds) for key, kinds in layout.items()},
                    'facts': {name: [{'location': key, 'field': field, 'values': sorted(values)}
                                     for (key, field), values in sorted(entries.items())]
                              for name, entries in facts.items()}}
        del generic
        # Expression lookup caches retain closures over their source stores.
        # Release that cyclic reference graph before constructing its replacement.
        import gc
        gc.collect()
        result = _compile_graph(execution, dt, progress, facts)
        for row in result['nodes'].values():
            row['compiler'].scalar_layout = layout
        result['type_evidence'] = evidence
        return result
    return _compile_graph(execution, dt, progress)


def _compile_graph(execution, dt, progress=None, facts=None):
    graph=execution['decision_transition'];schema=source_storage_schema(graph)
    action=actions(graph);literal_identities={};compiled={};started=time.monotonic()
    total_equations=0
    for index,identity in enumerate(sorted(graph['nodes'])):
        compiler=NodeCompiler(execution,dt,prefix='node_'+str(index),schema=schema)
        compiler.eq.literal_identities=literal_identities
        initial=identity==graph['initial_entry']
        before=compiler.empty() if initial else compiler.symbolic('node_'+str(index)+'_in',
                                    facts[identity] if facts is not None else None)
        overrides,premises=scenario_overrides(compiler) if initial else ({},[])
        relation=compiler.lower(identity,before,action=action,overrides=overrides)
        compiled[identity]={'compiler':compiler,'input':before,'relation':relation,'premises':premises}
        total_equations+=len(compiler.eq.equations)
        if progress:progress({'source_node':identity,'compiled_nodes':len(compiled),
                             'source_nodes':len(graph['nodes']),'equations':total_equations,
                             'seconds':round(time.monotonic()-started,3)})
    return {'graph':graph,'schema':schema,'nodes':compiled,'actions':action,
            'equations':total_equations,'seconds':time.monotonic()-started,
            'claim':'source-node lowering; representation and theorem obligations remain to be checked'}
