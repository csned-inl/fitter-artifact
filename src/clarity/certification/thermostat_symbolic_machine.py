"""One exact-real thermostat SysML-to-Markov prototype.

The parser validates the specific source equations and relations used below;
Z3's native AST is the object representation.  It imports neither the legacy
``markov_*`` stack nor the simulator.  Per contract, `dt` and outside
temperature are fixed, setpoint is observed reset-selectable state, a proposed
action is deterministically shielded, each continuation updates once and shifts
a `(b_obs=2, b_act=1)` buffer, and pre-action completion stops the trace.

This is a source-to-contract theorem, not Python-simulator or generic SysML
semantics.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import hashlib
import importlib
from pathlib import Path
from typing import Any, Iterable

from clarity.sysml.parser import (
    BinaryExpr,
    Expr,
    ExpressionParser,
    LiteralExpr,
    RefExpr,
    SubactionCallStmt,
    SysMLParser,
    UnaryExpr,
)
from clarity.certification.symbolic_process import FixedProcessContext

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_MODEL_PATH = ROOT / "src" / "clarity" / "models" / "thermostat" / "model.sysml"
FIXED_DT = Fraction(1, 10)
QUERY_NAME = "thermostat_exact_real_bobs2_bact1_v1"
SEMANTIC_PROFILE = "thermostat_atomic_exact_real_v1"
APPROVED_SOURCE_SHA256 = "44dec2333511c5cf7a40ad1828b612a043f9c7c71f6b960c276ce866f4a521aa"
MAX_SMT2_BYTES = 16_384
RESULT_SCHEMA = ("Stop(reward)", "Continue(reward, next_buffer)")
CONTRACT_ASSUMPTIONS = (
    "the approved source hash and thermostat_atomic_exact_real_v1 profile are exact",
    "dt is the exact real value 1/10",
    "outside temperature is fixed for one MDP instance, including every reset",
    "setpoint is reset-selectable state and is observed exactly",
    "reset sets physical and sensor temperature to 23.9, time to zero, actuator flags/outputs off, and history to zero",
    "physical and sensor temperature are distinct state fields constrained equal only by this synchronous profile",
    "one step observes setpoint/sensor temperature and tests completion before any action",
    "on Continue SpecShield keeps a live requirement-satisfying proposal and otherwise replaces it",
    "actuator state/output updates atomically with no queue, delay, error, or unmatched-command effect",
    "the exact thermal and clock equations run once, the sensor samples physical temperature, then the buffer shifts",
    "Stop returns only reward 1; Continue returns reward -0.01 and the next buffer",
    "there are no reset, truncation, error, status, or other controller-visible result fields",
    "all arithmetic is exact real arithmetic",
)


class UnsupportedThermostatModel(ValueError):
    """The source does not match the deliberately small supported profile."""


def _z3() -> Any:
    try:
        return importlib.import_module("z3")
    except ModuleNotFoundError as error:  # pragma: no cover - workstation/CI dependent
        raise RuntimeError("z3-solver is required for symbolic-machine construction") from error


def _fraction(value: int | float | Fraction) -> Fraction:
    if isinstance(value, Fraction):
        return value
    return Fraction(str(value))


def _real(z3: Any, value: int | float | Fraction) -> Any:
    number = _fraction(value)
    return z3.RealVal(f"{number.numerator}/{number.denominator}")


def _expr_key(expr: Expr) -> tuple:
    """A lossless-enough structural key for this parser's supported AST."""

    if isinstance(expr, LiteralExpr):
        value = expr.value
        if isinstance(value, float):
            value = str(value)
        return ("literal", value)
    if isinstance(expr, RefExpr):
        return ("ref", tuple(expr.path))
    if isinstance(expr, UnaryExpr):
        return ("unary", expr.op, _expr_key(expr.operand))
    if isinstance(expr, BinaryExpr):
        return ("binary", expr.op, _expr_key(expr.left), _expr_key(expr.right))
    raise UnsupportedThermostatModel(f"unsupported source expression: {type(expr).__name__}")


