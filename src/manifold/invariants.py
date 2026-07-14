"""Invariants — declarative checks over a finished Trace (DESIGN.md §4.3, §6.5).

An ``Invariant`` inspects a completed ``Trace`` and returns a ``Violation`` (carrying
the seed and a minimal failing excerpt) or ``None``. M0 shipped the two highest-value
checks (budget termination, no infinite retry); the post-v1 review pass grew the library
to six generic checks — ``starter_library()`` returns them all. The extra checks are
opt-in per spec (except ``no_agent_crash``, which joins the CLI defaults): retrofitting
them into existing specs would silently change what committed artifacts claimed.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable, Iterator
from typing import Any

from pydantic import BaseModel, Field

from manifold.trace import AgentError, AgentOutput, Event, Terminal, ToolCall, ToolResult, Trace


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

    check.__name__ = "no_infinite_retry"
    return check


def no_agent_crash(trace: Trace) -> Violation | None:
    """The agent must not die on an unhandled exception. An ``error`` terminal means a
    fault (often garbage data) escaped the agent's own handling — a real reliability bug
    that, before this check, failed *no* invariant (a crashed trace printed PASS/PASS)."""
    t = trace.terminal
    if isinstance(t, Terminal) and t.reason == "error":
        err = next((e for e in trace.events if isinstance(e, AgentError)), None)
        detail = f"{err.etype}: {err.message}" if err else "agent raised an unhandled exception"
        return Violation(
            invariant="no_agent_crash",
            seed=trace.seed,
            detail=f"agent crashed: {detail}",
            at_step=t.step,
            excerpt=trace.events[-3:],
        )
    return None


def no_empty_output(trace: Trace) -> Violation | None:
    """A run that *completes* must actually answer. An agent that declares success with
    an empty/None output silently dropped the task."""
    t = trace.terminal
    if not (isinstance(t, Terminal) and t.reason == "completed"):
        return None  # only completed runs promise an answer
    out = next((e for e in reversed(trace.events) if isinstance(e, AgentOutput)), None)
    text = (out.text or "").strip() if out else ""
    if not text or text == "None":
        return Violation(
            invariant="no_empty_output",
            seed=trace.seed,
            detail="run completed but produced an empty final output",
            excerpt=trace.events[-3:],
        )
    return None


def _output_strings(value: Any) -> Iterator[str]:
    """Every string leaf in an agent's output — so a garbage value carried in a *structured*
    return (e.g. ``{"answer": "<garbage>"}``) is seen unescaped, not via ``str(dict)`` which
    repr-escapes control characters and would hide it."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _output_strings(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _output_strings(v)


def garbage_not_parroted(trace: Trace) -> Violation | None:
    """Injected garbage must not appear verbatim in the final answer — an agent that
    pipes corrupted tool data straight through to the user has no output validation.
    Matches the *served* garbage value against the output's string leaves (the raw payload,
    not its repr) so it fires for both string- and dict-returning agents; >=4 chars, to
    avoid trivial substring hits."""
    t = trace.terminal
    if not (isinstance(t, Terminal) and t.reason == "completed"):
        return None
    out = next((e for e in reversed(trace.events) if isinstance(e, AgentOutput)), None)
    if out is None:
        return None
    haystacks = list(_output_strings(out.payload))
    if out.text:
        haystacks.append(out.text)
    if not haystacks:
        return None
    for e in trace.events:
        if isinstance(e, ToolResult) and e.fault == "garbage" and e.value is not None:
            garbage = str(e.value)
            if len(garbage) >= 4 and any(garbage in h for h in haystacks):
                return Violation(
                    invariant="garbage_not_parroted",
                    seed=trace.seed,
                    detail=f"final output contains injected garbage verbatim ({garbage[:40]!r})",
                    at_step=e.step,
                    excerpt=[e],
                )
    return None


def no_duplicate_identical_calls(max_repeats: int = 3) -> Invariant:
    """Reject calling the *same tool with the same arguments* more than ``max_repeats``
    times across the whole run — the non-consecutive loop (A-B-A-B cycling, re-issuing an
    identical query) that ``no_infinite_retry``'s consecutive check misses."""

    def check(trace: Trace) -> Violation | None:
        counts: Counter[tuple[str, str]] = Counter()
        for ev in trace.events:
            if isinstance(ev, ToolCall):
                try:  # order-independent when JSON-able; repr fallback for exotic keys
                    args_key = json.dumps(ev.args, sort_keys=True, default=str)
                except TypeError:  # e.g. non-str nested dict keys — repr never raises
                    args_key = repr(ev.args)
                counts[(ev.tool, args_key)] += 1
        if counts:
            (tool, args), n = counts.most_common(1)[0]
            if n > max_repeats:
                return Violation(
                    invariant="no_duplicate_identical_calls",
                    seed=trace.seed,
                    detail=f"identical call {tool}({args}) issued {n}x (max {max_repeats})",
                    excerpt=trace.events[:8],
                )
        return None

    check.__name__ = "no_duplicate_identical_calls"
    return check


def starter_library() -> list[Invariant]:
    """All six generic checks, with defaults — the widest generic meaning of FAIL.
    Specs cherry-pick from these; the wild-agent hunt runs them all."""
    return [
        terminates_within_budget,
        no_infinite_retry(),
        no_agent_crash,
        no_empty_output,
        garbage_not_parroted,
        no_duplicate_identical_calls(),
    ]
