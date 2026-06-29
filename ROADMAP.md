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

- [x] **M1 — Coverage model + report.** *(credibility milestone)* The `CoverageModel`
  primitives (coverpoints + bins, a transition FSM over the tool-call sequence,
  crosses) in `model.py`; `evaluate(trace, model) -> CoverageDB` with `merge`/`pct`/
  `holes` in `coverage.py`; a starter library of generic agent coverpoints; a `rich`
  terminal coverage report and a single static HTML heatmap (`report.py`, **stdlib —
  no jinja2 dep**). A `manifold cover <spec.py>` command lists the declared model;
  `examples/spec_example.py` is the worked spec. Coverage wired into `manifold run`.
  **Test:** `manifold run examples/toy_agents.py --agent toy.retry_forever --scenarios 50
  --html report.html` → prints a coverage table with hit % and named holes; `report.html`
  opens to a heatmap; `manifold cover examples/spec_example.py` lists the coverpoints.
  _(shipped 2026-06-28)_

## Phase 2 — Generate and close the loop

- [x] **M2 — Constrained-random generator + fault injection.** `ScenarioSpace` + `ToolSpec`
  + the pure seeded `sample(space, seed)` in `generate.py` (the canonical stimulus path;
  hand-built `make_scenario` kept as the zero-spec fallback); the full fault menu
  (error/timeout/garbage/latency, persistent + transient); the sweep loop extracted to
  `closure.py` with K **repeats-per-seed** and per-seed flakiness (the `pass^k` signal).
  A `toy.flaky_retry` agent (unseeded internal RNG) demonstrates it. `examples/spec_example.py`
  now exposes `SPACE`.
  **Test:** `manifold run examples/toy_agents.py --spec examples/spec_example.py
  --agent toy.retry_forever --scenarios 500 --seed 7` → coverage jumps to ~47% (the
  generator closes the M1 `search:err->search:ok` recovery hole and fills `fault_seen`
  to 5/5); `--agent toy.flaky_retry --repeats 3` → lists flaky seeds (pass X/3); the same
  `--seed` reproduces the same scenarios. _(shipped 2026-06-28)_

- [x] **M3 — Coverage-directed generation + the Claude demo.** Coverage-direction via
  **seed selection** in `closure.py` (`--coverage-directed`; keeps `sample` pure so seeds
  stay reproducible — ADR-0004), verified to beat uniform random. The real Claude-backed
  agent `examples/claude_agent.py` (`anthropic` tool-use loop, `claude-haiku-4-5`, lazy
  import + injectable client, gated behind `ANTHROPIC_API_KEY`) that Manifold finds a
  retry-to-budget bug in; a committed recorded trace (`examples/recorded/`) makes the demo
  + test run offline with no key.
  **Test (live):** `ANTHROPIC_API_KEY=... manifold run examples/claude_agent.py --spec
  examples/spec_example.py --scenarios 10 --coverage-directed` → finds the bug, prints the
  §7 output. **Test (offline, what CI runs):** `python examples/claude_agent.py` prints
  PASS/FAIL on the scripted run; `pytest` flags the recorded trace and shows directed
  beats random — all with no API call. _(shipped 2026-06-28)_

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

---

## Review-driven hardening — make it sparkle (added 2026-06-28)

> Added after an external code review (captured in `../ai-docs/project_eval/`). The
> methodology is the most *novel* in the family and M0–M3 ship — but the flagship
> "directed beats random" lift is an unconvincing **~2 points** (42.6% vs 40.4%), the
> "real Claude agent" demo currently runs on a **scripted stub** (the committed trace is
> from `ScriptedClient`, not the live API), and the testing tool has the **lowest test
> ratio** of the family. These items fix exactly that. **Standing rule:** a milestone is
> checked only when it has produced **one real, captured, reproducible artifact**.

**Definition of Done — the "Sparkle Bar"** (applies to every milestone):
1. **Real artifact captured** — produced against a real agent, pinned at the top of the README with the exact reproduce command.
2. **Flagship demo in one screen** — the coverage heatmap + a real bug with its repro.
3. **Stress-tested** — and the testing tool itself should be the best-tested thing in the set.
4. **Honest numbers** — a real margin, not a 2-point delta; an explicit "can't do" list.
5. **Cold-clone reproducible** — pinned deps, fixed seeds, one `make demo`, CI runs the real-or-recorded path.
6. **Polished** — no stray files, no unreachable declared coverpoints, README opens with the artifact.
7. **Positioned** — one paragraph: who it's for, what it beats, why this not the obvious alternative.

**Hardening items (Manifold-specific):**
- [ ] **H1 — Make coverage-directed *decisively* win.** Strengthen the hole-bias to target **all** current holes (transition edges + crosses, not just `fault_seen`), and produce a **coverage-vs-scenarios curve**. *Accept:* on the example, `--coverage-directed` reaches **~90% coverage in ≤ 1/3 the scenarios** of `--random`, and the curve is in the README — a headline margin, not a 2-point delta.
- [ ] **H2 — Find a real bug in a real agent.** Run `examples/claude_agent.py` against the **live** API once, find a genuine reliability bug, and commit the **real** trace (recorded for offline CI) + the heatmap screenshot — replacing the scripted-stub trace as the headline. *Accept:* a real-model trace is committed; the README shows the real bug + its one-line `manifold repro`.
- [ ] **H3 — Off-the-shelf adapter (promote from M4).** Ship the `claude-agent-sdk` (or popular-framework) adapter so Manifold is proven on an agent the author **didn't** write. *Accept:* a third-party agent runs unmodified behind the adapter and is covered.
- [ ] **H4 — No unreachable coverpoints.** Make latency faults optionally real (simulated clock / flagged sleep) so the `timeout` bin is reachable — or remove the bin honestly. *Accept:* every declared coverpoint is reachable on the example, or is documented as deliberately aspirational.
- [ ] **H5 — Dogfood.** Raise Manifold's own test ratio to the family norm (~0.5 test-to-source). *Accept:* test ratio raised; ideally the suite is self-gated.
