"""
One place that knows how to reach SQL Server.

Connection details come from the environment so the same code runs against a
developer's local instance, a Docker container, and the CI service container
without a code change and without a credential in the repository:

    CDM_SQL_SERVER    host[,port] or host\\instance   (default: localhost)
    CDM_SQL_DATABASE  database name                  (default: ClinicalWarehouse)
    CDM_SQL_USER      SQL login; omit for Windows authentication
    CDM_SQL_PASSWORD  password for that login
    CDM_SQL_DRIVER    ODBC driver name               (default: best installed)

`available()` is what the test suite calls to decide whether the warehouse tests
can run at all. It has to be cheap and it must never raise, because a machine
without SQL Server should skip those tests rather than fail them — the CDM half
of this repo still runs anywhere with nothing installed.
"""

from __future__ import annotations

import os
from contextlib import contextmanager

DEFAULT_DATABASE = "ClinicalWarehouse"

# Newest first. 17 is what ships with the SQL Server client tools; 18 changed the
# default for Encrypt, which is why TrustServerCertificate is set explicitly
# below rather than left to the driver.
_PREFERRED_DRIVERS = (
    "ODBC Driver 18 for SQL Server",
    "ODBC Driver 17 for SQL Server",
    "SQL Server Native Client 11.0",
    "SQL Server",
)


class SqlServerUnavailable(RuntimeError):
    """Raised when a connection cannot be established, with the reason kept."""


def _installed_driver() -> str:
    import pyodbc

    configured = os.environ.get("CDM_SQL_DRIVER")
    if configured:
        return configured
    installed = set(pyodbc.drivers())
    for candidate in _PREFERRED_DRIVERS:
        if candidate in installed:
            return candidate
    raise SqlServerUnavailable(
        f"no SQL Server ODBC driver installed; found {sorted(installed)}"
    )


def connection_string(database: str | None = None, *, autocommit_master: bool = False) -> str:
    server = os.environ.get("CDM_SQL_SERVER", "localhost")
    if autocommit_master:
        database = "master"
    else:
        database = database or os.environ.get("CDM_SQL_DATABASE", DEFAULT_DATABASE)

    parts = [
        f"DRIVER={{{_installed_driver()}}}",
        f"SERVER={server}",
        f"DATABASE={database}",
        "TrustServerCertificate=yes",
    ]
    user = os.environ.get("CDM_SQL_USER")
    if user:
        parts.append(f"UID={user}")
        parts.append(f"PWD={os.environ.get('CDM_SQL_PASSWORD', '')}")
    else:
        parts.append("Trusted_Connection=yes")
    return ";".join(parts)


def connect(database: str | None = None, *, master: bool = False, autocommit: bool = False):
    """Open a connection, translating every failure into SqlServerUnavailable."""
    try:
        import pyodbc
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise SqlServerUnavailable(f"pyodbc is not installed: {exc}") from exc

    try:
        cn = pyodbc.connect(
            connection_string(database, autocommit_master=master),
            timeout=int(os.environ.get("CDM_SQL_TIMEOUT", "15")),
        )
    except Exception as exc:  # pyodbc.Error and anything the driver throws
        raise SqlServerUnavailable(str(exc)) from exc
    cn.autocommit = autocommit or master
    return cn


@contextmanager
def cursor(database: str | None = None, *, master: bool = False, autocommit: bool = False):
    cn = connect(database, master=master, autocommit=autocommit)
    try:
        cur = cn.cursor()
        yield cur
        if not cn.autocommit:
            cn.commit()
    finally:
        cn.close()


def available(database: str | None = None) -> tuple[bool, str]:
    """(reachable, reason). Never raises — the caller is deciding whether to skip."""
    try:
        with cursor(database, master=True) as cur:
            cur.execute("SELECT @@VERSION")
            return True, cur.fetchone()[0].splitlines()[0].strip()
    except SqlServerUnavailable as exc:
        return False, str(exc)
    except Exception as exc:  # pragma: no cover - defensive
        return False, f"{type(exc).__name__}: {exc}"


def server_description() -> dict:
    """Edition and version, recorded in metrics.json so measured numbers carry
    the engine they were measured on."""
    with cursor(master=True) as cur:
        cur.execute(
            "SELECT CAST(SERVERPROPERTY('Edition') AS NVARCHAR(200)),"
            "       CAST(SERVERPROPERTY('ProductVersion') AS NVARCHAR(50)),"
            "       CAST(SERVERPROPERTY('ProductLevel') AS NVARCHAR(50)),"
            "       CAST(SERVERPROPERTY('EngineEdition') AS INT)"
        )
        edition, version, level, engine = cur.fetchone()
    return {
        "edition": edition,
        "product_version": version,
        "product_level": level,
        "engine_edition": engine,
    }
