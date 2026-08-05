-- @check: REC-01
-- @severity: error
-- @description: Row counts reconcile from raw through norm to dw, within a documented variance.
--
-- The check that catches a warehouse quietly losing rows. Every layer boundary
-- gets an expected relationship, and every relationship that is not equality
-- gets a reason -- because "the numbers do not match and we think that is fine"
-- is how a pipeline ends up 3% short and nobody can say when it started.
--
--   raw -> norm    equality for every resource type. A resource that landed in
--                  raw and did not reach norm was dropped, and dropping is what
--                  the quarantine exists to make impossible. Resources rejected
--                  at validation never reach raw at all, so they are not in
--                  this denominator.
--
--   norm -> dw     equality for encounters, conditions, procedures and
--                  medication orders. NOT equality for observations: the fact
--                  grain is one *result*, so a two-component blood pressure
--                  becomes two rows and a 21-item panel becomes 21, while an
--                  observation carrying no value at all becomes none. The
--                  expected identity is therefore
--
--                      FactObservation = observations with a value
--                                      + observation components
--
--                  which is checked exactly, not approximated with a tolerance.
--
-- A row here is a layer boundary that lost or invented data.
WITH expected AS (
    SELECT 'Patient' AS layer_boundary,
           (SELECT COUNT_BIG(*) FROM raw.vw_current_resource WHERE resource_type = 'Patient') AS raw_rows,
           (SELECT COUNT_BIG(*) FROM norm.patient) AS norm_rows,
           (SELECT COUNT_BIG(*) FROM dw.DimPatient WHERE is_current = 1 AND patient_key <> -1) AS dw_rows,
           CAST('equal' AS VARCHAR(20)) AS rule
    UNION ALL
    SELECT 'Encounter',
           (SELECT COUNT_BIG(*) FROM raw.vw_current_resource WHERE resource_type = 'Encounter'),
           (SELECT COUNT_BIG(*) FROM norm.encounter),
           (SELECT COUNT_BIG(*) FROM dw.FactEncounter), 'equal'
    UNION ALL
    SELECT 'Condition',
           (SELECT COUNT_BIG(*) FROM raw.vw_current_resource WHERE resource_type = 'Condition'),
           (SELECT COUNT_BIG(*) FROM norm.condition),
           (SELECT COUNT_BIG(*) FROM dw.FactEncounterDiagnosis), 'equal'
    UNION ALL
    SELECT 'Procedure',
           (SELECT COUNT_BIG(*) FROM raw.vw_current_resource WHERE resource_type = 'Procedure'),
           (SELECT COUNT_BIG(*) FROM norm.[procedure]),
           (SELECT COUNT_BIG(*) FROM dw.FactProcedure), 'equal'
    UNION ALL
    SELECT 'MedicationRequest',
           (SELECT COUNT_BIG(*) FROM raw.vw_current_resource WHERE resource_type = 'MedicationRequest'),
           (SELECT COUNT_BIG(*) FROM norm.medication_request),
           (SELECT COUNT_BIG(*) FROM dw.FactMedicationOrder), 'equal'
)
SELECT layer_boundary, raw_rows, norm_rows, dw_rows,
       'raw <> norm' AS failure
FROM expected WHERE raw_rows <> norm_rows

UNION ALL
SELECT layer_boundary, raw_rows, norm_rows, dw_rows, 'norm <> dw'
FROM expected WHERE rule = 'equal' AND norm_rows <> dw_rows

UNION ALL
-- Observation, checked against its exact expected identity rather than exempted.
SELECT 'Observation',
       (SELECT COUNT_BIG(*) FROM raw.vw_current_resource WHERE resource_type = 'Observation'),
       (SELECT COUNT_BIG(*) FROM norm.observation),
       (SELECT COUNT_BIG(*) FROM dw.FactObservation),
       'raw <> norm'
WHERE (SELECT COUNT_BIG(*) FROM raw.vw_current_resource WHERE resource_type = 'Observation')
   <> (SELECT COUNT_BIG(*) FROM norm.observation)

UNION ALL
SELECT 'Observation (grain identity)',
       (SELECT COUNT_BIG(*) FROM norm.observation
        WHERE value_quantity IS NOT NULL OR value_code_concept_id IS NOT NULL
           OR value_string IS NOT NULL),
       (SELECT COUNT_BIG(*) FROM norm.observation_component),
       (SELECT COUNT_BIG(*) FROM dw.FactObservation),
       'valued observations + components <> FactObservation'
WHERE (SELECT COUNT_BIG(*) FROM norm.observation
       WHERE value_quantity IS NOT NULL OR value_code_concept_id IS NOT NULL
          OR value_string IS NOT NULL)
    + (SELECT COUNT_BIG(*) FROM norm.observation_component)
   <> (SELECT COUNT_BIG(*) FROM dw.FactObservation);
