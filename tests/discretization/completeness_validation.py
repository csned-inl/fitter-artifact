"""Regression checks for missing proofs, false results, and the training gate."""

from __future__ import annotations

import copy
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

from clarity.certification.equations import Const, Op, Var
from clarity.discretization.certificates import (
    certificate_hash, compact_analysis, expand_analysis, load_certificate,
    verify_certificate,
)
from clarity.discretization.certificates.verification.verifier import (
    _resolve_shared_proof_certificates, verify_case_evidence,
)
from clarity.discretization.model.proof_rules import expr_to_dict, prove_implication_exact
from clarity.pipeline import runner
from clarity.pipeline import discretization_safety as generation_cli
from clarity.pipeline.stages.discretization import run_discretization_stage
from clarity.sysml.inputs import discover_sysml


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def _expanded(certificate):
    certificate = copy.deepcopy(certificate)
    analysis, errors = expand_analysis(certificate['analysis'])
    require(not errors, str(errors))
    analysis, errors = _resolve_shared_proof_certificates(analysis)
    require(not errors, str(errors))
    analysis.pop('shared_proof_certificates', None)
    certificate['analysis'] = analysis
    certificate['self_sha256'] = certificate_hash(certificate)
    return certificate


def _check_mutation(base, label, mutate, *, full=False, diagnostic=None):
    certificate = copy.deepcopy(base)
    mutate(certificate)
    # Rebuild all outer storage metadata: this must fail semantic checking.
    certificate['analysis'] = compact_analysis(certificate['analysis'])
    certificate['self_sha256'] = certificate_hash(certificate)
    report = verify_certificate(certificate, check_files=full)
    require(not report['valid'] and not report['safety_certified'], f'{label}: mutation accepted')
    if diagnostic:
        require(any(diagnostic in error for error in report['errors']),
                f'{label}: missing diagnostic {diagnostic!r}: {report["errors"]}')


def validate_completeness(paths):
    checked = 0
    for path in paths:
        stored = load_certificate(path)
        baseline = verify_certificate(stored)
        require(baseline['safety_certified'], f'baseline rejected: {baseline["errors"]}')
        require(baseline['obligations_certified'] == 2 * baseline['properties_checked'],
                'required obligation count is incorrect')
        base = _expanded(stored)
        structural = verify_certificate(base, check_files=False)
        require(structural['valid'] and not structural['source_verified'] and not structural['safety_certified'],
                'structural-only checking granted source-verified approval')
        first = lambda c: c['analysis']['properties'][0]
        root = lambda c: first(c)['cases'][0]
        mutations = [
            ('empty attempts', lambda c: root(c).__setitem__('progression', []), 'no recorded proof attempts'),
            ('missing successful attempt', lambda c: root(c).__setitem__('progression',
                [s for s in root(c)['progression'] if s['outcome'] != 'CERTIFIED']), 'no verified successful proof'),
            ('empty proof', lambda c: root(c)['progression'][-1].__setitem__('proof', {}), None),
            ('unknown checker', lambda c: root(c)['progression'][-1].__setitem__('checker', 'unrecognized'), 'unsupported proof checker'),
            ('missing obligation', lambda c: first(c)['cases'].pop(), 'requires exactly one'),
            ('duplicate obligation', lambda c: first(c)['cases'].append(copy.deepcopy(root(c))), 'duplicate case identifier'),
            ('unsupported coverage', lambda c: first(c)['reduction']['case_coverage']['obligations']['sampled_point'].__setitem__('rule', 'unknown'), 'unsupported obligation coverage rule'),
            ('false case label', lambda c: root(c).__setitem__('result', 'NOT_CERTIFIED'), 'recorded result disagrees'),
            ('false requirement label', lambda c: first(c).__setitem__('result', 'NOT_CERTIFIED'), 'disagrees with verified result'),
            ('false analysis label', lambda c: c['analysis'].__setitem__('result', 'NOT_CERTIFIED'), 'analysis result'),
            ('false certificate label', lambda c: c.__setitem__('result', 'NOT_CERTIFIED'), 'certificate result'),
            ('deferred reduction claiming safety', lambda c: first(c).__setitem__('reduction', {'outcome':'DEFERRED'}), 'deferred reduction'),
            ('empty properties claiming safety', lambda c: c['analysis'].__setitem__('properties', []), 'analysis result'),
            ('blocking diagnostics claiming safety', lambda c: c['analysis'].__setitem__('blocking_diagnostics', ['unresolved']), 'analysis result'),
            ('missing shared region', lambda c: c['analysis']['shared_reachability'].__setitem__('regions', {}), 'reference is missing'),
        ]
        for label, mutation, diagnostic in mutations:
            _check_mutation(base, label, mutation, diagnostic=diagnostic)
            checked += 1
        _check_mutation(base, 'missing source requirement', lambda c: c['analysis']['properties'].pop(),
                        full=True, diagnostic='SysML requirements')
        checked += 1
        # A legitimate unresolved obligation remains valid evidence of non-certification.
        unresolved = copy.deepcopy(base)
        root(unresolved)['progression'] = [{'checker':'linear','outcome':'DEFERRED','proof':{}}]
        root(unresolved)['result'] = 'NOT_CERTIFIED'
        first(unresolved)['result'] = 'NOT_CERTIFIED'
        unresolved['analysis']['result'] = unresolved['result'] = 'NOT_CERTIFIED'
        unresolved['claim']['status'] = 'not_discharged'
        unresolved['self_sha256'] = certificate_hash(unresolved)
        report = verify_certificate(unresolved)
        require(report['valid'] and report['source_verified'] and not report['safety_certified'],
                f'unresolved evidence mishandled: {report["errors"]}')
        # Explicit deferred reductions may carry no cases, but never count as proof.
        deferred = copy.deepcopy(unresolved)
        first(deferred)['reduction'] = {'outcome':'DEFERRED'}
        first(deferred)['cases'] = []
        deferred['self_sha256'] = certificate_hash(deferred)
        report = verify_certificate(deferred)
        require(report['valid'] and not report['safety_certified'], f'deferred reduction mishandled: {report["errors"]}')
    validate_symbolic_dispatch()
    validate_pipeline_gate(paths[0])
    validate_saved_artifact_reload(paths[0])
    print(f'proof completeness: PASSED ({len(paths)} complete certificates; {checked} rejected mutations; unresolved and deferred evidence)')


