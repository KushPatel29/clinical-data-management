-- @check: DOM-01
-- @severity: error
-- @description: Domain rules a check constraint cannot express.
--
-- Check constraints cover what is true of one row in isolation. These are the
-- rules that need a join, an aggregate, or a comparison against another table,
-- which is exactly why they are the ones that get skipped.
--
-- Every row returned is a clinical fact that cannot be true.
SELECT 'observation_before_birth' AS failure,
       o.observation_id AS offending_id,
       CONCAT('patient ', p.patient_id, ' born ', CONVERT(VARCHAR(10), p.birth_date, 23),
              ', result dated ', CONVERT(VARCHAR(10), o.effective_datetime, 23)) AS detail
FROM norm.observation AS o
JOIN norm.patient AS p ON p.patient_id = o.patient_id
WHERE p.birth_date IS NOT NULL AND o.effective_datetime IS NOT NULL
  AND CAST(o.effective_datetime AS DATE) < p.birth_date

UNION ALL
SELECT 'encounter_before_birth', e.encounter_id,
       CONCAT('patient ', p.patient_id, ' born ', CONVERT(VARCHAR(10), p.birth_date, 23),
              ', encounter ', CONVERT(VARCHAR(10), e.period_start, 23))
FROM norm.encounter AS e
JOIN norm.patient AS p ON p.patient_id = e.patient_id
WHERE p.birth_date IS NOT NULL AND e.period_start IS NOT NULL
  AND CAST(e.period_start AS DATE) < p.birth_date

UNION ALL
-- A result dated after the load is either a clock problem upstream or a mapping
-- that read the wrong element.
SELECT 'observation_in_the_future', o.observation_id,
       CONCAT('result dated ', CONVERT(VARCHAR(10), o.effective_datetime, 23))
FROM norm.observation AS o
WHERE o.effective_datetime > DATEADD(DAY, 1, SYSUTCDATETIME())

UNION ALL
-- Negative length of stay. CK_encounter_period stops it at the source; this
-- catches it arriving by another path, and it is the measure most likely to be
-- quoted to a ministry.
SELECT 'negative_length_of_stay', f.encounter_id,
       CONCAT('los ', CAST(f.length_of_stay_days AS VARCHAR(20)), ' days')
FROM dw.FactEncounter AS f
WHERE f.length_of_stay_days < 0

UNION ALL
-- Longer than a year is not impossible, but a batch of them means a discharge
-- date defaulted rather than parsed.
SELECT 'implausible_length_of_stay', f.encounter_id,
       CONCAT('los ', CAST(CAST(f.length_of_stay_days AS DECIMAL(10,1)) AS VARCHAR(20)), ' days')
FROM dw.FactEncounter AS f
WHERE f.length_of_stay_days > 365

UNION ALL
-- A readmission flagged against no prior discharge is a window function that
-- lost its partition.
SELECT 'readmission_without_prior_discharge', f.encounter_id, 'flagged with no prior stay'
FROM dw.FactEncounter AS f
WHERE f.is_readmission_30d = 1 AND f.days_since_prior_discharge IS NULL

UNION ALL
-- The flag and the interval it is derived from must agree.
SELECT 'readmission_flag_disagrees_with_interval', f.encounter_id,
       CONCAT('days_since_prior_discharge = ', CAST(f.days_since_prior_discharge AS VARCHAR(10)),
              ', flag = ', CAST(f.is_readmission_30d AS VARCHAR(1)))
FROM dw.FactEncounter AS f
WHERE f.days_since_prior_discharge IS NOT NULL
  AND ((f.days_since_prior_discharge BETWEEN 0 AND 30 AND f.is_readmission_30d = 0)
    OR (f.days_since_prior_discharge > 30 AND f.is_readmission_30d = 1))

UNION ALL
-- A readmission on a non-inpatient encounter means the flag was computed over
-- the wrong population.
SELECT 'readmission_on_non_inpatient', f.encounter_id, 'flagged but not inpatient'
FROM dw.FactEncounter AS f
WHERE f.is_readmission_30d = 1 AND f.is_inpatient = 0

UNION ALL
-- A quantity with no unit. CK_observation_quantity_has_unit enforces it; a row
-- here means the constraint is off.
SELECT 'quantity_without_unit', o.observation_id, 'value with no unit'
FROM norm.observation AS o
WHERE o.value_quantity IS NOT NULL AND o.value_unit IS NULL;
