-- nonclustered columnstore index for Q2: Whole-warehouse aggregate across every observation code
--
-- Why: Volume, mean and spread for every observation code by fiscal quarter — the extract behind a Power BI import model, which reads the whole fact table rather than a slice of it.
-- Measured effect: see docs/performance.md, generated from
-- analytics/measure_performance.py.
DROP INDEX IF EXISTS NCCI_FactObservation ON dw.FactObservation;
CREATE NONCLUSTERED COLUMNSTORE INDEX NCCI_FactObservation
            ON dw.FactObservation
               (observation_code_key, effective_date_key, patient_key,
                value_numeric, is_numeric);
