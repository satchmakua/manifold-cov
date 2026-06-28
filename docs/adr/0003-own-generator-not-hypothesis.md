# 3. Roll our own seeded generator instead of building on Hypothesis

- **Status:** Accepted
- **Date:** 2026-06-28

## Context

[Hypothesis](https://hypothesis.readthedocs.io/) is the obvious off-the-shelf engine
for constrained-random generation in Python: mature strategies, excellent shrinking,
a failing-example database. Two forces pull against adopting it as a dependency:

1. **Control of the loop.** Manifold's core feature is *coverage-directed*
   generation — biasing the sampler toward unhit coverage bins using live feedback
   (`closure.py`). Hypothesis owns its own test-execution loop (`@given`) and is not
   designed to be driven by an external coverage signal; bending it to that shape
   would fight the library.
2. **Licensing.** Hypothesis is **MPL-2.0**. Manifold is **MIT** and means to stay
   cleanly MIT for unencumbered portfolio/OSS use. A code dependency would entangle
   that.

## Decision

Do **not** take a code dependency on Hypothesis. Implement our own:

- a pure `seed → Scenario` sampler over a declared `ScenarioSpace` (`generate.py`),
  seeded by `random.Random(seed)` only, so coverage bias adjusts *weights* while the
  seed still determines the outcome (preserving ADR-0002's determinism contract);
- our own delta-debug **shrinker** over the structured `Scenario` (M4).

Hypothesis remains a **design reference** (strategies, shrinking) and is cited as
prior art in `DESIGN.md`, but is not imported.

## Consequences

- **Easy:** wiring coverage feedback directly into generation; staying MIT-clean;
  keeping the generator small and fully under our control.
- **Hard / accepted:** we re-implement shrinking ourselves (more work than reusing
  Hypothesis's battle-tested shrinker). Mitigated by the scenario being a small,
  structured object that delta-debugging handles well.
- If a future need outweighs these forces, this is reversible — `generate.py` is the
  single seam to swap.