def _parsed_expression(text: str) -> tuple:
    return _expr_key(ExpressionParser(text).parse())


def _find_parameter(parser: SysMLParser, key: str) -> Fraction:
    for parameter in parser.parameters:
        if parameter.qualified_name == key:
            return _fraction(parameter.value)
    raise UnsupportedThermostatModel(f"missing source parameter {key}")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise UnsupportedThermostatModel(message)


@dataclass(frozen=True, slots=True)
class NamedPredicate:
    name: str
    role: str
    expression: Any


@dataclass(frozen=True, slots=True)
class ThermostatVariables:
    physical_temperature: Any
    sensor_temperature: Any
    setpoint: Any
    heater_on: Any
    ac_on: Any
    current_time: Any

    def expressions(self) -> tuple[Any, ...]:
        return (self.physical_temperature, self.sensor_temperature, self.setpoint,
                self.heater_on, self.ac_on, self.current_time)


@dataclass(frozen=True, slots=True)
class BufferVariables:
    current_setpoint: Any
    current_temperature: Any
    prior_1_setpoint: Any
    prior_1_temperature: Any
    prior_2_setpoint: Any
    prior_2_temperature: Any
    prior_executed_action: Any

    def expressions(self) -> tuple[Any, ...]:
        return (self.current_setpoint, self.current_temperature, self.prior_1_setpoint,
                self.prior_1_temperature, self.prior_2_setpoint, self.prior_2_temperature,
                self.prior_executed_action)


@dataclass(frozen=True, slots=True)
class ThermostatRelation:
    """One Z3-native instance of the thermostat symbolic machine."""

    source_sha256: str
    prefix: str
    fixed: FixedProcessContext
    proposal: Any
    current: ThermostatVariables
    next: ThermostatVariables
    domain: Any
    initial: Any
    sensor_relation: Any
    enabled: Any
    completion: Any
    required_action: Any
    proposal_is_dead: Any
    proposal_satisfies_requirement: Any
    shield_replaced_proposal: Any
    executed_action: Any
    outcome_tag: Any
    observation: tuple[Any, Any]
    next_observation: tuple[Any, Any]
    reward: Any
    properties: tuple[NamedPredicate, ...]
    assumptions: tuple[str, ...]

    def renamed(self, prefix: str, *, fixed: FixedProcessContext | None = None,
                proposal: Any | None = None) -> "ThermostatRelation":
        """Build an independent state copy while sharing contract-fixed values."""

        return _build_relation(source_sha256=self.source_sha256, prefix=prefix,
                               fixed_parameters=fixed or self.fixed, proposal=proposal)

    def summary(self) -> dict[str, object]:
        return {
            "query": QUERY_NAME,
            "source_sha256": self.source_sha256,
            "fixed_parameters": self.fixed.names(),
            "observation": ("setpoint", "sensor_temperature"),
            "sensor_relation": "sensor_temperature == physical_temperature at decision epochs",
            "proposed_action": "integer in {0, 1, 2, 3}",
            "executed_action": "SpecShield keep-valid-otherwise-replace decision",
            "result_schema": RESULT_SCHEMA,
            "properties": tuple((item.name, item.role) for item in self.properties),
            "assumptions": self.assumptions,
        }


