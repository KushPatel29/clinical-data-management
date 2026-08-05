-- covering nonclustered index for Q1: Result trend for one observation type over a date range
--
-- Why: Monthly mean and volume for a single LOINC code across a two-year window, split by patient age band — the shape behind every 'is this measure drifting' tile on a clinical dashboard.
-- Measured effect: see docs/performance.md, generated from
-- analytics/measure_performance.py.
DROP INDEX IF EXISTS IX_FactObservation_code_date ON dw.FactObservation;
CREATE NONCLUSTERED INDEX IX_FactObservation_code_date
            ON dw.FactObservation (observation_code_key, effective_date_key)
            INCLUDE (patient_key, value_numeric, is_numeric);
