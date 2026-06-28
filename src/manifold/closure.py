"""The sweep loop (DESIGN.md §6.6).

Runs N seeded scenarios (each ``repeats`` times) against an agent, accumulating
coverage and invariant failures, and recording per-seed pass/fail across repeats so
**flakiness** is a first-class result — the ``pass^k`` reliability signal (ADR-0002):
a seed that passes on some repeats and fails on others is unreliable even though no
single run looks broken.

M2 sweeps uniformly at random. M3 adds the coverage-directed feedback (biasing
generation toward unhit bins) on top of this same loop.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from manifold.agent import Agent
from manifold.coverage import CoverageDB, evaluate
from manifold.harness import run
from manifold.invariants import Invariant, Violation
from manifold.model import CoverageModel
from manifold.scenario import Scenario


@dataclass
class SweepResult:
    db: CoverageDB
    failures: list[Violation]  # deduplicated by (invariant, seed)
    results_by_seed: dict[int, list[bool]]  # seed -> per-repeat pass flag

    @property
    def n_scenarios(self) -> int:
        return len(self.results_by_seed)

    def fully_passing(self) -> int:
        return sum(1 for flags in self.results_by_seed.values() if all(flags))

    def flaky_seeds(self) -> list[int]:
        """Seeds that pass on some repeats and fail on others."""
        return sorted(
            s for s, flags in self.results_by_seed.items() if any(flags) and not all(flags)
        )


def sweep(
    agent: Agent,
    build_scenario: Callable[[int], Scenario],
    model: CoverageModel,
    invariants: list[Invariant],
    *,
    scenarios: int,
    base_seed: int = 0,
    repeats: int = 1,
) -> SweepResult:
    db = CoverageDB()
    failures: dict[tuple[str, int], Violation] = {}
    results: dict[int, list[bool]] = {}

    for i in range(scenarios):
        seed = base_seed + i
        scenario = build_scenario(seed)
        for k in range(repeats):
            trace = run(agent, scenario, repeat=k)
            db = db.merge(evaluate(trace, model))
            violations = [v for inv in invariants if (v := inv(trace)) is not None]
            results.setdefault(seed, []).append(not violations)
            for v in violations:
                failures.setdefault((v.invariant, seed), v)

    return SweepResult(db=db, failures=list(failures.values()), results_by_seed=results)
