# ROADMAP — Manifold

The milestone checklist.

**Rules of the road:**
- Each milestone is an **independently runnable** slice, testable end-to-end.
- Every milestone ends with explicit **Test** steps — the acceptance criteria.
- Build **top-down**: a thin end-to-end slice first, then deepen. Counts and scopes
  are budgets, not promises — split a milestone if it grows too big.
- Check a box **only after its Test passes**.

Maps directly to DESIGN.md §8.

---

## Phase 0 — Walking skeleton

- [x] **M0 — Skeleton & it runs.** `Agent`/`ToolEnv` ports; `harness.run` records a
  `Trace`; two invariants (`terminates_within_budget`, `no_infinite_retry`); seeded
  `make_scenario`; `manifold run` sweeps N seeds and reports failures; `manifold
  repro <seed>` replays one. A planted retry-forever toy agent is caught and
  reproduced; the bounded-retry fix is clean. `ruff`/`mypy`/`pytest` wired and green.
  **Test:** `manifold run examples/toy_agents.py --agent toy.retry_forever --scenarios 12`
  → reports failing seeds with both invariants + a repro command; `manifold repro <seed>
  examples/toy_agents.py --agent toy.retry_forever` → prints the full trace ending in
  `budget_steps` with both invariants FAIL; `pytest` → green. _(shipped 2026-06-28)_

## Phase 1 — The novel core

- [ ] **M1 — Coverage model + report.** *(credibility milestone)* The `CoverageModel`
  primitives (coverpoints + bins, a transition FSM over the tool-call sequence,
  crosses) in `model.py`; `evaluate(trace, model) -> CoverageDB` with `merge`/`pct`/
  `holes` in `coverage.py`; a starter library of generic agent coverpoints; a `rich`
  terminal coverage report and a single static HTML heatmap (`report.py`, jinja2). A
  `manifold cover <spec.py>` command lists the declared model. Wire coverage into
  `manifold run` so a sweep prints coverage % + holes alongside failures.
  **Test:** `manifold run examples/toy_agents.py --agent toy.retry_forever --scenarios 50
  --html report.html` → prints a coverage table with hit % and named holes; `report.html`
  opens to a heatmap; `manifold cover examples/spec_example.py` lists the coverpoints.

## Phase 2 — Generate and close the loop

- [ ] **M2 — Constrained-random generator + fault injection.** `ScenarioSpace` + the
  pure seeded `sample(space, seed)` in `generate.py` (replacing hand-built
  `make_scenario`); the full fault menu (error/timeout/garbage/latency); K
  **repeats-per-seed** with per-seed flakiness reporting.
  **Test:** `manifold run … --scenarios 500 --seed 7 --repeats 3` → sweeps generated
  scenarios, aggregates coverage, and reports any flaky (pass-some/fail-some) seeds;
  re-running the same `--seed` reproduces the same scenarios.

- [ ] **M3 — Coverage-directed generation + the Claude demo.** Hole-biased sampling in
  `closure.py` (`--coverage-directed`); the real Claude-backed example agent
  (`anthropic` tool-use loop, `claude-haiku-4-5`, gated behind `ANTHROPIC_API_KEY`,
  with a committed recorded trace for offline runs) carrying a real reliability bug
  that Manifold finds, reports at ~80% coverage, and hands back as a seed + heatmap +
  one-line repro.
  **Test:** `manifold run examples/claude_agent.py --spec examples/spec_example.py
  --scenarios 500 --coverage-directed` → reaches the coverage target in fewer scenarios
  than `--random`, finds the planted bug, and prints the §7 demo output; the demo's
  pytest runs offline from the recorded trace.

## Phase 3 — Depth (stretch)

- [ ] **M4 — Crosses, shrinking, real-framework adapter.** Cross-coverage; an
  own delta-debug **shrinker** that minimizes a failing `Scenario`; a `claude-agent-sdk`
  adapter so Manifold verifies an off-the-shelf agent; optional parallel scenario
  execution.
  **Test:** a failing scenario shrinks to a minimal reproducer; `manifold run` works
  against a `claude-agent-sdk` agent through the adapter.

---

**North star:** point Manifold at an agent and one screen tells the whole story —
coverage %, the holes, the bugs found, and a one-line `manifold repro <seed>` for each
(DESIGN.md §7).
