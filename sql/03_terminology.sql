/*
    Terminology.

    Every coded value in FHIR is a (system, code, display) triple, and the
    system is the part that gets dropped. A `code` column holding '8302-2' with
    no system is ambiguous the moment a second vocabulary arrives, and in
    clinical data a second vocabulary always arrives: LOINC for what was
    measured, SNOMED CT for what was diagnosed and done, RxNorm for what was
    prescribed, ICD-10-CA for what gets reported to CIHI.

    So codes are resolved once, into a concept table with a real key, and every
    clinical table joins to it. The cost is a join. The benefit is that
    "how many diabetes diagnoses" cannot be answered by matching a string
    against the wrong vocabulary.

    Scope, stated plainly: this is a *reference structure* seeded with the
    subset of codes the extract actually contains, discovered from the data.
    It is not a terminology server and it does not ship LOINC, SNOMED CT, or
    ICD-10-CA content — all three are licensed and cannot be redistributed.
    Concept display text comes from the FHIR resources themselves, which is
    what the source system asserted, not what the publisher's release file says.
*/

USE [$(DatabaseName)];
GO

IF OBJECT_ID('norm.code_system') IS NULL
CREATE TABLE norm.code_system (
    code_system_id  INT IDENTITY(1,1) NOT NULL,
    system_uri      NVARCHAR(200) NOT NULL,
    system_name     NVARCHAR(100) NOT NULL,
    is_licensed     BIT           NOT NULL CONSTRAINT DF_code_system_licensed DEFAULT 0,
    notes           NVARCHAR(400) NULL,
    CONSTRAINT PK_code_system PRIMARY KEY CLUSTERED (code_system_id),
    CONSTRAINT UQ_code_system_uri UNIQUE (system_uri)
);
GO

IF OBJECT_ID('norm.code_concept') IS NULL
CREATE TABLE norm.code_concept (
    code_concept_id INT IDENTITY(1,1) NOT NULL,
    code_system_id  INT           NOT NULL,
    code            NVARCHAR(64)  NOT NULL,
    display         NVARCHAR(400) NULL,
    first_seen_at   DATETIME2(3)  NOT NULL CONSTRAINT DF_code_concept_seen DEFAULT SYSUTCDATETIME(),
    CONSTRAINT PK_code_concept PRIMARY KEY CLUSTERED (code_concept_id),
    /*  The natural key. A code is unique within its system and nowhere else —
        'E11.9' is a diabetes ICD-10 code and also a perfectly good LOINC-shaped
        string. Uniqueness on `code` alone would be wrong.                    */
    CONSTRAINT UQ_code_concept UNIQUE (code_system_id, code),
    CONSTRAINT FK_code_concept_system FOREIGN KEY (code_system_id)
        REFERENCES norm.code_system (code_system_id)
);
GO

/*  The systems this warehouse recognises. Anything else that appears in the
    data still resolves — resolve_code inserts unknown systems rather than
    rejecting them — but these are the ones that carry meaning downstream and
    the ones the data dictionary documents.                                   */
MERGE norm.code_system AS target
USING (VALUES
    (N'http://loinc.org',
     N'LOINC', 1,
     N'Laboratory and clinical observations. Licensed; content not redistributed.'),
    (N'http://snomed.info/sct',
     N'SNOMED CT', 1,
     N'Conditions and procedures. Licensed via affiliate agreement; content not redistributed.'),
    (N'http://www.nlm.nih.gov/research/umls/rxnorm',
     N'RxNorm', 0,
     N'Medications, as Synthea emits them.'),
    (N'http://hl7.org/fhir/sid/icd-10-ca',
     N'ICD-10-CA', 1,
     N'Canadian ICD-10 modification used for CIHI DAD reporting. Crosswalk target; see notes in docs/architecture.md.'),
    (N'http://terminology.hl7.org/CodeSystem/v3-ActCode',
     N'HL7 v3 ActCode', 0,
     N'Encounter class: AMB, IMP, EMER.'),
    (N'http://terminology.hl7.org/CodeSystem/v3-MaritalStatus',
     N'HL7 v3 MaritalStatus', 0,
     N'Patient marital status, a tracked SCD2 attribute.'),
    (N'http://unitsofmeasure.org',
     N'UCUM', 0,
     N'Units on observation values.')
) AS source (system_uri, system_name, is_licensed, notes)
ON target.system_uri = source.system_uri
WHEN MATCHED THEN UPDATE SET
    system_name = source.system_name,
    is_licensed = source.is_licensed,
    notes       = source.notes
WHEN NOT MATCHED BY TARGET THEN
    INSERT (system_uri, system_name, is_licensed, notes)
    VALUES (source.system_uri, source.system_name, source.is_licensed, source.notes);
GO

