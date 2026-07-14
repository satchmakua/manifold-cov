"""W3: the wild-framework adapters, tested offline — each framework's own agent loop is
driven by its own fake/scripted model (zero API calls) to prove, per framework:

* the wiring: a tool call flows through Manifold's ``env`` and the answer comes back;
* the ``ToolError`` contract: an injected fault becomes an observation, not a crash;
* the load-bearing invariant: ``BudgetExceeded`` (a ``BaseException``) crosses the
  framework's loop intact, so the harness records a budget terminal;
* (pydantic-ai) the crash-not-degrade default: retry exhaustion under a persistent
  outage raises ``UnexpectedModelBehavior`` — ``no_agent_crash`` flags it, offline and
  deterministically.

The frameworks ARE installed in this venv (the `[wild]` extra); these tests use their
fake-model seams, so they run with no key and no network.
"""

from __future__ import annotations

from types import ModuleType
from typing import Any

from manifold.harness import run
from manifold.invariants import no_agent_crash
from manifold.scenario import Budgets, Fault, Scenario, ToolMock
from manifold.trace import Terminal


def _clean(max_steps: int = 10) -> Scenario:
    return Scenario(
        seed=1,
        task="q",
        mocks=[
            ToolMock(tool="search", responses=["hit-A"]),
            ToolMock(tool="fetch", responses=["page"]),
        ],
        budgets=Budgets(max_steps=max_steps),
    )


def _outage(max_steps: int = 10) -> Scenario:
    return Scenario(
        seed=1,
        task="q",
        mocks=[
            ToolMock(tool="search", faults=[Fault(at_call=-1, kind="error")]),
            ToolMock(tool="fetch", responses=["page"]),
        ],
        budgets=Budgets(max_steps=max_steps),
    )


def test_module_exposes_the_contract(wild: ModuleType) -> None:
    assert set(wild.AGENTS) == {"wild.langgraph", "wild.smolagents", "wild.pydantic_ai"}
    assert len(wild.INVARIANTS) == 7  # the full starter library judges the hunt


# --- LangGraph -----------------------------------------------------------------


def _lg_call(query: str = "x") -> Any:
    from langchain_core.messages import AIMessage

    return AIMessage(
        content="",
        tool_calls=[{"name": "search", "args": {"query": query}, "id": "c1", "type": "tool_call"}],
    )


def _lg_bind(fake: Any) -> Any:
    # Fakes don't implement bind_tools; pre-bind so create_react_agent skips re-binding.
    return fake.bind(tools=[{"name": "search"}, {"name": "fetch"}])


def test_langgraph_routes_tools_through_env(wild: ModuleType) -> None:
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
    from langchain_core.messages import AIMessage

    fake = GenericFakeChatModel(messages=iter([_lg_call(), AIMessage(content="The answer.")]))
    trace = run(wild.LangGraphAgent(model=_lg_bind(fake)), _clean())
    assert trace.tools_called() == ["search"]
    assert isinstance(trace.terminal, Terminal)
    assert trace.terminal.reason == "completed"


def test_langgraph_tool_error_becomes_observation_not_crash(wild: ModuleType) -> None:
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
    from langchain_core.messages import AIMessage

    fake = GenericFakeChatModel(messages=iter([_lg_call(), AIMessage(content="It failed.")]))
    trace = run(wild.LangGraphAgent(model=_lg_bind(fake)), _outage())
    assert isinstance(trace.terminal, Terminal)
    assert trace.terminal.reason == "completed"  # ToolError fed back, loop continued


def test_budget_exceeded_crosses_langgraph(wild: ModuleType) -> None:
    from collections.abc import Iterator

    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
    from langchain_core.messages import AIMessage

    def _retry_forever() -> Iterator[AIMessage]:
        # Fresh message + tool_call ids each turn (LangGraph's add_messages reducer
        # de-duplicates by id, so a cycled identical message would derange the loop).
        i = 0
        while True:
            i += 1
            yield AIMessage(
                content="",
                id=f"ai{i}",
                tool_calls=[
                    {"name": "search", "args": {"query": "x"}, "id": f"c{i}", "type": "tool_call"}
                ],
            )

    fake = GenericFakeChatModel(messages=_retry_forever())
    trace = run(wild.LangGraphAgent(model=_lg_bind(fake)), _clean(max_steps=3))
    assert isinstance(trace.terminal, Terminal)
    assert trace.terminal.reason == "budget_steps"  # BaseException crossed the framework
    assert len(trace.tools_called()) == 3


# --- smolagents ------------------------------------------------------------------


