# Manifold — Design

> Bring hardware verification's **constrained-random stimulus + functional coverage** discipline to AI agents: define a behavior space, generate seeded scenarios (with fault injection), measure how much of that space you've actually exercised, and systematically hunt failures — instead of eyeballing a few demos.

**Status:** Design draft · **Language:** Python 3.11+ · **Stack target:** CLI + library (single-process), static HTML report

Name: **Manifold** (the behavior *manifold* you cover; "manifold" = many/diverse). Repo identity, import package, and CLI command all stay `manifold`. The PyPI name `Manifold` is **already taken** (an unrelated SMF manifest tool, verified 2026-06-28) — if/when published, distribute as **`manifold-cov`** while keeping `import manifold` and the `manifold` CLI. Publishing is a stretch goal; local `uv`/editable install gives the `manifold` command regardless.

> **Licensing note (clean by construction).** All runtime deps are MIT/BSD/Apache-2.0 (pydantic, typer, rich, pytest, anthropic, claude-agent-sdk). We deliberately do **not** take a code dependency on **Hypothesis** (MPL-2.0) — it is *prior-art reference only*; Manifold rolls its own seeded sampler and shrinker to stay MIT-clean and self-contained (see §6.4, §6.8). The Claude-backed example calls a paid API and is gated behind `ANTHROPIC_API_KEY`; a recorded trace lets the demo and its test run offline.

---

## 1. Concept

Hardware verification solved "how do you know you tested enough?" decades ago with **coverage-driven verification (CDV)**: constrained-random stimulus drives the design, *functional coverage* measures which behaviors were actually exercised, gap analysis finds the holes, and you bias generation toward them until you reach closure. The agent world hasn't made this leap — today people "test" agents by running a handful of demos by hand, or by scoring task success on a fixed benchmark (tau²-bench, AgentBench, DeepEval). Those measure *did it pass these N tasks*; none of them measure *how much of the agent's behavior space have you touched*, and none systematically generate adversarial stimulus to find the holes.

Manifold is that missing tool. You point it at a tool-using agent behind a one-method interface. You declare a **coverage model as data** — coverpoints (which tools, which input categories, which states), transition coverage (an FSM over tool-call sequences), and crosses. Manifold generates **seeded constrained-random scenarios** — a task input + mocked tool responses + injected faults (latency, errors, garbage, timeouts) — runs the agent against each, records a **structured trace** of every event, evaluates the trace against the coverage model and a set of **invariants** (e.g. *terminates within budget*, *never retries a failed tool forever*, *output matches schema*), and reports coverage (hit %, holes, heatmap) plus any failures **each carrying the exact seed to reproduce it**.

The headline demo: Manifold sweeps a small Claude-backed agent, reports ~80% coverpoint coverage, and flags an uncovered branch where the agent retries forever after a tool error — handing back `manifold repro 0x7a3f` and a one-line repro plus a coverage heatmap. That image — *coverage % + a real bug + a reproducible seed* — tells the whole story.

**Who it's for.** (1) The author's portfolio — it demonstrates verification-methodology depth, agentic-AI fluency, and rigorous test engineering, and sits under the same banner as the sibling projects **Inductor** and **Congruent**: *bringing chip-grade verification rigor to AI*. (2) Agent builders who want more than vibes: a way to measure thoroughness and find reliability bugs before users do.

### Engineering pillars — the 1–3 hardest things

1. **The trace schema + coverage model as data (the domain-critical core, §4).** Everything else is plumbing around these two artifacts. The trace must capture every decision-relevant event in a serializable, replayable form; the coverage model must express coverpoints/bins/transitions/crosses declaratively and evaluate cleanly against a trace. Get these shapes right and the rest follows; get them wrong and nothing composes.

