"""A worked Manifold spec for the toy `search` agent (DESIGN.md §6.8).

A spec module declares what to measure and check for a given target:

    MODEL: CoverageModel       # the behavior space (coverpoints, FSM, crosses)
    INVARIANTS: list[Invariant]  # the checks every run must satisfy

Point any command at it with ``--spec examples/spec_example.py``, or inspect it with
``manifold cover examples/spec_example.py``. (M2 adds ``SPACE`` — the ScenarioSpace —
here too, replacing the hand-built make_scenario in toy_agents.py.)
"""

from __future__ import annotations

from manifold.generate import ScenarioSpace, ToolSpec
from manifold.invariants import no_infinite_retry, terminates_within_budget
from manifold.model import (
    CoverageModel,
    Cross,
    TransitionCoverpoint,
    fault_seen_cp,
    n_tool_calls_cp,
    terminal_reason_cp,
    tool_called_cp,
)
from manifold.scenario import Budgets
from manifold.trace import ToolCall, ToolResult, Trace


def _search_fsm_symbols(t: Trace) -> list[str]:
    """Reduce the trace to an FSM symbol sequence over `search` call outcomes.
    The high-value edges this exposes: `search:err -> search:err` (the retry-forever
    signature) and `search:err -> search:ok` (recovery — which our M0 scenarios never
    exercise, so it shows up as a coverage hole)."""
    syms = ["START"]
    pending = False
    for e in t.events:
        if isinstance(e, ToolCall) and e.tool == "search":
            pending = True
        elif isinstance(e, ToolResult) and e.tool == "search" and pending:
            syms.append("search:ok" if e.ok else "search:err")
            pending = False
    syms.append("END")
    return syms


SEARCH_FSM = TransitionCoverpoint(
    "tool_fsm",
    _search_fsm_symbols,
    [
        ("START", "search:ok"),
        ("START", "search:err"),
        ("search:err", "search:err"),  # retried after failure
        ("search:err", "search:ok"),  # recovered after failure
        ("search:ok", "END"),
        ("search:err", "END"),
    ],
)

MODEL = CoverageModel(
    coverpoints=[
        n_tool_calls_cp(),
        terminal_reason_cp(),
        fault_seen_cp(),
        tool_called_cp(["search"]),
    ],
    transitions=[SEARCH_FSM],
    crosses=[Cross("reason_x_ncalls", "terminal_reason", "n_tool_calls")],
)

INVARIANTS = [terminates_within_budget, no_infinite_retry()]

# The constrained-random space the M2 generator samples (replaces the hand-built
# make_scenario in toy_agents.py). The full fault menu + a mix of persistent and
# transient faults is what lets the sweep exercise the recovery edge that the M0
# scenarios never reached — closing that coverage hole.
SPACE = ScenarioSpace(
    tasks=[
        "weather in Paris",
        "stock price AAPL",
        "who won the 2018 final",
        "translate hello to French",
        "define entropy",
    ],
    tools=[
        ToolSpec(
            name="search",
            responses=["result-A", "result-B", "result-C", "docs#42"],
            faults=["error", "timeout", "garbage", "latency"],
        )
    ],
    fault_rate=0.4,
    persistent_prob=0.5,
    budget_choices=[Budgets(max_steps=15), Budgets(max_steps=8)],
)
