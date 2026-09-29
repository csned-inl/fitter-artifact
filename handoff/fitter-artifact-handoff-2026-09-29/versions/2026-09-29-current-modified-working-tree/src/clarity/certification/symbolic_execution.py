"""SMT values for the source operation equations, including binary64 arithmetic.

Python Booleans, arbitrary-precision integers and binary64 floats stay distinct.
An unresolved branch requests two guarded equations; it is never selected using
a representative numeric value. The caller retains cycles as program edges.
"""
from __future__ import annotations

from dataclasses import dataclass
import operator

import z3


class BranchRequired(BaseException):
    """Control request, not a source execution exception."""
    def __init__(self, predicate):
        self.predicate = predicate


class SymbolicContext:
    def __init__(self, choices=()):
        self.choices = dict(choices)
        self.conditions = []

    def choose(self, expression):
        expression = z3.simplify(expression)
        if z3.is_true(expression): return True
        if z3.is_false(expression): return False
        key = expression.sexpr()
        if key not in self.choices:
            raise BranchRequired(expression)
        value = self.choices[key]
        self.conditions.append(expression if value else z3.Not(expression))
        return value

    def variable(self, name, kind):
        constructors = {bool: z3.Bool, int: z3.Int, float: lambda name: z3.FP(name, z3.Float64())}
        return SymbolicValue(self, constructors[kind](name), kind)

    def constant(self, value):
        if isinstance(value, SymbolicValue): return value
        kind = type(value)
        if kind is bool: expr = z3.BoolVal(value)
        elif kind is int: expr = z3.IntVal(value)
        elif kind is float: expr = z3.FPVal(value, z3.Float64())
        else: raise TypeError('unsupported source scalar ' + kind.__name__)
        return SymbolicValue(self, expr, kind)