2. **Reproducibility under nondeterminism.** Constrained-random + seeds presumes determinism — but LLM agents are nondeterministic. The resolution: Manifold makes the **environment** deterministic (seed → exact task input + tool mocks + faults + budgets, as a pure function), and treats the **agent** as a black box that may or may not be deterministic. For nondeterministic agents it runs **K repeats per seed** and reports per-seed flakiness — directly mirroring tau²-bench's `pass^k` reliability finding (a 90%-on-benchmark agent is often ~70% reliable in production). A seed always reproduces the *stimulus*; for a deterministic agent it reproduces the *run*.

3. **Coverage-directed generation (the closure loop).** Pure random generation plateaus. The thing that makes this *verification* rather than *fuzzing-by-luck* is biasing the sampler toward unhit bins using live coverage feedback — the agent-world analog of coverage-guided fuzzing (AFL/libFuzzer) and CDV constraint optimization. It must measurably close holes faster than uniform random on the example agents.

---

## 2. Goals / Non-goals

**Goals (v1 — each is testable):**

- **One-method agent port.** Wrap any tool-using agent behind a single `Agent` protocol; ship deterministic toy adapters and one real Claude-backed adapter implementing it.
- **Structured trace.** Record every event (tool call, args, result, state snapshot, decision, output, error, terminal) into a typed, JSONL-serializable `Trace`, replayable from disk.
- **Coverage model as data.** Declare coverpoints, bins, a transition FSM over tool-call sequences, and crosses; evaluate against traces into a `CoverageDB`; render hit %, holes, and a heatmap (terminal + static HTML).
- **Seeded constrained-random generation.** `seed → Scenario` is a pure function over a declared `ScenarioSpace`; scenarios include task input, per-tool (optionally stateful) mock responses, injected faults, and budgets.
- **Declarative invariants.** Predicates over a `Trace`; a violation is a `Failure` carrying its seed + minimal failing context. `manifold repro <seed>` re-runs it.
- **Closure loop.** `manifold run agent.py --scenarios 500 --seed 7` runs N scenarios, biases generation toward holes, and prints an end-of-run coverage report + ranked failures.
- **The demo that lands.** A Claude-backed example agent with a real reliability bug (infinite retry after tool error, or budget blowout) that Manifold discovers, reproduces from a seed, and visualizes on a heatmap — runnable offline from a recorded trace.

**Non-goals (v1) — deliberately out of scope (this section protects the build):**

- **Proof of correctness.** Manifold does coverage + falsification, *not* formal verification. Coverage is evidence of thoroughness, never a correctness guarantee — stated plainly in the README.
- **Full UVM parity.** No phasing, register model, scoreboard framework, or SystemVerilog-style constraint solver. We borrow the *methodology*, not the feature surface.
- **RL / training / agent improvement.** Manifold finds and reports; it does not fix or fine-tune.
- **Multi-agent swarms.** Single agent under test in v1.
- **Hosted UI / web dashboard / live server.** Terminal output + a single static HTML report file. No backend.
- **Distributed or parallel execution at scale.** Single-process v1; parallel scenario execution is a clearly-labeled later optimization (§8 M4).
- **Statistical coverage guarantees.** Coverage % is descriptive, not a probability bound over the true behavior space.
- **Auto-deriving the coverage model from the agent.** You *declare* it (with a starter library of generic agent coverpoints to make this cheap — §6.3).
- **Full-fidelity environment emulation.** Manifold mocks at the **tool boundary** and injects the failure modes that matter; it does not reproduce real external systems byte-for-byte.

---

## 3. Tech stack

Pinned to current stable as of **2026-06-28**.

