/*
    norm -> dw. The dimensional load.

    Runs in one order and only one: dimensions before facts, and DimPatient
    before any fact that needs a version-correct patient_key. Every procedure is
    re-runnable — a second run over unchanged data inserts nothing, updates
    nothing, and leaves every checksum identical, which tests/test_warehouse.py
    asserts by running the whole pipeline twice.

    Late-arriving dimensions are handled by *inference*, not by deferral. If a
    fact references a practitioner whose record has not arrived, the loader
    inserts a stub carrying only the business key with is_inferred = 1 and the
    fact gets a real surrogate key immediately. When the practitioner record
    turns up, usp_load_dim_provider updates that same row in place: the key does
    not change, so every fact already pointing at it becomes correct without
    being rewritten. The alternative — parking the fact until its dimension
    arrives — means a report that is silently missing yesterday's admissions.
*/

USE [$(DatabaseName)];
GO

-- ---------------------------------------------------------------------------
-- Type 1 dimensions
-- ---------------------------------------------------------------------------

CREATE OR ALTER PROCEDURE dw.usp_load_dim_provider
AS
BEGIN
    SET NOCOUNT ON;

    MERGE dw.DimProvider AS target
    USING (
        SELECT
            p.practitioner_id,
            npi.identifier_value AS npi,
            NULLIF(LTRIM(RTRIM(CONCAT(
                COALESCE(p.name_prefix + ' ', ''),
                COALESCE(p.given_name + ' ', ''),
                COALESCE(p.family_name, '')))), '') AS full_name,
            p.gender
        FROM norm.practitioner AS p
        OUTER APPLY (
            SELECT TOP (1) identifier_value
            FROM norm.practitioner_identifier AS i
            WHERE i.practitioner_id = p.practitioner_id
              AND i.identifier_system = 'http://hl7.org/fhir/sid/us-npi'
        ) AS npi
    ) AS source
    ON target.practitioner_id = source.practitioner_id
    /*  is_inferred is set back to 0 here — this is the moment a stub becomes a
        real member. The key is untouched, so the facts pointing at it need no
        rewrite.                                                              */
    WHEN MATCHED THEN UPDATE SET
        npi = source.npi, full_name = source.full_name,
        gender = source.gender, is_inferred = 0
    WHEN NOT MATCHED BY TARGET THEN
        INSERT (practitioner_id, npi, full_name, gender, is_inferred)
        VALUES (source.practitioner_id, source.npi, source.full_name, source.gender, 0);
END
GO

CREATE OR ALTER PROCEDURE dw.usp_load_dim_organization
AS
BEGIN
    SET NOCOUNT ON;

    MERGE dw.DimOrganization AS target
    USING (
        SELECT o.organization_id, o.name AS organization_name,
               c.display AS organization_type,
               o.city, o.state, o.postal_code
        FROM norm.organization AS o
        LEFT JOIN norm.code_concept AS c ON c.code_concept_id = o.type_code_concept_id
    ) AS source
    ON target.organization_id = source.organization_id
    WHEN MATCHED THEN UPDATE SET
        organization_name = source.organization_name,
        organization_type = source.organization_type,
        city = source.city, state = source.state,
        postal_code = source.postal_code, is_inferred = 0
    WHEN NOT MATCHED BY TARGET THEN
        INSERT (organization_id, organization_name, organization_type, city, state,
                postal_code, is_inferred)
        VALUES (source.organization_id, source.organization_name, source.organization_type,
                source.city, source.state, source.postal_code, 0);
END
GO