@dataclass(frozen=True, slots=True)
class ThermostatPairedMarkovQuery:
    """Two relation instances and the bounded exact-real Markov obligations."""

    left: ThermostatRelation
    right: ThermostatRelation
    left_buffer: BufferVariables
    right_buffer: BufferVariables
    initial_empty: Any
    initial_contained: Any
    invariant_closed: Any
    counterexample: Any
    source_sha256: str
    assumptions: tuple[str, ...]

    def smt2(self) -> str:
        z3 = _z3()
        solver = z3.Solver()
        solver.add(self.counterexample)
        return solver.to_smt2()

    def run(self, *, timeout_ms: int = 5_000) -> dict[str, object]:
        """Run only the bounded source-contract checks for this one query."""

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
        initial = check(self.initial_empty)
        contained = check(self.initial_contained)
        closure = check(self.invariant_closed)
        markov = check(self.counterexample)
        if initial != "unsat":
            return {"classification": "NO_RESULT", "reason": "initialization", "initial": initial}
        if contained != "unsat":
            return {"classification": "NO_RESULT", "reason": "initial_containment", "contained": contained}
        if closure != "unsat":
            return {"classification": "NO_RESULT", "reason": "invariant_closure", "closure": closure}
        if markov == "unsat":
            return {
                "classification": "CERTIFIED_UNDER_CONTRACT",
                "initial": initial,
                "initial_contained": contained,
                "closure": closure,
                "markov": markov,
                "smt2_bytes": sizes,
            }
        # This bounded relation uses an inductive overapproximation.  A SAT
        # pair is diagnostic until a source-valid history witness is added.
        return {"classification": "NO_RESULT", "reason": "counterexample_or_unknown", "markov": markov}


def _action_has_heater(z3: Any, action: Any) -> Any:
    return z3.Or(action == 1, action == 3)


def _action_has_ac(z3: Any, action: Any) -> Any:
    return z3.Or(action == 2, action == 3)


def _new_thermostat_fixed_context(
    z3: Any, *, outside_temperature: Any | None = None
) -> FixedProcessContext:
    outside = outside_temperature if outside_temperature is not None else z3.Real("outside_temperature")
    return FixedProcessContext(fields=(
        ("outside_temperature", outside),
        ("dt", _real(z3, FIXED_DT)),
        ("tolerance", _real(z3, 1)),
        ("volume", _real(z3, 20)),
        ("heat_loss_coefficient", _real(z3, 10)),
        ("heater_power", _real(z3, 2000)),
        ("ac_power", _real(z3, -2000)),
        ("air_density", _real(z3, Fraction(6, 5))),
        ("heat_capacity", _real(z3, 1005)),
        ("initial_temperature", _real(z3, Fraction(239, 10))),
        ("stop_reward", _real(z3, 1)),
        ("continue_reward", _real(z3, Fraction(-1, 100))),
    ))


def _new_variables(z3: Any, prefix: str) -> ThermostatVariables:
    return ThermostatVariables(
        physical_temperature=z3.Real(prefix + "physical_temperature"),
        sensor_temperature=z3.Real(prefix + "sensor_temperature"),
        setpoint=z3.Real(prefix + "setpoint"),
        heater_on=z3.Bool(prefix + "heater_on"),
        ac_on=z3.Bool(prefix + "ac_on"),
        current_time=z3.Real(prefix + "current_time"),
    )


def _new_buffer(z3: Any, prefix: str) -> BufferVariables:
    return BufferVariables(
        current_setpoint=z3.Real(prefix + "buffer_current_setpoint"),
        current_temperature=z3.Real(prefix + "buffer_current_temperature"),
        prior_1_setpoint=z3.Real(prefix + "buffer_prior_1_setpoint"),
        prior_1_temperature=z3.Real(prefix + "buffer_prior_1_temperature"),
        prior_2_setpoint=z3.Real(prefix + "buffer_prior_2_setpoint"),
        prior_2_temperature=z3.Real(prefix + "buffer_prior_2_temperature"),
        prior_executed_action=z3.Int(prefix + "buffer_prior_executed_action"),
    )


def _equal(z3: Any, left: Iterable[Any], right: Iterable[Any]) -> Any:
    return z3.And(*(a == b for a, b in zip(left, right, strict=True)))