| Layer | Choice | Why |
|---|---|---|
| Language | **Python 3.11+** | The agent ecosystem lives in Python; matches the foundational repo layout and the Claude SDKs (3.10+). Floor is 3.11 (`StrEnum`, modern typing) — the dev machine runs 3.11.9. |
| Packaging / env | **`pyproject.toml`, `src/` layout** (uv-compatible; `venv`+`pip` works) | Standard PEP 621 metadata installable by either `uv` or plain `pip` — no toolchain lock-in. Dev machine uses `venv`+`pip`; `uv` recommended when available. |
| Data model | **pydantic v2** | The trace/coverage/scenario schemas *are* the product — pydantic v2 gives typed, validated, JSON(L)-serializable models with fast (de)serialization for replay. |
| CLI | **typer** (on click) | Declarative typed commands (`run`, `repro`, `report`, `cover`), good help output, minimal boilerplate. |
| Terminal report | **rich** | Coverage tables, heatmap cells, progress bars, ranked-failure panels — the demo's terminal money shot. |
| HTML report | **Jinja2** → single self-contained `.html` | One static file with an inline SVG/CSS heatmap; no server, opens in a browser, screenshot-ready for the portfolio. |
| Testing | **pytest** | Manifold is a testing tool; its own suite must be exemplary. Includes a recorded-trace fixture so the Claude demo is tested offline. |
| Real-LLM example | **anthropic** SDK (tool-use loop), `claude-haiku-4-5` | The v1 Claude example is built directly on the `anthropic` tool-use loop for full control over the agent loop and cost (`claude-haiku-4-5`, $1/$5 per MTok). Gated behind `ANTHROPIC_API_KEY`. |
| Real-framework adapter (stretch) | **claude-agent-sdk** (MIT, 3.10+) | M4 adapter targets Anthropic's higher-level Agent SDK — "chip-grade verification for Claude agents." Verified on PyPI 2026-06-28. |
| Lint / format | **ruff** | Single fast tool for lint + format; current default. |

**Prior art deliberately *not* depended on:** Hypothesis (MPL-2.0) — referenced for strategies/shrinking design, but Manifold rolls its own seeded sampler (§6.4) and delta-debug shrinker (§8 M4) to keep the licence MIT-clean and the generator under our control for coverage-directed feedback.

---

## 4. The domain-critical core — Trace schema + Coverage model

This is where the design earns its keep. Two artifacts, specified exactly. Both are pydantic v2 models; both round-trip to JSONL for replay.

### 4.1 The Trace

A `Trace` is the totally-ordered record of one agent run against one scenario. Every event the coverage model or an invariant could care about must appear here.

```python
from enum import Enum
from typing import Any, Literal, Union
from pydantic import BaseModel, Field

class EventKind(str, Enum):
    TOOL_CALL    = "tool_call"     # agent requested a tool
    TOOL_RESULT  = "tool_result"   # environment returned (incl. injected fault)
    STATE        = "state"         # snapshot of agent-exposed state / decision point
    OUTPUT       = "output"        # agent produced a (partial or final) answer
    ERROR        = "error"         # agent raised / crashed
    TERMINAL     = "terminal"      # run ended: reason + budget consumed

class ToolCall(BaseModel):
    kind: Literal[EventKind.TOOL_CALL] = EventKind.TOOL_CALL
    step: int                       # monotonic step index within the run
    tool: str
    args: dict[str, Any]

class ToolResult(BaseModel):
    kind: Literal[EventKind.TOOL_RESULT] = EventKind.TOOL_RESULT
    step: int
    tool: str
    ok: bool
    value: Any | None = None
    fault: str | None = None        # which injected fault fired, if any (e.g. "error", "timeout", "garbage")
    latency_ms: int = 0

class StateSnapshot(BaseModel):
    kind: Literal[EventKind.STATE] = EventKind.STATE
    step: int
    label: str                      # e.g. "planning", "retrying", "awaiting_tool"
    data: dict[str, Any] = Field(default_factory=dict)

class AgentOutput(BaseModel):
    kind: Literal[EventKind.OUTPUT] = EventKind.OUTPUT
    step: int
    text: str | None = None
    payload: Any | None = None      # structured output, validated by invariants

class AgentError(BaseModel):
    kind: Literal[EventKind.ERROR] = EventKind.ERROR
    step: int
    etype: str
    message: str

class Terminal(BaseModel):
    kind: Literal[EventKind.TERMINAL] = EventKind.TERMINAL
    step: int
    reason: Literal["completed", "budget_steps", "budget_cost", "timeout", "error"]
    steps_used: int
    cost_used: float                # tokens or $ — whatever the budget meters
    wall_ms: int

Event = Union[ToolCall, ToolResult, StateSnapshot, AgentOutput, AgentError, Terminal]

class Trace(BaseModel):
    seed: int                       # the scenario seed — the reproduction key
    scenario_id: str                # stable hash of the resolved Scenario
    agent_id: str                   # adapter identity (e.g. "toy.retry_bug", "claude.haiku")
    repeat: int = 0                 # which of K repeats-per-seed this is
    events: list[Event] = Field(default_factory=list, discriminator="kind")

    # convenience views used by coverage + invariants
    def tools_called(self) -> list[str]: ...
    def symbol_sequence(self) -> list[str]: ...   # event → FSM symbol, for transition coverage
```

