-- @check: NULL-01
-- @severity: error
-- @description: Null rates stay under their per-column threshold.
--
-- NOT NULL is the right tool where the FHIR spec says 1..1. This check covers
-- the columns where it is not: elements the spec makes optional, that this
-- warehouse nonetheless expects to be almost always present, because the day
-- they stop being present is the day an upstream mapping broke.
--
-- The thresholds are deliberately loose. A tight threshold on a genuinely
-- optional element produces a failing build every time a source system is
-- honest about not knowing something, and a check that cries wolf gets
-- disabled. These are set to catch a *step change* -- a feed that goes from 2%
-- missing to 60% missing -- not to enforce completeness.
--
-- Each threshold and its reason:
--   norm.patient.birth_date         2%   optional in R4; absent in practice
--                                        only for unidentified patients.
--   norm.patient.gender             2%   same.
--   norm.encounter.period_start     1%   an encounter with no start cannot be
--                                        placed on a timeline at all.
--   norm.encounter.service_provider 5%   the conditional-reference resolution.
--                                        This is the column that goes to 100%
--                                        null the moment someone "simplifies"
--                                        reference parsing to Type/id.
--   norm.encounter.primary_performer 15% not every encounter records a named
--                                        clinician; a jump still means the
--                                        participant mapping broke.
--   norm.observation.effective      1%   a result with no time is not usable.
--   dw.FactEncounter.length_of_stay 40%  open encounters legitimately have no
--                                        discharge.
--
-- A row here is a column whose null rate has crossed its threshold.
WITH rates AS (
    SELECT 'norm.patient.birth_date' AS column_name, 0.02 AS threshold,
           COUNT_BIG(*) AS total,
           SUM(CASE WHEN birth_date IS NULL THEN 1 ELSE 0 END) AS nulls
    FROM norm.patient
    UNION ALL
    SELECT 'norm.patient.gender', 0.02, COUNT_BIG(*),
           SUM(CASE WHEN gender IS NULL THEN 1 ELSE 0 END) FROM norm.patient
    UNION ALL
    SELECT 'norm.encounter.period_start', 0.01, COUNT_BIG(*),
           SUM(CASE WHEN period_start IS NULL THEN 1 ELSE 0 END) FROM norm.encounter
    UNION ALL
    SELECT 'norm.encounter.service_provider_id', 0.05, COUNT_BIG(*),
           SUM(CASE WHEN service_provider_id IS NULL THEN 1 ELSE 0 END) FROM norm.encounter
    UNION ALL
    SELECT 'norm.encounter.primary_performer_id', 0.15, COUNT_BIG(*),
           SUM(CASE WHEN primary_performer_id IS NULL THEN 1 ELSE 0 END) FROM norm.encounter
    UNION ALL
    SELECT 'norm.observation.effective_datetime', 0.01, COUNT_BIG(*),
           SUM(CASE WHEN effective_datetime IS NULL THEN 1 ELSE 0 END) FROM norm.observation
    UNION ALL
    SELECT 'norm.condition.onset_datetime', 0.05, COUNT_BIG(*),
           SUM(CASE WHEN onset_datetime IS NULL THEN 1 ELSE 0 END) FROM norm.condition
    UNION ALL
    SELECT 'dw.DimPatient.address_city', 0.02, COUNT_BIG(*),
           SUM(CASE WHEN address_city IS NULL THEN 1 ELSE 0 END)
    FROM dw.DimPatient WHERE patient_key <> -1
    UNION ALL
    SELECT 'dw.FactEncounter.length_of_stay_days', 0.40, COUNT_BIG(*),
           SUM(CASE WHEN length_of_stay_days IS NULL THEN 1 ELSE 0 END) FROM dw.FactEncounter
)
SELECT column_name,
       total AS row_count,
       nulls AS null_count,
       CAST(CAST(nulls AS DECIMAL(18,6)) / NULLIF(total, 0) AS DECIMAL(9,4)) AS null_rate,
       CAST(threshold AS DECIMAL(9,4)) AS threshold
FROM rates
WHERE total > 0
  AND CAST(nulls AS DECIMAL(18,6)) / total > threshold;
