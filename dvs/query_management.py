"""
Query lifecycle: aging, site performance, and readiness for database lock.

Raising a query is the easy part. The job is closing them, and the metric that
actually predicts whether a study locks on time is not how many queries were
raised but **how long the open ones have been open**. A study with 400 queries
all under a week old is healthy. A study with 40 queries averaging 60 days is in
trouble, and the second one looks better on a count.

This module simulates the site responses that would arrive over the course of a
study and reports what a data manager takes to the weekly study team meeting:
open queries by age band, site responsiveness, and which checks are generating
the most work — because a check that fires 200 times is usually a CRF design
problem, not 200 site errors.

Usage:
    python dvs/query_management.py
"""

import csv
import random
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

random.seed(271828)

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "output"

DATA_CUT = date(2026, 7, 30)
AGE_BANDS = [(7, "0-7 days"), (14, "8-14 days"), (30, "15-30 days"),
             (60, "31-60 days"), (10**6, "60+ days")]

# Site responsiveness differs, and it differs persistently — which is why this
# is a site *management* metric, not a data metric. SITE-105 is the problem
# site, and the report has to make that visible without editorialising.
SITE_RESPONSIVENESS = {
    "SITE-101": 0.90, "SITE-102": 0.78, "SITE-103": 0.86,
    "SITE-104": 0.92, "SITE-105": 0.58,
}
SITE_SPEED_DAYS = {
    "SITE-101": 6, "SITE-102": 11, "SITE-103": 8,
    "SITE-104": 5, "SITE-105": 22,
}


def age_band(days):
    for limit, name in AGE_BANDS:
        if days <= limit:
            return name
    return AGE_BANDS[-1][1]


def simulate_lifecycle(queries):
    """Attach an issue date, a status, and where relevant a resolution."""
    out = []
    for q in queries:
        site = q["site_id"]
        issued = DATA_CUT - timedelta(days=random.randint(1, 120))
        answered = random.random() < SITE_RESPONSIVENESS[site]

        row = dict(q)
        row["issued_date"] = issued.isoformat()
        if answered:
            turnaround = max(1, int(random.gauss(SITE_SPEED_DAYS[site],
                                                 SITE_SPEED_DAYS[site] * 0.5)))
            closed = issued + timedelta(days=turnaround)
            if closed <= DATA_CUT:
                row["status"] = "closed"
                row["closed_date"] = closed.isoformat()
                row["days_to_close"] = turnaround
                row["age_days"] = ""
                row["age_band"] = ""
                out.append(row)
                continue
        row["status"] = "open"
        row["closed_date"] = ""
        row["days_to_close"] = ""
        row["age_days"] = (DATA_CUT - issued).days
        row["age_band"] = age_band(row["age_days"])
        out.append(row)
    return out


def main():
    with open(OUT / "queries_raised.csv", encoding="utf-8") as f:
        queries = list(csv.DictReader(f))

    tracked = simulate_lifecycle(queries)
    with open(OUT / "query_log.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=tracked[0].keys())
        w.writeheader()
        w.writerows(tracked)

    open_q = [q for q in tracked if q["status"] == "open"]
    closed_q = [q for q in tracked if q["status"] == "closed"]

    by_site = defaultdict(lambda: {"raised": 0, "open": 0, "days": []})
    for q in tracked:
        s = by_site[q["site_id"]]
        s["raised"] += 1
        if q["status"] == "open":
            s["open"] += 1
        else:
            s["days"].append(int(q["days_to_close"]))

    site_rows = []
    for site in sorted(by_site):
        s = by_site[site]
        site_rows.append({
            "site_id": site,
            "queries_raised": s["raised"],
            "queries_open": s["open"],
            "queries_closed": s["raised"] - s["open"],
            "close_rate": round((s["raised"] - s["open"]) / s["raised"], 4),
            "median_days_to_close": (sorted(s["days"])[len(s["days"]) // 2]
                                     if s["days"] else ""),
        })
    with open(OUT / "query_site_performance.csv", "w", newline="",
              encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=site_rows[0].keys())
        w.writeheader()
        w.writerows(site_rows)

    band_counts = defaultdict(int)
    for q in open_q:
        band_counts[q["age_band"]] += 1

    by_check = defaultdict(int)
    for q in tracked:
        by_check[q["check_id"]] += 1

    all_days = sorted(int(q["days_to_close"]) for q in closed_q)
    median = all_days[len(all_days) // 2] if all_days else 0
    aged = sum(1 for q in open_q if int(q["age_days"]) > 30)

    lines = [
        "QUERY MANAGEMENT — SYN-2026-01",
        "=" * 54,
        f"Data cut:                 {DATA_CUT.isoformat()}",
        f"Queries raised:           {len(tracked):>6}",
        f"  closed:                 {len(closed_q):>6}"
        f" ({len(closed_q) / len(tracked):.1%})",
        f"  open:                   {len(open_q):>6}",
        f"Median days to close:     {median:>6}",
        f"Open >30 days:            {aged:>6}"
        f"  <- the database-lock risk",
        "-" * 54,
        "OPEN QUERY AGING",
        *[f"  {name:<14} {band_counts.get(name, 0):>5}"
          for _, name in AGE_BANDS],
        "-" * 54,
        "SITE PERFORMANCE",
        f"  {'site':<12}{'raised':>8}{'open':>7}{'close %':>10}{'median d':>10}",
        *[f"  {r['site_id']:<12}{r['queries_raised']:>8}{r['queries_open']:>7}"
          f"{r['close_rate']:>9.0%}{str(r['median_days_to_close']):>10}"
          for r in site_rows],
        "-" * 54,
        "TOP CHECKS BY VOLUME",
        # A check firing far more than its peers is usually telling you about
        # the CRF, not about the sites: an unclear field, a range set too
        # tight, or a required flag on something sites cannot always know.
        *[f"  {cid:<14} {by_check[cid]:>5}"
          for cid in sorted(by_check, key=lambda c: -by_check[c])[:5]],
    ]
    (OUT / "query_summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
