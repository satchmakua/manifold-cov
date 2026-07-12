# 5. Scenario shrinking operates on the Scenario directly, off the seed→Scenario path

- **Status:** Accepted
- **Date:** 2026-07-12

## Context

A sweep hands back a *failing* scenario. Often it is bloated — several tools, multiple
faults, generous budgets, a task string — while only a small core actually causes the bug.
M4 calls for an own delta-debug **shrinker** that reduces a failing `Scenario` to its
minimal still-failing reproducer (ADR-0003: our own, not Hypothesis).

Two contracts constrain how this can work:

- **ADR-0002** — `seed → Scenario` is a pure function; a seed is a complete reproduction
  handle for the environment, and nothing may leak wall-clock or unseeded RNG into scenario
  construction.
- **ADR-0004** — coverage-direction stays reproduction-safe by *selecting seeds*, never
  mutating the scenario a seed maps to.

A shrinker, by definition, must produce scenarios that are *not* what any seed maps to (a
minimized scenario is smaller than `sample(space, seed)`). So it cannot be expressed as
seed selection, and its outputs cannot be seeds.

## Decision

The shrinker **mutates a concrete `Scenario` object directly** and returns a minimal
`Scenario` — not a seed. Its reproduction handle is the minimized `Scenario` itself,
serialized (pydantic `model_dump_json`); `manifold shrink` prints that JSON as the
replayable reproducer.

Mechanics (`src/manifold/shrink.py`):

- **ddmin over the fault set.** A classic reduce-to-complement delta-debug minimizes the
  faults — the failure-causing atoms — to a 1-minimal subset.
- **Greedy structural + numeric passes.** Drop mocks that are no longer load-bearing, trim
  responses, binary-search each budget toward its smallest still-failing value (leaving
  `wall_ms` at its default unless a `timeout` terminal makes it the culprit), and simplify
  the task.
- **Anchored oracle.** Every candidate is judged by re-running the agent and requiring that
  *every invariant that failed on the input still fails* — so shrinking never drifts into a
  different or weaker bug. `target=[…]` can narrow this to a chosen invariant.

## Consequences

- **This does not violate ADR-0002/0004.** Those govern `seed → Scenario` construction; the
  shrinker is a *separate* operation over an already-built scenario. It uses no wall-clock
  and no unseeded RNG (ddmin and chunk-splitting are deterministic), so shrinking a
  deterministic agent is itself deterministic and reproducible — the same guarantee, held by
  construction rather than by a seed.
- **Reproduction handle widens from "a seed" to "a seed *or* a minimal Scenario JSON."** The
  seed still reproduces the *original* failure; the JSON reproduces the *minimized* one. Both
  are exact.
- **Greedy, not globally minimal.** The result is a small, verified-still-failing reproducer,
  not a proof of the smallest possible one; the honest framing is "delta-debugged," and the
  evaluation count is reported so the cost is visible.
- **Cost is agent runs.** Each candidate re-runs the agent, so shrinking a live/expensive
  agent is metered; `manifold run` only shrinks on an explicit `--shrink`, while the toy/
  example demos (deterministic, cheap) shrink freely. Nondeterministic agents pass
  `--repeats K` so the oracle requires the failure across K runs.
