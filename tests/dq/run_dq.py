"""
Run the data quality suite.

Each `tests/dq/NN_*.sql` file is one check. The convention is deliberately
minimal so a check stays a plain SQL file a DBA can paste into Management
Studio and run:

    -- @check: <id>
    -- @severity: error | warn
    -- @description: <one line>
    <a query that returns the offending rows>

**Zero rows is a pass.** A check does not return a boolean, a count, or a
pass/fail string — it returns the rows that are wrong. That is the whole design
decision here, and it is the difference between a suite that tells you
"REC-01 FAILED" and one that tells you which four encounters lost their
organisation and what their ids are. The first gets muted; the second gets
fixed.

    python tests/dq/run_dq.py                 # run everything
    python tests/dq/run_dq.py --check SCD-01  # one check
    python tests/dq/run_dq.py --json          # machine-readable
    python tests/dq/run_dq.py --dir other/    # a different directory of checks
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from db.connection import SqlServerUnavailable, connect  # noqa: E402

DQ_DIR = Path(__file__).resolve().parent
MAX_ROWS_SHOWN = 20


@dataclass
class Check:
    path: Path
    check_id: str
    severity: str
    description: str
    sql: str


@dataclass
class CheckResult:
    check: Check
    offending_rows: list[tuple] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    elapsed_seconds: float = 0.0
    error: str | None = None

    @property
    def passed(self) -> bool:
        return self.error is None and not self.offending_rows

    @property
    def blocking(self) -> bool:
        """A `warn` check that fails is reported and does not fail the build."""
        return not self.passed and self.check.severity == "error"


def _header(text: str, key: str, default: str) -> str:
    match = re.search(rf"^--\s*@{key}:\s*(.+)$", text, re.MULTILINE | re.IGNORECASE)
    return match.group(1).strip() if match else default


def discover(directory: Path = DQ_DIR) -> list[Check]:
    checks = []
    for path in sorted(directory.glob("*.sql")):
        text = path.read_text(encoding="utf-8")
        checks.append(Check(
            path=path,
            check_id=_header(text, "check", path.stem),
            severity=_header(text, "severity", "error").lower(),
            description=_header(text, "description", ""),
            sql=text,
        ))
    return checks


def run(checks: list[Check], database: str | None = None) -> list[CheckResult]:
    results: list[CheckResult] = []
    with connect(database) as cn:
        cur = cn.cursor()
        for check in checks:
            started = time.perf_counter()
            result = CheckResult(check=check)
            try:
                cur.execute(check.sql)
                # A check file may contain several statements; the offending
                # rows are whichever result set has any. Draining the rest keeps
                # the connection usable for the next check.
                while True:
                    if cur.description:
                        result.columns = [c[0] for c in cur.description]
                        rows = cur.fetchall()
                        if rows:
                            result.offending_rows.extend(tuple(r) for r in rows)
                    if not cur.nextset():
                        break
            except Exception as exc:
                result.error = f"{type(exc).__name__}: {exc}"
            result.elapsed_seconds = round(time.perf_counter() - started, 3)
            results.append(result)
    return results


def report(results: list[CheckResult], stream=sys.stdout) -> None:
    width = max((len(r.check.check_id) for r in results), default=10)
    for result in results:
        check = result.check
        if result.error:
            status = "ERROR"
        elif result.passed:
            status = "pass"
        elif check.severity == "error":
            status = "FAIL"
        else:
            status = "warn"

        print(f"  [{status:^5}] {check.check_id:<{width}}  {check.description}"
              f"  ({result.elapsed_seconds}s)", file=stream)

        if result.error:
            print(f"           {result.error}", file=stream)
            continue
        if result.passed:
            continue

        print(f"           {len(result.offending_rows)} offending row(s):", file=stream)
        print(f"           {' | '.join(result.columns)}", file=stream)
        for row in result.offending_rows[:MAX_ROWS_SHOWN]:
            rendered = " | ".join("NULL" if v is None else str(v) for v in row)
            print(f"           {rendered}", file=stream)
        if len(result.offending_rows) > MAX_ROWS_SHOWN:
            print(f"           ... and {len(result.offending_rows) - MAX_ROWS_SHOWN} more",
                  file=stream)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the T-SQL data quality suite.")
    parser.add_argument("--database", default=None)
    parser.add_argument("--check", default=None, help="run one check by id")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--dir", type=Path, default=DQ_DIR,
                        help="run the checks in another directory; used by the "
                             "self-test that proves the gate can actually close")
    args = parser.parse_args(argv)

    checks = discover(args.dir)
    if args.check:
        checks = [c for c in checks if c.check_id.lower() == args.check.lower()]
        if not checks:
            print(f"no check with id {args.check!r}", file=sys.stderr)
            return 2

    try:
        results = run(checks, args.database)
    except SqlServerUnavailable as exc:
        print(f"SQL Server unavailable: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps([{
            "check_id": r.check.check_id,
            "severity": r.check.severity,
            "description": r.check.description,
            "passed": r.passed,
            "offending_rows": len(r.offending_rows),
            "columns": r.columns,
            "sample": [[None if v is None else str(v) for v in row]
                       for row in r.offending_rows[:MAX_ROWS_SHOWN]],
            "elapsed_seconds": r.elapsed_seconds,
            "error": r.error,
        } for r in results], indent=2))
    else:
        print(f"data quality: {len(results)} checks")
        report(results)
        blocking = [r for r in results if r.blocking or r.error]
        passed = sum(1 for r in results if r.passed)
        print(f"\n  {passed}/{len(results)} passed, {len(blocking)} blocking")

    return 1 if any(r.blocking or r.error for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
