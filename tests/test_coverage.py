"""M1 acceptance tests: the coverage model evaluates against traces, holes are the
ones we expect (revealing what the M0 scenarios never exercise), merge is monotonic,
percentages are well-formed, and the HTML report writes a self-contained file.
"""

from __future__ import annotations

from pathlib import Path
from types import ModuleType

from manifold.coverage import CoverageDB, evaluate
from manifold.harness import run
from manifold.model import CoverageModel, default_model
from manifold.report import write_html


def _sweep_db(toy: ModuleType, model: CoverageModel, agent_id: str, n: int = 80) -> CoverageDB:
    db = CoverageDB()
    for s in range(n):
        trace = run(toy.AGENTS[agent_id], toy.make_scenario(s))
        db = db.merge(evaluate(trace, model))
    return db


def test_default_model_evaluates(toy: ModuleType, clean_seed: int) -> None:
    trace = run(toy.AGENTS["toy.echo"], toy.make_scenario(clean_seed))
    db = evaluate(trace, default_model())
    assert db.hits["n_tool_calls"].get("1", 0) == 1
    assert db.hits["terminal_reason"].get("completed", 0) == 1
    assert db.hits["fault_seen"].get("none", 0) == 1
    assert 0 < db.pct() < 100  # one trace can't cover the whole declared space


def test_sweep_reveals_expected_holes(toy: ModuleType, spec: ModuleType) -> None:
    db = _sweep_db(toy, spec.MODEL, "toy.retry_forever")
    holes = set(db.holes())
    # Faults we never inject must show up as holes — the model surfaces untested space.
    assert ("fault_seen", "garbage") in holes
    assert ("fault_seen", "timeout") in holes
    # M0 outages are persistent-or-clean, never transient, so "recovery" is never tested:
    assert ("tool_fsm", "search:err->search:ok") in holes
    # ...but the retry-after-failure signature edge IS exercised:
    assert ("tool_fsm", "search:err->search:err") not in holes
    assert 0 < db.pct() < 100


def test_merge_is_monotonic(
    toy: ModuleType, spec: ModuleType, error_seed: int, clean_seed: int
) -> None:
    a = evaluate(run(toy.AGENTS["toy.retry_forever"], toy.make_scenario(error_seed)), spec.MODEL)
    b = evaluate(run(toy.AGENTS["toy.echo"], toy.make_scenario(clean_seed)), spec.MODEL)
    merged = a.merge(b)
    assert merged.pct() >= a.pct()
    assert merged.pct() >= b.pct()


def test_pct_is_well_formed(toy: ModuleType, spec: ModuleType) -> None:
    db = _sweep_db(toy, spec.MODEL, "toy.retry_forever", n=40)
    for g in db.totals:
        assert 0 <= db.pct(g) <= 100
    assert 0 <= db.pct() <= 100


def test_html_report_is_self_contained(
    toy: ModuleType, spec: ModuleType, tmp_path: Path
) -> None:
    db = _sweep_db(toy, spec.MODEL, "toy.retry_forever", n=20)
    out = tmp_path / "report.html"
    write_html(db, [], out)
    text = out.read_text(encoding="utf-8")
    assert "<!doctype html>" in text
    assert "<style>" in text  # CSS inlined — no external assets
    assert "n_tool_calls" in text
    assert "tool_fsm" in text
