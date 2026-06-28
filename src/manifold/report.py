"""Reporting (DESIGN.md §6.7).

A ``rich`` terminal coverage table for the CLI, and a single self-contained static
HTML heatmap for the portfolio screenshot. The HTML is built with stdlib string
templating (no template-engine dependency) — the heatmap is a simple grid of cells.
"""

from __future__ import annotations

import html
from collections.abc import Sequence
from pathlib import Path

from rich.table import Table

from manifold.coverage import CoverageDB
from manifold.invariants import Violation


def coverage_table(db: CoverageDB) -> Table:
    table = Table(title=f"Coverage — {db.pct():.0f}% overall")
    table.add_column("group")
    table.add_column("hit/total", justify="right")
    table.add_column("%", justify="right")
    table.add_column("holes")
    holes_by_group: dict[str, list[str]] = {}
    for g, b in db.holes():
        holes_by_group.setdefault(g, []).append(b)
    for g in db.totals:
        hit, total = db.hit_total(g)
        group_holes = holes_by_group.get(g, [])
        holes_text = _summarize(group_holes)
        bar = _bar(db.pct(g))
        table.add_row(g, f"{hit}/{total}", f"{bar} {db.pct(g):.0f}%", holes_text)
    return table


def _summarize(holes: list[str], limit: int = 5) -> str:
    if not holes:
        return "[green]—[/]"
    if len(holes) <= limit:
        return ", ".join(holes)
    return ", ".join(holes[:limit]) + f", [dim]+{len(holes) - limit} more[/]"


def _bar(pct: float, width: int = 10) -> str:
    filled = round(pct / 100 * width)
    return "[green]" + "█" * filled + "[/]" + "░" * (width - filled)


_CSS = """
:root { color-scheme: light dark; }
body { font: 15px/1.5 ui-sans-serif, system-ui, sans-serif; margin: 2rem auto; max-width: 900px;
       padding: 0 1rem; }
h1 { margin-bottom: .2rem; } .sub { color: #888; margin-top: 0; }
h2 { margin: 1.4rem 0 .4rem; font-size: 1.05rem; }
.grid { display: flex; flex-wrap: wrap; gap: 6px; }
.cell { display: inline-flex; flex-direction: column;
        align-items: center; justify-content: center;
        min-width: 78px; padding: 8px 6px;
        border-radius: 6px; font-size: 12px; text-align: center; }
.cell b { font-size: 14px; }
.hit  { background: #1f9d55; color: #fff; }
.hole { background: #2a2a2a10; border: 1px dashed #999; color: #888; }
table { border-collapse: collapse; width: 100%; margin-top: .5rem; }
td, th { border-bottom: 1px solid #8884; padding: 6px 8px; text-align: left; font-size: 13px; }
.pct { font-weight: 700; }
"""


def write_html(
    db: CoverageDB,
    failures: Sequence[Violation],
    path: str | Path,
    *,
    title: str = "Manifold coverage report",
) -> None:
    sections: list[str] = []
    for g in db.totals:
        hit, total = db.hit_total(g)
        got = db.hits.get(g, {})
        cells = []
        for b in db.totals[g]:
            n = got.get(b, 0)
            cls = "hit" if n > 0 else "hole"
            label = html.escape(b)
            cells.append(
                f"<span class='cell {cls}' title='{label}: {n}'>{label}<br><b>{n}</b></span>"
            )
        head = (
            f"<h2>{html.escape(g)} — "
            f"<span class='pct'>{db.pct(g):.0f}%</span> ({hit}/{total})</h2>"
        )
        sections.append(f"{head}<div class='grid'>{''.join(cells)}</div>")

    fail_rows = "".join(
        f"<tr><td>{html.escape(f.invariant)}</td><td>0x{f.seed:x}</td>"
        f"<td>{html.escape(f.detail)}</td></tr>"
        for f in failures
    )
    fail_table = (
        f"<h2>Failures ({len(failures)})</h2>"
        f"<table><tr><th>invariant</th><th>seed</th><th>detail</th></tr>{fail_rows}</table>"
        if failures
        else "<h2>Failures (0)</h2><p>No invariant violations.</p>"
    )

    doc = (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{html.escape(title)}</title><style>{_CSS}</style></head><body>"
        f"<h1>{html.escape(title)}</h1>"
        f"<p class='sub'>Overall coverage: <span class='pct'>{db.pct():.0f}%</span> · "
        f"{len(db.holes())} holes · {len(failures)} failures</p>"
        f"{''.join(sections)}{fail_table}</body></html>"
    )
    Path(path).write_text(doc, encoding="utf-8")
