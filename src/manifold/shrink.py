"""Scenario shrinking — an own delta-debug minimizer over a failing Scenario (DESIGN.md §8 M4).

A sweep hands back a *failing* scenario; often it is bloated (many tools, several faults,
generous budgets, a long task) and only a small core actually causes the bug. ``shrink``
reduces it to a minimal reproducer that still triggers **the same invariant failures** —
the agent-world analog of a delta-debugged test case.

Design (see ADR-0005):

* **Own delta-debug, no Hypothesis** (ADR-0003). The core is a classic ``ddmin``
  (reduce-to-complement) over the fault set — the failure-causing atoms — followed by
  greedy structural passes (drop inert mocks, trim responses, simplify the task) and a
  binary search over each budget toward its smallest still-failing value.
* **Anchored on the original failure set.** The oracle requires *every* invariant that
  failed on the input to still fail, so shrinking never drifts into a different or weaker
  bug (e.g. turning a retry-forever into a plain budget cut).
* **Operates on the Scenario directly, off the seed→Scenario path.** Unlike the generator
  (ADR-0004, seed selection keeps ``sample`` pure), the shrinker mutates a concrete
  ``Scenario`` — its output is a minimal ``Scenario`` (pydantic-serialisable), reproduced
  from that object, not a seed. It uses no wall-clock and no unseeded RNG, so a shrink of
  a deterministic agent is itself deterministic (ADR-0002 preserved).

It is a *greedy* minimizer: the result is a small, verified-still-failing reproducer, not
a proof of global minimality.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from manifold.agent import Agent
from manifold.harness import run
from manifold.invariants import Invariant
from manifold.scenario import Budgets, Scenario


@dataclass
class ShrinkResult:
    original: Scenario
    minimal: Scenario
    preserved: list[str]  # invariant names still failing on `minimal`
    evaluations: int  # candidate scenarios the oracle ran (honest cost)
    original_size: int
    minimal_size: int

    @property
    def reduction_pct(self) -> float:
        if self.original_size == 0:
            return 0.0
        return 100.0 * (1 - self.minimal_size / self.original_size)


def _failing(agent: Agent, scn: Scenario, invariants: list[Invariant], repeats: int) -> set[str]:
    """Names of the invariants that fail on ``scn`` (on any of ``repeats`` runs)."""
    failed: set[str] = set()
    for k in range(repeats):
        trace = run(agent, scn, repeat=k)
        for inv in invariants:
            v = inv(trace)
            if v is not None:
                failed.add(v.invariant)
    return failed


def _size(scn: Scenario) -> int:
    """A small honest size metric: mocks + faults + responses + (task present)."""
    faults = sum(len(m.faults) for m in scn.mocks)
    responses = sum(len(m.responses) for m in scn.mocks)
    return len(scn.mocks) + faults + responses + (1 if scn.task is not None else 0)


def _ddmin(atoms: list[int], still_fails: Callable[[list[int]], bool]) -> list[int]:
    """Classic delta-debug (reduce-to-complement): return a 1-minimal sub-list of ``atoms``
    for which ``still_fails`` holds. ``atoms`` itself must already satisfy ``still_fails``."""
    n = 2
    while len(atoms) >= 2:
        chunk = max(1, len(atoms) // n)
        removed = False
        for i in range(0, len(atoms), chunk):
            complement = atoms[:i] + atoms[i + chunk :]
            if complement and still_fails(complement):
                atoms, n, removed = complement, max(n - 1, 2), True
                break
        if not removed:
            if n >= len(atoms):
                break
            n = min(len(atoms), n * 2)
    return atoms


def shrink(
    agent: Agent,
    scenario: Scenario,
    invariants: list[Invariant],
    *,
    repeats: int = 1,
    target: list[str] | None = None,
) -> ShrinkResult:
    """Minimize ``scenario`` to a still-failing reproducer. ``target`` overrides which
    invariants to preserve (default: all that fail on the input). Returns the input
    unchanged when nothing fails."""
    original = scenario.model_copy(deep=True)
    failing = _failing(agent, scenario, invariants, repeats)
    tgt = set(target) if target else set(failing)
    if not tgt or not tgt.issubset(failing):  # nothing (requested) fails -> nothing to shrink
        keep = sorted(tgt & failing)
        return ShrinkResult(original, original, keep, 0, _size(original), _size(original))

    evals = 0

    def oracle(cand: Scenario) -> bool:
        nonlocal evals
        evals += 1
        return tgt.issubset(_failing(agent, cand, invariants, repeats))

    current = scenario.model_copy(deep=True)

    # --- Phase A: ddmin over the fault set (the failure-causing atoms) ---
    base = current.model_copy(deep=True)
    fault_ids = [
        (mi << 16) | fi for mi, m in enumerate(base.mocks) for fi in range(len(m.faults))
    ]

    def with_faults(keep_ids: list[int]) -> Scenario:
        keep = set(keep_ids)
        scn = base.model_copy(deep=True)
        for mi, m in enumerate(scn.mocks):
            m.faults = [
                f for fi, f in enumerate(base.mocks[mi].faults) if ((mi << 16) | fi) in keep
            ]
        return scn

    if fault_ids:
        current = with_faults(_ddmin(fault_ids, lambda ids: oracle(with_faults(ids))))

    # --- Phase B: drop mocks that are no longer load-bearing ---
    changed = True
    while changed:
        changed = False
        for mi in range(len(current.mocks)):
            cand = current.model_copy(deep=True)
            del cand.mocks[mi]
            if oracle(cand):
                current, changed = cand, True
                break

    # --- Phase C: trim each mock's responses to the fewest that still fail ---
    for mi in range(len(current.mocks)):
        for trial in ([], current.mocks[mi].responses[:1]):
            if len(trial) < len(current.mocks[mi].responses):
                cand = current.model_copy(deep=True)
                cand.mocks[mi].responses = trial
                if oracle(cand):
                    current = cand

    # --- Phase D: binary-search each budget toward its smallest still-failing value ---
    defaults = Budgets()

    def set_steps(v: int) -> Scenario:
        c = current.model_copy(deep=True)
        c.budgets.max_steps = v
        return c

    def set_cost(v: int) -> Scenario:
        c = current.model_copy(deep=True)
        c.budgets.max_cost = float(v)
        return c

    def set_wall(v: int) -> Scenario:
        c = current.model_copy(deep=True)
        c.budgets.wall_ms = v
        return c

    current = _min_budget(current, set_steps, current.budgets.max_steps, oracle)
    current = _min_budget(current, set_cost, int(current.budgets.max_cost), oracle)
    # wall_ms only matters when it's the culprit (a latency bug); otherwise leave the default
    term = run(agent, current).terminal
    if term is not None and term.reason == "timeout":
        current = _min_budget(current, set_wall, current.budgets.wall_ms, oracle)
    elif current.budgets.wall_ms < defaults.wall_ms and oracle(set_wall(defaults.wall_ms)):
        current = set_wall(defaults.wall_ms)

    # --- Phase E: simplify the task (None, then a short canonical string) ---
    for trial_task in (None, "x"):
        if current.task != trial_task:
            cand = current.model_copy(deep=True)
            cand.task = trial_task
            if oracle(cand):
                current = cand
                break

    return ShrinkResult(
        original, current, sorted(tgt), evals, _size(original), _size(current)
    )


def _min_budget(
    current: Scenario, set_: Callable[[int], Scenario], hi: int, oracle: Callable[[Scenario], bool]
) -> Scenario:
    """Binary-search the smallest budget value in ``[0, hi]`` for which ``oracle`` still
    holds (``hi`` is the known-failing current value). Greedy: assumes near-monotonicity."""
    best, lo = hi, 0
    while lo < best:
        mid = (lo + best) // 2
        if oracle(set_(mid)):
            best = mid
        else:
            lo = mid + 1
    return set_(best)
