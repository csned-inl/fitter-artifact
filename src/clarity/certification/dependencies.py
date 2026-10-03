"""
SysML-native transition dependency extractor.

Builds the per-variable next-state dependency graph DIRECTLY from the SysML model
(via sysml_parser, the same parser the shield uses) -- NOT from the exported SMV,
which lossily drops the controller->state-machine-actuator wire.

get_model(path) -> dict(STATE, ACTIONS, nsupp, copies, OBS, R), consumed by
reconstruct_closure. Canonical names drop the root part name read from SysML
and replace `::` with `_`.

Edges:
  step_actions / state-machines  -> NEXT-cycle dep
  flows / connects / binds / derived / send-accept -> SAME-cycle alias (value eq)
"""
import sys

from clarity.sysml.parser import (SysMLParser, RefExpr, BinaryExpr, UnaryExpr, TernaryExpr,
                          LiteralExpr, SubactionCallStmt, InputBindingStmt, SendStmt,
                          AcceptStmt, IfStmt, AssignStmt, PerformStmt, InParamStmt)


def _refs(e):
    out = []
    def w(x):
        if isinstance(x, RefExpr): out.append(tuple(x.path))
        elif isinstance(x, BinaryExpr): w(x.left); w(x.right)
        elif isinstance(x, UnaryExpr): w(x.operand)
        elif isinstance(x, TernaryExpr): w(x.condition); w(x.true_expr); w(x.false_expr)
    if e is not None: w(e)
    return out


def _canon(parts, root=None):
    parts = [p for p in parts if p]
    if root and parts and parts[0] == root:
        parts = parts[1:]
    return "_".join(parts)