CREATE OR ALTER PROCEDURE dw.usp_load_code_dimensions
AS
BEGIN
    SET NOCOUNT ON;

    -- Diagnoses: the concepts actually used by a condition, not every concept
    -- in the terminology. A dimension holding a whole vocabulary is a dimension
    -- where 99% of the rows never join to a fact.
    MERGE dw.DimDiagnosis AS target
    USING (
        SELECT DISTINCT v.system_name AS code_system, v.code, v.display AS code_display
        FROM norm.condition AS c
        JOIN norm.vw_code AS v ON v.code_concept_id = c.code_concept_id
    ) AS source
    ON target.code_system = source.code_system AND target.code = source.code
    WHEN MATCHED THEN UPDATE SET code_display = source.code_display, is_inferred = 0
    WHEN NOT MATCHED BY TARGET THEN
        INSERT (code_system, code, code_display, is_inferred)
        VALUES (source.code_system, source.code, source.code_display, 0);

    MERGE dw.DimProcedure AS target
    USING (
        SELECT DISTINCT v.system_name AS code_system, v.code, v.display AS code_display
        FROM norm.[procedure] AS p
        JOIN norm.vw_code AS v ON v.code_concept_id = p.code_concept_id
    ) AS source
    ON target.code_system = source.code_system AND target.code = source.code
    WHEN MATCHED THEN UPDATE SET code_display = source.code_display, is_inferred = 0
    WHEN NOT MATCHED BY TARGET THEN
        INSERT (code_system, code, code_display, is_inferred)
        VALUES (source.code_system, source.code, source.code_display, 0);

    MERGE dw.DimMedication AS target
    USING (
        SELECT DISTINCT v.system_name AS code_system, v.code, v.display AS code_display
        FROM norm.medication_request AS m
        JOIN norm.vw_code AS v ON v.code_concept_id = m.code_concept_id
    ) AS source
    ON target.code_system = source.code_system AND target.code = source.code
    WHEN MATCHED THEN UPDATE SET code_display = source.code_display, is_inferred = 0
    WHEN NOT MATCHED BY TARGET THEN
        INSERT (code_system, code, code_display, is_inferred)
        VALUES (source.code_system, source.code, source.code_display, 0);

    -- Observation codes: the parent's code and every component's code, because
    -- FactObservation has a row for each and both need a dimension member.
    MERGE dw.DimObservationCode AS target
    USING (
        SELECT DISTINCT v.system_name AS code_system, v.code, v.display AS code_display
        FROM (
            SELECT code_concept_id FROM norm.observation
            UNION
            SELECT code_concept_id FROM norm.observation_component
        ) AS used
        JOIN norm.vw_code AS v ON v.code_concept_id = used.code_concept_id
    ) AS source
    ON target.code_system = source.code_system AND target.code = source.code
    WHEN MATCHED THEN UPDATE SET code_display = source.code_display, is_inferred = 0
    WHEN NOT MATCHED BY TARGET THEN
        INSERT (code_system, code, code_display, is_inferred)
        VALUES (source.code_system, source.code, source.code_display, 0);

    MERGE dw.DimEncounterType AS target
    USING (
        SELECT DISTINCT
            e.class_code,
            e.class_display,
            COALESCE(v.code, '(none)')    AS type_code,
            COALESCE(v.display, 'Not specified') AS type_display,
            CASE e.class_code
                WHEN 'IMP'   THEN 'Inpatient'
                WHEN 'ACUTE' THEN 'Inpatient'
                WHEN 'NONAC' THEN 'Inpatient'
                WHEN 'EMER'  THEN 'Emergency'
                WHEN 'AMB'   THEN 'Ambulatory'
                WHEN 'OBSENC' THEN 'Ambulatory'
                WHEN 'PRENC' THEN 'Ambulatory'
                WHEN 'SS'    THEN 'Ambulatory'
                WHEN 'VR'    THEN 'Virtual'
                WHEN 'HH'    THEN 'Home'
                WHEN 'FLD'   THEN 'Other'
                ELSE 'Unknown'
            END AS care_setting
        FROM norm.encounter AS e
        LEFT JOIN norm.vw_code AS v ON v.code_concept_id = e.type_code_concept_id
    ) AS source
    ON target.class_code = source.class_code AND target.type_code = source.type_code
    WHEN MATCHED THEN UPDATE SET
        class_display = source.class_display, type_display = source.type_display,
        care_setting = source.care_setting, is_inferred = 0
    WHEN NOT MATCHED BY TARGET THEN
        INSERT (class_code, class_display, type_code, type_display, care_setting, is_inferred)
        VALUES (source.class_code, source.class_display, source.type_code,
                source.type_display, source.care_setting, 0);
END
GO

-- ---------------------------------------------------------------------------
-- DimPatient — Type 2
-- ---------------------------------------------------------------------------

