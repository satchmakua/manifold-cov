"""The hunt — verify agents built on *third-party frameworks*, unmodified (post-v1 W3).

Every bug Manifold caught before this module lived in an agent its author wrote to be
buggy. These three adapters point it at agent loops **other people** wrote — LangGraph's
prebuilt ReAct agent, HuggingFace smolagents' ``ToolCallingAgent``, and pydantic-ai's
``Agent`` — with tools bound to Manifold's ``env`` and each framework's error-handling /
retry / step defaults left intact. The full seven-check invariant library judges the traces.
The standing rule: honest outcomes only — if the animals are healthy, the artifact says so.

**On fairness (disclosed).** All three surface an injected ``ToolError`` to the model as an
observation through that framework's *intended* seam, ``BudgetExceeded`` crosses all three
untouched, and they run the identical scenario space + budgets. LangGraph and pydantic-ai
get the neutral ``SYSTEM`` prompt below (it says nothing about error-handling or giving up);
smolagents runs on its **own default** system prompt on purpose — that default contains the
retry coaching, which is the variable under study, so overriding it would suppress the very
behavior we're measuring. The clean controlled comparison is LangGraph vs smolagents (both
unlimited-retry, both errors-as-observations, same tasks), which isolates smolagents'
framework defaults as the cause. An adversarial review confirmed the setup is apples-to-apples.

Per-framework notes (facts verified against the installed source, not docs):

* **LangGraph** (`wild.langgraph`) — ``create_react_agent`` + ``ChatAnthropic``. The
  landmine: ``ToolNode``'s *default* error handling re-raises tool exceptions, so a plain
  ``ToolError`` would crash the run; ``handle_tool_errors=True`` makes it an error
  observation the model sees (the framework's own recommended resilient mode). Parallel
  tool calls are disabled (``parallel_tool_calls=False``) so the harness meters serially.
  ``recursion_limit=100`` keeps our budget the binding constraint. Nothing in its loop
  catches ``BaseException`` → ``BudgetExceeded`` crosses intact.
* **smolagents** (`wild.smolagents`) — ``ToolCallingAgent`` + ``LiteLLMModel``. The
  framework actively *coaches* retrying ("Now let's retry: take care not to repeat
  previous errors!") and forces a tool call every step (``tool_choice='required'``) —
  the most promising hunting ground for retry storms. ``max_tool_threads=1`` serializes
  tool execution. ``BudgetExceeded`` escapes ``agent.run()`` untouched (verified).
* **pydantic-ai** (`wild.pydantic_ai`) — ``Agent`` with ``@tool_plain(sequential=True)``.
  Its contract: a failed tool must raise ``ModelRetry``; when a tool exhausts its retry
  budget the framework raises ``UnexpectedModelBehavior`` — i.e. **a persistent tool
  outage crashes the run instead of degrading gracefully**. The adapter leaves that
  default posture intact; ``no_agent_crash`` judges it.

All framework imports are lazy (inside ``run``), so this module imports without any of
them installed; offline tests drive each loop with the framework's own fake/scripted
model. Live runs need ``ANTHROPIC_API_KEY`` + ``pip install "manifold-cov[wild]"``.
"""

from __future__ import annotations

import os
import warnings
from pathlib import Path
from typing import Any

from manifold.agent import ToolEnv
from manifold.generate import ScenarioSpace, ToolSpec
from manifold.harness import ToolError
from manifold.invariants import starter_library
from manifold.model import (
    CoverageModel,
    fault_by_tool_cp,
    fault_seen_cp,
    n_tool_calls_cp,
    terminal_reason_cp,
    tool_called_cp,
)
from manifold.scenario import Budgets

# create_react_agent emits a deprecation notice; suppress it once at import (thread-safe —
# no save/restore race, unlike a per-run warnings.catch_warnings() under --parallel).
warnings.filterwarnings("ignore", category=DeprecationWarning, module=r"langgraph.*")

