"""Verify an *off-the-shelf* ``claude-agent-sdk`` agent with Manifold (DESIGN.md §8 M4 / H3).

The other examples wrap agents *we* wrote. This one wraps Anthropic's higher-level
``claude-agent-sdk`` — the agent loop, context management, and tool dispatch all live in
the SDK's bundled ``claude`` CLI subprocess, not in our code. The adapter's whole job is
to make that third-party agent's tool calls flow through Manifold's ``env`` (which serves
the seed-resolved mock and injects faults) and to keep budget enforcement intact across
the process boundary — so ``manifold run`` verifies the SDK agent *unmodified*.

How the bridge works
--------------------
* Each Manifold "tool" is exposed to the SDK as an **in-process MCP tool**
  (``create_sdk_mcp_server`` + ``@tool``). When the SDK agent calls it, the SDK routes the
  call back to our async handler, which invokes ``env.call(name, **args)`` — the same
  mocked, faulted, budget-metered path every other agent uses.
* ``ToolError`` (a catchable failure, by design) becomes an ``is_error`` tool result the
  agent sees and may retry — exactly its contract.
* ``BudgetExceeded`` inherits ``BaseException`` on purpose, and the SDK's MCP layer only
  catches ``Exception`` — a raised ``BudgetExceeded`` would be **silently swallowed in a
  detached task** and the CLI-side call would hang. So the handler catches it *itself*,
  stashes it in an ``_AbortBox``, returns an error result to unblock the CLI, and the
  driver re-raises it **outside** the SDK machinery. The harness then records the budget
  terminal as usual. Budget enforcement stays non-swallowable across the subprocess.

Running it
----------
* **Offline** (no key, no CLI) — ``python examples/sdk_agent.py`` drives a scripted
  stand-in that retries forever (bounded and flagged by the harness) and prints its
  verdict next to the committed real-SDK fixture. Never writes the fixture.

* **Live sweep** — needs ``ANTHROPIC_API_KEY`` + ``pip install "manifold-cov[sdk]"`` (the
  ``claude-agent-sdk`` wheel bundles the ``claude`` CLI, so no npm install is needed):

      manifold run examples/sdk_agent.py --agent sdk.search --scenarios 2

* **Re-record the fixture** — ``python examples/sdk_agent.py --live`` runs the seed-7
  scenario against the real SDK and overwrites the recording. Run once, inspect, commit.

``claude_agent_sdk`` is imported lazily (only on a live run), so this module imports fine
— and the offline demo/test runs — without the package installed.
"""

from __future__ import annotations

import os
import random
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from manifold.agent import ToolEnv
from manifold.generate import ScenarioSpace, ToolSpec
from manifold.harness import ToolError
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
from manifold.scenario import Budgets, Fault, Scenario, ToolMock
from manifold.trace import ToolCall, ToolResult, Trace

_SDK_MODEL = "claude-haiku-4-5"  # cheap; the demo proves the wiring, not frontier capability
_SERVER = "manifold"  # MCP server name; tool ids are mcp__<_SERVER>__<tool>
_MAX_TURNS = 8  # a hard ceiling on the SDK agent's loop, independent of Manifold's budget
SYSTEM = (
    "You are a research assistant with exactly one tool, `search`. Answer the user's "
    "question by calling `search` with a query string. You have no other tools and no "
    "other way to look things up. Keep your final answer to one or two sentences."
)


# --- The env <-> SDK bridge (the reusable, testable core) --------------------


class _AbortBox:
    """Carries the first non-``ToolError`` a tool handler hit (e.g. ``BudgetExceeded``).

    The SDK swallows a ``BaseException`` raised in a handler, so the handler stashes it
    here instead and the driver re-raises it outside the SDK's machinery. ``tripped`` is a
    plain bool the driver's consumer loop polls between messages to stop promptly.
    """

    def __init__(self) -> None:
        self.exc: BaseException | None = None
        self.tripped = False

    def trip(self, exc: BaseException) -> None:
        if self.exc is None:  # keep the first — the real cause
            self.exc = exc
        self.tripped = True