/*
    The hash of the tracked attributes.

    A view rather than a computed column so the definition lives in one place
    and the SCD2 procedure cannot drift from it. Three things matter:

      * The separator. Concatenating ('AB','C') and ('A','BC') without one
        produces the same hash, so a patient moving from Boston to Ludlow could
        hash identically to one moving the other way. The record separator here
        (char 31) cannot appear in an address.
      * The NULL marker. CONCAT turns NULL into '', which makes a NULL city and
        an empty-string city indistinguishable — and "attribute was cleared"
        then looks like "attribute never changed".
      * Only the *tracked* attributes go in. Adding birth_date would make a
        corrected date of birth open a new version and split every historical
        measure across two rows for no analytical gain.
*/
CREATE OR ALTER VIEW dw.vw_patient_scd_source
AS
SELECT
    p.patient_id,
    p.birth_date,
    p.gender,
    CASE
        WHEN p.birth_date IS NULL THEN NULL
        WHEN DATEDIFF(YEAR, p.birth_date, SYSUTCDATETIME()) < 18 THEN '00-17'
        WHEN DATEDIFF(YEAR, p.birth_date, SYSUTCDATETIME()) < 45 THEN '18-44'
        WHEN DATEDIFF(YEAR, p.birth_date, SYSUTCDATETIME()) < 65 THEN '45-64'
        WHEN DATEDIFF(YEAR, p.birth_date, SYSUTCDATETIME()) < 80 THEN '65-79'
        ELSE '80+'
    END AS age_band,
    p.marital_status_display AS marital_status,
    a.city  AS address_city,
    a.state AS address_state,
    a.postal_code AS address_postal_code,
    HASHBYTES('SHA2_256', CONCAT(
        COALESCE(p.marital_status_display, N'<null>'), NCHAR(31),
        COALESCE(a.city, N'<null>'),                   NCHAR(31),
        COALESCE(a.state, N'<null>'),                  NCHAR(31),
        COALESCE(a.postal_code, N'<null>')
    )) AS row_hash
FROM norm.patient AS p
OUTER APPLY (
    SELECT TOP (1) city, state, postal_code
    FROM norm.patient_address AS pa
    WHERE pa.patient_id = p.patient_id
    ORDER BY pa.address_seq
) AS a;
GO

CREATE OR ALTER PROCEDURE dw.usp_load_dim_patient
    @effective_date DATE = NULL
AS
BEGIN
    SET NOCOUNT ON;
    SET @effective_date = COALESCE(@effective_date, CAST(SYSUTCDATETIME() AS DATE));

    DECLARE @end_of_time DATE = '9999-12-31';
    -- The earliest date dw.DimDate can express. Not NULL: a NULL bound turns
    -- every BETWEEN into three-valued logic and the join silently matches
    -- nothing rather than everything.
    DECLARE @beginning_of_time DATE = '1900-01-01';

    /*  Step 1: close the versions whose tracked attributes changed.

        Ordered before the insert, and that order is not negotiable: the
        filtered unique index UQ_DimPatient_one_current allows exactly one
        current row per patient, so inserting the new version first would
        violate it. Doing it in this order means the constraint is doing real
        work — it is not decoration, it is what makes the classic
        two-current-rows bug impossible rather than merely unlikely.

        effective_to is the day *before* the new version starts. Setting it to
        the same day would make a BETWEEN join match both versions for one day
        and double every measure on that date.                                */
    UPDATE d
       SET effective_to = DATEADD(DAY, -1, @effective_date),
           is_current   = 0
    FROM dw.DimPatient AS d
    JOIN dw.vw_patient_scd_source AS s ON s.patient_id = d.patient_id
    WHERE d.is_current = 1
      AND d.patient_key <> -1
      AND d.row_hash <> s.row_hash
      /*  Guard against a same-day change: if the current version already starts
          today, closing it would set effective_to before effective_from and
          break CK_DimPatient_effective. Same-day changes overwrite instead.  */
      AND d.effective_from < @effective_date;

    -- Same-day correction: the version opened today is updated in place.
    UPDATE d
       SET marital_status = s.marital_status,
           address_city = s.address_city,
           address_state = s.address_state,
           address_postal_code = s.address_postal_code,
           birth_date = s.birth_date,
           gender = s.gender,
           age_band = s.age_band,
           row_hash = s.row_hash
    FROM dw.DimPatient AS d
    JOIN dw.vw_patient_scd_source AS s ON s.patient_id = d.patient_id
    WHERE d.is_current = 1
      AND d.patient_key <> -1
      AND d.row_hash <> s.row_hash
      AND d.effective_from = @effective_date;

    /*  Step 2: insert new versions and brand-new patients in one statement.

        effective_from is where a Type 2 dimension is most often quietly broken,
        and this repository broke it before fixing it. The first version of a
        patient must open at the *beginning of warehouse time*, not at the load
        date. Stamping it with today looks right — that is when the row was
        created — and it means every fact dated before today falls outside the
        only version that exists, so the BETWEEN join misses and every encounter
        lands on the Unknown member. Symptom: the load succeeds, every row count
        is correct, every foreign key holds, and the star schema reports that
        371 of 371 encounters belonged to nobody.

        Subsequent versions do start at the change date, because that is a real
        assertion about when the attribute changed. The first one is not an
        assertion about anything; it is the absence of prior knowledge.       */
    INSERT dw.DimPatient (patient_id, birth_date, gender, age_band, marital_status,
                          address_city, address_state, address_postal_code,
                          effective_from, effective_to, is_current, version_number,
                          row_hash, is_inferred)
    SELECT s.patient_id, s.birth_date, s.gender, s.age_band, s.marital_status,
           s.address_city, s.address_state, s.address_postal_code,
           CASE WHEN prior.max_version IS NULL THEN @beginning_of_time ELSE @effective_date END,
           @end_of_time, 1,
           COALESCE(prior.max_version, 0) + 1,
           s.row_hash, 0
    FROM dw.vw_patient_scd_source AS s
    OUTER APPLY (
        SELECT MAX(version_number) AS max_version
        FROM dw.DimPatient AS d
        WHERE d.patient_id = s.patient_id
    ) AS prior
    WHERE NOT EXISTS (
        SELECT 1 FROM dw.DimPatient AS d
        WHERE d.patient_id = s.patient_id AND d.is_current = 1
    );

    /*  Step 3: an inferred patient stub becomes real. Same rule as the
        provider dimension — update in place, never re-key.                   */
    UPDATE d
       SET birth_date = s.birth_date, gender = s.gender, age_band = s.age_band,
           marital_status = s.marital_status, address_city = s.address_city,
           address_state = s.address_state, address_postal_code = s.address_postal_code,
           row_hash = s.row_hash, is_inferred = 0
    FROM dw.DimPatient AS d
    JOIN dw.vw_patient_scd_source AS s ON s.patient_id = d.patient_id
    WHERE d.is_current = 1 AND d.is_inferred = 1;
