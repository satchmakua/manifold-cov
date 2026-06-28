"""The Agent and ToolEnv ports (DESIGN.md §6.1).

The agent contract is one method. The agent talks to the world *only* through the
``ToolEnv`` the harness hands it — every ``call`` is recorded and may be faulted,
and ``state`` lets the agent mark decision points the coverage model and invariants
can key on. The harness owns budgets and trace construction; the agent just does
the task.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from manifold.scenario import Budgets


class ToolEnv(Protocol):
    """Handed to the agent. Every call is recorded; faults may fire."""

    def call(self, tool: str, /, **args: Any) -> Any:
        """Invoke a tool. Returns the (possibly mocked) value, or raises a
        ``ToolError`` / ``ToolTimeout`` when a fault is injected."""
        ...

    def state(self, label: str, /, **data: Any) -> None:
        """Mark a decision point (e.g. "planning", "retrying"). Optional."""
        ...


class Agent(Protocol):
    """The thing under test. Anything with an ``id`` and this ``run`` is an agent."""

    id: str

    def run(self, task: Any, env: ToolEnv, budget: Budgets) -> Any:
        """Do ``task`` using only ``env`` for tools; return the final output."""
        ...
