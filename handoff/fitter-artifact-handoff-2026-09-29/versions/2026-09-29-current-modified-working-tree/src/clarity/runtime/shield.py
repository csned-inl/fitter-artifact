"""
Specification-derived safety shield for SysML neural controllers.

Extracts from the #NeuralRequirement:
  - Full AST (prohibitions + obligations)
  - Output-only clauses → structurally dead actions
  - All clause structure for neural compilation

Used only at construction time by ShieldNet in composite_model.py.
At runtime, ShieldNet makes all decisions via tensor math.
"""

from clarity.sysml.parser import (
    SysMLParser, ExpressionParser, BinaryExpr, RefExpr, LiteralExpr,
    UnaryExpr, TernaryExpr, Expr,
)


# ---------------------------------------------------------------------------
# AST evaluator
# ---------------------------------------------------------------------------

def _evaluate(expr, values: dict, subject_var: str = ""):
    from clarity.sysml.simulator import ExpressionEvaluator
    state = {name.replace('.', '::'): value for name, value in values.items()}
    if subject_var:
        state.update({subject_var + '::' + name.replace('.', '::'): value
                      for name, value in values.items()})
    return ExpressionEvaluator(state, strict=True).evaluate(expr)


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------

def collect_references(expr) -> set:
    refs = set()
    if isinstance(expr, RefExpr):
        refs.add(expr.path[-1])
    elif isinstance(expr, BinaryExpr):
        refs.update(collect_references(expr.left))
        refs.update(collect_references(expr.right))
    elif isinstance(expr, UnaryExpr):
        refs.update(collect_references(expr.operand))
    elif isinstance(expr, TernaryExpr):
        refs.update(collect_references(expr.condition))
        refs.update(collect_references(expr.true_expr))
        refs.update(collect_references(expr.false_expr))
    return refs


def flatten_conjunction(expr) -> list:
    if isinstance(expr, BinaryExpr) and expr.op == 'and':
        return flatten_conjunction(expr.left) + flatten_conjunction(expr.right)
    return [expr]



def _fixup_precedence(req_ast, out_set, const_set, subject_var):
    """Fix operator precedence issues where == binds tighter than and/or.

    Detects two patterns:

    Pattern 1 — AND split:
      The spec says: (comp1 AND comp2) == output
      The parser produces: comp1 AND (comp2 == output)
      After flattening: [comp1, (comp2 == output)]
      Fix: merge into [(comp1 AND comp2) == output]

    Pattern 2 — OR with biconditional child:
      The spec says: (comp1 OR comp2) == output
      The parser produces: comp1 OR (comp2 == output)
      Fix: restructure to (comp1 OR comp2) == output

    Detection: a "bare comparison" is a clause that references only
    observations and constants — no output parameters. If it sits
    next to a biconditional, they should be merged.
    """
    ignore = {subject_var} | const_set

    def _refs_no_outputs(expr):
        refs = collect_references(expr) - ignore
        return refs and refs.isdisjoint(out_set)

    def _is_biconditional_with_output(expr):
        if isinstance(expr, BinaryExpr) and expr.op == '==':
            left_refs = collect_references(expr.left) - ignore
            right_refs = collect_references(expr.right) - ignore
            if right_refs & out_set:
                return True
            if left_refs & out_set:
                return True
        return False

    def _get_biconditional_parts(expr):
        """Return (comparison_side, output_side) of a biconditional."""
        left_refs = collect_references(expr.left) - ignore
        right_refs = collect_references(expr.right) - ignore
        if right_refs & out_set:
            return expr.left, expr.right
        if left_refs & out_set:
            return expr.right, expr.left
        return None, None

    # Pattern 2: fix OR nodes with bare comp + biconditional
    def _fixup_or(expr):
        if not isinstance(expr, BinaryExpr) or expr.op != 'or':
            return expr
        # Recurse first
        left = _fixup_or(expr.left)
        right = _fixup_or(expr.right)

        # Check: left is bare comparison, right is biconditional
        if _refs_no_outputs(left) and _is_biconditional_with_output(right):
            comp_side, out_side = _get_biconditional_parts(right)
            if comp_side is not None:
                return BinaryExpr('==',
                                  BinaryExpr('or', left, comp_side),
                                  out_side)

        # Check: right is bare comparison, left is biconditional
        if _refs_no_outputs(right) and _is_biconditional_with_output(left):
            comp_side, out_side = _get_biconditional_parts(left)
            if comp_side is not None:
                return BinaryExpr('==',
                                  BinaryExpr('or', comp_side, right),
                                  out_side)

        return BinaryExpr('or', left, right)

    # Apply OR fixup to the whole AST first
    def _walk_fix_or(expr):
        if isinstance(expr, BinaryExpr):
            left = _walk_fix_or(expr.left)
            right = _walk_fix_or(expr.right)
            fixed = BinaryExpr(expr.op, left, right)
            if expr.op == 'or':
                return _fixup_or(fixed)
            return fixed
        if isinstance(expr, UnaryExpr):
            return UnaryExpr(expr.op, _walk_fix_or(expr.operand))
        return expr

    fixed_ast = _walk_fix_or(req_ast)

    # Pattern 1: flatten AND, merge bare comparisons into adjacent biconditionals
    clauses = flatten_conjunction(fixed_ast)
    merged = []
    i = 0
    while i < len(clauses):
        clause = clauses[i]

        # Look ahead: bare comparison followed by biconditional
        if (_refs_no_outputs(clause) and
                i + 1 < len(clauses) and
                _is_biconditional_with_output(clauses[i + 1])):
            bicon = clauses[i + 1]
            comp_side, out_side = _get_biconditional_parts(bicon)
            if comp_side is not None:
                merged.append(BinaryExpr('==',
                                         BinaryExpr('and', clause, comp_side),
                                         out_side))
                i += 2
                continue

        # Look behind: biconditional followed by bare comparison
        if (_is_biconditional_with_output(clause) and
                i + 1 < len(clauses) and
                _refs_no_outputs(clauses[i + 1])):
            bare = clauses[i + 1]
            comp_side, out_side = _get_biconditional_parts(clause)
            if comp_side is not None:
                merged.append(BinaryExpr('==',
                                         BinaryExpr('and', comp_side, bare),
                                         out_side))
                i += 2
                continue

        merged.append(clause)
        i += 1

    # Rebuild conjunction
    if not merged:
        return fixed_ast
    result = merged[0]
    for j in range(1, len(merged)):
        result = BinaryExpr('and', result, merged[j])
    return result



