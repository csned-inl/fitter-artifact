"""Run one specified production proof obligation, without the pipeline."""
import argparse
import json
import time
from clarity.certification.strict_extract import extract_equation_model
from clarity.certification.relevance import compute_transition_closed_relevance
from clarity.certification.source_solver import _compiled_query, implementation_identity


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('model')
    parser.add_argument('--obligation', choices=['acyclic', 'closure', 'history'], required=True)
    parser.add_argument('--capture-query')
    args = parser.parse_args()
    started = time.monotonic()
    model = extract_equation_model(args.model)
    q = compute_transition_closed_relevance(model).q
    query = _compiled_query(json.dumps(model.execution), json.dumps(model.value_semantics, sort_keys=True),
                            tuple(sorted(q)), .1, json.dumps(implementation_identity(), sort_keys=True))
    constructed = time.monotonic()
    print(json.dumps({'stage': 'compiled', 'model': args.model, 'seconds': constructed-started,
                      'q': sorted(q), 'progress': query.progress_evidence}), flush=True)
    if args.capture_query:
        from pathlib import Path
        import z3
        from clarity.certification import lazy_solver
        original_check = lazy_solver.bounded_acyclic_check
        def capture(constraints, *positional, **keywords):
            target = Path(args.capture_query)
            target.parent.mkdir(parents=True, exist_ok=True)
            solver = z3.Solver()
            solver.add(*constraints)
            target.write_text(solver.to_smt2())
            print(json.dumps({'stage':'captured', 'path':str(target), 'bytes':target.stat().st_size}), flush=True)
            return original_check(constraints, *positional, **keywords)
        lazy_solver.bounded_acyclic_check = capture
    if args.obligation == 'acyclic':
        result = query.acyclic_projection(30000)
    elif args.obligation == 'closure':
        from clarity.certification.source_solver import check_source_transition_closure
        result = check_source_transition_closure(model, q, dt=.1, timeout_ms=30000)
    else:
        from clarity.certification.source_solver import check_source_history_reconstruction
        result = check_source_history_reconstruction(model, q, dt=.1, b_obs=2, b_act=4, timeout_ms=30000)
    print(json.dumps({'stage': 'result', 'model': args.model, 'obligation': args.obligation,
                      'result': result, 'obligation_seconds': time.monotonic()-constructed,
                      'total_seconds': time.monotonic()-started}), flush=True)
    return 0 if result['status'] in ('unsat', 'discharged') else 1


if __name__ == '__main__':
    raise SystemExit(main())
