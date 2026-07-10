"""H3 acceptance: the claude-agent-sdk adapter, tested entirely offline (no key, no CLI
subprocess). These cover the load-bearing bridge logic — the part that must be right for
the live run to mean anything:

* a tool call routes through Manifold's ``env`` and comes back as an MCP result;
* an injected ``ToolError`` becomes a *catchable* ``is_error`` result (the agent may retry);
* a ``BudgetExceeded`` raised through the bridge is stashed and re-raised **outside** the
  SDK machinery — never swallowed — so the harness records the budget terminal;
* the scripted worst-case agent (retries forever) is bounded and flagged;
* the module imports with ``claude-agent-sdk`` absent, and a live run fails fast without a key.

The real SDK actually *driving* the bridge is proven by the committed live fixture + the
one live run; it can't be exercised here without the CLI subprocess.
"""

from __future__ import annotations

from types import ModuleType
from typing import Any

import pytest

from manifold.harness import BudgetExceeded, run
from manifold.scenario import Budgets, Fault, Scenario, ToolMock
from manifold.trace import Terminal, Trace


def _clean_scenario() -> Scenario:
    return Scenario(
        seed=1, task="q", mocks=[ToolMock(tool="search", responses=["hit"])],
        budgets=Budgets(max_steps=10),
    )


def test_module_imports_without_the_sdk(sdk: ModuleType) -> None:
    # The `sdk` fixture loaded the module; `claude_agent_sdk` is imported lazily, only in
    # the live driver, so import must not require it.
    assert "sdk.search" in sdk.AGENTS
    assert sdk.AGENTS["sdk.search"].id == "sdk.search"


def test_bridge_routes_a_tool_call_through_env(sdk: ModuleType) -> None:
    # A driver that makes exactly one tool call, capturing the MCP result it gets back.
    captured: dict[str, Any] = {}

    def driver(prompt: str, bridges: list[Any], model: str, system: str, abort: Any) -> str:
        captured["result"] = bridges[0].call({"query": prompt})
        return "answered"

    agent = sdk.SdkAgent(driver=driver)
    trace = run(agent, _clean_scenario())
    assert captured["result"]["content"][0]["text"] == "hit"  # env served the mock response
    assert "is_error" not in captured["result"]
    assert trace.tools_called() == ["search"]
    assert isinstance(trace.terminal, Terminal)
    assert trace.terminal.reason == "completed"


def test_tool_error_becomes_a_catchable_is_error_result(sdk: ModuleType) -> None:
    # An injected error must reach the agent as an is_error result (not stop the run) —
    # ToolError is meant to be catchable/recoverable.
    captured: dict[str, Any] = {}

    def driver(prompt: str, bridges: list[Any], model: str, system: str, abort: Any) -> str:
        captured["result"] = bridges[0].call({"query": prompt})
        assert abort.tripped is False  # a ToolError must NOT trip the budget-abort path
        return "recovered"

    scn = Scenario(
        seed=1, task="q",
        mocks=[ToolMock(tool="search", faults=[Fault(at_call=-1, kind="error")])],
        budgets=Budgets(max_steps=10),
    )
    agent = sdk.SdkAgent(driver=driver)
    trace = run(agent, scn)
    assert captured["result"]["is_error"] is True
    assert "tool error" in captured["result"]["content"][0]["text"]
    assert isinstance(trace.terminal, Terminal)
    assert trace.terminal.reason == "completed"  # the driver "recovered" and returned


def test_budget_exceeded_is_stashed_and_reraised_not_swallowed(sdk: ModuleType) -> None:
    # The crux: a BudgetExceeded raised through the bridge must be re-raised OUTSIDE the
    # SDK machinery (the SDK would swallow it), so the harness records a budget terminal.
    def driver(prompt: str, bridges: list[Any], model: str, system: str, abort: Any) -> str:
        for _ in range(20):  # keep calling; the 4th call trips the 3-step budget
            bridges[0].call({"query": prompt})
            if abort.tripped:
                break
        assert abort.exc is not None
        assert isinstance(abort.exc, BudgetExceeded)  # stashed, not raised into the driver
        return ""  # the driver never sees the exception raised at it

    scn = Scenario(
        seed=1, task="q", mocks=[ToolMock(tool="search", responses=["ok"])],
        budgets=Budgets(max_steps=3),
    )
    agent = sdk.SdkAgent(driver=driver)
    trace = run(agent, scn)  # the harness catches the re-raised BudgetExceeded
    assert isinstance(trace.terminal, Terminal)
    assert trace.terminal.reason == "budget_steps"
    assert len(trace.tools_called()) == 3  # exactly the budget's worth, then stopped


