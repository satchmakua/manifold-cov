# ROADMAP — Manifold

The milestone checklist.

**Rules of the road:**
- Each milestone is an **independently runnable** slice — something actually testable end-to-end.
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

- [~] **M4 — Crosses, shrinking, real-framework adapter.** Cross-coverage **[done in M1]**; the
  `claude-agent-sdk` adapter so Manifold verifies an off-the-shelf agent **[done — see H3]**; an
  own delta-debug **shrinker** that minimizes a failing `Scenario` **[remaining]**; optional
  parallel scenario execution **[remaining]**.
  **Test:** `manifold run` works against a `claude-agent-sdk` agent through the adapter **[met — H3]**;
  a failing scenario shrinks to a minimal reproducer **[pending the shrinker]**.

---

**North star:** point Manifold at an agent and one screen tells the whole story —
coverage %, the holes, the bugs found, and a one-line `manifold repro <seed>` for each
(DESIGN.md §7).

---

## Review-driven hardening — make it sparkle (added 2026-06-28)

> Added after an external code review (captured in `../ai-docs/project_eval/`). The
> methodology is the most *novel* in the family and M0–M3 ship — but the flagship
> "directed beats random" lift is an unconvincing **~2 points** (42.6% vs 40.4%), the
> "real Claude agent" demo, at review time, ran on a **scripted stub** (the committed trace was
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
- [x] **H0 — Fix the Windows crash.** `manifold`'s typer callback forces UTF-8 stdio, so the
  flagship coverage table no longer `UnicodeEncodeError`s on a stock cp1252 console.
  _(shipped 2026-07-03)_
- [x] **H1 — Make coverage-directed *decisively* win.** The selector scores candidates by how many
  unhit *reachable* bins they fill across **every projected coverpoint** — the input-derivable
  `fault_by_tool` 12-cell space plus input-derived `project` hooks on `terminal_reason`/`n_tool_calls`
  so directed genuinely *targets* the budget/latency behaviours (not coupon-collector luck). A
  `manifold curve` command charts coverage-vs-scenarios to a self-contained SVG. *Accept met:* on
  `research.pipeline`, directed reaches **90% at N=8 vs random N=35 (~4×) and full 100% at N=11 vs
  random N=217 (~20×)** at the default seed; **both reach 100%** (fair example). Median across seeds
  ~2.9× to 90%, ~16× to full. Curve in the README. _(shipped 2026-07-03; corrected after adversarial
  self-review flagged a truncation-artifact overclaim.)_
- [x] **H4 — No unreachable coverpoints.** A **simulated clock** (latency faults advance virtual
  time) makes `timeout` reachable; small cost/step/wall budget choices make `budget_cost`/
  `budget_steps`/`timeout` reachable; structurally-impossible bins are marked `ignore` (excluded
  from the denominator, à la UVM ignore_bins). *Accept met:* a directed sweep of the research model
  reaches **100%** — every declared bin is reachable (tested). _(shipped 2026-07-03)_
- [x] **H2 — Verify a real agent live.** *(originally "find a real bug in a real agent" — the
  honest outcome is better than the planned one.)* Ran the real `claude-haiku-4-5` tool-use agent
  live (2026-07-10): on the recorded seed-7 outage it retried twice, rephrased its query, and
  answered gracefully within budget — **the retry-forever bug does not exist in the real model**
  (it lives on in the toy/stubborn agents and the scripted worst-case client, which the offline
  demo contrasts side-by-side). The live 10-scenario coverage-directed sweep passed **10/10
  invariants at 36% coverage**, and the coverage table names exactly which behaviors went
  untested — the thesis, demonstrated on a real model. `examples/recorded/` now holds the genuine
  trace (scrubbed; no key material); `--live` re-records it, guarded so a crashed run can't
  clobber it and the offline demo never writes. *Accept met:* real-model trace recorded + README
  rewritten around what it shows. _(shipped 2026-07-10)_
- [x] **H3 — Off-the-shelf adapter (promote from M4).** Shipped `examples/sdk_agent.py`: a Manifold
  `Agent` that wraps Anthropic's higher-level **`claude-agent-sdk`** — the agent loop + tool dispatch
  run in the SDK's bundled `claude` CLI subprocess, and the adapter exposes Manifold's `env` as an
  **in-process MCP server** so the third-party agent's tool calls flow through the mock/fault/budget
  machinery *unmodified*. Load-bearing subtlety: the SDK's MCP layer only catches `Exception`, so a
  raised `BudgetExceeded` (a `BaseException`) would be swallowed in a detached task — the adapter
  catches it at the handler, stashes it, and re-raises **outside** the SDK loop, keeping budget
  enforcement non-swallowable across the process boundary. *Accept met (live 2026-07-10):*
  `manifold run examples/sdk_agent.py --agent sdk.search --scenarios 2` drove the real SDK agent
  through the adapter (2/2 invariants pass), and `python examples/sdk_agent.py --live` recorded a
  genuine SDK trace (the agent retried the outage twice, rephrasing its query, then answered
  gracefully — committed at `examples/recorded/sdk_search.jsonl`). No npm needed: the wheel bundles
  the CLI. Offline: 11 adapter tests cover the bridge (routing, ToolError→is_error, budget
  stash+re-raise, and the re-record guard — an adversarial review caught that a degenerate
  live trace could silently clobber the fixture; fixed) with no key/subprocess. _(shipped 2026-07-10)_
- [x] **H5 — Dogfood.** Test-to-source **0.28 → 0.57** (50 tests; **705 test LOC / 1239 source LOC**,
  raw count) with the reachability / projection / curve suites + the H2 live-wiring/guard tests + the
  H3 adapter-bridge suite. *Accept met:* at/above the ~0.5 goal by raw LOC (figure stated so the
  reader can recompute); the M4 shrinker will add more. _(shipped 2026-07-10)_