def validate_symbolic_dispatch():
    expression = Op('and', (Op('>=', (Var('x'), Const(1))), Op('<=', (Var('x'), Const(0)))))
    exact = prove_implication_exact([expression], Const(False), set())
    case = {'case_id':'symbolic', 'expression':expr_to_dict(expression), 'progression':[
        {'checker':'linear','outcome':'DEFERRED','proof':{}},
        {'checker':'exact_symbolic','outcome':'CERTIFIED','proof':{
            'rule':'reduced_case_exact_infeasibility_v2','within_interval_proof':exact}},
    ]}
    require(verify_case_evidence(case, {}, {})['result'] == 'CERTIFIED', 'symbolic replay rejected')
    case['expression'] = expr_to_dict(Op('>=', (Var('x'), Const(1))))
    require(verify_case_evidence(case, {}, {})['errors'], 'symbolic proof accepted for feasible substituted expression')


def validate_pipeline_gate(certificate_path):
    cert = load_certificate(certificate_path)
    models = discover_sysml([cert['model']['path']], models_root=Path(cert['model']['path']).parent)
    with tempfile.TemporaryDirectory(prefix='clarity-gate-') as directory:
        out_dir = Path(directory) / 'run'
        def incomplete_stage(out_dir, *_args, **kwargs):
            summary = {'settings':{'dt':0.1}, 'rows':[{
                'model':models[0].key,'result':'CERTIFIED','checker':'passed',
                'safety_certified':False,'properties_checked':3,'properties_certified':2,
                'elapsed_seconds':0.0,
            }]}
            def run_command(*_args):
                path = out_dir / '04_discretization_safety/generation.json'
                path.write_text(json.dumps(summary))
                return {'returncode':0}, ''
            with patch('clarity.pipeline.stages.discretization.run_command', side_effect=run_command), \
                 patch('clarity.pipeline.stages.discretization.write_command_summary_log'):
                return run_discretization_stage(out_dir, sys.executable, models, 0.1,
                    dt_text='0.1',optimization_timeout_ms=250,smt_timeout_ms=30000)
        with patch.object(runner,'run_affine_stage',return_value={}), \
             patch.object(runner,'run_memoryless_stage',return_value={}), \
             patch.object(runner,'run_markov_stage',return_value={}), \
             patch.object(runner,'run_discretization_stage',side_effect=incomplete_stage), \
             patch.object(runner,'run_training_stage') as training:
            try:
                runner.run_pipeline(models=models,out_dir=out_dir,python_bin=sys.executable,
                    dt=0.1,dt_text='0.1',preprocessing_only=False,smoke_training=False,
                    training_jobs=None,override_tolerance=0.01,collection_backend='auto',
                    collection_workers_per_job=None,collection_start_method='spawn',
                    discretization_optimization_timeout_ms=250,discretization_smt_timeout_ms=30000)
            except RuntimeError as exc:
                require('certificates failed' in str(exc), str(exc))
            else:
                raise AssertionError('pipeline accepted incomplete verification')
            training.assert_not_called()
    print('pipeline training gate: PASSED')


def validate_saved_artifact_reload(certificate_path):
    base = _expanded(load_certificate(certificate_path))
    with tempfile.TemporaryDirectory(prefix='clarity-reload-') as directory:
        root = Path(directory)
        def write_incomplete(certificate, path):
            changed = copy.deepcopy(certificate)
            changed['analysis']['properties'][0]['cases'][0]['progression'] = []
            changed['self_sha256'] = certificate_hash(changed)
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_text(json.dumps(changed))
        args = ['certify', '--mdp-dir',str(root/'mdp'),'--artifact-dir',str(root/'artifacts'),
                '--out-json',str(root/'summary.json'),base['model']['path']]
        with patch.object(sys,'argv',args), \
             patch.object(generation_cli,'build_certificate',return_value=base), \
             patch.object(generation_cli,'write_certificate',side_effect=write_incomplete):
            require(generation_cli.main() == 3, 'pipeline verified in-memory certificate instead of saved artifact')
        summary = json.loads((root/'summary.json').read_text())
        require(summary['result'] == 'NOT_CERTIFIED', 'incomplete saved artifact approved')
    print('saved certificate reload: PASSED')


if __name__ == '__main__':
    validate_completeness(sys.argv[1:])
