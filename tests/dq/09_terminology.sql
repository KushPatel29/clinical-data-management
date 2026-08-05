-- @check: TERM-01
-- @severity: error
-- @description: Every coded value resolves, and resolves in the right vocabulary.
--
-- The failure this catches is not a missing code — foreign keys handle that —
-- but a code resolved against the wrong code system. A short alphanumeric like
-- E11.9 is a valid ICD-10 code and also a plausible string in three other
-- vocabularies, and a diagnosis dimension that silently mixes them answers
-- "how many diabetes diagnoses" with a number wrong in a direction nobody can
-- predict.
--
-- Every row returned is a concept in the wrong vocabulary, or a dimension
-- member no fact can reach.
SELECT 'condition_not_coded_in_snomed' AS failure,
       CONCAT(cs.system_name, '|', cc.code) AS offending_value,
       COUNT_BIG(*) AS occurrences
FROM norm.condition AS c
JOIN norm.code_concept AS cc ON cc.code_concept_id = c.code_concept_id
JOIN norm.code_system  AS cs ON cs.code_system_id = cc.code_system_id
WHERE cs.system_uri <> 'http://snomed.info/sct'
GROUP BY cs.system_name, cc.code

UNION ALL
SELECT 'observation_primary_not_coded_in_loinc',
       CONCAT(cs.system_name, '|', cc.code), COUNT_BIG(*)
FROM norm.observation AS o
JOIN norm.code_concept AS cc ON cc.code_concept_id = o.code_concept_id
JOIN norm.code_system  AS cs ON cs.code_system_id = cc.code_system_id
WHERE cs.system_uri <> 'http://loinc.org'
GROUP BY cs.system_name, cc.code

UNION ALL
SELECT 'medication_not_coded_in_rxnorm',
       CONCAT(cs.system_name, '|', cc.code), COUNT_BIG(*)
FROM norm.medication_request AS m
JOIN norm.code_concept AS cc ON cc.code_concept_id = m.code_concept_id
JOIN norm.code_system  AS cs ON cs.code_system_id = cc.code_system_id
WHERE cs.system_uri <> 'http://www.nlm.nih.gov/research/umls/rxnorm'
GROUP BY cs.system_name, cc.code

UNION ALL
-- A concept whose display text never arrived. Every report using this dimension
-- shows a bare code where a clinician expects a name.
SELECT 'concept_without_display_text', CONCAT(cs.system_name, '|', cc.code), 1
FROM norm.code_concept AS cc
JOIN norm.code_system  AS cs ON cs.code_system_id = cc.code_system_id
WHERE cc.display IS NULL OR LTRIM(RTRIM(cc.display)) = ''

UNION ALL
-- A dimension member with no fact pointing at it. Harmless in a small
-- dimension; in a large one it means a join key changed shape.
SELECT 'diagnosis_dimension_member_with_no_facts', CONCAT(d.code_system, '|', d.code), 0
FROM dw.DimDiagnosis AS d
WHERE d.diagnosis_key <> -1
  AND NOT EXISTS (SELECT 1 FROM dw.FactEncounterDiagnosis AS f
                  WHERE f.diagnosis_key = d.diagnosis_key);
