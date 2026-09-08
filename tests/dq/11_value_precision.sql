-- @check: DOM-02
-- @severity: warn
-- @description: Numeric results that lost significance landing in DECIMAL(18,6).
--
-- The only `warn` check in the suite, and it is warn on purpose.
--
-- `norm.observation.value_quantity` is DECIMAL(18,6). One observation in this
-- extract carries "1.2607e-06", which is a real measurement and rounds to zero
-- at that scale. That is a documented limit of the column, not a defect: a
-- check that failed the build on it would be a check somebody disables within a
-- week, and then it stops reporting the case that *is* a defect — a whole feed
-- arriving in scientific notation because an upstream export changed format.
--
-- So it reports and does not block. What matters is the count moving: one row
-- is the known case, four thousand rows is an incident.
--
-- Getting here at all took two bugs. Casting the JSON straight to DECIMAL
-- raises "Error converting data type nvarchar to decimal" and kills the whole
-- statement — 925,282 good rows lost to one. Using TRY_CAST instead does not
-- raise, it returns NULL, which is worse: the value disappears and nothing says
-- so. Parsing through FLOAT keeps the number and this check keeps it visible.
SELECT
    'value_underflowed_decimal_scale' AS failure,
    o.observation_id                  AS offending_id,
    JSON_VALUE(r.payload, '$.valueQuantity.value') AS source_value,
    o.value_unit                      AS unit,
    CAST(o.value_quantity AS VARCHAR(40)) AS stored_value
FROM norm.observation AS o
JOIN raw.fhir_resource AS r
  ON r.resource_type = 'Observation'
 AND r.resource_id   = o.observation_id
WHERE o.value_quantity = 0
  AND TRY_CAST(JSON_VALUE(r.payload, '$.valueQuantity.value') AS FLOAT) <> 0

UNION ALL

-- The same thing one level down, on components. This is the path that used to
-- lose the value silently.
SELECT
    'component_value_underflowed_decimal_scale',
    c.observation_id,
    JSON_VALUE(comp.value, '$.valueQuantity.value'),
    c.value_unit,
    CAST(c.value_quantity AS VARCHAR(40))
FROM norm.observation_component AS c
JOIN raw.fhir_resource AS r
  ON r.resource_type = 'Observation'
 AND r.resource_id   = c.observation_id
CROSS APPLY OPENJSON(JSON_QUERY(r.payload, '$.component')) AS comp
WHERE CAST(comp.[key] AS SMALLINT) + 1 = c.component_seq
  AND c.value_quantity = 0
  AND TRY_CAST(JSON_VALUE(comp.value, '$.valueQuantity.value') AS FLOAT) <> 0;
