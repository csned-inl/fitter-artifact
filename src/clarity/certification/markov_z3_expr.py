"""Deterministic, fail-closed scalar-expression lowering to SMT-LIB 2.

The compiler covers the Boolean, integer, and binary64 expression subset found
in the checked thermostat relevance slice.  It resolves source storage names to
typed ``StorageId`` objects, follows checked live-expression aliases, and emits
explicit IEEE-754 round-to-nearest-even operations.  It does not lower control
flow or whole transition relations.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import struct
from typing import Any

from .markov_ir import NativeSort
from .markov_slice import TheoremSlice
from .markov_z3 import UnsupportedLoweringError


@dataclass(frozen=True)
class SMTTerm:
    text: str
    sort: NativeSort
    literal: int | float | bool | None = None


def smt_sort(sort: NativeSort) -> str:
    if sort in {NativeSort.BOOL, NativeSort.PRESENCE}:
        return "Bool"
    if sort is NativeSort.INT:
        return "Int"
    if sort is NativeSort.FLOAT32:
        return "(_ FloatingPoint 8 24)"
    if sort is NativeSort.FLOAT64:
        return "(_ FloatingPoint 11 53)"
    raise UnsupportedLoweringError(f"scalar expression uses unsupported sort: {sort.value}")


def quoted_symbol(*components: str) -> str:
    value = "::".join(components)
    if not value or "|" in value or "\\" in value:
        raise ValueError("SMT symbol contains an unsupported character")
    return f"|{value}|"


def _fp_literal(value: float, sort: NativeSort) -> str:
    if not math.isfinite(value):
        raise UnsupportedLoweringError("non-finite source literals are unsupported")
    if sort is NativeSort.FLOAT64:
        bits = struct.unpack(">Q", struct.pack(">d", float(value)))[0]
        return f"((_ to_fp 11 53) #x{bits:016x})"
    if sort is NativeSort.FLOAT32:
        bits = struct.unpack(">I", struct.pack(">f", float(value)))[0]
        return f"((_ to_fp 8 24) #x{bits:08x})"
    raise UnsupportedLoweringError("floating literal requested for a non-floating sort")


class ThermostatExpressionCompiler:
    """Compile checked expression JSON against one event/run namespace."""

    def __init__(self, slice_: TheoremSlice, *, namespace: str = "run"):
        self.slice = slice_
        self.namespace = namespace
        self._by_graph_key: dict[str, list[Any]] = {}
        for storage in slice_.storages:
            for key in storage.graph_keys:
                self._by_graph_key.setdefault(key, []).append(storage)
        self._declarations: dict[str, NativeSort] = {}

    @property
    def declarations(self) -> tuple[tuple[str, NativeSort], ...]:
        return tuple(sorted(self._declarations.items()))

    def declaration_smt2(self) -> str:
        return "\n".join(
            f"(declare-const {symbol} {smt_sort(sort)})"
            for symbol, sort in self.declarations
        ) + ("\n" if self._declarations else "")

    def _reference(self, expression: dict[str, Any]) -> SMTTerm:
        live = expression.get("live_expression")
        if live is not None:
            if not isinstance(live, dict):
                raise UnsupportedLoweringError("live expression is not a checked AST node")
            return self.compile(live)
        key = expression.get("storage")
        if not isinstance(key, str) or not key:
            raise UnsupportedLoweringError("reference lacks an exact storage key")
        candidates = self._by_graph_key.get(key, [])
        if len(candidates) != 1:
            raise UnsupportedLoweringError(
                f"reference storage does not resolve uniquely in the theorem slice: {key}"
            )
        identity = candidates[0].identity
        symbol = quoted_symbol(self.namespace, "entry", identity.uid)
        self._declarations[symbol] = identity.native_sort
        return SMTTerm(symbol, identity.native_sort)

    @staticmethod
    def _literal(expression: dict[str, Any]) -> SMTTerm:
        declared = expression.get("type")
        value = expression.get("value")
        if declared == "Boolean" and type(value) is bool:
            return SMTTerm("true" if value else "false", NativeSort.BOOL, value)
        if declared == "Integer" and type(value) is int:
            return SMTTerm(str(value), NativeSort.INT, value)
        if declared == "Real" and type(value) in {int, float}:
            converted = float(value)
            return SMTTerm(_fp_literal(converted, NativeSort.FLOAT64),
                           NativeSort.FLOAT64, converted)
        raise UnsupportedLoweringError(
            f"unsupported or ill-typed literal: {declared!r} {value!r}"
        )

    @staticmethod
    def _coerce_pair(left: SMTTerm, right: SMTTerm) -> tuple[SMTTerm, SMTTerm]:
        if left.sort is right.sort:
            return left, right
        floating = {NativeSort.FLOAT32, NativeSort.FLOAT64}
        if left.sort in floating and right.sort is NativeSort.INT \
                and type(right.literal) is int:
            return left, SMTTerm(_fp_literal(float(right.literal), left.sort),
                                 left.sort, float(right.literal))
        if right.sort in floating and left.sort is NativeSort.INT \
                and type(left.literal) is int:
            return SMTTerm(_fp_literal(float(left.literal), right.sort),
                           right.sort, float(left.literal)), right
        raise UnsupportedLoweringError(
            f"implicit coercion is unsupported: {left.sort.value}, {right.sort.value}"
        )

    @staticmethod
    def _boolean(term: SMTTerm, operator: str) -> None:
        if term.sort not in {NativeSort.BOOL, NativeSort.PRESENCE}:
            raise UnsupportedLoweringError(
                f"{operator} requires Boolean operands, found {term.sort.value}"
            )

    def _unary(self, expression: dict[str, Any]) -> SMTTerm:
        operator = expression.get("operator")
        operand = self.compile(expression.get("operand"))
        if operator == "not":
            self._boolean(operand, operator)
            return SMTTerm(f"(not {operand.text})", NativeSort.BOOL)
        raise UnsupportedLoweringError(f"unsupported unary operator: {operator!r}")

    def _binary(self, expression: dict[str, Any]) -> SMTTerm:
        operator = expression.get("operator")
        left = self.compile(expression.get("left"))
        right = self.compile(expression.get("right"))
        if operator in {"and", "or", "implies"}:
            self._boolean(left, operator)
            self._boolean(right, operator)
            smt_operator = "=>" if operator == "implies" else operator
            return SMTTerm(f"({smt_operator} {left.text} {right.text})", NativeSort.BOOL)

        left, right = self._coerce_pair(left, right)
        if operator == "==":
            if left.sort in {NativeSort.FLOAT32, NativeSort.FLOAT64}:
                return SMTTerm(f"(fp.eq {left.text} {right.text})", NativeSort.BOOL)
            return SMTTerm(f"(= {left.text} {right.text})", NativeSort.BOOL)
        if operator in {"<", "<=", ">", ">="}:
            if left.sort in {NativeSort.FLOAT32, NativeSort.FLOAT64}:
                name = {"<": "fp.lt", "<=": "fp.leq", ">": "fp.gt", ">=": "fp.geq"}[operator]
            elif left.sort is NativeSort.INT:
                name = operator
            else:
                raise UnsupportedLoweringError(
                    f"ordered comparison is unsupported for {left.sort.value}"
                )
            return SMTTerm(f"({name} {left.text} {right.text})", NativeSort.BOOL)
        if operator in {"+", "-", "*", "/"}:
            if left.sort in {NativeSort.FLOAT32, NativeSort.FLOAT64}:
                name = {"+": "fp.add", "-": "fp.sub", "*": "fp.mul", "/": "fp.div"}[operator]
                return SMTTerm(
                    f"({name} RNE {left.text} {right.text})", left.sort
                )
            if left.sort is NativeSort.INT and operator != "/":
                return SMTTerm(f"({operator} {left.text} {right.text})", NativeSort.INT)
            raise UnsupportedLoweringError(
                f"arithmetic operator {operator!r} is unsupported for {left.sort.value}"
            )
        raise UnsupportedLoweringError(f"unsupported binary operator: {operator!r}")

    def compile(self, expression: Any) -> SMTTerm:
        if not isinstance(expression, dict):
            raise UnsupportedLoweringError("expression is not a checked AST node")
        kind = expression.get("kind")
        if kind == "reference":
            return self._reference(expression)
        if kind == "literal":
            return self._literal(expression)
        if kind == "unary":
            return self._unary(expression)
        if kind == "binary":
            return self._binary(expression)
        raise UnsupportedLoweringError(f"unsupported expression kind: {kind!r}")
