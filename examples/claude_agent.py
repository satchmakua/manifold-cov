"""A real Claude-backed tool-using agent wrapped in Manifold's Agent port (DESIGN.md §3).

``ClaudeSearchAgent`` runs the standard ``anthropic`` Messages API tool-use loop with a
single ``search`` tool, calling it through Manifold's ``env`` (which serves the mocked
response and injects faults). When the search tool keeps failing, a real model tends to
retry — and Manifold bounds that with the step budget and flags it.

Running it:

* **Live** — needs ``ANTHROPIC_API_KEY`` and ``pip install "manifold-cov[claude]"``:

      manifold run examples/claude_agent.py --spec examples/spec_example.py \\
          --scenarios 10 --coverage-directed

* **Offline** (no key, free, deterministic) — a scripted stand-in client drives the same
  loop, and ``python examples/claude_agent.py`` records the trace + prints the verdict.
  ``examples/recorded/claude_search_retry.jsonl`` is that committed recording; the demo's
  test analyses it with no API call. Re-record with a real key for a genuine model trace.

The ``anthropic`` package is imported lazily (only on a live run), so this module imports
fine — and the offline demo/test runs — without it installed.
"""

from __future__ import annotations

import os
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from manifold.agent import ToolEnv
from manifold.harness import ToolError
from manifold.scenario import Budgets, Fault, Scenario, ToolMock

MODEL = "claude-haiku-4-5"  # cheap + capable; the demo doesn't need a frontier model
SYSTEM = "You are a research assistant. Use the search tool to answer the user's question."
SEARCH_TOOL = {
    "name": "search",
    "description": "Search the web for information. Returns a short result string.",
    "input_schema": {
        "type": "object",
        "properties": {"query": {"type": "string", "description": "The search query."}},
        "required": ["query"],
    },
}


def _live_client() -> Any:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError(
            "ClaudeSearchAgent needs ANTHROPIC_API_KEY and `pip install \"manifold-cov[claude]\"` "
            "to run live. For an offline demo, run `python examples/claude_agent.py` (scripted)."
        )
    import anthropic  # lazy: keep the module importable without the package

    return anthropic.Anthropic()


class ClaudeSearchAgent:
    """The real tool-use loop. Pass ``client`` to inject a stand-in (offline/testing);
    otherwise a live ``anthropic.Anthropic()`` is built on first run."""

    id = "claude.search"

    def __init__(self, client: Any | None = None) -> None:
        self._client = client

    def run(self, task: Any, env: ToolEnv, budget: Budgets) -> Any:
        client = self._client or _live_client()
        messages: list[dict[str, Any]] = [{"role": "user", "content": str(task)}]

        while True:
            resp = client.messages.create(
                model=MODEL,
                max_tokens=1024,
                system=SYSTEM,
                tools=[SEARCH_TOOL],
                messages=messages,
            )
            messages.append({"role": "assistant", "content": resp.content})
            tool_uses = [b for b in resp.content if getattr(b, "type", None) == "tool_use"]

            if resp.stop_reason != "tool_use" or not tool_uses:
                texts = (b.text for b in resp.content if getattr(b, "type", None) == "text")
                return next(texts, "")

            results = []
            for tu in tool_uses:
                try:
                    value = env.call(tu.name, **(tu.input or {}))
                    results.append(
                        {"type": "tool_result", "tool_use_id": tu.id, "content": str(value)}
                    )
                except ToolError as exc:
                    results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": tu.id,
                            "content": f"tool error: {exc}",
                            "is_error": True,
                        }
                    )
            messages.append({"role": "user", "content": results})


# --- Offline stand-in --------------------------------------------------------
# A stubborn model that always retries `search` and never gives up. Drives the exact
# same agent loop with no API key, producing the recorded bug trace. A real model is
# nondeterministic; this is the deterministic floor for the offline demo/test.


@dataclass
class _Block:
    type: str
    id: str | None = None
    name: str | None = None
    input: dict[str, Any] | None = None
    text: str | None = None


@dataclass
class _Response:
    content: list[_Block]
    stop_reason: str


@dataclass
class _ScriptedMessages:
    n: int = 0

    def create(self, **_: Any) -> _Response:
        self.n += 1
        block = _Block(type="tool_use", id=f"toolu_{self.n}", name="search", input={"query": "..."})
        return _Response(content=[block], stop_reason="tool_use")


@dataclass
class ScriptedClient:
    """Offline stand-in for ``anthropic.Anthropic`` — exposes ``.messages.create``."""

    messages: _ScriptedMessages = field(default_factory=_ScriptedMessages)


AGENTS = {"claude.search": ClaudeSearchAgent()}

_TASKS = ["weather in Paris", "stock price AAPL", "latest CPI figure", "who won the 2018 final"]


def make_scenario(seed: int) -> Scenario:
    """A persistent `search` outage — the condition under which a retry-prone agent
    blows its budget. (With `--spec examples/spec_example.py` the richer SPACE is used.)"""
    rng = random.Random(seed)
    outage = ToolMock(tool="search", responses=["result"], faults=[Fault(at_call=-1, kind="error")])
    return Scenario(
        seed=seed,
        task=rng.choice(_TASKS),
        mocks=[outage],
        budgets=Budgets(max_steps=12),
    )


RECORDED = Path(__file__).resolve().parent / "recorded" / "claude_search_retry.jsonl"


def _offline_demo() -> None:
    """Run the scripted agent through the harness, print the verdict, (re)write the trace."""
    from manifold.harness import run
    from manifold.invariants import no_infinite_retry, terminates_within_budget

    agent = ClaudeSearchAgent(client=ScriptedClient())
    trace = run(agent, make_scenario(7))
    RECORDED.parent.mkdir(parents=True, exist_ok=True)
    trace.dump_jsonl(RECORDED)

    terminal = trace.terminal
    reason = terminal.reason if terminal else "?"
    print(f"Manifold - agent={agent.id} - seed=0x7 (offline scripted client)")
    print(f"  {len(trace.tools_called())} search calls, terminal reason = {reason}")
    checks = [
        ("terminates_within_budget", terminates_within_budget),
        ("no_infinite_retry", no_infinite_retry()),
    ]
    for name, inv in checks:
        v = inv(trace)
        detail = f" - {v.detail}" if v else ""
        print(f"  {'FAIL' if v else 'PASS'} {name}{detail}")
    print(f"  recorded -> {RECORDED}")
    print("  reproduce: manifold repro 7 examples/claude_agent.py --agent claude.search")


if __name__ == "__main__":
    _offline_demo()
