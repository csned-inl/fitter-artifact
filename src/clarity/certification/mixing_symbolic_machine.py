"""Direct standalone-SysML symbolic machine for the Mixing Machine.

The authoritative source is the named file in ``csned-inl/clarity-standalone``.
The local copy is accepted only when both its Git blob identity and SHA-256
match that source.  This module compiles the decision-to-decision equations;
it neither imports nor symbolically executes the simulator.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import hashlib
import importlib
from pathlib import Path
from typing import Any, Iterable

from clarity.certification.symbolic_process import FixedProcessContext
from clarity.sysml.parser import (
    AssignStmt,
    BinaryExpr,
    Expr,
    ExpressionParser,
    IfStmt,
    LiteralExpr,
    PerformStmt,
    RefExpr,
    SubactionCallStmt,
    SysMLParser,
    UnaryExpr,
)

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_MODEL_PATH = ROOT / "src" / "clarity" / "models" / "mixing-sysml-model" / "model.sysml"
AUTHORITATIVE_REPOSITORY = "csned-inl/clarity-standalone"
AUTHORITATIVE_PATH = "sysml-models/mixing-sysml-model/model.sysml"
AUTHORITATIVE_COMMIT = "aa3c6ae4640e3bcadcf66542eb0124e6c57e2bb3"
APPROVED_GIT_BLOB_SHA = "20ef2ed6dd55ab083ce8b1a403beab17c58dbdff"
APPROVED_SOURCE_SHA256 = "09d59723435da435325299934ed8472bbac3e89c6d9840df892b9f7e1c912f8f"
QUERY_NAME = "mixing_exact_integer_bobs2_bact1_v1"
SEMANTIC_PROFILE = "mixing_policy_decision_exact_v1"
MAX_SMT2_BYTES = 32_768
RESULT_SCHEMA = (
    "Success(reward, property_statuses)",
    "Running(reward, property_statuses, next_buffer)",
    "Violation(reward, property_statuses)",
)
CONTRACT_ASSUMPTIONS = (
    "the authoritative standalone source identities and mixing_policy_decision_exact_v1 profile are exact",
    "controller decisions occur only at Policy invocations inside ScanCycle",
    "dt is exact 1/10 and consecutive post-warmup Policy invocations are one scan period apart",
    "Modbus reads synchronously sample each physical feeder level at the Policy decision boundary",
    "observedLevel1 and observedLevel2 are distinct held sensor state equal to sampled physical level minus tolerance 2 only at that boundary",
    "scenario originals and transfer targets are fixed for one MDP instance and are controller-visible",
    "pump and valve writes complete atomically without transport error, queueing, or partial command application",
    "the shield keeps a live requirement-satisfying proposal and otherwise replaces it with the unique requirement action",
    "feeder drain is exact maxFlowRate * dt when both its pump and valve are on and zero otherwise",
    "all arithmetic is mathematical integer/rational arithmetic; no runtime normalization or floating-point rounding is claimed",
    "completion still applies the shielded command and property checks before terminal success",
    "external truncation and unspecified error behavior are outside this theorem",
)


class UnsupportedMixingModel(ValueError):
    """The standalone source does not match the supported Mixing profile."""


def _z3() -> Any:
    try:
        return importlib.import_module("z3")
    except ModuleNotFoundError as error:  # pragma: no cover - workstation dependent
        raise RuntimeError("z3-solver is required for symbolic-machine construction") from error


def _git_blob_sha(data: bytes) -> str:
    header = f"blob {len(data)}\0".encode("ascii")
    return hashlib.sha1(header + data).hexdigest()  # nosec: Git object identity, not security


def _expr_key(expr: Expr) -> tuple:
    if isinstance(expr, LiteralExpr):
        value = str(expr.value) if isinstance(expr.value, float) else expr.value
        return ("literal", value)
    if isinstance(expr, RefExpr):
        return ("ref", tuple(expr.path))
    if isinstance(expr, UnaryExpr):
        return ("unary", expr.op, _expr_key(expr.operand))
    if isinstance(expr, BinaryExpr):
        return ("binary", expr.op, _expr_key(expr.left), _expr_key(expr.right))
    raise UnsupportedMixingModel(f"unsupported source expression: {type(expr).__name__}")


def _parsed_expression(text: str) -> tuple:
    return _expr_key(ExpressionParser(text).parse())


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise UnsupportedMixingModel(message)


@dataclass(frozen=True, slots=True)
class NamedPredicate:
    name: str
    role: str
    expression: Any


@dataclass(frozen=True, slots=True)
class MixingVariables:
    physical_level_1: Any
    physical_level_2: Any
    sampled_level_1: Any
    sampled_level_2: Any
    pump_1_running: Any
    valve_1_open: Any
    pump_2_running: Any
    valve_2_open: Any

    def expressions(self) -> tuple[Any, ...]:
        return (
            self.physical_level_1, self.physical_level_2,
            self.sampled_level_1, self.sampled_level_2,
            self.pump_1_running, self.valve_1_open,
            self.pump_2_running, self.valve_2_open,
        )


@dataclass(frozen=True, slots=True)
class MixingBuffer:
    current_observation: tuple[Any, ...]
    prior_observation_1: tuple[Any, ...]
    prior_observation_2: tuple[Any, ...]
    prior_executed_action: Any

    def expressions(self) -> tuple[Any, ...]:
        return (
            *self.current_observation,
            *self.prior_observation_1,
            *self.prior_observation_2,
            self.prior_executed_action,
        )


@dataclass(frozen=True, slots=True)
class MixingRelation:
    source_sha256: str
    prefix: str
    fixed: FixedProcessContext
    proposal: Any
    current: MixingVariables
    next: MixingVariables
    domain: Any
    initial: Any
    sample_relation: Any
    enabled: Any
    completion: Any
    required_action: Any
    proposal_is_dead: Any
    proposal_satisfies_requirement: Any
    shield_replaced_proposal: Any
    executed_action: Any
    observation: tuple[Any, ...]
    next_observation: tuple[Any, ...]
    properties: tuple[NamedPredicate, ...]
    all_properties_hold: Any
    outcome_tag: Any
    reward: Any
    assumptions: tuple[str, ...]

    def renamed(self, prefix: str, *, fixed: FixedProcessContext | None = None,
                proposal: Any | None = None) -> "MixingRelation":
        return _build_relation(
            source_sha256=self.source_sha256,
            prefix=prefix,
            fixed_context=fixed or self.fixed,
            proposal=proposal,
        )

    def summary(self) -> dict[str, object]:
        return {
            "query": QUERY_NAME,
            "authoritative_repository": AUTHORITATIVE_REPOSITORY,
            "authoritative_path": AUTHORITATIVE_PATH,
            "authoritative_commit": AUTHORITATIVE_COMMIT,
            "git_blob_sha": APPROVED_GIT_BLOB_SHA,
            "source_sha256": self.source_sha256,
            "fixed_context": self.fixed.names(),
            "observation": (
                "sampled_level_1", "sampled_level_2",
                "target_transfer_1", "target_transfer_2",
                "original_level_1", "original_level_2",
            ),
            "sample_relation": "sampled_level_i == physical_level_i - tolerance at Policy boundaries",
            "result_schema": RESULT_SCHEMA,
            "properties": tuple((item.name, item.role) for item in self.properties),
            "assumptions": self.assumptions,
        }


@dataclass(frozen=True, slots=True)
class MixingPairedMarkovQuery:
    left: MixingRelation
    right: MixingRelation
    left_buffer: MixingBuffer
    right_buffer: MixingBuffer
    initial_empty: Any
    initial_contained: Any
    invariant_closed: Any
    counterexample: Any
    assumptions: tuple[str, ...]

    def smt2(self) -> str:
        z3 = _z3()
        solver = z3.Solver()
        solver.add(self.counterexample)
        return solver.to_smt2()

    def run(self, *, timeout_ms: int = 5_000) -> dict[str, object]:
        z3 = _z3()

        def check(formula: Any) -> str:
            solver = z3.Solver()
            solver.set(timeout=timeout_ms)
            solver.add(formula)
            return str(solver.check())

        formulas = {
            "initial": self.initial_empty,
            "initial_contained": self.initial_contained,
            "closure": self.invariant_closed,
            "markov": self.counterexample,
        }
        sizes = {}
        for name, formula in formulas.items():
            solver = z3.Solver()
            solver.add(formula)
            sizes[name] = len(solver.to_smt2().encode("utf-8"))
        if max(sizes.values()) > MAX_SMT2_BYTES:
            return {"classification": "NO_RESULT", "reason": "formula_budget", "smt2_bytes": sizes}
        results = {name: check(formula) for name, formula in formulas.items()}
        if results["initial"] != "unsat":
            return {"classification": "NO_RESULT", "reason": "initialization", **results}
        if results["initial_contained"] != "unsat":
            return {"classification": "NO_RESULT", "reason": "initial_containment", **results}
        if results["closure"] != "unsat":
            return {"classification": "NO_RESULT", "reason": "invariant_closure", **results}
        if results["markov"] == "unsat":
            return {"classification": "CERTIFIED_UNDER_CONTRACT", **results, "smt2_bytes": sizes}
        return {"classification": "NO_RESULT", "reason": "counterexample_or_unknown", **results}


def _int(z3: Any, value: int) -> Any:
    return z3.IntVal(value)


def _new_fixed_context(z3: Any) -> FixedProcessContext:
    return FixedProcessContext(fields=(
        ("target_transfer_1", z3.Int("target_transfer_1")),
        ("target_transfer_2", z3.Int("target_transfer_2")),
        ("original_level_1", z3.Int("original_level_1")),
        ("original_level_2", z3.Int("original_level_2")),
        ("tolerance_ml", _int(z3, 2)),
        ("scan_frequency_hz", _int(z3, 10)),
        ("dt", z3.RealVal("1/10")),
        ("flow_rate_1", _int(z3, 10)),
        ("flow_rate_2", _int(z3, 10)),
        ("success_reward", z3.RealVal(1)),
        ("running_reward", z3.RealVal("-1/100")),
        ("violation_reward", z3.RealVal(-1)),
    ))


def _new_variables(z3: Any, prefix: str) -> MixingVariables:
    return MixingVariables(
        physical_level_1=z3.Int(prefix + "physical_level_1"),
        physical_level_2=z3.Int(prefix + "physical_level_2"),
        sampled_level_1=z3.Int(prefix + "sampled_level_1"),
        sampled_level_2=z3.Int(prefix + "sampled_level_2"),
        pump_1_running=z3.Bool(prefix + "pump_1_running"),
        valve_1_open=z3.Bool(prefix + "valve_1_open"),
        pump_2_running=z3.Bool(prefix + "pump_2_running"),
        valve_2_open=z3.Bool(prefix + "valve_2_open"),
    )


def _new_buffer(z3: Any, prefix: str) -> MixingBuffer:
    def obs(label: str) -> tuple[Any, ...]:
        return tuple(z3.Int(f"{prefix}{label}_{index}") for index in range(6))
    return MixingBuffer(obs("current_observation"), obs("prior_observation_1"),
                        obs("prior_observation_2"), z3.Int(prefix + "prior_executed_action"))


def _equal(z3: Any, left: Iterable[Any], right: Iterable[Any]) -> Any:
    return z3.And(*(a == b for a, b in zip(left, right, strict=True)))


def _bit(z3: Any, action: Any, value: int) -> Any:
    return z3.Or(*(action == candidate for candidate in range(16)
                   if candidate & value))


def _fixed_domain(z3: Any, fixed: FixedProcessContext) -> Any:
    return z3.And(
        fixed["target_transfer_1"] >= 0,
        fixed["target_transfer_1"] <= 50,
        fixed["target_transfer_2"] >= 0,
        fixed["target_transfer_2"] <= 100,
        fixed["original_level_1"] >= 0,
        fixed["original_level_1"] <= 150,
        fixed["original_level_2"] >= 0,
        fixed["original_level_2"] <= 150,
        fixed["original_level_1"] >= fixed["target_transfer_1"],
        fixed["original_level_2"] >= fixed["target_transfer_2"],
    )


def _build_relation(*, source_sha256: str, prefix: str,
                    fixed_context: FixedProcessContext | None = None,
                    proposal: Any | None = None) -> MixingRelation:
    z3 = _z3()
    fixed = fixed_context or _new_fixed_context(z3)
    current = _new_variables(z3, prefix)
    proposed = proposal if proposal is not None else z3.Int(prefix + "proposal")
    sample_relation = z3.And(
        current.sampled_level_1 == current.physical_level_1 - fixed["tolerance_ml"],
        current.sampled_level_2 == current.physical_level_2 - fixed["tolerance_ml"],
    )
    domain = z3.And(
        _fixed_domain(z3, fixed),
        current.physical_level_1 >= 0,
        current.physical_level_1 <= fixed["original_level_1"],
        current.physical_level_2 >= 0,
        current.physical_level_2 <= fixed["original_level_2"],
    )
    pending_1 = (
        fixed["original_level_1"] - current.sampled_level_1
        < fixed["target_transfer_1"]
    )
    pending_2 = (
        fixed["original_level_2"] - current.sampled_level_2
        < fixed["target_transfer_2"]
    )
    required_action = z3.If(pending_1, 5, 0) + z3.If(pending_2, 10, 0)
    enabled = z3.And(proposed >= 0, proposed <= 15)
    dead = z3.Or(*(proposed == value for value in (1, 2, 3, 4, 6, 7, 8, 9, 11, 12, 13, 14)))
    satisfies = z3.And(
        _bit(z3, proposed, 1) == pending_1,
        _bit(z3, proposed, 4) == pending_1,
        _bit(z3, proposed, 2) == pending_2,
        _bit(z3, proposed, 8) == pending_2,
    )
    replaced = z3.Or(dead, z3.Not(satisfies))
    executed = z3.If(replaced, required_action, proposed)
    next_pump_1 = _bit(z3, executed, 4)
    next_valve_1 = _bit(z3, executed, 1)
    next_pump_2 = _bit(z3, executed, 8)
    next_valve_2 = _bit(z3, executed, 2)
    drain_1 = z3.If(z3.And(next_pump_1, next_valve_1), 1, 0)
    drain_2 = z3.If(z3.And(next_pump_2, next_valve_2), 1, 0)
    physical_next_1 = current.physical_level_1 - drain_1
    physical_next_2 = current.physical_level_2 - drain_2
    next_ = MixingVariables(
        physical_level_1=physical_next_1,
        physical_level_2=physical_next_2,
        sampled_level_1=physical_next_1 - fixed["tolerance_ml"],
        sampled_level_2=physical_next_2 - fixed["tolerance_ml"],
        pump_1_running=next_pump_1,
        valve_1_open=next_valve_1,
        pump_2_running=next_pump_2,
        valve_2_open=next_valve_2,
    )
    observation = (
        current.sampled_level_1, current.sampled_level_2,
        fixed["target_transfer_1"], fixed["target_transfer_2"],
        fixed["original_level_1"], fixed["original_level_2"],
    )
    next_observation = (
        next_.sampled_level_1, next_.sampled_level_2,
        fixed["target_transfer_1"], fixed["target_transfer_2"],
        fixed["original_level_1"], fixed["original_level_2"],
    )
    properties = (
        NamedPredicate("No Dry Running", "Prohibition", z3.And(
            z3.Implies(current.sampled_level_1 <= 0, z3.Not(next_pump_1)),
            z3.Implies(current.sampled_level_2 <= 0, z3.Not(next_pump_2)),
        )),
        NamedPredicate("No Dead Heading", "Prohibition", z3.And(
            z3.Implies(z3.Not(next_valve_1), z3.Not(next_pump_1)),
            z3.Implies(z3.Not(next_valve_2), z3.Not(next_pump_2)),
        )),
        NamedPredicate("Fluid Transfer Termination Safety", "Obligation", z3.And(
            z3.Implies(z3.Not(pending_1), z3.Not(next_pump_1)),
            z3.Implies(z3.Not(pending_2), z3.Not(next_pump_2)),
        )),
        NamedPredicate("Fluid Transfer Liveness", "Obligation", z3.And(
            z3.Implies(pending_1, z3.And(next_valve_1, next_pump_1)),
            z3.Implies(pending_2, z3.And(next_valve_2, next_pump_2)),
        )),
    )
    all_properties_hold = z3.And(*(item.expression for item in properties))
    completion = z3.And(z3.Not(pending_1), z3.Not(pending_2))
    outcome_tag = z3.If(z3.Not(all_properties_hold), 2, z3.If(completion, 0, 1))
    initial = z3.And(
        domain,
        current.physical_level_1 == fixed["original_level_1"],
        current.physical_level_2 == fixed["original_level_2"],
        sample_relation,
        z3.Not(current.pump_1_running), z3.Not(current.valve_1_open),
        z3.Not(current.pump_2_running), z3.Not(current.valve_2_open),
    )
    reward = z3.If(outcome_tag == 2, fixed["violation_reward"],
                   z3.If(outcome_tag == 0, fixed["success_reward"], fixed["running_reward"]))
    return MixingRelation(
        source_sha256, prefix, fixed, proposed, current, next_, domain, initial,
        sample_relation, enabled, completion, required_action, dead, satisfies,
        replaced, executed, observation, next_observation, properties,
        all_properties_hold, outcome_tag, reward, CONTRACT_ASSUMPTIONS,
    )


def _buffer_invariant(z3: Any, relation: MixingRelation, buffer: MixingBuffer) -> Any:
    return z3.And(
        relation.domain,
        relation.sample_relation,
        _equal(z3, buffer.current_observation, relation.observation),
        buffer.prior_executed_action >= 0,
        buffer.prior_executed_action <= 15,
        relation.current.valve_1_open == _bit(z3, buffer.prior_executed_action, 1),
        relation.current.valve_2_open == _bit(z3, buffer.prior_executed_action, 2),
        relation.current.pump_1_running == _bit(z3, buffer.prior_executed_action, 4),
        relation.current.pump_2_running == _bit(z3, buffer.prior_executed_action, 8),
    )


def _initial_buffer(z3: Any, relation: MixingRelation, buffer: MixingBuffer) -> Any:
    return z3.And(
        relation.initial,
        _equal(z3, buffer.current_observation, relation.observation),
        *(value == 0 for value in buffer.prior_observation_1),
        *(value == 0 for value in buffer.prior_observation_2),
        buffer.prior_executed_action == 0,
    )


def _next_buffer(relation: MixingRelation, buffer: MixingBuffer) -> tuple[Any, ...]:
    return (
        *relation.next_observation,
        *buffer.current_observation,
        *buffer.prior_observation_1,
        relation.executed_action,
    )


def _next_invariant(z3: Any, relation: MixingRelation, buffer: MixingBuffer) -> Any:
    replacements = list(zip(
        relation.current.expressions() + buffer.expressions(),
        relation.next.expressions() + _next_buffer(relation, buffer),
        strict=True,
    ))
    return z3.substitute(_buffer_invariant(z3, relation, buffer), *replacements)


def _validate_source(parser: SysMLParser) -> None:
    _require(parser.package_name == "TankFillingSystem", "expected TankFillingSystem package")
    _require(parser.system_part == "system" and parser.system_type == "FillingSystem",
             "unsupported system declaration")
    expected_parameters = {
        "system::pump1::maxFlowRateMl": 10,
        "system::pump2::maxFlowRateMl": 10,
        "system::feederTank1::capacityMl": 150,
        "system::feederTank2::capacityMl": 150,
        "system::controller::tank1TransferMl": 50,
        "system::controller::tank2TransferMl": 100,
        "system::controller::tank1OriginalLevelMl": 150,
        "system::controller::tank2OriginalLevelMl": 150,
    }
    actual_parameters = {item.qualified_name: Fraction(str(item.value)) for item in parser.parameters}
    for name, value in expected_parameters.items():
        _require(actual_parameters.get(name) == value, f"unexpected {name}")
    derived = {item.qualified_name: _expr_key(item.expression) for item in parser.derived_attributes}
    _require(derived.get("system::controller::toleranceMl") == ("literal", 2),
             "unsupported tolerance")
    _require(derived.get("system::controller::scanCycleFrequencyHz") == ("literal", 10),
             "unsupported scan frequency")
    _require(parser.parsed_bindings == {
        "system::feederTank1::volumeSensorPort::reading::volumeMl":
            "system::feederTank1::currentLevelMl",
        "system::feederTank2::volumeSensorPort::reading::volumeMl":
            "system::feederTank2::currentLevelMl",
        "system::fillingTank::volumeSensorPort::reading::volumeMl":
            "system::fillingTank::currentLevelMl",
    }, "unsupported physical-to-sensor bindings")
    _require({(item.from_port, item.to_port) for item in parser.flows} == {
        ("feederTank1.outlet", "pump1.inlet"),
        ("pump1.outlet", "valve1.inlet"),
        ("valve1.outlet", "fillingTank.inlet"),
        ("feederTank2.outlet", "pump2.inlet"),
        ("pump2.outlet", "valve2.inlet"),
        ("valve2.outlet", "fillingTank.inlet"),
        ("feederTank1.volumeSensorPort", "volumeSensor1.tankConnection"),
        ("feederTank2.volumeSensorPort", "volumeSensor2.tankConnection"),
    }, "unsupported fluid or sensor topology")
    _require(set(parser.connects) == {
        ("controller.pump1Port", "pump1.modbusPort"),
        ("controller.pump2Port", "pump2.modbusPort"),
        ("controller.valve1Port", "valve1.modbusPort"),
        ("controller.valve2Port", "valve2.modbusPort"),
        ("controller.volumeSensor1Port", "volumeSensor1.modbusPort"),
        ("controller.volumeSensor2Port", "volumeSensor2.modbusPort"),
    }, "unsupported Modbus topology")

    expected_tank_step = _parsed_expression(
        "currentLevelMl + (inlet.flowRateMl - outlet.flowRateMl) * dt"
    )
    tank_steps = [item for item in parser.step_actions if item.target_key.endswith("::currentLevelMl")]
    _require(len(tank_steps) == 3 and
             all(_expr_key(item.expression) == expected_tank_step for item in tank_steps),
             "unsupported tank dynamics")

    controller = parser.part_defs["Controller"]
    scan = next((action for action in controller.actions if action.name == "ScanCycle"), None)
    _require(scan is not None, "missing ScanCycle")
    assignments = {tuple(item.target): _expr_key(item.expr)
                   for item in scan.body if isinstance(item, AssignStmt)}
    _require(assignments.get(("observedLevel1",)) ==
             _parsed_expression("volume1Res.response - toleranceMl"),
             "unsupported first sampled-level update")
    _require(assignments.get(("observedLevel2",)) ==
             _parsed_expression("volume2Res.response - toleranceMl"),
             "unsupported second sampled-level update")
    policy = next((item for item in scan.body
                   if isinstance(item, SubactionCallStmt) and item.type_name == "Policy"), None)
    _require(policy is not None, "missing Policy call")
    bindings = {item.name: _expr_key(item.expr) for item in policy.bindings}
    expected_bindings = {
        "tank1VolumeMl": _parsed_expression("observedLevel1"),
        "tank2VolumeMl": _parsed_expression("observedLevel2"),
        "tank1TargetTransferMl": _parsed_expression("tank1TransferMl"),
        "tank2TargetTransferMl": _parsed_expression("tank2TransferMl"),
        "tank1OriginalMl": _parsed_expression("tank1OriginalLevelMl"),
        "tank2OriginalMl": _parsed_expression("tank2OriginalLevelMl"),
        "done": _parsed_expression(
            "(tank1OriginalLevelMl - observedLevel1 >= tank1TransferMl) and "
            "(tank2OriginalLevelMl - observedLevel2 >= tank2TransferMl)"
        ),
    }
    _require(bindings == expected_bindings, "unsupported Policy input bindings")
    performed = [(type(item.condition).__name__, item.body[0].action_name)
                 for item in scan.body if isinstance(item, IfStmt)
                 and len(item.body) == 1 and isinstance(item.body[0], PerformStmt)]
    _require([name for _condition, name in performed] == [
        "openValve1", "turnOnPump1", "turnOffPump1", "closeValve1",
        "openValve2", "turnOnPump2", "turnOffPump2", "closeValve2",
    ], "unsupported command application order")

    step = next((action for action in controller.actions if action.name == "step"), None)
    _require(step is not None and len(step.body) == 3 and isinstance(step.body[2], IfStmt),
             "unsupported controller schedule")
    _require(_expr_key(step.body[1].expr) == _parsed_expression("currentTimeSeconds + dt"),
             "unsupported controller clock")
    _require(_expr_key(step.body[2].condition) == _parsed_expression(
        "currentTimeSeconds - lastScanTimeSeconds >= 1.0 / scanCycleFrequencyHz"
    ), "unsupported scan guard")

    neural = next((item for item in controller.requirements
                   if item[0] == "Neural Controller Soundness"), None)
    _require(neural is not None and neural[4] == ["NeuralRequirement"],
             "missing neural requirement")
    expected_neural = _parsed_expression(
        "(p.tank1OriginalMl - p.tank1VolumeMl < p.tank1TargetTransferMl == p.shouldTurnOnPump1) and "
        "(p.shouldTurnOnPump1 == p.shouldOpenValve1) and "
        "(p.tank2OriginalMl - p.tank2VolumeMl < p.tank2TargetTransferMl == p.shouldTurnOnPump2) and "
        "(p.shouldTurnOnPump2 == p.shouldOpenValve2)"
    )
    _require(_expr_key(ExpressionParser(neural[3]).parse()) == expected_neural,
             "unsupported neural requirement")
    expected_properties = {
        "No Dry Running": "Prohibition",
        "No Dead Heading": "Prohibition",
        "Fluid Transfer Termination Safety": "Obligation",
        "Fluid Transfer Liveness": "Obligation",
    }
    _require({item.name: item.metadata[0] for item in parser.parsed_requirements}
             == expected_properties, "unsupported property inventory")
    scenario = [item for item in parser.parsed_constraints
                if item.metadata == ["ScenarioConstraint"]]
    _require(len(scenario) == 1 and _expr_key(scenario[0].expression) == _parsed_expression(
        "controller.tank1TransferMl >= 0 and controller.tank1TransferMl <= 50 and "
        "controller.tank2TransferMl >= 0 and controller.tank2TransferMl <= 100 and "
        "controller.tank1OriginalLevelMl >= 0 and controller.tank1OriginalLevelMl <= 150 and "
        "controller.tank2OriginalLevelMl >= 0 and controller.tank2OriginalLevelMl <= 150 and "
        "controller.tank1OriginalLevelMl >= controller.tank1TransferMl and "
        "controller.tank2OriginalLevelMl >= controller.tank2TransferMl and "
        "feederTank1.currentLevelMl == controller.tank1OriginalLevelMl and "
        "feederTank2.currentLevelMl == controller.tank2OriginalLevelMl"
    ), "unsupported scenario domain")
    expected_property_expressions = {
        "No Dry Running": _parsed_expression(
            "(s.controller.observedLevel1 <= 0 implies not s.pump1.isRunning) and "
            "(s.controller.observedLevel2 <= 0 implies not s.pump2.isRunning)"
        ),
        "No Dead Heading": _parsed_expression(
            "(not s.valve1.isOpen implies not s.pump1.isRunning) and "
            "(not s.valve2.isOpen implies not s.pump2.isRunning)"
        ),
        "Fluid Transfer Termination Safety": _parsed_expression(
            "s.controller.lastScanTimeSeconds > 0 implies "
            "((s.controller.tank1OriginalLevelMl - s.controller.observedLevel1 >= "
            "s.controller.tank1TransferMl) implies not s.pump1.isRunning) and "
            "((s.controller.tank2OriginalLevelMl - s.controller.observedLevel2 >= "
            "s.controller.tank2TransferMl) implies not s.pump2.isRunning)"
        ),
        "Fluid Transfer Liveness": _parsed_expression(
            "s.controller.lastScanTimeSeconds > 0 implies "
            "(((s.controller.tank1OriginalLevelMl - s.controller.observedLevel1 < "
            "s.controller.tank1TransferMl) implies (s.valve1.isOpen and s.pump1.isRunning)) and "
            "((s.controller.tank2OriginalLevelMl - s.controller.observedLevel2 < "
            "s.controller.tank2TransferMl) implies (s.valve2.isOpen and s.pump2.isRunning)))"
        ),
    }
    _require({item.name: _expr_key(item.expression) for item in parser.parsed_requirements}
             == expected_property_expressions, "unsupported property equations")


def validate_mixing_source(model_path: str | Path | None = None) -> str:
    selected = Path(model_path) if model_path is not None else DEFAULT_MODEL_PATH
    data = selected.read_bytes()
    _require(_git_blob_sha(data) == APPROVED_GIT_BLOB_SHA,
             "local source does not match authoritative standalone Git blob")
    source_sha256 = hashlib.sha256(data).hexdigest()
    _require(source_sha256 == APPROVED_SOURCE_SHA256,
             "local source does not match authoritative standalone SHA-256")
    parser = SysMLParser(str(selected))
    parser.parse()
    _validate_source(parser)
    return source_sha256


def extract_mixing_relation(model_path: str | Path | None = None, *, prefix: str = "") -> MixingRelation:
    source_sha256 = validate_mixing_source(model_path)
    return _build_relation(source_sha256=source_sha256, prefix=prefix)


def build_fixed_buffer_markov_query(model_path: str | Path | None = None) -> MixingPairedMarkovQuery:
    z3 = _z3()
    source_sha256 = validate_mixing_source(model_path)
    fixed = _new_fixed_context(z3)
    proposal = z3.Int("proposed_action")
    left = _build_relation(source_sha256=source_sha256, prefix="left_",
                           fixed_context=fixed, proposal=proposal)
    right = left.renamed("right_", fixed=fixed, proposal=proposal)
    left_buffer = _new_buffer(z3, "left_")
    right_buffer = _new_buffer(z3, "right_")
    left_invariant = _buffer_invariant(z3, left, left_buffer)
    right_invariant = _buffer_invariant(z3, right, right_buffer)
    next_buffers_equal = _equal(
        z3, _next_buffer(left, left_buffer), _next_buffer(right, right_buffer)
    )
    statuses_equal = _equal(
        z3,
        (item.expression for item in left.properties),
        (item.expression for item in right.properties),
    )
    result_difference = z3.Or(
        left.outcome_tag != right.outcome_tag,
        left.reward != right.reward,
        z3.Not(statuses_equal),
        z3.And(left.outcome_tag == 1, right.outcome_tag == 1, z3.Not(next_buffers_equal)),
    )
    counterexample = z3.And(
        left_invariant,
        right_invariant,
        _equal(z3, left_buffer.expressions(), right_buffer.expressions()),
        left.enabled,
        right.enabled,
        result_difference,
    )
    initial = _initial_buffer(z3, left, left_buffer)
    initial_empty = z3.And(
        _fixed_domain(z3, fixed),
        z3.Not(z3.Exists(left.current.expressions() + left_buffer.expressions(), initial)),
    )
    initial_contained = z3.And(initial, z3.Not(left_invariant))
    invariant_closed = z3.And(
        left_invariant,
        left.enabled,
        left.outcome_tag == 1,
        z3.Not(_next_invariant(z3, left, left_buffer)),
    )
    assumptions = CONTRACT_ASSUMPTIONS + (
        "the fixed controller buffer contains current observation, two prior observations, and one prior executed action",
        "the MDP action is the proposed action and shield execution is internal to the transition",
    )
    return MixingPairedMarkovQuery(
        left, right, left_buffer, right_buffer, initial_empty,
        initial_contained, invariant_closed, counterexample, assumptions,
    )


__all__ = [
    "APPROVED_GIT_BLOB_SHA",
    "APPROVED_SOURCE_SHA256",
    "AUTHORITATIVE_COMMIT",
    "AUTHORITATIVE_PATH",
    "AUTHORITATIVE_REPOSITORY",
    "CONTRACT_ASSUMPTIONS",
    "DEFAULT_MODEL_PATH",
    "MAX_SMT2_BYTES",
    "MixingBuffer",
    "MixingPairedMarkovQuery",
    "MixingRelation",
    "MixingVariables",
    "QUERY_NAME",
    "RESULT_SCHEMA",
    "SEMANTIC_PROFILE",
    "UnsupportedMixingModel",
    "build_fixed_buffer_markov_query",
    "extract_mixing_relation",
    "validate_mixing_source",
]
