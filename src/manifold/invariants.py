"""Invariants — declarative checks over a finished Trace (DESIGN.md §4.3, §6.5).

An ``Invariant`` inspects a completed ``Trace`` and returns a ``Violation`` (carrying
the seed and a minimal failing excerpt) or ``None``. M0 ships the two highest-value
checks; the starter library grows as later milestones land.
"""

from __future__ import annotations

from collections.abc import Callable

from pydantic import BaseModel, Field

from manifold.trace import Event, Terminal, ToolCall, ToolResult, Trace


class Violation(BaseModel):
    invariant: str
    seed: int
    detail: str
    at_step: int | None = None
    excerpt: list[Event] = Field(default_factory=list)


Invariant = Callable[[Trace], "Violation | None"]

_BUDGET_REASONS = {"budget_steps", "budget_cost", "timeout"}


def terminates_within_budget(trace: Trace) -> Violation | None:
    """The agent must finish before exhausting any budget."""
    t = trace.terminal
    if isinstance(t, Terminal) and t.reason in _BUDGET_REASONS:
        return Violation(
            invariant="terminates_within_budget",
            seed=trace.seed,
            detail=f"run ended with reason={t.reason} after {t.steps_used} steps",
            at_step=t.step,
            excerpt=trace.events[-3:],
        )
    return None


def no_infinite_retry(max_consecutive: int = 3) -> Invariant:
    """Reject more than ``max_consecutive`` consecutive retries of a tool that keeps
    failing — the classic agent-reliability bug (retry-forever after a tool error)."""

    def check(trace: Trace) -> Violation | None:
        worst = 0
        worst_tool: str | None = None
        failed_tool: str | None = None  # tool whose most recent result was a failure
        run_len = 0
        for ev in trace.events:
            if isinstance(ev, ToolCall):
                run_len = run_len + 1 if failed_tool == ev.tool else 0
                if run_len > worst:
                    worst, worst_tool = run_len, ev.tool
                failed_tool = None
            elif isinstance(ev, ToolResult):
                failed_tool = ev.tool if not ev.ok else None
        if worst > max_consecutive:
            return Violation(
                invariant="no_infinite_retry",
                seed=trace.seed,
                detail=f"tool {worst_tool!r} retried {worst}x consecutively after failures",
                excerpt=trace.events[:8],
            )
        return None

    return check
