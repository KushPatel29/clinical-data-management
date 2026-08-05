"""
Apply the SQL files in `sql/` in order.

Every file is written to be re-runnable: tables are guarded by
`IF OBJECT_ID(...) IS NULL`, procedures and views use `CREATE OR ALTER`. Running
this twice is a no-op, which is what makes it safe to call from a test fixture
and from CI without a teardown step.

Two things this deliberately does rather than shelling out to sqlcmd:

  * `GO` is not T-SQL. It is a batch separator that only the client tools
    understand, so a runner has to split on it — matching a line that is exactly
    `GO`, not the substring, or `CREATE PROCEDURE usp_GO_somewhere` becomes two
    invalid batches.
  * `$(DatabaseName)` is sqlcmd variable syntax, substituted here so the same
    files run under sqlcmd for a human and under pyodbc for the pipeline.

    python db/migrate.py                 # apply everything
    python db/migrate.py --reset         # drop the database first
    python db/migrate.py --only 05_dw    # one file, by prefix
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.connection import (  # noqa: E402
    DEFAULT_DATABASE,
    SqlServerUnavailable,
    connect,
)

ROOT = Path(__file__).resolve().parent.parent
SQL_DIR = ROOT / "sql"

_GO = re.compile(r"^\s*GO\s*(?:--.*)?$", re.IGNORECASE | re.MULTILINE)


def split_batches(script: str) -> list[str]:
    return [batch for batch in (part.strip() for part in _GO.split(script)) if batch]


def substitute(script: str, database: str) -> str:
    return script.replace("$(DatabaseName)", database)


def sql_files(only: str | None = None) -> list[Path]:
    files = sorted(p for p in SQL_DIR.glob("*.sql"))
    if only:
        files = [p for p in files if p.name.startswith(only) or p.stem == only]
        if not files:
            raise SystemExit(f"no sql file matching {only!r} in {SQL_DIR}")
    return files


def drop_database(database: str) -> None:
    with connect(master=True, autocommit=True) as cn:
        cur = cn.cursor()
        cur.execute(
            f"IF DB_ID('{database}') IS NOT NULL BEGIN "
            f"  ALTER DATABASE [{database}] SET SINGLE_USER WITH ROLLBACK IMMEDIATE; "
            f"  DROP DATABASE [{database}]; END"
        )


def apply(database: str = DEFAULT_DATABASE, only: str | None = None,
          verbose: bool = True) -> list[tuple[str, int, float]]:
    """Returns (filename, batches applied, seconds) per file."""
    applied: list[tuple[str, int, float]] = []
    for path in sql_files(only):
        script = substitute(path.read_text(encoding="utf-8"), database)
        batches = split_batches(script)
        # 00_database.sql creates the database, so it cannot run inside it.
        # It also issues ALTER DATABASE, which is not allowed in a transaction.
        against_master = path.name.startswith("00_")
        started = time.perf_counter()
        with connect(master=against_master, autocommit=against_master) as cn:
            cur = cn.cursor()
            for index, batch in enumerate(batches, start=1):
                try:
                    cur.execute(batch)
                    # A batch may leave result sets behind (a MERGE with OUTPUT,
                    # a SELECT INTO). Draining them keeps the connection usable.
                    while cur.nextset():
                        pass
                except Exception as exc:
                    head = "\n".join(batch.splitlines()[:6])
                    raise RuntimeError(
                        f"{path.name} batch {index}/{len(batches)} failed:\n{head}\n...\n{exc}"
                    ) from exc
            if not against_master:
                cn.commit()
        elapsed = time.perf_counter() - started
        applied.append((path.name, len(batches), elapsed))
        if verbose:
            print(f"  {path.name:<28} {len(batches):>3} batches  {elapsed:6.2f}s")
    return applied


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apply sql/ to SQL Server.")
    parser.add_argument("--database", default=None)
    parser.add_argument("--only", default=None, help="filename prefix, e.g. 05_dw")
    parser.add_argument("--reset", action="store_true", help="drop the database first")
    args = parser.parse_args(argv)

    import os

    database = args.database or os.environ.get("CDM_SQL_DATABASE", DEFAULT_DATABASE)
    try:
        if args.reset:
            print(f"dropping {database}")
            drop_database(database)
        print(f"applying sql/ to {database}")
        apply(database, args.only)
    except SqlServerUnavailable as exc:
        print(f"SQL Server unavailable: {exc}", file=sys.stderr)
        return 2
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 1
    print("ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