def _buffer_invariant(z3: Any, relation: ThermostatRelation, buffer: BufferVariables) -> Any:
    return z3.And(
        relation.domain,
        buffer.current_setpoint == relation.current.setpoint,
        buffer.current_temperature == relation.current.sensor_temperature,
        relation.sensor_relation,
        buffer.prior_executed_action >= 0,
        buffer.prior_executed_action <= 3,
        relation.current.heater_on == _action_has_heater(z3, buffer.prior_executed_action),
        relation.current.ac_on == _action_has_ac(z3, buffer.prior_executed_action),
        relation.current.current_time >= _real(z3, 0),
    )


def _initial_buffer(z3: Any, relation: ThermostatRelation, buffer: BufferVariables) -> Any:
    return z3.And(
        relation.initial,
        buffer.current_setpoint == relation.current.setpoint,
        buffer.current_temperature == relation.current.sensor_temperature,
        buffer.prior_1_setpoint == _real(z3, 0),
        buffer.prior_1_temperature == _real(z3, 0),
        buffer.prior_2_setpoint == _real(z3, 0),
        buffer.prior_2_temperature == _real(z3, 0),
        buffer.prior_executed_action == 0,
    )


def _next_buffer(relation: ThermostatRelation, buffer: BufferVariables) -> tuple[Any, ...]:
    return (
        relation.next.setpoint,
        relation.next.sensor_temperature,
        buffer.current_setpoint,
        buffer.current_temperature,
        buffer.prior_1_setpoint,
        buffer.prior_1_temperature,
        relation.executed_action,
    )


def _next_invariant(z3: Any, relation: ThermostatRelation, buffer: BufferVariables) -> Any:
    replacements = list(zip(
        relation.current.expressions() + buffer.expressions(),
        relation.next.expressions() + _next_buffer(relation, buffer),
        strict=True,
    ))
    current = _buffer_invariant(z3, relation, buffer)
    return z3.substitute(current, *replacements)