END
GO

-- ---------------------------------------------------------------------------
-- Late-arriving dimension members
-- ---------------------------------------------------------------------------

CREATE OR ALTER PROCEDURE dw.usp_infer_missing_members
AS
BEGIN
    SET NOCOUNT ON;

    -- Patients referenced by an encounter but absent from the dimension.
    INSERT dw.DimPatient (patient_id, effective_from, effective_to, is_current,
                          version_number, row_hash, is_inferred)
    SELECT DISTINCT e.patient_id, '1900-01-01', '9999-12-31', 1, 1,
           HASHBYTES('SHA2_256', N'<inferred>'), 1
    FROM norm.encounter AS e
    WHERE NOT EXISTS (SELECT 1 FROM dw.DimPatient AS d
                      WHERE d.patient_id = e.patient_id AND d.is_current = 1);

    INSERT dw.DimProvider (practitioner_id, full_name, is_inferred)
    SELECT DISTINCT e.primary_performer_id, N'(late-arriving provider)', 1
    FROM norm.encounter AS e
    WHERE e.primary_performer_id IS NOT NULL
      AND NOT EXISTS (SELECT 1 FROM dw.DimProvider AS d
                      WHERE d.practitioner_id = e.primary_performer_id);

    INSERT dw.DimOrganization (organization_id, organization_name, is_inferred)
    SELECT DISTINCT e.service_provider_id, N'(late-arriving organization)', 1
    FROM norm.encounter AS e
    WHERE e.service_provider_id IS NOT NULL
      AND NOT EXISTS (SELECT 1 FROM dw.DimOrganization AS d
                      WHERE d.organization_id = e.service_provider_id);
END
GO

-- ---------------------------------------------------------------------------
-- Facts
-- ---------------------------------------------------------------------------

CREATE OR ALTER PROCEDURE dw.usp_load_fact_encounter
    @batch_id BIGINT = NULL
