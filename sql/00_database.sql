/*
    Database and schemas.

    Four schemas, one per stage, because the stage a table belongs to is the
    single most useful thing to know about it and a naming convention is a
    weaker way to say it than a boundary the engine enforces:

        raw    FHIR JSON exactly as received. Never edited, never deleted.
        stg    Quarantine. Resources that failed validation, with the reason.
        norm   Third normal form. Constraints, keys, referential integrity.
        dw     Kimball dimensional model. Denormalised, surrogate-keyed.

    Run against master. Everything after this file runs against the database
    created here.
*/

IF DB_ID('$(DatabaseName)') IS NULL
BEGIN
    DECLARE @create NVARCHAR(300) = N'CREATE DATABASE [$(DatabaseName)]';
    EXEC sp_executesql @create;
END
GO

USE [$(DatabaseName)];
GO

/*
    Snapshot isolation. The data quality suite reads the same tables the loader
    writes, and a DQ check that blocks the load it is meant to be auditing turns
    a reporting problem into an outage.
*/
DECLARE @sql NVARCHAR(400) = N'ALTER DATABASE [$(DatabaseName)] SET READ_COMMITTED_SNAPSHOT ON WITH ROLLBACK IMMEDIATE';
IF EXISTS (SELECT 1 FROM sys.databases WHERE name = '$(DatabaseName)' AND is_read_committed_snapshot_on = 0)
    EXEC sp_executesql @sql;
GO

IF SCHEMA_ID('raw')  IS NULL EXEC('CREATE SCHEMA raw');
GO
IF SCHEMA_ID('stg')  IS NULL EXEC('CREATE SCHEMA stg');
GO
IF SCHEMA_ID('norm') IS NULL EXEC('CREATE SCHEMA norm');
GO
IF SCHEMA_ID('dw')   IS NULL EXEC('CREATE SCHEMA dw');
GO
IF SCHEMA_ID('dq')   IS NULL EXEC('CREATE SCHEMA dq');
GO
IF SCHEMA_ID('meta') IS NULL EXEC('CREATE SCHEMA meta');
GO

/*
    Load audit. Every batch that touches the warehouse gets a row here before it
    writes anything, so "when did this land and how many rows did it move" is
    answerable without reading the loader's logs.
*/
IF OBJECT_ID('meta.load_batch') IS NULL
CREATE TABLE meta.load_batch (
    batch_id            BIGINT IDENTITY(1,1) NOT NULL,
    batch_kind          VARCHAR(32)   NOT NULL,
    source              NVARCHAR(400) NOT NULL,
    started_at          DATETIME2(3)  NOT NULL CONSTRAINT DF_load_batch_started DEFAULT SYSUTCDATETIME(),
    completed_at        DATETIME2(3)  NULL,
    resources_read      INT           NOT NULL CONSTRAINT DF_load_batch_read     DEFAULT 0,
    resources_accepted  INT           NOT NULL CONSTRAINT DF_load_batch_accepted DEFAULT 0,
    resources_rejected  INT           NOT NULL CONSTRAINT DF_load_batch_rejected DEFAULT 0,
    status              VARCHAR(16)   NOT NULL CONSTRAINT DF_load_batch_status   DEFAULT 'running',
    CONSTRAINT PK_load_batch PRIMARY KEY CLUSTERED (batch_id),
    CONSTRAINT CK_load_batch_status CHECK (status IN ('running', 'succeeded', 'failed')),
    CONSTRAINT CK_load_batch_kind CHECK (batch_kind IN ('bulk', 'rest', 'changefeed'))
);
GO
