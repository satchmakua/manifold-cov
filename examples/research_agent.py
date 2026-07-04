"""A richer, multi-tool example — the target for the coverage-directed vs random demo.

A `research.pipeline` agent calls three tools in sequence (search -> fetch -> summarize),
each of which the generator can fault independently. That makes the behavior space large
enough to show coverage-directed generation *decisively* beating random: the
``fault_by_tool`` coverpoint has 12 input-derivable cells (3 tools x 4 fault kinds), so
uniform random has a long coupon-collector tail while directed — which scores candidates
by how many unhit reachable bins they fill — closes them fast. Run the curve with:

    manifold curve examples/research_agent.py --max-scenarios 120 --svg docs/coverage_curve.svg

Every declared coverpoint bin is reachable on this agent (H4). Only bins the agent truly
can't produce are marked ``ignore``: 0 tool calls (it always calls at least one), 9+ tool
calls (it caps at 3 tools x 2 attempts = 6), and an `error` terminal (the pipeline handles
`ToolError` and never crashes). A **1**-tool-call run *is* reachable — a persistent latency
fault on the first tool trips the tight wall budget after one completed call — so bin "1"
is kept in the denominator. `terminal_reason` and `n_tool_calls` carry input-derived
``project`` hooks so coverage-directed generation steers toward the budget/latency behaviors
too, not just the fault kinds.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from manifold.agent import ToolEnv
from manifold.generate import ScenarioSpace, ToolSpec
from manifold.harness import ToolError
from manifold.invariants import no_infinite_retry, terminates_within_budget
from manifold.model import (
    CoverageModel,
    fault_by_tool_cp,
    fault_seen_cp,
    n_tool_calls_cp,
    terminal_reason_cp,
    tool_called_cp,
)
from manifold.scenario import Budgets, Scenario

TOOLS = ["search", "fetch", "summarize"]
_MAX_CALLS = len(TOOLS) * 2  # the pipeline agent's call ceiling (one retry per tool)


def _terminal_projection(scn: Scenario) -> set[str]:
    """Predict, from a scenario's budgets + faults alone, which terminal reasons it can
    reach — so the directed selector steers toward budget/latency behaviors. Approximate
    by design (it's input-derived, not a run), but enough to guide generation."""
    b = scn.budgets
    kinds = {f.kind for m in scn.mocks for f in m.faults}
    retry_faults = bool(kinds & {"error", "timeout"})  # these cause retries -> more calls
    reasons = {"completed"}
    if retry_faults and b.max_steps <= _MAX_CALLS:
        reasons.add("budget_steps")
    if retry_faults and b.max_cost <= _MAX_CALLS:
        reasons.add("budget_cost")
    if "latency" in kinds and b.wall_ms <= 1200:
        reasons.add("timeout")
    return reasons


def _ncalls_projection(scn: Scenario) -> set[str]:
    """Predict reachable tool-call-count bins from inputs. A persistent latency fault on
    the first tool + a tight wall budget ends the run after a single call (bin '1')."""
    b = scn.budgets
    kinds = {f.kind for m in scn.mocks for f in m.faults}
    first = scn.mocks[0] if scn.mocks else None
    first_latency = bool(
        first and any(f.kind == "latency" and f.at_call == -1 for f in first.faults)
    )
    reachable = {"2-3"}  # ~one call per tool on the clean path
    if first_latency and b.wall_ms <= 1200:
        reachable.add("1")
    if kinds & {"error", "timeout"}:
        reachable.add("4-8")  # retries push the count up
    return reachable


class ResearchAgent:
    """Robust pipeline: try each tool (bounded retry), then move on if it stays down.
    Reaches every tool, so every injected fault lands in the trace — good coverage."""

    id = "research.pipeline"

    def run(self, task: Any, env: ToolEnv, budget: Budgets) -> Any:
        answers: dict[str, Any] = {}
        for tool in TOOLS:
            for _ in range(2):  # bounded retry, then continue to the next tool
                try:
                    answers[tool] = env.call(tool, query=str(task))
                    break
                except ToolError:
                    env.state("retrying", tool=tool)
        return {"answer": answers}


class StubbornAgent:
    """PLANTED BUG: retries the *first* failing tool forever instead of moving on —
    Manifold flags `no_infinite_retry` + a budget terminal."""

    id = "research.stubborn"

    def run(self, task: Any, env: ToolEnv, budget: Budgets) -> Any:
        answers: dict[str, Any] = {}
        for tool in TOOLS:
            while True:
                try:
                    answers[tool] = env.call(tool, query=str(task))
                    break
                except ToolError:
                    env.state("retrying", tool=tool)
        return {"answer": answers}


AGENTS = {a.id: a for a in (ResearchAgent(), StubbornAgent())}

# Budget mix chosen so every terminal_reason is reachable: a normal budget completes; a
# tight step / cost budget trips budget_steps / budget_cost; a tight wall budget trips
# `timeout` once latency faults accumulate on the simulated clock.
_BUDGETS = [
    Budgets(max_steps=15, max_cost=100.0, wall_ms=5000),
    Budgets(max_steps=4, max_cost=100.0, wall_ms=5000),
    Budgets(max_steps=15, max_cost=3.0, wall_ms=5000),
    Budgets(max_steps=15, max_cost=100.0, wall_ms=150),
]

_FAULT_MENU = ["error", "timeout", "garbage", "latency"]

SPACE = ScenarioSpace(
    tasks=[
        "weather in Paris",
        "stock price AAPL",
        "who won the 2018 final",
        "define entropy",
        "translate hello to French",
    ],
    tools=[
        ToolSpec(name=t, responses=[f"{t}-A", f"{t}-B", f"{t}-C"], faults=list(_FAULT_MENU))
        for t in TOOLS
    ],
    fault_rate=0.4,
    persistent_prob=0.5,
    budget_choices=_BUDGETS,
)

MODEL = CoverageModel(
    coverpoints=[
        tool_called_cp(TOOLS),
        fault_seen_cp(),
        fault_by_tool_cp(TOOLS),  # 12 input-derivable cells — the coupon-collector space
        replace(  # 0 and 9+ calls unreachable; 1 IS reachable (timeout after one call)
            n_tool_calls_cp(), project=_ncalls_projection, ignore=frozenset({"0", "9+"})
        ),
        replace(  # the pipeline handles ToolError, so `error` never terminates it
            terminal_reason_cp(), project=_terminal_projection, ignore=frozenset({"error"})
        ),
    ],
)

INVARIANTS = [terminates_within_budget, no_infinite_retry()]