Invariant guaranteed by the harness: a well-formed `Trace` is non-empty and ends in exactly one `Terminal`. The harness enforces the budgets, so even a runaway agent yields a `Terminal(reason="budget_steps")` rather than hanging.

### 4.2 The Coverage model

The coverage model is **data**, not code paths. You build it from four primitives; the engine evaluates a `Trace` into bin hits and merges them into a `CoverageDB` that accumulates across an entire run.

```python
from collections.abc import Callable, Iterable

class Bin(BaseModel):
    name: str
    # a bin is "hit" if the predicate matches any extracted value in the trace
    predicate: Callable[[Any], bool]   # value-level: ==, range, set-membership, custom

class Coverpoint(BaseModel):
    name: str                          # e.g. "tool_called", "input_length", "final_state"
    extract: Callable[[Trace], Iterable[Any]]   # pull the values of interest from a trace
    bins: list[Bin]                    # bins should ideally partition the value space

class TransitionCoverpoint(BaseModel):
    name: str                          # e.g. "tool_fsm"
    # coverage over EDGES of an FSM defined on Trace.symbol_sequence()
    states: set[str]
    edges: set[tuple[str, str]]        # the edges you care to cover (the "bins")

class Cross(BaseModel):
    name: str
    points: tuple[str, str]            # cross two coverpoints → grid of (binA, binB) cells
    # optional: ignore/illegal cells you don't expect to hit

class CoverageModel(BaseModel):
    coverpoints: list[Coverpoint] = []
    transitions: list[TransitionCoverpoint] = []
    crosses: list[Cross] = []
```

Evaluation is a pure fold — `Trace × CoverageModel → CoverageDB(hits)` — and DBs merge associatively so the run-level total is just the merge over all per-trace DBs:

```python
class CoverageDB(BaseModel):
    # (coverpoint_or_cross_or_transition name) -> (bin/cell/edge name) -> hit count
    hits: dict[str, dict[str, int]] = Field(default_factory=dict)

    def merge(self, other: "CoverageDB") -> "CoverageDB": ...
    def pct(self, group: str | None = None) -> float: ...   # hit bins / total bins
    def holes(self) -> list[tuple[str, str]]:               # (group, bin) never hit
        ...

def evaluate(trace: Trace, model: CoverageModel) -> CoverageDB: ...
```

**Why this shape.** Coverpoints are value-level (extract → bin), transitions are edge-level over an explicit FSM, and crosses are the Cartesian product of two coverpoints' bins — exactly the UVM trio (`coverpoint`/`bins`, transition bins, `cross`), reduced to the minimum that's still expressive. "Coverage %" is unambiguous: hit bins over total declared bins, per group and overall. "Holes" is the literal driver of coverage-directed generation (§6.6).

### 4.3 Invariants and Scenarios (the other two load-bearing types)

