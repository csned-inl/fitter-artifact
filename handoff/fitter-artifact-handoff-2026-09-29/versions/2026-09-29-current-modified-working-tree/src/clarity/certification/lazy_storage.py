"""Source-derived storage bounds for compact transition equations.

Bounds count live source storage, not possible values or branch combinations.
Unsupported recursive dispatch is rejected, rather than assigning a scan limit.
"""
from dataclasses import dataclass

from .ordered_execution import fingerprint


@dataclass(frozen=True)
class StorageSchema:
    source_sha256: str
    scalar_locations: tuple
    machine_states: tuple
    local_names: tuple
    payload_fields: tuple
    mailbox_types: tuple
    call_depth: int
    payload_capacity: int
    item_types: tuple = ()

    def record(self):
        from dataclasses import asdict
        result = asdict(self)
        result['sha256'] = fingerprint(result)
        return result


def source_storage_schema(graph):
    """Bound finite mailboxes and acyclic saved frames from writer semantics.

    This checks graph operations, not arbitrary Python programs. The normal
    source-graph checker remains responsible for matching them to the runtime.
    """
    nodes = graph['nodes']
    initial = graph.get('initial_configuration', {})
    for key in ('ordered_mailboxes', 'local_items', 'call_stack', 'machine_frames'):
        if initial.get(key):
            raise ValueError('unsupported nonempty initial ' + key)
    entries = [name for name, node in nodes.items() if node['operation'] == 'enter_machine']
    calls = {}
    for entry in entries:
        seen, pending, targets = set(), [entry], set()
        while pending:
            name = pending.pop()
            if name in seen: continue
            seen.add(name); node = nodes[name]
            if node['operation'] in ('return', 'outcome'): continue
            if node['operation'] == 'call_machine':
                targets.add(node['successors']['call'])
                pending.append(node['successors']['return'])
            else: pending.extend(node['successors'].values())
        calls[entry] = targets
    depths = {}
    def depth(entry, active=()):
        if entry in active: raise ValueError('recursive source dispatch: ' + entry)
        if entry not in calls: raise ValueError('call does not enter a source machine: ' + entry)
        if entry not in depths:
            depths[entry] = 1 + max((depth(child, (*active, entry)) for child in calls[entry]), default=0)
        return depths[entry]
    call_depth = max((depth(entry) for entry in entries), default=0)
    locations = {key for key in graph['storage'] if not key.startswith('$')}
    local_names, fields, item_types, destinations = set(), set(), set(), set()
    declared_local_types = {}
    for node in nodes.values():
        op, data = node['operation'], node['data']
        mailbox_write = any(key == '$mailboxes' or key.startswith('$mailbox:')
                            for key in node.get('writes', ()))
        if mailbox_write and op not in (
                'initialize_existing_runtime', 'send_copy', 'accept_copy', 'finish_machine_transition'):
            raise ValueError('unrecognized mailbox writer: ' + op)
        if op == 'enter_block':
            local_names.update(data['declarations'])
            item_types.update(data['declarations'].values())
            for name, kind in data['declarations'].items():
                declared_local_types.setdefault(name, set()).add(kind)
        elif op == 'accept_copy':
            if data['destination']: local_names.add(data['destination'])
        elif op == 'send_copy':
            if data['destination']: destinations.add(data['destination'])
    # Local references are written only by declarations and accepts. Union all
    # declarations of a name across scopes and all compatible accept types.
    # This deliberately ignores guards, timing and caller correlations.
    parents = graph.get('item_type_parents', {})
    def compatible(actual, expected):
        seen = set()
        while actual is not None:
            if actual in seen:
                raise ValueError('cyclic item inheritance')
            seen.add(actual)
            if actual == expected:
                return True
            actual = parents.get(actual)
        return False
    for node in nodes.values():
        data = node['data']
        if node['operation'] == 'accept_copy' and data['destination']:
            declared_local_types.setdefault(data['destination'], set()).update(
                kind for kind in item_types if compatible(kind, data['expected_type']))
    port_types = {port: set() for port in destinations}
    for node in nodes.values():
        data = node['data']
        if node['operation'] == 'send_copy' and data['destination']:
            port_types[data['destination']].update(declared_local_types.get(data['payload'], item_types))
    # Exact-type replacement bounds each mailbox by its source-compatible
    # send types. Missing local provenance conservatively retains all types.
    boxes = tuple((port, tuple(sorted(kinds))) for port, kinds in sorted(port_types.items()))
    for node in nodes.values():
        if node['operation'] in ('assign', 'declare_attribute'):
            path = node['data'].get('source_target', [])
            if path and path[0] in local_names:
                fields.add('.'.join(path[1:]))
    # Accept copies the attributes actually present on the payload, including
    # attributes assigned by source code rather than declared in its item type.
    # Reserve their possible scalar destinations; presence remains symbolic.
    # Extra reserved cells start absent and do not create runtime attributes.
    for node in nodes.values():
        op, data = node['operation'], node['data']
        if op == 'accept_copy' and data['destination']:
            prefix = data['context'] + '::' + data['destination'] + '::'
        elif op == 'match_trigger' and data['type'] and data['port'] and data['destination']:
            prefix = data['instance'] + '::' + data['destination'] + '::'
        else:
            continue
        locations.update(prefix + field.replace('.', '::') for field in fields)
    # Source initialization starts empty. Declarations, send-copy and accepts
    # retain payloads only through mailbox slots, current/saved locals or a
    # current/saved matched-item reference. Reserve one extra allocation slot
    # for replacement before the overwritten item becomes unreachable.
    capacity = sum(len(types) for _, types in boxes) + (call_depth + 1) * (len(local_names) + 1) + 1
    return StorageSchema(graph['sha256'], tuple(sorted(locations)),
        tuple((owner, tuple(states)) for owner, states in sorted(graph['machine_states'].items())),
        tuple(sorted(local_names)), tuple(sorted(fields)), boxes, call_depth, capacity,
        tuple(sorted(item_types)))


def verify_storage_schema(record, graph):
    """Reconstruct the bounding inventory, not semantic correspondence evidence."""
    # This manifest comparison is structural validation; it is not the local
    # semantic proof of message operations and must not be labeled as one.
    expected = source_storage_schema(graph).record()
    return [] if record == expected else ['storage schema differs from source-derived bounds']
