"""Constrained-random scenario generation (DESIGN.md §6.4).

You declare a ``ScenarioSpace`` — tasks, per-tool response pools, the eligible fault
menu, budget choices, and constraints — and ``sample(space, seed)`` resolves it to a
concrete ``Scenario`` by a **pure function of the seed** (``random.Random(seed)`` is
the only entropy). Same seed -> identical scenario; that is the reproduction contract
(ADR-0002). The space is code-as-config (it holds callables / pools) so it is a
dataclass, never serialised; the ``Scenario`` it produces is pure pydantic data.

M3 adds coverage-directed biasing on top of this (nudging the weighted draws toward
unhit bins); M2 samples uniformly.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from manifold.scenario import Budgets, Fault, FaultKind, Scenario, ToolMock


@dataclass(frozen=True)
class ToolSpec:
    name: str
    responses: list[Any]  # pool a response is drawn from
    faults: list[FaultKind] = field(default_factory=list)  # fault kinds eligible for this tool


@dataclass
class ScenarioSpace:
    tasks: list[Any]
    tools: list[ToolSpec]
    fault_rate: float = 0.3  # P(a fault-eligible tool gets a fault this scenario)
    persistent_prob: float = 0.5  # given a fault, P(it's a persistent outage vs a single-call blip)
    budget_choices: list[Budgets] = field(default_factory=lambda: [Budgets(max_steps=15)])
    constraints: list[Callable[[Scenario], bool]] = field(default_factory=list)
    max_resamples: int = 50  # tries to satisfy constraints before giving up


def _fault_detail(kind: FaultKind, rng: random.Random) -> Any:
    if kind == "latency":
        return rng.randint(50, 800)  # ms (recorded, not slept — keeps runs fast/deterministic)
    return None  # error/timeout carry no detail; garbage uses the harness default value


def _draw(space: ScenarioSpace, rng: random.Random, seed: int) -> Scenario:
    task = rng.choice(space.tasks) if space.tasks else None
    mocks: list[ToolMock] = []
    for tool in space.tools:
        responses = [rng.choice(tool.responses)] if tool.responses else []
        faults: list[Fault] = []
        if tool.faults and rng.random() < space.fault_rate:
            kind: FaultKind = rng.choice(tool.faults)
            at_call = -1 if rng.random() < space.persistent_prob else 0  # persistent vs transient
            faults = [Fault(at_call=at_call, kind=kind, detail=_fault_detail(kind, rng))]
        mocks.append(ToolMock(tool=tool.name, responses=responses, faults=faults))
    budgets = rng.choice(space.budget_choices)
    return Scenario(seed=seed, task=task, mocks=mocks, budgets=budgets)


def sample(space: ScenarioSpace, seed: int) -> Scenario:
    """Resolve ``space`` to a concrete ``Scenario`` deterministically from ``seed``.
    Re-draws (advancing the same RNG) until the constraints hold, so the result stays
    a pure function of the seed."""
    rng = random.Random(seed)
    scenario = _draw(space, rng, seed)
    for _ in range(space.max_resamples):
        if all(c(scenario) for c in space.constraints):
            return scenario
        scenario = _draw(space, rng, seed)
    return scenario  # constraints unsatisfiable within budget — return the last draw