```python
class Violation(BaseModel):
    invariant: str
    seed: int
    detail: str
    at_step: int | None = None
    excerpt: list[Event] = []          # minimal failing context

# an Invariant inspects a finished Trace; None = passed
Invariant = Callable[[Trace], Violation | None]

class ToolMock(BaseModel):
    tool: str
    # resolved deterministically from the seed; may be stateful (call index -> response)
    responses: list[Any]
    faults: list["Fault"] = []         # faults attached to specific calls

class Fault(BaseModel):
    at_call: int                       # which invocation of the tool
    kind: Literal["error", "timeout", "garbage", "latency"]
    detail: Any | None = None

class Scenario(BaseModel):
    seed: int
    task: Any                          # the input handed to the agent
    mocks: list[ToolMock]
    budgets: "Budgets"

class Budgets(BaseModel):
    max_steps: int = 30
    max_cost: float = 1.0              # tokens or $; meter is agent-defined
    wall_ms: int = 30_000
```

`Scenario` is produced **purely** from a seed by sampling a declared `ScenarioSpace` (§6.4). Same seed → byte-identical `Scenario` → identical stimulus. That is the whole reproducibility contract.

---

## 5. Architecture

**Pattern: ports & adapters (hexagonal).** A pure verification *core* (trace, coverage, invariants, generation, closure) depends on nothing concrete; **driven ports** abstract the agent-under-test and the tool environment; **driving adapters** are the CLI and the reporters. This is the right fit because the core must be *target-agnostic* — swap a toy agent for a Claude agent for a future framework adapter, or swap the terminal reporter for the HTML one, without touching the engine. Data flows one direction; every stochastic step takes a seed.

```
                          ┌──────────────────────── driving ────────────────────────┐
                          │  CLI (typer):  run · repro · report · cover              │
                          └───────────────────────────┬─────────────────────────────┘
                                                       │
        ┌──────────────────────────────────  CORE (pure, seedable)  ──────────────────────────────┐
        │                                                                                          │
   seed │   generate.py            harness.py            coverage.py         invariants.py          │
   ─────┼─► ScenarioSpace ─► Scenario ─► run(agent, scenario) ─► Trace ─► evaluate ─► CoverageDB    │
        │        ▲                              │                    │                              │
        │        │  closure.py: bias toward holes (coverage-directed feedback)                      │
        │        └────────────────────  CoverageDB.holes()  ◄────────┘          Failures(+seed)     │
        │                                                                            │              │
        └──────────────┬───────────────────────────────────┬─────────────────────── │ ─────────────┘
                       │ Agent port                         │ Tool/Env port          │ report.py
                       ▼                                     ▼                        ▼
              ┌────────────────┐                   ┌──────────────────┐     ┌───────────────────┐
              │ adapters/      │                   │ mocked tools +   │     │ rich terminal +   │
              │  toy_agents.py │                   │ fault injection  │     │ static HTML       │
              │  claude_agent  │  (anthropic loop) │ (seed-resolved)  │     │ heatmap           │
              └────────────────┘                   └──────────────────┘     └───────────────────┘
```

Refined repo layout (evolves the foundational sketch):

```
manifold/
  src/manifold/
    agent.py        # the Agent port (Protocol) + ToolEnv port
    harness.py      # run(agent, scenario) -> Trace; budget enforcement; tool interception
    trace.py        # §4.1 schema + JSONL (de)serialize
    model.py        # §4.2 CoverageModel primitives + a starter library of generic coverpoints
    coverage.py     # evaluate(trace, model) -> CoverageDB; merge/pct/holes
    invariants.py   # §4.3 invariant type + a starter library (terminates, no-infinite-retry, schema, no-X-after-Y)
    scenario.py     # §4.3 Scenario/ToolMock/Fault/Budgets
    generate.py     # ScenarioSpace + seeded constrained-random sampler + fault injection
    closure.py      # run loop + coverage-directed (hole-biased) feedback
    report.py       # rich terminal report + Jinja2 static HTML heatmap
    cli.py          # typer app
  examples/
    toy_agents.py   # deterministic agents incl. planted bugs (retry-forever, budget-blowout)
    claude_agent.py # real Claude tool-using agent (anthropic loop), + recorded trace for offline test
    spec_example.py # a worked CoverageModel + invariants + ScenarioSpace for an example agent
  tests/
  README.md  ROADMAP.md  pyproject.toml
```

