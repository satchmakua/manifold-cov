"""Harness — runs an agent against a scenario and records a Trace (DESIGN.md §6.1).

The harness intercepts every tool call to serve the scenario's seed-resolved mock
(injecting faults), meters budgets, and emits the ``Trace``. Two deliberate design
points:

* ``BudgetExceeded`` inherits ``BaseException`` (not ``Exception``) so an agent's
  ``except Exception`` cannot swallow budget enforcement — "retries forever" is made
  observable and bounded rather than a hang.
* ``ToolError`` inherits ``Exception`` and *is* meant to be catchable by agents: a
  robust agent recovers from it; a buggy one may retry it forever.
"""

from __future__ import annotations

from typing import Any

from manifold.agent import Agent
from manifold.scenario import Scenario
from manifold.trace import (
    AgentError,
    AgentOutput,
    StateSnapshot,
    Terminal,
    TerminalReason,
    ToolCall,
    ToolResult,
    Trace,
)


class BudgetExceeded(BaseException):
    """A budget was hit. Non-swallowable by design (see module docstring)."""

    def __init__(self, reason: TerminalReason) -> None:
        super().__init__(reason)
        self.reason: TerminalReason = reason


class ToolError(Exception):
    """An injected tool failure. Catchable by agents — that's the point."""


class ToolTimeout(ToolError):
    """An injected timeout (a kind of tool failure)."""


class _RecordingEnv:
    """Concrete ``ToolEnv`` that records events, meters budgets, and faults calls."""

    def __init__(self, scenario: Scenario) -> None:
        self._scn = scenario
        self.events: list[Any] = []
        self.step = 0
        self.cost = 0.0
        self._sim_ms = 0  # simulated wall-clock: latency faults advance it (no real sleeping)
        self._call_counts: dict[str, int] = {}

    def wall_ms(self) -> int:
        # Simulated, not real: keeps the whole environment deterministic (ADR-0002) and
        # makes the `timeout` terminal reachable via accumulated latency faults.
        return self._sim_ms

    def _check_budget(self) -> None:
        b = self._scn.budgets
        if self.step >= b.max_steps:
            raise BudgetExceeded("budget_steps")
        if self.cost >= b.max_cost:
            raise BudgetExceeded("budget_cost")
        if self.wall_ms() >= b.wall_ms:
            raise BudgetExceeded("timeout")

    def state(self, label: str, /, **data: Any) -> None:
        self.events.append(StateSnapshot(step=self.step, label=label, data=data))

    def charge(self, amount: float, /, **data: Any) -> None:
        """Bill real spend the harness can't observe (LLM tokens/dollars burned inside the
        agent's loop) against the cost budget, and record it. Checked *after* charging, so
        an agent that blows the budget on model spend alone is still stopped — the tool-call
        meter can't see that."""
        self.cost += float(amount)
        self.events.append(
            StateSnapshot(step=self.step, label="charge", data={"amount": amount, **data})
        )
        b = self._scn.budgets
        if self.cost >= b.max_cost:
            raise BudgetExceeded("budget_cost")

    def call(self, tool: str, /, **args: Any) -> Any:
        self._check_budget()
        self.step += 1
        self.cost += 1.0
        idx = self._call_counts.get(tool, 0)
        self._call_counts[tool] = idx + 1
        self.events.append(ToolCall(step=self.step, tool=tool, args=args))

        mock = self._scn.mock_for(tool)
        fault = mock.fault_for(idx) if mock else None

        if fault is not None and fault.kind in ("error", "timeout"):
            self.events.append(
                ToolResult(step=self.step, tool=tool, ok=False, fault=fault.kind)
            )
            if fault.kind == "timeout":
                raise ToolTimeout(f"{tool} timed out")
            raise ToolError(f"{tool} failed")

        value = mock.response_for(idx) if mock else None
        latency = 0
        if fault is not None and fault.kind == "latency":
            latency = int(fault.detail) if fault.detail else 50
            self._sim_ms += latency  # advance the simulated clock toward the wall budget
        if fault is not None and fault.kind == "garbage":
            value = fault.detail if fault.detail is not None else "\x00<garbage>\x00"

        self.events.append(
            ToolResult(
                step=self.step,
                tool=tool,
                ok=True,
                value=value,
                fault=fault.kind if fault is not None else None,
                latency_ms=latency,
            )
        )
        return value


def run(agent: Agent, scenario: Scenario, repeat: int = 0) -> Trace:
    """Run ``agent`` against ``scenario`` once and return the recorded ``Trace``."""
    env = _RecordingEnv(scenario)
    reason: TerminalReason
    try:
        out = agent.run(scenario.task, env, scenario.budgets)
        env.events.append(AgentOutput(step=env.step, text=str(out), payload=out))
        reason = "completed"
    except BudgetExceeded as exc:
        reason = exc.reason
    except Exception as exc:  # the agent crashed (e.g. an unhandled ToolError)
        env.events.append(
            AgentError(step=env.step, etype=type(exc).__name__, message=str(exc))
        )
        reason = "error"

    env.events.append(
        Terminal(
            step=env.step,
            reason=reason,
            steps_used=env.step,
            cost_used=env.cost,
            wall_ms=env.wall_ms(),
        )
    )
    return Trace(
        seed=scenario.seed,
        scenario_id=scenario.scenario_id(),
        agent_id=agent.id,
        repeat=repeat,
        events=env.events,
    )
