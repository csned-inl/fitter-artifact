"""Repeat the unchanged saved-policy evaluation while comparing source equations.

Both original replay passes still run with their original seeds, weights, buffers,
episode counts and controller actions. The equation evaluator only observes.
"""
from __future__ import annotations
import argparse
from collections import Counter
import contextlib
import io
import json
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'certification'))
from validate_execution_equations import compare_configuration
from validate_value_replay import replay
from clarity.certification.execution_equations import ExecutionEquations
from clarity.certification.strict_extract import extract_equation_model
from clarity.sysml.simulator_adapter import SimulatorTwin


def validate(saved, output):
    original_prepare,original_start,original_advance=(SimulatorTwin.prepare,SimulatorTwin.start,SimulatorTwin.advance)
    models={}; counts=Counter(); configurations={}; overrides={}
    def equations(twin):
        key=(twin._model_path,twin.dt)
        if key not in models:
            with contextlib.redirect_stdout(io.StringIO()):
                source=extract_equation_model(twin._model_path)
            models[key]=ExecutionEquations(source.execution,twin.dt)
        return models[key]
    def prepare(twin,overrides=None):
        # Store exactly what the existing scenario generator passed to runtime.
        scenario=dict(overrides or {})
        setattr(twin,'_equation_test_scenario',scenario)
        return original_prepare(twin,overrides)
    def start(twin):
        result=original_start(twin)
        source=equations(twin)
        current=source.advance(overrides=twin._equation_test_scenario)
        compare_configuration(source,current,twin,result)
        configurations[twin]=current
        counts[Path(twin._model_path).parent.name+'/initializations']+=1
        return result
    def advance(twin,action):
        result=original_advance(twin,action)
        source=equations(twin)
        current=source.advance(configurations[twin],action=action)
        compare_configuration(source,current,twin,result)
        configurations[twin]=current
        counts[Path(twin._model_path).parent.name+'/responses']+=1
        return result
    with patch.object(SimulatorTwin,'prepare',prepare),patch.object(SimulatorTwin,'start',start),patch.object(SimulatorTwin,'advance',advance):
        replay(saved,output)
    (output/'source-equation-comparison.json').write_text(json.dumps({'mismatches':0,'compared':dict(counts)},indent=2)+'\n')

if __name__=='__main__':
    ap=argparse.ArgumentParser()
    ap.add_argument('--saved-runs',type=Path,required=True)
    ap.add_argument('--out-dir',type=Path,required=True)
    args=ap.parse_args()
    validate(args.saved_runs,args.out_dir)
