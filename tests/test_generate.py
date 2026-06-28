"""M2 acceptance tests: the generator is a pure function of the seed, exercises the
full fault menu (persistent and transient), and covers strictly more of the behavior
space than the M0 hand-built scenarios; the sweep records per-seed flakiness without
false positives on deterministic agents.
"""

from __future__ import annotations

from types import ModuleType

from manifold.closure import SweepResult, sweep
from manifold.coverage import CoverageDB
from manifold.generate import sample


def test_sample_is_pure(spec: ModuleType) -> None:
    a = sample(spec.SPACE, 7)
    b = sample(spec.SPACE, 7)
    assert a.scenario_id() == b.scenario_id()
    assert a.model_dump() == b.model_dump()


def test_distinct_seeds_usually_differ(spec: ModuleType) -> None:
    ids = {sample(spec.SPACE, s).scenario_id() for s in range(30)}
    assert len(ids) > 15  # the generator actually varies the stimulus


def test_full_fault_menu_is_exercised(spec: ModuleType) -> None:
    kinds: set[str] = set()
    persistent = transient = False
    for s in range(200):
        for mock in sample(spec.SPACE, s).mocks:
            for f in mock.faults:
                kinds.add(f.kind)
                persistent |= f.at_call == -1
                transient |= f.at_call == 0
    assert kinds == {"error", "timeout", "garbage", "latency"}
    assert persistent and transient  # both outage shapes appear


def test_generator_closes_the_recovery_hole(toy: ModuleType, spec: ModuleType) -> None:
    result = sweep(
        toy.AGENTS["toy.retry_forever"],
        lambda s: sample(spec.SPACE, s),
        spec.MODEL,
        spec.INVARIANTS,
        scenarios=150,
    )
    holes = set(result.db.holes())
    # The transient faults the generator injects now exercise recovery-after-failure,
    # the edge the M0 scenarios never reached (see test_coverage.py):
    assert ("tool_fsm", "search:err->search:ok") not in holes
    for kind in ("error", "timeout", "garbage", "latency"):
        assert ("fault_seen", kind) not in holes


def test_generator_beats_hand_built_coverage(toy: ModuleType, spec: ModuleType) -> None:
    gen = sweep(
        toy.AGENTS["toy.retry_forever"],
        lambda s: sample(spec.SPACE, s),
        spec.MODEL,
        spec.INVARIANTS,
        scenarios=120,
    )
    m0 = sweep(
        toy.AGENTS["toy.retry_forever"],
        toy.make_scenario,
        spec.MODEL,
        spec.INVARIANTS,
        scenarios=120,
    )
    assert gen.db.pct() > m0.db.pct()


def test_flaky_detection_synthetic() -> None:
    result = SweepResult(
        db=CoverageDB(),
        failures=[],
        results_by_seed={1: [True, True, True], 2: [True, False, True], 3: [False, False, False]},
    )
    assert result.flaky_seeds() == [2]
    assert result.fully_passing() == 1  # only seed 1 passes every repeat


def test_deterministic_agent_is_not_flaky(toy: ModuleType, spec: ModuleType) -> None:
    result = sweep(
        toy.AGENTS["toy.retry_forever"],
        lambda s: sample(spec.SPACE, s),
        spec.MODEL,
        spec.INVARIANTS,
        scenarios=40,
        repeats=3,
    )
    assert result.flaky_seeds() == []  # deterministic agent → no false flakiness