class SysMLModel:
    def __init__(self, path):
        p = SysMLParser(path); p.parse()
        self.p = p
        if not p.system_part:
            raise ValueError("SysML file must contain exactly one root part instance")
        self.sys = p.system_part
        self.inst_by_name = {v.name: v for v in p.part_instances.values()}
        self.insts = set(self.inst_by_name.keys())
        # action_name -> body, for `perform action X` inlining (Modbus models put
        # the coil-write sends in separate performed actions).
        self.actions_by_name = {a.name: a.body
                                for pd in p.part_defs.values() for a in pd.actions}

        # ---- neural action def: actions, the controller instance, in-bindings ----
        (
            self.neural_out,
            self.in_binds,
            self.ctrl_inst,
            self.neural_call,
        ) = self._find_neural()
        self.ctrl_fqn = f"{self.sys}::{self.ctrl_inst}" if self.ctrl_inst else self.sys
        self.ACTIONS = {
            self._canon([self.ctrl_inst, self.neural_call, output])
            for output in self.neural_out
        }

        # ---- state-variable targets (known before resolving deps) ----
        self.state_targets = set()
        for sa in p.step_actions:
            self.state_targets.add(self._canon(sa.target_key.split("::")))
        for fqn, sm in p.instance_state_machines.items():
            inst = fqn.split("::")[-1]
            self.state_targets.add(f"{inst}_state")
            for t in sm.transitions:
                for d in (t.do_action or []):
                    if isinstance(d, AssignStmt):
                        self.state_targets.add(self._canon([inst] + d.target))

        # ---- numeric constants (exclude anything that's actually a state var) ----
        self.consts = set()
        for prm in p.parameters:
            self.consts.add(self._canon(prm.qualified_name.split("::")))
        for inst in p.part_instances.values():
            for a in inst.attributes:
                self.consts.add(self._canon([inst.name, a]))
        for fqn, statements in p.step_action_bodies:
            ctx = fqn.split("::")
            for statement in statements:
                if isinstance(statement, InParamStmt):
                    self.consts.add(self._qual([statement.name], ctx))
        self.consts -= self.state_targets

        # ---- same-cycle alias map: canonical-prefix -> canonical-prefix ----
        self.alias = {}
        for f in p.flows:                                   # receiver <- sender
            self._alias(self._pc(f.to_port), self._pc(f.from_port))
        for frm, to in p.connects:                          # receiver <- sender
            self._alias(self._pc(to), self._pc(frm))
        for lhs, rhs in p.parsed_bindings.items():          # bind lhs = rhs
            self._alias(self._canon(lhs.split("::")), self._canon(rhs.split("::")))
        self._wire_send_accept()                            # ports carry sent items

        # derived attrs (DEFINEs / `attribute x = expr`) are SAME-cycle computed
        # values -> expanded to their dependencies during dep collection.
        self.derived = {}
        for d in p.derived_attributes:
            ctx = d.context.split("::") if d.context else [self.sys]
            self.derived[self._canon(d.qualified_name.split("::"))] = (d.expression, ctx)
        self._constraints()

        # ---- build deps ----
        self.STATE = set()
        self.nsupp = {}
        self.copies = set()
        self.checked_copies = set()
        # Exact Boolean source effects for event-driven actuator latches.  These
        # are populated only after command payloads and receive assignments have
        # both been checked; a mere dependency edge is never treated as equality.
        self.action_effects = {}
        self._invalid_action_effect_targets = set()
        self._build_steps()
        self._build_state_machines()
        self.OBS = self._build_obs()
        self.R = self._build_R()

    # ---------- low-level ----------
    def canonical_name(self, parts):
        """Return the model-root-relative canonical name for a path."""
        return self._canon(parts)

    def connection_port_name(self, dotted):
        """Return the canonical name for a dotted connection-port path."""
        return self._pc(dotted)

    def qualify_name(self, parts, context):
        """Qualify a context-local path without following aliases."""
        return self._qual(parts, context)

    def dependency_keys(self, reference, context):
        """Resolve and expand one reference into transition dependency keys."""
        return self._collect_one(reference, context)

    def expanded_statements(self, statements):
        """Iterate statements with performed actions and branches expanded."""
        return self._flatten(statements)

    def actuator_decision(self, instance, trigger_port, connection_map):
        """Return the controller decision driving a coil-write actuator."""
        return self._actuator_decision(instance, trigger_port, connection_map)

    def latch_action(self, state_machine, instance):
        """Return the controller action latched by a state machine."""
        return self._latch_action(state_machine, instance)

    def _canon(self, parts):
        return _canon(parts, self.sys)

    def _alias(self, a, b):
        if a and b and a != b:
            self.alias[a] = b

    def _pc(self, dotted):
        return self._canon(dotted.split("."))

    def _qual(self, parts, ctx):
        """Qualify a context-local ref to a rooted canonical key (no aliasing)."""
        if parts[0] in self.insts or parts[0] == self.sys:
            return self._canon(parts)
        return self._canon(list(ctx) + list(parts))

    def _deref(self, key):
        for _ in range(30):
            hit = False
            for pre in sorted(self.alias, key=len, reverse=True):
                if key == pre or key.startswith(pre + "_"):
                    key = self.alias[pre] + key[len(pre):]
                    hit = True; break
            if not hit:
                return key
        return key

    def _resolve(self, parts, ctx):
        return self._deref(self._qual(parts, ctx))

    def _collect_refs(self, expr, ctx):
        deps = set()
        for r in _refs(expr):
            deps |= self._collect_one(list(r), ctx)
        return deps

    def _collect_one(self, ref, ctx, seen=None):
        return self._expand_key(self._resolve(ref, ctx), seen or set())

    def _expand_key(self, k, seen):
        if k in self.derived and k not in seen:               # same-cycle DEFINE
            seen = seen | {k}
            expr, dctx = self.derived[k]
            out = set()
            for r in _refs(expr):
                out |= self._collect_one(list(r), dctx, seen)
            return out
        return {k} if self._keep(k) else set()

    def _conjuncts(self, e):
        if isinstance(e, BinaryExpr) and e.op == "and":
            return self._conjuncts(e.left) + self._conjuncts(e.right)
        return [e]

    def _constraints(self):
        """Turn SysML equalities and implications into same-cycle edges."""
        for c in self.p.parsed_constraints:
            if "ScenarioConstraint" in getattr(c, "metadata", []):
                continue
            ctx = c.context.split("::") if c.context else [self.sys]
            for conj in self._conjuncts(c.expression):
                if isinstance(conj, BinaryExpr) and conj.op == "implies":
                    A, B = conj.left, conj.right
                    if (isinstance(B, BinaryExpr) and B.op == "==" and
                            isinstance(B.left, RefExpr)):
                        key = self._qual(B.left.path, ctx)   # the controlled flow port
                        # depends on the condition (isRunning) AND the assigned value
                        self.derived.setdefault(key, (BinaryExpr("and", A, B.right), ctx))
                elif (isinstance(conj, BinaryExpr) and conj.op == "==" and
                        isinstance(conj.left, RefExpr)):
                    key = self._qual(conj.left.path, ctx)
                    self.derived.setdefault(key, (conj.right, ctx))

    def _part_ctx(self, key_parts):
        """Longest prefix ending in a part instance (the owning part)."""
        last = 0
        for i, tok in enumerate(key_parts):
            if tok in self.insts:
                last = i
        return key_parts[:last + 1]

    # ---------- send/accept wiring ----------
    def _wire_send_accept(self):
        bodies = []
        loop_bodies = []   # SM self-loops: same-cycle Modbus computations
        for fqn, stmts in self.p.step_action_bodies:
            bodies.append((fqn.split("::")[-1], stmts))
        for name, pd in self.p.part_defs.items():
            insts = [i.name for i in self.p.part_instances.values() if i.part_type == name]
            for act in pd.actions:
                for inst in insts:
                    bodies.append((inst, act.body))
        sm_triggers = []
        for fqn, sm in self.p.instance_state_machines.items():
            inst = fqn.split("::")[-1]
            for t in sm.transitions:
                if t.trigger_var and t.trigger_port:
                    sm_triggers.append((inst, t.trigger_var, t.trigger_port))
                if t.do_action and t.from_state == t.to_state:
                    loop_bodies.append((inst, t.do_action))
        allb = bodies + loop_bodies
        cmap = {}
        for frm, to in self.p.connects:
            a, b = self._pc(frm), self._pc(to); cmap[a] = b; cmap[b] = a
        # index every send by its port (+ sub-channel) -> the sent message
        send_index = {}
        for inst, stmts in allb:
            for s in self._flatten(stmts):
                if isinstance(s, SendStmt):
                    send_index[self._canon([inst] + s.port.split("."))] = self._canon([inst, s.item_name])
                    self._alias(self._port_key(inst, s.port), self._canon([inst, s.item_name]))
        # accepts: link the accepted var DIRECTLY to the message sent on the
        # connected port's matching sub-channel (handles bidirectional Modbus
        # req/resp); fall back to the local port for simple unidirectional ports.
        def wire_accept(inst, var, port):
            parts = port.split(".")
            connected = cmap.get(self._canon([inst, parts[0]]))
            lookup = self._canon([connected] + parts[1:]) if connected else None
            if lookup and lookup in send_index:
                self._alias(self._canon([inst, var]), send_index[lookup])
            else:
                self._alias(self._canon([inst, var]), self._port_key(inst, port))
        for inst, stmts in allb:
            for s in self._flatten(stmts):
                if isinstance(s, AcceptStmt) and s.var_name:
                    wire_accept(inst, s.var_name, s.port)
        for inst, var, port in sm_triggers:
            wire_accept(inst, var, port)
        # same-cycle item-field copies in SM self-loops (sensor response read)
        for inst, stmts in loop_bodies:
            for s in self._flatten(stmts):
                if (isinstance(s, AssignStmt) and len(s.target) >= 2
                        and isinstance(s.expr, RefExpr)):
                    self._alias(self._canon([inst] + s.target),
                                self._canon([inst] + list(s.expr.path)))

    def _port_key(self, inst, port):
        """Canonical key for a port ref. Bare port -> append its single item slot;
        dotted port ('thermometerPort.reading') -> use as given."""
        parts = port.split(".")
        if len(parts) == 1:
            slot = self._port_slot(inst, port)
            if slot:
                parts = parts + [slot]
        return self._canon([inst] + parts)

    def _port_slot(self, inst, port):
        pt = self.inst_by_name.get(inst)
        if not pt: return None
        ptype = self.p.part_def_ports.get(pt.part_type, {}).get(port)
        items = self.p.port_def_items.get(ptype, {}) if ptype else {}
        return next(iter(items)) if len(items) == 1 else None

    def _flatten(self, stmts):
        for s in stmts:
            if isinstance(s, PerformStmt) and s.action_name in self.actions_by_name:
                yield from self._flatten(self.actions_by_name[s.action_name])
                continue
            yield s
            if isinstance(s, IfStmt):
                yield from self._flatten(s.body); yield from self._flatten(s.else_body)

    # ---------- neural ----------
    def _find_neural(self):
        if self.p.controller_part is None:
            raise ValueError(
                "SysML model must contain exactly one part instance that owns a #Neural action"
            )
        ctrl_fqn = self.p.controller_part
        ctrl_instance = self.p.part_instances[ctrl_fqn]
        part_def = self.p.part_defs[ctrl_instance.part_type]
        neural_defs = [
            action for action in part_def.action_defs if "Neural" in action.metadata
        ]
        if len(neural_defs) != 1:
            raise ValueError("the #Neural action owner must define exactly one #Neural action")
        neural = neural_defs[0]
        calls_by_identity = {
            id(statement): statement
            for action in part_def.actions
            for statement in self._flatten(action.body)
            if isinstance(statement, SubactionCallStmt)
            and statement.type_name == neural.name
        }
        calls = list(calls_by_identity.values())
        if len(calls) != 1:
            raise ValueError(
                f"expected exactly one call to #Neural action {neural.name}, found {len(calls)}"
            )
        call = calls[0]
        binds = {
            binding.name: tuple(binding.expr.path)
            for binding in call.bindings
            if isinstance(binding, InputBindingStmt)
            and isinstance(binding.expr, RefExpr)
        }
        self.is_continuous = any(
            (getattr(output, "type_name", "") or "").lower() not in {"bool", "boolean"}
            for output in neural.out_params
        )
        return (
            [output.name for output in neural.out_params],
            binds,
            ctrl_instance.name,
            call.name,
        )

    # ---------- dependency edges ----------
    def _keep(self, k):
        return k in self.ACTIONS or k in self.state_targets or \
               k not in self.consts

    def _build_steps(self):
        for sa in self.p.step_actions:
            tgt = self._canon(sa.target_key.split("::"))
            ctx = self._part_ctx(sa.target_key.split("::"))
            self.STATE.add(tgt)
            deps = self._collect_refs(sa.expression, ctx)
            self.nsupp[tgt] = deps
            if isinstance(sa.expression, RefExpr) and len(deps) == 1:
                self.copies.add(tgt)
                self.checked_copies.add(tgt)

    def _build_state_machines(self):
        cmap = {}                                # port connect map, both directions
        for frm, to in self.p.connects:
            a, b = self._pc(frm), self._pc(to)
            cmap[a] = b; cmap[b] = a
        for fqn, sm in self.p.instance_state_machines.items():
            inst = fqn.split("::")[-1]
            sv = f"{inst}_state"
            latch = self._latch_action(sm, inst)
            state_changing = any(t.from_state != t.to_state for t in sm.transitions)
            trig_vars = {t.trigger_var for t in sm.transitions if t.trigger_var}
            trig_port = next((t.trigger_port for t in sm.transitions if t.trigger_port), None)
            if latch and state_changing:                    # the SM state IS the latch
                self.STATE.add(sv); self.nsupp[sv] = {latch}; self.copies.add(sv)
            for t in sm.transitions:
                for d in (t.do_action or []):
                    if not isinstance(d, AssignStmt):
                        continue
                    tk = self._canon([inst] + d.target)
                    if any(r and r[0] in trig_vars for r in _refs(d.expr)):
                        # Event-driven actuator latch.  Derive the final Boolean
                        # effect from the controller's actual message payloads;
                        # the older dependency-only shortcut was not sufficient
                        # evidence of value equality.
                        effect = self._verified_actuator_effect(
                            inst, trig_port, cmap, t, d
                        )
                        self.STATE.add(tk)
                        if effect is None:
                            self._invalid_action_effect_targets.add(tk)
                            self.nsupp[tk] = {tk}
                            self.action_effects.pop(tk, None)
                            self.checked_copies.discard(tk)
                        elif tk not in self._invalid_action_effect_targets:
                            deps = self._collect_refs(effect, self.ctrl_fqn.split("::"))
                            self.nsupp[tk] = deps or {tk}
                            self.action_effects[tk] = effect
                        if (
                            tk not in self._invalid_action_effect_targets
                            and isinstance(effect, RefExpr)
                            and len(self.nsupp[tk]) == 1
                        ):
                            self.copies.add(tk)
                            self.checked_copies.add(tk)
                    elif latch and state_changing:
                        # latch output following the SM state (e.g. heatOut := watts)
                        self.STATE.add(tk); self.nsupp[tk] = {sv}; self.copies.add(tk)
                    # else: self-loop item-field copy -> alias (in _wire_send_accept)

    def _verified_actuator_effect(self, inst, trig_port, cmap, transition, assignment):
        """Return the exact post-cycle Boolean latch effect, or ``None``.

        This deliberately supports only the small synchronous command profile:
        the actuator directly copies one Boolean trigger field, every relevant
        controller send has a literal Boolean payload, its message satisfies the
        receiver guard, and the action containing the Policy call assigns the
        latch on every Boolean valuation independently of its prior value.
        """
        if not (
            trig_port
            and isinstance(assignment.expr, RefExpr)
            and len(assignment.expr.path) >= 2
            and assignment.expr.path[0] == transition.trigger_var
        ):
            return None
        payload_field = assignment.expr.path[-1]
        ctrl_port = cmap.get(self._canon([inst, trig_port.split(".")[0]]))
        if not ctrl_port:
            return None
        cycle = self._policy_cycle_body()
        if cycle is None:
            return None
        cycle_name, body = cycle
        if not self._actuator_send_scope_is_closed(
            ctrl_port, cycle_name, body
        ):
            return None
        old = RefExpr(["__prior_actuator_value"])
        effect, valid = self._command_effect(
            body, ctrl_port, transition, payload_field, old
        )
        if not valid:
            return None
        return self._eliminate_prior_value(effect, old)

    def _policy_cycle_body(self):
        controller = self.inst_by_name.get(self.ctrl_inst)
        if controller is None:
            return None
        part = self.p.part_defs.get(controller.part_type)
        if part is None:
            return None

        def contains(statements):
            for statement in statements:
                if (isinstance(statement, SubactionCallStmt)
                        and statement.name == self.neural_call):
                    return True
                if isinstance(statement, IfStmt) and (
                    contains(statement.body) or contains(statement.else_body)
                ):
                    return True
            return False

        matches = [
            (action.name, action.body)
            for action in part.actions if contains(action.body)
        ]
        return matches[0] if len(matches) == 1 else None

    def _actuator_send_scope_is_closed(self, ctrl_port, cycle_name, cycle_body):
        controller = self.inst_by_name.get(self.ctrl_inst)
        part = self.p.part_defs.get(controller.part_type) if controller else None
        if part is None:
            return False

        def direct_send(statements):
            for statement in statements:
                if isinstance(statement, SendStmt):
                    port = self._canon(
                        [self.ctrl_inst] + statement.port.split(".")
                    )
                    if port == ctrl_port or port.startswith(ctrl_port + "_"):
                        return True
                if isinstance(statement, IfStmt) and (
                    direct_send(statement.body) or direct_send(statement.else_body)
                ):
                    return True
            return False

        command_actions = {
            action.name for action in part.actions if direct_send(action.body)
        }
        def calls(statements):
            result = set()
            for statement in statements:
                if isinstance(statement, PerformStmt):
                    result.add(statement.action_name)
                elif isinstance(statement, IfStmt):
                    result |= calls(statement.body)
                    result |= calls(statement.else_body)
            return result

        called_by_cycle = calls(cycle_body)
        if not command_actions.issubset(called_by_cycle):
            return False
        for action in part.actions:
            if action.name == cycle_name:
                continue
            if calls(action.body) & command_actions:
                return False
        return True

    def _command_effect(self, statements, ctrl_port, transition, payload_field, current):
        value = current
        for statement in statements:
            if isinstance(statement, PerformStmt):
                command, relevant, valid = self._performed_command(
                    statement.action_name, ctrl_port, transition, payload_field
                )
                if relevant and not valid:
                    return value, False
                if relevant:
                    value = command
            elif isinstance(statement, IfStmt):
                on_true, true_valid = self._command_effect(
                    statement.body, ctrl_port, transition, payload_field, value
                )
                on_false, false_valid = self._command_effect(
                    statement.else_body, ctrl_port, transition, payload_field, value
                )
                if not true_valid or not false_valid:
                    return value, False
                value = (on_true if on_true == on_false else
                         TernaryExpr(statement.condition, on_true, on_false))
            elif isinstance(statement, SendStmt):
                port = self._canon([self.ctrl_inst] + statement.port.split("."))
                if port == ctrl_port or port.startswith(ctrl_port + "_"):
                    # Direct sends need local item-flow evaluation, which is not
                    # part of this deliberately small profile.
                    return value, False
        return value, True

    def _performed_command(self, action_name, ctrl_port, transition, payload_field):
        body = self.actions_by_name.get(action_name)
        if body is None:
            return None, False, True
        item_types = {}
        fields = {}
        result = None
        relevant = False
        valid = True
        for statement in body:
            # Avoid depending on the parser's item-declaration class here; the
            # stable fields are sufficient and keep this extractor compatible
            # with both parser front ends used by the repository.
            if statement.__class__.__name__ == "ItemDeclStmt":
                item_types[statement.name] = statement.type_name
            elif isinstance(statement, AssignStmt) and len(statement.target) >= 2:
                fields[tuple(statement.target)] = statement.expr
            elif isinstance(statement, SendStmt):
                port = self._canon([self.ctrl_inst] + statement.port.split("."))
                if not (port == ctrl_port or port.startswith(ctrl_port + "_")):
                    continue
                relevant = True
                if item_types.get(statement.item_name) != transition.trigger:
                    valid = False
                    continue
                message_fields = {
                    target[-1]: expr for target, expr in fields.items()
                    if target[0] == statement.item_name
                }
                if not self._message_guard_true(
                    transition.guard, transition.trigger_var, message_fields
                ):
                    valid = False
                    continue
                payload = message_fields.get(payload_field)
                if not (
                    isinstance(payload, LiteralExpr)
                    and isinstance(payload.value, bool)
                ):
                    valid = False
                    continue
                result = LiteralExpr(payload.value)
            elif isinstance(statement, (IfStmt, PerformStmt)):
                # A command helper with internal control flow is outside the
                # flat literal-payload profile.  Reject it if that flow can send
                # on the actuator port rather than silently skipping the send.
                if self._contains_send_to_port(
                    [statement], ctrl_port, seen={action_name}
                ):
                    relevant = True
                    valid = False
        return result, relevant, valid and result is not None

    def _contains_send_to_port(self, statements, ctrl_port, seen):
        for statement in statements:
            if isinstance(statement, SendStmt):
                port = self._canon([self.ctrl_inst] + statement.port.split("."))
                if port == ctrl_port or port.startswith(ctrl_port + "_"):
                    return True
            elif isinstance(statement, IfStmt):
                if (self._contains_send_to_port(statement.body, ctrl_port, seen)
                        or self._contains_send_to_port(
                            statement.else_body, ctrl_port, seen
                        )):
                    return True
            elif isinstance(statement, PerformStmt):
                if statement.action_name in seen:
                    return True
                nested = self.actions_by_name.get(statement.action_name, [])
                if self._contains_send_to_port(
                    nested, ctrl_port, seen | {statement.action_name}
                ):
                    return True
        return False

    def _message_guard_true(self, expression, trigger_var, fields):
        if expression is None:
            return True

        def evaluate(node):
            if isinstance(node, LiteralExpr):
                return node.value
            if isinstance(node, RefExpr):
                if len(node.path) >= 2 and node.path[0] == trigger_var:
                    source = fields.get(node.path[-1])
                    return evaluate(source) if source is not None else None
                return None
            if isinstance(node, UnaryExpr):
                value = evaluate(node.operand)
                if value is None:
                    return None
                if node.op == "not": return not value
                if node.op == "-": return -value
                return None
            if isinstance(node, BinaryExpr):
                left, right = evaluate(node.left), evaluate(node.right)
                if left is None or right is None:
                    return None
                operations = {
                    "==": lambda: left == right, "!=": lambda: left != right,
                    ">": lambda: left > right, ">=": lambda: left >= right,
                    "<": lambda: left < right, "<=": lambda: left <= right,
                    "and": lambda: bool(left) and bool(right),
                    "or": lambda: bool(left) or bool(right),
                    "+": lambda: left + right, "-": lambda: left - right,
                    "*": lambda: left * right, "/": lambda: left / right,
                }
                return operations[node.op]() if node.op in operations else None
            return None

        return evaluate(expression) is True

    def _eliminate_prior_value(self, expression, old):
        names = sorted({
            path[-1] for path in _refs(expression)
            if len(path) == 2 and path[0] == self.neural_call
            and path[-1] in self.neural_out
        })

        def evaluate(node, assignment, prior):
            if isinstance(node, LiteralExpr) and isinstance(node.value, bool):
                return node.value
            if isinstance(node, RefExpr):
                if node == old:
                    return prior
                if len(node.path) == 2 and node.path[0] == self.neural_call:
                    return assignment.get(node.path[-1])
                return None
            if isinstance(node, UnaryExpr) and node.op == "not":
                value = evaluate(node.operand, assignment, prior)
                return None if value is None else not value
            if isinstance(node, BinaryExpr) and node.op in {"and", "or", "implies"}:
                left = evaluate(node.left, assignment, prior)
                right = evaluate(node.right, assignment, prior)
                if left is None or right is None:
                    return None
                if node.op == "and": return left and right
                if node.op == "or": return left or right
                return (not left) or right
            if isinstance(node, TernaryExpr):
                condition = evaluate(node.condition, assignment, prior)
                if condition is None:
                    return None
                return evaluate(
                    node.true_expr if condition else node.false_expr,
                    assignment, prior,
                )
            return None

        rows = []
        for bits in range(1 << len(names)):
            assignment = {
                name: bool(bits & (1 << index))
                for index, name in enumerate(names)
            }
            low = evaluate(expression, assignment, False)
            high = evaluate(expression, assignment, True)
            if low is None or high is None or low != high:
                return None
            rows.append((assignment, low))
        if all(value for _assignment, value in rows):
            return LiteralExpr(True)
        if all(not value for _assignment, value in rows):
            return LiteralExpr(False)
        refs = {name: RefExpr([self.neural_call, name]) for name in names}
        for name in names:
            if all(value == assignment[name] for assignment, value in rows):
                return refs[name]
            if all(value != assignment[name] for assignment, value in rows):
                return UnaryExpr("not", refs[name])
        true_rows = [assignment for assignment, value in rows if value]
        terms = []
        for assignment in true_rows:
            literals = [
                refs[name] if assignment[name] else UnaryExpr("not", refs[name])
                for name in names
            ]
            term = literals[0]
            for literal in literals[1:]:
                term = BinaryExpr("and", term, literal)
            terms.append(term)
        result = terms[0]
        for term in terms[1:]:
            result = BinaryExpr("or", result, term)
        return result

    def _actuator_decision(self, inst, trig_port, cmap):
        """For a coil-write actuator, find the controller decision that drives it:
        the actuator's cmd port connects to a controller port; scan the controller
        for a condition on the extracted neural-action call whose body sends
        through that port."""
        if not trig_port:
            return None
        ctrl_port = cmap.get(self._canon([inst, trig_port.split(".")[0]]))
        if not ctrl_port:
            return None
        field = self._find_decision_for_port(ctrl_port)
        return self._canon([self.ctrl_inst, self.neural_call, field]) if field else None

    def _find_decision_for_port(self, ctrl_port):
        for pd in self.p.part_defs.values():
            if any(i.part_type == pd.name and i.name == self.ctrl_inst
                   for i in self.p.part_instances.values()):
                for act in pd.actions:
                    r = self._scan_dec(act.body, ctrl_port, None)
                    if r:
                        return r
        return None

    def _scan_dec(self, stmts, ctrl_port, cur):
        for s in stmts:
            if isinstance(s, IfStmt):
                d = cur
                for r in _refs(s.condition):
                    if r and r[0] == self.neural_call:
                        d = r[-1]
                res = self._scan_dec(s.body, ctrl_port, d) \
                    or self._scan_dec(s.else_body, ctrl_port, cur)
                if res:
                    return res
            elif isinstance(s, PerformStmt) and s.action_name in self.actions_by_name:
                res = self._scan_dec(self.actions_by_name[s.action_name], ctrl_port, cur)
                if res:
                    return res
            elif isinstance(s, SendStmt) and cur:
                if self._canon([self.ctrl_inst] + s.port.split(".")).startswith(ctrl_port):
                    return cur
        return None

    def _latch_action(self, sm, inst):
        ports = {t.trigger_port for t in sm.transitions if t.trigger_port}
        for port in ports:
            src = self.alias.get(self._canon([inst, port]))
            if not src:
                continue
            a = self._controlport_action(src)
            if a:
                return a
        return None

    def _controlport_action(self, ctrl_port_canon):
        for name, pd in self.p.part_defs.items():
            for act in pd.actions:
                hit = self._scan_if_send(act.body, ctrl_port_canon)
                if hit:
                    return self._canon([self.ctrl_inst, self.neural_call, hit])
        return None

    def _scan_if_send(self, stmts, ctrl_port_canon):
        for s in stmts:
            if isinstance(s, IfStmt):
                for snd in [d for d in s.body if isinstance(d, SendStmt)]:
                    if self._canon([self.ctrl_inst, snd.port]) == ctrl_port_canon:
                        for r in _refs(s.condition):
                            if r and r[0] == self.neural_call:
                                return r[-1]
                r = (self._scan_if_send(s.body, ctrl_port_canon)
                     or self._scan_if_send(s.else_body, ctrl_port_canon))
                if r: return r
        return None

    def _build_obs(self):
        obs = set()
        ctx = self.ctrl_fqn.split("::")
        for nm, ref in self.in_binds.items():
            for k in self._collect_one(list(ref), ctx):
                if k in self.state_targets:
                    obs.add(k)
        return obs & self.STATE

    def _build_R(self):
        R = set()
        for req in self.p.parsed_requirements:
            ctx = req.context.split("::") if req.context else [self.sys]
            for r in _refs(req.expression):
                rr = list(r)
                if rr and rr[0] == req.subject_var:
                    rr = rr[1:]
                if not rr:
                    continue
                for k in self._collect_one(rr, ctx):
                    if k in self.STATE:
                        R.add(k)
        return R

def get_model(path):
    m = SysMLModel(path)
    return dict(STATE=m.STATE, ACTIONS=m.ACTIONS, nsupp=m.nsupp,
                copies=m.copies, OBS=m.OBS, R=m.R,
                continuous=getattr(m, "is_continuous", False))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("provide one SysML file path")
    path = sys.argv[1]
    m = SysMLModel(path)
    print(f"MODEL: {path}")
    print(f"ACTIONS: {sorted(m.ACTIONS)}")
    print(f"OBS:     {sorted(m.OBS)}")
    print(f"R:       {sorted(m.R)}")
    print("STATE next-deps:")
    for v in sorted(m.STATE):
        c = " [copy]" if v in m.copies else ""
        print(f"   {v}{c}  <-  {sorted(m.nsupp.get(v, set()))}")
