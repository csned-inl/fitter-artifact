"""Synchronized source execution with explicit decision, terminal and error results."""

from __future__ import annotations

from dataclasses import dataclass
import threading

from clarity.runtime.requirements import RequirementLedger
from clarity.sysml.parser import SysMLParser
from clarity.sysml.runtime_settings import validate_dt
from clarity.sysml.simulator import SimulationEngine


@dataclass(frozen=True)
class AdvanceResult:
    state: dict | None
    outcome: str
    events: tuple
    error: str | None = None


class SimulatorTwin:
    def __init__(self, model_path: str, dt: float):
        self._model_path = model_path
        self._dt = validate_dt(dt)
        self._parser = SysMLParser(model_path)
        self._parser.parse()
        self._engine = None
        self._sim_thread = None
        self._model_called = threading.Event()
        self._action_ready = threading.Event()
        self._sim_done = threading.Event()
        self._model_inputs = {}
        self._action = {}
        self._response = None
        self._episode_id = 0
        neural = [a for p in self._parser.part_defs.values() for a in p.action_defs
                  if 'Neural' in a.metadata]
        if len(neural) != 1:
            raise ValueError('execution requires exactly one Neural action')
        self._input_names = {p.name for p in neural[0].in_params}
        completion = [p.name for p in neural[0].in_params if 'Completion' in p.metadata]
        if len(completion) != 1:
            raise ValueError('execution requires one Completion input')
        self._completion_name = completion[0]

    def _publish(self, outcome, state=None, error=None):
        self._response = AdvanceResult(
            None if state is None else dict(state), outcome,
            self.engine.requirement_ledger.drain(), error,
        )
        self._model_called.set()

    def _model_fn(self, inputs):
        if set(inputs) != self._input_names or any(x is None for x in inputs.values()):
            raise ValueError('missing, extra or undefined source Neural input')
        if type(inputs[self._completion_name]) is not bool:
            raise ValueError('source Completion input is not Boolean')
        self._model_inputs = dict(inputs)
        # All source safety properties are checked together at cycle_end.
        # Completion must likewise wait for this final response to be applied.
        self._terminal_after_response = inputs[self._completion_name]
        self._publish('decision', inputs)
        self._action_ready.wait()
        self._action_ready.clear()
        if self._sim_done.is_set():
            raise _SimulationStopped
        return dict(self._action)

    def _run_simulation(self):
        try:
            while not self._sim_done.is_set():
                self.engine.step(self._dt)
                if self._terminal_after_response:
                    self._publish('terminal', self._model_inputs)
                    break
        except _SimulationStopped:
            pass
        except Exception as exc:
            self._publish('error', error=f'{type(exc).__name__}: {exc}')
        finally:
            self._sim_done.set()
            if self._response is None:
                self._publish('terminal')

    def _stop(self):
        if self._sim_thread is not None and self._sim_thread.is_alive():
            self._sim_done.set()
            self._action_ready.set()
            self._sim_thread.join(timeout=2.0)
            if self._sim_thread.is_alive():
                raise RuntimeError('previous simulator did not stop; reset refused')
        self._sim_thread = None

    def prepare(self, overrides=None):
        """Install scenario/initial values before any sample, input or action."""
        self._stop()
        self._episode_id += 1
        self._model_called.clear()
        self._action_ready.clear()
        self._sim_done.clear()
        self._model_inputs = {}
        self._response = None
        self._terminal_after_response = False
        self._engine = SimulationEngine(self._parser)
        self._engine.model = self._model_fn
        self._engine.requirement_ledger = RequirementLedger(self._episode_id)
        try:
            self._engine.initialize(overrides=overrides)
            self._engine.record_requirements('initialization')
        except Exception as exc:
            self._publish('error', error=f'{type(exc).__name__}: {exc}')
            self._sim_done.set()

    def start(self):
        if self._engine is None:
            raise RuntimeError('prepare must precede start')
        if self._response is not None:
            return self._response
        self._sim_thread = threading.Thread(target=self._run_simulation, daemon=True)
        self._sim_thread.start()
        return self._wait_response()

    def _wait_response(self):
        self._model_called.wait()
        self._model_called.clear()
        if self._response is None:
            raise RuntimeError('simulator woke caller without a response')
        return self._response

    def advance(self, action):
        if self._response is None or self._response.outcome != 'decision':
            raise RuntimeError('action requires a pending source decision')
        self._response = None
        self._action = dict(action)
        self._action_ready.set()
        return self._wait_response()

    def __call__(self, action=None):
        if action is None:
            self.prepare()
            result = self.start()
        else:
            result = self.advance(action)
        if result.error:
            raise RuntimeError(result.error)
        if result.state is None:
            raise RuntimeError(f'no source observation: {result.outcome}')
        return dict(result.state)

    @property
    def parser(self):
        return self._parser

    @property
    def engine(self):
        if self._engine is None:
            raise RuntimeError('simulator has not been initialized')
        return self._engine

    @property
    def model_inputs(self):
        return dict(self._model_inputs)

    @property
    def state_value_pairs(self):
        return self.engine.state_value_pairs()

    @property
    def dt(self):
        return self._dt

    def stop(self):
        self._stop()


class _SimulationStopped(Exception):
    pass
