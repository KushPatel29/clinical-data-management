/*
    raw -> norm. The shredding layer.

    This is T-SQL and not Python on purpose. The mapping from a FHIR element
    path to a column is the artefact a reviewer, an auditor, or the next
    engineer needs to read, and it belongs in the repository as a queryable
    object rather than as a dictionary walk that only exists at runtime.
    docs/data-map.md is generated from what these procedures actually do.

    Three SQL Server JSON functions, used where each is right:

      OPENJSON ... WITH (...)  turns a JSON array into a rowset with a typed
                               schema. The explicit WITH clause is what makes
                               this a *set* operation — without it you get
                               key/value/type triples and end up pivoting.
      JSON_VALUE(payload, '$.x')  extracts one scalar. Returns NULL rather than
                               erroring on a missing path, which is exactly the
                               behaviour "optional element" needs.
      JSON_QUERY(payload, '$.x')  extracts an object or array to pass to a
                               nested OPENJSON. JSON_VALUE returns NULL for a
                               non-scalar, so using it here silently loses every
                               nested structure — the single most common mistake
                               with these functions.

    Every procedure is a MERGE over the current version of each resource, so
    re-running is a no-op and a re-sent resource updates in place. They read
    raw.fhir_resource joined to raw.current_version rather than through
    raw.vw_current_resource — see the comment on that table in sql/01_raw.sql
    for the 157-second statement that made the difference measurable.
*/

USE [$(DatabaseName)];
GO

-- ---------------------------------------------------------------------------
-- Codings
--
-- Every coding on every resource, registered before anything that references
-- them. Runs first because norm's code_concept_id columns are NOT NULL and a
-- shredder cannot resolve a code that has not been registered.
-- ---------------------------------------------------------------------------

