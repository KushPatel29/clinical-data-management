/*
    Raw landing.

    One row per FHIR resource version, holding the JSON exactly as it arrived.
    Nothing here is parsed by the ingestion process — parsing happens in T-SQL in
    10_load_norm.sql using OPENJSON, so the shredding logic is a reviewable
    artefact in the repository rather than a Python dictionary walk that only
    exists at runtime.

    The primary key is the idempotency contract. FHIR's identity of a resource
    version is (type, id, versionId); loading the same extract twice must not
    produce a second row, and the loader relies on this key to say so rather
    than checking first and racing.
*/

USE [$(DatabaseName)];
GO

IF OBJECT_ID('raw.fhir_resource') IS NULL
CREATE TABLE raw.fhir_resource (
    resource_type   VARCHAR(32)    NOT NULL,
    resource_id     VARCHAR(64)    NOT NULL,
    version_id      VARCHAR(32)    NOT NULL,
    last_updated    DATETIME2(3)   NULL,
    source_system   VARCHAR(64)    NOT NULL,
    source_ref      NVARCHAR(400)  NOT NULL,
    batch_id        BIGINT         NULL,
    payload         NVARCHAR(MAX)  NOT NULL,
    ingested_at     DATETIME2(3)   NOT NULL CONSTRAINT DF_fhir_resource_ingested DEFAULT SYSUTCDATETIME(),

    CONSTRAINT PK_fhir_resource PRIMARY KEY CLUSTERED (resource_type, resource_id, version_id),

    /*  The reason this column is trustworthy.

        Without it, "payload NVARCHAR(MAX)" is a column that holds text and
        promises nothing; every downstream OPENJSON has to be defensive about
        being handed a log line. ISJSON is the engine agreeing to enforce the
        one property the whole warehouse is built on. It costs a parse per
        insert and it is the cheapest guarantee in the schema.               */
    CONSTRAINT CK_fhir_resource_is_json CHECK (ISJSON(payload) = 1),

    /*  A resource whose payload disagrees with the columns keyed off it is a
        row that will shred into the wrong place. Checking it here means the
        columns are derived facts, not independent claims.                   */
    CONSTRAINT CK_fhir_resource_type_matches
        CHECK (JSON_VALUE(payload, '$.resourceType') = resource_type),
    CONSTRAINT CK_fhir_resource_id_matches
        CHECK (JSON_VALUE(payload, '$.id') = resource_id),

    CONSTRAINT FK_fhir_resource_batch FOREIGN KEY (batch_id)
        REFERENCES meta.load_batch (batch_id)
);
GO

/*  The shredding procedures all filter by resource_type and order by
    last_updated. The clustered key leads with resource_type, so type filtering
    is already a seek; this covers the "what changed since" access path that
    incremental loads use.                                                    */
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_fhir_resource_type_updated')
CREATE INDEX IX_fhir_resource_type_updated
    ON raw.fhir_resource (resource_type, last_updated)
    INCLUDE (resource_id, version_id);
GO

/*  Version history: the current version of a resource is the one with the
    highest last_updated, and every load asks for it. Without this the loaders
    read every historical version of every resource to find the newest.       */
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_fhir_resource_current')
CREATE INDEX IX_fhir_resource_current
    ON raw.fhir_resource (resource_type, resource_id, last_updated DESC)
    INCLUDE (version_id);
GO

/*
    Which version of each resource is the current one.

    ROW_NUMBER over (last_updated DESC, version_id DESC) rather than MAX(): a
    resource can carry two versions with the same lastUpdated — Synthea does not
    stamp one at all — and MAX would then return two rows for one resource and
    silently double every downstream count. Ordering by version_id second makes
    the choice deterministic instead of dependent on scan order.

    Materialised into a table rather than left as a view, and that is a
    measured decision rather than a stylistic one.

    The first version of this was a view, and the shredding procedures
    referenced it sixteen times. Each reference re-ran the window function over
    the whole raw table: on 1,663,658 resources that was a single MERGE
    statement running for 157 seconds and 5.1 million logical reads, and the
    shred as a whole was heading for well over half an hour. The ranking itself
    is not expensive — it is covered by IX_fhir_resource_current, which holds no
    payload — it was being paid for sixteen times.

    So it is computed once, into three narrow columns. The payload is joined
    back from raw.fhir_resource through its clustered key. The view below is
    kept for interactive use, where one scan is fine and correctness matters
    more than convenience; the loaders use the table.
*/
IF OBJECT_ID('raw.current_version') IS NULL
CREATE TABLE raw.current_version (
    resource_type VARCHAR(32) NOT NULL,
    resource_id   VARCHAR(64) NOT NULL,
    version_id    VARCHAR(32) NOT NULL,
    CONSTRAINT PK_current_version PRIMARY KEY CLUSTERED (resource_type, resource_id, version_id)
);
GO

CREATE OR ALTER PROCEDURE raw.usp_refresh_current_version
AS
BEGIN
    SET NOCOUNT ON;
    /*  TRUNCATE and reload rather than MERGE. The table is derived, narrow, and
        rebuilt in a couple of seconds; a MERGE would compare 1.6 million rows
        to save writing 1.6 million small ones.                               */
    TRUNCATE TABLE raw.current_version;

    INSERT raw.current_version (resource_type, resource_id, version_id)
    SELECT resource_type, resource_id, version_id
    FROM (
        SELECT resource_type, resource_id, version_id,
               ROW_NUMBER() OVER (
                   PARTITION BY resource_type, resource_id
                   ORDER BY last_updated DESC, version_id DESC) AS rn
        FROM raw.fhir_resource
    ) AS ranked
    WHERE rn = 1;
END
GO

/*  The convenience view. Correct, and one full ranking pass per reference —
    which is why the loaders do not use it.                                   */
CREATE OR ALTER VIEW raw.vw_current_resource
AS
SELECT
    r.resource_type,
    r.resource_id,
    r.version_id,
    r.last_updated,
    r.source_system,
    r.batch_id,
    r.payload,
    r.ingested_at
FROM raw.fhir_resource AS r
JOIN raw.current_version AS c
  ON c.resource_type = r.resource_type
 AND c.resource_id   = r.resource_id
 AND c.version_id    = r.version_id;
GO