def _build_relation(*, source_sha256: str, prefix: str,
                    fixed_parameters: FixedProcessContext | None = None,
                    proposal: Any | None = None) -> ThermostatRelation:
    z3 = _z3()
    current = _new_variables(z3, prefix)
    fixed = fixed_parameters or _new_thermostat_fixed_context(
        z3, outside_temperature=z3.Real(prefix + "outside_temperature"))
    proposed = proposal if proposal is not None else z3.Int(prefix + "proposal")

    domain = z3.And(
        current.setpoint >= _real(z3, 13),
        current.setpoint <= _real(z3, 33),
        fixed["outside_temperature"] >= _real(z3, -10),
        fixed["outside_temperature"] <= _real(z3, 50),
    )
    sensor_relation = current.sensor_temperature == current.physical_temperature
    enabled = z3.And(proposed >= 0, proposed <= 3)
    heater_required = current.setpoint >= current.sensor_temperature + fixed["tolerance"]
    ac_required = current.setpoint <= current.sensor_temperature - fixed["tolerance"]
    required_action = z3.If(heater_required, 1, 0) + z3.If(ac_required, 2, 0)
    proposal_heater = _action_has_heater(z3, proposed)
    proposal_ac = _action_has_ac(z3, proposed)
    proposal_is_dead = proposed == 3
    proposal_satisfies = z3.And(
        z3.Not(proposal_is_dead),
        proposal_heater == heater_required,
        proposal_ac == ac_required,
        z3.Not(z3.And(proposal_heater, proposal_ac)),
    )
    shield_replaced = z3.Or(proposal_is_dead, z3.Not(proposal_satisfies))
    executed = z3.If(shield_replaced, required_action, proposed)
    completion = z3.And(
        current.setpoint <= current.sensor_temperature + fixed["tolerance"],
        current.setpoint >= current.sensor_temperature - fixed["tolerance"],
        z3.Not(current.heater_on),
        z3.Not(current.ac_on),
    )
    net_power = z3.If(
        _action_has_heater(z3, executed), fixed["heater_power"], _real(z3, 0)
    ) + z3.If(
        _action_has_ac(z3, executed), fixed["ac_power"], _real(z3, 0)
    )
    temperature_next = current.physical_temperature + (
        (net_power - fixed["heat_loss_coefficient"]
         * (current.physical_temperature - fixed["outside_temperature"])) * fixed["dt"]
        / (fixed["volume"] * fixed["air_density"] * fixed["heat_capacity"])
    )
    # The successor and result are expressions, not a satisfiable relation.
    # Therefore every enabled input has exactly one Stop or Continue result.
    next_ = ThermostatVariables(
        physical_temperature=temperature_next,
        sensor_temperature=temperature_next,
        setpoint=current.setpoint,
        heater_on=_action_has_heater(z3, executed),
        ac_on=_action_has_ac(z3, executed),
        current_time=current.current_time + fixed["dt"],
    )
    initial = z3.And(
        domain,
        current.physical_temperature == fixed["initial_temperature"],
        current.sensor_temperature == fixed["initial_temperature"],
        current.heater_on == z3.BoolVal(False),
        current.ac_on == z3.BoolVal(False),
        current.current_time == _real(z3, 0),
    )
    properties = (
        NamedPredicate(
            "No Simultaneous Heating and Cooling",
            "monitored",
            z3.Not(z3.And(current.heater_on, current.ac_on)),
        ),
        NamedPredicate(
            "Heat When Cold",
            "monitored",
            z3.Implies(
                current.current_time > _real(z3, 0),
                z3.Implies(current.sensor_temperature < current.setpoint - fixed["tolerance"],
                           current.heater_on),
            ),
        ),
        NamedPredicate(
            "Cool When Hot",
            "monitored",
            z3.Implies(
                current.current_time > _real(z3, 0),
                z3.Implies(current.sensor_temperature > current.setpoint + fixed["tolerance"],
                           current.ac_on),
            ),
        ),
    )
    return ThermostatRelation(
        source_sha256=source_sha256,
        prefix=prefix,
        fixed=fixed,
        proposal=proposed,
        current=current,
        next=next_,
        domain=domain,
        initial=initial,
        sensor_relation=sensor_relation,
        enabled=enabled,
        completion=completion,
        required_action=required_action,
        proposal_is_dead=proposal_is_dead,
        proposal_satisfies_requirement=proposal_satisfies,
        shield_replaced_proposal=shield_replaced,
        executed_action=executed,
        outcome_tag=z3.If(completion, 0, 1),
        observation=(current.setpoint, current.sensor_temperature),
        next_observation=(next_.setpoint, next_.sensor_temperature),
        reward=z3.If(completion, fixed["stop_reward"], fixed["continue_reward"]),
        properties=properties,
        assumptions=CONTRACT_ASSUMPTIONS,
    )