AS
BEGIN
    SET NOCOUNT ON;

    /*  Readmission is computed here rather than in the report, because it needs
        the *prior* inpatient discharge for the same patient and a report tool
        would have to self-join the fact table to get it. LAG over the patient's
        inpatient encounters ordered by admission is one pass.

        Definition, stated because "30-day readmission" means four different
        things in four different hospitals: an inpatient admission whose start
        is within 30 days of the discharge of that patient's previous inpatient
        encounter. Not all-cause-any-setting, not index-admission-anchored.   */
    WITH inpatient AS (
        SELECT e.encounter_id, e.patient_id, e.period_start, e.period_end,
               LAG(e.period_end) OVER (PARTITION BY e.patient_id ORDER BY e.period_start,
                                                                          e.encounter_id)
                   AS prior_discharge
        FROM norm.encounter AS e
        WHERE e.class_code IN ('IMP', 'ACUTE', 'NONAC')
    ),
    source AS (
        SELECT
            e.encounter_id,
            COALESCE(dp.patient_key, -1)  AS patient_key,
            COALESCE(dpr.provider_key, -1) AS provider_key,
            COALESCE(dorg.organization_key, -1) AS organization_key,
            COALESCE(det.encounter_type_key, -1) AS encounter_type_key,
            COALESCE(sd.date_key, -1) AS start_date_key,
            COALESCE(ed.date_key, -1) AS end_date_key,
            e.status AS encounter_status,
            CASE WHEN e.period_end IS NULL OR e.period_start IS NULL THEN NULL
                 ELSE CAST(DATEDIFF(SECOND, e.period_start, e.period_end) AS DECIMAL(18,4)) / 86400.0
            END AS length_of_stay_days,
            CASE WHEN e.period_end IS NULL OR e.period_start IS NULL THEN NULL
                 ELSE DATEDIFF(MINUTE, e.period_start, e.period_end) END AS length_of_stay_minutes,
            CASE WHEN e.class_code IN ('IMP', 'ACUTE', 'NONAC') THEN 1 ELSE 0 END AS is_inpatient,
            CASE WHEN e.class_code = 'EMER' THEN 1 ELSE 0 END AS is_emergency,
            CASE WHEN ip.prior_discharge IS NOT NULL
                   AND DATEDIFF(DAY, ip.prior_discharge, e.period_start) BETWEEN 0 AND 30
                 THEN 1 ELSE 0 END AS is_readmission_30d,
            CASE WHEN ip.prior_discharge IS NOT NULL
                 THEN DATEDIFF(DAY, ip.prior_discharge, e.period_start) END AS days_since_prior_discharge
        FROM norm.encounter AS e
        /*  The Type 2 join. `BETWEEN effective_from AND effective_to` on the
            encounter's *start date* is what makes the fact point at the patient
            version that was current when the encounter happened, rather than at
            whatever is current now. Joining on is_current = 1 instead is the
            single most common way a Type 2 dimension ends up doing nothing.  */
        LEFT JOIN dw.DimPatient AS dp
               ON dp.patient_id = e.patient_id
              AND CAST(COALESCE(e.period_start, '1900-01-01') AS DATE)
                  BETWEEN dp.effective_from AND dp.effective_to
        LEFT JOIN dw.DimProvider AS dpr ON dpr.practitioner_id = e.primary_performer_id
        LEFT JOIN dw.DimOrganization AS dorg ON dorg.organization_id = e.service_provider_id
        LEFT JOIN norm.vw_code AS tc ON tc.code_concept_id = e.type_code_concept_id
        LEFT JOIN dw.DimEncounterType AS det
               ON det.class_code = e.class_code
              AND det.type_code = COALESCE(tc.code, '(none)')
        LEFT JOIN inpatient AS ip ON ip.encounter_id = e.encounter_id
        /*  Look the date key up rather than computing it. A computed
            yyyymmdd for a date the dimension does not hold fails the foreign
            key and takes the whole load down; a join degrades that same row
            to the Unknown member and leaves the other 169,905 alone. It also
            drops FORMAT(), which is CLR-backed and roughly an order of
            magnitude slower than CONVERT with style 112.                  */
        LEFT JOIN dw.DimDate AS sd ON sd.full_date = CAST(e.period_start AS DATE)
        LEFT JOIN dw.DimDate AS ed ON ed.full_date = CAST(e.period_end AS DATE)
    )
    MERGE dw.FactEncounter AS target
    USING source ON target.encounter_id = source.encounter_id
    WHEN MATCHED THEN UPDATE SET
        patient_key = source.patient_key, provider_key = source.provider_key,
        organization_key = source.organization_key,
        encounter_type_key = source.encounter_type_key,
        start_date_key = source.start_date_key, end_date_key = source.end_date_key,
        encounter_status = source.encounter_status,
        length_of_stay_days = source.length_of_stay_days,
        length_of_stay_minutes = source.length_of_stay_minutes,
        is_inpatient = source.is_inpatient, is_emergency = source.is_emergency,
        is_readmission_30d = source.is_readmission_30d,
        days_since_prior_discharge = source.days_since_prior_discharge,
        load_batch_id = @batch_id
    WHEN NOT MATCHED BY TARGET THEN
        INSERT (encounter_id, patient_key, provider_key, organization_key,
                encounter_type_key, start_date_key, end_date_key, encounter_status,
                length_of_stay_days, length_of_stay_minutes, is_inpatient, is_emergency,
                is_readmission_30d, days_since_prior_discharge, load_batch_id)
        VALUES (source.encounter_id, source.patient_key, source.provider_key,
                source.organization_key, source.encounter_type_key, source.start_date_key,
                source.end_date_key, source.encounter_status, source.length_of_stay_days,
                source.length_of_stay_minutes, source.is_inpatient, source.is_emergency,
                source.is_readmission_30d, source.days_since_prior_discharge, @batch_id);
