"""An isolated build must never migrate, ingest or reset a different target."""

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from db import build_warehouse, connection, migrate


@pytest.mark.parametrize("explicit, expected", [(None, "CDM_Test"), ("Other_Test", "Other_Test")])
def test_connection_and_migrations_use_the_same_database(monkeypatch, tmp_path, explicit, expected):
    monkeypatch.setenv("CDM_SQL_DATABASE", "CDM_Test")
    monkeypatch.setattr(connection, "_installed_driver", lambda: "Test Driver")
    assert f"DATABASE={expected}" in connection.connection_string(explicit)
    (tmp_path / "00_database.sql").write_text("USE [$(DatabaseName)];", encoding="utf-8")
    (tmp_path / "01_tables.sql").write_text("SELECT DB_NAME();", encoding="utf-8")
    monkeypatch.setattr(migrate, "SQL_DIR", tmp_path)
    calls, statements = [], []

    @contextmanager
    def connect(database=None, **kwargs):
        calls.append((database, kwargs))
        cur = SimpleNamespace(execute=statements.append, nextset=lambda: False)
        yield SimpleNamespace(cursor=lambda: cur, commit=lambda: None)

    monkeypatch.setattr(migrate, "connect", connect)
    migrate.apply(explicit, verbose=False)
    assert calls == [(expected, {"master": True, "autocommit": True}),
                     (expected, {"master": False, "autocommit": False})]
    assert statements[0] == f"USE [{expected}];"


@pytest.mark.parametrize("explicit, expected", [(None, "CDM_Test"), ("Other_Test", "Other_Test")])
def test_build_routes_every_stage_to_one_target(monkeypatch, tmp_path, explicit, expected):
    monkeypatch.setenv("CDM_SQL_DATABASE", "CDM_Test")
    calls = []
    monkeypatch.setattr(build_warehouse, "METRICS", tmp_path / "metrics.json")
    monkeypatch.setattr(migrate, "drop_database", lambda db: calls.append(("reset", db)))
    monkeypatch.setattr(migrate, "apply", lambda db, **kw: calls.append(("schema", db)))
    result = SimpleNamespace(read=1, accepted=1, rejected=0, elapsed_seconds=0)

    def ingest(_path, database):
        calls.append(("ingest", database))
        return result

    monkeypatch.setattr(build_warehouse, "ingest_bulk", ingest)
    monkeypatch.setattr(build_warehouse.changefeed, "generate", lambda *_: {"patients_changed": 1})
    monkeypatch.setattr(build_warehouse, "run_procedures",
                        lambda db, _: calls.append(("load", db)))
    monkeypatch.setattr(build_warehouse, "table_counts", lambda db: {"target": db})
    monkeypatch.setattr(build_warehouse, "quality_measures", lambda _: {})
    monkeypatch.setattr(build_warehouse, "server_description", lambda: {})
    monkeypatch.setattr(build_warehouse, "write_board_summary",
                        lambda _, db: calls.append(("summary", db)))
    metrics = build_warehouse.build(explicit, reset=True, extract=tmp_path / "fhir")
    assert metrics["row_counts"]["target"] == expected
    assert [stage for stage, _ in calls] == [
        "reset", "schema", "ingest", "load", "load", "ingest", "load", "summary"]
    assert all(db == expected for _, db in calls)


@pytest.mark.parametrize("name", ["bad;DB", "bad]DB", "bad'DB", "bad\nDB", "x" * 129])
def test_invalid_names_fail_before_any_connection(monkeypatch, name):
    monkeypatch.setattr(migrate, "connect", lambda **_: pytest.fail("opened a connection"))
    with pytest.raises(ValueError, match="database name"):
        migrate.drop_database(name)
    with pytest.raises(ValueError, match="database name"):
        migrate.apply(name)
    with pytest.raises(ValueError, match="database name"):
        build_warehouse.build(name, reset=True)


@pytest.mark.parametrize("name", ["master", "MODEL", "msdb", "tempdb"])
def test_system_databases_cannot_be_reset(monkeypatch, name):
    monkeypatch.setattr(migrate, "connect", lambda **_: pytest.fail("opened a connection"))
    with pytest.raises(ValueError, match="system database"):
        migrate.drop_database(name)
    with pytest.raises(ValueError, match="system database"):
        migrate.apply(name)


def test_migration_cli_explicit_target_overrides_environment(monkeypatch):
    monkeypatch.setenv("CDM_SQL_DATABASE", "ExistingWarehouse")
    calls = []
    monkeypatch.setattr(migrate, "drop_database", lambda db: calls.append(("reset", db)))
    monkeypatch.setattr(migrate, "apply", lambda db, _: calls.append(("apply", db)))
    assert migrate.main(["--database", "CDM_Test", "--reset"]) == 0
    assert calls == [("reset", "CDM_Test"), ("apply", "CDM_Test")]
