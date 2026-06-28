"""Scenarios — the seed-resolved stimulus handed to the harness (DESIGN.md §4.3).

A ``Scenario`` is pure data: a task input, per-tool (optionally stateful) mock
responses, injected faults, and budgets. In v1 the harness reproduces a scenario
exactly from its seed, so a seed is a complete reproduction handle for the
*environment*. The constrained-random generator that samples scenarios from a
declared space arrives in M2 (DESIGN.md §6.4); for M0 the example modules build
scenarios by hand from the seed.
"""

from __future__ import annotations

import hashlib
from typing import Any, Literal

from pydantic import BaseModel, Field

FaultKind = Literal["error", "timeout", "garbage", "latency"]


class Fault(BaseModel):
    at_call: int  # which invocation of the tool (0-based); -1 = every call (persistent outage)
    kind: FaultKind
    detail: Any = None  # latency ms, or the garbage value to return


class ToolMock(BaseModel):
    tool: str
    responses: list[Any] = Field(default_factory=list)  # cycled by call index
    faults: list[Fault] = Field(default_factory=list)

    def response_for(self, call_index: int) -> Any:
        if not self.responses:
            return None
        return self.responses[call_index % len(self.responses)]

    def fault_for(self, call_index: int) -> Fault | None:
        for f in self.faults:
            if f.at_call == call_index or f.at_call == -1:
                return f
        return None


class Budgets(BaseModel):
    max_steps: int = 30
    max_cost: float = 1_000_000.0  # tokens or $; the harness meters 1.0/tool-call in v1
    wall_ms: int = 30_000


class Scenario(BaseModel):
    seed: int
    task: Any = None
    mocks: list[ToolMock] = Field(default_factory=list)
    budgets: Budgets = Field(default_factory=Budgets)

    def mock_for(self, tool: str) -> ToolMock | None:
        for m in self.mocks:
            if m.tool == tool:
                return m
        return None

    def scenario_id(self) -> str:
        """Stable 12-hex-char hash of the resolved scenario (its identity)."""
        payload = self.model_dump_json().encode("utf-8")
        return hashlib.sha256(payload).hexdigest()[:12]
