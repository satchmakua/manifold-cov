"""Toy agents for Manifold — deterministic, some with planted reliability bugs.

This module is the M0 contract a Manifold-testable target exposes:

    AGENTS: dict[str, Agent]          # name -> agent instance
    make_scenario(seed: int) -> Scenario   # seed-resolved stimulus (pure)

Run it:

    manifold run examples/toy_agents.py --agent toy.retry_forever --scenarios 12
    manifold repro <seed> examples/toy_agents.py --agent toy.retry_forever
"""

from __future__ import annotations

import random
from typing import Any

from manifold.agent import ToolEnv
from manifold.harness import ToolError
from manifold.scenario import Budgets, Fault, Scenario, ToolMock


class EchoAgent:
    """Well-behaved baseline: search once, return the result. Completes cleanly
    when the tool works; crashes (unhandled) on a persistent outage — naive on
    purpose, as a contrast to the retry agents."""

    id = "toy.echo"

    def run(self, task: Any, env: ToolEnv, budget: Budgets) -> Any:
        env.state("planning")
        return {"answer": env.call("search", query=str(task))}


class RetryForeverAgent:
    """PLANTED BUG: retries `search` forever on error instead of giving up. Against
    a persistent outage Manifold should flag `no_infinite_retry` and a budget_steps
    terminal — the headline reliability bug."""

    id = "toy.retry_forever"

    def run(self, task: Any, env: ToolEnv, budget: Budgets) -> Any:
        while True:
            try:
                return {"answer": env.call("search", query=str(task))}
            except ToolError:
                env.state("retrying")
                continue


class BoundedRetryAgent:
    """The fix: retry a bounded number of times, then give up gracefully. Stays
    clean even against a persistent outage."""

    id = "toy.bounded_retry"

    def run(self, task: Any, env: ToolEnv, budget: Budgets) -> Any:
        for attempt in range(4):
            try:
                return {"answer": env.call("search", query=str(task))}
            except ToolError:
                env.state("retrying", attempt=attempt)
        return {"answer": None, "error": "search unavailable after retries"}


AGENTS = {a.id: a for a in (EchoAgent(), RetryForeverAgent(), BoundedRetryAgent())}

_TASKS = ["weather in Paris", "stock price AAPL", "who won the 2018 final"]


def make_scenario(seed: int) -> Scenario:
    """Seed-resolved stimulus. M0 builds it by hand from the seed; M2 replaces this
    with the constrained-random generator over a declared ScenarioSpace. Same seed
    always yields the identical Scenario — that's the reproduction contract."""
    rng = random.Random(seed)
    # ~40% of seeds model a *persistent* `search` outage (at_call=-1 = every call),
    # which is what makes the retry-forever bug surface.
    faults = [Fault(at_call=-1, kind="error")] if rng.random() < 0.4 else []
    return Scenario(
        seed=seed,
        task=rng.choice(_TASKS),
        mocks=[
            ToolMock(
                tool="search",
                responses=[f"result-{rng.randint(1000, 9999)}"],
                faults=faults,
            )
        ],
        budgets=Budgets(max_steps=15, wall_ms=10_000),
    )
