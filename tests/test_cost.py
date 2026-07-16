"""W5: the real cost model. Previously `max_cost` was a proxy — the harness metered a flat
1.0 per tool call, because tokens are burned *inside* the agent's loop where the harness
can't see them. An agent could make one tool call and five hundred model turns and the cost
budget would never notice.

`env.charge()` closes that: an adapter reports the real token/dollar spend of each model
turn, so `Budgets.max_cost` means money and spend is bounded *between* tool calls too.

(The review's literal suggestion, "cost grows monotonically", is deliberately not built: a
counter cannot decrease, so the check could never fail. The meter is the real fix; with
`Terminal.cost_used` now carrying dollars, a spec can express whatever cost property it
actually wants.)
"""

from __future__ import annotations

from types import ModuleType
from typing import Any

from manifold.harness import run
from manifold.scenario import Budgets, Scenario, ToolMock
from manifold.trace import StateSnapshot, Terminal


def _scn(max_cost: float = 100.0, max_steps: int = 10) -> Scenario:
    return Scenario(
        seed=1,
        task="q",
        mocks=[ToolMock(tool="search", responses=["hit"])],
        budgets=Budgets(max_steps=max_steps, max_cost=max_cost),
    )


class _Charging:
    """An agent that reports model spend the way a real adapter does."""

    id = "test.charging"

    def __init__(self, per_turn: float, turns: int, call_tool: bool = True) -> None:
        self._per_turn, self._turns, self._call_tool = per_turn, turns, call_tool

    def run(self, task: Any, env: Any, budget: Budgets) -> Any:
        for _ in range(self._turns):
            env.charge(self._per_turn, input_tokens=100, output_tokens=20)
            if self._call_tool:
                env.call("search", q="x")
        return "done"


def test_charge_adds_real_spend_to_the_cost_meter() -> None:
    trace = run(_Charging(per_turn=0.25, turns=2), _scn())
    assert isinstance(trace.terminal, Terminal)
    assert trace.terminal.reason == "completed"
    # 2 charges of 0.25 + 2 tool calls at the built-in 1.0/call proxy
    assert trace.terminal.cost_used == 2.5


def test_model_spend_alone_trips_the_cost_budget() -> None:
    # THE gap this closes: no tool calls at all, pure model spend — previously invisible to
    # the budget, so an agent could burn unlimited money between tool calls.
    trace = run(_Charging(per_turn=0.5, turns=100, call_tool=False), _scn(max_cost=2.0))
    assert isinstance(trace.terminal, Terminal)
    assert trace.terminal.reason == "budget_cost"
    assert trace.tools_called() == []  # not one tool call — and it was still stopped
    assert trace.terminal.cost_used >= 2.0


def test_charge_is_recorded_in_the_trace() -> None:
    trace = run(_Charging(per_turn=0.75, turns=1), _scn())
    charges = [
        e for e in trace.events if isinstance(e, StateSnapshot) and e.label == "charge"
    ]
    assert len(charges) == 1
    assert charges[0].data["amount"] == 0.75
    assert charges[0].data["input_tokens"] == 100  # the adapter's usage detail survives


def test_charging_is_optional_and_changes_nothing_for_agents_that_dont(
    toy: ModuleType, error_seed: int
) -> None:
    # Backwards compatibility: agents that never charge meter exactly as before (1.0/call).
    trace = run(toy.AGENTS["toy.retry_forever"], toy.make_scenario(error_seed))
    assert isinstance(trace.terminal, Terminal)
    assert trace.terminal.cost_used == float(len(trace.tools_called()))


def test_claude_adapter_charges_real_token_usage(claude: ModuleType) -> None:
    # The canonical adapter reports the model turn's *real* usage, priced at list rate.
    class _Usage:
        input_tokens, output_tokens = 1_000_000, 1_000_000

    class _Block:
        type, text = "text", "the answer"

    class _Resp:
        content, stop_reason, usage = [_Block()], "end_turn", _Usage()

    class _Messages:
        def create(self, **_: Any) -> Any:
            return _Resp()

    class _Client:
        def __init__(self) -> None:
            self.messages = _Messages()

    trace = run(claude.ClaudeSearchAgent(client=_Client()), _scn())
    assert isinstance(trace.terminal, Terminal)
    # 1M in @ $1/M + 1M out @ $5/M = $6.00, and no tool calls were made
    assert trace.terminal.cost_used == 6.0


def test_claude_adapter_is_silent_when_the_client_reports_no_usage(claude: ModuleType) -> None:
    # The offline scripted stand-in has no `.usage`; charging must simply not happen.
    trace = run(claude.ClaudeSearchAgent(client=claude.ScriptedClient()), claude.make_scenario(7))
    assert isinstance(trace.terminal, Terminal)
    assert trace.terminal.reason == "budget_steps"  # unchanged: still the step budget
    assert trace.terminal.cost_used == float(len(trace.tools_called()))
