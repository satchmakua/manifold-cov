"""A **real** toolset, recorded — mocks that are recordings, not inventions (post-v1 W5).

The honest objection this answers: every other example here invents both the tool *and* its
responses. ``ToolSpec(name="search", responses=["search-result-A", "search-result-B"])`` is
a pair of strings a human typed. A pass against that measures "correct against responses I
imagined," and nothing checked whether they resembled a real tool. That made "are the mocks
faithful?" unanswerable — because nothing real was on the other side.

Here there is. ``search`` and ``fetch`` below are **real implementations** that really grep
and really read real files in ``examples/corpus/``. The response pool in ``SPACE`` is not
authored — it is **recorded from those tools actually executing** (``examples/recorded/
tool_cassette.json``, regenerate with ``python examples/real_tools.py --record``). And
``tests/test_cassette.py`` re-runs the live tools against every recorded call, so a stale
recording *fails CI* instead of quietly becoming fiction.

**What this does and does not buy** (stated plainly, because the distinction is the point):

* It makes the *response* side real and **drift-checked**: the pool is machine-captured from
  real execution, and its fidelity to the live tool is continuously verified.
* It does **not** make faults real. ``error``/``timeout``/``garbage``/``latency`` remain
  deliberate *models* of failure modes, not emulation of an external system (DESIGN scope).
* A recording is a **sample** of a real tool's behavior space, never the whole of it. This
  demonstrates the mechanism on a local, deterministic tool — where the drift check can run
  free and offline in CI. A remote flaky API would record the same way, but its drift check
  needs the network, and its failure modes we still *model*.

So the residual limit is real and smaller: "the mocks are a checked recording of a real
tool, sampled" — not "the mocks are invented."
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from manifold.agent import ToolEnv
from manifold.cassette import Cassette, digest, record
from manifold.generate import ScenarioSpace, ToolSpec
from manifold.harness import ToolError
from manifold.invariants import starter_library
from manifold.model import (
    CoverageModel,
    fault_by_tool_cp,
    fault_seen_cp,
    n_tool_calls_cp,
    terminal_reason_cp,
    tool_called_cp,
)
from manifold.scenario import Budgets

CORPUS = Path(__file__).resolve().parent / "corpus"
CASSETTE = Path(__file__).resolve().parent / "recorded" / "tool_cassette.json"
TOOLS = ["search", "fetch"]
_MAX_HITS = 4
_MAX_CHARS = 300


# --- The real tools (these actually touch the filesystem) ----------------------


def search(query: str) -> str:
    """Really grep the corpus for ``query``; return real matching lines."""
    hits: list[str] = []
    for path in sorted(CORPUS.glob("*.md")):  # sorted: real, and deterministic
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if query.lower() in line.lower():
                hits.append(f"{path.name}:{n}: {line.strip()}")
    if not hits:
        return "no results"
    return "\n".join(hits[:_MAX_HITS])


def fetch(path: str) -> str:
    """Really read a corpus document; return its real opening text."""
    target = CORPUS / Path(path).name  # basename only: no traversal out of the corpus
    if not target.exists() or target.suffix != ".md":
        return f"error: no such document {path!r}"
    return target.read_text(encoding="utf-8")[:_MAX_CHARS]


REAL_TOOLS = {"search": search, "fetch": fetch}

# The calls to record — real questions against the real corpus.
_RECORD_CALLS: dict[str, list[dict[str, Any]]] = {
    "search": [
        {"query": "coverage"},
        {"query": "retry"},
        {"query": "delta debugging"},
        {"query": "budget"},
        {"query": "flakiness"},
        {"query": "quantum tunnelling"},  # a real miss -> the real "no results" response
    ],
    "fetch": [
        {"path": "coverage.md"},
        {"path": "generation.md"},
        {"path": "reliability.md"},
        {"path": "nonexistent.md"},  # a real error -> the real error string
    ],
}


def corpus_digest() -> str:
    """Digest of the corpus the cassette was recorded against."""
    return digest(*(p.read_text(encoding="utf-8") for p in sorted(CORPUS.glob("*.md"))))


def _load_cassette() -> Cassette:
    if not CASSETTE.exists():  # not recorded yet — fail loudly, don't invent a pool
        raise RuntimeError(
            f"no tool cassette at {CASSETTE}; record one with "
            "`python examples/real_tools.py --record`"
        )
    return Cassette.load(CASSETTE)


# --- The agent under test ------------------------------------------------------


class CorpusAgent:
    """Searches the corpus, then fetches the top hit's document. Bounded retry, then
    degrades gracefully — the same well-behaved shape as `research.pipeline`."""

    id = "real.corpus"

    def run(self, task: Any, env: ToolEnv, budget: Budgets) -> Any:
        hits = None
        for _ in range(2):
            try:
                hits = env.call("search", query=str(task))
                break
            except ToolError:
                env.state("retrying", tool="search")
        if hits is None:
            return "I was unable to search the corpus, so I cannot answer."

        doc = str(hits).split(":", 1)[0] if ":" in str(hits) else "coverage.md"
        for _ in range(2):
            try:
                body = env.call("fetch", path=doc)
                return {"answer": f"from {doc}: {str(body)[:120]}"}
            except ToolError:
                env.state("retrying", tool="fetch")
        return f"I found {doc} but was unable to read it."


AGENTS = {a.id: a for a in (CorpusAgent(),)}

_TASKS = ["coverage", "retry", "budget", "flakiness", "delta debugging"]

# The response pools come from the CASSETTE — real recorded tool output, not hand-written.
_CASSETTE = _load_cassette() if CASSETTE.exists() else Cassette()

SPACE = ScenarioSpace(
    tasks=_TASKS,
    tools=[
        ToolSpec(
            name=name,
            responses=_CASSETTE.responses(name),  # <- recorded from the real tool
            faults=["error", "timeout", "garbage", "latency"],  # <- still models, honestly
        )
        for name in TOOLS
    ],
    fault_rate=0.4,
    persistent_prob=0.5,
    budget_choices=[
        Budgets(max_steps=10, max_cost=100.0, wall_ms=30_000),
        Budgets(max_steps=4, max_cost=100.0, wall_ms=30_000),
    ],
)

MODEL = CoverageModel(
    coverpoints=[
        tool_called_cp(TOOLS),
        fault_seen_cp(),
        fault_by_tool_cp(TOOLS),
        n_tool_calls_cp(),
        terminal_reason_cp(),
    ],
)

INVARIANTS = starter_library()


def _record(recorded_at: str) -> None:
    """Run the real tools and (re)write the cassette. `recorded_at` is passed in, never
    read from the clock inside the library (ADR-0002)."""
    cassette = record(
        REAL_TOOLS, _RECORD_CALLS, recorded_at=recorded_at, source_digest=corpus_digest()
    )
    CASSETTE.parent.mkdir(parents=True, exist_ok=True)
    cassette.dump(CASSETTE)
    n = sum(len(t) for t in cassette.tools.values())
    print(f"recorded {n} real tool calls -> {CASSETTE}")
    print(f"  corpus digest: {cassette.source_digest}")
    for name, takes in cassette.tools.items():
        print(f"  {name}: {len(takes)} takes; first response: {takes[0].response!r:.70}")


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--record" in args:
        stamp = ""
        if "--at" in args:  # optional: `--at 2026-07-14` for a reproducible stamp
            stamp = args[args.index("--at") + 1]
        _record(stamp)
    else:
        cas = _load_cassette()
        from manifold.cassette import drift

        print(f"cassette: {sum(len(t) for t in cas.tools.values())} recorded real calls")
        print(f"  recorded_at={cas.recorded_at!r} source_digest={cas.source_digest}")
        found = drift(cas, REAL_TOOLS)
        if found:
            print(f"  DRIFT ({len(found)}) — the recording no longer matches the live tools:")
            for d in found:
                print(f"    {d}")
            raise SystemExit(1)
        print("  no drift: every recorded response still matches the live tool")