END
GO

CREATE OR ALTER PROCEDURE dw.usp_load_fact_observation
    @batch_id BIGINT = NULL
AS
BEGIN
    SET NOCOUNT ON;

    /*  Grain: one result. component_seq 0 is the observation's own value[x];
        1..n are its components. An observation carrying neither — the container
        row for a panel — produces nothing, which is the documented variance in
        the raw-to-dw reconciliation.                                          */
    WITH results AS (
        SELECT o.observation_id, CAST(0 AS SMALLINT) AS component_seq,
               o.patient_id, o.encounter_id, o.code_concept_id, o.effective_datetime,
               o.status, o.value_quantity, o.value_unit,
               COALESCE(vc.display, o.value_string) AS value_text
        FROM norm.observation AS o
        LEFT JOIN norm.code_concept AS vc ON vc.code_concept_id = o.value_code_concept_id
        WHERE o.value_quantity IS NOT NULL
           OR o.value_code_concept_id IS NOT NULL
           OR o.value_string IS NOT NULL

        UNION ALL

        SELECT c.observation_id, c.component_seq,
               o.patient_id, o.encounter_id, c.code_concept_id, o.effective_datetime,
               o.status, c.value_quantity, c.value_unit, vc.display
        FROM norm.observation_component AS c
        JOIN norm.observation AS o ON o.observation_id = c.observation_id
        LEFT JOIN norm.code_concept AS vc ON vc.code_concept_id = c.value_code_concept_id
    ),
    source AS (
        SELECT
            r.observation_id, r.component_seq,
            COALESCE(dp.patient_key, -1) AS patient_key,
            fe.encounter_key,
            COALESCE(doc.observation_code_key, -1) AS observation_code_key,
            COALESCE(ed.date_key, -1) AS effective_date_key,
            r.status AS observation_status,
            r.value_quantity AS value_numeric,
            r.value_unit,
            r.value_text,
            CASE WHEN r.value_quantity IS NOT NULL THEN 1 ELSE 0 END AS is_numeric
        FROM results AS r
        LEFT JOIN dw.DimPatient AS dp
               ON dp.patient_id = r.patient_id
              AND CAST(COALESCE(r.effective_datetime, '1900-01-01') AS DATE)
                  BETWEEN dp.effective_from AND dp.effective_to
        LEFT JOIN dw.FactEncounter AS fe ON fe.encounter_id = r.encounter_id
        LEFT JOIN norm.vw_code AS v ON v.code_concept_id = r.code_concept_id
        LEFT JOIN dw.DimObservationCode AS doc
               ON doc.code_system = v.system_name AND doc.code = v.code
        LEFT JOIN dw.DimDate AS ed ON ed.full_date = CAST(r.effective_datetime AS DATE)
    )
    MERGE dw.FactObservation AS target
    USING source
    ON target.observation_id = source.observation_id
       AND target.component_seq = source.component_seq
    WHEN MATCHED THEN UPDATE SET
        patient_key = source.patient_key, encounter_key = source.encounter_key,
        observation_code_key = source.observation_code_key,
        effective_date_key = source.effective_date_key,
        observation_status = source.observation_status,
        value_numeric = source.value_numeric, value_unit = source.value_unit,
        value_text = source.value_text, is_numeric = source.is_numeric,
        load_batch_id = @batch_id
    WHEN NOT MATCHED BY TARGET THEN
        INSERT (observation_id, component_seq, patient_key, encounter_key,
                observation_code_key, effective_date_key, observation_status,
                value_numeric, value_unit, value_text, is_numeric, load_batch_id)
        VALUES (source.observation_id, source.component_seq, source.patient_key,
                source.encounter_key, source.observation_code_key, source.effective_date_key,
                source.observation_status, source.value_numeric, source.value_unit,
                source.value_text, source.is_numeric, @batch_id);
