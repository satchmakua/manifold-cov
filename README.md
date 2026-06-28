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

**Status:** **M0 shipped** — the trace + reproduction core runs and finds a planted
retry-forever bug. See [ROADMAP.md](ROADMAP.md) for the plan. Next up: the coverage model + heatmap (M1).

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

Then see Manifold find (and reproduce) a real agent-reliability bug:

```bash
manifold run examples/toy_agents.py --agent toy.retry_forever --scenarios 12
manifold repro 1 examples/toy_agents.py --agent toy.retry_forever
```

The first sweeps 12 seeded scenarios and reports which seeds make the agent retry a
failing tool forever (blowing its step budget); the second replays one seed and prints
the full event trace with the failing invariants. Try `--agent toy.bounded_retry` to
see the fix come back clean.

### Commands

| Command | What it does |
|---|---|
| `manifold run <file> [--agent NAME] [--scenarios N] [--seed S]` | Sweep N seeded scenarios; report invariant failures + a repro for each. |
| `manifold repro <seed> <file> [--agent NAME]` | Re-run one scenario by seed; print its full trace + verdict. |
| `ruff check . && mypy && pytest` | Lint, typecheck (strict), and run the test suite. |

A target module exposes `AGENTS: dict[str, Agent]` and `make_scenario(seed) -> Scenario`
— see [`examples/toy_agents.py`](examples/toy_agents.py).

---

## Project docs

| Doc | What's in it |
|---|---|
| [DESIGN.md](DESIGN.md) | The full design and rationale — the single source of truth. |
| [ROADMAP.md](ROADMAP.md) | The milestone checklist (the plan + what's done). |
| [`docs/`](docs/) | Architecture Decision Records and longer-form notes. |

## Tech stack

Python 3.11+ · pydantic v2 · typer · rich · pytest · ruff · mypy (strict). Optional
extras: `jinja2` (HTML report, M1) and `anthropic` (the Claude example, M3). MIT-licensed
and dependency-clean by design (no copyleft deps). Ports & adapters architecture.

## License

MIT — see [LICENSE](LICENSE). Part of a family bringing chip-grade verification rigor to
AI (alongside *Inductor* and *Congruent*).