CREATE OR ALTER PROCEDURE norm.usp_register_all_codings
AS
BEGIN
    SET NOCOUNT ON;

    /*  Every (system, code, display) triple anywhere in the extract, collected
        in ONE pass and reduced to its distinct set before anything is written.

        The pass is the expensive part: it reads every current resource and runs
        OPENJSON over several nested paths per Observation. It was originally
        written twice — once to discover the code systems, once to discover the
        concepts within them — which doubled the most expensive statement in the
        shred for no benefit, because the second pass produced exactly the rows
        the first one had already computed. Now it lands in a temp table and both
        MERGE statements read from that.

        UNION ALL rather than UNION: the deduplication happens once in the
        GROUP BY below, and making the engine sort ten million rows to remove
        duplicates it is about to group anyway is pure cost.

        MIN(display) because one code can arrive with two spellings of its
        display text in the same batch, and MERGE fails outright — not
        partially — if the source offers the same key twice.                  */
    /*  Synthea omits Encounter.class.display. Prefer source display text, then
        fill the small HL7 v3 ActCode class value set so reporting labels do
        not degrade to bare codes. Unknown class codes remain visible to TERM-01. */
    SELECT system_uri, code,
           COALESCE(
               MIN(NULLIF(LTRIM(RTRIM(display)), '')),
               CASE WHEN system_uri =
                    'http://terminology.hl7.org/CodeSystem/v3-ActCode'
                    THEN CASE code
                        WHEN 'AMB'    THEN 'Ambulatory'
                        WHEN 'EMER'   THEN 'Emergency'
                        WHEN 'FLD'    THEN 'Field'
                        WHEN 'HH'     THEN 'Home health'
                        WHEN 'IMP'    THEN 'Inpatient encounter'
                        WHEN 'ACUTE'  THEN 'Inpatient acute'
                        WHEN 'NONAC'  THEN 'Inpatient non-acute'
                        WHEN 'OBSENC' THEN 'Observation encounter'
                        WHEN 'PRENC'  THEN 'Pre-admission'
                        WHEN 'SS'     THEN 'Short stay'
                        WHEN 'VR'     THEN 'Virtual'
                    END
               END
           ) AS display
    INTO #codings
    FROM (
        -- Observation.code, Condition.code, Procedure.code, MedicationRequest.medication
        SELECT JSON_VALUE(c.value, '$.system')  AS system_uri,
               JSON_VALUE(c.value, '$.code')    AS code,
               JSON_VALUE(c.value, '$.display') AS display
        FROM raw.fhir_resource AS r
        JOIN raw.current_version AS cv
          ON cv.resource_type = r.resource_type AND cv.resource_id = r.resource_id
         AND cv.version_id = r.version_id
        CROSS APPLY OPENJSON(JSON_QUERY(r.payload, '$.code.coding')) AS c
        WHERE r.resource_type IN ('Observation', 'Condition', 'Procedure')

        UNION ALL

        SELECT JSON_VALUE(c.value, '$.system'), JSON_VALUE(c.value, '$.code'),
               JSON_VALUE(c.value, '$.display')
        FROM raw.fhir_resource AS r
        JOIN raw.current_version AS cv
          ON cv.resource_type = r.resource_type AND cv.resource_id = r.resource_id
         AND cv.version_id = r.version_id
        CROSS APPLY OPENJSON(JSON_QUERY(r.payload, '$.medicationCodeableConcept.coding')) AS c
        WHERE r.resource_type = 'MedicationRequest'

        UNION ALL

        -- Observation.component[].code.coding — nested two levels, which is why
        -- the outer OPENJSON hands JSON_QUERY the component object and the
        -- inner one takes its coding array.
        SELECT JSON_VALUE(cc.value, '$.system'), JSON_VALUE(cc.value, '$.code'),
               JSON_VALUE(cc.value, '$.display')
        FROM raw.fhir_resource AS r
        JOIN raw.current_version AS cv
          ON cv.resource_type = r.resource_type AND cv.resource_id = r.resource_id
         AND cv.version_id = r.version_id
        CROSS APPLY OPENJSON(JSON_QUERY(r.payload, '$.component')) AS comp
        CROSS APPLY OPENJSON(JSON_QUERY(comp.value, '$.code.coding')) AS cc
        WHERE r.resource_type = 'Observation'

        UNION ALL

        -- Observation.valueCodeableConcept and component[].valueCodeableConcept
        SELECT JSON_VALUE(c.value, '$.system'), JSON_VALUE(c.value, '$.code'),
               JSON_VALUE(c.value, '$.display')
        FROM raw.fhir_resource AS r
        JOIN raw.current_version AS cv
          ON cv.resource_type = r.resource_type AND cv.resource_id = r.resource_id
         AND cv.version_id = r.version_id
        CROSS APPLY OPENJSON(JSON_QUERY(r.payload, '$.valueCodeableConcept.coding')) AS c
        WHERE r.resource_type = 'Observation'

        UNION ALL

        SELECT JSON_VALUE(cc.value, '$.system'), JSON_VALUE(cc.value, '$.code'),
               JSON_VALUE(cc.value, '$.display')
        FROM raw.fhir_resource AS r
        JOIN raw.current_version AS cv
          ON cv.resource_type = r.resource_type AND cv.resource_id = r.resource_id
         AND cv.version_id = r.version_id
        CROSS APPLY OPENJSON(JSON_QUERY(r.payload, '$.component')) AS comp
        CROSS APPLY OPENJSON(JSON_QUERY(comp.value, '$.valueCodeableConcept.coding')) AS cc
        WHERE r.resource_type = 'Observation'

        UNION ALL

        -- Encounter.class is a bare Coding, not a CodeableConcept: no .coding
        -- array to descend into. Treating it like the others returns nothing.
        SELECT JSON_VALUE(r.payload, '$.class.system'),
               JSON_VALUE(r.payload, '$.class.code'),
               JSON_VALUE(r.payload, '$.class.display')
        FROM raw.fhir_resource AS r
        JOIN raw.current_version AS cv
          ON cv.resource_type = r.resource_type AND cv.resource_id = r.resource_id
         AND cv.version_id = r.version_id
        WHERE r.resource_type = 'Encounter'

        UNION ALL

        -- Encounter.type[].coding[] and Organization.type[].coding[]
        SELECT JSON_VALUE(c.value, '$.system'), JSON_VALUE(c.value, '$.code'),
               JSON_VALUE(c.value, '$.display')
        FROM raw.fhir_resource AS r
        JOIN raw.current_version AS cv
          ON cv.resource_type = r.resource_type AND cv.resource_id = r.resource_id
         AND cv.version_id = r.version_id
        CROSS APPLY OPENJSON(JSON_QUERY(r.payload, '$.type')) AS t
        CROSS APPLY OPENJSON(JSON_QUERY(t.value, '$.coding')) AS c
        WHERE r.resource_type IN ('Encounter', 'Organization')

        UNION ALL

        SELECT JSON_VALUE(c.value, '$.system'), JSON_VALUE(c.value, '$.code'),
               JSON_VALUE(c.value, '$.display')
        FROM raw.fhir_resource AS r
        JOIN raw.current_version AS cv
          ON cv.resource_type = r.resource_type AND cv.resource_id = r.resource_id
         AND cv.version_id = r.version_id
        CROSS APPLY OPENJSON(JSON_QUERY(r.payload, '$.maritalStatus.coding')) AS c
        WHERE r.resource_type = 'Patient'
    ) AS codings
    WHERE code IS NOT NULL AND system_uri IS NOT NULL
    GROUP BY system_uri, code;

    MERGE norm.code_system WITH (HOLDLOCK) AS target
    USING (SELECT DISTINCT system_uri FROM #codings) AS source
    ON target.system_uri = source.system_uri
    WHEN NOT MATCHED BY TARGET THEN
        INSERT (system_uri, system_name, notes)
        VALUES (source.system_uri, source.system_uri, N'Discovered in source data.');

    MERGE norm.code_concept WITH (HOLDLOCK) AS target
    USING (
        SELECT cs.code_system_id, c.code, c.display
        FROM #codings AS c
        JOIN norm.code_system AS cs ON cs.system_uri = c.system_uri
    ) AS source
    ON target.code_system_id = source.code_system_id AND target.code = source.code
    WHEN MATCHED AND target.display IS NULL AND source.display IS NOT NULL THEN
        UPDATE SET display = source.display
    WHEN NOT MATCHED BY TARGET THEN
        INSERT (code_system_id, code, display)
        VALUES (source.code_system_id, source.code, source.display);

    DROP TABLE #codings;
END
GO

-- ---------------------------------------------------------------------------
-- Organizations and practitioners
-- ---------------------------------------------------------------------------

CREATE OR ALTER PROCEDURE norm.usp_load_organization
AS
BEGIN
    SET NOCOUNT ON;

    MERGE norm.organization AS target
    USING (
        SELECT
            r.resource_id AS organization_id,
            j.name,
            j.is_active,
            /*  address is 0..* and this model keeps one. `$.address[0].line[0]`
                indexes twice: the first address, its first line. Getting the
                second index wrong returns the whole array as NULL under
                JSON_VALUE rather than erroring, which is how an address column
                ends up empty for every row without anything failing.        */
            JSON_VALUE(r.payload, '$.address[0].line[0]')   AS address_line,
            JSON_VALUE(r.payload, '$.address[0].city')      AS city,
            JSON_VALUE(r.payload, '$.address[0].state')     AS state,
            JSON_VALUE(r.payload, '$.address[0].postalCode') AS postal_code,
            JSON_VALUE(r.payload, '$.address[0].country')   AS country,
            cc.code_concept_id AS type_code_concept_id
        FROM raw.fhir_resource AS r
        JOIN raw.current_version AS cv
          ON cv.resource_type = r.resource_type AND cv.resource_id = r.resource_id
         AND cv.version_id = r.version_id
        CROSS APPLY OPENJSON(r.payload)
            WITH (name NVARCHAR(300) '$.name',
                  is_active BIT      '$.active') AS j
        OUTER APPLY (
            SELECT TOP (1) v.code_concept_id
            FROM norm.vw_code AS v
            WHERE v.system_uri = JSON_VALUE(r.payload, '$.type[0].coding[0].system')
              AND v.code       = JSON_VALUE(r.payload, '$.type[0].coding[0].code')
        ) AS cc
        WHERE r.resource_type = 'Organization'
    ) AS source
    ON target.organization_id = source.organization_id
    WHEN MATCHED THEN UPDATE SET
        name = source.name, is_active = source.is_active,
        address_line = source.address_line, city = source.city, state = source.state,
        postal_code = source.postal_code, country = source.country,
        type_code_concept_id = source.type_code_concept_id
    WHEN NOT MATCHED BY TARGET THEN
        INSERT (organization_id, name, is_active, address_line, city, state,
                postal_code, country, type_code_concept_id)
        VALUES (source.organization_id, source.name, source.is_active, source.address_line,
                source.city, source.state, source.postal_code, source.country,
                source.type_code_concept_id);

    /*  The identifiers, without which conditional references cannot resolve.
        Deduplicated on (system, value) because the target's primary key is
        (system, value) and MERGE fails outright — not partially — if the source
        offers the same key twice.                                            */
    MERGE norm.organization_identifier AS target
    USING (
        SELECT identifier_system, identifier_value, MIN(organization_id) AS organization_id
        FROM (
            SELECT JSON_VALUE(i.value, '$.system') AS identifier_system,
                   JSON_VALUE(i.value, '$.value')  AS identifier_value,
                   r.resource_id                   AS organization_id
            FROM raw.fhir_resource AS r
        JOIN raw.current_version AS cv
          ON cv.resource_type = r.resource_type AND cv.resource_id = r.resource_id
         AND cv.version_id = r.version_id
            CROSS APPLY OPENJSON(JSON_QUERY(r.payload, '$.identifier')) AS i
            WHERE r.resource_type = 'Organization'
        ) AS raw_ids
        WHERE identifier_system IS NOT NULL AND identifier_value IS NOT NULL
          AND EXISTS (SELECT 1 FROM norm.organization AS o WHERE o.organization_id = raw_ids.organization_id)
        GROUP BY identifier_system, identifier_value
    ) AS source
    ON target.identifier_system = source.identifier_system
       AND target.identifier_value = source.identifier_value
    WHEN MATCHED AND target.organization_id <> source.organization_id THEN
        UPDATE SET organization_id = source.organization_id
    WHEN NOT MATCHED BY TARGET THEN
        INSERT (identifier_system, identifier_value, organization_id)
        VALUES (source.identifier_system, source.identifier_value, source.organization_id);
END
GO

CREATE OR ALTER PROCEDURE norm.usp_load_practitioner
AS
BEGIN
    SET NOCOUNT ON;

    MERGE norm.practitioner AS target
    USING (
        SELECT
            r.resource_id AS practitioner_id,
            JSON_VALUE(r.payload, '$.name[0].family')    AS family_name,
            JSON_VALUE(r.payload, '$.name[0].given[0]')  AS given_name,
            JSON_VALUE(r.payload, '$.name[0].prefix[0]') AS name_prefix,
            JSON_VALUE(r.payload, '$.gender')            AS gender,
            CAST(JSON_VALUE(r.payload, '$.active') AS BIT) AS is_active
        FROM raw.fhir_resource AS r
        JOIN raw.current_version AS cv
          ON cv.resource_type = r.resource_type AND cv.resource_id = r.resource_id
         AND cv.version_id = r.version_id
        WHERE r.resource_type = 'Practitioner'
    ) AS source
    ON target.practitioner_id = source.practitioner_id
    WHEN MATCHED THEN UPDATE SET
        family_name = source.family_name, given_name = source.given_name,
        name_prefix = source.name_prefix, gender = source.gender, is_active = source.is_active
    WHEN NOT MATCHED BY TARGET THEN
        INSERT (practitioner_id, family_name, given_name, name_prefix, gender, is_active)
        VALUES (source.practitioner_id, source.family_name, source.given_name,
                source.name_prefix, source.gender, source.is_active);

    MERGE norm.practitioner_identifier AS target
    USING (
        SELECT identifier_system, identifier_value, MIN(practitioner_id) AS practitioner_id
        FROM (
            SELECT JSON_VALUE(i.value, '$.system') AS identifier_system,
                   JSON_VALUE(i.value, '$.value')  AS identifier_value,
                   r.resource_id                   AS practitioner_id
            FROM raw.fhir_resource AS r
        JOIN raw.current_version AS cv
          ON cv.resource_type = r.resource_type AND cv.resource_id = r.resource_id
         AND cv.version_id = r.version_id
            CROSS APPLY OPENJSON(JSON_QUERY(r.payload, '$.identifier')) AS i
            WHERE r.resource_type = 'Practitioner'
        ) AS raw_ids
        WHERE identifier_system IS NOT NULL AND identifier_value IS NOT NULL
          AND EXISTS (SELECT 1 FROM norm.practitioner AS p WHERE p.practitioner_id = raw_ids.practitioner_id)
        GROUP BY identifier_system, identifier_value
    ) AS source
    ON target.identifier_system = source.identifier_system
       AND target.identifier_value = source.identifier_value
    WHEN MATCHED AND target.practitioner_id <> source.practitioner_id THEN
        UPDATE SET practitioner_id = source.practitioner_id
    WHEN NOT MATCHED BY TARGET THEN
        INSERT (identifier_system, identifier_value, practitioner_id)
        VALUES (source.identifier_system, source.identifier_value, source.practitioner_id);
END
GO

-- ---------------------------------------------------------------------------
-- Patient
-- ---------------------------------------------------------------------------

CREATE OR ALTER PROCEDURE norm.usp_load_patient
AS
BEGIN
    SET NOCOUNT ON;

    MERGE norm.patient AS target
    USING (
        SELECT
            r.resource_id AS patient_id,
            r.version_id  AS source_version_id,
            r.last_updated AS source_last_updated,
            j.family_name, j.given_name, j.birth_date, j.gender,
            j.marital_status_code, j.marital_status_display, j.deceased_datetime
        FROM raw.fhir_resource AS r
        JOIN raw.current_version AS cv
          ON cv.resource_type = r.resource_type AND cv.resource_id = r.resource_id
         AND cv.version_id = r.version_id
        /*  One OPENJSON ... WITH over the whole resource rather than eight
            JSON_VALUE calls: the payload is parsed once instead of eight times.
            The path syntax in the WITH clause is what makes it possible to pull
            a nested scalar without a second OPENJSON.                        */
        CROSS APPLY OPENJSON(r.payload) WITH (
            family_name            NVARCHAR(200) '$.name[0].family',
            given_name             NVARCHAR(200) '$.name[0].given[0]',
            birth_date             DATE          '$.birthDate',
            gender                 VARCHAR(16)   '$.gender',
            marital_status_code    VARCHAR(16)   '$.maritalStatus.coding[0].code',
            marital_status_display NVARCHAR(100) '$.maritalStatus.coding[0].display',
            deceased_datetime      DATETIME2(3)  '$.deceasedDateTime'
        ) AS j
        WHERE r.resource_type = 'Patient'
    ) AS source
    ON target.patient_id = source.patient_id
    WHEN MATCHED THEN UPDATE SET
        family_name = source.family_name, given_name = source.given_name,
        birth_date = source.birth_date, gender = source.gender,
        marital_status_code = source.marital_status_code,
        marital_status_display = source.marital_status_display,
        deceased_datetime = source.deceased_datetime,
        source_version_id = source.source_version_id,
        source_last_updated = source.source_last_updated
    WHEN NOT MATCHED BY TARGET THEN
        INSERT (patient_id, family_name, given_name, birth_date, gender,
                marital_status_code, marital_status_display, deceased_datetime,
                source_version_id, source_last_updated)
        VALUES (source.patient_id, source.family_name, source.given_name, source.birth_date,
                source.gender, source.marital_status_code, source.marital_status_display,
                source.deceased_datetime, source.source_version_id, source.source_last_updated);

    /*  Addresses. `WITH (... AS JSON)` is not needed here because OPENJSON on
        the array already gives one row per address; `key` is the ordinal.    */
    MERGE norm.patient_address AS target
    USING (
        SELECT
            r.resource_id                     AS patient_id,
            CAST(a.[key] AS SMALLINT)         AS address_seq,
            JSON_VALUE(a.value, '$.use')      AS address_use,
            JSON_VALUE(a.value, '$.line[0]')  AS address_line,
            JSON_VALUE(a.value, '$.city')     AS city,
            JSON_VALUE(a.value, '$.state')    AS state,
            JSON_VALUE(a.value, '$.postalCode') AS postal_code,
            JSON_VALUE(a.value, '$.country')  AS country
        FROM raw.fhir_resource AS r
        JOIN raw.current_version AS cv
          ON cv.resource_type = r.resource_type AND cv.resource_id = r.resource_id
         AND cv.version_id = r.version_id
        CROSS APPLY OPENJSON(JSON_QUERY(r.payload, '$.address')) AS a
        WHERE r.resource_type = 'Patient'
          AND EXISTS (SELECT 1 FROM norm.patient AS p WHERE p.patient_id = r.resource_id)
    ) AS source
    ON target.patient_id = source.patient_id AND target.address_seq = source.address_seq
    WHEN MATCHED THEN UPDATE SET
        address_use = source.address_use, address_line = source.address_line,
        city = source.city, state = source.state,
        postal_code = source.postal_code, country = source.country
    WHEN NOT MATCHED BY TARGET THEN
        INSERT (patient_id, address_seq, address_use, address_line, city, state, postal_code, country)
        VALUES (source.patient_id, source.address_seq, source.address_use, source.address_line,
                source.city, source.state, source.postal_code, source.country);
END
GO

-- ---------------------------------------------------------------------------
-- Reference resolution
--
-- The view that turns a FHIR reference string into a local key, handling both
-- forms. Written once, because getting it right once and wrong in five other
-- places is the realistic failure mode.
-- ---------------------------------------------------------------------------

/*
    Literal reference form: "Patient/1234" -> "1234". Everything after the last
    slash, and NULL for a conditional reference, which has no literal id.

    **One RETURN statement, and that is not a style choice.** The first version
    of this was written the obvious way:

        IF @reference IS NULL OR CHARINDEX('?', @reference) > 0 RETURN NULL;
        RETURN CAST(RIGHT(...) AS VARCHAR(64));

    Two statements. SQL Server 2019 introduced scalar UDF inlining (Froid), which
    rewrites a qualifying function into the calling query — but a
    multi-statement body does not qualify, so the function stayed a black box the
    optimizer could not see into, and a black box in a query is a query that
    **cannot go parallel at all**. The optimizer says so in as many words:

        NonParallelPlanReason="TSQLUserDefinedFunctionsNotParallelizable"

    Every shredding procedure calls this. The whole raw-to-norm load was
    therefore running at DOP 1 on a machine the optimizer would otherwise have
    given eleven schedulers, and the encounter MERGE alone ran for more than
    five minutes on 169,906 rows before it was killed. Rewritten as a single
    CASE expression it inlines, the reason disappears from the plan, and the
    same query is granted DOP 11.

    Measured before and after in docs/performance.md. It is the single largest
    performance change in this repository and it is four lines of SQL.
*/
CREATE OR ALTER FUNCTION norm.fn_reference_id (@reference NVARCHAR(400))
RETURNS VARCHAR(64)
WITH SCHEMABINDING
AS
BEGIN
    RETURN CASE
        WHEN @reference IS NULL OR CHARINDEX('?', @reference) > 0 THEN NULL
        ELSE CAST(RIGHT(@reference, CHARINDEX('/', REVERSE(@reference)) - 1) AS VARCHAR(64))
    END;
END
GO

/*
    The same resolution as an INLINE TABLE-VALUED function, and this is the form
    the shredding procedures actually use.

    Making the scalar function single-statement got it inlined — `sys.sql_modules
    .is_inlineable` says 1, and a plain SELECT that calls it shows no UDF in the
    plan and is granted DOP 11. Inside a MERGE it is a different story. The
    estimated plan for norm.usp_load_encounter still carried

        <UserDefinedFunction FunctionName="[norm].[fn_reference_id]">
        NonParallelPlanReason="TSQLUserDefinedFunctionsNotParallelizable"

    so the encounter shred was *still* serial after the rewrite, and ran for five
    minutes before it was killed a second time. Scalar UDF inlining is applied
    selectively, and a MERGE is one of the places it is not applied. `is_inlineable`
    means "this function could be inlined", not "this call was".

    An inline table-valued function has no such caveat: it is not a UDF the
    optimizer chooses to expand, it is a parameterised view that gets expanded
    into the calling query by definition. Called with CROSS APPLY it also reads
    better than the nested-function form, because each reference is resolved once
    and named, instead of the same expression appearing in the select list and
    again in an EXISTS.

    The scalar function is kept for interactive use, where one row at a time is
    the point.
*/
CREATE OR ALTER FUNCTION norm.tvf_reference_id (@reference NVARCHAR(400))
RETURNS TABLE
WITH SCHEMABINDING
AS
RETURN
    SELECT resource_id = CASE
        WHEN @reference IS NULL OR CHARINDEX('?', @reference) > 0 THEN NULL
        ELSE CAST(RIGHT(@reference, CHARINDEX('/', REVERSE(@reference)) - 1) AS VARCHAR(64))
    END;
GO

/*  Conditional form: "Practitioner?identifier=<system>|<value>". Split at the
    '=' and the '|'. Kept as a view over the raw table rather than a scalar
    function so it stays set-based — a scalar UDF called per row on 170,000
    encounters is the classic reason a load takes twenty minutes.            */
CREATE OR ALTER VIEW norm.vw_encounter_reference
AS
SELECT
    r.resource_id AS encounter_id,
    subj.resource_id AS patient_id,
    /*  serviceProvider and the primary performer are conditional references,
        so they resolve through the identifier tables. The two SUBSTRING
        expressions pull <system> and <value> out of
        "Organization?identifier=<system>|<value>".                          */
    org.organization_id AS service_provider_id,
    prf.practitioner_id AS primary_performer_id
FROM raw.fhir_resource AS r
JOIN raw.current_version AS cv
  ON cv.resource_type = r.resource_type AND cv.resource_id = r.resource_id
 AND cv.version_id = r.version_id
CROSS APPLY norm.tvf_reference_id(JSON_VALUE(r.payload, '$.subject.reference')) AS subj
OUTER APPLY (
    SELECT reference = JSON_VALUE(r.payload, '$.serviceProvider.reference')
) AS sp
OUTER APPLY (
    SELECT
        system_uri = SUBSTRING(sp.reference,
                        CHARINDEX('=', sp.reference) + 1,
                        CHARINDEX('|', sp.reference) - CHARINDEX('=', sp.reference) - 1),
        id_value   = SUBSTRING(sp.reference, CHARINDEX('|', sp.reference) + 1, 200)
    WHERE sp.reference IS NOT NULL AND CHARINDEX('|', sp.reference) > 0
) AS sp_parts
LEFT JOIN norm.organization_identifier AS org
    ON org.identifier_system = sp_parts.system_uri
   AND org.identifier_value  = sp_parts.id_value
OUTER APPLY (
    -- participant[] holds admitters and translators as well as the clinician;
    -- PPRF is the primary performer. TOP 1 with the PPRF-first ordering keeps
    -- one row per encounter without needing a window function.
    SELECT TOP (1)
        reference = JSON_VALUE(p.value, '$.individual.reference'),
        is_pprf   = CASE WHEN JSON_VALUE(p.value, '$.type[0].coding[0].code') = 'PPRF'
                         THEN 1 ELSE 0 END
    FROM OPENJSON(JSON_QUERY(r.payload, '$.participant')) AS p
    WHERE JSON_VALUE(p.value, '$.individual.reference') IS NOT NULL
    ORDER BY CASE WHEN JSON_VALUE(p.value, '$.type[0].coding[0].code') = 'PPRF'
                  THEN 0 ELSE 1 END, p.[key]
) AS part
OUTER APPLY (
    SELECT
        system_uri = SUBSTRING(part.reference,
                        CHARINDEX('=', part.reference) + 1,
                        CHARINDEX('|', part.reference) - CHARINDEX('=', part.reference) - 1),
        id_value   = SUBSTRING(part.reference, CHARINDEX('|', part.reference) + 1, 200)
    WHERE part.reference IS NOT NULL AND CHARINDEX('|', part.reference) > 0
) AS part_parts
LEFT JOIN norm.practitioner_identifier AS prf
    ON prf.identifier_system = part_parts.system_uri
   AND prf.identifier_value  = part_parts.id_value
WHERE r.resource_type = 'Encounter';
GO

-- ---------------------------------------------------------------------------
-- Encounter
-- ---------------------------------------------------------------------------

CREATE OR ALTER PROCEDURE norm.usp_load_encounter
AS
BEGIN
    SET NOCOUNT ON;

    MERGE norm.encounter AS target
    USING (
        SELECT
            r.resource_id AS encounter_id,
            ref.patient_id,
            j.status,
            j.class_code,
            COALESCE(j.class_display, class_cc.display) AS class_display,
            j.period_start,
            j.period_end,
            ref.service_provider_id,
            ref.primary_performer_id,
            cc.code_concept_id AS type_code_concept_id
        FROM raw.fhir_resource AS r
        JOIN raw.current_version AS cv
          ON cv.resource_type = r.resource_type AND cv.resource_id = r.resource_id
         AND cv.version_id = r.version_id
        JOIN norm.vw_encounter_reference AS ref ON ref.encounter_id = r.resource_id
        CROSS APPLY OPENJSON(r.payload) WITH (
            status        VARCHAR(20)   '$.status',
            class_code    VARCHAR(20)   '$.class.code',
            class_display NVARCHAR(100) '$.class.display',
            period_start  DATETIME2(3)  '$.period.start',
            period_end    DATETIME2(3)  '$.period.end'
        ) AS j
        OUTER APPLY (
            SELECT TOP (1) v.code_concept_id FROM norm.vw_code AS v
            WHERE v.system_uri = JSON_VALUE(r.payload, '$.type[0].coding[0].system')
              AND v.code       = JSON_VALUE(r.payload, '$.type[0].coding[0].code')
        ) AS cc
        OUTER APPLY (
            SELECT TOP (1) v.display FROM norm.vw_code AS v
            WHERE v.system_uri = JSON_VALUE(r.payload, '$.class.system')
              AND v.code       = j.class_code
        ) AS class_cc
        WHERE r.resource_type = 'Encounter'
          /*  An encounter whose patient is not loaded is not dropped silently:
              it fails the FK, so it is filtered here and counted by the
              dq.orphan checks. Loading it with a NULL patient would be worse —
              it would appear in every count and belong to nobody.           */
          AND EXISTS (SELECT 1 FROM norm.patient AS p WHERE p.patient_id = ref.patient_id)
    ) AS source
    ON target.encounter_id = source.encounter_id
    WHEN MATCHED THEN UPDATE SET
        patient_id = source.patient_id, status = source.status,
        class_code = source.class_code, class_display = source.class_display,
        period_start = source.period_start, period_end = source.period_end,
        service_provider_id = source.service_provider_id,
        primary_performer_id = source.primary_performer_id,
        type_code_concept_id = source.type_code_concept_id
    WHEN NOT MATCHED BY TARGET THEN
        INSERT (encounter_id, patient_id, status, class_code, class_display,
                period_start, period_end, service_provider_id, primary_performer_id,
                type_code_concept_id)
        VALUES (source.encounter_id, source.patient_id, source.status, source.class_code,
                source.class_display, source.period_start, source.period_end,
                source.service_provider_id, source.primary_performer_id,
                source.type_code_concept_id);
END
GO

-- ---------------------------------------------------------------------------
-- Condition, Observation, Procedure, MedicationRequest
-- ---------------------------------------------------------------------------

CREATE OR ALTER PROCEDURE norm.usp_load_condition
AS
BEGIN
    SET NOCOUNT ON;

    MERGE norm.condition AS target
    USING (
        SELECT
            r.resource_id AS condition_id,
            subj.resource_id AS patient_id,
            enc.resource_id AS encounter_id,
            cc.code_concept_id,
            j.clinical_status, j.verification_status,
            j.onset_datetime, j.abatement_datetime, j.recorded_date
        FROM raw.fhir_resource AS r
        JOIN raw.current_version AS cv
          ON cv.resource_type = r.resource_type AND cv.resource_id = r.resource_id
         AND cv.version_id = r.version_id
        CROSS APPLY norm.tvf_reference_id(JSON_VALUE(r.payload, '$.subject.reference')) AS subj
        CROSS APPLY norm.tvf_reference_id(JSON_VALUE(r.payload, '$.encounter.reference')) AS enc
        CROSS APPLY OPENJSON(r.payload) WITH (
            clinical_status     VARCHAR(20)  '$.clinicalStatus.coding[0].code',
            verification_status VARCHAR(20)  '$.verificationStatus.coding[0].code',
            onset_datetime      DATETIME2(3) '$.onsetDateTime',
            abatement_datetime  DATETIME2(3) '$.abatementDateTime',
            recorded_date       DATETIME2(3) '$.recordedDate'
        ) AS j
        JOIN norm.vw_code AS cc
          ON cc.system_uri = JSON_VALUE(r.payload, '$.code.coding[0].system')
         AND cc.code       = JSON_VALUE(r.payload, '$.code.coding[0].code')
        WHERE r.resource_type = 'Condition'
          AND EXISTS (SELECT 1 FROM norm.patient AS p
                      WHERE p.patient_id = subj.resource_id)
    ) AS source
    ON target.condition_id = source.condition_id
    WHEN MATCHED THEN UPDATE SET
        patient_id = source.patient_id, encounter_id = source.encounter_id,
        code_concept_id = source.code_concept_id, clinical_status = source.clinical_status,
        verification_status = source.verification_status, onset_datetime = source.onset_datetime,
        abatement_datetime = source.abatement_datetime, recorded_date = source.recorded_date
    WHEN NOT MATCHED BY TARGET THEN
        INSERT (condition_id, patient_id, encounter_id, code_concept_id, clinical_status,
                verification_status, onset_datetime, abatement_datetime, recorded_date)
        VALUES (source.condition_id, source.patient_id, source.encounter_id, source.code_concept_id,
                source.clinical_status, source.verification_status, source.onset_datetime,
                source.abatement_datetime, source.recorded_date);
END
GO

CREATE OR ALTER PROCEDURE norm.usp_load_observation
AS
BEGIN
    SET NOCOUNT ON;

    /*  Primary coding rule, written down so it is a decision and not an
        accident of array order: prefer LOINC, because an observation is a
        measurement and LOINC is the vocabulary for measurements. 0.7% of the
        observations in this extract carry both a LOINC and a SNOMED coding;
        taking coding[0] would key some of them on SNOMED and split the same
        concept across two dimension rows. The codings not chosen are still
        recorded, in norm.resource_coding.                                    */
    MERGE norm.observation AS target
    USING (
        SELECT
            r.resource_id AS observation_id,
            subj.resource_id AS patient_id,
            enc.resource_id AS encounter_id,
            primary_code.code_concept_id,
            j.category_code, j.status, j.effective_datetime, j.issued,
            j.value_quantity, j.value_unit, j.value_string,
            value_code.code_concept_id AS value_code_concept_id,
            CASE WHEN JSON_QUERY(r.payload, '$.component') IS NULL THEN 0 ELSE 1 END AS has_components
        FROM raw.fhir_resource AS r
        JOIN raw.current_version AS cv
          ON cv.resource_type = r.resource_type AND cv.resource_id = r.resource_id
         AND cv.version_id = r.version_id
        CROSS APPLY norm.tvf_reference_id(JSON_VALUE(r.payload, '$.subject.reference')) AS subj
        CROSS APPLY norm.tvf_reference_id(JSON_VALUE(r.payload, '$.encounter.reference')) AS enc
        CROSS APPLY OPENJSON(r.payload) WITH (
            category_code      VARCHAR(40)   '$.category[0].coding[0].code',
            status             VARCHAR(20)   '$.status',
            effective_datetime DATETIME2(3)  '$.effectiveDateTime',
            issued             DATETIME2(3)  '$.issued',
            value_quantity     DECIMAL(18,6) '$.valueQuantity.value',
            value_unit         NVARCHAR(40)  '$.valueQuantity.unit',
            value_string       NVARCHAR(400) '$.valueString'
        ) AS j
        CROSS APPLY (
            SELECT TOP (1) v.code_concept_id
            FROM OPENJSON(JSON_QUERY(r.payload, '$.code.coding')) AS c
            JOIN norm.vw_code AS v
              ON v.system_uri = JSON_VALUE(c.value, '$.system')
             AND v.code       = JSON_VALUE(c.value, '$.code')
            ORDER BY CASE WHEN JSON_VALUE(c.value, '$.system') = 'http://loinc.org'
                          THEN 0 ELSE 1 END, c.[key]
        ) AS primary_code
        OUTER APPLY (
            SELECT TOP (1) v.code_concept_id FROM norm.vw_code AS v
            WHERE v.system_uri = JSON_VALUE(r.payload, '$.valueCodeableConcept.coding[0].system')
              AND v.code       = JSON_VALUE(r.payload, '$.valueCodeableConcept.coding[0].code')
        ) AS value_code
        WHERE r.resource_type = 'Observation'
          AND EXISTS (SELECT 1 FROM norm.patient AS p
                      WHERE p.patient_id = subj.resource_id)
    ) AS source
    ON target.observation_id = source.observation_id
    WHEN MATCHED THEN UPDATE SET
        patient_id = source.patient_id, encounter_id = source.encounter_id,
        code_concept_id = source.code_concept_id, category_code = source.category_code,
        status = source.status, effective_datetime = source.effective_datetime,
        issued = source.issued, value_quantity = source.value_quantity,
        value_unit = source.value_unit, value_string = source.value_string,
        value_code_concept_id = source.value_code_concept_id,
        has_components = source.has_components
    WHEN NOT MATCHED BY TARGET THEN
        INSERT (observation_id, patient_id, encounter_id, code_concept_id, category_code,
                status, effective_datetime, issued, value_quantity, value_unit,
                value_string, value_code_concept_id, has_components)
        VALUES (source.observation_id, source.patient_id, source.encounter_id,
                source.code_concept_id, source.category_code, source.status,
                source.effective_datetime, source.issued, source.value_quantity,
                source.value_unit, source.value_string, source.value_code_concept_id,
                source.has_components);

    MERGE norm.observation_component AS target
    USING (
        SELECT
            r.resource_id AS observation_id,
            CAST(comp.[key] AS SMALLINT) + 1 AS component_seq,
            cc.code_concept_id,
            TRY_CAST(JSON_VALUE(comp.value, '$.valueQuantity.value') AS DECIMAL(18,6)) AS value_quantity,
            JSON_VALUE(comp.value, '$.valueQuantity.unit') AS value_unit,
            vc.code_concept_id AS value_code_concept_id
        FROM raw.fhir_resource AS r
        JOIN raw.current_version AS cv
          ON cv.resource_type = r.resource_type AND cv.resource_id = r.resource_id
         AND cv.version_id = r.version_id
        CROSS APPLY OPENJSON(JSON_QUERY(r.payload, '$.component')) AS comp
        JOIN norm.vw_code AS cc
          ON cc.system_uri = JSON_VALUE(comp.value, '$.code.coding[0].system')
         AND cc.code       = JSON_VALUE(comp.value, '$.code.coding[0].code')
        OUTER APPLY (
            SELECT TOP (1) v.code_concept_id FROM norm.vw_code AS v
            WHERE v.system_uri = JSON_VALUE(comp.value, '$.valueCodeableConcept.coding[0].system')
              AND v.code       = JSON_VALUE(comp.value, '$.valueCodeableConcept.coding[0].code')
        ) AS vc
        WHERE r.resource_type = 'Observation'
          AND EXISTS (SELECT 1 FROM norm.observation AS o WHERE o.observation_id = r.resource_id)
    ) AS source
    ON target.observation_id = source.observation_id AND target.component_seq = source.component_seq
    WHEN MATCHED THEN UPDATE SET
        code_concept_id = source.code_concept_id, value_quantity = source.value_quantity,
        value_unit = source.value_unit, value_code_concept_id = source.value_code_concept_id
    WHEN NOT MATCHED BY TARGET THEN
        INSERT (observation_id, component_seq, code_concept_id, value_quantity,
                value_unit, value_code_concept_id)
        VALUES (source.observation_id, source.component_seq, source.code_concept_id,
                source.value_quantity, source.value_unit, source.value_code_concept_id);
END
GO

CREATE OR ALTER PROCEDURE norm.usp_load_procedure
AS
BEGIN
    SET NOCOUNT ON;

    MERGE norm.[procedure] AS target
    USING (
        SELECT
            r.resource_id AS procedure_id,
            subj.resource_id AS patient_id,
            enc.resource_id AS encounter_id,
            cc.code_concept_id,
            j.status,
            /*  performed[x] is a choice: performedDateTime or performedPeriod.
                COALESCE across both branches, because a procedure with a period
                and no dateTime is not a procedure with no date.              */
            COALESCE(j.performed_datetime, j.performed_period_start) AS performed_start,
            COALESCE(j.performed_period_end, j.performed_datetime)   AS performed_end
        FROM raw.fhir_resource AS r
        JOIN raw.current_version AS cv
          ON cv.resource_type = r.resource_type AND cv.resource_id = r.resource_id
         AND cv.version_id = r.version_id
        CROSS APPLY norm.tvf_reference_id(JSON_VALUE(r.payload, '$.subject.reference')) AS subj
        CROSS APPLY norm.tvf_reference_id(JSON_VALUE(r.payload, '$.encounter.reference')) AS enc
        CROSS APPLY OPENJSON(r.payload) WITH (
            status                 VARCHAR(20)  '$.status',
            performed_datetime     DATETIME2(3) '$.performedDateTime',
            performed_period_start DATETIME2(3) '$.performedPeriod.start',
            performed_period_end   DATETIME2(3) '$.performedPeriod.end'
        ) AS j
        JOIN norm.vw_code AS cc
          ON cc.system_uri = JSON_VALUE(r.payload, '$.code.coding[0].system')
         AND cc.code       = JSON_VALUE(r.payload, '$.code.coding[0].code')
        WHERE r.resource_type = 'Procedure'
          AND EXISTS (SELECT 1 FROM norm.patient AS p
                      WHERE p.patient_id = subj.resource_id)
    ) AS source
    ON target.procedure_id = source.procedure_id
    WHEN MATCHED THEN UPDATE SET
        patient_id = source.patient_id, encounter_id = source.encounter_id,
        code_concept_id = source.code_concept_id, status = source.status,
        performed_start = source.performed_start, performed_end = source.performed_end
    WHEN NOT MATCHED BY TARGET THEN
        INSERT (procedure_id, patient_id, encounter_id, code_concept_id, status,
                performed_start, performed_end)
        VALUES (source.procedure_id, source.patient_id, source.encounter_id,
                source.code_concept_id, source.status, source.performed_start,
                source.performed_end);
END
GO

CREATE OR ALTER PROCEDURE norm.usp_load_medication_request
AS
BEGIN
    SET NOCOUNT ON;

    MERGE norm.medication_request AS target
    USING (
        SELECT
            r.resource_id AS medication_request_id,
            subj.resource_id AS patient_id,
            enc.resource_id AS encounter_id,
            j.status, j.intent, j.authored_on,
            cc.code_concept_id,
            /*  The other branch of medication[x]. 16% of the rows here take it.
                Both branches are carried and CK_medication_request_choice
                enforces that exactly one is populated.                       */
            CASE WHEN cc.code_concept_id IS NULL
                 THEN medref.resource_id
            END AS medication_reference_id,
            prf.practitioner_id AS requester_id
        FROM raw.fhir_resource AS r
        JOIN raw.current_version AS cv
          ON cv.resource_type = r.resource_type AND cv.resource_id = r.resource_id
         AND cv.version_id = r.version_id
        CROSS APPLY norm.tvf_reference_id(JSON_VALUE(r.payload, '$.subject.reference')) AS subj
        CROSS APPLY norm.tvf_reference_id(JSON_VALUE(r.payload, '$.encounter.reference')) AS enc
        CROSS APPLY norm.tvf_reference_id(
            JSON_VALUE(r.payload, '$.medicationReference.reference')) AS medref
        CROSS APPLY OPENJSON(r.payload) WITH (
            status      VARCHAR(20)  '$.status',
            intent      VARCHAR(20)  '$.intent',
            authored_on DATETIME2(3) '$.authoredOn'
        ) AS j
        OUTER APPLY (
            SELECT TOP (1) v.code_concept_id FROM norm.vw_code AS v
            WHERE v.system_uri = JSON_VALUE(r.payload, '$.medicationCodeableConcept.coding[0].system')
              AND v.code       = JSON_VALUE(r.payload, '$.medicationCodeableConcept.coding[0].code')
        ) AS cc
        OUTER APPLY (
            SELECT reference = JSON_VALUE(r.payload, '$.requester.reference')
        ) AS req
        OUTER APPLY (
            SELECT
                system_uri = SUBSTRING(req.reference,
                                CHARINDEX('=', req.reference) + 1,
                                CHARINDEX('|', req.reference) - CHARINDEX('=', req.reference) - 1),
                id_value   = SUBSTRING(req.reference, CHARINDEX('|', req.reference) + 1, 200)
            WHERE req.reference IS NOT NULL AND CHARINDEX('|', req.reference) > 0
        ) AS req_parts
        LEFT JOIN norm.practitioner_identifier AS prf
            ON prf.identifier_system = req_parts.system_uri
           AND prf.identifier_value  = req_parts.id_value
        WHERE r.resource_type = 'MedicationRequest'
          AND EXISTS (SELECT 1 FROM norm.patient AS p
                      WHERE p.patient_id = subj.resource_id)
    ) AS source
    ON target.medication_request_id = source.medication_request_id
    WHEN MATCHED THEN UPDATE SET
        patient_id = source.patient_id, encounter_id = source.encounter_id,
        status = source.status, intent = source.intent, authored_on = source.authored_on,
        code_concept_id = source.code_concept_id,
        medication_reference_id = source.medication_reference_id,
        requester_id = source.requester_id
    WHEN NOT MATCHED BY TARGET THEN
        INSERT (medication_request_id, patient_id, encounter_id, status, intent,
                authored_on, code_concept_id, medication_reference_id, requester_id)
        VALUES (source.medication_request_id, source.patient_id, source.encounter_id,
                source.status, source.intent, source.authored_on, source.code_concept_id,
                source.medication_reference_id, source.requester_id);
END
GO

-- ---------------------------------------------------------------------------
-- Secondary codings
-- ---------------------------------------------------------------------------

CREATE OR ALTER PROCEDURE norm.usp_load_resource_codings
AS
BEGIN
    SET NOCOUNT ON;

    MERGE norm.resource_coding AS target
    USING (
        SELECT
            r.resource_type,
            r.resource_id,
            CAST(c.[key] AS SMALLINT) + 1 AS coding_seq,
            v.code_concept_id,
            CASE WHEN JSON_VALUE(c.value, '$.system') =
                      CASE r.resource_type WHEN 'Observation' THEN 'http://loinc.org'
                                           ELSE 'http://snomed.info/sct' END
                 THEN 1 ELSE 0 END AS is_primary
        FROM raw.fhir_resource AS r
        JOIN raw.current_version AS cv
          ON cv.resource_type = r.resource_type AND cv.resource_id = r.resource_id
         AND cv.version_id = r.version_id
        CROSS APPLY OPENJSON(JSON_QUERY(r.payload, '$.code.coding')) AS c
        JOIN norm.vw_code AS v
          ON v.system_uri = JSON_VALUE(c.value, '$.system')
         AND v.code       = JSON_VALUE(c.value, '$.code')
        WHERE r.resource_type IN ('Observation', 'Condition', 'Procedure')
    ) AS source
    ON target.resource_type = source.resource_type
       AND target.resource_id = source.resource_id
       AND target.coding_seq = source.coding_seq
    WHEN MATCHED THEN UPDATE SET
        code_concept_id = source.code_concept_id, is_primary = source.is_primary
    WHEN NOT MATCHED BY TARGET THEN
        INSERT (resource_type, resource_id, coding_seq, code_concept_id, is_primary)
        VALUES (source.resource_type, source.resource_id, source.coding_seq,
                source.code_concept_id, source.is_primary);
END
GO

-- ---------------------------------------------------------------------------
-- The whole layer, in dependency order.
-- ---------------------------------------------------------------------------

CREATE OR ALTER PROCEDURE norm.usp_load_all
AS
BEGIN
    SET NOCOUNT ON;
    /*  Which version of each resource is current, computed once for the whole
        shred rather than re-derived by every statement that needs it. See the
        comment on raw.current_version for the measurement that made this a
        table instead of a view.                                              */
    EXEC raw.usp_refresh_current_version;
    EXEC norm.usp_register_all_codings;
    EXEC norm.usp_load_organization;
    EXEC norm.usp_load_practitioner;
    EXEC norm.usp_load_patient;
    EXEC norm.usp_load_encounter;
    EXEC norm.usp_load_condition;
    EXEC norm.usp_load_observation;
    EXEC norm.usp_load_procedure;
    EXEC norm.usp_load_medication_request;
    EXEC norm.usp_load_resource_codings;
END
GO