---

## 6. Core systems

### 6.1 Harness & ports (`agent.py`, `harness.py`)

The agent contract is one method. The harness drives it step by step, intercepts every tool call to serve the scenario's seed-resolved mock (injecting faults), enforces budgets, and emits the `Trace`.

```python
from typing import Protocol

class ToolEnv(Protocol):
    """Handed to the agent; every call is recorded + may be faulted."""
    def call(self, tool: str, **args) -> Any: ...   # returns mock value or raises injected error

class Agent(Protocol):
    id: str
    def run(self, task: Any, env: ToolEnv, *, budget: "Budgets") -> Any:
        """Do the task using only `env` for tools. Return the final output.
        The harness records calls/results/state via `env` and the agent's
        optional `emit(event)` hook; it does not need to build the Trace itself."""

def run(agent: Agent, scenario: Scenario) -> Trace: ...
```

Budget enforcement lives in the harness, not the agent: a `ToolEnv.call` past `max_steps`/`max_cost`/`wall_ms` raises `BudgetExceeded`, which the harness converts into a `Terminal(reason="budget_*")`. This is what makes "agent retries forever" *observable and bounded* rather than a hang.

### 6.2 Trace (`trace.py`)

The §4.1 models plus `dump_jsonl(path)` / `load_jsonl(path)`. Traces are the unit of replay: `manifold repro <seed>` regenerates the scenario, re-runs, and (for deterministic agents) reproduces the trace exactly; the recorded Claude trace lets the demo/test run with no API call.

### 6.3 Coverage model & DB (`model.py`, `coverage.py`)

The §4.2 primitives, plus a **starter library** of generic agent coverpoints so authors don't start from zero — `tool_called` (bin per tool), `n_tool_calls` (range bins: 0,1,2–3,4–8,9+), `terminal_reason` (bin per reason), `fault_seen` (bin per fault kind), and a `tool_fsm` transition coverpoint with the high-value edge `tool_error → same_tool_call` (the retry-after-failure signature). Evaluation is the pure fold from §4.2; `CoverageDB.holes()` feeds §6.6.

### 6.4 Constrained-random generator (`scenario.py`, `generate.py`)

You declare a `ScenarioSpace` — typed choices with weights and constraints — and a seeded sampler resolves it to a concrete `Scenario`. We roll our own (not Hypothesis) so the sampler is a *pure `seed → Scenario` function* we can bias by coverage.

```python
class ScenarioSpace(BaseModel):
    tasks: list[Any]                              # or a generator of task inputs
    tools: list["ToolSpec"]                       # per-tool response generators + fault menu
    fault_rate: float = 0.2                       # P(any given call is faulted)
    budget_choices: list[Budgets]
    constraints: list[Callable[[Scenario], bool]] = []   # reject illegal combos

def sample(space: ScenarioSpace, seed: int,
           bias: "HoleBias | None" = None) -> Scenario:
    rng = random.Random(seed)                     # the only entropy source
    ...                                           # weighted choices; reject-and-resample on constraints
```

`HoleBias` (from `closure.py`) nudges the weighted choices toward values that map to unhit bins — e.g. raise the weight of a fault kind whose `fault_seen` bin is empty, or pick a task length that lands in an empty `input_length` bin. Determinism is preserved: bias changes the *weights*, the seed still picks the outcome.

### 6.5 Invariants (`invariants.py`)

The §4.3 `Invariant` type plus a starter set: `terminates_within_budget`, `no_infinite_retry` (no tool called > N times consecutively after a failure), `valid_output_schema(model)`, `never_calls_after_failure(x, y)`, `cost_under(budget)`. Each returns a `Violation` carrying the seed and the minimal failing excerpt.

### 6.6 Closure loop + coverage-directed feedback (`closure.py`)

