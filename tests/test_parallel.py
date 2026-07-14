"""W2: parallel sweep execution. The load-bearing claim is *identity*: a parallel sweep
must produce byte-identical results (coverage DB, failures, per-seed flags, trajectory)
to a sequential one — parallelism changes wall-clock, never outcomes (ADR-0002).
"""

from __future__ import annotations

import threading
import time
from types import ModuleType
from typing import Any

from manifold.closure import SweepResult, sweep
from manifold.generate import sample


def _run(research: ModuleType, agent_id: str, *, parallel: int, **kw: Any) -> SweepResult:
    return sweep(
        research.AGENTS[agent_id],
        lambda s: sample(research.SPACE, s),
        research.MODEL,
        research.INVARIANTS,
        parallel=parallel,
        **kw,
    )


def _assert_identical(a: SweepResult, b: SweepResult) -> None:
    assert a.db.hits == b.db.hits
    assert a.db.totals == b.db.totals
    assert a.trajectory == b.trajectory
    assert a.results_by_seed == b.results_by_seed
    assert {(v.invariant, v.seed, v.detail) for v in a.failures} == {
        (v.invariant, v.seed, v.detail) for v in b.failures
    }


def test_parallel_random_sweep_is_identical_to_sequential(research: ModuleType) -> None:
    seq = _run(research, "research.pipeline", parallel=1, scenarios=12, repeats=2)
    par = _run(research, "research.pipeline", parallel=4, scenarios=12, repeats=2)
    _assert_identical(seq, par)


def test_parallel_sweep_identical_on_a_failing_agent(research: ModuleType) -> None:
    # Failures (dedupe by invariant x seed) must also merge identically.
    seq = _run(research, "research.stubborn", parallel=1, scenarios=8)
    par = _run(research, "research.stubborn", parallel=4, scenarios=8)
    assert seq.failures  # sanity: the stubborn agent actually fails
    _assert_identical(seq, par)


def test_parallel_directed_sweep_is_identical_to_sequential(research: ModuleType) -> None:
    # Directed mode parallelizes only repeats; selection stays sequential -> same seeds.
    seq = _run(
        research, "research.pipeline", parallel=1, scenarios=6, repeats=2, coverage_directed=True
    )
    par = _run(
        research, "research.pipeline", parallel=4, scenarios=6, repeats=2, coverage_directed=True
    )
    _assert_identical(seq, par)


def test_parallel_error_cancels_pending_runs_not_the_whole_queue() -> None:
    # Regression (adversarial review): when consuming a result raises mid-sweep (here a
    # throwing invariant, evaluated in the main loop), still-queued runs must be CANCELLED,
    # not drained — live, draining is real API spend. 50 scenarios, 2 workers, a slow agent,
    # and an invariant that throws on the first trace => far fewer than 50 runs execute.
    import pytest

    from manifold.model import CoverageModel, tool_called_cp
    from manifold.scenario import Budgets, Scenario, ToolMock
    from manifold.trace import Trace

    started = {"n": 0}
    lock = threading.Lock()

    class _Slow:
        id = "test.slow"

        def run(self, task: Any, env: Any, budget: Budgets) -> Any:
            with lock:
                started["n"] += 1
            time.sleep(0.02)
            return "ok"

    def boom_invariant(trace: Trace) -> Any:
        raise RuntimeError("boom")  # thrown while consuming the first result

    def build(seed: int) -> Scenario:
        return Scenario(seed=seed, task="t", mocks=[ToolMock(tool="search", responses=["r"])])

    model = CoverageModel(coverpoints=[tool_called_cp(["search"])])
    with pytest.raises(RuntimeError, match="boom"):
        sweep(_Slow(), build, model, [boom_invariant], scenarios=50, parallel=2)
    assert started["n"] < 50  # pending runs were cancelled, not drained


def test_parallel_actually_runs_concurrently() -> None:
    # An agent whose run() blocks proves overlap: 8 scenarios x 50ms serially is >=400ms;
    # with 8 workers the whole sweep should finish in well under half that.
    from manifold.model import CoverageModel, tool_called_cp
    from manifold.scenario import Budgets, Scenario, ToolMock

    peak = {"n": 0, "cur": 0}
    lock = threading.Lock()

    class _SlowAgent:
        id = "test.slow"

        def run(self, task: Any, env: Any, budget: Budgets) -> Any:
            with lock:
                peak["cur"] += 1
                peak["n"] = max(peak["n"], peak["cur"])
            time.sleep(0.05)
            env.call("search", q="x")
            with lock:
                peak["cur"] -= 1
            return "done"

    def build(seed: int) -> Scenario:
        return Scenario(seed=seed, task="t", mocks=[ToolMock(tool="search", responses=["r"])])

    model = CoverageModel(coverpoints=[tool_called_cp(["search"])])
    start = time.perf_counter()
    result = sweep(_SlowAgent(), build, model, [], scenarios=8, parallel=8)
    elapsed = time.perf_counter() - start
    assert result.n_scenarios == 8
    assert peak["n"] >= 2  # overlap actually happened
    assert elapsed < 0.35  # 8 x 50ms serial would be >= 0.4s
