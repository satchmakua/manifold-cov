"""H4 acceptance: every declared coverpoint is reachable. Latency faults advance the
simulated clock (so `timeout` is reachable), a small cost budget makes `budget_cost`
reachable, and `ignore` bins are excluded from the denominator. On the research example
a directed sweep reaches 100% — i.e. no declared bin is structurally unreachable.
"""

from __future__ import annotations

from dataclasses import replace
from types import ModuleType
from typing import Any

from manifold.agent import ToolEnv
from manifold.closure import sweep
from manifold.coverage import empty_db, project_bins
from manifold.generate import sample
from manifold.harness import run
from manifold.model import CoverageModel, terminal_reason_cp
from manifold.scenario import Budgets, Fault, Scenario, ToolMock
from manifold.trace import Terminal


class _Caller:
    """A trivial agent that calls one tool a fixed number of times."""

    id = "test.caller"

    def __init__(self, tool: str = "search", calls: int = 5) -> None:
        self._tool = tool
        self._calls = calls

    def run(self, task: Any, env: ToolEnv, budget: Budgets) -> Any:
        for _ in range(self._calls):
            env.call(self._tool, q="x")
        return "done"


def test_latency_faults_make_timeout_reachable() -> None:
    scn = Scenario(
        seed=1,
        task="t",
        mocks=[
            ToolMock(
                tool="search",
                responses=["r"],
                faults=[Fault(at_call=-1, kind="latency", detail=100)],
            )
        ],
        budgets=Budgets(max_steps=10, wall_ms=150),
    )
    trace = run(_Caller("search", calls=5), scn)
    assert isinstance(trace.terminal, Terminal)
    assert trace.terminal.reason == "timeout"  # two 100ms calls exceed the 150ms wall budget


def test_small_cost_budget_makes_budget_cost_reachable() -> None:
    scn = Scenario(
        seed=1,
        task="t",
        mocks=[ToolMock(tool="search", responses=["r"])],
        budgets=Budgets(max_steps=10, max_cost=3.0),
    )
    trace = run(_Caller("search", calls=8), scn)
    assert isinstance(trace.terminal, Terminal)
    assert trace.terminal.reason == "budget_cost"


def test_ignore_bins_excluded_from_denominator() -> None:
    cp = replace(terminal_reason_cp(), ignore=frozenset({"error", "budget_cost"}))
    db = empty_db(CoverageModel(coverpoints=[cp]))
    assert set(db.totals["terminal_reason"]) == {"completed", "budget_steps", "timeout"}


def test_project_bins_targets_fault_by_tool(research: ModuleType) -> None:
    scn = Scenario(
        seed=0,
        task="t",
        mocks=[
            ToolMock(tool="search", faults=[Fault(at_call=-1, kind="garbage")]),
            ToolMock(tool="fetch", faults=[Fault(at_call=0, kind="timeout")]),
        ],
    )
    proj = project_bins(research.MODEL, scn)
    assert ("fault_by_tool", "search:garbage") in proj
    assert ("fault_by_tool", "fetch:timeout") in proj
    assert ("fault_seen", "garbage") in proj


def test_one_tool_call_run_is_reachable(research: ModuleType) -> None:
    # Regression (adversarial review): bin "1" was wrongly marked unreachable. A persistent
    # latency fault on the first tool trips the tight wall budget after one completed call.
    for seed in range(500):
        trace = run(research.AGENTS["research.pipeline"], sample(research.SPACE, seed))
        if len(trace.tools_called()) == 1:
            assert isinstance(trace.terminal, Terminal)
            assert trace.terminal.reason == "timeout"
            return
    raise AssertionError("expected some seed in range(500) to yield a 1-tool-call run")


def test_terminal_projection_lets_directed_target_timeout(research: ModuleType) -> None:
    # Regression: terminal_reason:timeout must be *targetable* (was project=None → luck only).
    scn = Scenario(
        seed=0,
        task="t",
        mocks=[ToolMock(tool="search", faults=[Fault(at_call=-1, kind="latency", detail=500)])],
        budgets=Budgets(max_steps=15, max_cost=100.0, wall_ms=150),
    )
    assert ("terminal_reason", "timeout") in project_bins(research.MODEL, scn)


def test_research_model_has_no_unreachable_coverpoints(research: ModuleType) -> None:
    result = sweep(
        research.AGENTS["research.pipeline"],
        lambda s: sample(research.SPACE, s),
        research.MODEL,
        research.INVARIANTS,
        scenarios=80,
        coverage_directed=True,
    )
    assert result.db.pct() == 100.0  # every declared, non-ignored bin is actually reachable
    assert result.db.holes() == []