The loop that ties it together and is the third engineering pillar:

```
db = CoverageDB()
failures = []
for i in range(n_scenarios):
    seed = base_seed + i
    bias = hole_bias(db) if coverage_directed and i >= warmup else None
    scenario = sample(space, seed, bias)
    for k in range(repeats):                      # K repeats-per-seed for nondeterministic agents
        trace = run(agent, scenario.with_repeat(k))
        db = db.merge(evaluate(trace, model))
        for inv in invariants:
            if (v := inv(trace)): failures.append(v)
    if db.pct() >= target: break                  # coverage closure
report(db, failures)
```

`hole_bias(db)` maps current holes back to sampler weights (§6.4). Success criterion: on the example agents, coverage-directed reaches a coverage target in measurably fewer scenarios than uniform random (a benchmark in `tests/`).

### 6.7 Reporting (`report.py`)

`rich` for the terminal: a coverage table (group, hit/total, %), a compact heatmap (hit = filled cell, hole = empty), and a ranked-failures panel (invariant, seed, one-line detail). `Jinja2` renders the same data to a single self-contained `report.html` with an inline-SVG heatmap — the screenshot artifact for the portfolio.

### 6.8 CLI surface (`cli.py`)

```
manifold run <agent.py[:Agent]> --spec <spec.py> [--scenarios N] [--seed S]
                                 [--repeats K] [--coverage-directed/--random]
                                 [--target 0.9] [--html report.html]
manifold repro <seed> <agent.py> --spec <spec.py>      # re-run one scenario, print its trace + verdict
manifold report <run-dir>                              # re-render a saved run (terminal or --html)
manifold cover  <spec.py>                              # list the declared coverage model (sanity check)
```

`--spec` points at a module exposing `MODEL: CoverageModel`, `INVARIANTS: list[Invariant]`, and `SPACE: ScenarioSpace` for the agent under test (see `examples/spec_example.py`).

---

## 7. The demo / CLI experience

The product *is* its output. The money shot:

```
$ manifold run examples/claude_agent.py --spec examples/spec_example.py --scenarios 500 --seed 7 --html report.html

Manifold · 500 scenarios · coverage-directed · agent=claude.haiku
Coverage ████████████████░░░░  82%   (41/50 bins)
  tool_called          5/5   100%
  n_tool_calls         4/5    80%   hole: [9+]
  terminal_reason      4/5    80%   hole: [timeout]
  fault_seen           3/4    75%   hole: [garbage]
  tool_fsm (edges)    25/31   81%   hole: error→search ×6

Failures (2)
  ✗ no_infinite_retry   seed=0x7a3f   search retried 12× after error (budget_steps)
  ✗ cost_under          seed=0x0c19   12.4 > 10.0 budget

Reproduce:  manifold repro 0x7a3f examples/claude_agent.py --spec examples/spec_example.py
HTML report → report.html
```

One screen says: *how much of the behavior space you covered, where the holes are, the real bugs found, and the exact command to reproduce each.* That is the entire pitch, rendered.

---

## 8. Milestones

Top-down and independently runnable.

- **M0 — Skeleton & it runs.** `Agent` port + `harness.run` records a `Trace` from a deterministic toy agent; one invariant (`terminates_within_budget`); `manifold run examples/toy_agents.py` prints the trace + a pass/fail; a planted-bug toy agent fails an invariant and the failure is reproduced from its seed via `manifold repro`. *Proves: the trace + reproduction contract end-to-end.*

- **M1 — Coverage model + report (credibility milestone).** The §4.2 primitives + starter coverpoints + transition coverage, evaluated into a `CoverageDB`, rendered as a terminal report and a static HTML heatmap on the toy agents. *Proves: the novel core — measured functional coverage of an agent's behavior space.*

- **M2 — Constrained-random generator + fault injection.** `ScenarioSpace` + seeded sampler + tool fault injection (error/timeout/garbage/latency) + K-repeats-per-seed; `manifold run … --scenarios 500 --seed 7` sweeps and aggregates coverage. *Proves: seeded, reproducible stimulus generation and per-seed flakiness reporting.*