@dataclass
class _ToolBridge:
    """One Manifold-backed tool: its SDK-facing schema + a sync call into ``env``."""

    name: str
    description: str
    schema: dict[str, Any]
    call: Callable[[dict[str, Any]], dict[str, Any]]


def _bridge_call(
    env: ToolEnv, name: str, args: dict[str, Any], abort: _AbortBox
) -> dict[str, Any]:
    """Route one SDK tool call through Manifold's ``env`` and shape the MCP result.

    ``ToolError`` -> a catchable ``is_error`` result (the agent may retry). Anything else,
    crucially ``BudgetExceeded`` (a ``BaseException`` the SDK would swallow), is stashed on
    ``abort`` and returned as an error result so the CLI unblocks; the driver re-raises it.
    """
    try:
        value = env.call(name, **args)
    except ToolError as exc:  # injected fault the agent is meant to see and can recover from
        return {"content": [{"type": "text", "text": f"tool error: {exc}"}], "is_error": True}
    except BaseException as exc:  # BudgetExceeded (or a genuine bug) - never let it vanish
        abort.trip(exc)
        return {"content": [{"type": "text", "text": f"aborted: {exc}"}], "is_error": True}
    return {"content": [{"type": "text", "text": str(value)}]}


# A driver runs the (real or scripted) agent over the bridges and returns its final text.
Driver = Callable[[str, "list[_ToolBridge]", str, str, _AbortBox], str]


class SdkAgent:
    """Manifold ``Agent`` that runs a ``claude-agent-sdk`` agent against a mocked env.

    Parameterised by the wrapped agent's config (tools, system prompt, model) so a user
    verifies *their* SDK agent by describing it here — the tool *implementations* are
    swapped for Manifold's env, everything else is the agent's own. Pass ``driver`` to
    inject a stand-in for offline tests; the default drives the real SDK.
    """

    def __init__(
        self,
        agent_id: str = "sdk.search",
        *,
        tools: list[tuple[str, str, dict[str, Any]]] | None = None,
        system_prompt: str = SYSTEM,
        model: str = _SDK_MODEL,
        driver: Driver | None = None,
    ) -> None:
        self.id = agent_id
        self._tools = tools or [("search", "Search the web for information.", {"query": str})]
        self._system_prompt = system_prompt
        self._model = model
        self._driver = driver

    def run(self, task: Any, env: ToolEnv, budget: Budgets) -> Any:
        abort = _AbortBox()
        bridges = [
            _ToolBridge(
                name=name,
                description=desc,
                schema=schema,
                call=lambda args, _n=name: _bridge_call(env, _n, args, abort),
            )
            for name, desc, schema in self._tools
        ]
        driver = self._driver or _live_driver
        final = driver(str(task), bridges, self._model, self._system_prompt, abort)
        if abort.exc is not None:  # re-raise BudgetExceeded etc. OUTSIDE the SDK machinery
            raise abort.exc
        return final


# --- Live driver: the real claude-agent-sdk ----------------------------------


