-- @check: RI-02
-- @severity: error
-- @description: Check constraints are enabled and trusted.
--
-- The same argument as RI-01, applied to the constraints that carry the FHIR
-- value sets and the ISJSON guarantee. CK_fhir_resource_is_json is the one the
-- whole warehouse rests on: if it is untrusted, "payload NVARCHAR(MAX)" has
-- gone back to being a column that holds text and promises nothing.
SELECT
    OBJECT_SCHEMA_NAME(cc.parent_object_id) AS table_schema,
    OBJECT_NAME(cc.parent_object_id)        AS table_name,
    cc.name                                 AS constraint_name,
    cc.is_disabled,
    cc.is_not_trusted
FROM sys.check_constraints AS cc
WHERE OBJECT_SCHEMA_NAME(cc.parent_object_id) IN ('norm', 'dw', 'raw', 'stg')
  AND (cc.is_disabled = 1 OR cc.is_not_trusted = 1);