_MODEL_ID = "claude-haiku-4-5"
SYSTEM = "You are a research assistant. Use the available tools to answer the user concisely."
TOOLS = ["search", "fetch"]


def _require_key(framework: str) -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError(
            f"the {framework} adapter needs ANTHROPIC_API_KEY (and `pip install "
            '"manifold-cov[wild]"`) to run live; offline tests inject a scripted model.'
        )


# --- LangGraph ----------------------------------------------------------------


class LangGraphAgent:
    """LangGraph's prebuilt ReAct agent, tools routed through Manifold's env.
    Pass ``model`` to inject a (pre-bound) fake chat model for offline tests."""

    id = "wild.langgraph"

    def __init__(self, model: Any | None = None) -> None:
        self._model = model

    def run(self, task: Any, env: ToolEnv, budget: Budgets) -> Any:
        from langchain_core.tools import tool
        from langgraph.prebuilt import ToolNode, create_react_agent

        @tool
        def search(query: str) -> str:
            """Search the web for information."""
            return str(env.call("search", query=query))

        @tool
        def fetch(url: str) -> str:
            """Fetch the contents of a URL."""
            return str(env.call("fetch", url=url))

        tools = [search, fetch]
        if self._model is not None:
            model = self._model  # injected fakes come pre-bound (bind_tools is abstract)
        else:
            _require_key("langgraph")
            from langchain_anthropic import ChatAnthropic

            # framework defaults kept (incl. max_retries=2 — model-API retries are
            # infrastructure, not agent behavior; overriding would fake crash findings)
            chat = ChatAnthropic(model=_MODEL_ID, timeout=60, max_tokens=1024)
            # serialize tool calls so the harness meters deterministically per call
            model = chat.bind_tools(tools, parallel_tool_calls=False)

        # handle_tool_errors=True: the framework's resilient mode — a raised ToolError
        # becomes an error observation. This is not a thumb on the scale: LangGraph's
        # *default* re-raises tool errors (would crash the run), so True *levels* it to the
        # same errors-as-observations baseline smolagents and pydantic-ai have by default.
        agent = create_react_agent(
            model, ToolNode(tools, handle_tool_errors=True), prompt=SYSTEM
        )
        result = agent.invoke(
            {"messages": [{"role": "user", "content": str(task)}]},
            config={"recursion_limit": 100},  # our Budgets stay the binding constraint
        )
        return str(result["messages"][-1].text)


# --- smolagents -----------------------------------------------------------------


class SmolAgent:
    """HF smolagents ``ToolCallingAgent``, tools routed through Manifold's env.
    Pass ``model`` to inject a scripted ``smolagents.models.Model`` for offline tests."""

    id = "wild.smolagents"

    def __init__(self, model: Any | None = None) -> None:
        self._model = model

    def run(self, task: Any, env: ToolEnv, budget: Budgets) -> Any:
        from smolagents import ToolCallingAgent, tool
        from smolagents.monitoring import LogLevel

        @tool
        def search(query: str) -> str:
            """Search the web for information.

            Args:
                query: The search query string.
            """
            return str(env.call("search", query=query))

        @tool
        def fetch(url: str) -> str:
            """Fetch the contents of a URL.

            Args:
                url: The URL to fetch.
            """
            return str(env.call("fetch", url=url))

        if self._model is not None:
            model = self._model
        else:
            _require_key("smolagents")
            import litellm
            from smolagents import LiteLLMModel

            litellm.suppress_debug_info = True  # kill the stderr banner litellm prints per error
            model = LiteLLMModel(model_id=f"anthropic/{_MODEL_ID}")

        agent = ToolCallingAgent(
            tools=[search, fetch],
            model=model,
            max_steps=30,  # above our budgets: Manifold's Budgets bind first
            max_tool_threads=1,  # serialize tool execution for deterministic metering
            verbosity_level=LogLevel.OFF,
        )
        return str(agent.run(str(task)))


# --- pydantic-ai ------------------------------------------------------------------


