"""Scheduling regressions independent of proof outcomes."""
import threading
import unittest
from run_workstation_validation import run_batch


class Batch(unittest.TestCase):
    def test_independent_jobs_overlap(self):
        barrier = threading.Barrier(2)
        def execute(row):
            barrier.wait(timeout=3)
            return row['name']
        rows = [{'name': name, 'memory_gb': 2} for name in ('a', 'b')]
        self.assertEqual(set(run_batch(rows, execute, workers=2, memory_gb=4)), {'a', 'b'})

    def test_reservations_prevent_overcommit(self):
        lock = threading.Lock()
        active = 0
        peak = 0
        def execute(row):
            nonlocal active, peak
            with lock:
                active += row['memory_gb']
                peak = max(peak, active)
            threading.Event().wait(.02)
            with lock:
                active -= row['memory_gb']
            return row['name']
        rows = [{'name': str(i), 'memory_gb': 3} for i in range(5)]
        self.assertEqual(len(run_batch(rows, execute, workers=4, memory_gb=5)), 5)
        self.assertEqual(peak, 3)

    def test_failures_are_returned_not_hidden(self):
        rows = [{'name': 'failing', 'memory_gb': 1}]
        result = run_batch(rows, lambda row: {'exit_code': 7}, workers=1, memory_gb=1)
        self.assertEqual(result, [{'exit_code': 7}])
        with self.assertRaisesRegex(RuntimeError, 'worker failure'):
            run_batch(rows, lambda row: (_ for _ in ()).throw(RuntimeError('worker failure')),
                      workers=1, memory_gb=1)

    def test_impossible_reservation_fails_without_launch(self):
        with self.assertRaises(ValueError):
            run_batch([{'memory_gb': 65}], lambda row: self.fail('launched'), workers=32, memory_gb=60)


if __name__ == '__main__':
    unittest.main()