def _validate_source(parser: SysMLParser) -> None:
    """Reject source changes rather than silently reinterpreting the model."""

    _require(parser.package_name == "Thermostat", "expected Thermostat package")
    expected_parameters = {
        "system::environment::temperatureCelcius": Fraction(239, 10),
        "system::environment::volumeMetersCubed": Fraction(20),
        "system::environment::outsideTemperatureCelcius": Fraction(5),
        "system::environment::heatLossCoefficientWattsPerCelcius": Fraction(10),
        "system::ac::heatOutputWatts": Fraction(-2000),
        "system::heater::heatOutputWatts": Fraction(2000),
        "system::controller::setPointCelcius": Fraction(183, 10),
        "system::controller::toleranceCelcius": Fraction(1),
        "system::currentTime": Fraction(0),
    }
    for key, value in expected_parameters.items():
        _require(_find_parameter(parser, key) == value, f"unexpected {key}")

    expected_temperature = _parsed_expression(
        "temperatureCelcius + ((heaterPort.heat.rateWatts + acPort.heat.rateWatts "
        "- heatLossCoefficientWattsPerCelcius * (temperatureCelcius - outsideTemperatureCelcius)) "
        "* dt) / (volumeMetersCubed * 1.2 * 1005)"
    )
    temperature_step = next((item for item in parser.step_actions
                             if item.target_key == "system::environment::temperatureCelcius"), None)
    _require(temperature_step is not None, "missing thermal step")
    _require(_expr_key(temperature_step.expression) == expected_temperature,
             "unsupported thermal equation")

    expected_time = _parsed_expression("currentTime + dt")
    time_step = next((item for item in parser.step_actions if item.target_key == "system::currentTime"), None)
    _require(time_step is not None and _expr_key(time_step.expression) == expected_time,
             "unsupported clock equation")
    _require(
        parser.parsed_bindings.get("system::environment::temperaturePort::reading::temperatureCelcius")
        == "system::environment::temperatureCelcius",
        "unsupported environment-to-sensor binding",
    )
    sensor_step = next((action for action in parser.part_defs["TemperatureSensor"].actions
                        if action.name == "step"), None)
    _require(sensor_step is not None, "missing sensor step")
    sensor_assignments = {
        tuple(item.target): _expr_key(item.expr) for item in sensor_step.body
        if hasattr(item, "target") and hasattr(item, "expr")
    }
    _require(sensor_assignments == {
        ("temperatureReading", "temperatureCelcius"):
            ("ref", ("environmentPort", "reading", "temperatureCelcius")),
        ("lastReadingCelcius",): ("ref", ("temperatureReading", "temperatureCelcius")),
    } and any(getattr(item, "port", None) == "upstreamPort" for item in sensor_step.body),
             "unsupported sensor transfer")
    _require({(flow.from_port, flow.to_port) for flow in parser.flows} == {
        ("ac.heatOut", "environment.acPort"),
        ("heater.heatOut", "environment.heaterPort"),
        ("environment.temperaturePort", "thermometer.environmentPort"),
    }, "unsupported thermal flow topology")
    _require(set(parser.connects) == {
        ("controller.acControlPort", "ac.cmdIn"),
        ("controller.heaterControlPort", "heater.cmdIn"),
        ("thermometer.upstreamPort", "controller.thermometerPort"),
    }, "unsupported controller connection topology")

    controller = parser.part_defs["Controller"]
    controller_step = next((action for action in controller.actions if action.name == "step"), None)
    _require(controller_step is not None, "missing controller step")
    policy_call = next((stmt for stmt in controller_step.body
                        if isinstance(stmt, SubactionCallStmt) and stmt.type_name == "Policy"), None)
    _require(policy_call is not None, "missing Policy invocation")
    done_binding = next((item for item in policy_call.bindings if item.name == "done"), None)
    expected_done = _parsed_expression(
        "setPointCelcius <= reading.temperatureCelcius + toleranceCelcius and "
        "setPointCelcius >= reading.temperatureCelcius - toleranceCelcius and "
        "not heaterOn and not acOn"
    )
    _require(done_binding is not None and _expr_key(done_binding.expr) == expected_done,
             "unsupported completion equation")
    policy_assignments = {
        tuple(item.target): _expr_key(item.expr) for item in controller_step.body
        if hasattr(item, "target") and hasattr(item, "expr")
    }
    _require(policy_assignments == {
        ("heaterOn",): ("ref", ("policyCall", "heaterState")),
        ("acOn",): ("ref", ("policyCall", "acState")),
    }, "unsupported policy-to-flag assignments")
    command_rules = []
    for item in controller_step.body:
        if not hasattr(item, "condition"):
            continue
        _require(len(item.body) == 2, "unsupported actuator command body")
        declaration, send = item.body
        command_rules.append((_expr_key(item.condition),
                              getattr(declaration, "type_name", None),
                              getattr(send, "port", None)))
    _require(command_rules == [
        (("unary", "not", ("ref", ("heaterOn",))), "HeatingCoolingResetCmd", "heaterControlPort"),
        (("unary", "not", ("ref", ("acOn",))), "HeatingCoolingResetCmd", "acControlPort"),
        (("ref", ("policyCall", "heaterState")), "HeatingCoolingOnCmd", "heaterControlPort"),
        (("ref", ("policyCall", "acState")), "HeatingCoolingOnCmd", "acControlPort"),
    ], "unsupported actuator command rules")
    neural = next((item for item in controller.requirements
                   if item[0] == "Neural Controller Soundness"), None)
    _require(neural is not None and neural[4] == ["NeuralRequirement"],
             "missing neural requirement")
    expected_neural = _parsed_expression(
        "(p.setPoint >= p.temperatureCelcius + toleranceCelcius == p.heaterState) and "
        "(p.setPoint <= p.temperatureCelcius - toleranceCelcius == p.acState) and "
        "(not (p.heaterState and p.acState))"
    )
    _require(_expr_key(ExpressionParser(neural[3]).parse()) == expected_neural,
             "unsupported neural requirement")

    expected_properties = {
        "No Simultaneous Heating and Cooling": "Prohibition",
        "Heat When Cold": "Obligation",
        "Cool When Hot": "Obligation",
    }
    _require({item.name: item.metadata[0] for item in parser.parsed_requirements} == expected_properties,
             "unsupported thermostat property set")
    _require(len(parser.state_machines) == 1, "unsupported actuator state machines")
    machine = parser.state_machines[0]
    _require(machine.initial_state == "reset" and {state.name for state in machine.states} == {"reset", "on"},
             "unsupported actuator modes")
    _require({(item.from_state, item.to_state, item.trigger) for item in machine.transitions} == {
        ("reset", "on", "HeatingCoolingOnCmd"),
        ("on", "reset", "HeatingCoolingResetCmd"),
    }, "unsupported actuator transitions")
    transition_effects = {
        (item.from_state, item.to_state, item.trigger): (
            tuple(item.do_action[0].target) if item.do_action and len(item.do_action) == 1 else None,
            _expr_key(item.do_action[0].expr) if item.do_action and len(item.do_action) == 1 else None,
        )
        for item in machine.transitions
    }
    _require(transition_effects == {
        ("reset", "on", "HeatingCoolingOnCmd"):
            (("heatOut", "heat", "rateWatts"), ("ref", ("heatOutputWatts",))),
        ("on", "reset", "HeatingCoolingResetCmd"):
            (("heatOut", "heat", "rateWatts"), ("literal", "0.0")),
    }, "unsupported actuator transition effects")
    _require(len(parser.parsed_constraints) == 1 and
             parser.parsed_constraints[0].metadata == ["ScenarioConstraint"],
             "unsupported scenario domain")
    expected_domain = _parsed_expression(
        "controller.setPointCelcius >= 13.00 and controller.setPointCelcius <= 33.00 and "
        "environment.outsideTemperatureCelcius <= 50 and environment.outsideTemperatureCelcius >= -10"
    )
    _require(_expr_key(parser.parsed_constraints[0].expression) == expected_domain,
             "unsupported scenario domain equation")

    expected_property_expressions = {
        "No Simultaneous Heating and Cooling": _parsed_expression(
            "not (s.controller.heaterOn and s.controller.acOn)"
        ),
        "Heat When Cold": _parsed_expression(
            "currentTime > 0 implies (s.lastObservedTemperature < "
            "s.controller.setPointCelcius - s.controller.toleranceCelcius implies "
            "s.controller.heaterOn)"
        ),
        "Cool When Hot": _parsed_expression(
            "currentTime > 0 implies (s.lastObservedTemperature > "
            "s.controller.setPointCelcius + s.controller.toleranceCelcius implies "
            "s.controller.acOn)"
        ),
    }
    _require(
        {item.name: _expr_key(item.expression) for item in parser.parsed_requirements}
        == expected_property_expressions,
        "unsupported thermostat property equations",
    )


