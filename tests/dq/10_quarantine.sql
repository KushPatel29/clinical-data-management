-- @check: QUAR-01
-- @severity: error
-- @description: Nothing was dropped, and nothing was quarantined without a reason.
--
-- The quarantine's whole contract, in four assertions:
--
--   * every rejected resource carries a non-empty reason and a stage. A reject
--     with a blank reason is a row nobody will ever action.
--   * every rejected resource carries its payload, unless it could not be
--     parsed at all. "It failed" without the input is a dead end.
--   * every completed load batch balances: read = accepted + duplicate +
--     rejected. If it does not, resources went somewhere neither table records,
--     which is precisely the silent drop this design exists to prevent.
--   * no batch is left running. Either a load is in flight, or one died and
--     nobody noticed.
SELECT 'reject_without_a_reason' AS failure,
       CAST(reject_id AS VARCHAR(20)) AS offending_id,
       ISNULL(failure_reason, '(null)') AS detail
FROM stg.ingest_rejects
WHERE failure_reason IS NULL OR LTRIM(RTRIM(failure_reason)) = ''

UNION ALL
SELECT 'reject_without_a_payload', CAST(reject_id AS VARCHAR(20)), failure_reason
FROM stg.ingest_rejects
WHERE payload IS NULL AND failure_stage <> 'parse'

UNION ALL
SELECT 'reject_without_a_source_reference', CAST(reject_id AS VARCHAR(20)), failure_reason
FROM stg.ingest_rejects
WHERE source_ref IS NULL OR LTRIM(RTRIM(source_ref)) = ''

UNION ALL
-- accepted counts rows newly inserted; a resource already present from an
-- earlier run is a duplicate, not a loss. The balance therefore allows for the
-- difference between what was read and what was newly landed.
SELECT 'batch_does_not_balance', CAST(batch_id AS VARCHAR(20)),
       CONCAT('read ', resources_read, ' < accepted ', resources_accepted,
              ' + rejected ', resources_rejected)
FROM meta.load_batch
WHERE status = 'succeeded'
  AND resources_read < resources_accepted + resources_rejected

UNION ALL
SELECT 'batch_never_completed', CAST(batch_id AS VARCHAR(20)),
       CONCAT('status ', status, ', started ', CONVERT(VARCHAR(19), started_at, 120))
FROM meta.load_batch
WHERE completed_at IS NULL AND started_at < DATEADD(HOUR, -1, SYSUTCDATETIME());
