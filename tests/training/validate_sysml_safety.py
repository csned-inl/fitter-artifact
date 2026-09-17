#!/usr/bin/env python3
"""Check safety accounting against actual temporary SysML specifications."""
from __future__ import annotations
import argparse
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
import numpy as np
from clarity.models import models_root
from clarity.runtime.env import SysMLEnv
from clarity.runtime.requirements import RequirementEvent, ResetResult, ResetUnavailable
from clarity.runtime.oracle import extract_interface
from clarity.pipeline.affine_rule import evaluate as evaluate_rule
from clarity.pipeline.stages.training import _select_models
from clarity.sysml.inputs import discover_sysml
from clarity.sysml.parser import SysMLParser
from clarity.training.reduced.buffered_env import BufferedDiscreteEnv
from clarity.training.reduced.policy import MLPActorCritic
from clarity.training.reduced.composite import ProgramShieldComposite
from clarity.training.reduced.episode import collect_episode, measure_episodes, summarize_measurements
from clarity.training.reduced.collection import DiscreteRuntime, CollectionSettings, build_episode_collector, make_episode_jobs
from clarity.training.recurrent.episode import evaluate as evaluate_recurrent


def check_source_requirements(out_dir: Path) -> dict:
    variants = out_dir / 'sysml_safety_variants'
    variants.mkdir(parents=True, exist_ok=True)
    models = discover_sysml([], models_root=models_root())
    rows = []
    for model in models:
        parser = SysMLParser(str(model.path)); parser.parse()
        text = model.path.read_text()
        for index, requirement in enumerate(parser.parsed_requirements):
            name = f'Audit {model.key} requirement {index}'
            changed = text.replace(requirement.raw_text, 'false', 1)
            assert changed != text
            changed = changed.replace(f"requirement def '{requirement.name}'", f"requirement def '{name}'", 1)
            path = variants / f'{model.key}-{index}.sysml'; path.write_text(changed)
            iface = extract_interface(str(path))
            env = BufferedDiscreteEnv(str(path), dt=.1, max_steps=3, phase=2, n_obs=0, n_act=0, observation_scale=1)
            policy = MLPActorCritic(env.obs_dim, env.n_actions, 4, seed=0)
            composite = ProgramShieldComposite(policy, iface['spec_shield'], iface['obs_names'])
            try:
                episode = collect_episode(env, composite, greedy=True, reset_seed=123)
                assert episode.outcome == 'VIOLATION'
                assert name in episode.violations
                assert set(episode.requirement_checks) == {
                    name if req.name == requirement.name else req.name for req in parser.parsed_requirements
                }
                # The serialized Boolean flag cannot suppress named violations.
                episode.safety_viol = False
                summary = summarize_measurements(measure_episodes([episode.compact()]))
                assert summary.safety_violation_rate == 1.0
                assert summary.requirement_violation_episodes[name] == 1
                runtime = DiscreteRuntime(str(path), .1, 3, 2, 0, 0, env.obs_dim, env.n_actions, 4, observation_scale=1)
            finally:
                env.close()
            direct = evaluate_rule(path, episodes=2, seed=0, dt=.1, max_steps=3, observation_scale=1)
            assert direct['safety_violations'] == 2 and direct['safety_violation_rate'] == 1.0
            assert direct['requirement_violation_episodes'][name] == 2
            recurrent = evaluate_recurrent(
                lambda: SysMLEnv(str(path), dt=.1, max_steps=3, phase=2, observation_scale=1),
                composite, n_episodes=2,
            )
            assert recurrent.safety_violation_rate == 1.0
            assert recurrent.requirement_violation_episodes[name] == 2
            candidates = [{'model': model.key, 'status': 'trained', 'test_safety': summary.safety_violation_rate}]
            assert _select_models(candidates, override_tolerance=.01, models=[model])[0]['selection_status'] == 'no_safe_trained_candidate'
            if not rows:
                # The process collector must preserve property names and counts.
                backend_counts = []
                for backend in ['serial', 'process']:
                    with build_episode_collector(CollectionSettings(backend=backend, workers=2), runtime, composite) as collector:
                        result = collector.evaluate(policy, make_episode_jobs(4, seed_base=20000))
                        assert result.safety_violation_rate == 1.0
                        assert result.requirement_violation_episodes[name] == 4
                        backend_counts.append(result.requirement_checks)
                assert backend_counts[0] == backend_counts[1]
            rows.append({'model': model.key, 'original_requirement': requirement.name,
                         'kind': requirement.metadata, 'renamed_requirement': name,
                         'reduced_safety_rate': summary.safety_violation_rate,
                         'direct_safety_rate': direct['safety_violation_rate'],
                         'recurrent_safety_rate': recurrent.safety_violation_rate})
    assert len(rows) == 12
    (out_dir/'source_requirement_results.json').write_text(json.dumps(rows, indent=2)+'\n')
    return {'source_requirements_tested': len(rows), 'all_counted_in_three_evaluation_paths': True}


