"""Enforce the configured Z3 query timeout, including unresponsive preprocessing."""
import multiprocessing
import time

import z3


def bounded_query(fixedpoint, predicate, timeout_ms):
    """Run the existing exact query in an isolated Unix worker process.

    Z3's internal timeout does not interrupt all FP/Horn preprocessing. A fork
    retains the shared AST without serializing or expanding it. Terminating
    this process returns UNKNOWN; it never supplies a proof or a counterexample.
    """
    return _bounded_result(fixedpoint, lambda: fixedpoint.query(predicate), timeout_ms)


def bounded_check(solver, timeout_ms):
    """Apply the same deadline to acyclic SMT preprocessing and solving."""
    return _bounded_result(solver, solver.check, timeout_ms)


def bounded_acyclic_check(constraints, timeout_ms, *, prefix, include_artifacts=False):
    """Eliminate definitional SSA equations before abstracting arithmetic.

    Z3's standard preprocessing preserves satisfiability. Arithmetic abstraction
    then overapproximates the remaining formula. UNSAT proves the original
    query, whereas SAT is retried with the exact preprocessed formula. The one
    external deadline covers preprocessing, abstraction and both solver calls.
    """
    def execute(report):
        from .lazy_congruence import abstract_numeric
        from .lazy_substitution import propagate_asserted_equalities
        started = time.monotonic()
        def remaining():
            return max(1, timeout_ms - int(1000*(time.monotonic()-started)))
        report('equality_propagation')
        propagated = propagate_asserted_equalities(constraints)
        report('ssa_preprocessing')
        goal = z3.Goal()
        goal.add(*propagated)
        reduced = z3.Then('simplify', 'propagate-values', 'solve-eqs', 'simplify')(goal)
        if len(reduced) != 1:
            return {'status': 'unknown', 'reason': 'unexpected_preprocessing_subgoals'}
        exact_formula = z3.And(*propagate_asserted_equalities([reduced[0].as_expr()]))
        report('numeric_abstraction')
        abstracted, operators = abstract_numeric([exact_formula], prefix)
        solver = z3.Solver()
        solver.set(timeout=remaining())
        solver.add(*abstracted)
        report('abstract_solver')
        result = solver.check()
        abstract_status = str(result)
        refined = False
        if result == z3.sat:
            refined = True
            report('exact_solver')
            solver = z3.Solver()
            solver.set(timeout=remaining())
            solver.add(exact_formula)
            result = solver.check()
        evidence = {'status': str(result), 'abstract_status': abstract_status,
                    'exact_refinement_attempted': refined, 'operators': operators,
                    'queries': 2 if refined else 1,
                    'reason': solver.reason_unknown() if result == z3.unknown else None}
        if include_artifacts:
            evidence['smt2'] = solver.to_smt2()
        return evidence
    return _bounded_operation(execute, timeout_ms)


def _bounded_result(solver, check, timeout_ms):
    def execute(report):
        report('solver')
        solver.set(timeout=timeout_ms)
        result = check()
        return {'status': str(result), 'reason': solver.reason_unknown()
                if result == z3.unknown else None}
    return _bounded_operation(execute, timeout_ms)


def _bounded_operation(operation, timeout_ms):
    if timeout_ms <= 0:
        raise ValueError('the source solver requires a positive configured timeout')
    context = multiprocessing.get_context('fork')
    receiver, sender = context.Pipe(duplex=False)

    def execute():
        receiver.close()
        try:
            def report(stage):
                sender.send({'progress': stage})
            sender.send({'result': operation(report)})
        except Exception as exc:
            sender.send({'result': {'status': 'unknown', 'reason': 'solver_timeout' if 'canceled' in str(exc)
                         else 'source_solver_error', 'error': type(exc).__name__ + ': ' + str(exc)}})
        finally:
            sender.close()

    started = time.monotonic()
    process = context.Process(target=execute)
    process.start()
    sender.close()
    last_stage = 'worker_start'
    try:
        while True:
            remaining = timeout_ms/1000 - (time.monotonic()-started)
            if remaining <= 0 or not receiver.poll(remaining):
                return {'status': 'unknown', 'reason': 'solver_timeout',
                        'timeout_enforcement': 'external_process', 'stage': last_stage}
            try:
                message = receiver.recv()
            except EOFError:
                return {'status': 'unknown', 'reason': 'solver_process_exited_without_result',
                        'stage': last_stage}
            if 'result' in message:
                return dict(message['result'], stage=last_stage)
            last_stage = message['progress']
    finally:
        receiver.close()
        if process.is_alive():
            process.terminate()
        process.join(.1)
        if process.is_alive():
            process.kill()
            process.join()
