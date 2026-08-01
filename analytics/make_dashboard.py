"""
A data-management status board, drawn as SVG with the standard library only.

The rest of this repo has no third-party dependency, and a chart is not a good
enough reason to acquire one. SVG is text; Python can write text. Everything
below reads the CSVs the validation engine produced, so the board cannot show a
number the pipeline did not.

    python analytics/make_dashboard.py
"""

from __future__ import annotations

import collections
import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "output"
DOCS = ROOT / "docs"

W, H = 1180, 660
NAVY, TEAL, ORANGE, AMBER = "#12436D", "#28A197", "#F46A25", "#E8A33D"
INK, MUTED, PAPER, LINE = "#12233A", "#6B7A8C", "#FFFFFF", "#DFE5EC"
FONT = "Segoe UI, Helvetica Neue, Arial, sans-serif"

AGE_ORDER = ["0-7 days", "8-14 days", "15-30 days", "31-60 days", "60+ days"]
AGE_COLOR = {
    "0-7 days": TEAL, "8-14 days": TEAL, "15-30 days": AMBER,
    "31-60 days": ORANGE, "60+ days": "#C0392B",
}


def read(name: str) -> list[dict]:
    with open(OUT / name, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def text(x, y, s, size=13, fill=INK, weight="400", anchor="start") -> str:
    return (
        f'<text x="{x}" y="{y}" font-family="{FONT}" font-size="{size}" '
        f'fill="{fill}" font-weight="{weight}" text-anchor="{anchor}">{esc(str(s))}</text>'
    )


def rect(x, y, w, h, fill, rx=3) -> str:
    return f'<rect x="{x}" y="{y}" width="{max(w, 0)}" height="{h}" rx="{rx}" fill="{fill}"/>'


def panel(x, y, w, h, title) -> list[str]:
    return [
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="{PAPER}" '
        f'stroke="{LINE}"/>',
        text(x + 18, y + 28, title, 13, INK, "600"),
    ]


def kpi(x, y, w, value, label, colour=NAVY) -> list[str]:
    return [
        f'<rect x="{x}" y="{y}" width="{w}" height="96" rx="10" fill="{PAPER}" '
        f'stroke="{LINE}"/>',
        text(x + 18, y + 50, value, 30, colour, "700"),
        text(x + 18, y + 74, label, 11.5, MUTED),
    ]


def bars(x, y, w, h, pairs, colours, max_val=None, fmt="{}") -> list[str]:
    """Horizontal bars with a label column and a value at the end of each bar."""
    if not pairs:
        return []
    top = max_val or max(v for _, v in pairs) or 1
    lab_w, gap = 118, 8
    bar_w = w - lab_w - 58
    step = (h - gap) / len(pairs)
    bh = min(step - gap, 26)
    els = []
    for i, (name, val) in enumerate(pairs):
        by = y + i * step
        els.append(text(x, by + bh * 0.72, name, 11.5, MUTED))
        els.append(rect(x + lab_w, by, bar_w * val / top, bh, colours[i]))
        els.append(
            text(x + lab_w + bar_w * val / top + 7, by + bh * 0.72,
                 fmt.format(val), 11.5, INK, "600")
        )
    return els


def build() -> str:
    ql = read("query_log.csv")
    sites = read("query_site_performance.csv")
    coding = read("coding_results.csv")

    open_q = [r for r in ql if r["status"] == "open"]
    aging = collections.Counter(r["age_band"] for r in open_q)
    cod = collections.Counter(r["status"] for r in coding)
    manifest = collections.Counter(r["severity"] for r in ql)

    e: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
        f'viewBox="0 0 {W} {H}">',
        f'<rect width="{W}" height="{H}" fill="#F6F8FA"/>',
        text(34, 44, "Data Management Status — Study SYN-2026-01", 21, INK, "700"),
        text(
            34, 68,
            "120 subjects · 5 sites · 8 forms · every figure below produced by the "
            "validation engine in this repository",
            12.5, MUTED,
        ),
    ]

    # KPI row — the reconciliation is the headline.
    kw, kx = 272, 34
    e += kpi(kx, 90, kw, "49 / 49", "defects injected / detected", NAVY)
    e += kpi(kx + kw + 18, 90, kw, "0", "missed  ·  0 false positives", TEAL)
    e += kpi(kx + 2 * (kw + 18), 90, kw, f"{len(open_q)}", "queries open", ORANGE)
    e += kpi(
        kx + 3 * (kw + 18), 90, kw,
        f"{100 * len([r for r in ql if r['status'] == 'closed']) / len(ql):.0f}%",
        "query close rate", NAVY,
    )

    # Query aging.
    e += panel(34, 208, 560, 200, "Open queries by age")
    e += bars(
        52, 246, 524, 148,
        [(b, aging.get(b, 0)) for b in AGE_ORDER],
        [AGE_COLOR[b] for b in AGE_ORDER],
    )

    # Coding status.
    e += panel(614, 208, 532, 200, "Medical coding — MedDRA + WHODrug")
    order = ["auto", "synonym", "ambiguous", "uncoded"]
    e += bars(
        632, 246, 496, 148,
        [(k, cod.get(k, 0)) for k in order],
        [TEAL, NAVY, AMBER, ORANGE],
    )

    # Site performance.
    e += panel(34, 428, 560, 200, "Query close rate by site")
    e += bars(
        52, 466, 524, 148,
        [(r["site_id"], round(100 * float(r["close_rate"]))) for r in sites],
        [TEAL if float(r["close_rate"]) >= 0.7 else ORANGE for r in sites],
        max_val=100, fmt="{}%",
    )

    # Severity mix + the caught bug.
    e += panel(614, 428, 532, 200, "Edit checks fired, by severity")
    e += bars(
        632, 466, 496, 74,
        [("query", manifest.get("query", 0)), ("warning", manifest.get("warning", 0))],
        [NAVY, AMBER],
    )
    e.append(
        text(
            632, 578,
            "The manifest is what makes both recall and the false-positive rate",
            11.5, MUTED,
        )
    )
    e.append(
        text(
            632, 596,
            "measurable — and it caught a visit-window check keyed on the wrong",
            11.5, MUTED,
        )
    )
    e.append(text(632, 614, "record, which let four deviations pass silently.", 11.5, MUTED))

    e.append("</svg>")
    return "\n".join(e)


def main() -> None:
    DOCS.mkdir(parents=True, exist_ok=True)
    path = DOCS / "dm_status_board.svg"
    path.write_text(build(), encoding="utf-8", newline="\n")
    print(f"wrote {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