def check_invalid_requirements(out_dir: Path) -> dict:
    model = next(m for m in discover_sysml([], models_root=models_root()) if m.key == 'tank-filling-system')
    parser = SysMLParser(str(model.path)); parser.parse()
    req = parser.parsed_requirements[0]
    results = []
    for expression in ['true unsupported_suffix', 'not s.attributeThatDoesNotExist', '(1 / 0 == 0)', '1']:
        path = out_dir / f'invalid-requirement-{len(results)}.sysml'
        path.write_text(model.path.read_text().replace(req.raw_text, expression, 1))
        env = None
        try:
            env = SysMLEnv(str(path), dt=.1, phase=2, observation_scale=1)
            env.reset(seed=0)
            env.step(0)
        except (ValueError, ResetUnavailable) as exc:
            if isinstance(exc, ResetUnavailable):
                assert exc.result.outcome == 'error' and (exc.result.error or exc.result.errors)
            results.append({'expression': expression, 'rejected': str(exc)})
        else:
            raise AssertionError(f'invalid requirement silently accepted: {expression}')
        finally:
            if env is not None: env.close()
    duplicate = out_dir/'duplicate-requirement.sysml'
    duplicate.write_text(model.path.read_text().replace(
        f"requirement def '{parser.parsed_requirements[1].name}'", f"requirement def '{req.name}'", 1))
    try:
        other = SysMLParser(str(duplicate)); other.parse()
        from clarity.certification.ordered_execution import source_requirement_inventory
        source_requirement_inventory(other)
    except ValueError as exc:
        assert 'duplicate' in str(exc)
    else:
        raise AssertionError('duplicate requirement can overwrite a result')
    return {'invalid_expressions': results, 'duplicate_name_rejected': True}


def check_reserve() -> dict:
    model = next(m for m in discover_sysml([], models_root=models_root()) if m.key == 'tank-filling-system')
    env = SysMLEnv(str(model.path), dt=.1, phase=2)
    try:
        for job in make_episode_jobs(200, seed_base=20000):
            initial = env.reset_with_result(seed=job.env_seed)
            assert initial.error is None and not initial.errors
            raw = env.model_inputs
            for tank in [1, 2]:
                original = raw[f'tank{tank}OriginalMl']; target = raw[f'tank{tank}TargetTransferMl']
                assert target >= 0
                assert target == 0 or original - target >= 5
    finally:
        env.close()
    return {'scenarios': 200, 'tank_reserve_checks': 400}


