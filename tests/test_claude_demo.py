"""M3 acceptance (part 2): the Claude-backed example. Runs entirely offline — no API
key, no `anthropic` install — proving (a) the module imports with the SDK absent (the
live client is lazy), (b) the committed recorded trace is flagged by Manifold's
invariants, and (c) the scripted offline client drives the same loop to the bug.
"""

from __future__ import annotations

from types import ModuleType

from manifold.harness import run
from manifold.invariants import no_infinite_retry, terminates_within_budget
from manifold.trace import Terminal, Trace


def test_module_imports_without_anthropic(claude: ModuleType) -> None:
    # The `claude` fixture loaded the module; `anthropic` is not installed in dev.
    assert "claude.search" in claude.AGENTS
    assert claude.MODEL == "claude-haiku-4-5"


def test_recorded_trace_shows_the_bug(claude: ModuleType) -> None:
    trace = Trace.load_jsonl(claude.RECORDED)
    assert trace.agent_id == "claude.search"
    assert isinstance(trace.terminal, Terminal)
    assert trace.terminal.reason == "budget_steps"
    assert terminates_within_budget(trace) is not None
    assert no_infinite_retry()(trace) is not None


def test_scripted_offline_path_reproduces_the_bug(claude: ModuleType) -> None:
    agent = claude.ClaudeSearchAgent(client=claude.ScriptedClient())
    trace = run(agent, claude.make_scenario(7))
    assert isinstance(trace.terminal, Terminal)
    assert trace.terminal.reason == "budget_steps"
    assert "search" in trace.tools_called()