@dataclass(eq=False, frozen=True)
class SymbolicValue:
    context: SymbolicContext
    expression: object
    python_kind: type

    def __deepcopy__(self, memo):
        # Values are immutable; source copies preserve object sharing.
        return self

    def __bool__(self):
        if self.python_kind is bool: predicate = self.expression
        elif self.python_kind is int: predicate = self.expression != 0
        else: predicate = z3.Not(z3.fpEQ(self.expression, z3.FPVal(0.0, z3.Float64())))
        return self.context.choose(predicate)

    def logical_not(self):
        if self.python_kind is not bool: raise TypeError('not expects Boolean')
        return SymbolicValue(self.context, z3.Not(self.expression), bool)

    def logical_combine(self, other, operation):
        other = self.context.constant(other)
        if self.python_kind is not bool or other.python_kind is not bool:
            raise TypeError('logical operation expects Boolean operands')
        constructor = {'and': z3.And, 'or': z3.Or, 'implies': z3.Implies}[operation]
        return SymbolicValue(self.context, constructor(self.expression, other.expression), bool)

    def _integer(self):
        if self.python_kind is int: return self.expression
        if self.python_kind is bool: return z3.If(self.expression, 1, 0)
        raise TypeError('not an integer scalar')

    def _float(self):
        if self.python_kind is float: return self.expression
        converted = z3.fpRealToFP(z3.RNE(), z3.ToReal(self._integer()), z3.Float64())
        if self.context.choose(z3.fpIsInf(converted)):
            raise OverflowError('int too large to convert to float')
        return converted

    def _arithmetic(self, other, op, reverse=False):
        other = self.context.constant(other)
        left, right = (other, self) if reverse else (self, other)
        if left.python_kind is not float and right.python_kind is not float:
            a, b = left._integer(), right._integer()
            if op == '/':
                if self.context.choose(b == 0): raise ZeroDivisionError('division by zero')
                value = z3.fpRealToFP(z3.RNE(), z3.ToReal(a) / z3.ToReal(b), z3.Float64())
                if self.context.choose(z3.fpIsInf(value)):
                    raise OverflowError('integer division result too large for a float')
                return SymbolicValue(self.context, value, float)
            expression = {'+': operator.add, '-': operator.sub, '*': operator.mul}[op](a, b)
            return SymbolicValue(self.context, expression, int)
        a, b = left._float(), right._float()
        if op == '/' and self.context.choose(z3.fpEQ(b, z3.FPVal(0.0, z3.Float64()))):
            raise ZeroDivisionError('float division by zero')
        expression = {'+': z3.fpAdd, '-': z3.fpSub, '*': z3.fpMul, '/': z3.fpDiv}[op](z3.RNE(), a, b)
        return SymbolicValue(self.context, expression, float)

    def _compare(self, other, op):
        if not isinstance(other, (SymbolicValue, bool, int, float)):
            return False if op == '==' else NotImplemented
        other = self.context.constant(other)
        if self.python_kind is float and other.python_kind is float:
            value = {'==': z3.fpEQ, '>': z3.fpGT, '>=': z3.fpGEQ,
                     '<': z3.fpLT, '<=': z3.fpLEQ}[op](self.expression, other.expression)
        elif self.python_kind is float or other.python_kind is float:
            # Python compares int to float exactly; it does not first round the
            # integer to binary64 (e.g. (2**53 + 1) != float(2**53)).
            fp = self if self.python_kind is float else other
            integer = other if self.python_kind is float else self
            a = z3.fpToReal(fp.expression) if self.python_kind is float else z3.ToReal(integer._integer())
            b = z3.ToReal(integer._integer()) if self.python_kind is float else z3.fpToReal(fp.expression)
            finite = {'==': operator.eq, '>': operator.gt, '>=': operator.ge,
                      '<': operator.lt, '<=': operator.le}[op](a, b)
            positive = z3.Not(z3.fpIsNegative(fp.expression))
            if op == '==': infinite = z3.BoolVal(False)
            elif op in ('>', '>='): infinite = positive if self.python_kind is float else z3.Not(positive)
            else: infinite = z3.Not(positive) if self.python_kind is float else positive
            value = z3.And(z3.Not(z3.fpIsNaN(fp.expression)), z3.If(z3.fpIsInf(fp.expression), infinite, finite))
        else:
            a, b = self._integer(), other._integer()
            value = {'==': operator.eq, '>': operator.gt, '>=': operator.ge,
                     '<': operator.lt, '<=': operator.le}[op](a, b)
        return SymbolicValue(self.context, value, bool)

    def __add__(self, other): return self._arithmetic(other, '+')
    def __radd__(self, other): return self._arithmetic(other, '+', True)
    def __sub__(self, other): return self._arithmetic(other, '-')
    def __rsub__(self, other): return self._arithmetic(other, '-', True)
    def __mul__(self, other): return self._arithmetic(other, '*')
    def __rmul__(self, other): return self._arithmetic(other, '*', True)
    def __truediv__(self, other): return self._arithmetic(other, '/')
    def __rtruediv__(self, other): return self._arithmetic(other, '/', True)
    def __neg__(self):
        return SymbolicValue(self.context, z3.fpNeg(self.expression) if self.python_kind is float
                             else -self._integer(), self.python_kind)
    def __eq__(self, other): return self._compare(other, '==')
    def __ne__(self, other):
        result = self._compare(other, '==')
        return result.logical_not() if isinstance(result, SymbolicValue) else not result
    def __gt__(self, other): return self._compare(other, '>')
    def __ge__(self, other): return self._compare(other, '>=')
    def __lt__(self, other): return self._compare(other, '<')
    def __le__(self, other): return self._compare(other, '<=')


def guarded_evaluations(function, *, constraints=(), timeout_ms=1000, check_feasibility=True):
    """Enumerate satisfiable local branches, preserving unknown branches.

    ``function(context)`` evaluates one finite source region. A recursive program
    edge is returned by its caller, not unfolded into a guessed scan count.
    """
    pending = [((), ())]
    while pending:
        choices, guards = pending.pop()
        condition = z3.simplify(z3.And(*constraints, *guards))
        if z3.is_false(condition): continue
        if check_feasibility and not z3.is_true(condition):
            solver = z3.Solver(); solver.set(timeout=timeout_ms)
            solver.add(condition)
            result = solver.check()
            if result == z3.unsat: continue
            if result == z3.unknown:
                yield {'status': 'unknown', 'guards': guards, 'reason': solver.reason_unknown()}
                continue
        context = SymbolicContext(choices)
        try:
            value = function(context)
        except BranchRequired as branch:
            key = z3.simplify(branch.predicate).sexpr()
            for selected in (False, True):
                guard = branch.predicate if selected else z3.Not(branch.predicate)
                pending.append(((*choices, (key, selected)), (*guards, guard)))
        except Exception as exc:
            yield {'status': 'error', 'guards': guards, 'error': f'{type(exc).__name__}: {exc}'}
        else:
            yield {'status': 'equation', 'guards': guards, 'value': value}
