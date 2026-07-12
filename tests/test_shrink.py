"""M4 acceptance: the scenario shrinker (delta-debug minimizer). A bloated failing
scenario reduces to a minimal reproducer that (a) still fails the *same* invariants,
(b) is strictly smaller, (c) is reached deterministically, and (d) drops the parts that
don't cause the bug. Also unit-tests the ddmin core's 1-minimality guarantee.
"""

from __future__ import annotations

from types import ModuleType

from manifold.harness import run
from manifold.invariants import Invariant
from manifold.scenario import Budgets, Fault, Scenario, ToolMock
from manifold.shrink import _ddmin, _size, shrink


def _bloated() -> Scenario:
    """A padded failing scenario for research.stubborn: two irrelevant mocks (a clean
    search, a fetch with a non-fatal latency blip) plus the real cause — a persistent
    `summarize` error the stubborn agent retries forever — and generous budgets + a task."""
    return Scenario(
        seed=1,
        task="stock price AAPL",
        mocks=[
            ToolMock(tool="search", responses=["a", "b", "c"]),
            ToolMock(tool="fetch", faults=[Fault(at_call=0, kind="latency", detail=10)]),
            ToolMock(tool="summarize", faults=[Fault(at_call=-1, kind="error")]),
        ],
        budgets=Budgets(max_steps=15, max_cost=100.0, wall_ms=5000),
    )


def _failing(agent: object, scn: Scenario, invariants: list[Invariant]) -> set[str]:
    trace = run(agent, scn)  # type: ignore[arg-type]
    return {v.invariant for inv in invariants if (v := inv(trace)) is not None}


def test_minimal_still_fails_the_same_invariants(research: ModuleType) -> None:
    agent = research.AGENTS["research.stubborn"]
    original_fails = _failing(agent, _bloated(), research.INVARIANTS)
    assert original_fails  # sanity: the bloated scenario really fails

    result = shrink(agent, _bloated(), research.INVARIANTS)
    assert set(result.preserved) == original_fails
    # the minimal reproducer, run fresh, still fails exactly the preserved invariants
    assert _failing(agent, result.minimal, research.INVARIANTS) >= original_fails


def test_minimal_is_strictly_smaller(research: ModuleType) -> None:
    result = shrink(research.AGENTS["research.stubborn"], _bloated(), research.INVARIANTS)
    assert result.minimal_size < result.original_size
    assert result.reduction_pct > 0
    assert result.evaluations > 0  # it actually did work


def test_shrink_drops_the_irrelevant_mocks(research: ModuleType) -> None:
    # The bug is the persistent `summarize` error; the clean search and the latency-blip
    # fetch are not load-bearing and should be gone from the minimal reproducer.
    result = shrink(research.AGENTS["research.stubborn"], _bloated(), research.INVARIANTS)
    tools = {m.tool for m in result.minimal.mocks}
    assert "summarize" in tools
    assert "search" not in tools and "fetch" not in tools


def test_shrink_is_deterministic(research: ModuleType) -> None:
    # No wall-clock, no unseeded RNG: shrinking a deterministic agent is reproducible.
    agent = research.AGENTS["research.stubborn"]
    a = shrink(agent, _bloated(), research.INVARIANTS)
    b = shrink(agent, _bloated(), research.INVARIANTS)
    assert a.minimal.model_dump() == b.minimal.model_dump()
    assert a.evaluations == b.evaluations


def test_passing_scenario_is_returned_unchanged(research: ModuleType) -> None:
    # A scenario that fails nothing has nothing to shrink.
    clean = Scenario(
        seed=1, task="q",
        mocks=[ToolMock(tool="search", responses=["ok"])],
        budgets=Budgets(max_steps=30),
    )
    result = shrink(research.AGENTS["research.pipeline"], clean, research.INVARIANTS)
    assert result.preserved == []
    assert result.minimal_size == result.original_size
    assert result.evaluations == 0


def test_target_narrows_the_preserved_failure(research: ModuleType) -> None:
    # Preserving only terminates_within_budget lets the budget shrink further than when
    # no_infinite_retry (which needs >3 retries) must also hold.
    agent = research.AGENTS["research.stubborn"]
    both = shrink(agent, _bloated(), research.INVARIANTS)
    budget_only = shrink(
        agent, _bloated(), research.INVARIANTS, target=["terminates_within_budget"]
    )
    assert budget_only.preserved == ["terminates_within_budget"]
    # fewer constraints -> the step budget can go at least as low
    assert budget_only.minimal.budgets.max_steps <= both.minimal.budgets.max_steps


def test_ddmin_core_is_one_minimal() -> None:
    # The failure needs BOTH atoms 2 and 4 present; ddmin must return a 1-minimal set
    # (no single element removable) that still satisfies the predicate.
    def needs_2_and_4(subset: list[int]) -> bool:
        return 2 in subset and 4 in subset

    atoms = [0, 1, 2, 3, 4, 5, 6, 7]
    result = _ddmin(atoms, needs_2_and_4)
    assert needs_2_and_4(result)
    for a in result:  # 1-minimality: removing any single atom breaks the failure
        assert not needs_2_and_4([x for x in result if x != a])


def test_size_metric_counts_the_moving_parts() -> None:
    scn = Scenario(
        seed=1, task="q",
        mocks=[ToolMock(tool="t", responses=["a", "b"], faults=[Fault(at_call=-1, kind="error")])],
    )
    # 1 mock + 1 fault + 2 responses + 1 task = 5
    assert _size(scn) == 5
