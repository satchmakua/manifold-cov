"""M3 acceptance (part 1): coverage-directed generation reaches more coverage than
uniform random for the same number of scenarios, while keeping per-seed reproduction.
Both modes are deterministic given the base seed, so these comparisons are stable.
"""

from __future__ import annotations

from types import ModuleType

from manifold.closure import sweep
from manifold.generate import sample


def _build(spec: ModuleType):  # type: ignore[no-untyped-def]
    return lambda s: sample(spec.SPACE, s)


def test_directed_beats_random(toy: ModuleType, spec: ModuleType) -> None:
    build = _build(spec)
    ag = toy.AGENTS["toy.retry_forever"]
    directed = sweep(ag, build, spec.MODEL, spec.INVARIANTS, scenarios=10, coverage_directed=True)
    random_ = sweep(ag, build, spec.MODEL, spec.INVARIANTS, scenarios=10, coverage_directed=False)
    assert directed.db.pct() > random_.db.pct()
    # directed closes the full fault menu early; random hasn't yet at N=10
    assert directed.db.pct("fault_seen") == 100.0
    assert random_.db.pct("fault_seen") < 100.0


def test_directed_seeds_reproduce(toy: ModuleType, spec: ModuleType) -> None:
    directed = sweep(
        toy.AGENTS["toy.retry_forever"], _build(spec), spec.MODEL, spec.INVARIANTS,
        scenarios=8, coverage_directed=True,
    )
    seed = next(iter(directed.results_by_seed))
    a = sample(spec.SPACE, seed)
    b = sample(spec.SPACE, seed)
    assert a.scenario_id() == b.scenario_id()  # a directed seed is still a pure handle


def test_directed_is_deterministic(toy: ModuleType, spec: ModuleType) -> None:
    build = _build(spec)
    ag = toy.AGENTS["toy.retry_forever"]
    a = sweep(ag, build, spec.MODEL, spec.INVARIANTS, scenarios=12, coverage_directed=True)
    b = sweep(ag, build, spec.MODEL, spec.INVARIANTS, scenarios=12, coverage_directed=True)
    assert a.db.pct() == b.db.pct()
    assert set(a.results_by_seed) == set(b.results_by_seed)
