"""The sweep loop + coverage-directed generation (DESIGN.md §6.6).

Runs N seeded scenarios (each ``repeats`` times) against an agent, accumulating
coverage and invariant failures, and recording per-seed pass/fail across repeats so
**flakiness** is a first-class result — the ``pass^k`` reliability signal (ADR-0002).

**Coverage-directed generation** (ADR-0004): rather than mutating the scenario a seed
maps to (which would break per-seed reproduction), the directed sweep keeps
``sample(space, seed)`` pure and instead *selects which seeds to run*. Each step it
draws a candidate pool of seeds, cheaply samples each (no agent run), and picks the one
whose scenario best fills current holes — favouring fault kinds whose ``fault_seen`` bin
is still empty, and under-used inputs generally. Seeds stay pure reproduction handles;
``manifold repro <seed>`` reproduces exactly. Must beat uniform random on the examples.
"""

from __future__ import annotations

import random
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field

from manifold.agent import Agent
from manifold.coverage import CoverageDB, evaluate, project_bins
from manifold.harness import run
from manifold.invariants import Invariant, Violation
from manifold.model import CoverageModel
from manifold.scenario import Scenario


@dataclass
class SweepResult:
    db: CoverageDB
    failures: list[Violation]  # deduplicated by (invariant, seed)
    results_by_seed: dict[int, list[bool]]  # seed -> per-repeat pass flag
    trajectory: list[float] = field(default_factory=list)  # overall coverage % after each scenario

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


@dataclass
class _SeenFeatures:
    """Counts of the input features the generator controls, for diversity scoring."""

    fault_kind: Counter[str] = field(default_factory=Counter)
    persistent: Counter[bool] = field(default_factory=Counter)
    budget: Counter[int] = field(default_factory=Counter)
    task: Counter[str] = field(default_factory=Counter)

    @staticmethod
    def _features(scn: Scenario) -> tuple[list[str], list[bool], int, str]:
        kinds: list[str] = [str(f.kind) for m in scn.mocks for f in m.faults]
        persistent = [f.at_call == -1 for m in scn.mocks for f in m.faults]
        return (kinds or ["none"], persistent, scn.budgets.max_steps, str(scn.task))

    def novelty(self, scn: Scenario) -> float:
        kinds, persistent, budget, task = self._features(scn)
        score = sum(1.0 / (1 + self.fault_kind[k]) for k in kinds)
        score += sum(0.5 / (1 + self.persistent[p]) for p in persistent)
        score += 0.5 / (1 + self.budget[budget])
        score += 0.25 / (1 + self.task[task])
        return score

    def update(self, scn: Scenario) -> None:
        kinds, persistent, budget, task = self._features(scn)
        for k in kinds:
            self.fault_kind[k] += 1
        for p in persistent:
            self.persistent[p] += 1
        self.budget[budget] += 1
        self.task[task] += 1


def _select_seed(
    build: Callable[[int], Scenario],
    meta: random.Random,
    seen: _SeenFeatures,
    model: CoverageModel,
    db: CoverageDB,
    candidates: int,
) -> int:
    """Pick the candidate seed whose scenario fills the most currently-unhit *reachable*
    bins — across every coverpoint with a projection, not just `fault_seen` (H1) — breaking
    ties by input novelty. `sample` stays pure, so the chosen seed still reproduces exactly."""
    holes = set(db.holes())
    best_seed, best_score = 0, -1.0
    for _ in range(candidates):
        cand = meta.randrange(1, 2**31)
        scn = build(cand)
        fills = len(project_bins(model, scn) & holes)
        score = 100.0 * fills + seen.novelty(scn)
        if score > best_score:
            best_score, best_seed = score, cand
    return best_seed


def sweep(
    agent: Agent,
    build_scenario: Callable[[int], Scenario],
    model: CoverageModel,
    invariants: list[Invariant],
    *,
    scenarios: int,
    base_seed: int = 0,
    repeats: int = 1,
    coverage_directed: bool = False,
    candidates: int = 8,
) -> SweepResult:
    db = CoverageDB()
    failures: dict[tuple[str, int], Violation] = {}
    results: dict[int, list[bool]] = {}
    trajectory: list[float] = []
    seen = _SeenFeatures()
    meta = random.Random(base_seed)

    for i in range(scenarios):
        if coverage_directed:
            seed = _select_seed(build_scenario, meta, seen, model, db, candidates)
        else:
            seed = base_seed + i
        scenario = build_scenario(seed)
        for k in range(repeats):
            trace = run(agent, scenario, repeat=k)
            db = db.merge(evaluate(trace, model))
            violations = [v for inv in invariants if (v := inv(trace)) is not None]
            results.setdefault(seed, []).append(not violations)
            for v in violations:
                failures.setdefault((v.invariant, seed), v)
        if coverage_directed:
            seen.update(scenario)
        trajectory.append(db.pct())

    return SweepResult(
        db=db, failures=list(failures.values()), results_by_seed=results, trajectory=trajectory
    )