END
GO

CREATE OR ALTER PROCEDURE dw.usp_load_fact_medication_order
    @batch_id BIGINT = NULL
AS
BEGIN
    SET NOCOUNT ON;

    MERGE dw.FactMedicationOrder AS target
    USING (
        SELECT
            m.medication_request_id,
            COALESCE(dp.patient_key, -1) AS patient_key,
            fe.encounter_key,
            COALESCE(dpr.provider_key, -1) AS provider_key,
            COALESCE(dm.medication_key, -1) AS medication_key,
            COALESCE(ad.date_key, -1) AS authored_date_key,
            m.status AS order_status,
            m.intent AS order_intent,
            CASE WHEN m.status = 'active' THEN 1 ELSE 0 END AS is_active
        FROM norm.medication_request AS m
        LEFT JOIN dw.DimPatient AS dp
               ON dp.patient_id = m.patient_id
              AND CAST(COALESCE(m.authored_on, '1900-01-01') AS DATE)
                  BETWEEN dp.effective_from AND dp.effective_to
        LEFT JOIN dw.FactEncounter AS fe ON fe.encounter_id = m.encounter_id
        LEFT JOIN dw.DimProvider AS dpr ON dpr.practitioner_id = m.requester_id
        LEFT JOIN norm.vw_code AS v ON v.code_concept_id = m.code_concept_id
        LEFT JOIN dw.DimMedication AS dm
               ON dm.code_system = v.system_name AND dm.code = v.code
        LEFT JOIN dw.DimDate AS ad ON ad.full_date = CAST(m.authored_on AS DATE)
    ) AS source
    ON target.medication_request_id = source.medication_request_id
    WHEN MATCHED THEN UPDATE SET
        patient_key = source.patient_key, encounter_key = source.encounter_key,
        provider_key = source.provider_key, medication_key = source.medication_key,
        authored_date_key = source.authored_date_key, order_status = source.order_status,
        order_intent = source.order_intent, is_active = source.is_active,
        load_batch_id = @batch_id
    WHEN NOT MATCHED BY TARGET THEN
        INSERT (medication_request_id, patient_key, encounter_key, provider_key,
                medication_key, authored_date_key, order_status, order_intent,
                is_active, load_batch_id)
        VALUES (source.medication_request_id, source.patient_key, source.encounter_key,
                source.provider_key, source.medication_key, source.authored_date_key,
                source.order_status, source.order_intent, source.is_active, @batch_id);
END
GO

CREATE OR ALTER PROCEDURE dw.usp_load_fact_encounter_diagnosis
    @batch_id BIGINT = NULL
