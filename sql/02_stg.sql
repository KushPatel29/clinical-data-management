/*
    Quarantine.

    A resource that fails validation is written here with the reason. It is
    never dropped, and it never silently becomes a NULL in a fact table.

    Note what this table deliberately does NOT have: an ISJSON check constraint
    on `payload`. raw.fhir_resource has one because everything that reaches it
    is known-good JSON. The single most common thing to arrive here is text that
    is not JSON at all — a truncated line, an HTML error page returned by a
    server that meant to return a Bundle. A quarantine table that can only
    accept well-formed input cannot hold the failures that matter most, and the
    insert would fail at exactly the moment the pipeline needs to record why.
*/

USE [$(DatabaseName)];
GO

IF OBJECT_ID('stg.ingest_rejects') IS NULL
CREATE TABLE stg.ingest_rejects (
    reject_id       BIGINT IDENTITY(1,1) NOT NULL,
    batch_id        BIGINT         NULL,
    resource_type   VARCHAR(32)    NULL,   -- NULL when the payload could not be parsed
    resource_id     VARCHAR(64)    NULL,   -- at all, which is a real and common case
    source_system   VARCHAR(64)    NOT NULL,
    source_ref      NVARCHAR(400)  NOT NULL,
    source_line     INT            NULL,
    failure_stage   VARCHAR(32)    NOT NULL,
    failure_reason  NVARCHAR(400)  NOT NULL,
    failure_detail  NVARCHAR(MAX)  NULL,
    payload         NVARCHAR(MAX)  NULL,
    rejected_at     DATETIME2(3)   NOT NULL CONSTRAINT DF_ingest_rejects_at DEFAULT SYSUTCDATETIME(),

    CONSTRAINT PK_ingest_rejects PRIMARY KEY CLUSTERED (reject_id),
    CONSTRAINT CK_ingest_rejects_stage
        CHECK (failure_stage IN ('parse', 'schema', 'reference', 'constraint')),
    CONSTRAINT FK_ingest_rejects_batch FOREIGN KEY (batch_id)
        REFERENCES meta.load_batch (batch_id)
);
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_ingest_rejects_reason')
CREATE INDEX IX_ingest_rejects_reason
    ON stg.ingest_rejects (failure_stage, resource_type)
    INCLUDE (failure_reason, rejected_at);
GO

/*
    What a data manager opens on Monday: what is being rejected, and is it one
    systemic problem or forty unrelated ones. Forty distinct reasons is a bad
    source; one reason with forty thousand rows is one upstream bug.
*/
CREATE OR ALTER VIEW stg.vw_reject_summary
AS
SELECT
    failure_stage,
    ISNULL(resource_type, '(unparsed)') AS resource_type,
    failure_reason,
    COUNT(*)      AS rejects,
    MIN(rejected_at) AS first_seen,
    MAX(rejected_at) AS last_seen
FROM stg.ingest_rejects
GROUP BY failure_stage, resource_type, failure_reason;
GO
