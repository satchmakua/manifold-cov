"""Reporting (DESIGN.md §6.7).

A ``rich`` terminal coverage table for the CLI, a self-contained static HTML heatmap,
and a self-contained SVG coverage-vs-scenarios curve — all built with stdlib string
templating (no template-engine or plotting dependency).
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


def _first_reaching(traj: Sequence[float], target: float) -> int | None:
    return next((i + 1 for i, p in enumerate(traj) if p >= target), None)


def write_curve_svg(
    directed: Sequence[float],
    random_: Sequence[float],
    path: str | Path,
    *,
    title: str = "Coverage vs scenarios — directed vs random",
    target: float = 90.0,
) -> None:
    """A self-contained SVG line chart of the two coverage trajectories (no plotting dep)."""
    n = max(len(directed), len(random_), 2)
    lx, rx, ty, by = 64, 736, 48, 344
    pw, ph = rx - lx, by - ty

    def x(i: int) -> float:
        return lx + (i / (n - 1)) * pw

    def y(p: float) -> float:
        return ty + (1 - p / 100) * ph

    def polyline(traj: Sequence[float], color: str) -> str:
        pts = " ".join(f"{x(i):.1f},{y(p):.1f}" for i, p in enumerate(traj))
        return f"<polyline fill='none' stroke='{color}' stroke-width='2.5' points='{pts}'/>"

    def txt(xx: float, yy: float, s: str, *, size: int = 11, fill: str = "#888",
            anchor: str = "middle", weight: str = "normal") -> str:
        w = f" font-weight='{weight}'" if weight != "normal" else ""
        return (f"<text x='{xx:.1f}' y='{yy:.1f}' text-anchor='{anchor}' "
                f"font-size='{size}'{w} fill='{fill}'>{s}</text>")

    grid = []
    for pct in (0, 25, 50, 75, 100):
        yy = y(pct)
        grid.append(f"<line x1='{lx}' y1='{yy:.1f}' x2='{rx}' y2='{yy:.1f}' stroke='#8883'/>")
        grid.append(txt(lx - 8, yy + 4, f"{pct}%", anchor="end"))
    for frac in (0, 0.25, 0.5, 0.75, 1.0):
        i = round(frac * (n - 1))
        grid.append(txt(x(i), by + 20, str(i + 1)))

    target_line = (
        f"<line x1='{lx}' y1='{y(target):.1f}' x2='{rx}' y2='{y(target):.1f}' "
        f"stroke='#c05621' stroke-width='1' stroke-dasharray='5 4'/>"
        + txt(rx, y(target) - 6, f"{target:.0f}% target", fill="#c05621", anchor="end")
    )

    markers = []
    for traj, color, name in ((directed, "#1f9d55", "directed"), (random_, "#3b6fb0", "random")):
        at = _first_reaching(traj, target)
        if at is not None:
            cx, cy = x(at - 1), y(traj[at - 1])
            markers.append(
                f"<circle cx='{cx:.1f}' cy='{cy:.1f}' r='4' fill='{color}'/>"
                + txt(cx, cy - 9, f"{name} @ N={at}", fill=color)
            )

    legend = (
        f"<rect x='{lx + 8}' y='{ty + 8}' width='12' height='12' fill='#1f9d55'/>"
        + txt(lx + 26, ty + 18, "coverage-directed", size=12, fill="#333", anchor="start")
        + f"<rect x='{lx + 8}' y='{ty + 28}' width='12' height='12' fill='#3b6fb0'/>"
        + txt(lx + 26, ty + 38, "random", size=12, fill="#333", anchor="start")
    )

    header = (
        "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 800 380' "
        "font-family='system-ui, sans-serif'><rect width='800' height='380' fill='white'/>"
    )
    axes = (
        f"<line x1='{lx}' y1='{ty}' x2='{lx}' y2='{by}' stroke='#444'/>"
        f"<line x1='{lx}' y1='{by}' x2='{rx}' y2='{by}' stroke='#444'/>"
    )
    svg = (
        header
        + txt(400, 24, html.escape(title), size=15, fill="#222", weight="700")
        + "".join(grid) + axes
        + txt(400, 372, "scenarios run", size=12, fill="#555")
        + target_line + polyline(random_, "#3b6fb0") + polyline(directed, "#1f9d55")
        + "".join(markers) + legend + "</svg>"
    )
    Path(path).write_text(svg, encoding="utf-8")
