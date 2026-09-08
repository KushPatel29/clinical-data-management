# Two halves, two entry points.
#
#   make cdm         the clinical data management pipeline. Standard library
#                    only, no database, no install step. Runs anywhere.
#   make warehouse   the FHIR ingestion and SQL Server warehouse. Needs a
#                    server and the packages in requirements.txt.
#
#   make all         both, then the full test suite.
#
# Connection details come from the environment; see db/connection.py.
#   CDM_SQL_SERVER    default localhost
#   CDM_SQL_DATABASE  default ClinicalWarehouse
#   CDM_SQL_USER / CDM_SQL_PASSWORD   omit for Windows authentication

PYTHON      ?= python
POPULATION  ?= 10000
SAMPLE      ?= 100

.PHONY: all cdm warehouse data schema ingest load dq perf test test-cdm clean help

help:
	@echo "make cdm         - CDM pipeline (stdlib only, no database)"
	@echo "make data        - generate a $(POPULATION)-patient FHIR extract with Synthea"
	@echo "make sample      - generate a $(SAMPLE)-patient extract (what CI uses)"
	@echo "make warehouse   - schema, ingest, shred, star, change feed, metrics"
	@echo "make dq          - run the T-SQL data quality suite"
	@echo "make perf        - measure three tuned queries and rewrite docs/performance.md"
	@echo "make test        - the full pytest suite"
	@echo "make test-cdm    - only the tests that need no database"

all: cdm warehouse dq test

# ---------------------------------------------------------------------------
# The CDM half. Unchanged, and deliberately still dependency-free.
# ---------------------------------------------------------------------------
cdm:
	$(PYTHON) data_generator/generate_edc_data.py
	$(PYTHON) dvs/edit_checks.py
	$(PYTHON) dvs/query_management.py
	$(PYTHON) sdtm/map_to_sdtm.py
	$(PYTHON) coding/code_terms.py
	$(PYTHON) uat/generate_uat_plan.py
	$(PYTHON) analytics/make_dashboard.py

# ---------------------------------------------------------------------------
# The warehouse half.
# ---------------------------------------------------------------------------

# Needs a JDK and synthea-with-dependencies.jar; see fhir/synthea.py for where
# to get both. The jar is 197 MB and is not vendored.
data:
	$(PYTHON) fhir/synthea.py --population $(POPULATION) --output data/synthea

sample:
	$(PYTHON) fhir/synthea.py --population $(SAMPLE) --output data/synthea

schema:
	$(PYTHON) db/migrate.py

warehouse:
	$(PYTHON) db/build_warehouse.py --reset

# The live REST path, against the public HAPI test server. Separate from the
# bulk load because it depends on someone else's server being up.
# Also rewrites the live_rest block of metrics.json — the source for the
# README's live-server table and the dashboard's REST panel. Those counts move
# as the public server's contents change, so the block carries the date it was
# observed. Before this target existed they were the one set of figures in the
# repository with no command behind them.
rest:
	$(PYTHON) fhir/ingest.py --rest --resource Patient,Observation,Encounter \
		--count 50 --max-pages 3 --record-metrics

dq:
	$(PYTHON) tests/dq/run_dq.py

perf:
	$(PYTHON) analytics/measure_performance.py
	$(PYTHON) docs/build_docs.py

docs:
	$(PYTHON) docs/build_docs.py

test:
	$(PYTHON) -m pytest tests/ -v

# What a contributor with no SQL Server can run: everything except the
# warehouse tests, which skip rather than fail.
test-cdm:
	$(PYTHON) -m pytest tests/ -v -m "not warehouse"

clean:
	$(PYTHON) db/migrate.py --reset
