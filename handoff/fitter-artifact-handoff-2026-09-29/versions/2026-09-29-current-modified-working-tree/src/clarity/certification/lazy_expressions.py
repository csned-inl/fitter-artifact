"""Named, guarded source-expression equations without path enumeration.

Each source expression contributes a constant number of equations. Values and
evaluation errors are separate terms, so an inactive operand cannot raise an
error. This is expression lowering, not a completed decision-transition proof.
"""
from dataclasses import dataclass

import z3


_Value = z3.Datatype('ClarityLazyScalar')
_Value.declare('Absent')
_Value.declare('Boolean', ('boolean', z3.BoolSort()))
_Value.declare('Integer', ('integer', z3.IntSort()))
_Value.declare('Float', ('floating', z3.Float64()))
_Value.declare('Text', ('text', z3.IntSort()))
Value = _Value.create()
_Status = z3.Datatype('ClarityLazyStatus')
_Status.declare('Success')
_Status.declare('Failure', ('exception_type', z3.IntSort()), ('message', z3.IntSort()))
_Status.declare('ConstraintFailure', ('constraint_mode', z3.IntSort()),
                ('constraint_details', z3.ArraySort(z3.IntSort(), z3.IntSort())))
Status = _Status.create()


def integer_value(value):
    return z3.If(Value.is_Integer(value), Value.integer(value), z3.IntVal(0))


def float_value(value):
    return z3.If(Value.is_Float(value), Value.floating(value), z3.FPVal(0, z3.Float64()))


def boolean_value(value):
    return z3.And(Value.is_Boolean(value), Value.boolean(value))


def text_id(value):
    """Injective UTF-8 label encoding, with no string-theory solver dependency."""
    return int.from_bytes(b'\x01' + value.encode('utf-8'), 'big')


def scalar(value):
    if value is None: return Value.Absent
    if type(value) is bool: return Value.Boolean(value)
    if type(value) is int: return Value.Integer(value)
    if type(value) is float: return Value.Float(z3.FPVal(value, z3.Float64()))
    if type(value) is str: return Value.Text(text_id(value))
    raise TypeError('unsupported literal ' + type(value).__name__)


def failure(message, kind='ValueError'):
    return Status.Failure(text_id(kind), text_id(message))


@dataclass(frozen=True)
class Term:
    value: object
    status: object = Status.Success
    identity: object = z3.IntVal(0)