def check_boundary_and_terminal_results() -> dict:
    class Composite:
        policy = SimpleNamespace(initial_hidden=lambda n: np.zeros((n, 1)))
        def act(self, *args, **kw):
            return 0, 0., 0., np.zeros((1, 1)), False, None, dict(policy_us=0., shield_us=0., total_us=0.)
        def requirement_holds(self, *args):
            raise AssertionError('the neural rule must not decide a safety metric')
    class Env:
        model_inputs = {}
        def reset_with_result(self, **kw):
            self.index = 0
            return ResetResult(np.zeros(1), 'decision', (RequirementEvent(1, 0, 'initialization', '', 0, {}),))
        def step(self, action):
            self.index += 1
            return np.zeros(1), float(self.index == 2), self.index == 2, {
                'requirement_events': (RequirementEvent(1, self.index, 'cycle_end', '', self.index,
                    {'Unlisted safety property': {'status': self.index == 2, 'kind': 'Obligation', 'error':None}}),),
                'outcome': 'SUCCESS' if self.index == 2 else 'RUNNING'}
    ep = collect_episode(Env(), Composite())
    assert ep.violations == ['Unlisted safety property'] and ep.safety_viol
    assert ep.requirement_checks == {'Unlisted safety property': 2}
    # Actual environment reward gives a false property priority over completion.
    env = object.__new__(SysMLEnv); env.phase = 2; env._completion_key = 'done'
    statuses = {'Any source name': {'kind': 'Obligation', 'status': False}}
    assert env._compute_reward({'done': True}, statuses) == (-1., True)
    return {'boundary_failure_preserved': True, 'completion_cannot_mask_failure': True}



def check_original_dry_running_failure() -> dict:
    """Negative control: restore the known unsafe request in this test only."""
    model = next(m for m in discover_sysml([], models_root=models_root()) if m.key == 'tank-filling-system')
    env = BufferedDiscreteEnv(str(model.path), dt=.1, max_steps=100, phase=1, n_obs=0, n_act=0, observation_scale=1)
    original_sample = env._sample_scenario
    def unsafe_request():
        scenario = original_sample()
        for tank in [1, 2]:
            original = scenario[f'system::controller::tank{tank}OriginalLevelMl']
            assert original <= (50 if tank == 1 else 100)
            scenario[f'system::controller::tank{tank}TransferMl'] = original
        return scenario
    env._sample_scenario = unsafe_request
    # Fault-injection fixture uses phase 1 to retain the whole trace after the
    # independently detected initialization failure; all failures still count.
    iface = extract_interface(str(model.path))
    policy = MLPActorCritic(env.obs_dim, env.n_actions, 4, seed=0)
    composite = ProgramShieldComposite(policy, iface['spec_shield'], iface['obs_names'])
    try:
        episode = collect_episode(env, composite, greedy=True, reset_seed=1323514403)
        actual = env._twin.engine.requirement_statuses()
        assert actual['No Dry Running']['status'] is False
        assert 'No Dry Running' in episode.violations
        assert episode.safety_viol
        summary = summarize_measurements(measure_episodes([episode]))
        assert summary.safety_violation_rate == 1.0
        assert summary.requirement_violation_episodes['No Dry Running'] == 1
    finally:
        env.close()
    return {'original_specification_unchanged': True, 'No Dry Running': 'counted',
            'environment_seed': 1323514403, 'safety_violation_rate': 1.0}


def validate(out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    result = {'original_dry_running_failure': check_original_dry_running_failure(),
              'source_requirements': check_source_requirements(out_dir),
              'invalid_requirements': check_invalid_requirements(out_dir),
              'reserve': check_reserve(),
              'boundary_and_terminal': check_boundary_and_terminal_results()}
    (out_dir/'sysml_safety_validation.json').write_text(json.dumps(result, indent=2)+'\n')
    return result

if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--out-dir', type=Path)
    args = parser.parse_args()
    if args.out_dir:
        print(json.dumps(validate(args.out_dir), indent=2))
    else:
        with tempfile.TemporaryDirectory(prefix='clarity-sysml-safety-') as temporary:
            print(json.dumps(validate(Path(temporary)), indent=2))
