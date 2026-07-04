"""H1 acceptance: coverage-directed generation *decisively* beats random on the rich
multi-tool example — reaching both 90% of achievable coverage and *full* coverage in far
fewer scenarios by targeting untested behaviors. Both modes reach 100% eventually (the
example is fair, not rigged); directed just gets there much sooner. Deterministic given
the base seed, so these comparisons are stable.
"""

from __future__ import annotations

from collections.abc import Sequence
from types import ModuleType

from manifold.closure import sweep
from manifold.generate import sample


def _reach(trajectory: Sequence[float], target: float) -> int | None:
    return next((i + 1 for i, p in enumerate(trajectory) if p >= target), None)


def test_directed_decisively_beats_random(research: ModuleType) -> None:
    build = lambda s: sample(research.SPACE, s)  # noqa: E731
    ag = research.AGENTS["research.pipeline"]
    directed = sweep(
        ag, build, research.MODEL, research.INVARIANTS, scenarios=300, coverage_directed=True
    )
    random_ = sweep(ag, build, research.MODEL, research.INVARIANTS, scenarios=300)

    # both reach full coverage — the example is fair (random isn't structurally capped)
    assert directed.db.pct() == 100.0
    assert random_.db.pct() == 100.0

    # directed reaches FULL coverage in far fewer scenarios, by targeting untested behaviors
    d_full = _reach(directed.trajectory, 99.99)
    r_full = _reach(random_.trajectory, 99.99)
    assert d_full is not None and r_full is not None
    assert r_full >= 3 * d_full  # measured ~16x at the default seed (11 vs 217)

    # ...and 90% of achievable in <= half the scenarios random needs (measured ~4x, seed 0)
    d90 = _reach(directed.trajectory, 90.0)
    r90 = _reach(random_.trajectory, 90.0)
    assert d90 is not None and r90 is not None
    assert r90 >= 2 * d90


def test_trajectory_is_monotonic_and_sized(research: ModuleType) -> None:
    result = sweep(
        research.AGENTS["research.pipeline"],
        lambda s: sample(research.SPACE, s),
        research.MODEL,
        research.INVARIANTS,
        scenarios=15,
    )
    assert len(result.trajectory) == 15
    assert result.trajectory == sorted(result.trajectory)  # coverage never decreases


def test_stubborn_agent_is_flagged(research: ModuleType) -> None:
    # the planted-bug variant blows a budget and is caught
    result = sweep(
        research.AGENTS["research.stubborn"],
        lambda s: sample(research.SPACE, s),
        research.MODEL,
        research.INVARIANTS,
        scenarios=40,
    )
    assert result.failures  # at least one invariant violation surfaced
