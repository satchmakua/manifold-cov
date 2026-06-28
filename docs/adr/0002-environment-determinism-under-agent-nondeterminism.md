# 2. Environment determinism, decoupled from agent nondeterminism

- **Status:** Accepted
- **Date:** 2026-06-28

## Context

Constrained-random verification rests on **reproducibility**: a seed must reproduce
a run so a failure can be replayed and shrunk. But the things Manifold tests —
tool-using LLM agents — are inherently **nondeterministic**: the same prompt can
yield different tool calls run to run. A naive "seed reproduces the whole run"
contract is therefore impossible for real agents, and pretending otherwise would
make every reported failure suspect.

## Decision

Split determinism across the boundary defined by the `Agent`/`ToolEnv` ports:

- **The environment is deterministic.** A seed resolves, by a *pure function*, to a
  complete `Scenario` — task input, per-tool mock responses, injected faults, and
  budgets (`scenario.py`, `generate.py`). Same seed → byte-identical stimulus. The
  seed is the reproduction handle for the *environment*, always.
- **The agent is a black box** that may be deterministic (the toy agents) or not
  (the Claude example). Manifold does not try to make the LLM deterministic.
- **Nondeterminism is measured, not hidden.** The harness supports running **K
  repeats per seed** (`Trace.repeat`); per-seed flakiness is a first-class result —
  the `pass^k` reliability signal from tau²-bench (a high benchmark score often
  hides much lower run-to-run reliability).

A supporting invariant of this decision: budget enforcement must be
**non-swallowable**, so `BudgetExceeded` inherits `BaseException` (not `Exception`)
— a "retry-everything" agent cannot catch it and defeat reproducible termination.

## Consequences

- **Easy:** reproducing and replaying the stimulus for any seed; testing real LLM
  agents honestly; reporting reliability (flakiness), not just pass/fail.
- **Hard / accepted:** for a nondeterministic agent, a single seed may *not*
  reproduce the exact trace — only the stimulus. Failures are therefore reported
  with their seed *and* repeat index, and invariants that depend on a specific
  trajectory must be evaluated per-repeat.
- This boundary is why the harness, not the agent, owns budgets and trace
  construction (`harness.py`).