/*
    Resolve a (system, code, display) triple to a concept key, inserting it the
    first time it is seen.

    Written as a MERGE with HOLDLOCK rather than "check then insert" because two
    shredding statements running in parallel will otherwise both miss, both
    insert, and one will hit the unique constraint. HOLDLOCK takes the range
    lock that makes the read-then-write atomic — this is the standard
    upsert-race fix and the reason MERGE is worth its awkwardness here.
*/
CREATE OR ALTER PROCEDURE norm.usp_resolve_code
    @system_uri NVARCHAR(200),
    @code       NVARCHAR(64),
    @display    NVARCHAR(400) = NULL,
    @code_concept_id INT OUTPUT
AS
BEGIN
    SET NOCOUNT ON;

    IF @system_uri IS NULL OR @code IS NULL
    BEGIN
        SET @code_concept_id = NULL;
        RETURN;
    END

    DECLARE @system_id INT;

    MERGE norm.code_system WITH (HOLDLOCK) AS target
    USING (SELECT @system_uri AS system_uri) AS source
    ON target.system_uri = source.system_uri
    WHEN NOT MATCHED BY TARGET THEN
        INSERT (system_uri, system_name, notes)
        VALUES (source.system_uri, source.system_uri, N'Discovered in source data.');

    SELECT @system_id = code_system_id FROM norm.code_system WHERE system_uri = @system_uri;

    MERGE norm.code_concept WITH (HOLDLOCK) AS target
    USING (SELECT @system_id AS code_system_id, @code AS code, @display AS display) AS source
    ON target.code_system_id = source.code_system_id AND target.code = source.code
    WHEN MATCHED AND target.display IS NULL AND source.display IS NOT NULL THEN
        UPDATE SET display = source.display
    WHEN NOT MATCHED BY TARGET THEN
        INSERT (code_system_id, code, display)
        VALUES (source.code_system_id, source.code, source.display);

    SELECT @code_concept_id = code_concept_id
    FROM norm.code_concept
    WHERE code_system_id = @system_id AND code = @code;
END
GO

/*
    Set-based registration for a whole batch of codings.

    usp_resolve_code is correct but it is one round trip per code. The shredding
    procedures extract every coding in a resource type with OPENJSON and hand
    the whole set here once, which is the difference between one statement and
    nine hundred thousand.

    @codings is a JSON array of {system, code, display} — JSON rather than a
    table-valued parameter because the caller already holds the codings as JSON
    and OPENJSON is the cheaper of the two conversions.
*/
CREATE OR ALTER PROCEDURE norm.usp_register_codings
    @codings NVARCHAR(MAX)
AS
BEGIN
    SET NOCOUNT ON;

    IF @codings IS NULL OR ISJSON(@codings) <> 1 RETURN;

    SELECT system_uri, code, display
    INTO #incoming
    FROM OPENJSON(@codings)
         WITH (system_uri NVARCHAR(200) '$.system',
               code       NVARCHAR(64)  '$.code',
               display    NVARCHAR(400) '$.display')
    WHERE code IS NOT NULL AND system_uri IS NOT NULL;

    MERGE norm.code_system WITH (HOLDLOCK) AS target
    USING (SELECT DISTINCT system_uri FROM #incoming) AS source
    ON target.system_uri = source.system_uri
    WHEN NOT MATCHED BY TARGET THEN
        INSERT (system_uri, system_name, notes)
        VALUES (source.system_uri, source.system_uri, N'Discovered in source data.');

    MERGE norm.code_concept WITH (HOLDLOCK) AS target
    USING (
        SELECT cs.code_system_id,
               i.code,
               /*  One code can arrive with two spellings of its display text in
                   the same batch. MIN makes the choice deterministic rather
                   than letting MERGE fail with "attempted to UPDATE or DELETE
                   the same row more than once", which is what a naive DISTINCT
                   over (code, display) produces.                             */
               MIN(i.display) AS display
        FROM #incoming AS i
        JOIN norm.code_system AS cs ON cs.system_uri = i.system_uri
        GROUP BY cs.code_system_id, i.code
    ) AS source
    ON target.code_system_id = source.code_system_id AND target.code = source.code
    WHEN MATCHED AND target.display IS NULL AND source.display IS NOT NULL THEN
        UPDATE SET display = source.display
    WHEN NOT MATCHED BY TARGET THEN
        INSERT (code_system_id, code, display)
        VALUES (source.code_system_id, source.code, source.display);

    DROP TABLE #incoming;
END
GO

/*
    Resolve a (system, code) pair to its key. Used in joins by every shredding
    procedure, so it is a view over the two tables rather than a function that
    would force a row-by-row call.
*/
CREATE OR ALTER VIEW norm.vw_code
AS
SELECT cc.code_concept_id,
       cs.system_uri,
       cs.system_name,
       cc.code,
       cc.display
FROM norm.code_concept AS cc
JOIN norm.code_system  AS cs ON cs.code_system_id = cc.code_system_id;
GO