class Equations:
    def __init__(self, prefix='expr', *, specialize_operations=True):
        self.prefix = prefix
        self.specialize_operations = specialize_operations
        self.equations = []
        self.terms = []
        self.cache = {}
        self.source_expressions = {}
        self.native_count = 0
        self.identity_counter = z3.IntVal(0)
        self.literal_identities = {}

    def new_identity(self):
        self.identity_counter = self.native(self.identity_counter + 1)
        return self.identity_counter

    def native(self, expression):
        """Name a storage/guard equation without recursively inlining it."""
        expression = z3.simplify(expression)
        if z3.is_true(expression) or z3.is_false(expression) or z3.is_int_value(expression):
            return expression
        result = z3.Const(f'{self.prefix}_native_{self.native_count}', expression.sort())
        self.native_count += 1
        self.equations.append(result == expression)
        return result

    def bind(self, value, status=Status.Success, identity=None):
        index = len(self.terms)
        value = z3.simplify(value)
        status = z3.simplify(status)
        # Preserve types proved by the constructor expression itself. Replacing
        # Float(x) by an unconstrained datatype variable would hide that fact
        # and force the solver to reason about irrelevant integer/error arms.
        constructors = ((Value.is_Boolean, Value.Boolean, z3.BoolSort()),
                        (Value.is_Integer, Value.Integer, z3.IntSort()),
                        (Value.is_Float, Value.Float, z3.Float64()),
                        (Value.is_Text, Value.Text, z3.IntSort()))
        variable = z3.Const(f'{self.prefix}_{index}_value', Value)
        for test, constructor, sort in constructors:
            if z3.is_true(z3.simplify(test(value))):
                variable = constructor(z3.Const(f'{self.prefix}_{index}_payload', sort))
                break
        if z3.is_true(z3.simplify(Value.is_Absent(value))): variable = Value.Absent
        status_var = (Status.Success if z3.is_true(z3.simplify(status == Status.Success))
                      else z3.Const(f'{self.prefix}_{index}_status', Status))
        result = Term(variable, status_var, z3.IntVal(0) if identity is None else identity)
        self.equations.extend((result.value == value, result.status == status))
        self.terms.append(result)
        return result

    def conditional(self, guard, yes, no):
        """Both arms are total SMT terms; only the selected error propagates."""
        is_bool = Value.is_Boolean(guard.value)
        selected = boolean_value(guard.value)
        status = z3.If(guard.status != Status.Success, guard.status,
            z3.If(is_bool, z3.If(selected, yes.status, no.status),
                  failure('non-Boolean conditional guard')))
        return self.bind(z3.If(selected, yes.value, no.value), status,
                         self.native(z3.If(selected, yes.identity, no.identity)))

    def expression(self, expr, lookup, *, strict=False, context_key=''):
        """Lookup returns a Term for the exact pre-operation source storage.

        context_key must identify immutable input versions and reference scope.
        No inferred physical alias or event equation participates in lookup.
        """
        # The lookup object is retained to prevent Python object-id reuse from
        # accidentally reusing results belonging to a previous input state.
        self.source_expressions[id(expr)] = expr
        key = (id(expr), strict, context_key, lookup)
        if key in self.cache: return self.cache[key]
        kind = expr['kind']
        if kind == 'literal':
            value = expr['value']
            identity = z3.IntVal(0)
            if type(value) is float and value != value:
                # Literal objects are reused when their AST is reevaluated.
                identity = z3.IntVal(self.literal_identities.setdefault(id(value),
                                      -len(self.literal_identities) - 1))
            result = self.bind(scalar(value), identity=identity)
        elif kind == 'reference':
            value = lookup(expr)
            status = value.status
            if strict:
                status = z3.If(status != Status.Success, status,
                    z3.If(Value.is_Absent(value.value),
                          failure('unresolved requirement reference ' + '.'.join(expr['path']),
                                  'MissingReference'), Status.Success))
            result = self.bind(value.value, status, value.identity)
        elif kind == 'conditional':
            parts = [self.expression(expr[k], lookup, strict=strict, context_key=context_key)
                     for k in ('condition', 'true', 'false')]
            result = self.conditional(*parts)
        elif kind == 'unary':
            result = self.unary(expr['operator'], self.expression(expr['operand'], lookup,
                                strict=strict, context_key=context_key))
        elif kind == 'binary':
            left = self.expression(expr['left'], lookup, strict=strict, context_key=context_key)
            right = self.expression(expr['right'], lookup, strict=strict, context_key=context_key)
            result = self.binary(expr['operator'], left, right)
        else: raise ValueError('unsupported expression ' + kind)
        self.cache[key] = result
        return result

    def unary(self, op, operand):
        v = operand.value
        if op == 'not':
            valid = Value.is_Boolean(v)
            value = Value.Boolean(z3.Not(boolean_value(v)))
        elif op == '-':
            valid = z3.Or(Value.is_Integer(v), Value.is_Float(v))
            value = z3.If(Value.is_Integer(v), Value.Integer(-integer_value(v)),
                          Value.Float(z3.fpNeg(float_value(v))))
        else: raise ValueError('unsupported unary operator ' + op)
        return self.bind(value, z3.If(operand.status != Status.Success, operand.status,
            z3.If(valid, Status.Success, failure('invalid operand for ' + op))),
            self.new_identity() if op == '-' else None)

    def binary(self, op, left, right):
        a, b = left.value, right.value
        if op in ('and', 'or', 'implies'):
            select = z3.Not(boolean_value(a)) if op == 'or' else boolean_value(a)
            short = False if op == 'and' else True
            value = z3.If(select, b, Value.Boolean(short))
            status = z3.If(left.status != Status.Success, left.status,
                z3.If(z3.Not(Value.is_Boolean(a)), failure('non-Boolean left operand for ' + op),
                  z3.If(select,
                    z3.If(right.status != Status.Success, right.status,
                      z3.If(Value.is_Boolean(b), Status.Success,
                            failure('non-Boolean right operand for ' + op))), Status.Success)))
            return self.bind(value, status)
        if op not in ('+', '-', '*', '/', '==', '>', '>=', '<', '<='):
            raise ValueError('unsupported binary operator ' + op)
        # Constructor evidence arrives from checked source-node facts or local
        # expressions. Dispatch before constructing irrelevant mixed/int arms.
        if self.specialize_operations and all(z3.is_true(z3.simplify(Value.is_Float(v))) for v in (a, b)):
            af, bf = z3.simplify(Value.floating(a)), z3.simplify(Value.floating(b))
            operation_status = Status.Success
            if op in ('+', '-', '*', '/'):
                value = Value.Float({'+': z3.fpAdd, '-': z3.fpSub, '*': z3.fpMul,
                                     '/': z3.fpDiv}[op](z3.RNE(), af, bf))
                if op == '/':
                    operation_status = z3.If(z3.fpIsZero(bf),
                        failure('division by zero in source expression'), Status.Success)
            else:
                value = Value.Boolean({'==': z3.fpEQ, '>': z3.fpGT, '>=': z3.fpGEQ,
                                       '<': z3.fpLT, '<=': z3.fpLEQ}[op](af, bf))
            status = z3.If(left.status != Status.Success, left.status,
                z3.If(right.status != Status.Success, right.status, operation_status))
            return self.bind(value, status, self.new_identity() if op in ('+', '-', '*', '/') else None)
        if (self.specialize_operations and op != '/' and
                all(z3.is_true(z3.simplify(Value.is_Integer(v))) for v in (a, b))):
            ai, bi = z3.simplify(Value.integer(a)), z3.simplify(Value.integer(b))
            if op in ('+', '-', '*'):
                value = Value.Integer({'+': lambda: ai + bi, '-': lambda: ai - bi,
                                       '*': lambda: ai * bi}[op]())
            else:
                value = Value.Boolean({'==': lambda: ai == bi, '>': lambda: ai > bi,
                                       '>=': lambda: ai >= bi, '<': lambda: ai < bi,
                                       '<=': lambda: ai <= bi}[op]())
            status = z3.If(left.status != Status.Success, left.status, right.status)
            return self.bind(value, status, self.new_identity() if op in ('+', '-', '*') else None)
        ai, bi = integer_value(a), integer_value(b)
        af, bf = float_value(a), float_value(b)
        ia, ib, fa, fb = Value.is_Integer(a), Value.is_Integer(b), Value.is_Float(a), Value.is_Float(b)
        numeric = z3.And(z3.Or(ia, fa), z3.Or(ib, fb))
        both_int = z3.And(ia, ib)
        both_float = z3.And(fa, fb)
        to_float_a = z3.If(fa, af, z3.fpRealToFP(z3.RNE(), z3.ToReal(ai), z3.Float64()))
        to_float_b = z3.If(fb, bf, z3.fpRealToFP(z3.RNE(), z3.ToReal(bi), z3.Float64()))
        convert_overflow = z3.Or(z3.And(ia, z3.fpIsInf(to_float_a)),
                                 z3.And(ib, z3.fpIsInf(to_float_b)))
        operation_status = Status.Success
        if op in ('+', '-', '*', '/'):
            if op == '/':
                rational = z3.ToReal(ai) / z3.ToReal(bi)
                int_div = z3.If(z3.And(ai == 0, bi < 0), z3.FPVal(-0.0, z3.Float64()),
                               z3.fpRealToFP(z3.RNE(), rational, z3.Float64()))
                raw = z3.fpDiv(z3.RNE(), to_float_a, to_float_b)
                value = Value.Float(z3.If(both_int, int_div, raw))
                zero = z3.Or(z3.And(ib, bi == 0), z3.And(fb, z3.fpIsZero(bf)))
                operation_status = z3.If(zero, failure('division by zero in source expression'),
                    z3.If(both_int,
                      z3.If(z3.fpIsInf(int_div), failure('integer division result too large for a float', 'OverflowError'), Status.Success),
                      z3.If(convert_overflow, failure('int too large to convert to float', 'OverflowError'), Status.Success)))
            else:
                integer = {'+': lambda: ai + bi, '-': lambda: ai - bi, '*': lambda: ai * bi}[op]()
                floating = {'+': z3.fpAdd, '-': z3.fpSub, '*': z3.fpMul}[op](z3.RNE(), to_float_a, to_float_b)
                value = z3.If(both_int, Value.Integer(integer), Value.Float(floating))
                operation_status = z3.If(z3.And(z3.Not(both_int), convert_overflow),
                    failure('int too large to convert to float', 'OverflowError'), Status.Success)
        else:
            def compare(x, y):
                return {'==': lambda: x == y, '>': lambda: x > y, '>=': lambda: x >= y,
                        '<': lambda: x < y, '<=': lambda: x <= y}[op]()
            # Mixed Python int/float comparisons do not round the integer.
            finite = compare(z3.If(fa, z3.fpToReal(af), z3.ToReal(ai)),
                             z3.If(fb, z3.fpToReal(bf), z3.ToReal(bi)))
            floating = z3.If(fa, af, bf)
            positive = z3.Not(z3.fpIsNegative(floating))
            if op == '==': infinite = z3.BoolVal(False)
            elif op in ('>', '>='): infinite = z3.If(fa, positive, z3.Not(positive))
            else: infinite = z3.If(fa, z3.Not(positive), positive)
            mixed = z3.And(z3.Not(z3.fpIsNaN(floating)), z3.If(z3.fpIsInf(floating), infinite, finite))
            fp_compare = {'==': z3.fpEQ, '>': z3.fpGT, '>=': z3.fpGEQ,
                          '<': z3.fpLT, '<=': z3.fpLEQ}[op](af, bf)
            value = Value.Boolean(z3.If(both_int, compare(ai, bi), z3.If(both_float, fp_compare, mixed)))
            if op == '==':
                value = z3.If(numeric, value, Value.Boolean(a == b))
        status = z3.If(left.status != Status.Success, left.status,
            z3.If(right.status != Status.Success, right.status,
              z3.If(z3.Or(Value.is_Absent(a), Value.is_Absent(b)), failure('undefined operand for ' + op),
                z3.If(z3.Xor(Value.is_Boolean(a), Value.is_Boolean(b)) if op == '==' else z3.Not(numeric),
                      failure('incompatible Boolean/numeric equality' if op == '==' else 'non-numeric operand for ' + op),
                      operation_status))))
        return self.bind(value, status, self.new_identity() if op in ('+', '-', '*', '/') else None)
