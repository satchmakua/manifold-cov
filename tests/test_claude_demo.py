"""M3/H2 acceptance: the Claude-backed example. Runs entirely offline — no API key
required, and no `anthropic` import happens (the live client is lazy) — proving (a) the
module imports with the SDK absent, (b) the committed **real claude-haiku-4-5 trace**
(recorded live 2026-07-10) loads and shows the model handling the outage gracefully,
(c) the scripted worst-case client drives the same loop into the budget guardrail, and
(d) the `--live` recording path is wired, fail-fast, and can't clobber the fixture.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from manifold.harness import run
from manifold.invariants import no_infinite_retry, terminates_within_budget
from manifold.trace import Terminal, Trace


def test_module_imports_without_anthropic(claude: ModuleType) -> None:
    # The `claude` fixture loaded the module without importing `anthropic` (lazy).
    assert "claude.search" in claude.AGENTS
    assert claude.MODEL == "claude-haiku-4-5"


def test_recorded_live_trace_shows_graceful_recovery(claude: ModuleType) -> None:
    # Describes the committed real-model recording (H2): under a persistent search outage
    # the live claude-haiku-4-5 retried (>=2 calls), then answered within budget — both
    # invariants pass. If the fixture is ever re-recorded, update this to match reality.
    trace = Trace.load_jsonl(claude.RECORDED)
    assert trace.agent_id == "claude.search"
    assert isinstance(trace.terminal, Terminal)
    assert trace.terminal.reason == "completed"
    assert len(trace.tools_called()) >= 2  # it retried the failing tool before giving up
    assert terminates_within_budget(trace) is None
    assert no_infinite_retry()(trace) is None


def test_scripted_worst_case_is_bounded_by_the_budget(claude: ModuleType) -> None:
    # The deterministic contrast: a stand-in that retries forever is stopped and flagged.
    agent = claude.ClaudeSearchAgent(client=claude.ScriptedClient())
    trace = run(agent, claude.make_scenario(7))
    assert isinstance(trace.terminal, Terminal)
    assert trace.terminal.reason == "budget_steps"
    assert "search" in trace.tools_called()


def test_offline_demo_never_writes_the_fixture(
    claude: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Regression: the offline demo used to (re)write RECORDED — which would now clobber
    # the committed *real-model* trace with the scripted one. Offline is read-only.
    monkeypatch.setattr(claude, "RECORDED", tmp_path / "fixture.jsonl")
    claude._demo(live=False)
    assert not (tmp_path / "fixture.jsonl").exists()


def _graceful_client(claude: ModuleType) -> Any:
    """A stand-in that calls `search` twice (both faulted) then answers — the graceful-
    recovery shape the fixture contract requires, so `--live` records it."""

    class _Messages:
        n = 0

        def create(self, **_: Any) -> Any:
            self.n += 1
            if self.n <= 2:
                block = claude._Block(type="tool_use", id=f"t{self.n}", name="search", input={})
                return claude._Response(content=[block], stop_reason="tool_use")
            block = claude._Block(type="text", text="Here is the answer.")
            return claude._Response(content=[block], stop_reason="end_turn")

    class _Client:
        def __init__(self) -> None:
            self.messages = _Messages()

    return _Client()


def test_live_flag_records_a_graceful_trace(
    claude: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # H2 readiness: `--live` builds the client up front and records a graceful-recovery
    # trace (completed, >=2 calls) — the shape the fixture contract accepts. No key needed.
    monkeypatch.setattr(claude, "_live_client", lambda: _graceful_client(claude))
    fixture = tmp_path / "live.jsonl"
    monkeypatch.setattr(claude, "RECORDED", fixture)
    claude._demo(live=True)
    trace = Trace.load_jsonl(fixture)
    assert trace.agent_id == "claude.search"
    assert isinstance(trace.terminal, Terminal)
    assert trace.terminal.reason == "completed"
    assert len(trace.tools_called()) == 2


def test_live_rerecord_refuses_to_clobber_a_degenerate_trace(
    claude: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Regression (adversarial review): a live re-record that doesn't match the graceful-
    # recovery contract (here: the model answers with 0 tool calls) must leave the committed
    # fixture untouched and exit non-zero, not silently overwrite it with a green verdict.
    class _Messages:
        def create(self, **_: Any) -> Any:
            block = claude._Block(type="text", text="Answered from priors, no search.")
            return claude._Response(content=[block], stop_reason="end_turn")

    class _Client:
        def __init__(self) -> None:
            self.messages = _Messages()

    monkeypatch.setattr(claude, "_live_client", lambda: _Client())
    fixture = tmp_path / "live.jsonl"
    fixture.write_text("precious\n", encoding="utf-8")
    monkeypatch.setattr(claude, "RECORDED", fixture)
    with pytest.raises(SystemExit) as exc_info:
        claude._demo(live=True)
    assert exc_info.value.code == 4
    assert fixture.read_text(encoding="utf-8") == "precious\n"  # committed fixture unchanged


def test_live_without_key_fails_fast(
    claude: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No key -> a clear RuntimeError *before* anything runs (nothing half-recorded).
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        claude._demo(live=True)


def test_live_without_extra_fails_fast(
    claude: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Regression (adversarial audit): key set but `anthropic` not installed used to escape
    # as a raw ModuleNotFoundError traceback instead of the clean install hint.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-not-a-real-key")
    monkeypatch.setitem(sys.modules, "anthropic", None)  # makes `import anthropic` fail
    with pytest.raises(RuntimeError, match=r"manifold-cov\[claude\]"):
        claude._demo(live=True)


def test_live_api_failure_leaves_fixture_untouched(
    claude: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Regression (adversarial audit): a mid-run API failure (bad key, rate limit, 529)
    # becomes a terminal reason 'error' trace - it must NOT clobber the committed fixture,
    # and must exit non-zero instead of printing a healthy-looking PASS/PASS verdict.
    class _ExplodingMessages:
        def create(self, **_: object) -> object:
            raise ConnectionError("simulated mid-run API failure")

    class _ExplodingClient:
        def __init__(self) -> None:
            self.messages = _ExplodingMessages()

    monkeypatch.setattr(claude, "_live_client", lambda: _ExplodingClient())
    fixture = tmp_path / "fixture.jsonl"
    fixture.write_text("precious\n", encoding="utf-8")
    monkeypatch.setattr(claude, "RECORDED", fixture)
    with pytest.raises(SystemExit) as exc_info:
        claude._demo(live=True)
    assert exc_info.value.code == 3
    assert fixture.read_text(encoding="utf-8") == "precious\n"
