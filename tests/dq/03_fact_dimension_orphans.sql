-- @check: RI-03
-- @severity: error
-- @description: No fact row points at a dimension member that does not exist.
--
-- The nullable foreign keys are the ones worth checking, because a NULL passes
-- a foreign key and still breaks an inner-join report. FactObservation
-- .encounter_key is nullable by design -- a lab result ordered outside an
-- encounter is real -- so the check is that a non-NULL value resolves, not that
-- it is present.
--
-- Every row returned is a join that will silently drop from a report.
SELECT 'FactObservation.encounter_key' AS relationship,
       f.observation_fact_key AS fact_key, f.encounter_key AS missing_key
FROM dw.FactObservation AS f
WHERE f.encounter_key IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM dw.FactEncounter AS e WHERE e.encounter_key = f.encounter_key)
UNION ALL
SELECT 'FactMedicationOrder.encounter_key', f.medication_order_key, f.encounter_key
FROM dw.FactMedicationOrder AS f
WHERE f.encounter_key IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM dw.FactEncounter AS e WHERE e.encounter_key = f.encounter_key)
UNION ALL
SELECT 'FactEncounterDiagnosis.encounter_key', f.encounter_diagnosis_key, f.encounter_key
FROM dw.FactEncounterDiagnosis AS f
WHERE f.encounter_key IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM dw.FactEncounter AS e WHERE e.encounter_key = f.encounter_key)
UNION ALL
SELECT 'FactProcedure.encounter_key', f.procedure_fact_key, f.encounter_key
FROM dw.FactProcedure AS f
WHERE f.encounter_key IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM dw.FactEncounter AS e WHERE e.encounter_key = f.encounter_key);