def validate_thermostat_source(model_path: str | Path | None = None) -> str:
    """Validate the supported source profile without importing Z3.

    This is intentionally useful in environments where the pinned solver is
    unavailable: source drift is caught before any symbolic formula is built.
    """

    selected = Path(model_path) if model_path is not None else DEFAULT_MODEL_PATH
    source_sha256 = hashlib.sha256(selected.read_bytes()).hexdigest()
    _require(source_sha256 == APPROVED_SOURCE_SHA256,
             "source does not match the approved thermostat source hash")
    parser = SysMLParser(str(selected))
    parser.parse()
    _validate_source(parser)
    return source_sha256


def extract_thermostat_relation(model_path: str | Path | None = None, *, prefix: str = "") -> ThermostatRelation:
    """Validate the source and return its one bounded symbolic-machine instance."""

    selected = Path(model_path) if model_path is not None else DEFAULT_MODEL_PATH
    source_sha256 = validate_thermostat_source(selected)
    return _build_relation(source_sha256=source_sha256, prefix=prefix)


def build_fixed_buffer_markov_query(model_path: str | Path | None = None) -> ThermostatPairedMarkovQuery:
    """Build the one exact-real `(b_obs=2, b_act=1)` paired query.

    Outside temperature and proposed action are shared.  The setpoint remains
    an observed reset-selectable state in each copy.
    """

    z3 = _z3()
    source = extract_thermostat_relation(model_path, prefix="left_")
    fixed = _new_thermostat_fixed_context(z3)
    proposal = z3.Int("proposed_action")
    left = _build_relation(
        source_sha256=source.source_sha256,
        prefix="left_",
        fixed_parameters=fixed,
        proposal=proposal,
    )
    right = source.renamed("right_", fixed=fixed, proposal=proposal)
    left_buffer = _new_buffer(z3, "left_")
    right_buffer = _new_buffer(z3, "right_")
    left_invariant = _buffer_invariant(z3, left, left_buffer)
    right_invariant = _buffer_invariant(z3, right, right_buffer)
    next_buffers_equal = _equal(z3, _next_buffer(left, left_buffer), _next_buffer(right, right_buffer))
    result_difference = z3.Or(
        left.outcome_tag != right.outcome_tag,
        left.reward != right.reward,
        z3.And(z3.Not(left.completion), z3.Not(right.completion), z3.Not(next_buffers_equal)),
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
        fixed["outside_temperature"] >= _real(z3, -10),
        fixed["outside_temperature"] <= _real(z3, 50),
        z3.Not(z3.Exists(left.current.expressions() + left_buffer.expressions(), initial)),
    )
    initial_contained = z3.And(initial, z3.Not(left_invariant))
    invariant_closed = z3.And(
        left_invariant,
        left.enabled,
        z3.Not(left.completion),
        z3.Not(_next_invariant(z3, left, left_buffer)),
    )
    assumptions = source.assumptions + (
        "the buffer stores exact-real current observation, two prior observations, and one prior executed action",
        "the MDP action is the proposed action; shield execution is internal to the transition",
    )
    return ThermostatPairedMarkovQuery(
        left=left,
        right=right,
        left_buffer=left_buffer,
        right_buffer=right_buffer,
        initial_empty=initial_empty,
        initial_contained=initial_contained,
        invariant_closed=invariant_closed,
        counterexample=counterexample,
        source_sha256=source.source_sha256,
        assumptions=assumptions,
    )


__all__ = [
    "BufferVariables",
    "APPROVED_SOURCE_SHA256",
    "CONTRACT_ASSUMPTIONS",
    "DEFAULT_MODEL_PATH",
    "FIXED_DT",
    "MAX_SMT2_BYTES",
    "NamedPredicate",
    "QUERY_NAME",
    "RESULT_SCHEMA",
    "SEMANTIC_PROFILE",
    "ThermostatPairedMarkovQuery",
    "FixedProcessContext",
    "ThermostatRelation",
    "ThermostatVariables",
    "UnsupportedThermostatModel",
    "build_fixed_buffer_markov_query",
    "extract_thermostat_relation",
    "validate_thermostat_source",
]