def test_bridge_never_lets_budget_exceeded_escape_the_handler(sdk: ModuleType) -> None:
    # Directly: calling the bridge past budget must RETURN an error dict, never raise
    # (a raise inside a real SDK handler is swallowed in a detached task).
    env_calls = {"n": 0}

    class _Env:
        def call(self, tool: str, /, **args: Any) -> Any:
            env_calls["n"] += 1
            raise BudgetExceeded("budget_steps")

        def state(self, label: str, /, **data: Any) -> None: ...

    abort = sdk._AbortBox()
    result = sdk._bridge_call(_Env(), "search", {"query": "x"}, abort)
    assert result["is_error"] is True  # returned, not raised
    assert isinstance(abort.exc, BudgetExceeded)
    assert abort.tripped is True


def test_scripted_worst_case_is_bounded_by_the_budget(sdk: ModuleType) -> None:
    # The offline stand-in retries forever; the harness must stop and flag it.
    from manifold.invariants import no_infinite_retry, terminates_within_budget

    agent = sdk.SdkAgent(driver=sdk._scripted_driver)
    trace = run(agent, sdk.make_scenario(7))
    assert isinstance(trace.terminal, Terminal)
    assert trace.terminal.reason == "budget_steps"
    assert terminates_within_budget(trace) is not None  # both invariants FAIL, as intended
    assert no_infinite_retry()(trace) is not None


def test_live_without_key_fails_fast(sdk: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    # Call the live driver directly: the harness would catch a RuntimeError into an
    # "error" trace, so assert the fail-fast at its source (before any subprocess spawn).
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        sdk._live_driver("q", [], "claude-haiku-4-5", "sys", sdk._AbortBox())


def test_module_exposes_the_cli_contract(sdk: ModuleType) -> None:
    from manifold.generate import ScenarioSpace
    from manifold.model import CoverageModel

    assert isinstance(sdk.SPACE, ScenarioSpace)
    assert isinstance(sdk.MODEL, CoverageModel)  # resolvable by the CLI (not the SDK model str)
    assert sdk.INVARIANTS


def test_live_demo_records_a_graceful_trace(
    sdk: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    # The positive path: a live driver that makes >=2 calls then completes matches the
    # fixture contract, so `--live` records it. (Exercises _demo(live=True) + the guard.)
    def graceful_driver(prompt: str, bridges: list[Any], m: str, s: str, abort: Any) -> str:
        bridges[0].call({"query": prompt})  # call 1 (faulted -> is_error, no abort)
        bridges[0].call({"query": prompt})  # call 2
        return "here is the answer"

    monkeypatch.setattr(sdk, "_live_driver", graceful_driver)
    fixture = tmp_path / "sdk_search.jsonl"
    monkeypatch.setattr(sdk, "RECORDED", fixture)
    sdk._demo(live=True)
    trace = Trace.load_jsonl(fixture)
    assert trace.terminal is not None and trace.terminal.reason == "completed"
    assert len(trace.tools_called()) == 2


def test_live_rerecord_refuses_to_clobber_a_degenerate_trace(
    sdk: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    # Regression (adversarial review): a live re-record that does NOT match the graceful-
    # recovery contract (here: the model answers with 0 tool calls) must leave the committed
    # fixture untouched and exit non-zero — not silently overwrite it with a green verdict.
    def degenerate_driver(prompt: str, bridges: list[Any], m: str, s: str, abort: Any) -> str:
        return "answered from priors, never searched"  # 0 tool calls, reason=completed

    monkeypatch.setattr(sdk, "_live_driver", degenerate_driver)  # SdkAgent().run resolves this
    fixture = tmp_path / "sdk_search.jsonl"
    fixture.write_text("precious\n", encoding="utf-8")
    monkeypatch.setattr(sdk, "RECORDED", fixture)
    with pytest.raises(SystemExit) as exc_info:
        sdk._demo(live=True)
    assert exc_info.value.code == 4
    assert fixture.read_text(encoding="utf-8") == "precious\n"  # committed fixture unchanged


def test_recorded_live_sdk_trace_shows_graceful_recovery(sdk: ModuleType) -> None:
    # The committed real-SDK recording (H3): the off-the-shelf claude-agent-sdk agent,
    # driven through the adapter's in-process MCP bridge, retried the outage (>=2 calls)
    # then answered within budget — both invariants pass. Re-recording updates this.
    from manifold.invariants import no_infinite_retry, terminates_within_budget

    trace = Trace.load_jsonl(sdk.RECORDED)
    assert trace.agent_id == "sdk.search"
    assert isinstance(trace.terminal, Terminal)
    assert trace.terminal.reason == "completed"
    assert len(trace.tools_called()) >= 2  # it retried the failing tool before giving up
    assert terminates_within_budget(trace) is None
    assert no_infinite_retry()(trace) is None
