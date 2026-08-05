-- @check: DUP-01
-- @severity: error
-- @description: No natural key appears twice.
--
-- Every fact table has a UNIQUE constraint on its natural key, so a duplicate
-- cannot exist while those constraints are enabled -- and this check is what
-- notices when one is not. It also covers the keys no constraint can express:
-- a practitioner NPI claimed by two practitioner rows is a real-world identity
-- collision, and the identifier table's primary key hides it by keeping only
-- the last one loaded.
--
-- Every row returned is a key that will double a measure.
SELECT 'dw.FactEncounter.encounter_id' AS natural_key, encounter_id AS key_value, COUNT_BIG(*) AS occurrences
FROM dw.FactEncounter GROUP BY encounter_id HAVING COUNT_BIG(*) > 1
UNION ALL
SELECT 'dw.FactObservation.(observation_id, component_seq)',
       CONCAT(observation_id, ':', component_seq), COUNT_BIG(*)
FROM dw.FactObservation GROUP BY observation_id, component_seq HAVING COUNT_BIG(*) > 1
UNION ALL
SELECT 'dw.FactMedicationOrder.medication_request_id', medication_request_id, COUNT_BIG(*)
FROM dw.FactMedicationOrder GROUP BY medication_request_id HAVING COUNT_BIG(*) > 1
UNION ALL
SELECT 'dw.FactProcedure.procedure_id', procedure_id, COUNT_BIG(*)
FROM dw.FactProcedure GROUP BY procedure_id HAVING COUNT_BIG(*) > 1
UNION ALL
SELECT 'dw.FactEncounterDiagnosis.condition_id', condition_id, COUNT_BIG(*)
FROM dw.FactEncounterDiagnosis GROUP BY condition_id HAVING COUNT_BIG(*) > 1
UNION ALL
SELECT 'dw.DimProvider.practitioner_id', practitioner_id, COUNT_BIG(*)
FROM dw.DimProvider GROUP BY practitioner_id HAVING COUNT_BIG(*) > 1
UNION ALL
-- Two clinicians asserting the same NPI. Not a schema violation -- the
-- identifier table's key silently keeps whichever loaded last -- and a genuine
-- data quality incident that would misattribute every encounter one of them saw.
SELECT 'norm.practitioner_identifier duplicate NPI across practitioners',
       identifier_value, COUNT_BIG(DISTINCT practitioner_id)
FROM norm.practitioner_identifier
WHERE identifier_system = 'http://hl7.org/fhir/sid/us-npi'
GROUP BY identifier_value HAVING COUNT_BIG(DISTINCT practitioner_id) > 1
UNION ALL
-- One code registered twice within one system defeats the whole point of
-- resolving terminology to a key.
SELECT 'norm.code_concept.(system, code)', CONCAT(cs.system_name, '|', cc.code), COUNT_BIG(*)
FROM norm.code_concept AS cc
JOIN norm.code_system AS cs ON cs.code_system_id = cc.code_system_id
GROUP BY cs.system_name, cc.code HAVING COUNT_BIG(*) > 1;