def _smol_model(plan: list[tuple[str, dict[str, Any]]]) -> Any:
    from smolagents.models import (  # type: ignore[import-untyped]
        ChatMessage,
        ChatMessageToolCall,
        ChatMessageToolCallFunction,
        MessageRole,
        Model,
    )

    class _Scripted(Model):  # type: ignore[misc]  # smolagents ships no type stubs
        def __init__(self) -> None:
            super().__init__(model_id="scripted")
            self._i = 0

        def generate(self, messages: Any, **kwargs: Any) -> Any:
            name, args = plan[min(self._i, len(plan) - 1)]
            self._i += 1
            return ChatMessage(
                role=MessageRole.ASSISTANT,
                content=None,
                tool_calls=[
                    ChatMessageToolCall(
                        function=ChatMessageToolCallFunction(name=name, arguments=args),
                        id=f"c{self._i}",
                        type="function",
                    )
                ],
            )

    return _Scripted()


def test_smolagents_routes_tools_through_env(wild: ModuleType) -> None:
    model = _smol_model(
        [("search", {"query": "x"}), ("final_answer", {"answer": "The answer."})]
    )
    trace = run(wild.SmolAgent(model=model), _clean())
    assert trace.tools_called() == ["search"]
    assert isinstance(trace.terminal, Terminal)
    assert trace.terminal.reason == "completed"


def test_budget_exceeded_crosses_smolagents(wild: ModuleType) -> None:
    model = _smol_model([("search", {"query": "x"})])  # retries the tool forever
    trace = run(wild.SmolAgent(model=model), _clean(max_steps=3))
    assert isinstance(trace.terminal, Terminal)
    assert trace.terminal.reason == "budget_steps"
    assert len(trace.tools_called()) == 3


# --- pydantic-ai --------------------------------------------------------------------


def _pai_model(stubborn: bool) -> Any:
    from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
    from pydantic_ai.models.function import FunctionModel

    def script(messages: Any, info: Any) -> Any:
        n_responses = sum(1 for m in messages if isinstance(m, ModelResponse))
        if stubborn or n_responses == 0:
            return ModelResponse(parts=[ToolCallPart("search", {"query": "x"})])
        return ModelResponse(parts=[TextPart("The answer.")])

    return FunctionModel(script)


def test_pydantic_ai_routes_tools_through_env(wild: ModuleType) -> None:
    trace = run(wild.PydanticAIAgent(model=_pai_model(stubborn=False)), _clean())
    assert trace.tools_called() == ["search"]
    assert isinstance(trace.terminal, Terminal)
    assert trace.terminal.reason == "completed"


def test_pydantic_ai_crashes_on_persistent_outage(wild: ModuleType) -> None:
    # The framework's documented default, demonstrated offline: retry exhaustion under a
    # persistent tool outage raises UnexpectedModelBehavior — the run CRASHES rather than
    # degrading. Manifold records an `error` terminal and `no_agent_crash` flags it.
    trace = run(wild.PydanticAIAgent(model=_pai_model(stubborn=True)), _outage())
    assert isinstance(trace.terminal, Terminal)
    assert trace.terminal.reason == "error"
    v = no_agent_crash(trace)
    assert v is not None
    assert "UnexpectedModelBehavior" in v.detail


def test_budget_exceeded_crosses_pydantic_ai(wild: ModuleType) -> None:
    trace = run(wild.PydanticAIAgent(model=_pai_model(stubborn=True)), _clean(max_steps=3))
    assert isinstance(trace.terminal, Terminal)
    assert trace.terminal.reason == "budget_steps"
    assert len(trace.tools_called()) == 3


# --- The wild-caught bug (committed live trace) --------------------------------------


def test_recorded_wild_bug_shows_the_retry_storm(wild: ModuleType) -> None:
    # The first bug caught in an agent Manifold's author didn't write: live haiku inside
    # smolagents' ToolCallingAgent retried the dead `search` tool 5x consecutively
    # (rephrasing each time) and burned the whole 12-step budget. Both invariants fail.
    from manifold.invariants import no_infinite_retry, terminates_within_budget
    from manifold.trace import Trace

    trace = Trace.load_jsonl(wild.RECORDED_BUG)
    assert trace.agent_id == "wild.smolagents"
    assert isinstance(trace.terminal, Terminal)
    assert trace.terminal.reason == "budget_steps"
    assert len(trace.tools_called()) == 12  # every budgeted step spent on tool calls
    v = no_infinite_retry()(trace)
    assert v is not None and "search" in v.detail
    assert terminates_within_budget(trace) is not None
