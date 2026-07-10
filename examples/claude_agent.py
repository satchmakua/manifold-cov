"""A real Claude-backed tool-using agent wrapped in Manifold's Agent port (DESIGN.md §3).

``ClaudeSearchAgent`` runs the standard ``anthropic`` Messages API tool-use loop with a
single ``search`` tool, calling it through Manifold's ``env`` (which serves the mocked
response and injects faults). ``examples/recorded/claude_search_retry.jsonl`` is a
**genuine claude-haiku-4-5 trace recorded live** (2026-07-10): under a persistent search
outage the real model retried twice — rephrasing its query — then answered gracefully
within budget. A live 10-scenario coverage-directed sweep passed every invariant at
**36% coverage**: the model is robust here, and the coverage table names exactly which
behaviors (budget/timeout terminals, error-recovery transitions) were *not* exercised —
which is Manifold's point. The deterministic ``ScriptedClient`` below is the worst-case
contrast: a stand-in that retries forever, which the harness bounds and flags.

Running it:

* **Offline** (no key, free, deterministic) — ``python examples/claude_agent.py`` drives
  the scripted worst case and prints its verdict next to the committed real-model
  fixture's. Never writes the fixture.

* **Live sweep** — needs ``ANTHROPIC_API_KEY`` and ``pip install "manifold-cov[claude]"``:

      manifold run examples/claude_agent.py --spec examples/spec_example.py \\
          --agent claude.search --scenarios 10 --coverage-directed

* **Re-record the fixture** — ``python examples/claude_agent.py --live`` runs the seed-7
  scenario against the real API and overwrites the recording. Run once, inspect, commit.

The ``anthropic`` package is imported lazily (only on a live run), so this module imports
fine — and the offline demo/test runs — without it installed.
"""

from __future__ import annotations

import os
import random
import sys
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
    try:
        import anthropic  # lazy: keep the module importable without the package
    except ImportError as exc:
        raise RuntimeError(
            "the `anthropic` package is not installed - run `pip install \"manifold-cov[claude]\"` "
            "to enable live runs."
        ) from exc

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
# A stubborn model that always retries `search` and never gives up — the worst-case
# retry behavior the harness must bound. Drives the exact same agent loop with no API
# key. The real claude-haiku-4-5, recorded live in `examples/recorded/`, behaves
# *better*: it retries twice then answers gracefully — the scripted client is the
# deterministic contrast (and the offline test of the budget guardrail).


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


def _print_verdict(trace: Any) -> None:
    """Print a trace's call count, terminal reason, and invariant verdicts."""
    from manifold.invariants import no_infinite_retry, terminates_within_budget

    terminal = trace.terminal
    reason = terminal.reason if terminal else "?"
    print(f"  {len(trace.tools_called())} search calls, terminal reason = {reason}")
    checks = [
        ("terminates_within_budget", terminates_within_budget),
        ("no_infinite_retry", no_infinite_retry()),
    ]
    for name, inv in checks:
        v = inv(trace)
        detail = f" - {v.detail}" if v else ""
        print(f"  {'FAIL' if v else 'PASS'} {name}{detail}")


def _record_or_refuse(trace: Any, reason: str) -> None:
    """Overwrite the committed fixture only if the live trace matches the graceful-recovery
    contract the demo + tests assert (``completed`` with >=2 tool calls). A model that
    answers without retrying, or blows the budget, is a real change: write a side copy,
    leave the committed fixture UNCHANGED, and exit non-zero — so a non-representative trace
    can't be committed silently (the crash case is handled earlier)."""
    RECORDED.parent.mkdir(parents=True, exist_ok=True)
    if reason == "completed" and len(trace.tools_called()) >= 2:
        trace.dump_jsonl(RECORDED)
        print(f"  recorded -> {RECORDED}")
        return
    side = RECORDED.with_suffix(".new.jsonl")
    trace.dump_jsonl(side)
    print(
        f"  WARNING: live trace ({len(trace.tools_called())} calls, reason={reason}) does not "
        "match the graceful-recovery contract (completed, >=2 calls) the demo + tests assert."
    )
    print(f"  wrote {side} for inspection; the committed fixture is UNCHANGED.")
    print("  if this is the model's real new behavior, update the demo + tests, then replace it.")
    raise SystemExit(4)


def _demo(live: bool = False) -> None:
    """Offline (default): drive the loop with the deterministic ``ScriptedClient`` — the
    worst-case retry-forever stand-in the harness bounds at the step budget — and contrast
    it with the committed *real-model* fixture. The fixture is never written offline.

    ``live=True``: run the real API on the same seed-7 scenario and (re)record the
    fixture. Missing key / missing extra fail fast before anything runs; a run that
    crashes (bad key, rate limit, network) exits non-zero and leaves the fixture alone.
    """
    from manifold.harness import run
    from manifold.trace import AgentError, Trace

    client = _live_client() if live else ScriptedClient()
    agent = ClaudeSearchAgent(client=client)
    trace = run(agent, make_scenario(7))

    terminal = trace.terminal
    reason = terminal.reason if terminal else "?"
    if reason == "error":  # the run crashed (e.g. auth/rate-limit live) - keep the good fixture
        err = next((e for e in trace.events if isinstance(e, AgentError)), None)
        detail = f"{err.etype}: {err.message}" if err else "unknown error"
        print(f"error: run crashed ({detail})")
        print(f"  {RECORDED} left untouched - fix the cause and re-run")
        raise SystemExit(3)

    source = f"live model: {MODEL}" if live else "offline scripted client (worst-case stand-in)"
    print(f"Manifold - agent={agent.id} - seed=0x7 ({source})")
    _print_verdict(trace)

    if live:
        _record_or_refuse(trace, reason)
    elif RECORDED.exists():
        print(f"Committed real-model fixture ({MODEL}, recorded live):")
        _print_verdict(Trace.load_jsonl(RECORDED))
        print("  re-record: python examples/claude_agent.py --live  (needs ANTHROPIC_API_KEY)")
    print("  reproduce: manifold repro 7 examples/claude_agent.py --agent claude.search")


if __name__ == "__main__":
    try:
        _demo(live="--live" in sys.argv[1:])
    except RuntimeError as exc:  # missing key / missing extra — say so cleanly, no traceback
        print(f"error: {exc}")
        raise SystemExit(2) from None
