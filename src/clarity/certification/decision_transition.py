"""Compose the source programs into a decision-to-decision control-flow relation.

This is a proof representation, not an executor. Nodes use the existing runtime's
operations and retain their ordering, continuations, exceptional outcomes and
loops. A decision transition is a *path* through this graph, not one simulator
cycle and not a simultaneous assignment of all sample/physical values.

The equation solver must discharge this relation before it can replace it with
closed-form next-state equations. Building this object does not discharge that
obligation or change the runtime.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict
import hashlib
from pathlib import Path

from .ordered_execution import expression_record, fingerprint, resolve_source_key

VERSION = 1


def runtime_identity():
    """Bind the relation to the implementation whose ordering it describes."""
    root = Path(__file__).resolve().parents[1]
    names = ('sysml/simulator.py', 'sysml/simulator_adapter.py', 'sysml/parser.py',
             'sysml/parser_values.py', 'runtime/requirements.py', 'runtime/env.py',
             'training/reduced/buffered_env.py')
    return {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
            for name in names}


class Composer:
    def __init__(self, parser, inventory):
        self.parser = parser
        self.inventory = inventory
        self.nodes = {}
        self.events = {}
        self.decisions = []
        self.writers = defaultdict(list)
        self.readers = defaultdict(list)
        self.bound = {a.qualified_name: a for a in parser.derived_attributes}
        # Port lookup has no state effects. Reuse the runtime's routing rule.
        from clarity.sysml.simulator import SimulationEngine
        self.router = SimulationEngine(parser)

    def key(self, context, path):
        key = resolve_source_key(self.parser, context, path)
        seen = set()
        while key in self.parser.parsed_bindings:
            if key in seen:
                raise ValueError(f'cyclic stored alias {key}')
            seen.add(key)
            key = self.parser.parsed_bindings[key]
        return key

    def expression(self, expression, context, active=()):
        """Resolve names without expanding stored samples into physical values."""
        if expression is None:
            return None
        result = dict(expression)
        if expression['kind'] == 'reference':
            key = self.key(context, expression['path'])
            result['storage'] = key
            result['version'] = 'node_entry'
            # Runtime name resolution is ordered and depends on availability.
            # Retain all candidates; a missing local may fall back to a system
            # feature, whereas a present-but-unavailable binding does not.
            path = expression['path']
            ref = self.parser.ref_bindings.get(context + '::' + path[0]) if path else None
            absolute = (ref.split('::') + path[1:]) if ref else path
            candidates = (["::".join(absolute)] if ref else [])
            candidates += ([context + '::' + '.'.join(path)] if context else [])
            candidates += ['.'.join(path), '::'.join(absolute)]
            candidates += ([context + '::' + '::'.join(path)] if context else [])
            candidates += [self.parser.system_part + '::' + '::'.join(path)]
            result['lookup_order'] = list(dict.fromkeys(candidates))
            result['lookup_rule'] = 'first key present; resolve stored aliases/live expressions at this node'
            if key in self.bound:
                if key in active:
                    raise ValueError(f'cyclic live expression {key}')
                attr = self.bound[key]
                result['live_expression'] = self.expression(
                    expression_record(attr.expression), attr.context, active + (key,))
        for key in ('operand', 'left', 'right', 'condition', 'true', 'false'):
            if key in expression:
                result[key] = self.expression(expression[key], context, active)
        return result

    def refs(self, value):
        result = set()
        if isinstance(value, dict):
            if 'storage' in value:
                result.add(value['storage'])
                result.update(value.get('lookup_order', []))
            for child in value.values():
                result.update(self.refs(child))
        elif isinstance(value, (list, tuple)):
            for child in value:
                result.update(self.refs(child))
        return result

    def node(self, identity, operation, successors=None, *, writes=(), reads=(), **data):
        if identity in self.nodes:
            raise ValueError(f'duplicate transition node {identity}')
        record = {'operation': operation, 'successors': successors or {},
                  'data': data, 'writes': sorted(set(writes)),
                  'reads': sorted(self.refs(data) | set(reads)),
                  'read_version': 'node_entry', 'write_version': 'node_exit',
                  'frame': 'all unmodified storage retains its entry value',
                  'on_exception': 'execution_error'}
        self.nodes[identity] = record
        for key in record['writes']:
            self.writers[key].append(identity)
        for key in record['reads']:
            self.readers[key].append(identity)
        return identity

    def payload_fields(self, type_name):
        # Accept/trigger can select a subtype with additional fields.
        return sorted({field for candidate, fields in self.parser.item_def_attrs.items()
                       if self.router._is_subtype(candidate, type_name) for field in fields})

    def block(self, events, context, identity, continuation, *, fresh=False):
        entry = continuation
        for event in reversed(events):
            entry = self.event(event, entry)
        declarations = {e['data']['name']: e['data']['type'] for e in events
                        if e['kind'] == 'ItemDeclStmt'}
        return self.node(identity, 'enter_block', {'next': entry},
                         writes=('$locals',), reads=(() if fresh else ('$locals',)), context=context, fresh_locals=fresh,
                         declarations=declarations,
                         item_initial_value={'attrs': {}},
                         declaration_order='before the first statement in this block')

    def event(self, event, continuation):
        identity, context, kind, data = (event[k] for k in
                                       ('event_id', 'context', 'kind', 'data'))
        self.events[identity] = kind
        common = {'source_event': identity, 'context': context}
        edges = {'next': continuation}
        if kind == 'IfStmt':
            yes = self.block(event['children'], context, identity + '/true/entry', continuation)
            no = self.block(event['alternatives'], context, identity + '/false/entry', continuation)
            return self.node(identity, 'branch', {'true': yes, 'false': no},
                             condition=self.expression(data['condition'], context),
                             require_boolean=True, **common)
        if kind == 'PerformStmt':
            entry = self.block(event['children'], context, identity + '/body/entry', continuation)
            return self.node(identity, 'perform', {'next': entry}, action=data['action'], **common)
        if kind == 'AssignStmt':
            # Assignments use the literal context path plus stored aliases;
            # reference-parameter rewriting is a read operation in this runtime.
            target = context + '::' + '::'.join(data['source_target'])
            raw_target = target
            seen = set()
            while target in self.parser.parsed_bindings:
                if target in seen:
                    raise ValueError(f'cyclic assignment alias {target}')
                seen.add(target)
                target = self.parser.parsed_bindings[target]
            return self.node(identity, 'assign', edges, writes=(target, '$locals'),
                             reads=(target, '$locals'),
                             target=target, source_storage_target=raw_target,
                             source_target=list(data['source_target']), expression=self.expression(data['expression'], context),
                             local_payload_write='also update matching current local item',
                             undefined_result='no write',
                             bound_target='error', implicit_capacity_clamp='error', **common)
        if kind == 'AttributeDeclStmt':
            target = context + '::' + data['name']
            return self.node(identity, 'declare_attribute', edges, writes=(target,),
                             target=target, expression=self.expression(data['expression'], context),
                             undefined_result='no write', **common)
        if kind == 'SendStmt':
            port = context + '::' + data['port'].replace('.', '::')
            destination = self.router._find_connected_port(port)
            recipient = next((inst for inst in self.parser.instance_state_machines
                              if destination and destination.startswith(inst + '::')), None)
            if recipient:
                after_send = self.node(identity + '/dispatch', 'call_machine',
                                       {'call': recipient + '/machine/entry', 'return': continuation},
                                       writes=('$stack',), reads=('$stack', '$locals', '$machine_frame'), instance=recipient)
            else:
                after_send = continuation
            return self.node(identity, 'send_copy', {'sent': after_send, 'absent': continuation},
                             writes=(('$mailbox:' + destination,) if destination else ()),
                             reads=('$locals',) + (('$mailbox:' + destination,) if destination else ()),
                             payload=data['payload'], sender_port=port, destination=destination,
                             copy='current local item type and a copy of its current attrs',
                             queue_update='replace same concrete type at its index, otherwise append',
                             absent='unknown local item or unconnected port', **common)
        if kind == 'AcceptStmt':
            port = context + '::' + data['port'].replace('.', '::')
            fields = self.payload_fields(data['type'])
            targets = [context + '::' + data['destination'] + '::' + field.replace('.', '::')
                       for field in fields] if data['destination'] else []
            return self.node(identity, 'accept_copy', {'accepted': continuation, 'blocked': 'execution_error'},
                             writes=(*targets, '$locals', '$mailbox:' + port,
                                     '$mailbox:' + port.rsplit('::', 1)[0]),
                             reads=('$locals', '$mailbox:' + port, '$mailbox:' + port.rsplit('::', 1)[0]),
                             port=port, parent_fallback='only when full-path mailbox is empty',
                             expected_type=data['type'], destination=data['destination'],
                             selection='first compatible subtype in mailbox order',
                             copy='present payload attributes only; store local item then remove message',
                             fields=fields, **common)
        if kind == 'Decision':
            definition = self.router._find_action_def(context, data['type'])
            completion = [p.name for p in definition.in_params if 'Completion' in p.metadata]
            if len(completion) != 1:
                raise ValueError('decision composition requires the existing unique Completion input')
            resume = self.node(identity + '/resume', 'apply_executed_action', edges,
                               writes=tuple(context + '::' + data['name'] + '::' + name
                                            for name in data['outputs']),
                               reads=('$executed_action',),
                               outputs={name: context + '::' + data['name'] + '::' + name
                                        for name in data['outputs']},
                               output_types=data['outputs'], action_convention='executed')
            self.decisions.append({'request': identity, 'resume': resume, 'context': context})
            return self.node(identity, 'decision', {'resume': resume},
                             writes=('$pending_completion', '$inputs'),
                             inputs={name: self.expression(expr, context) for name, expr in data['inputs'].items()},
                             expected_inputs=[p.name for p in definition.in_params],
                             completion_input=completion[0], stop_before_action=True,
                             latch_completion='before pausing; terminal only after cycle_end', **common)
        if kind in {'ItemDeclStmt', 'InParamStmt', 'OutParamStmt'}:
            return self.node(identity, 'no_op', edges, declaration=data, **common)
        raise ValueError(f'uncomposed source event {kind}: {identity}')

    def machine(self, context, machine, programs):
        return_node = self.node(context + '/machine/return', 'return',
                                writes=('$stack', '$locals', '$machine_frame'), reads=('$stack',))
        next_transition = return_node
        for index in reversed(range(len(machine.transitions))):
            transition = machine.transitions[index]
            prefix = context + '/machine/' + str(index)
            finish = self.node(prefix + '/finish', 'finish_machine_transition', {'next': return_node},
                               writes=('$machine:' + context, '$mailboxes'),
                               reads=('$machine_frame', '$mailboxes'),
                               instance=context, to=transition.to_state,
                               consume='remove matched item by equality if still present, after do-action')
            body = programs[(context, transition.name)]['events']
            entry = self.block(body, context, prefix + '/body', finish, fresh=True)
            if transition.guard is not None:
                entry = self.node(prefix + '/guard', 'machine_guard', {'true': entry, 'false': next_transition},
                                  condition=self.expression(expression_record(transition.guard), context),
                                  truth_test='runtime truth value; no extra Boolean restriction')
            trigger_writes = ['$machine_frame']
            if transition.trigger and not transition.trigger_port:
                trigger_writes.append(context + '::' + transition.trigger)
            if transition.trigger_var:
                trigger_writes.extend(context + '::' + transition.trigger_var + '::' + field.replace('.', '::')
                                      for field in self.payload_fields(transition.trigger))
            triggered = self.node(prefix + '/trigger', 'match_trigger',
                                  {'matched': entry, 'absent': next_transition}, writes=trigger_writes,
                                  reads=('$mailboxes', '$machine_frame') +
                                        ((context + '::' + transition.trigger,) if transition.trigger and not transition.trigger_port else ()),
                                  instance=context, type=transition.trigger, port=transition.trigger_port,
                                  destination=transition.trigger_var,
                                  selection='first compatible subtype; absent trigger matches unconditionally',
                                  before_guard='copy trigger attrs, or clear the legacy flag',
                                  guard_failure='retain copied attrs/cleared flag; try the next transition')
            next_transition = self.node(prefix + '/from', 'machine_from_state',
                                        {'true': triggered, 'false': next_transition},
                                        reads=('$machine_frame',),
                                        instance=context, expected=transition.from_state,
                                        current='state saved at machine entry')
        self.node(context + '/machine/entry', 'enter_machine', {'next': next_transition},
                  writes=('$machine_frame',), instance=context,
                  reads=('$machine:' + context, '$machine_frame'),
                  order='source transition order; return after first fired transition',
                  save='current state, matched message and caller locals on this call frame')

    def constraint_data(self):
        constraints = [c for c in self.parser.parsed_constraints
                       if 'ScenarioConstraint' not in getattr(c, 'metadata', [])]
        return {
            'constraints': [{'name': c.name, 'context': c.context,
                             'expression': self.expression(expression_record(c.expression), c.context)}
                            for c in constraints],
            'bindings': [{'target': a.qualified_name,
                          'expression': self.expression(expression_record(a.expression), a.context)}
                         for a in self.parser.derived_attributes],
            'flows': [asdict(f) for f in self.parser.flows],
            'algorithm': {
                'behavior_predicates': 'refresh from current machine states before solving',
                'bindings': 'install live expressions; do not snapshot them',
                'order': 'implication constraints, then other constraints, then flows',
                'assignment': 'evaluate equality RHS against current store, then write LHS',
                'conjunction': 'execute left then right',
                'implication': 'apply consequent assignments only if guard is true',
                'flow': 'copy single-source fields except targets already defined by a constraint',
                'ambiguous_flow': 'execution error',
                'iteration_limit': len(constraints) + len(self.parser.flows) + 2,
                'convergence': 'equal store before/after a pass AND all source constraints validate true',
                'unresolved_or_nonconvergent': 'execution error, never an identity update',
            },
        }

    def constraint_targets(self, data):
        targets = set(self.bound)
        def walk(expr):
            if expr.get('kind') == 'binary':
                if expr['operator'] == '==' and expr['left']['kind'] == 'reference':
                    targets.add(expr['left']['storage'])
                elif expr['operator'] == 'implies':
                    walk(expr['right'])
                elif expr['operator'] == 'and':
                    walk(expr['left']); walk(expr['right'])
        for constraint in data['constraints']:
            walk(constraint['expression'])
        keys = set(self.inventory.value_types) | set(self.writers)
        for flow in self.parser.flows:
            start = self.parser.system_part + '::' + flow.from_port.replace('.', '::') + '::'
            end = self.parser.system_part + '::' + flow.to_port.replace('.', '::') + '::'
            targets.update(self.key('', (end + key[len(start):]).split('::'))
                           for key in keys if key.startswith(start))
        targets.update(k for k in keys if 'behavior.' in k)
        targets.update('behavior.' + state.name for machine in self.parser.instance_state_machines.values()
                       for state in machine.states)
        return targets

    def compose(self):
        from clarity.runtime.env import (_extract_scenario_bounds,
                                         _extract_scenario_state_bindings)
        try:
            scenario_profile = {'bounds': _extract_scenario_bounds(self.parser),
                                'state_bindings': _extract_scenario_state_bindings(self.parser)}
        except ValueError as exc:
            scenario_profile = {'error': str(exc)}
        self.node('execution_error', 'outcome', outcome='execution_error', preserves_error=True)
        self.node('terminal', 'outcome', outcome='terminal', response_applied=True)
        self.node('cycle/terminal', 'completion_test',
                  {'true': 'terminal', 'false': 'cycle/entry'}, reads=('$pending_completion',))
        requirements = [r['name'] for r in self.inventory.property_inventory]
        requirement_expressions = {
            r.name: self.expression(expression_record(r.expression), r.context)
            for r in self.parser.parsed_requirements if r.name in requirements}
        self.node('cycle/check', 'check_all_requirements', {'next': 'cycle/terminal'},
                  writes=('$requirement_ledger',), boundary='cycle_end', properties=requirements,
                  reads=('$requirement_ledger', '$engine_time'), expressions=requirement_expressions,
                  accumulation='retain every false and evaluation error, including previous cycles')
        self.node('cycle/time', 'advance_engine_time', {'next': 'cycle/check'},
                  writes=('$engine_time',), reads=('$engine_time', '$configured_dt'),
                  expression='entry engine.time + configured dt', arithmetic='runtime numeric operations')
        entry = 'cycle/time'
        step_programs = [p for p in self.inventory.programs if p['kind'] == 'step']
        for index in reversed(range(len(step_programs))):
            program = step_programs[index]
            entry = self.block(program['events'], program['context'], f'cycle/step/{index}', entry, fresh=True)
        self.node('cycle/dt', 'set_dt', {'next': entry}, writes=('dt',), reads=('$configured_dt',), value='configured dt')
        machine_programs = {(p['context'], p['name']): p for p in self.inventory.programs
                            if p['kind'] == 'state_machine_transition'}
        for context, machine in self.parser.instance_state_machines.items():
            self.machine(context, machine, machine_programs)
        constraints = self.constraint_data()
        self.node('cycle/solve', 'solve_source_constraints', {'next': 'cycle/dt'},
                  reads=tuple('$machine:' + context for context in self.parser.instance_state_machines),
                  writes=self.constraint_targets(constraints), **constraints)
        entry = 'cycle/solve'
        for index, context in reversed(list(enumerate(self.parser.instance_state_machines))):
            entry = self.node(f'cycle/machine/{index}', 'call_machine',
                              {'call': context + '/machine/entry', 'return': entry},
                              writes=('$stack',), reads=('$stack', '$locals', '$machine_frame'), instance=context)
        self.node('cycle/entry', 'begin_cycle', {'next': entry})
        self.node('initial/check', 'check_all_requirements', {'next': 'cycle/entry'},
                  writes=('$requirement_ledger',), boundary='initialization', properties=requirements,
                  reads=('$requirement_ledger', '$engine_time'), expressions=requirement_expressions,
                  accumulation='retain every false and evaluation error')
        self.node('initial/entry', 'initialize_existing_runtime', {'next': 'initial/check'},
                  writes=('$state', '$machines', '$mailboxes', '$pending_completion', '$engine_time'),
                  reads=('$initial_overrides',),
                  values=list(self.inventory.initial_values),
                  parameters=[asdict(p) for p in self.parser.parameters],
                  declared_types=dict(self.inventory.value_types),
                  stored_aliases=dict(self.parser.parsed_bindings),
                  machine_initial_states={k: m.initial_state for k, m in self.parser.instance_state_machines.items()},
                  source_initialization='existing parser_values.initialization_values; no operator reinterpretation',
                  order=['parameters and scenario overrides', 'initial machine states',
                         'missing part Boolean=False and Real=0.0; no implicit Integer default',
                         'per-instance behavior predicates', 'stored aliases',
                         'missing direct transition assignment targets=0.0',
                         'live derived expressions', 'dependency-ordered initial expressions',
                         'initial constraint solve'],
                  constraints=constraints, pending_completion=False, engine_time=0.0)
        self.validate_edges()
        storage = set(self.inventory.value_types) | set(self.writers) | set(self.readers) | {'$state', '$machines', '$mailboxes'}
        fields = {key: {'declared_type': self.inventory.value_types.get(key),
                        'writers': sorted(self.writers[key]), 'readers': sorted(self.readers[key]),
                        'otherwise': 'retain previous storage, including unavailable values',
                        'initial_availability': 'determined only by initial/entry and executed writes'}
                  for key in sorted(storage)}
        record = {
            'version': VERSION, 'runtime_sha256': runtime_identity(),
            'reference_bindings': dict(self.parser.ref_bindings),
            'stored_aliases': dict(self.parser.parsed_bindings),
            'item_type_parents': dict(self.parser.item_type_parents),
            'connections': [list(pair) for pair in self.parser.connects],
            'system_part': self.parser.system_part,
            'scenario_profile': scenario_profile,
            'part_attribute_types': {
                context: dict(self.parser.part_defs[instance.part_type].attributes)
                for context, instance in self.parser.part_instances.items()
            },
            'machine_states': {context: [state.name for state in machine.states]
                               for context, machine in self.parser.instance_state_machines.items()},
            'initial_transition_targets': sorted({
                context + '::' + '::'.join(statement.target)
                for context, machine in self.parser.instance_state_machines.items()
                for transition in machine.transitions for statement in (transition.do_action or [])
                if type(statement).__name__ == 'AssignStmt'
            }),
            'nodes': self.nodes, 'storage': fields,
            'source_events': dict(sorted(self.events.items())),
            'decisions': sorted(self.decisions, key=lambda d: d['request']),
            'initial_entry': 'initial/entry', 'cycle_entry': 'cycle/entry',
            'initial_configuration': {
                'source': 'fresh SimulationEngine and RequirementLedger created by SimulatorTwin.prepare',
                'state': {}, 'machine_states': {}, 'ordered_mailboxes': {},
                'local_items': {}, 'call_stack': [], 'machine_frames': [],
                'engine_time': 0.0, 'pending_completion': False,
                'requirement_ledger': {'sequence': 0, 'pending_events': []},
                'arguments': ['configured dt', 'existing scenario overrides'],
            },
            'composition': {
                'configuration': ['node', 'state', 'local_items', 'call_stack', 'machine_frames',
                                  'machine_states', 'ordered_mailboxes', 'engine_time',
                                  'pending_completion', 'requirement_ledger'],
                'step': 'apply node operation to its entry configuration and follow its selected successor',
                'items': 'retain runtime object sharing on accept/trigger; send creates a fresh attrs copy',
                'call': 'save continuation and local/machine frames; return restores them',
                'decision_transition': 'resume executed action; stop at first next decision, terminal, or execution error',
                'initial_transition': 'initial_entry to first decision, terminal, or execution error',
                'path_length': 'arbitrary finite length; no cycle-count bound',
                'infinite_path': 'nontermination retained; no next decision invented',
                'state_version': 'each successor reads the preceding node exit, including across cycles',
                'numeric_domain': 'source declarations retained; runtime operations retained; arithmetic correspondence unproved',
                'solver_discharge': 'required; this graph is not a solver proof',
            },
        }
        record['sha256'] = fingerprint(record)
        return record

    def validate_edges(self):
        for identity, node in self.nodes.items():
            for target in node['successors'].values():
                if target not in self.nodes:
                    raise ValueError(f'unresolved continuation {identity} -> {target}')
        if not self.decisions:
            raise ValueError('source has no composed controller decision')


def compose_decision_transition(parser, inventory):
    return Composer(parser, inventory).compose()


def validate_decision_transition(record, parser, inventory):
    """Reconstruct every branch/effect from current inputs, not a claimed hash."""
    expected = compose_decision_transition(parser, inventory)
    return [] if record == expected else ['decision transition differs from source/runtime composition']


def compose_path(record, path, *, entry=None):
    """Give a finite graph path explicit read/write versions for solver lowering.

    This is relational composition, not execution or satisfiability checking.
    The returned branch/operation conditions still have to be proved. Looping
    paths can be any length; callers must not infer progress from a finite path.
    A prefix may end at any node, so it is explicitly labelled as a prefix rather
    than being silently treated as a complete decision transition.
    """
    if not path:
        raise ValueError('cannot compose an empty path')
    entry = entry or record['initial_entry']
    if path[0] != entry:
        raise ValueError('path does not start at the specified configuration')
    versions = {key: 0 for key in record['storage']}
    relations, stack = [], []
    for index, identity in enumerate(path):
        if identity not in record['nodes']:
            raise ValueError(f'unknown transition node {identity}')
        node = record['nodes'][identity]
        successor = path[index + 1] if index + 1 < len(path) else None
        if successor and node['operation'] == 'decision':
            raise ValueError('a transition path must stop at the next decision')
        if successor:
            if node['operation'] == 'return':
                if not stack or successor != stack.pop():
                    raise ValueError('machine return does not match its calling continuation')
                branch = ['return']
            elif node['operation'] == 'call_machine':
                if successor != node['successors']['call']:
                    raise ValueError('path skipped called machine')
                stack.append(node['successors']['return'])
                branch = ['call']
            else:
                branch = [key for key, target in node['successors'].items() if target == successor]
                if successor == node['on_exception']:
                    branch.append('exception')
                if not branch:
                    raise ValueError(f'path skipped source operations: {identity} -> {successor}')
        else:
            branch = []
        read_versions = dict(versions)
        writes = set(node['writes'])
        # Containers and their fields are two views of the same storage. A
        # field write updates its container version without changing siblings.
        whole_state = '$state' in writes
        whole_mailboxes = '$mailboxes' in writes
        whole_machines = '$machines' in writes
        if any(not key.startswith('$') for key in writes): writes.add('$state')
        if any(key.startswith('$mailbox:') for key in writes): writes.add('$mailboxes')
        if any(key.startswith('$machine:') for key in writes): writes.add('$machines')
        if whole_state:
            writes.update(key for key in versions if not key.startswith('$'))
        if whole_mailboxes:
            writes.update(key for key in versions if key.startswith('$mailbox:'))
        if whole_machines:
            writes.update(key for key in versions if key.startswith('$machine:'))
        for key in writes:
            versions[key] += 1
        relations.append({'node': identity, 'operation': node['operation'],
                          'condition': {'selected_successors': branch, 'operation_must_succeed': 'exception' not in branch},
                          'inputs': read_versions,
                          'outputs': {key: versions[key] for key in sorted(writes)},
                          'operation_data': node['data'],
                          'unchanged': sorted(set(versions) - writes)})
    final = record['nodes'][path[-1]]['operation']
    return {'relations': relations, 'final_versions': versions,
            'outcome': final if final in {'decision', 'outcome'} else 'prefix',
            'satisfiability': 'not checked', 'covers_all_paths': False}
