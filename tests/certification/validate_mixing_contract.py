"""Reproduce the literal mixing requirement/contract conflict without changing it.

The four-row check concerns completed-scan states, not reachability. The
separate trace starts from the declared initial state, executes the original
actions with the source controller contract, and retains every safety event.
That trace is evidence about the current CLARITY execution, not a proof of the
unspecified SysML scheduler.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict
from pathlib import Path

import z3

from clarity.models import models_root
from clarity.runtime.requirements import RequirementLedger
from clarity.runtime.shield import SpecShield
from clarity.sysml.parser import SysMLParser, ExpressionParser
from clarity.sysml.simulator import SimulationEngine, ExpressionEvaluator


def check(model_path, *, max_cycles=5000, dt=.1):
    parser = SysMLParser(str(model_path)); parser.parse()
    requirement = next(r for r in parser.parsed_requirements
                       if r.name == 'Fluid Transfer Liveness')
    shield = SpecShield(str(model_path))
    rows = []
    for first_pending in (False, True):
        for second_pending in (False, True):
            engine = SimulationEngine(parser); engine.initialize()
            state = engine.state
            prefix = 'system::controller::'
            original1 = state[prefix + 'tank1OriginalLevelMl']
            original2 = state[prefix + 'tank2OriginalLevelMl']
            target1 = state[prefix + 'tank1TransferMl']
            target2 = state[prefix + 'tank2TransferMl']
            level1 = original1 - target1 + int(first_pending)
            level2 = original2 - target2 + int(second_pending)
            inputs = dict(tank1VolumeMl=level1, tank2VolumeMl=level2,
                          tank1TargetTransferMl=target1, tank2TargetTransferMl=target2,
                          tank1OriginalMl=original1, tank2OriginalMl=original2,
                          done=not (first_pending or second_pending))
            action = shield.requirement_action(inputs)
            outputs = shield.action_map[action]
            state.update({prefix + 'observedLevel1': level1,
                          prefix + 'observedLevel2': level2,
                          prefix + 'lastScanTimeSeconds': 1.0,
                          'system::pump1::isRunning': outputs['shouldTurnOnPump1'],
                          'system::pump2::isRunning': outputs['shouldTurnOnPump2'],
                          'system::valve1::isOpen': outputs['shouldOpenValve1'],
                          'system::valve2::isOpen': outputs['shouldOpenValve2']})
            literal = ExpressionEvaluator(state, requirement.context,
                parser.ref_bindings, parser.system_part, strict=True).evaluate(requirement.expression)
            # This is an independent Boolean reduction, not a substitute used
            # by the runtime. Verify it against the actual parsed source AST.
            a, c = first_pending, second_pending
            b = outputs['shouldOpenValve1'] and outputs['shouldTurnOnPump1']
            d = outputs['shouldOpenValve2'] and outputs['shouldTurnOnPump2']
            chained = (not ((not a) or (b and c))) or d
            assert literal is chained
            proposed = ((not a) or b) and ((not c) or d)
            rows.append(dict(first_pending=a, second_pending=c, inputs=inputs,
                             outputs=outputs, original_requirement=literal,
                             proposed_parenthesized_requirement=proposed))

    # Symbolic, exhaustive Boolean proof: at a completed contract-conforming
    # scan, the literal requirement is equivalent to A or C, not true.
    a, b, c, d = z3.Bools('tank1_pending tank1_on tank2_pending tank2_on')
    literal = z3.Implies(z3.Implies(a, z3.And(b, c)), d)
    contract = z3.And(b == a, d == c)
    equivalence = z3.Solver(); equivalence.add(contract, literal != z3.Or(a, c))
    contradiction = z3.Solver()
    contradiction.add(contract, z3.Not(a), z3.Not(c), literal)
    proposed = z3.Solver()
    proposed.add(contract, z3.Not(z3.And(z3.Implies(a, b), z3.Implies(c, d))))
    checks = {'literal_reduces_to_any_target_pending': str(equivalence.check()),
              'completed_targets_contract_and_literal_requirement': str(contradiction.check()),
              'parenthesized_per_tank_implications_follow_from_contract': str(proposed.check())}
    assert all(result == 'unsat' for result in checks.values())

    engine = SimulationEngine(parser); engine.initialize()
    engine.requirement_ledger = RequirementLedger()
    engine.record_requirements('initialization')
    actions = []
    def controller(inputs):
        action = shield.requirement_action(inputs)
        actions.append({'inputs': dict(inputs), 'action': action,
                        'outputs': dict(shield.action_map[action])})
        return shield.action_map[action]
    engine.model = controller
    events = list(engine.requirement_ledger.drain())
    for cycle in range(max_cycles):
        engine.step(dt)
        events.extend(engine.requirement_ledger.drain())
        if actions and actions[-1]['inputs']['done']:
            break
    else:
        raise AssertionError('declared initialization did not complete within the configured cycle limit')
    statuses = engine.requirement_statuses()
    assert statuses['Fluid Transfer Liveness']['status'] is False
    report = {
        'source_path': str(Path(model_path).resolve()),
        'source_sha256': hashlib.sha256(Path(model_path).read_bytes()).hexdigest(),
        'completed_scan_truth_table': rows, 'solver_checks': checks,
        'source_executed_trace': {
            'scope': 'current_CLARITY_execution_from_declared_initialization',
            'dt': dt, 'maximum_cycles': max_cycles, 'cycles': cycle + 1,
            'engine_time': engine.time, 'controller_calls': len(actions),
            'last_controller_call': actions[-1], 'final_statuses': statuses,
            'state': {key: value for key, value in engine.state.items()
                      if type(value) in (int, float, bool)},
            'safety_event_count': len(events),
            'false_requirement_events': sum(any(row['status'] is False
                for row in event.statuses.values()) for event in events),
        },
    }
    return report, [asdict(event) for event in events]


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--model', type=Path, default=models_root() / 'mixing-sysml-model/model.sysml')
    p.add_argument('--out', type=Path, required=True)
    args = p.parse_args()
    report, events = check(args.model)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / 'mixing-contract-conflict.json').write_text(json.dumps(report, indent=2) + '\n')
    (args.out / 'mixing-contract-events.jsonl').write_text(''.join(json.dumps(e) + '\n' for e in events))
    print(json.dumps({'solver_checks':report['solver_checks'],
                      'final_statuses':report['source_executed_trace']['final_statuses']}, indent=2))


if __name__ == '__main__':
    main()
