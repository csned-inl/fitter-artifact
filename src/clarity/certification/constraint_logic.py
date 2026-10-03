"""Typed logical meaning of the direct-SysML symbolic process IR.

The classes in this module are deliberately independent of both the SysML
parser AST and any particular solver.  They define the quantifier-free,
many-sorted constraint logic used by the certification prototypes.  A separate
compiler gives accepted source expressions this meaning; a separate backend
lowers the resulting logic to Z3.

This separation does not prove the source parser correct.  It makes the
post-parse semantic claim explicit and gives tests and proof-rule validators a
stable object to reason about.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from fractions import Fraction
import hashlib
import json
from typing import Any, Iterable, Mapping

from clarity.sysml.parser import (
    BinaryExpr,
    Expr,
    LiteralExpr,
    RefExpr,
    TernaryExpr,
    UnaryExpr,
)


LOGIC_PROFILE = "ot-constraint-logic-0.1"


class LogicSort(str, Enum):
    BOOL = "Bool"
    INT = "Int"
    REAL = "Real"


NUMERIC_SORTS = {LogicSort.INT, LogicSort.REAL}


@dataclass(frozen=True, slots=True)
class LogicTerm:
    """A fully typed term in quantifier-free Boolean/linear arithmetic logic."""

    op: str
    sort: LogicSort
    args: tuple["LogicTerm", ...] = ()
    value: bool | Fraction | None = None
    name: str | None = None

    def __post_init__(self) -> None:
        if self.op == "symbol":
            if not self.name or self.args or self.value is not None:
                raise ValueError("a symbol requires only a nonempty name")
            return
        if self.op == "literal":
            if self.name is not None or self.args:
                raise ValueError("a literal cannot have a name or arguments")
            if self.sort is LogicSort.BOOL:
                if type(self.value) is not bool:
                    raise ValueError("a Bool literal requires a Boolean value")
            elif not isinstance(self.value, Fraction):
                raise ValueError("a numeric literal requires an exact Fraction")
            elif self.sort is LogicSort.INT and self.value.denominator != 1:
                raise ValueError("an Int literal must be integral")
            return
        if self.name is not None or self.value is not None:
            raise ValueError("an application cannot carry a name or literal value")
        _check_application(self.op, self.sort, self.args)

    def as_dict(self) -> dict[str, object]:
        result: dict[str, object] = {"op": self.op, "sort": self.sort.value}
        if self.name is not None:
            result["name"] = self.name
        elif self.op == "literal":
            result["value"] = (
                self.value
                if type(self.value) is bool
                else f"{self.value.numerator}/{self.value.denominator}"
            )
        else:
            result["args"] = tuple(item.as_dict() for item in self.args)
        return result


def _require_arity(op: str, args: tuple[LogicTerm, ...], arity: int) -> None:
    if len(args) != arity:
        raise ValueError(f"{op} requires {arity} arguments")


def _check_application(op: str, sort: LogicSort, args: tuple[LogicTerm, ...]) -> None:
    if op == "not":
        _require_arity(op, args, 1)
        if sort is not LogicSort.BOOL or args[0].sort is not LogicSort.BOOL:
            raise ValueError("not requires and returns Bool")
        return
    if op == "neg":
        _require_arity(op, args, 1)
        if sort not in NUMERIC_SORTS or args[0].sort not in NUMERIC_SORTS:
            raise ValueError("neg requires and returns a numeric sort")
        if sort is not args[0].sort:
            raise ValueError("neg preserves its operand sort")
        return
    if op in {"and", "or", "implies"}:
        _require_arity(op, args, 2)
        if sort is not LogicSort.BOOL or any(
            item.sort is not LogicSort.BOOL for item in args
        ):
            raise ValueError(f"{op} requires and returns Bool")
        return
    if op == "eq":
        _require_arity(op, args, 2)
        compatible = args[0].sort is args[1].sort or {
            args[0].sort, args[1].sort
        } <= NUMERIC_SORTS
        if sort is not LogicSort.BOOL or not compatible:
            raise ValueError("eq requires compatible operands and returns Bool")
        return
    if op in {"lt", "le", "gt", "ge"}:
        _require_arity(op, args, 2)
        if sort is not LogicSort.BOOL or any(
            item.sort not in NUMERIC_SORTS for item in args
        ):
            raise ValueError(f"{op} requires numeric operands and returns Bool")
        return
    if op in {"add", "sub", "mul", "div"}:
        _require_arity(op, args, 2)
        if sort not in NUMERIC_SORTS or any(
            item.sort not in NUMERIC_SORTS for item in args
        ):
            raise ValueError(f"{op} requires and returns numeric terms")
        expected = (
            LogicSort.REAL
            if op == "div" or LogicSort.REAL in {item.sort for item in args}
            else LogicSort.INT
        )
        if sort is not expected:
            raise ValueError(f"{op} result must have sort {expected.value}")
        return
    if op == "ite":
        _require_arity(op, args, 3)
        if args[0].sort is not LogicSort.BOOL:
            raise ValueError("ite condition must be Bool")
        branches = {args[1].sort, args[2].sort}
        if len(branches) == 1:
            expected = args[1].sort
        elif branches <= NUMERIC_SORTS:
            expected = LogicSort.REAL
        else:
            raise ValueError("ite branches have incompatible sorts")
        if sort is not expected:
            raise ValueError(f"ite result must have sort {expected.value}")
        return
    raise ValueError(f"unsupported logic operator {op}")


def symbol(name: str, sort: LogicSort) -> LogicTerm:
    return LogicTerm("symbol", sort, name=name)


def literal(value: bool | int | float | Fraction) -> LogicTerm:
    if type(value) is bool:
        return LogicTerm("literal", LogicSort.BOOL, value=value)
    if isinstance(value, int):
        return LogicTerm("literal", LogicSort.INT, value=Fraction(value))
    fraction = value if isinstance(value, Fraction) else Fraction(str(value))
    return LogicTerm("literal", LogicSort.REAL, value=fraction)


def apply(op: str, *args: LogicTerm) -> LogicTerm:
    values = tuple(args)
    if op in {"not", "and", "or", "implies", "eq", "lt", "le", "gt", "ge"}:
        sort = LogicSort.BOOL
    elif op == "neg":
        sort = values[0].sort
    elif op in {"add", "sub", "mul"}:
        sort = (
            LogicSort.REAL
            if any(item.sort is LogicSort.REAL for item in values)
            else LogicSort.INT
        )
    elif op == "div":
        sort = LogicSort.REAL
    elif op == "ite":
        sort = (
            LogicSort.REAL
            if {values[1].sort, values[2].sort} == NUMERIC_SORTS
            else values[1].sort
        )
    else:
        raise ValueError(f"unsupported logic operator {op}")
    return LogicTerm(op, sort, values)


def conjunction(items: Iterable[LogicTerm]) -> LogicTerm:
    values = tuple(items)
    if not values:
        return literal(True)
    result = values[0]
    for value in values[1:]:
        result = apply("and", result, value)
    return result


def disjunction(items: Iterable[LogicTerm]) -> LogicTerm:
    values = tuple(items)
    if not values:
        return literal(False)
    result = values[0]
    for value in values[1:]:
        result = apply("or", result, value)
    return result


@dataclass(frozen=True, slots=True)
class LogicSequent:
    """The semantic claim ``premises |= conclusion``."""

    premises: tuple[LogicTerm, ...]
    conclusion: LogicTerm
    profile: str = LOGIC_PROFILE

    def __post_init__(self) -> None:
        if any(item.sort is not LogicSort.BOOL for item in self.premises):
            raise ValueError("every sequent premise must be Bool")
        if self.conclusion.sort is not LogicSort.BOOL:
            raise ValueError("a sequent conclusion must be Bool")
        seen: dict[str, LogicSort] = {}
        for term in self.premises + (self.conclusion,):
            for name, sort in symbols_in(term).items():
                previous = seen.get(name)
                if previous is not None and previous is not sort:
                    raise ValueError(f"symbol {name} has conflicting sorts")
                seen[name] = sort

    def counterexample(self) -> LogicTerm:
        return conjunction(self.premises + (apply("not", self.conclusion),))

    def as_dict(self) -> dict[str, object]:
        return {
            "profile": self.profile,
            "premises": tuple(item.as_dict() for item in self.premises),
            "conclusion": self.conclusion.as_dict(),
        }

    def fingerprint(self) -> str:
        encoded = json.dumps(
            self.as_dict(), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


def symbols_in(term: LogicTerm) -> dict[str, LogicSort]:
    result: dict[str, LogicSort] = {}

    def visit(item: LogicTerm) -> None:
        if item.op == "symbol":
            assert item.name is not None
            previous = result.get(item.name)
            if previous is not None and previous is not item.sort:
                raise ValueError(f"symbol {item.name} has conflicting sorts")
            result[item.name] = item.sort
        for argument in item.args:
            visit(argument)

    visit(term)
    return result


def sort_from_sysml(type_name: str) -> LogicSort:
    mapping = {
        "Boolean": LogicSort.BOOL,
        "Integer": LogicSort.INT,
        "Real": LogicSort.REAL,
    }
    try:
        return mapping[type_name]
    except KeyError as exc:
        raise ValueError(f"unsupported logical source sort {type_name}") from exc


def compile_expression(
    model: Any,
    expr: Expr,
    *,
    observation: Mapping[str, LogicTerm],
    action: Mapping[str, LogicTerm],
    prior_action: Mapping[str, LogicTerm],
    scenario: Mapping[str, LogicTerm],
    context: str,
) -> LogicTerm:
    """Give an accepted symbolic expression its explicit constraint-logic meaning."""

    # Imported lazily so the logical IR itself has no dependency on the model
    # compiler and the model compiler does not acquire a circular import.
    from .ot_markov import UnsupportedOTProfile, _parameter_key, _parameter_maps

    path_to_observation = dict(model.observation_paths)
    latches = dict(model.prior_action_latches)
    parameters, _types = _parameter_maps(model.parser)
    if isinstance(expr, LiteralExpr):
        return literal(expr.value)
    if isinstance(expr, RefExpr):
        path = tuple(expr.path)
        if len(path) == 2 and path[0] == model.policy_subject:
            name = path[1]
            if name in observation:
                return observation[name]
            if name in action:
                return action[name]
        if path in path_to_observation:
            return observation[path_to_observation[path]]
        if len(path) == 1 and path[0] in latches:
            output = latches[path[0]]
            if output in prior_action:
                return prior_action[output]
        key = _parameter_key(model, path, context)
        if key in scenario:
            return scenario[key]
        if key in parameters:
            return literal(parameters[key].value)
        raise UnsupportedOTProfile(
            "UNSUPPORTED_LOGIC_REFERENCE", f"unresolved reference {'.'.join(path)}"
        )
    if isinstance(expr, UnaryExpr):
        operand = compile_expression(
            model, expr.operand, observation=observation, action=action,
            prior_action=prior_action, scenario=scenario, context=context,
        )
        operations = {"not": "not", "-": "neg"}
        if expr.op in operations:
            return apply(operations[expr.op], operand)
    if isinstance(expr, BinaryExpr):
        left = compile_expression(
            model, expr.left, observation=observation, action=action,
            prior_action=prior_action, scenario=scenario, context=context,
        )
        right = compile_expression(
            model, expr.right, observation=observation, action=action,
            prior_action=prior_action, scenario=scenario, context=context,
        )
        operations = {
            "+": "add", "-": "sub", "*": "mul", "/": "div",
            "==": "eq", ">=": "ge", "<=": "le", ">": "gt", "<": "lt",
            "and": "and", "or": "or", "implies": "implies",
        }
        if expr.op in operations:
            return apply(operations[expr.op], left, right)
    if isinstance(expr, TernaryExpr):
        return apply(
            "ite",
            compile_expression(
                model, expr.condition, observation=observation, action=action,
                prior_action=prior_action, scenario=scenario, context=context,
            ),
            compile_expression(
                model, expr.true_expr, observation=observation, action=action,
                prior_action=prior_action, scenario=scenario, context=context,
            ),
            compile_expression(
                model, expr.false_expr, observation=observation, action=action,
                prior_action=prior_action, scenario=scenario, context=context,
            ),
        )
    raise UnsupportedOTProfile(
        "UNSUPPORTED_LOGIC_EXPRESSION", f"unsupported expression {expr!r}"
    )


def compile_scenario_domain(
    model: Any,
    scenario: Mapping[str, LogicTerm],
) -> LogicTerm:
    """Compile the complete accepted scenario-only domain into constraint logic."""

    from .ot_markov import _conjuncts, _parameter_key, _refs

    accepted: list[LogicTerm] = []
    scenario_keys = set(model.scenario_parameters)
    for constraint in model.parser.parsed_constraints:
        if "ScenarioConstraint" not in constraint.metadata:
            continue
        for conjunct in _conjuncts(constraint.expression):
            keys = {
                _parameter_key(model, ref, constraint.context)
                for ref in _refs(conjunct)
            }
            if None not in keys and keys.issubset(scenario_keys):
                accepted.append(compile_expression(
                    model, conjunct, observation={}, action={}, prior_action={},
                    scenario=scenario, context=constraint.context,
                ))
    return conjunction(accepted)


def _coerce_z3_numeric(z3: Any, value: Any, source: LogicSort, target: LogicSort) -> Any:
    if source is LogicSort.INT and target is LogicSort.REAL:
        return z3.ToReal(value)
    return value


def lower_to_z3(
    term: LogicTerm,
    z3: Any,
    symbols: dict[str, Any] | None = None,
) -> Any:
    """Lower the solver-independent logical IR to Z3 expressions."""

    cache = {} if symbols is None else symbols
    if term.op == "symbol":
        assert term.name is not None
        if term.name not in cache:
            constructors = {
                LogicSort.BOOL: z3.Bool,
                LogicSort.INT: z3.Int,
                LogicSort.REAL: z3.Real,
            }
            cache[term.name] = constructors[term.sort](term.name)
        return cache[term.name]
    if term.op == "literal":
        if term.sort is LogicSort.BOOL:
            return z3.BoolVal(term.value)
        assert isinstance(term.value, Fraction)
        if term.sort is LogicSort.INT:
            return z3.IntVal(term.value.numerator)
        return z3.RealVal(f"{term.value.numerator}/{term.value.denominator}")

    values = [lower_to_z3(item, z3, cache) for item in term.args]
    if term.op == "not":
        return z3.Not(values[0])
    if term.op == "neg":
        return -values[0]
    if term.op == "and":
        return z3.And(*values)
    if term.op == "or":
        return z3.Or(*values)
    if term.op == "implies":
        return z3.Implies(*values)
    if term.op == "ite":
        target = term.sort
        branches = [
            _coerce_z3_numeric(z3, values[index], term.args[index].sort, target)
            for index in (1, 2)
        ]
        return z3.If(values[0], branches[0], branches[1])
    if term.op in {"eq", "lt", "le", "gt", "ge", "add", "sub", "mul", "div"}:
        target = (
            LogicSort.REAL
            if term.op == "div" or any(
                item.sort is LogicSort.REAL for item in term.args
            )
            else term.args[0].sort
        )
        left = _coerce_z3_numeric(z3, values[0], term.args[0].sort, target)
        right = _coerce_z3_numeric(z3, values[1], term.args[1].sort, target)
        operations = {
            "eq": lambda: left == right,
            "lt": lambda: left < right,
            "le": lambda: left <= right,
            "gt": lambda: left > right,
            "ge": lambda: left >= right,
            "add": lambda: left + right,
            "sub": lambda: left - right,
            "mul": lambda: left * right,
            "div": lambda: left / right,
        }
        return operations[term.op]()
    raise ValueError(f"unsupported logic operator {term.op}")


__all__ = [
    "LOGIC_PROFILE",
    "LogicSequent",
    "LogicSort",
    "LogicTerm",
    "apply",
    "compile_expression",
    "compile_scenario_domain",
    "conjunction",
    "disjunction",
    "literal",
    "lower_to_z3",
    "sort_from_sysml",
    "symbol",
    "symbols_in",
]
