"""Checked basic blocks for the source decision-transition graph.

An equation names an intermediate *configuration*, not a physical time step.
The source operation still determines its value, availability and exceptions.
Expressions are shared as syntax DAGs; sharing never evaluates an untaken arm or
identifies a stored reading with the physical quantity from which it was copied.

The checker validates the transformation against the source graph, independently
of the builder. A second, SMT check compares ordered functional composition with
the intermediate equations. That check proves representation preservation only:
it does not implement the retained runtime operations or prove the history MDP.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import json

from .ordered_execution import fingerprint

VERSION = 1

# These operations delimit blocks even when the graph gives them one successor.
# In particular, never fuse across observations, checks, calls or cycle boundaries.
BOUNDARIES = frozenset({
    'decision', 'apply_executed_action', 'check_all_requirements', 'outcome',
    'call_machine', 'return', 'begin_cycle', 'initialize_existing_runtime',
    'solve_source_constraints', 'enter_machine', 'finish_machine_transition',
})
EXPRESSION_KINDS = frozenset({'literal', 'reference', 'unary', 'binary', 'conditional'})
REF = '$expression'


def _is_expression(value):
    return isinstance(value, dict) and value.get('kind') in EXPRESSION_KINDS


class _Expressions:
    def __init__(self):
        self.terms = {}
        self.identities = {}

    def pack(self, value):
        if isinstance(value, list):
            return [self.pack(child) for child in value]
        if isinstance(value, tuple):
            return [self.pack(child) for child in value]
        if not isinstance(value, dict):
            return value
        packed = {key: self.pack(child) for key, child in value.items()}
        if not _is_expression(value):
            return packed
        signature = json.dumps(packed, sort_keys=True, separators=(',', ':'), allow_nan=False)
        if signature not in self.identities:
            identity = f'e{len(self.terms)}'
            self.identities[signature] = identity
            self.terms[identity] = packed
        return {REF: self.identities[signature]}


def _partition(graph):
    nodes = graph['nodes']
    incoming = Counter(target for node in nodes.values() for target in node['successors'].values())
    entries = {graph['initial_entry'], graph['cycle_entry']}
    entries.update(d[key] for d in graph['decisions'] for key in ('request', 'resume'))
    entries.update(node['on_exception'] for node in nodes.values())
    # Each block has one entry. A loop is left in the CFG, never bounded/unrolled.
    def chain(start, used):
        path = [start]
        while True:
            node = nodes[path[-1]]
            if node['operation'] in BOUNDARIES or len(node['successors']) != 1:
                return path
            target = next(iter(node['successors'].values()))
            if (target in used or target in path or target in entries
                    or incoming[target] != 1 or nodes[target]['operation'] in BOUNDARIES):
                return path
            path.append(target)

    leaders = set(entries)
    leaders.update(key for key in nodes if incoming[key] != 1 or nodes[key]['operation'] in BOUNDARIES)
    for node in nodes.values():
        if node['operation'] in BOUNDARIES or len(node['successors']) != 1:
            leaders.update(node['successors'].values())
    paths, used = [], set()
    for start in sorted(leaders) + sorted(set(nodes) - leaders):
        if start not in used:
            path = chain(start, used)
            paths.append(path)
            used.update(path)
    return paths


def compact_decision_transition(graph):
    """Produce sparse intermediate equations and retain every branch/operation."""
    expressions = _Expressions()
    blocks = {}
    for path in _partition(graph):
        equations = []
        for index, identity in enumerate(path):
            node = graph['nodes'][identity]
            equations.append({
                'source_node': identity, 'input': f'c{index}', 'result': f'c{index + 1}',
                'operation': node['operation'], 'data': expressions.pack(node['data']),
                'reads': list(node['reads']), 'writes': list(node['writes']),
                'on_exception': node['on_exception'],
                'state_access': {key: node[key] for key in ('read_version', 'write_version', 'frame')},
            })
        blocks[path[0]] = {
            'input': 'c0', 'result': f'c{len(path)}', 'equations': equations,
            'successors': dict(graph['nodes'][path[-1]]['successors']),
        }
    record = {
        'version': VERSION, 'source_graph_sha256': graph['sha256'],
        'initial_entry': graph['initial_entry'], 'cycle_entry': graph['cycle_entry'],
        'decisions': deepcopy(graph['decisions']),
        'expressions': expressions.terms, 'blocks': blocks,
        'semantics': {
            'configuration': deepcopy(graph['composition']['configuration']),
            'equation': 'result = source operation(input); exceptions use the recorded edge',
            'expressions': 'syntax DAG; evaluate on demand against the equation input configuration',
            'frame': 'all storage not written by the source operation retains its value',
            'numeric_operations': 'retain source operations and order; no arithmetic reassociation',
            'cycles': 'unchanged control-flow edges; no bound or fixed scan schedule',
            'physical_step_equations': 'separate; these blocks are not continuous trajectories',
        },
    }
    record['sha256'] = fingerprint(record)
    return record


def expand_expressions(value, terms, *, active=(), used=None):
    """Decoder used by the checker; rejects missing and cyclic intermediate terms."""
    if isinstance(value, list):
        return [expand_expressions(v, terms, active=active, used=used) for v in value]
    if not isinstance(value, dict):
        return value
    if REF in value:
        if set(value) != {REF}:
            raise ValueError('expression reference has extra fields')
        identity = value[REF]
        if identity not in terms or identity in active:
            raise ValueError(f'missing or cyclic expression {identity}')
        if used is not None:
            used.add(identity)
        result = expand_expressions(terms[identity], terms, active=(*active, identity), used=used)
        if not _is_expression(result):
            raise ValueError('intermediate expression does not decode to source syntax')
        return result
    return {key: expand_expressions(child, terms, active=active, used=used)
            for key, child in value.items()}


def validate_compact_transition(record, graph):
    """Check graph coverage, order, copies, guards and equations without rebuilding.

    Neither a producer hash nor the producer's choice of blocks is trusted. Any
    single-entry partition preserving all operations/edges can pass this checker.
    """
    errors = []
    if not isinstance(record, dict) or record.get('version') != VERSION:
        return ['missing or unsupported compact decision transition']
    if record.get('source_graph_sha256') != graph['sha256']:
        errors.append('compact transition identifies a different source graph')
    if record.get('sha256') != fingerprint({k: v for k, v in record.items() if k != 'sha256'}):
        errors.append('compact transition digest mismatch')
    for key in ('initial_entry', 'cycle_entry', 'decisions'):
        if record.get(key) != graph[key]:
            errors.append(f'compact transition changes {key}')
    expected_semantics = {
        'configuration': graph['composition']['configuration'],
        'equation': 'result = source operation(input); exceptions use the recorded edge',
        'expressions': 'syntax DAG; evaluate on demand against the equation input configuration',
        'frame': 'all storage not written by the source operation retains its value',
        'numeric_operations': 'retain source operations and order; no arithmetic reassociation',
        'cycles': 'unchanged control-flow edges; no bound or fixed scan schedule',
        'physical_step_equations': 'separate; these blocks are not continuous trajectories',
    }
    if record.get('semantics') != expected_semantics:
        errors.append('compact transition changes operation semantics')
    try:
        terms, blocks = record['expressions'], record['blocks']
        seen, used = Counter(), set()
        for entry, block in blocks.items():
            equations = block['equations']
            if not equations or equations[0]['source_node'] != entry:
                raise ValueError('block entry differs from first source operation')
            if block['input'] != 'c0' or block['result'] != f'c{len(equations)}':
                raise ValueError('incorrect block input/result')
            for index, equation in enumerate(equations):
                identity = equation['source_node']
                seen[identity] += 1
                node = graph['nodes'][identity]
                if equation['input'] != f'c{index}' or equation['result'] != f'c{index + 1}':
                    raise ValueError('intermediate equation reads/writes the wrong configuration')
                expected = {'source_node': identity, 'input': f'c{index}', 'result': f'c{index + 1}',
                            'operation': node['operation'], 'data': node['data'],
                            'reads': node['reads'], 'writes': node['writes'],
                            'on_exception': node['on_exception'],
                            'state_access': {key: node[key] for key in ('read_version', 'write_version', 'frame')}}
                actual = dict(equation)
                actual['data'] = expand_expressions(equation['data'], terms, used=used)
                # JSON normalizes source tuples without changing order.
                if actual != json.loads(json.dumps(expected)):
                    raise ValueError(f'compact equation differs from source operation: {identity}')
                if index + 1 < len(equations):
                    if (node['operation'] in BOUNDARIES or len(node['successors']) != 1
                            or next(iter(node['successors'].values())) != equations[index + 1]['source_node']):
                        raise ValueError('compact block skips a guard, boundary or source operation')
                elif block['successors'] != node['successors']:
                    raise ValueError('compact block changes source successors')
            if set(block) != {'input', 'result', 'equations', 'successors'}:
                raise ValueError('unrecognized compact block fields')
        if seen != Counter(graph['nodes'].keys()):
            errors.append('compact blocks omit or duplicate source operations')
        # All cross-block edges and exceptional continuations must target entries.
        interiors = set(graph['nodes']) - set(blocks)
        for block in blocks.values():
            if set(block['successors'].values()) & interiors:
                errors.append('control flow enters the middle of a compact block')
            if any(eq['on_exception'] in interiors for eq in block['equations']):
                errors.append('exception enters the middle of a compact block')
        if {graph['initial_entry'], graph['cycle_entry']} & interiors:
            errors.append('initial/cycle entry is inside a block')
        if any(d[k] in interiors for d in graph['decisions'] for k in ('request', 'resume')):
            errors.append('decision entry is inside a block')
        if used != set(terms):
            errors.append('unreferenced or omitted intermediate expressions')
    except (KeyError, TypeError, ValueError, RecursionError) as exc:
        errors.append(f'invalid compact transition: {exc}')
    return errors


def check_block_equations(record, graph, *, timeout_ms=1000, include_artifacts=False):
    """SMT translation validation, with retained runtime operations uninterpreted.

    The source side composes operations directly; the compact side constrains
    named intermediates. UNSAT excludes a changed result at a block exit. The
    structural check also covers every exceptional exit and control-flow edge.
    No claim about numerical arithmetic or runtime implementation follows here.
    """
    errors = validate_compact_transition(record, graph)
    if errors:
        return {'status': 'rejected', 'errors': errors, 'claim': 'not_claimed'}
    try:
        import z3
    except ImportError:
        return {'status': 'unavailable', 'claim': 'not_claimed'}
    configuration = z3.DeclareSort('CompactConfiguration')
    functions = {}
    def operation(node):
        signature = fingerprint({'operation': node['operation'], 'data': node['data']})
        if signature not in functions:
            functions[signature] = z3.Function('source_operation_' + signature, configuration, configuration)
        return functions[signature]
    solver = z3.SolverFor('QF_UF')
    solver.set(timeout=timeout_ms)
    differences = []
    count = 0
    for entry, block in record['blocks'].items():
        initial = z3.Const(entry + '::input', configuration)
        source, compact = initial, initial
        for equation in block['equations']:
            source_node = graph['nodes'][equation['source_node']]
            source = operation(source_node)(source)
            decoded = {'operation': equation['operation'],
                       'data': expand_expressions(equation['data'], record['expressions'])}
            result = z3.Const(entry + '::' + equation['result'], configuration)
            solver.add(result == operation(decoded)(compact))
            compact = result
            count += 1
        differences.append(source != compact)
    solver.add(z3.Or(*differences))
    result = solver.check()
    report = {
        'status': 'discharged' if result == z3.unsat else str(result),
        'claim': 'compact_representation_preservation' if result == z3.unsat else 'not_claimed',
        'solver': 'z3', 'logic': 'QF_UF', 'blocks_checked': len(record['blocks']),
        'intermediate_equations_checked': count,
        'runtime_operation_semantics': 'retained, not discharged by this transformation check',
    }
    if include_artifacts:
        report['artifacts'] = {'smt2': solver.to_smt2(), 'z3_check_sat': str(result)}
    if result == z3.unknown:
        report['reason_unknown'] = solver.reason_unknown()
    return report