class SpecShield:
    """Extracts shield data from SysML specification.

    After construction, provides:
      - req_ast: full requirement AST
      - in_params, out_params: neural interface
      - unchanging: controller constants
      - action_map: action_id → actuator dict
      - dead_actions: structurally invalid actions
      - prohibition_clauses: output-only clauses

    Also callable for verification: shield(action_id, obs_dict) → action_id
    """

    def __init__(self, model_path: str):
        parser = SysMLParser(model_path)
        parser.parse()

        ctrl_fqn = parser.controller_part
        if ctrl_fqn is None:
            raise ValueError(
                "SysML model must contain exactly one part instance that owns a #Neural action"
            )
        ctrl_inst = parser.part_instances[ctrl_fqn]
        ctrl_def = parser.part_defs[ctrl_inst.part_type]

        neural_defs = [
            action for action in ctrl_def.action_defs if "Neural" in action.metadata
        ]
        if len(neural_defs) != 1:
            raise ValueError(
                f"expected exactly one #Neural action, found {len(neural_defs)}"
            )
        neural_def = neural_defs[0]

        self.in_params = [p.name for p in neural_def.in_params]
        self.out_params = [p.name for p in neural_def.out_params]
        self.output_types = [p.type_name for p in neural_def.out_params]
        in_set = set(self.in_params)
        out_set = set(self.out_params)

        neural_requirements = [
            (sv, req_expr)
            for _req_name, sv, _st, req_expr, req_meta in ctrl_def.requirements
            if "NeuralRequirement" in req_meta
        ]
        if len(neural_requirements) != 1:
            raise ValueError(
                "expected exactly one #NeuralRequirement, found "
                f"{len(neural_requirements)}"
            )
        self.subject_var, requirement_text = neural_requirements[0]
        from clarity.sysml.expression_types import parse_checked_expression
        self.req_ast = parse_checked_expression(requirement_text)

        ctrl_prefix = ctrl_fqn + "::"
        neural_names = in_set | out_set
        self.unchanging = {}
        from clarity.sysml.parser_values import assigned_features
        assigned = assigned_features(parser)
        for p in parser.parameters:
            if (p.qualified_name.startswith(ctrl_prefix) and p.name not in neural_names
                    and 'ScenarioInput' not in p.metadata and p.qualified_name not in assigned):
                self.unchanging[p.name] = p.value

        # Pick up controller constants not tagged #ScenarioInput
        # (e.g., toleranceMl) that appear in the requirement AST
        if self.req_ast:
            req_refs = collect_references(self.req_ast)
            req_refs.discard(self.subject_var)
            missing = req_refs - in_set - out_set - set(self.unchanging.keys())
            if missing:
                # Only source literal bindings are constants; an initialized
                # mutable state is not an unchanging requirement parameter.
                for attr in parser.derived_attributes:
                    if attr.qualified_name.startswith(ctrl_prefix) and isinstance(attr.expression, LiteralExpr):
                        name = attr.qualified_name[len(ctrl_prefix):]
                        if name in missing:
                            self.unchanging[name] = attr.expression.value
                            missing.discard(name)
                if missing:
                    raise ValueError(
                        "#NeuralRequirement contains unresolved references: "
                        + ", ".join(sorted(missing))
                    )

        boolean_outputs = all(
            (type_name or "").lower() in {"bool", "boolean"}
            for type_name in self.output_types
        )
        n_out = len(self.out_params)
        self.action_map = {}
        if boolean_outputs:
            for action_id in range(2 ** n_out):
                actuators = {}
                for bit, name in enumerate(self.out_params):
                    actuators[name] = bool(action_id & (1 << bit))
                self.action_map[action_id] = actuators

        # Restore the original shield's interpretation of the original parser
        # output. The source files and system safety requirement ASTs are untouched.
        if self.req_ast:
            self.req_ast = _fixup_precedence(
                self.req_ast, out_set, set(self.unchanging), self.subject_var)

        # Dead actions from output-only clauses
        self.dead_actions = set()
        self.prohibition_clauses = []
        if self.req_ast:
            clauses = flatten_conjunction(self.req_ast)
            for clause in clauses:
                refs = collect_references(clause)
                refs = {r for r in refs if r != self.subject_var}
                if refs and refs.issubset(out_set | set(self.unchanging.keys())):
                    self.prohibition_clauses.append(clause)

        for action_id, actuators in self.action_map.items():
            values = {**self.unchanging, **actuators}
            for clause in self.prohibition_clauses:
                try:
                    if not _evaluate(clause, values, self.subject_var):
                        self.dead_actions.add(action_id)
                        break
                except Exception as exc:
                    raise ValueError(
                        f"could not evaluate output-only #NeuralRequirement clause: {exc}"
                    ) from exc

        print(f"  [SpecShield] Loaded from {model_path}")
        if self.action_map:
            print(f"  [SpecShield] dead_actions={sorted(self.dead_actions)}, "
                  f"n_valid={len(self.action_map) - len(self.dead_actions)}")

    def requirement_actions(self, obs_dict: dict) -> list[int]:
        """Return every Boolean action allowed by the current requirement."""
        if not self.action_map:
            raise ValueError("#Neural outputs are not Boolean")
        allowed = []
        for action_id in sorted(self.action_map):
            if action_id in self.dead_actions:
                continue
            actuators = self.action_map[action_id]
            values = {**self.unchanging, **obs_dict, **actuators}
            try:
                if _evaluate(self.req_ast, values, self.subject_var):
                    allowed.append(action_id)
            except Exception as exc:
                raise ValueError(
                    f"could not evaluate #NeuralRequirement for action {action_id}: {exc}"
                ) from exc
        return allowed

    def requirement_action(self, obs_dict: dict) -> int:
        """Return the unique Boolean action required by the specification."""

        allowed = self.requirement_actions(obs_dict)
        if len(allowed) != 1:
            raise ValueError(
                "#NeuralRequirement must determine exactly one Boolean action; "
                f"it determined {len(allowed)} actions: {allowed}"
            )
        return allowed[0]

    def __call__(self, proposed_action: int, obs_dict: dict) -> int:
        """Evaluate shield decision via AST. Used only for verification."""
        if proposed_action in self.dead_actions:
            return self.requirement_action(obs_dict)
        actuators = self.action_map[proposed_action]
        values = {**self.unchanging, **obs_dict, **actuators}
        if not _evaluate(self.req_ast, values, self.subject_var):
            return self.requirement_action(obs_dict)
        return proposed_action
