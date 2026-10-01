"""Checked event-local scalar SSA lowering for the thermostat proof profile.

The lowering gives every retained scalar storage distinct entry and exit
symbols, emits exact update equations for the currently supported operation
classes, and emits a frame equation for every other scalar storage. Enum and
transport operations are deliberately rejected until their profile-specific
constructors and queue semantics are attached.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib

from .markov_ir import IREvent, IRStorage, NativeSort
from .markov_slice import SlicedEvent, TheoremSlice
from .markov_z3 import UnsupportedLoweringError
from .markov_z3_expr import SMTTerm, ThermostatExpressionCompiler, quoted_symbol, smt_sort


SCALAR_SORTS = {
    NativeSort.BOOL, NativeSort.PRESENCE, NativeSort.INT,
    NativeSort.FLOAT32, NativeSort.FLOAT64,
}
SUPPORTED_OPERATIONS = {"assign", "branch", "advance_engine_time", "set_dt"}

ENUM_CONSTRUCTORS = {
    "ExecutedAction": ("action_0", "action_1", "action_2", "action_3"),
    "ExecutionError": ("no_execution_error", "execution_error"),
    "OutcomeConstructor": ("outcome_continue", "outcome_terminal", "outcome_error"),
    "PolicyProposal": ("proposal_0", "proposal_1", "proposal_2", "proposal_3"),
    "HeaterBehaviorState": ("reset", "on"),
    "PropertyStatus": ("property_true", "property_false"),
    "PropertyError": ("no_error", "evaluation_error"),
}
SUPPORTED_OPERATIONS |= {
    "accept_copy", "apply_executed_action", "call_machine", "completion_test", "decision",
    "enter_machine", "finish_machine_transition", "machine_from_state",
    "match_trigger", "outcome", "return", "check_all_requirements",
    "send_copy", "solve_source_constraints",
}


def supports_scalar_event(event: SlicedEvent) -> bool:
    if event.operation not in SUPPORTED_OPERATIONS:
        return False
    return True


def _event_token(event: SlicedEvent) -> str:
    return hashlib.sha256(event.node_id.encode()).hexdigest()


@dataclass(frozen=True)
class ScalarEventEncoding:
    node_id: str
    operation: str
    namespace: str
    sort_declarations: tuple[str, ...]
    declarations: tuple[tuple[str, str], ...]
    updates: tuple[str, ...]
    frames: tuple[str, ...]
    branch_condition: SMTTerm | None
    precondition: SMTTerm | None
    exception_condition: SMTTerm

    @property
    def assertions(self) -> tuple[str, ...]:
        prefix = () if self.precondition is None else (self.precondition.text,)
        prefix += (f"(not {self.exception_condition.text})",)
        return prefix + self.updates + self.frames

    def smt2(self, *extra_assertions: str) -> str:
        lines = [
            *self.sort_declarations,
            *(f"(declare-const {name} {sort})"
              for name, sort in self.declarations),
            *(f"(assert {formula})"
              for formula in self.assertions + tuple(extra_assertions)),
        ]
        return "\n".join(lines) + "\n"


def enum_sort(declared_type: str) -> str:
    return quoted_symbol("enum-sort", declared_type)


def enum_constructor(declared_type: str, constructor: str) -> str:
    """Return one checked datatype constructor or fail closed."""

    allowed = ENUM_CONSTRUCTORS.get(declared_type)
    if allowed is None or constructor not in allowed:
        raise UnsupportedLoweringError(
            f"unknown constructor {constructor!r} for {declared_type!r}"
        )
    return quoted_symbol("enum", declared_type, constructor)


def enum_sort_declarations(declared_types) -> tuple[str, ...]:
    """Declare the requested checked enum sorts in deterministic order."""

    result = []
    for declared_type in sorted(set(declared_types)):
        constructors = ENUM_CONSTRUCTORS.get(declared_type)
        if constructors is None:
            raise UnsupportedLoweringError(
                f"enum type has no checked constructors: {declared_type!r}"
            )
        sort = enum_sort(declared_type)
        values = " ".join(
            enum_constructor(declared_type, constructor)
            for constructor in constructors
        )
        result.append(f"(declare-datatypes () (({sort} {values})))")
    return tuple(result)


def _storage_sort(storage: IRStorage) -> str:
    sort = storage.identity.native_sort
    if sort in SCALAR_SORTS:
        return smt_sort(sort)
    if sort is NativeSort.ENUM:
        return enum_sort(storage.identity.declared_type)
    raise UnsupportedLoweringError(
        f"event state contains a forbidden sort: {sort.value}"
    )


def _sort_declarations(slice_: TheoremSlice) -> tuple[str, ...]:
    declared_types = sorted({
        storage.identity.declared_type for storage in slice_.storages
        if storage.identity.native_sort is NativeSort.ENUM
    })
    return enum_sort_declarations(declared_types)


def compile_scalar_event(
    slice_: TheoremSlice,
    source: IREvent,
    event: SlicedEvent,
    *,
    namespace: str = "run",
) -> ScalarEventEncoding:
    """Lower one supported source-bound event with complete scalar framing."""

    if source.node_id != event.node_id or source.operation != event.operation:
        raise UnsupportedLoweringError("source/slice event identity mismatch")
    if not supports_scalar_event(event):
        raise UnsupportedLoweringError(
            f"event operation has no scalar SSA lowering: {event.operation}"
        )
    if not namespace:
        raise ValueError("event namespace is empty")

    state = {storage.identity.uid: storage for storage in slice_.storages}
    writes = set(event.writes)
    if not writes.issubset(state):
        unsupported = sorted(writes - set(state))
        raise UnsupportedLoweringError(
            f"event writes unknown retained storage: {unsupported}"
        )

    local_namespace = f"{namespace}::{_event_token(event)}"
    entry = {
        uid: quoted_symbol(local_namespace, "entry", uid) for uid in state
    }
    exit_ = {
        uid: quoted_symbol(local_namespace, "exit", uid) for uid in state
    }
    declarations = tuple(sorted(
        ((entry[uid], _storage_sort(storage)) for uid, storage in state.items()),
        key=lambda item: item[0],
    )) + tuple(sorted(
        ((exit_[uid], _storage_sort(storage)) for uid, storage in state.items()),
        key=lambda item: item[0],
    ))

    compiler = ThermostatExpressionCompiler(slice_, namespace=local_namespace)
    data = source.decoded_data()
    updates: list[str] = []
    branch_condition: SMTTerm | None = None
    precondition: SMTTerm | None = None
    defined_conditions: list[str] = []

    if event.operation == "assign":
        if len(writes) != 1:
            raise UnsupportedLoweringError("scalar assignment must write exactly one storage")
        target = next(iter(writes))
        term = compiler.compile(data.get("expression"))
        defined_conditions.extend(term.defined)
        target_sort = state[target].identity.native_sort
        if term.sort is not target_sort:
            raise UnsupportedLoweringError(
                f"assignment sort mismatch: {term.sort.value} -> {target_sort.value}"
            )
        updates.append(f"(= {exit_[target]} {term.text})")
    elif event.operation == "branch":
        if writes:
            raise UnsupportedLoweringError("branch unexpectedly writes scalar state")
        branch_condition = compiler.compile(data.get("condition"))
        defined_conditions.extend(branch_condition.defined)
        if branch_condition.sort not in {NativeSort.BOOL, NativeSort.PRESENCE}:
            raise UnsupportedLoweringError("branch guard is not Boolean")
    elif event.operation == "set_dt":
        if writes != {"graph:dt"} or event.reads != ("slice:configured_dt",):
            raise UnsupportedLoweringError("configured-dt copy identity mismatch")
        updates.append(
            f"(= {exit_['graph:dt']} {entry['slice:configured_dt']})"
        )
    elif event.operation == "advance_engine_time":
        if writes != {"semantic:engine_time"} \
                or set(event.reads) != {"semantic:engine_time", "slice:configured_dt"}:
            raise UnsupportedLoweringError("engine-time update identity mismatch")
        updates.append(
            f"(= {exit_['semantic:engine_time']} "
            f"(fp.add RNE {entry['semantic:engine_time']} "
            f"{entry['slice:configured_dt']}))"
        )
    elif event.operation == "decision":
        if writes != {"semantic:latched_completion"}:
            raise UnsupportedLoweringError("decision latch identity mismatch")
        inputs = data.get("inputs")
        if not isinstance(inputs, dict) or set(inputs) != {
                "setPoint", "temperatureCelcius", "done"}:
            raise UnsupportedLoweringError("decision input signature mismatch")
        term = compiler.compile(inputs["done"])
        defined_conditions.extend(term.defined)
        if term.sort is not NativeSort.BOOL:
            raise UnsupportedLoweringError("decision completion input is not Boolean")
        updates.append(f"(= {exit_['semantic:latched_completion']} {term.text})")
    elif event.operation == "completion_test":
        if writes or event.reads != ("semantic:latched_completion",):
            raise UnsupportedLoweringError("completion-test identity mismatch")
        branch_condition = SMTTerm(
            entry["semantic:latched_completion"], NativeSort.BOOL
        )
    elif event.operation == "apply_executed_action":
        expected_writes = {
            "graph:system::controller::policyCall::heaterState",
            "graph:system::controller::policyCall::acState",
        }
        if writes != expected_writes or event.reads != ("slice:executed_action",):
            raise UnsupportedLoweringError("executed-action interface mismatch")
        action = entry["slice:executed_action"]
        action_type = state["slice:executed_action"].identity.declared_type
        a1 = enum_constructor(action_type, "action_1")
        a2 = enum_constructor(action_type, "action_2")
        a3 = enum_constructor(action_type, "action_3")
        updates.extend((
            f"(= {exit_['graph:system::controller::policyCall::heaterState']} "
            f"(or (= {action} {a1}) (= {action} {a3})))",
            f"(= {exit_['graph:system::controller::policyCall::acState']} "
            f"(or (= {action} {a2}) (= {action} {a3})))",
        ))
    elif event.operation == "finish_machine_transition":
        instance = data.get("instance")
        mode_candidates = [storage for storage in state.values()
                           if storage.identity.role == "machine_mode"
                           and storage.identity.owner == instance]
        if len(mode_candidates) != 1:
            raise UnsupportedLoweringError("machine finish mode does not resolve")
        storage = mode_candidates[0]
        target = storage.identity.uid
        machine = instance.removeprefix("system::")
        command = f"slice:command:{machine}:{data.get('to')}"
        if writes != {target, command}:
            raise UnsupportedLoweringError("machine finish write identities mismatch")
        constructor = enum_constructor(
            storage.identity.declared_type, data.get("to")
        )
        updates.extend((
            f"(= {exit_[target]} {constructor})",
            f"(= {exit_[command]} false)",
        ))
    elif event.operation == "machine_from_state":
        if writes:
            raise UnsupportedLoweringError("machine source test unexpectedly writes state")
        instance = data.get("instance")
        candidates = [storage for storage in state.values()
                      if storage.identity.role == "machine_mode"
                      and storage.identity.owner == instance]
        if len(candidates) != 1:
            raise UnsupportedLoweringError("machine mode does not resolve uniquely")
        storage = candidates[0]
        machine = instance.removeprefix("system::")
        saved_uid = f"slice:machine:{machine}:saved_mode"
        if event.reads != (saved_uid,):
            raise UnsupportedLoweringError("saved machine-mode read mismatch")
        expected = enum_constructor(
            storage.identity.declared_type, data.get("expected")
        )
        branch_condition = SMTTerm(
            f"(= {entry[saved_uid]} {expected})", NativeSort.BOOL
        )
    elif event.operation == "enter_machine":
        instance = data.get("instance")
        machine = instance.removeprefix("system::")
        saved_uid = f"slice:machine:{machine}:saved_mode"
        mode_candidates = [storage.identity.uid for storage in state.values()
                           if storage.identity.role == "machine_mode"
                           and storage.identity.owner == instance]
        if len(mode_candidates) != 1 or writes != {saved_uid}:
            raise UnsupportedLoweringError("machine-entry snapshot identity mismatch")
        updates.append(f"(= {exit_[saved_uid]} {entry[mode_candidates[0]]})")
    elif event.operation == "match_trigger":
        if writes:
            raise UnsupportedLoweringError("trigger match unexpectedly writes state")
        machine = data.get("instance", "").removeprefix("system::")
        command_name = {
            "HeatingCoolingOnCmd": "on",
            "HeatingCoolingResetCmd": "reset",
        }.get(data.get("type"))
        if command_name is None:
            raise UnsupportedLoweringError("unknown thermostat command type")
        command = f"slice:command:{machine}:{command_name}"
        if event.reads != (command,):
            raise UnsupportedLoweringError("trigger command-presence read mismatch")
        branch_condition = SMTTerm(entry[command], NativeSort.BOOL)
    elif event.operation in {"call_machine", "return", "outcome"}:
        if writes:
            raise UnsupportedLoweringError(
                f"{event.operation} unexpectedly writes retained state"
            )
    elif event.operation == "solve_source_constraints":
        expected = {
            "graph:system::environment::acPort::heat::rateWatts": "semantic:ac_output",
            "graph:system::environment::heaterPort::heat::rateWatts": "semantic:heater_output",
            "graph:system::lastObservedTemperature": "semantic:held_temperature",
            "graph:system::thermometer::environmentPort::reading::temperatureCelcius":
                "semantic:physical_temperature",
        }
        if writes != set(expected) or data.get("constraints") != []:
            raise UnsupportedLoweringError("source-constraint projection mismatch")
        updates.extend(
            f"(= {exit_[target]} {entry[source_uid]})"
            for target, source_uid in sorted(expected.items())
        )
    elif event.operation == "send_copy":
        if event.node_id == "system::thermometer/step/4":
            expected_writes = {
                "semantic:sent_temperature_payload",
                "semantic:thermometer_payload_present",
            }
            payload = "graph:system::thermometer::temperatureReading::temperatureCelcius"
            if writes != expected_writes or payload not in event.reads:
                raise UnsupportedLoweringError("thermometer send-copy identity mismatch")
            updates.extend((
                f"(= {exit_['semantic:sent_temperature_payload']} {entry[payload]})",
                f"(= {exit_['semantic:thermometer_payload_present']} true)",
            ))
        else:
            destination = data.get("destination", "")
            payload = data.get("payload")
            if destination not in {"system::heater::cmdIn", "system::ac::cmdIn"}:
                raise UnsupportedLoweringError("unknown command transport destination")
            machine = destination.split("::")[1]
            command_name = {"onCmd": "on", "resetCmd": "reset"}.get(payload)
            command = f"slice:command:{machine}:{command_name}"
            if command_name is None or writes != {command}:
                raise UnsupportedLoweringError("controller command-send identity mismatch")
            updates.append(f"(= {exit_[command]} true)")
    elif event.operation == "accept_copy":
        expected_writes = {
            "semantic:received_temperature_payload",
            "semantic:sent_temperature_payload",
            "semantic:thermometer_payload_present",
        }
        expected_reads = {
            "semantic:sent_temperature_payload",
            "semantic:thermometer_payload_present",
        }
        if writes != expected_writes or set(event.reads) != expected_reads:
            raise UnsupportedLoweringError("controller accept-copy identity mismatch")
        precondition = SMTTerm(
            entry["semantic:thermometer_payload_present"], NativeSort.BOOL
        )
        updates.extend((
            f"(= {exit_['semantic:received_temperature_payload']} "
            f"{entry['semantic:sent_temperature_payload']})",
            f"(= {exit_['semantic:sent_temperature_payload']} "
            f"{entry['semantic:sent_temperature_payload']})",
            f"(= {exit_['semantic:thermometer_payload_present']} false)",
        ))
    elif event.operation == "check_all_requirements":
        expressions = data.get("expressions")
        properties = data.get("properties")
        if not isinstance(expressions, dict) or not isinstance(properties, list) \
                or set(expressions) != set(properties):
            raise UnsupportedLoweringError("requirement expression inventory mismatch")
        expected_writes = set()
        for name in properties:
            slug = name.replace(" ", "_").lower()
            status_uid = f"slice:property:{slug}:status"
            error_uid = f"slice:property:{slug}:error"
            expected_writes.update((status_uid, error_uid))
            term = compiler.compile(expressions[name])
            defined_conditions.extend(term.defined)
            if term.sort is not NativeSort.BOOL:
                raise UnsupportedLoweringError(
                    f"requirement result is not Boolean: {name}"
                )
            status_type = state[status_uid].identity.declared_type
            error_type = state[error_uid].identity.declared_type
            true_value = enum_constructor(status_type, "property_true")
            false_value = enum_constructor(status_type, "property_false")
            no_error = enum_constructor(error_type, "no_error")
            updates.extend((
                f"(= {exit_[status_uid]} (ite (and "
                f"(= {entry[status_uid]} {true_value}) {term.text}) "
                f"{true_value} {false_value}))",
                # Every checked thermostat requirement expression is total in
                # this profile. Preserve any earlier error in the interval;
                # the decision-boundary initializer later supplies no_error.
                f"(= {exit_[error_uid]} {entry[error_uid]})",
            ))
            if no_error not in "\n".join(_sort_declarations(slice_)):
                raise UnsupportedLoweringError("property-error constructor unavailable")
        if writes != expected_writes:
            raise UnsupportedLoweringError("requirement accumulator write mismatch")

    declared_names = {name for name, _ in declarations}
    expression_names = {name for name, _ in compiler.declarations}
    if not expression_names.issubset(declared_names):
        raise UnsupportedLoweringError("expression referenced undeclared scalar storage")
    frames = tuple(
        f"(= {exit_[uid]} {entry[uid]})"
        for uid in sorted(set(state) - writes)
    )
    if not defined_conditions:
        exception_condition = SMTTerm("false", NativeSort.BOOL)
    elif len(defined_conditions) == 1:
        exception_condition = SMTTerm(
            f"(not {defined_conditions[0]})", NativeSort.BOOL
        )
    else:
        exception_condition = SMTTerm(
            f"(not (and {' '.join(defined_conditions)}))", NativeSort.BOOL
        )
    return ScalarEventEncoding(
        node_id=event.node_id,
        operation=event.operation,
        namespace=local_namespace,
        sort_declarations=_sort_declarations(slice_),
        declarations=declarations,
        updates=tuple(updates),
        frames=frames,
        branch_condition=branch_condition,
        precondition=precondition,
        exception_condition=exception_condition,
    )


def compile_identity_event(
    slice_: TheoremSlice,
    node_id: str,
    operation: str,
    *,
    namespace: str = "run",
) -> ScalarEventEncoding:
    """Lower a checked dead begin/block/no-op node as a complete identity."""

    if operation not in {"begin_cycle", "enter_block", "no_op"}:
        raise UnsupportedLoweringError(
            f"operation is not an approved identity node: {operation}"
        )
    state = {storage.identity.uid: storage for storage in slice_.storages}
    token = hashlib.sha256(node_id.encode()).hexdigest()
    local_namespace = f"{namespace}::{token}"
    entry = {uid: quoted_symbol(local_namespace, "entry", uid) for uid in state}
    exit_ = {uid: quoted_symbol(local_namespace, "exit", uid) for uid in state}
    declarations = tuple(sorted(
        ((entry[uid], _storage_sort(storage)) for uid, storage in state.items()),
        key=lambda item: item[0],
    )) + tuple(sorted(
        ((exit_[uid], _storage_sort(storage)) for uid, storage in state.items()),
        key=lambda item: item[0],
    ))
    return ScalarEventEncoding(
        node_id=node_id,
        operation=operation,
        namespace=local_namespace,
        sort_declarations=_sort_declarations(slice_),
        declarations=declarations,
        updates=(),
        frames=tuple(
            f"(= {exit_[uid]} {entry[uid]})" for uid in sorted(state)
        ),
        branch_condition=None,
        precondition=None,
        exception_condition=SMTTerm("false", NativeSort.BOOL),
    )
