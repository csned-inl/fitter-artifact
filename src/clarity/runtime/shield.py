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
        self.req_ast = ExpressionParser(requirement_text).parse()

        ctrl_prefix = ctrl_fqn + "::"
        neural_names = in_set | out_set
        self.unchanging = {}
        for p in parser.parameters:
            if (p.qualified_name.startswith(ctrl_prefix) and p.name not in neural_names
                    and 'ScenarioInput' not in p.metadata and p.value_kind == 'binding'):
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

        # The parser owns precedence. Never rewrite the source contract to
        # infer the author's intended grouping from the output parameter names.

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
