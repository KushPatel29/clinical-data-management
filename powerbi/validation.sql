/*
    The T-SQL equivalent of every measure in powerbi/measures.md.

    A DAX measure that disagrees with this file has a filter-context bug, and
    finding that out from a query is considerably cheaper than finding it out
    from a director in a meeting. Documented DAX with nothing to check it
    against is a claim; this is what makes it a testable one.

        sqlcmd -S localhost -d ClinicalWarehouse -E -i powerbi/validation.sql

    tests/test_semantic_layer.py runs the same statements and asserts each
    returns a sane result, so a view renamed out from under this file fails the
    build rather than rotting quietly.
*/

SET NOCOUNT ON;

-- 1. Encounter volume ------------------------------------------------------
--    DAX: Encounters = COUNTROWS ( Encounter )
SELECT 'Encounters' AS measure_name,
       CAST(COUNT_BIG(*) AS DECIMAL(18,4)) AS value
FROM dw.vw_encounter;

-- 2a. Inpatient discharges (the readmission denominator) --------------------
--     DAX: CALCULATE ( COUNTROWS ( Encounter ), Encounter[is_inpatient] = TRUE () )
SELECT 'Inpatient Discharges',
       CAST(COUNT_BIG(*) AS DECIMAL(18,4))
FROM dw.vw_encounter WHERE is_inpatient = 1;

-- 2b. Readmissions ---------------------------------------------------------
SELECT 'Readmissions 30d',
       CAST(COUNT_BIG(*) AS DECIMAL(18,4))
FROM dw.vw_encounter WHERE is_readmission_30d = 1;

-- 2c. Readmission rate -----------------------------------------------------
--     The denominator is inpatient discharges, not all encounters. Including
--     ambulatory visits makes this roughly an order of magnitude smaller and
--     comparable to nothing.
SELECT 'Readmission Rate 30d',
       CAST(SUM(CAST(is_readmission_30d AS INT)) AS DECIMAL(18,6))
       / NULLIF(SUM(CAST(is_inpatient AS INT)), 0)
FROM dw.vw_encounter;

-- 3a. Average length of stay ----------------------------------------------
--     AVG ignores NULLs, which is the same behaviour as DAX AVERAGE and is
--     correct: an open stay has no length yet.
SELECT 'Average Length of Stay',
       CAST(AVG(length_of_stay_days) AS DECIMAL(18,4))
FROM dw.vw_encounter WHERE is_inpatient = 1;

-- 3b. Median length of stay -----------------------------------------------
--     PERCENTILE_CONT is a window function in T-SQL, so it needs OVER() and
--     produces one value per row; MIN collapses the constant.
SELECT 'Median Length of Stay',
       CAST(MIN(median_los) AS DECIMAL(18,4))
FROM (
    SELECT PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY length_of_stay_days)
               OVER () AS median_los
    FROM dw.vw_encounter
    WHERE is_inpatient = 1 AND length_of_stay_days IS NOT NULL
) AS x;

-- 4a. Active patient panel -------------------------------------------------
--     A standing population, anchored to the warehouse's most recent encounter
--     rather than to GETDATE().
SELECT 'Active Patients',
       CAST(COUNT_BIG(*) AS DECIMAL(18,4))
FROM dw.vw_patient_panel WHERE is_active = 1;

-- 4b. Patients seen --------------------------------------------------------
--     DISTINCTCOUNT on the BUSINESS key. Counting patient_key would count a
--     patient who moved twice, because DimPatient is Type 2.
SELECT 'Patients Seen',
       CAST(COUNT_BIG(DISTINCT patient_id) AS DECIMAL(18,4))
FROM dw.vw_encounter;

-- 4c. The trap, made visible ----------------------------------------------
--     If these two differ, Type 2 is doing its job and a distinct count on the
--     surrogate key would be wrong by exactly that difference.
SELECT 'Patients Seen (surrogate key - WRONG)',
       CAST(COUNT_BIG(DISTINCT f.patient_key) AS DECIMAL(18,4))
FROM dw.FactEncounter AS f;

-- 5a. Numeric results ------------------------------------------------------
SELECT 'Numeric Results',
       CAST(COUNT_BIG(*) AS DECIMAL(18,4))
FROM dw.FactObservation WHERE is_numeric = 1;

-- 5b. Statistical outliers -------------------------------------------------
--     NOT "abnormal". Outside the 5th-95th percentile of this warehouse's own
--     results for that LOINC code. There are no reference ranges in this data
--     and inventing them would be inventing clinical claims.
SELECT 'Statistical Outlier Results',
       CAST(SUM(CAST(is_statistical_outlier AS INT)) AS DECIMAL(18,4))
FROM dw.vw_observation_outlier;

-- 5c. Rate -----------------------------------------------------------------
SELECT 'Statistical Outlier Rate',
       CAST(SUM(CAST(is_statistical_outlier AS INT)) AS DECIMAL(18,6))
       / NULLIF(COUNT_BIG(*), 0)
FROM dw.vw_observation_outlier;

-- 6. Trial feasibility -----------------------------------------------------
--    The bridge to the CDM half of this repository: how many patients in the
--    health system could even be eligible for the study defined in crf/.
SELECT CONCAT('Feasibility: ', criterion),
       CAST(patients AS DECIMAL(18,4))
FROM dw.vw_trial_feasibility;