class PydanticAIAgent:
    """pydantic-ai ``Agent``, tools routed through Manifold's env. Its documented
    default is judged as-is: a tool that exhausts its retries raises
    ``UnexpectedModelBehavior`` — the run *crashes* under a persistent outage, which
    ``no_agent_crash`` flags. Pass ``model`` to inject TestModel/FunctionModel offline."""

    id = "wild.pydantic_ai"

    def __init__(self, model: Any | None = None) -> None:
        self._model = model

    def run(self, task: Any, env: ToolEnv, budget: Budgets) -> Any:
        from pydantic_ai import Agent, ModelRetry
        from pydantic_ai.usage import UsageLimits

        if self._model is None:
            _require_key("pydantic-ai")
        agent: Any = Agent(
            self._model or f"anthropic:{_MODEL_ID}",
            system_prompt=SYSTEM,
            retries=2,  # a modest, realistic per-tool retry budget
        )

        @agent.tool_plain(sequential=True)  # serialize: the harness meters per call
        def search(query: str) -> str:
            """Search the web for information."""
            try:
                return str(env.call("search", query=query))
            except ToolError as exc:  # the framework's contract for a failed tool
                raise ModelRetry(str(exc)) from exc

        @agent.tool_plain(sequential=True)
        def fetch(url: str) -> str:
            """Fetch the contents of a URL."""
            try:
                return str(env.call("fetch", url=url))
            except ToolError as exc:
                raise ModelRetry(str(exc)) from exc

        result = agent.run_sync(
            str(task), usage_limits=UsageLimits(request_limit=40)  # above our budgets
        )
        return str(result.output)


# --- The shared hunt space (AGENTS / SPACE / MODEL / INVARIANTS contract) ---------

AGENTS = {a.id: a for a in (LangGraphAgent(), SmolAgent(), PydanticAIAgent())}

_TASKS = [
    "weather in Paris",
    "stock price AAPL",
    "latest CPI figure",
    "who won the 2018 final",
    "define entropy",
]

# Budgets sit below every framework's own loop cap (recursion_limit=100, max_steps=30,
# request_limit=40), so Manifold's budget is the binding constraint; the tight-wall
# choice makes `timeout` reachable when latency faults accumulate.
_BUDGETS = [
    Budgets(max_steps=12, max_cost=100.0, wall_ms=30_000),
    Budgets(max_steps=6, max_cost=100.0, wall_ms=30_000),
    Budgets(max_steps=12, max_cost=100.0, wall_ms=600),
]

SPACE = ScenarioSpace(
    tasks=_TASKS,
    tools=[
        ToolSpec(
            name=t,
            responses=[f"{t}-result-A", f"{t}-result-B"],
            faults=["error", "timeout", "garbage", "latency"],
        )
        for t in TOOLS
    ],
    fault_rate=0.5,
    persistent_prob=0.5,
    budget_choices=_BUDGETS,
)

MODEL = CoverageModel(
    coverpoints=[
        tool_called_cp(TOOLS),
        fault_seen_cp(),
        fault_by_tool_cp(TOOLS),
        n_tool_calls_cp(),
        terminal_reason_cp(),
    ],
)

INVARIANTS = starter_library()  # all six checks — the widest generic meaning of FAIL

# The first wild-caught bug (2026-07-13): live claude-haiku-4-5 inside smolagents'
# ToolCallingAgent, seed 7 (persistent search outage). The framework's retry coaching +
# forced tool_choice pushed the model into a 5x consecutive retry storm ending in budget
# death — the same model gives up gracefully after ~2 retries in LangGraph, pydantic-ai,
# and the raw anthropic loop. Reproduce live: `manifold repro 7 examples/wild_agents.py
# --agent wild.smolagents` (flaky ~1/2 — that's the pass^k signal, run repeats).
RECORDED_BUG = Path(__file__).resolve().parent / "recorded" / "wild_smolagents_retry.jsonl"
