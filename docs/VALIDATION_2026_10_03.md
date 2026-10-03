# Isolated warehouse verification — 3 October 2026

The SQL Server integration gap from the portfolio review is closed on a separate
local test warehouse. The existing portfolio-scale warehouse was not reset.

## Changes

- Connection strings, migration batches, ingestion and warehouse loading now
  resolve the same database. An explicit target overrides `CDM_SQL_DATABASE`.
- Invalid names are rejected before connecting. Reset and migration operations
  reject SQL Server system databases.
- Warehouse tests require `--warehouse`; the SQL Server CI job opts in.
- Fourteen regression cases cover routing, environment precedence, invalid names
  and protected databases. The collector now finds 295 cases.

## Executed evidence

The input was a deterministic sample of the first 100 patients from the recorded
10,000-patient Synthea extract. Their modeled clinical resources and all reference
providers were retained. This was a sample of an existing generated extract,
not a new Synthea generation. Its source jar SHA-256 was
`7fdbc2951d305eebac1fa46b1027347efb7380e93053e39ca2ee86613beb84f9`.

| Check | Result |
|---|---|
| Build raw → normalized → star, then change feed | Completed |
| Raw resources after change feed | 18,591 |
| Encounter facts | 1,630 |
| Observation facts | 12,668 |
| Patients with two versions | 15 |
| Data-quality runner | All checks passed |
| Performance measurement and documentation generation | Completed |
| Full suite with `--warehouse` | 285 passed, 10 skipped |
| Re-ingestion and loader rerun | Counts and checksums unchanged |
| Routing regression suite after final system-database guard | 14 passed |
| Lint | Passed |

The skips were one optional live HAPI request, six absent explicit README
row-count claims, two checks that belong to a checkout without a freshly measured
database, and the open-stay comparison because this sample had no open stays.
No warehouse test failed. The rerun tests use the extract and change feed that
built this warehouse, which resolves the earlier missing-input problem.

The full-scale measurements, committed evidence and public console remain tied
to their original recorded runs. This sample does not replace those measurements
or establish a regulatory release. The README gives the isolated-checkout
sequence for repeating integration verification.