- **M3 — Coverage-directed generation + the real Claude example + headline demo.** Hole-biased sampling in `closure.py`; the `anthropic`-loop Claude example agent with a real reliability bug; Manifold discovers it, reports ~80% coverage, hands back the seed + heatmap + one-line repro (runnable offline from the recorded trace). *Proves: the full closure loop and the portfolio demo (§7).*

- **M4 (stretch).** Crosses; failing-scenario **shrinking** (own delta-debug minimizer over `Scenario`); a `claude-agent-sdk` adapter ("verify the agent you already built"); optional parallel scenario execution. *Proves: depth + real-world applicability beyond toy/example agents.*

Ship publicly at **M1–M3**.

---

## 9. Risks / open questions

- **LLM nondeterminism vs. seeded reproducibility.** A seed can't make an LLM deterministic. → Make the *environment* deterministic (seed → exact stimulus, §4.3) and run **K repeats per seed**, reporting per-seed flakiness as a first-class result (the `pass^k` insight). A seed always reproduces the stimulus; for deterministic agents it reproduces the run.
- **Coverage-model authoring burden.** Authors must declare the model. → Ship a starter library of generic agent coverpoints + invariants (§6.3, §6.5) and a worked `spec_example.py`, so a useful run is ~20 lines.
- **"Coverage theater" (high % ≠ correct).** → Frame honestly in the README; *always* pair the coverage number with invariant failures, and bins are author-declared (you can't game what you didn't declare).
- **Tool-mock fidelity.** Mocking at the tool boundary diverges from real tools. → Scope it as *failure-mode* injection, not full emulation; document the boundary. Faults model the reliability hazards that actually break agents (errors, timeouts, garbage), which is the point.
- **Claude example cost/flakiness in CI.** → Gate behind `ANTHROPIC_API_KEY`; commit a recorded trace so the demo and its test run offline and free; use `claude-haiku-4-5` for any live run.
- **Hypothesis licence (MPL-2.0) creep.** → Do not depend on it; roll our own sampler + shrinker (already the plan, for control reasons too). Keep it prior-art reference only.
- **Open question — transition-FSM authoring.** Hand-declaring `states`/`edges` may be tedious for complex agents. v1 keeps it explicit and small; a future helper could infer the FSM skeleton from observed traces (noted, not v1).

---

## 10. References

**Prior art — what exists and what's missing**
- tau²-bench / `pass^k` reliability (variance per run; "90% benchmark ≈ 70% production reliability") — validates the reliability thesis. AgentBench, DeepEval, LangSmith, Braintrust, Arize — task-success evaluation on fixed datasets; *none* measure behavior-space coverage or do coverage-directed generation. Verified 2026-06-28.
- Coverage-guided fuzzing (AFL, libFuzzer) — the software ancestor of coverage-directed generation.
- Coverage-driven verification / UVM (constrained-random + functional coverage + closure) — the hardware methodology being transferred. Verified 2026-06-28.
- Hypothesis (property-based testing; strategies + shrinking + failing-example DB) — design reference for the generator and the M4 shrinker; **not** a dependency (MPL-2.0). Verified 6.x on 2026-06-28.

**Libraries / SDKs (verified 2026-06-28)**
- pydantic v2, typer, rich, Jinja2, pytest, ruff, uv — runtime/tooling, MIT/BSD/Apache-2.0.
- `anthropic` SDK (tool-use loop) with `claude-haiku-4-5` — the v1 Claude example.
- `claude-agent-sdk` (MIT, Python 3.10+, released 2026-06-24) — the M4 stretch adapter target.

**Family**
- Sibling projects **Inductor** and **Congruent** under one banner — *bringing chip-grade verification rigor to AI*. Manifold reuses that framing and could share a common reporting/CLI aesthetic with them.

**Origin**
