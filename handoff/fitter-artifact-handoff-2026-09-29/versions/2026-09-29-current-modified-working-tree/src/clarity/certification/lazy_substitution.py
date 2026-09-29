"""Reuse Z3's substitution inputs across expressions without changing semantics.

This is z3.substitute's native operation with its sort checks and C argument
arrays prepared once. Both source and replacement ASTs remain strongly held.
"""
import z3
from z3.z3 import Ast, Z3_substitute, _to_expr_ref


class PreparedSubstitution:
    def __init__(self, pairs):
        self.pairs = tuple(pairs)
        self.context = self.pairs[0][0].ctx if self.pairs else None
        self.sources = (Ast * len(self.pairs))()
        self.targets = (Ast * len(self.pairs))()
        for index, pair in enumerate(self.pairs):
            if len(pair) != 2 or not all(z3.is_expr(value) for value in pair):
                raise ValueError('substitution requires expression pairs')
            left, right = pair
            if left.ctx != self.context or right.ctx != self.context or not left.sort().eq(right.sort()):
                raise ValueError('substitution requires matching contexts and sorts')
            self.sources[index], self.targets[index] = left.as_ast(), right.as_ast()
        self.cache = {}

    def __call__(self, expression):
        if not z3.is_expr(expression):
            raise ValueError('substitution requires an expression')
        if not self.pairs:
            return expression
        if expression.ctx != self.context:
            raise ValueError('substitution expression belongs to a different context')
        key = expression.get_id()
        if key not in self.cache:
            result = _to_expr_ref(Z3_substitute(self.context.ref(), expression.as_ast(),
                                  len(self.pairs), self.sources, self.targets), self.context)
            # Retaining the original prevents ID reuse for temporary expressions.
            self.cache[key] = (expression, result)
        return self.cache[key][1]


def equality_substitution(equalities):
    """Canonicalize the equivalence classes explicitly supplied by premises.

    Prefer literal values, then symbols, over compound terms. In particular,
    0 = x rewrites x to 0, never every occurrence of 0 to x. Union/find resolves
    alias chains in one pass and retains the original ASTs for their lifetime.
    No equality is inferred from names, types, or physical/sensor correspondence.
    """
    parents, terms = {}, {}

    def find(key):
        root = key
        while parents[root] != root:
            root = parents[root]
        while key != root:
            key, parents[key] = parents[key], root
        return root

    def rank(term):
        literal = z3.is_const(term) and term.decl().kind() != z3.Z3_OP_UNINTERPRETED
        return (0 if literal else 1 if z3.is_const(term) else 2, term.get_id())

    for equation in equalities:
        if not z3.is_eq(equation):
            raise ValueError('expression reduction requires explicit equalities')
        a, b = equation.children()
        for term in (a, b):
            key = term.get_id()
            terms[key] = term
            parents.setdefault(key, key)
        left, right = find(a.get_id()), find(b.get_id())
        if rank(terms[right]) < rank(terms[left]):
            left, right = right, left
        parents[right] = left
    return PreparedSubstitution([(term, terms[find(key)]) for key, term in terms.items()
                                 if find(key) != key])


def reduce_with_equalities(expressions, equalities):
    """Apply existing equality premises and retain them unchanged.

    For E the conjunction of premises, every replacement s -> t satisfies
    E => s = t. Consequently E & F is equivalent to E & F[s := t], including
    conflicting or overlapping premises. Keeping E is essential.
    """
    equalities = tuple(equalities)
    substitution = equality_substitution(equalities)
    return [*equalities, z3.simplify(substitution(z3.And(*expressions)))]


def propagate_asserted_equalities(expressions):
    """Use only equalities asserted as conjuncts, never guarded equalities."""
    conjuncts, pending = [], list(expressions)
    while pending:
        term = pending.pop()
        if z3.is_and(term):
            pending.extend(term.children())
        else:
            conjuncts.append(term)
    equalities = [term for term in conjuncts if z3.is_eq(term)]
    return reduce_with_equalities(conjuncts, equalities)
