"""Compose source transitions with checked per-location constant elimination."""
import z3
from .lazy_expressions import Status
from .lazy_graph import fields
from .lazy_substitution import PreparedSubstitution
from .source_layout import ConstantStateLayout


def construct_sparse(query):
    from .lazy_solver import Output, Events, successors, variables
    if hasattr(query, 'fixedpoint'):
        if getattr(query, 'relation_layout', None) != 'checked_constants_per_location_v1':
            raise ValueError('source relation layout is already fixed')
        return query
    facts = query.checked_constants()
    layout = ConstantStateLayout(query.program, facts)
    query.relation_layout = 'checked_constants_per_location_v1'
    query.flatten_state = True
    query.constant_layout = layout
    query.fixedpoint = z3.Fixedpoint()
    # Keep eager inlining enabled: the omitted-state regression returns
    # false UNSAT with it disabled on the workstation Z3 build.
    query.fixedpoint.set(engine='spacer', **{'xform.inline_eager':True,'xform.inline_linear':False})
    action_sorts = [value.sort() for value in query.actions]
    reachable, transition, steps, after_paths = {}, {}, {}, {}
    for i,(name,row) in enumerate(query.rows.items()):
        targets = set(successors(query.graph,name,query.returns))
        if row['relation'].outcome == 'decision':
            targets.add(query.graph['nodes'][name]['successors']['resume'])
        # A shared local step returns only fields consumed by any successor.
        # Destination constants follow from the checked edge invariant.
        paths = sorted(set().union(*(set(layout.paths[t]) for t in targets)))
        after_paths[name] = paths
        values = dict(fields(row['compiler'],row['relation'].state))
        prefix=query.prefix+'_'+str(i)
        reachable[name]=z3.Function(prefix+'_reach',*layout.sorts[name],*action_sorts,z3.BoolSort())
        transition[name]=z3.Function(prefix+'_transition',*layout.sorts[name],*action_sorts,Output,Events,z3.BoolSort())
        steps[name]=z3.Function(prefix+'_step',*layout.sorts[name],*action_sorts,
                               *[values[path].sort() for path in paths],
                               z3.IntSort(),Status,Events,z3.BoolSort(),z3.BoolSort())
        query.fixedpoint.register_relation(reachable[name],transition[name],steps[name])
    query.bad=z3.Function(query.prefix+'_bad',z3.BoolSort())
    query.fixedpoint.register_relation(query.bad)
    root=query.graph['initial_entry']
    row=query.rows[root]
    query.rule(reachable[root](*layout.pack(root,row['compiler'],row['input']),*query.actions))
    output=z3.Const(query.prefix+'_out',Output)
    tail=z3.Const(query.prefix+'_events',Events)
    for name,row in query.rows.items():
        compiler,relation=row['compiler'],row['relation']
        before=layout.pack(name,compiler,row['input'])
        after=dict(fields(compiler,relation.state))
        paths=after_paths[name]
        equations=query.node_equations(name)
        query.rule(steps[name](*before,*query.actions,*[after[p] for p in paths],
                              relation.successor,relation.status,query.events(relation,Events.Empty),
                              z3.And(*query.obligations(name))),*equations)
        before_vars=tuple(z3.Const(query.prefix+'_before_'+name+'_'+str(i),sort)
                          for i,sort in enumerate(layout.sorts[name]))
        after_vars={p:z3.Const(query.prefix+'_after_'+name+'_'+str(i),after[p].sort())
                    for i,p in enumerate(paths)}
        next_node=z3.Int(query.prefix+'_next_'+name)
        status=z3.Const(query.prefix+'_status_'+name,Status)
        events=z3.Const(query.prefix+'_emitted_'+name,Events)
        valid=z3.Bool(query.prefix+'_valid_'+name)
        step=steps[name](*before_vars,*query.actions,*[after_vars[p] for p in paths],next_node,status,events,valid)
        reached=reachable[name](*before_vars,*query.actions)
        query.rule(query.bad(),reached,step,z3.Not(valid))
        if len(relation.events)>1:
            raise ValueError('source node emits multiple boundary events')
        emitted=z3.If(Events.is_Empty(events),tail,
            Events.Event(Events.boundary(events),Events.values(events),Events.statuses(events),tail))
        for target in successors(query.graph,name,query.returns):
            guard=next_node==compiler.ids[target]
            if relation.outcome in ('decision','terminal','error'):
                guard=z3.And(guard,status!=Status.Success)
            target_values=[after_vars[p] for p in layout.paths[target]]
            query.rule(reachable[target](*target_values,*query.actions),reached,step,guard)
            query.rule(transition[name](*before_vars,*query.actions,output,emitted),step,guard,
                       transition[target](*target_values,*query.actions,output,tail))
        if name in query.output:
            query.rule(transition[name](*before,*query.actions,query.output[name],
                                        query.events(relation,Events.Empty)),*equations,relation.status==Status.Success)
        if relation.outcome=='decision':
            native=variables(*query.actions)
            replacement=PreparedSubstitution([(v,z3.Const(query.prefix+'_fresh_'+str(i),v.sort()))
                                              for i,v in enumerate(native)])
            target=query.graph['nodes'][name]['successors']['resume']
            query.rule(reachable[target](*[after_vars[p] for p in layout.paths[target]],
                                          *[replacement(v) for v in query.actions]),
                       reached,step,status==Status.Success)
    left_output,right_output=z3.Consts(query.prefix+'_ol '+query.prefix+'_or',Output)
    left_events,right_events=z3.Consts(query.prefix+'_el '+query.prefix+'_er',Events)
    shared={v.get_id() for v in variables(*query.actions)}
    for left in query.boundaries:
        row=query.rows[left]
        a=layout.pack(left,row['compiler'],row['input'])
        for right in query.boundaries:
            row=query.rows[right]
            b=layout.pack(right,row['compiler'],row['input'])
            eq=query.node_equations(right)
            other=PreparedSubstitution([(v,z3.Const(query.prefix+'_right_'+str(v.get_id()),v.sort()))
                for v in variables(*b,*query.projections[right],*eq) if v.get_id() not in shared])
            query.rule(query.bad(),reachable[left](*a,*query.actions),
                reachable[right](*[other(v) for v in b],*query.actions),
                transition[left](*a,*query.actions,left_output,left_events),
                transition[right](*[other(v) for v in b],*query.actions,right_output,right_events),
                *query.node_equations(left),*[other(e) for e in eq],
                *[x==other(y) for x,y in zip(query.projections[left],query.projections[right])],
                z3.Or(left_output!=right_output,left_events!=right_events))
    query.step_relations,query.reachable_relations=steps,reachable
    return query
