"""
A warehouse status board, drawn as SVG with the standard library only.

The companion to `make_dashboard.py`, which covers the clinical data management
half. Same constraints and the same reason for them: a chart is not a good
enough reason to acquire a dependency, SVG is text, and Python can write text.
A test walks this module's AST and fails on any non-stdlib import.

It reads `output/warehouse_summary.csv`, written by `db/build_warehouse.py` from
the load it just performed. That indirection is the point — the board cannot
open a database connection, so it cannot show a number the pipeline did not
produce, and it stays a *view* of the load rather than a second place figures
are computed.

    python analytics/make_warehouse_board.py
"""

from __future__ import annotations

import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SUMMARY = ROOT / "output" / "warehouse_summary.csv"
DOCS = ROOT / "docs"

W, H = 1180, 700
NAVY, TEAL, ORANGE, AMBER = "#12436D", "#28A197", "#F46A25", "#E8A33D"
INK, MUTED, PAPER, LINE = "#12233A", "#6B7A8C", "#FFFFFF", "#DFE5EC"
FONT = "Segoe UI, Helvetica Neue, Arial, sans-serif"

SETTING_COLOR = {
    "Ambulatory": TEAL, "Inpatient": NAVY, "Emergency": ORANGE,
    "Virtual": AMBER, "Home": "#7FB3D5", "Other": MUTED, "Unknown": "#C0392B",
}


