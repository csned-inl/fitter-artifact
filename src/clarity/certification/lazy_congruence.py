"""Sound arithmetic abstraction for equality-of-execution queries.

Each deterministic numeric operator is replaced by one uninterpreted function
of the same arguments and result sort. Every exact interpretation is an allowed
interpretation of these functions. Thus UNSAT implies UNSAT for exact execution;
SAT or UNKNOWN establishes nothing about the exact source program.

There are no value substitutions between storage locations. Equal operator
applications share a function, not a result independent of their arguments.
"""
import z3


def abstract_numeric(expressions, prefix):
    structural = {
        z3.Z3_OP_UNINTERPRETED, z3.Z3_OP_TRUE, z3.Z3_OP_FALSE,
        z3.Z3_OP_EQ, z3.Z3_OP_DISTINCT, z3.Z3_OP_ITE, z3.Z3_OP_AND,
        z3.Z3_OP_OR, z3.Z3_OP_NOT, z3.Z3_OP_IMPLIES, z3.Z3_OP_IFF,
        z3.Z3_OP_XOR, z3.Z3_OP_DT_CONSTRUCTOR, z3.Z3_OP_DT_RECOGNISER,
        z3.Z3_OP_DT_IS, z3.Z3_OP_DT_ACCESSOR,
        z3.Z3_OP_SELECT, z3.Z3_OP_STORE, z3.Z3_OP_CONST_ARRAY,
    }
    rewritten, operators = {}, {}
    pending = [(expr, False) for expr in expressions]
    while pending:
        expr, ready = pending.pop()
        if expr.get_id() in rewritten:
            continue
        if not z3.is_app(expr):
            raise ValueError('numeric congruence requires quantifier-free application terms')
        children = expr.children()
        if not children:
            rewritten[expr.get_id()] = expr
            continue
        if not ready:
            pending.append((expr, True))
            pending.extend((child, False) for child in children if child.get_id() not in rewritten)
            continue
        operands = [rewritten[child.get_id()] for child in children]
        declaration = expr.decl()
        if declaration.kind() not in structural:
            # Z3 shares declarations for associative operators across
            # argument counts. UF signatures must include the actual domain.
            identity = (declaration.get_id(), tuple(child.sort().get_id() for child in children),
                        expr.sort().get_id())
            if identity not in operators:
                operators[identity] = z3.Function(prefix + '_operator_' + str(len(operators)),
                    *[child.sort() for child in children], expr.sort())
            rewritten[expr.get_id()] = operators[identity](*operands)
        else:
            # Boolean conjunction/disjunction and Distinct are variadic. Their
            # declaration's reported arity is not an argument-count contract
            # for rebuilding an arbitrary source application through __call__.
            variadic = {z3.Z3_OP_AND: z3.And, z3.Z3_OP_OR: z3.Or,
                        z3.Z3_OP_DISTINCT: z3.Distinct}
            build = variadic.get(declaration.kind(), declaration)
            rewritten[expr.get_id()] = build(*operands)
    return [rewritten[expr.get_id()] for expr in expressions], len(operators)
