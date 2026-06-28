"""Structured execution traces — the record of one agent run (DESIGN.md §4.1).

A ``Trace`` is the totally-ordered list of events produced by running one agent
against one scenario. It is pure data: it round-trips to JSONL for replay, and is
the single artifact that both the coverage model (later milestones) and the
invariants (§6.5) evaluate against.

Implementation note: the event discriminator ``kind`` uses plain string literals
rather than an Enum — the most robust pydantic v2 discriminated-union pattern, and
what serialises cleanly to/from JSONL.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, TypeAdapter

TerminalReason = Literal["completed", "budget_steps", "budget_cost", "timeout", "error"]


class ToolCall(BaseModel):
    kind: Literal["tool_call"] = "tool_call"
    step: int
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)


class ToolResult(BaseModel):
    kind: Literal["tool_result"] = "tool_result"
    step: int
    tool: str
    ok: bool
    value: Any = None
    fault: str | None = None  # which injected fault fired, if any
    latency_ms: int = 0


class StateSnapshot(BaseModel):
    kind: Literal["state"] = "state"
    step: int
    label: str  # e.g. "planning", "retrying"
    data: dict[str, Any] = Field(default_factory=dict)


class AgentOutput(BaseModel):
    kind: Literal["output"] = "output"
    step: int
    text: str | None = None
    payload: Any = None


class AgentError(BaseModel):
    kind: Literal["error"] = "error"
    step: int
    etype: str
    message: str


class Terminal(BaseModel):
    kind: Literal["terminal"] = "terminal"
    step: int
    reason: TerminalReason
    steps_used: int
    cost_used: float
    wall_ms: int


Event = Annotated[
    ToolCall | ToolResult | StateSnapshot | AgentOutput | AgentError | Terminal,
    Field(discriminator="kind"),
]

_EVENT_ADAPTER: TypeAdapter[Event] = TypeAdapter(Event)


class Trace(BaseModel):
    seed: int  # the scenario seed — the reproduction key
    scenario_id: str  # stable hash of the resolved Scenario
    agent_id: str  # adapter identity (e.g. "toy.retry_forever")
    repeat: int = 0  # which of K repeats-per-seed this is
    events: list[Event] = Field(default_factory=list)

    @property
    def terminal(self) -> Terminal | None:
        last = self.events[-1] if self.events else None
        return last if isinstance(last, Terminal) else None

    def tools_called(self) -> list[str]:
        return [e.tool for e in self.events if isinstance(e, ToolCall)]

    def dump_jsonl(self, path: str | Path) -> None:
        """One header line + one JSON object per event."""
        header = {
            "seed": self.seed,
            "scenario_id": self.scenario_id,
            "agent_id": self.agent_id,
            "repeat": self.repeat,
        }
        with Path(path).open("w", encoding="utf-8") as fh:
            fh.write(json.dumps(header) + "\n")
            for ev in self.events:
                fh.write(ev.model_dump_json() + "\n")

    @classmethod
    def load_jsonl(cls, path: str | Path) -> Trace:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
        header = json.loads(lines[0])
        events = [_EVENT_ADAPTER.validate_json(ln) for ln in lines[1:] if ln.strip()]
        return cls(events=events, **header)
