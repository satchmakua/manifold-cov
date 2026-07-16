"""W5: recorded tool cassettes — the mocks are recordings of a real tool, and their
fidelity is *checked*, not assumed.

The headline test is ``test_no_drift_between_the_cassette_and_the_live_tools``: it re-runs
every recorded call against the **live** tools, so a stale recording fails CI instead of
quietly becoming fiction. It runs free and offline because the real tools here are local.
``test_drift_is_detected_when_a_tool_changes`` proves the check can actually *fail* — a
fidelity check that can only pass is worthless.
"""

from __future__ import annotations

from pathlib import Path
from types import ModuleType
from typing import Any

from manifold.cassette import Cassette, Take, digest, drift, record


def test_cassette_round_trips(tmp_path: Path) -> None:
    c = Cassette(
        tools={"search": [Take(args={"query": "x"}, response="hit")]},
        recorded_at="2026-07-14",
        source_digest="abc123",
    )
    p = tmp_path / "c.json"
    c.dump(p)
    back = Cassette.load(p)
    assert back.recorded_at == "2026-07-14"
    assert back.source_digest == "abc123"
    assert back.responses("search") == ["hit"]
    assert back.tools["search"][0].args == {"query": "x"}


def test_record_captures_what_the_real_tool_actually_returns() -> None:
    # record() must capture real execution, not a description of it.
    def upper(text: str) -> str:
        return text.upper()

    c = record({"upper": upper}, {"upper": [{"text": "abc"}, {"text": "de"}]})
    assert c.responses("upper") == ["ABC", "DE"]  # exactly what the real callable returned


def test_record_captures_a_real_error_as_real_behavior() -> None:
    def boom(x: str) -> str:
        raise ValueError(f"bad {x}")

    c = record({"boom": boom}, {"boom": [{"x": "input"}]})
    assert c.responses("boom") == ["ValueError: bad input"]


def test_no_drift_between_the_cassette_and_the_live_tools(real_tools: ModuleType) -> None:
    # THE check that makes "are the mocks faithful?" answerable: every recorded response
    # must still be exactly what the live tool returns today.
    cassette = Cassette.load(real_tools.CASSETTE)
    assert drift(cassette, real_tools.REAL_TOOLS) == []


def test_cassette_digest_still_matches_the_corpus(real_tools: ModuleType) -> None:
    # If the corpus is edited without re-recording, the recording is stale — catch it.
    cassette = Cassette.load(real_tools.CASSETTE)
    assert cassette.source_digest == real_tools.corpus_digest()


def test_drift_is_detected_when_a_tool_changes(real_tools: ModuleType) -> None:
    # A fidelity check that can't fail proves nothing. Swap in a "drifted" tool.
    cassette = Cassette.load(real_tools.CASSETTE)

    def drifted_search(query: str) -> str:
        return "the tool changed underneath the recording"

    found = drift(cassette, {**real_tools.REAL_TOOLS, "search": drifted_search})
    assert found  # drift reported
    assert all("search(" in d for d in found)
    assert any("live returns" in d for d in found)


def test_drift_reports_a_missing_live_tool(real_tools: ModuleType) -> None:
    found = drift(Cassette.load(real_tools.CASSETTE), {})  # nothing to check against
    assert found and all("no live tool" in d for d in found)


def test_space_response_pools_come_from_the_cassette(real_tools: ModuleType) -> None:
    # The load-bearing claim: the mock pool is recorded, not hand-authored.
    cassette = Cassette.load(real_tools.CASSETTE)
    for spec in real_tools.SPACE.tools:
        assert spec.responses == cassette.responses(spec.name)
        assert spec.responses, f"{spec.name} pool is empty"


def test_recorded_pool_holds_real_tool_output_including_a_miss_and_an_error(
    real_tools: ModuleType,
) -> None:
    # Real behavior, not just the happy path: a genuine miss and a genuine error string.
    cassette = Cassette.load(real_tools.CASSETTE)
    searches = cassette.responses("search")
    fetches = cassette.responses("fetch")
    assert "no results" in searches  # a real query that really matched nothing
    assert any(str(f).startswith("error: no such document") for f in fetches)
    # and the hits are real corpus lines, produced by really grepping real files
    assert any("coverage.md:" in str(s) for s in searches)
    assert any("# Functional coverage" in str(f) for f in fetches)


def test_digest_is_stable_and_order_sensitive() -> None:
    assert digest("a", "b") == digest("a", "b")
    assert digest("a", "b") != digest("b", "a")
    assert digest("ab", "") != digest("a", "b")  # the separator prevents collisions


def test_scenario_from_the_cassette_serves_a_recorded_response(real_tools: ModuleType) -> None:
    # End to end: sample a scenario off the cassette-backed space and confirm the mock
    # actually serves a real recorded string through the harness.
    from manifold.generate import sample
    from manifold.harness import run
    from manifold.trace import ToolResult

    cassette = Cassette.load(real_tools.CASSETTE)
    pool = set(map(str, cassette.responses("search")))
    for seed in range(30):
        scn = sample(real_tools.SPACE, seed)
        if any(m.tool == "search" and not m.faults for m in scn.mocks):
            trace = run(real_tools.AGENTS["real.corpus"], scn)
            served: list[Any] = [
                e.value for e in trace.events
                if isinstance(e, ToolResult) and e.tool == "search" and e.ok and e.fault is None
            ]
            if served:
                assert str(served[0]) in pool  # a real recorded response, not an invention
                return
    raise AssertionError("no clean search scenario found in range(30)")
