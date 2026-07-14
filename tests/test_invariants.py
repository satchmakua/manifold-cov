"""W1: the grown invariant library (2 -> 6 generic checks). Each new check is tested
positive (fires on the bug it names) and negative (silent on healthy traces) using
hand-built traces — no agent runs needed, these are pure Trace predicates.
"""

from __future__ import annotations

from manifold.invariants import (
    garbage_not_parroted,
    no_agent_crash,
    no_duplicate_identical_calls,
    no_empty_output,
    starter_library,
)
from manifold.trace import AgentError, AgentOutput, Event, Terminal, ToolCall, ToolResult, Trace


def _trace(events: list[Event], reason: str = "completed", step: int = 1) -> Trace:
    events = events + [
        Terminal(step=step, reason=reason, steps_used=step, cost_used=float(step), wall_ms=0)
    ]
    return Trace(seed=1, scenario_id="x" * 12, agent_id="test.agent", events=events)


def _call(step: int, tool: str = "search", **args: object) -> ToolCall:
    return ToolCall(step=step, tool=tool, args=dict(args))


# --- no_agent_crash -----------------------------------------------------------


def test_no_agent_crash_fires_on_error_terminal() -> None:
    t = _trace(
        [AgentError(step=1, etype="KeyError", message="'answer'")], reason="error"
    )
    v = no_agent_crash(t)
    assert v is not None
    assert "KeyError" in v.detail


def test_no_agent_crash_silent_on_budget_and_completed() -> None:
    assert no_agent_crash(_trace([], reason="completed")) is None
    assert no_agent_crash(_trace([], reason="budget_steps")) is None


# --- no_empty_output ----------------------------------------------------------


def test_no_empty_output_fires_on_blank_completion() -> None:
    for text in ("", "   ", None, "None"):
        t = _trace([AgentOutput(step=1, text=text)])
        assert no_empty_output(t) is not None, f"should fire for {text!r}"


def test_no_empty_output_silent_on_real_answer_and_non_completed() -> None:
    assert no_empty_output(_trace([AgentOutput(step=1, text="42 degrees")])) is None
    # a budget death produces no answer, but that's terminates_within_budget's job
    assert no_empty_output(_trace([], reason="budget_steps")) is None


# --- garbage_not_parroted -----------------------------------------------------


def test_garbage_not_parroted_fires_when_garbage_reaches_the_answer() -> None:
    garbage = "\x00<garbage>\x00"
    t = _trace(
        [
            _call(1),
            ToolResult(step=1, tool="search", ok=True, value=garbage, fault="garbage"),
            AgentOutput(step=1, text=f"The answer is {garbage} according to search."),
        ]
    )
    v = garbage_not_parroted(t)
    assert v is not None
    assert "garbage" in v.detail


def test_garbage_not_parroted_fires_for_dict_returning_agents() -> None:
    # Regression (adversarial review): the repo's toy/research agents return dicts, and
    # str(dict) repr-escapes the NUL bytes in the default garbage value — the check must
    # match the raw payload's string leaves, not just AgentOutput.text.
    garbage = "\x00<garbage>\x00"
    payload = {"answer": f"result: {garbage}"}
    t = _trace(
        [
            _call(1),
            ToolResult(step=1, tool="search", ok=True, value=garbage, fault="garbage"),
            AgentOutput(step=1, text=str(payload), payload=payload),  # text is repr-escaped
        ]
    )
    assert garbage not in (str(payload))  # sanity: the escaped text really hides it
    assert garbage_not_parroted(t) is not None  # ...but the payload path still catches it


def test_garbage_not_parroted_silent_when_agent_sanitizes() -> None:
    t = _trace(
        [
            _call(1),
            ToolResult(step=1, tool="search", ok=True, value="\x00<garbage>\x00", fault="garbage"),
            AgentOutput(step=1, text="The search tool returned corrupted data; retrying failed."),
        ]
    )
    assert garbage_not_parroted(t) is None


def test_garbage_not_parroted_ignores_short_values() -> None:
    # A 2-char garbage value would match half the alphabet — below the length floor.
    t = _trace(
        [
            _call(1),
            ToolResult(step=1, tool="search", ok=True, value="42", fault="garbage"),
            AgentOutput(step=1, text="The answer is 42."),
        ]
    )
    assert garbage_not_parroted(t) is None


# --- no_duplicate_identical_calls ----------------------------------------------


def test_duplicate_calls_fires_on_nonconsecutive_loop() -> None:
    # A-B-A-B cycling: `no_infinite_retry` (consecutive) misses this; the new check must not.
    events: list[Event] = []
    for i in range(4):
        events += [
            _call(2 * i + 1, "search", q="same"),
            ToolResult(step=2 * i + 1, tool="search", ok=True, value="r"),
            _call(2 * i + 2, "fetch", url="same"),
            ToolResult(step=2 * i + 2, tool="fetch", ok=True, value="r"),
        ]
    t = _trace(events, step=8)
    v = no_duplicate_identical_calls()(t)
    assert v is not None
    assert "4x" in v.detail


def test_duplicate_calls_silent_on_distinct_args() -> None:
    events: list[Event] = []
    for i in range(6):
        events += [
            _call(i + 1, "search", q=f"query-{i}"),
            ToolResult(step=i + 1, tool="search", ok=True, value="r"),
        ]
    assert no_duplicate_identical_calls()(_trace(events, step=6)) is None


# --- the library ----------------------------------------------------------------


def test_duplicate_calls_survives_nonstring_nested_arg_keys() -> None:
    # Regression (adversarial review): json.dumps(sort_keys=True) raised TypeError on
    # non-str nested dict keys (legal per the ToolEnv protocol), crashing the whole sweep.
    # The check must fall back gracefully and never raise.
    events: list[Event] = []
    for i in range(5):
        events += [
            ToolCall(step=i + 1, tool="lookup", args={"m": {(1, 2): "x", "k": 3}}),
            ToolResult(step=i + 1, tool="lookup", ok=True, value="r"),
        ]
    check = no_duplicate_identical_calls()
    v = check(_trace(events, step=5))  # must not raise
    assert v is not None  # and still detects the 5x identical exotic-arg call


def test_starter_library_has_six_named_checks() -> None:
    lib = starter_library()
    names = {getattr(inv, "__name__", "?") for inv in lib}
    assert len(lib) == 6
    assert names == {
        "terminates_within_budget",
        "no_infinite_retry",
        "no_agent_crash",
        "no_empty_output",
        "garbage_not_parroted",
        "no_duplicate_identical_calls",
    }


def test_library_all_pass_on_a_healthy_trace() -> None:
    t = _trace(
        [
            _call(1, q="weather"),
            ToolResult(step=1, tool="search", ok=True, value="sunny"),
            AgentOutput(step=1, text="It is sunny."),
        ]
    )
    assert all(inv(t) is None for inv in starter_library())