AS
BEGIN
    SET NOCOUNT ON;

    MERGE dw.FactEncounterDiagnosis AS target
    USING (
        SELECT
            c.condition_id,
            fe.encounter_key,
            COALESCE(dp.patient_key, -1) AS patient_key,
            COALESCE(dd.diagnosis_key, -1) AS diagnosis_key,
            COALESCE(od.date_key, -1) AS onset_date_key,
            c.clinical_status
        FROM norm.condition AS c
        LEFT JOIN dw.DimPatient AS dp
               ON dp.patient_id = c.patient_id
              AND CAST(COALESCE(c.onset_datetime, c.recorded_date, '1900-01-01') AS DATE)
                  BETWEEN dp.effective_from AND dp.effective_to
        LEFT JOIN dw.FactEncounter AS fe ON fe.encounter_id = c.encounter_id
        LEFT JOIN norm.vw_code AS v ON v.code_concept_id = c.code_concept_id
        LEFT JOIN dw.DimDiagnosis AS dd
               ON dd.code_system = v.system_name AND dd.code = v.code
        LEFT JOIN dw.DimDate AS od
               ON od.full_date = CAST(COALESCE(c.onset_datetime, c.recorded_date) AS DATE)
    ) AS source
    ON target.condition_id = source.condition_id
    WHEN MATCHED THEN UPDATE SET
        encounter_key = source.encounter_key, patient_key = source.patient_key,
        diagnosis_key = source.diagnosis_key, onset_date_key = source.onset_date_key,
        clinical_status = source.clinical_status, load_batch_id = @batch_id
    WHEN NOT MATCHED BY TARGET THEN
        INSERT (condition_id, encounter_key, patient_key, diagnosis_key,
                onset_date_key, clinical_status, load_batch_id)
        VALUES (source.condition_id, source.encounter_key, source.patient_key,
                source.diagnosis_key, source.onset_date_key, source.clinical_status, @batch_id);
END
GO

CREATE OR ALTER PROCEDURE dw.usp_load_fact_procedure
    @batch_id BIGINT = NULL
AS
BEGIN
    SET NOCOUNT ON;

    MERGE dw.FactProcedure AS target
    USING (
        SELECT
            p.procedure_id,
            COALESCE(dp.patient_key, -1) AS patient_key,
            fe.encounter_key,
            COALESCE(dpr.procedure_key, -1) AS procedure_key,
            COALESCE(pd.date_key, -1) AS performed_date_key,
            p.status AS procedure_status,
            CASE WHEN p.performed_end IS NULL OR p.performed_start IS NULL THEN NULL
                 ELSE DATEDIFF(MINUTE, p.performed_start, p.performed_end) END AS duration_minutes
        FROM norm.[procedure] AS p
        LEFT JOIN dw.DimPatient AS dp
               ON dp.patient_id = p.patient_id
              AND CAST(COALESCE(p.performed_start, '1900-01-01') AS DATE)
                  BETWEEN dp.effective_from AND dp.effective_to
        LEFT JOIN dw.FactEncounter AS fe ON fe.encounter_id = p.encounter_id
        LEFT JOIN norm.vw_code AS v ON v.code_concept_id = p.code_concept_id
        LEFT JOIN dw.DimProcedure AS dpr
               ON dpr.code_system = v.system_name AND dpr.code = v.code
        LEFT JOIN dw.DimDate AS pd ON pd.full_date = CAST(p.performed_start AS DATE)
    ) AS source
    ON target.procedure_id = source.procedure_id
    WHEN MATCHED THEN UPDATE SET
        patient_key = source.patient_key, encounter_key = source.encounter_key,
        procedure_key = source.procedure_key, performed_date_key = source.performed_date_key,
        procedure_status = source.procedure_status, duration_minutes = source.duration_minutes,
        load_batch_id = @batch_id
    WHEN NOT MATCHED BY TARGET THEN
        INSERT (procedure_id, patient_key, encounter_key, procedure_key,
                performed_date_key, procedure_status, duration_minutes, load_batch_id)
        VALUES (source.procedure_id, source.patient_key, source.encounter_key,
                source.procedure_key, source.performed_date_key, source.procedure_status,
                source.duration_minutes, @batch_id);
END
GO

-- ---------------------------------------------------------------------------
-- The whole layer, in dependency order.
-- ---------------------------------------------------------------------------

CREATE OR ALTER PROCEDURE dw.usp_load_all
    @effective_date DATE = NULL,
    @batch_id BIGINT = NULL
AS
BEGIN
    SET NOCOUNT ON;
    EXEC dw.usp_load_dim_provider;
    EXEC dw.usp_load_dim_organization;
    EXEC dw.usp_load_code_dimensions;
    EXEC dw.usp_load_dim_patient @effective_date = @effective_date;
    EXEC dw.usp_infer_missing_members;
    EXEC dw.usp_load_fact_encounter @batch_id = @batch_id;
    EXEC dw.usp_load_fact_observation @batch_id = @batch_id;
    EXEC dw.usp_load_fact_medication_order @batch_id = @batch_id;
    EXEC dw.usp_load_fact_encounter_diagnosis @batch_id = @batch_id;
    EXEC dw.usp_load_fact_procedure @batch_id = @batch_id;
END
GO
