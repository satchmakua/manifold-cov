"""M0 acceptance tests: the harness records a well-formed trace, the planted bug is
caught by invariants, the fix is clean, runs are reproducible, and traces round-trip.
"""

from __future__ import annotations

from pathlib import Path
from types import ModuleType

from manifold.harness import run
from manifold.invariants import no_infinite_retry, terminates_within_budget
from manifold.trace import Terminal, Trace


def test_harness_records_well_formed_trace(toy: ModuleType, clean_seed: int) -> None:
    trace = run(toy.AGENTS["toy.echo"], toy.make_scenario(clean_seed))
    assert trace.events, "trace must be non-empty"
    assert isinstance(trace.terminal, Terminal)
    assert trace.terminal.reason == "completed"
    assert "search" in trace.tools_called()


def test_planted_bug_is_caught(toy: ModuleType, error_seed: int) -> None:
    trace = run(toy.AGENTS["toy.retry_forever"], toy.make_scenario(error_seed))
    assert isinstance(trace.terminal, Terminal)
    assert trace.terminal.reason == "budget_steps"  # bounded, not a hang
    assert no_infinite_retry()(trace) is not None
    assert terminates_within_budget(trace) is not None


def test_bounded_retry_is_clean(toy: ModuleType, error_seed: int) -> None:
    trace = run(toy.AGENTS["toy.bounded_retry"], toy.make_scenario(error_seed))
    assert no_infinite_retry()(trace) is None
    assert terminates_within_budget(trace) is None


def test_run_is_reproducible_from_seed(toy: ModuleType, error_seed: int) -> None:
    a = run(toy.AGENTS["toy.retry_forever"], toy.make_scenario(error_seed))
    b = run(toy.AGENTS["toy.retry_forever"], toy.make_scenario(error_seed))
    assert a.scenario_id == b.scenario_id
    assert a.tools_called() == b.tools_called()


def test_trace_roundtrips_jsonl(toy: ModuleType, clean_seed: int, tmp_path: Path) -> None:
    trace = run(toy.AGENTS["toy.echo"], toy.make_scenario(clean_seed))
    path = tmp_path / "trace.jsonl"
    trace.dump_jsonl(path)
    loaded = Trace.load_jsonl(path)
    assert loaded.seed == trace.seed
    assert loaded.scenario_id == trace.scenario_id
    assert [e.kind for e in loaded.events] == [e.kind for e in trace.events]
