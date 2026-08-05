-- @check: RI-01
-- @severity: error
-- @description: Foreign keys are declared, enabled, and trusted.
--
-- Not a search for orphans. The engine enforces every relationship in norm and
-- dw, so an orphan cannot exist while the constraints are doing their job --
-- which makes "no orphans found" a statement about the constraints, not about
-- the data, and worth nothing unless the constraints are actually on.
--
-- Three states end that guarantee, and all three are silent:
--
--   is_disabled = 1     someone ran NOCHECK CONSTRAINT to get a load through
--                       and did not put it back.
--   is_not_trusted = 1  the constraint was re-enabled WITH NOCHECK, so it
--                       governs new rows but was never verified against the
--                       existing ones. The optimiser also stops using it,
--                       which is why this shows up as a performance
--                       regression before it shows up as bad data.
--   is_not_for_replication = 1  exempts replication traffic from the rule.
--
-- A row here is a foreign key that is not protecting anything.
SELECT
    OBJECT_SCHEMA_NAME(fk.parent_object_id) AS table_schema,
    OBJECT_NAME(fk.parent_object_id)        AS table_name,
    fk.name                                 AS constraint_name,
    fk.is_disabled,
    fk.is_not_trusted,
    fk.is_not_for_replication
FROM sys.foreign_keys AS fk
WHERE OBJECT_SCHEMA_NAME(fk.parent_object_id) IN ('norm', 'dw', 'raw', 'stg')
  AND (fk.is_disabled = 1 OR fk.is_not_trusted = 1 OR fk.is_not_for_replication = 1);