def _live_driver(
    prompt: str, bridges: list[_ToolBridge], model: str, system_prompt: str, abort: _AbortBox
) -> str:
    """Run the wrapped agent on the real SDK (bundled ``claude`` CLI). Fails fast, before
    anything runs, if the key or the package is missing."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError(
            "SdkAgent needs ANTHROPIC_API_KEY and `pip install \"manifold-cov[sdk]\"` to run "
            "live. For an offline demo, run `python examples/sdk_agent.py` (scripted)."
        )
    try:
        import claude_agent_sdk  # noqa: F401  # lazy: keep the module importable without it
    except ImportError as exc:
        raise RuntimeError(
            "the `claude-agent-sdk` package is not installed - run "
            "`pip install \"manifold-cov[sdk]\"` to enable live runs."
        ) from exc

    import asyncio

    os.environ.setdefault("CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK", "1")  # one less subprocess/run
    return asyncio.run(_consume(prompt, bridges, model, system_prompt, abort))


async def _consume(
    prompt: str, bridges: list[_ToolBridge], model: str, system_prompt: str, abort: _AbortBox
) -> str:
    from contextlib import aclosing

    from claude_agent_sdk import (  # type: ignore[import-not-found]
        ClaudeAgentOptions,
        ResultMessage,
        create_sdk_mcp_server,
        query,
        tool,
    )

    sdk_tools = []
    for bridge in bridges:

        async def handler(args: dict[str, Any], _b: _ToolBridge = bridge) -> dict[str, Any]:
            return _b.call(args)  # sync bridge; never raises (it stashes on abort)

        sdk_tools.append(tool(bridge.name, bridge.description, bridge.schema)(handler))

    server = create_sdk_mcp_server(name=_SERVER, version="1.0.0", tools=sdk_tools)
    options = ClaudeAgentOptions(
        tools=[],  # no built-in tools: the agent may use ONLY the manifold tools
        mcp_servers={_SERVER: server},
        allowed_tools=[f"mcp__{_SERVER}__{b.name}" for b in bridges],  # pre-approved, no prompt
        strict_mcp_config=True,  # ignore any ambient .mcp.json / user MCP config
        setting_sources=[],  # SDK isolation: no user/project/local settings, no CLAUDE.md
        system_prompt=system_prompt,
        model=model,
        max_turns=_MAX_TURNS,
        permission_mode="dontAsk",  # deny anything not pre-approved; never hang on a prompt
    )

    final = ""
    async with aclosing(query(prompt=prompt, options=options)) as stream:
        async for msg in stream:
            if isinstance(msg, ResultMessage):
                if msg.is_error:  # auth/API failure surfaces here - make it a crash, not ""
                    errs = getattr(msg, "errors", None) or msg.result or "unknown SDK error"
                    raise RuntimeError(f"claude-agent-sdk run failed: {errs}")
                final = msg.result or ""
            if abort.tripped:  # budget blown mid-run: stop; aclosing tears down the CLI child
                break
    return final


# --- Offline scripted stand-in -----------------------------------------------
# A stubborn agent that retries the first tool forever — the worst case the harness must
# bound. Drives the exact same bridge with no key and no subprocess.


def _scripted_driver(
    prompt: str, bridges: list[_ToolBridge], model: str, system_prompt: str, abort: _AbortBox
) -> str:
    bridge = bridges[0]
    while not abort.tripped:  # trips when env.call raises BudgetExceeded through the bridge
        bridge.call({"query": prompt})
    return ""


# --- Example wiring (the AGENTS / SPACE / MODEL contract) ---------------------

AGENTS = {"sdk.search": SdkAgent()}

_TASKS = ["weather in Paris", "stock price AAPL", "latest CPI figure", "who won the 2018 final"]


def make_scenario(seed: int) -> Scenario:
    """A persistent `search` outage — the stress condition for a retry-prone agent.
    (With a `--spec`/the module's SPACE, the richer generated space is used instead.)"""
    rng = random.Random(seed)
    outage = ToolMock(tool="search", responses=["result"], faults=[Fault(at_call=-1, kind="error")])
    return Scenario(
        seed=seed, task=rng.choice(_TASKS), mocks=[outage], budgets=Budgets(max_steps=12)
    )


def _search_fsm_symbols(t: Trace) -> list[str]:
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


SPACE = ScenarioSpace(
    tasks=_TASKS,
    tools=[
        ToolSpec(name="search", responses=["result-A", "result-B"], faults=["error", "timeout"])
    ],
    fault_rate=0.5,
    persistent_prob=0.5,
    budget_choices=[Budgets(max_steps=12), Budgets(max_steps=6)],
)

# The CLI resolves `MODEL: CoverageModel` (the SDK model string above is private `_SDK_MODEL`).
MODEL = CoverageModel(
    coverpoints=[
        n_tool_calls_cp(),
        terminal_reason_cp(),
        fault_seen_cp(),
        tool_called_cp(["search"]),
    ],
    transitions=[
        TransitionCoverpoint(
            "tool_fsm",
            _search_fsm_symbols,
            [
                ("START", "search:ok"),
                ("START", "search:err"),
                ("search:err", "search:err"),
                ("search:err", "search:ok"),
                ("search:ok", "END"),
                ("search:err", "END"),
            ],
        )
    ],
    crosses=[Cross("reason_x_ncalls", "terminal_reason", "n_tool_calls")],
)

INVARIANTS = [terminates_within_budget, no_infinite_retry()]

RECORDED = Path(__file__).resolve().parent / "recorded" / "sdk_search.jsonl"


def _record_or_refuse(trace: Trace, reason: str) -> None:
    """Overwrite the committed fixture only if the live trace matches the graceful-recovery
    contract the demo + tests assert (``completed`` with >=2 tool calls). If the model
    behaved differently — answered without searching, or blew the budget — that is a real
    change: write a side copy for inspection, leave the committed fixture UNCHANGED, and
    exit non-zero so a non-representative trace can't be committed silently (the crash case
    is handled earlier). This closes the 'green-looking degenerate re-record' footgun."""
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


def _print_verdict(trace: Trace) -> None:
    from manifold.invariants import no_infinite_retry as _nir
    from manifold.invariants import terminates_within_budget as _twb

    terminal = trace.terminal
    reason = terminal.reason if terminal else "?"
    print(f"  {len(trace.tools_called())} search calls, terminal reason = {reason}")
    for name, inv in [("terminates_within_budget", _twb), ("no_infinite_retry", _nir())]:
        v = inv(trace)
        detail = f" - {v.detail}" if v else ""
        print(f"  {'FAIL' if v else 'PASS'} {name}{detail}")


def _demo(live: bool = False) -> None:
    """Offline: drive the scripted worst case and contrast it with the committed real-SDK
    fixture (never writing it). ``live=True``: run the real SDK and re-record the fixture,
    guarded so a crashed run can't clobber it."""
    from manifold.harness import run
    from manifold.trace import AgentError

    agent = SdkAgent() if live else SdkAgent(driver=_scripted_driver)
    trace = run(agent, make_scenario(7))

    terminal = trace.terminal
    reason = terminal.reason if terminal else "?"
    if reason == "error":  # the run crashed (bad key, CLI failure) - keep the good fixture
        err = next((e for e in trace.events if isinstance(e, AgentError)), None)
        detail = f"{err.etype}: {err.message}" if err else "unknown error"
        print(f"error: run crashed ({detail})")
        print(f"  {RECORDED} left untouched - fix the cause and re-run")
        raise SystemExit(3)

    source = f"live SDK: {_SDK_MODEL}" if live else "offline scripted stand-in (worst case)"
    print(f"Manifold - agent={agent.id} - seed=0x7 ({source})")
    _print_verdict(trace)

    if live:
        _record_or_refuse(trace, reason)
    elif RECORDED.exists():
        print(f"Committed real-SDK fixture ({_SDK_MODEL}, recorded live):")
        _print_verdict(Trace.load_jsonl(RECORDED))
        print("  re-record: python examples/sdk_agent.py --live  (needs ANTHROPIC_API_KEY)")
    print("  reproduce: manifold repro 7 examples/sdk_agent.py --agent sdk.search")


if __name__ == "__main__":
    try:
        _demo(live="--live" in sys.argv[1:])
    except RuntimeError as exc:  # missing key / missing package - clean message, no traceback
        print(f"error: {exc}")
        raise SystemExit(2) from None
