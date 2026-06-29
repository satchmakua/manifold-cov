# Manifold

> **Functional coverage and constrained-random verification for AI agents.**

Hardware verification has a mature answer to "did you test enough?" — constrained-random
stimulus + functional coverage (UVM). The agent world doesn't. Manifold brings that
discipline to tool-using AI agents: define a behavior space, generate seeded scenarios
with fault injection, **measure how much of that space you've actually exercised**, and
systematically hunt reliability bugs — handing back the exact seed to reproduce each one.

Point it at an agent, and one screen tells the whole story: coverage %, where the holes
are, the bugs found, and a one-line `manifold repro <seed>` for each.

> Coverage is evidence of thoroughness, not a proof of correctness. Manifold does
> coverage + falsification — it finds bugs and measures how much of the behavior space
> you've covered.

**Status:** **M3 shipped — the v1 vertical slice runs end to end.** Manifold generates
seeded constrained-random scenarios with fault injection, measures functional coverage of
the behavior space (static HTML heatmap), biases generation toward holes
(`--coverage-directed`, verified to beat uniform random), surfaces per-seed **flakiness**
(the `pass^k` signal), and finds reliability bugs in both toy agents and a real
**Claude-backed** agent — handing back the exact seed to reproduce each. See
[ROADMAP.md](ROADMAP.md). Next: the M4 stretch (scenario
shrinking, a `claude-agent-sdk` adapter).

---

## Run it

**Prerequisites:** Python ≥ 3.11 (check: `python --version`). The dev machine uses
`venv` + `pip`; [`uv`](https://docs.astral.sh/uv/) works too if you have it.

```bash
python -m venv .venv
# Windows:        .venv\Scripts\activate
# macOS / Linux:  source .venv/bin/activate
pip install -e ".[dev]"
```

Then see Manifold measure coverage and find (and reproduce) a real agent-reliability bug:

```bash
manifold run examples/toy_agents.py --spec examples/spec_example.py \
    --agent toy.retry_forever --scenarios 50 --html report.html
manifold repro 1 examples/toy_agents.py --agent toy.retry_forever
manifold cover examples/spec_example.py
```

The first sweeps 50 generated scenarios, prints a **coverage table with the holes named**,
reports which seeds make the agent retry a failing tool forever, and writes a
self-contained `report.html` heatmap. The second replays one seed and prints the full
event trace with the failing invariants. The third lists the declared coverage model. Try
`--agent toy.bounded_retry` to see the fix come back clean.

To see per-seed **flakiness** (the same seed passing on some runs and failing on others —
exactly how a nondeterministic LLM agent behaves), run the nondeterministic example with
repeats:

```bash
manifold run examples/toy_agents.py --spec examples/spec_example.py \
    --agent toy.flaky_retry --scenarios 60 --repeats 3
```

Add `--coverage-directed` to bias generation toward the holes (it reaches higher coverage
in fewer scenarios than the default `--random`). And see Manifold find a retry-to-budget
bug in a **real Claude-backed agent** — offline, no API key needed (it analyses a committed
recorded trace):

```bash
python examples/claude_agent.py
# Live (needs ANTHROPIC_API_KEY + pip install "manifold-cov[claude]"):
#   manifold run examples/claude_agent.py --spec examples/spec_example.py --scenarios 10 --coverage-directed
```

### Commands

| Command | What it does |
|---|---|
| `manifold run <file> [--spec S] [--agent NAME] [--scenarios N] [--seed S] [--repeats K] [--coverage-directed] [--html PATH]` | Sweep N seeded scenarios (×K repeats); report coverage % + holes, flaky seeds, and invariant failures with a repro for each. |
| `manifold repro <seed> <file> [--spec S] [--agent NAME]` | Re-run one scenario by seed; print its full trace + verdict. |
| `manifold cover <spec_file>` | List the declared coverage model (coverpoints, FSM edges, crosses). |
| `ruff check . && mypy && pytest` | Lint, typecheck (strict), and run the test suite. |

A target module exposes `AGENTS: dict[str, Agent]` and `make_scenario(seed) -> Scenario`;
a spec module exposes `MODEL: CoverageModel` and `INVARIANTS` — see
[`examples/toy_agents.py`](examples/toy_agents.py) and
[`examples/spec_example.py`](examples/spec_example.py).

---

## Project docs

| Doc | What's in it |
|---|---|
| [DESIGN.md](DESIGN.md) | The full design and rationale — the single source of truth. |
| [ROADMAP.md](ROADMAP.md) | The milestone checklist (the plan + what's done). |
| [`docs/`](docs/) | Architecture Decision Records and longer-form notes. |

## Tech stack

Python 3.11+ · pydantic v2 · typer · rich · pytest · ruff · mypy (strict). The HTML
report uses the standard library only; the one optional extra is `anthropic` (the Claude
example, M3). MIT-licensed and dependency-clean by design (no copyleft deps). Ports &
adapters architecture.

## License

MIT — see [LICENSE](LICENSE). Part of a family bringing chip-grade verification rigor to
AI (alongside *Inductor* and *Congruent*).
