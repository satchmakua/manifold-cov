# 4. Coverage-directed generation via seed selection, not scenario mutation

- **Status:** Accepted
- **Date:** 2026-06-28

## Context

Coverage-directed generation must bias the sweep toward unhit coverage bins to close
them faster than uniform random (the "coverage closure" loop). The DESIGN sketched this
as a `bias` argument to `sample(space, seed, bias)` — i.e. the bias *mutates the scenario
a seed produces*.

That mutation breaks the per-seed reproduction contract (ADR-0002). The bias is derived
from the running `CoverageDB`, which depends on every prior scenario in the sweep. So the
scenario for seed *S* in a directed sweep would depend on sweep history — and
`manifold repro S`, which samples *S* in isolation with no history, would produce a
*different* scenario and fail to reproduce the directed-sweep failure.

## Decision

Keep `sample(space, seed)` a **pure function of the seed** (no bias parameter). Make
coverage-direction a **sweep-level seed-selection strategy** in `closure.py`:

- Each step, draw a candidate pool of seeds from a meta-RNG (itself seeded by `base_seed`,
  so the whole directed sweep is reproducible).
- **Cheaply sample each candidate** (`sample` is pure and runs no agent) and score its
  scenario by (a) a `_hole_bonus` — an exact reward when its fault kind maps to an unhit
  `fault_seen` bin (those bins *are* fault-kind names) — plus (b) general input novelty
  (rarest fault kind / persistent flag / budget / task so far).
- Run the highest-scoring candidate.

The chosen seed is recorded as the reproduction handle. Because `sample(space, seed)` is
unchanged, `manifold repro <seed>` reproduces the exact scenario the directed sweep ran.

## Consequences

- **Easy:** per-seed reproduction survives coverage-direction; `generate.py` needs no
  change (the `sample` signature stays pure); the selector is testable in isolation.
- **Effective:** verified to beat uniform random on the example — e.g. at N=8 it reaches
  `fault_seen` 100% (vs 80% random) and higher overall coverage; the gap persists as N
  grows. Deterministic given `base_seed`, so the comparison is a stable test.
- **Hard / accepted:** direction is limited to what's derivable from the *scenario* before
  running the agent (input features + the exact `fault_seen` mapping). Output bins that
  depend on agent behavior (e.g. `terminal_reason`) can't be targeted directly — only
  nudged via input diversity. A future, more powerful version could run a cheap surrogate
  or learn the seed→bin map; this selection approach is the reproduction-safe baseline.
- Directed-mode seeds are large random integers (from the meta-RNG); that's fine — they're
  still pure handles (`repro 1466357293 …` works).