def read_summary() -> dict[str, list[tuple[str, float]]]:
    grouped: dict[str, list[tuple[str, float]]] = {}
    with open(SUMMARY, newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            value = float(row["value"])
            grouped.setdefault(row["metric"], []).append((row["label"], value))
    return grouped


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def thousands(value: float) -> str:
    return f"{int(value):,}"


def text(x, y, s, size=13, fill=INK, weight="400", anchor="start") -> str:
    return (
        f'<text x="{x}" y="{y}" font-family="{FONT}" font-size="{size}" '
        f'fill="{fill}" font-weight="{weight}" text-anchor="{anchor}">{esc(str(s))}</text>'
    )


def rect(x, y, w, h, fill, rx=3) -> str:
    return f'<rect x="{x}" y="{y}" width="{max(w, 0)}" height="{h}" rx="{rx}" fill="{fill}"/>'


def panel(x, y, w, h, title, subtitle="") -> list[str]:
    parts = [
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="{PAPER}" '
        f'stroke="{LINE}"/>',
        text(x + 18, y + 28, title, 13, INK, "600"),
    ]
    if subtitle:
        parts.append(text(x + 18, y + 46, subtitle, 11, MUTED))
    return parts


def kpi(x, y, w, value, label, colour=NAVY) -> list[str]:
    return [
        f'<rect x="{x}" y="{y}" width="{w}" height="96" rx="10" fill="{PAPER}" '
        f'stroke="{LINE}"/>',
        text(x + 18, y + 50, value, 28, colour, "700"),
        text(x + 18, y + 74, label, 11.5, MUTED),
    ]


def bars(x, y, w, h, pairs, colours, max_val=None, fmt=thousands, label_w=124) -> list[str]:
    if not pairs:
        return []
    top = max_val or max(v for _, v in pairs) or 1
    gap = 8
    bar_w = w - label_w - 76
    step = (h - gap) / len(pairs)
    bh = min(step - gap, 24)
    els = []
    for i, (name, val) in enumerate(pairs):
        by = y + i * step
        els.append(text(x, by + bh * 0.72, name, 11.5, MUTED))
        els.append(rect(x + label_w, by, bar_w * val / top, bh, colours[i % len(colours)]))
        els.append(text(x + label_w + bar_w * val / top + 7, by + bh * 0.72,
                        fmt(val), 11.5, INK, "600"))
    return els


def paired_bars(x, y, w, h, triples) -> list[str]:
    """Two bars per row: `norm` above, `dw` below.

    The reconciliation panel. Equal-length pairs are the point — the only
    resource where they legitimately differ is Observation, because the fact
    grain is one result rather than one resource.
    """
    if not triples:
        return []
    top = max(max(a, b) for _, a, b in triples) or 1
    label_w, bar_w = 110, w - 110 - 92
    step = (h - 6) / len(triples)
    bh = min((step - 10) / 2, 11)
    els = []
    for i, (name, norm_value, dw_value) in enumerate(triples):
        by = y + i * step
        els.append(text(x, by + bh + 4, name, 11.5, MUTED))
        els.append(rect(x + label_w, by, bar_w * norm_value / top, bh, NAVY))
        els.append(rect(x + label_w, by + bh + 3, bar_w * dw_value / top, bh, TEAL))
        widest = bar_w * max(norm_value, dw_value) / top
        note = thousands(norm_value)
        if dw_value != norm_value:
            note = f"{thousands(norm_value)} / {thousands(dw_value)}"
        els.append(text(x + label_w + widest + 7, by + bh + 4, note, 11, INK, "600"))
    return els


def build() -> str:
    data = read_summary()
    kpis = dict(data.get("kpi", []))
    norm_rows = dict(data.get("norm", []))
    dw_rows = dict(data.get("dw", []))
    settings = data.get("setting", [])
    timings = data.get("timing", [])
    feasibility = data.get("feasibility", [])

    e: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
        f'viewBox="0 0 {W} {H}">',
        f'<rect width="{W}" height="{H}" fill="#F6F8FA"/>',
        text(34, 44, "Clinical Warehouse — FHIR R4 to SQL Server", 21, INK, "700"),
        text(34, 68,
             "Synthetic Synthea extract · raw JSON to 3NF to Kimball star · "
             "every figure read from the load that produced it",
             12.5, MUTED),
    ]

    kw, kx = 272, 34
    e += kpi(kx, 90, kw, thousands(kpis.get("resources ingested", 0)),
             "FHIR resources landed in raw", NAVY)
    e += kpi(kx + kw + 18, 90, kw, thousands(kpis.get("fact rows", 0)),
             "fact rows across five fact tables", TEAL)
    e += kpi(kx + 2 * (kw + 18), 90, kw, thousands(kpis.get("quarantined", 0)),
             "quarantined, each with a reason", ORANGE)
    e += kpi(kx + 3 * (kw + 18), 90, kw, thousands(kpis.get("patients with history", 0)),
             "patients with a Type 2 version history", NAVY)

    # Reconciliation.
    e += panel(34, 208, 560, 224, "Row counts: norm to dw",
               "navy = norm (3NF)   ·   teal = dw (dimensional)")
    triples = [(name, norm_rows.get(name, 0), dw_rows.get(name, 0))
               for name in ("Patient", "Encounter", "Condition", "Observation",
                            "Procedure", "Medication")
               if name in norm_rows]
    e += paired_bars(52, 260, 524, 156, triples)

    # Encounters by care setting.
    e += panel(614, 208, 532, 224, "Encounters by care setting",
               "derived once in DimEncounterType, not per query")
    e += bars(632, 262, 496, 152,
              [(name, value) for name, value in settings[:6]],
              [SETTING_COLOR.get(name, MUTED) for name, _ in settings[:6]])

    # Load timings.
    e += panel(34, 452, 560, 216, "Load stages, seconds",
               "measured by db/build_warehouse.py on the run that produced this board")
    stage_pairs = [(name.replace(" (bulk)", "").replace(" (change feed)", " (delta)"), value)
                   for name, value in timings if value > 0][:7]
    e += bars(52, 506, 524, 146, stage_pairs,
              [NAVY, TEAL, ORANGE, AMBER, NAVY, TEAL, ORANGE],
              fmt=lambda v: f"{v:.1f}s", label_w=150)

    # Feasibility funnel — the bridge to the CDM half.
    e += panel(614, 452, 532, 216, "Trial feasibility screen",
               "how many warehouse patients could be eligible for study SYN-2026-01")
    if feasibility:
        top = max(v for _, v in feasibility) or 1
        e += bars(632, 506, 496, 146,
                  [(name[:34], value) for name, value in feasibility],
                  [NAVY, NAVY, TEAL, TEAL, ORANGE], max_val=top, label_w=210)

    e.append("</svg>")
    return "\n".join(e)


def main() -> None:
    if not SUMMARY.exists():
        print(f"no {SUMMARY.relative_to(ROOT)} — run `python db/build_warehouse.py` first")
        return
    DOCS.mkdir(parents=True, exist_ok=True)
    path = DOCS / "warehouse_board.svg"
    path.write_text(build(), encoding="utf-8", newline="\n")
    print(f"wrote {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
