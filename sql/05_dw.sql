/*
    Dimensional warehouse — the OLAP side. Kimball star schema.

    Conventions used throughout, stated once here rather than re-explained per
    table:

    *  Surrogate keys. Every dimension is keyed by an INT/BIGINT IDENTITY, never
       by the FHIR id. A natural key in a fact table means every attribute
       change rewrites history and every join drags a 64-character string
       through a hash match.

    *  Key -1 is Unknown. Every dimension carries an explicit Unknown member so
       facts can use an inner join and NULL never reaches a report as a blank
       row that quietly disappears from a GROUP BY. `-1` is reserved and the
       IDENTITY seed starts at 1.

    *  `is_inferred` marks a late-arriving member. A fact can reference a
       provider whose Practitioner resource has not been loaded yet — that is
       normal in a feed, not an error — so the loader creates a stub row with
       the business key and is_inferred = 1, and the fact points at a real key
       immediately. When the dimension record arrives, the stub is *updated in
       place* rather than re-inserted, so every fact already pointing at it
       becomes correct without being touched.

    *  Only DimPatient is Type 2. Type 2 everywhere is a common and expensive
       mistake: it is only worth its cost where "what was true at the time"
       changes the answer. A patient moving from one health region to another
       changes which region the encounter counts against, so it is tracked. A
       clinician's name being corrected does not, so DimProvider is Type 1.
*/

USE [$(DatabaseName)];
GO

-- ---------------------------------------------------------------------------
-- DimDate
-- ---------------------------------------------------------------------------

IF OBJECT_ID('dw.DimDate') IS NULL
CREATE TABLE dw.DimDate (
    date_key        INT          NOT NULL,   -- YYYYMMDD, an int you can read
    full_date       DATE         NOT NULL,
    day_of_month    TINYINT      NOT NULL,
    day_of_week     TINYINT      NOT NULL,
    day_name        VARCHAR(10)  NOT NULL,
    day_of_year     SMALLINT     NOT NULL,
    week_of_year    TINYINT      NOT NULL,
    month_number    TINYINT      NOT NULL,
    month_name      VARCHAR(10)  NOT NULL,
    month_year      CHAR(7)      NOT NULL,   -- 2026-08, so reports sort correctly as text
    quarter_number  TINYINT      NOT NULL,
    quarter_name    CHAR(7)      NOT NULL,
    year_number     SMALLINT     NOT NULL,
    is_weekend      BIT          NOT NULL,
    /*  Ontario/BC health-sector fiscal year starts 1 April. Encounter volumes
        get reported to the ministry on that calendar, not on 1 January, and a
        warehouse that cannot express it makes every finance query a manual
        date-shift.                                                          */
    fiscal_year     SMALLINT     NOT NULL,
    fiscal_quarter  TINYINT      NOT NULL,
    CONSTRAINT PK_DimDate PRIMARY KEY CLUSTERED (date_key),
    CONSTRAINT UQ_DimDate_full_date UNIQUE (full_date)
);
GO

/*
    Range: 1900-01-01 to 2035-12-31.

    The brief this schema was built to asked for 2000-2035, and that range does
    not survive contact with the data. `exporter.years_of_history = 2` bounds
    Synthea's *Observations* to two years but not its Encounters or Conditions:
    a patient born in 1942 carries a birth encounter dated 1942, and 52 of the
    371 encounters in a 20-patient extract fall before 2000. Those are real
    clinical events, not corruption, and mapping them to an Unknown date to fit
    a dimension would delete a patient's history to protect a design decision.

    A date dimension in healthcare has to span a human lifetime. The extra
    36,524 rows cost 2 MB.
*/
IF NOT EXISTS (SELECT 1 FROM dw.DimDate)
BEGIN
    /*  A recursive CTE rather than a loop: 49,674 single-row inserts take about
        fifteen seconds, one set-based insert takes about a fifth of one.     */
    WITH dates AS (
        SELECT CAST('1900-01-01' AS DATE) AS d
        UNION ALL
        SELECT DATEADD(DAY, 1, d) FROM dates WHERE d < '2035-12-31'
    )
    INSERT dw.DimDate (date_key, full_date, day_of_month, day_of_week, day_name,
                       day_of_year, week_of_year, month_number, month_name, month_year,
                       quarter_number, quarter_name, year_number, is_weekend,
                       fiscal_year, fiscal_quarter)
    SELECT
        CONVERT(INT, CONVERT(CHAR(8), d, 112)),
        d,
        DAY(d),
        DATEPART(WEEKDAY, d),
        LEFT(DATENAME(WEEKDAY, d), 10),
        DATEPART(DAYOFYEAR, d),
        DATEPART(ISO_WEEK, d),
        MONTH(d),
        LEFT(DATENAME(MONTH, d), 10),
        CONVERT(CHAR(7), d, 126),
        DATEPART(QUARTER, d),
        CONCAT(YEAR(d), '-Q', DATEPART(QUARTER, d)),
        YEAR(d),
        CASE WHEN DATEPART(WEEKDAY, d) IN (1, 7) THEN 1 ELSE 0 END,
        CASE WHEN MONTH(d) >= 4 THEN YEAR(d) ELSE YEAR(d) - 1 END,
        CASE WHEN MONTH(d) >= 4 THEN ((MONTH(d) - 4) / 3) + 1 ELSE ((MONTH(d) + 8) / 3) + 1 END
    FROM dates
    OPTION (MAXRECURSION 0);

    INSERT dw.DimDate (date_key, full_date, day_of_month, day_of_week, day_name,
                       day_of_year, week_of_year, month_number, month_name, month_year,
                       quarter_number, quarter_name, year_number, is_weekend,
                       fiscal_year, fiscal_quarter)
    VALUES (-1, '1899-12-31', 1, 1, 'Unknown', 1, 1, 1, 'Unknown', 'Unknown',
            1, 'Unknown', 1900, 0, 1900, 1);
END
GO

-- ---------------------------------------------------------------------------
-- Type 2 dimension
-- ---------------------------------------------------------------------------

IF OBJECT_ID('dw.DimPatient') IS NULL
CREATE TABLE dw.DimPatient (
    patient_key       BIGINT IDENTITY(1,1) NOT NULL,
    patient_id        VARCHAR(64)   NOT NULL,   -- business key, repeats across versions
    birth_date        DATE          NULL,
    gender            VARCHAR(16)   NULL,
    age_band          VARCHAR(12)   NULL,
    -- The two tracked attributes.
    marital_status    NVARCHAR(100) NULL,
    address_city      NVARCHAR(100) NULL,
    address_state     NVARCHAR(100) NULL,
    address_postal_code NVARCHAR(20) NULL,
    -- Type 2 machinery.
    effective_from    DATE          NOT NULL,
    effective_to      DATE          NOT NULL,
    is_current        BIT           NOT NULL,
    version_number    INT           NOT NULL,
    /*  The change detector. Comparing eight nullable columns pairwise needs
        eight `IS NULL OR <>` clauses and gets one wrong eventually; hashing the
        tracked attributes once turns it into a single comparison. HASHBYTES
        SHA2_256 over a delimited, NULL-marked concatenation — the delimiter
        matters, or ('AB', 'C') and ('A', 'BC') hash the same.                */
    row_hash          BINARY(32)    NOT NULL,
    is_inferred       BIT           NOT NULL CONSTRAINT DF_DimPatient_inferred DEFAULT 0,
    CONSTRAINT PK_DimPatient PRIMARY KEY CLUSTERED (patient_key),
    /*  Two rows for the same patient may not start on the same day. This is the
        constraint that makes an overlapping-range bug impossible to commit
        rather than merely detectable afterwards.                            */
    CONSTRAINT UQ_DimPatient_version UNIQUE (patient_id, effective_from),
    CONSTRAINT CK_DimPatient_effective CHECK (effective_to >= effective_from),
    CONSTRAINT CK_DimPatient_current_open
        CHECK ((is_current = 1 AND effective_to = '9999-12-31')
            OR (is_current = 0 AND effective_to < '9999-12-31'))
);
GO

IF NOT EXISTS (SELECT 1 FROM dw.DimPatient WHERE patient_key = -1)
BEGIN
    SET IDENTITY_INSERT dw.DimPatient ON;
    INSERT dw.DimPatient (patient_key, patient_id, effective_from, effective_to,
                          is_current, version_number, row_hash, is_inferred)
    VALUES (-1, '(unknown)', '1900-01-01', '9999-12-31', 1, 1, 0x00, 0);
    SET IDENTITY_INSERT dw.DimPatient OFF;
END
GO

/*  Only one current row per patient. A filtered unique index rather than a
    check constraint, because the rule is about a *set* of rows and a check
    constraint can only see one. This is the guard that turns the classic SCD2
    failure — two rows both flagged current, every measure doubled — from a
    thing you discover in a report into a thing the insert refuses.          */
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'UQ_DimPatient_one_current')
CREATE UNIQUE INDEX UQ_DimPatient_one_current
    ON dw.DimPatient (patient_id) WHERE is_current = 1;
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_DimPatient_lookup')
CREATE INDEX IX_DimPatient_lookup
    ON dw.DimPatient (patient_id, effective_from, effective_to) INCLUDE (patient_key);
GO

-- ---------------------------------------------------------------------------
-- Type 1 dimensions
-- ---------------------------------------------------------------------------

IF OBJECT_ID('dw.DimProvider') IS NULL
CREATE TABLE dw.DimProvider (
    provider_key    INT IDENTITY(1,1) NOT NULL,
    practitioner_id VARCHAR(64)   NOT NULL,
    npi             NVARCHAR(200) NULL,
    full_name       NVARCHAR(300) NULL,
    gender          VARCHAR(16)   NULL,
    is_inferred     BIT           NOT NULL CONSTRAINT DF_DimProvider_inferred DEFAULT 0,
    CONSTRAINT PK_DimProvider PRIMARY KEY CLUSTERED (provider_key),
    CONSTRAINT UQ_DimProvider_practitioner UNIQUE (practitioner_id)
);
GO
IF NOT EXISTS (SELECT 1 FROM dw.DimProvider WHERE provider_key = -1)
BEGIN
    SET IDENTITY_INSERT dw.DimProvider ON;
    INSERT dw.DimProvider (provider_key, practitioner_id, full_name, is_inferred)
    VALUES (-1, '(unknown)', 'Unknown provider', 0);
    SET IDENTITY_INSERT dw.DimProvider OFF;
END
GO

IF OBJECT_ID('dw.DimOrganization') IS NULL
CREATE TABLE dw.DimOrganization (
    organization_key INT IDENTITY(1,1) NOT NULL,
    organization_id  VARCHAR(64)   NOT NULL,
    organization_name NVARCHAR(300) NULL,
    organization_type NVARCHAR(200) NULL,
    city             NVARCHAR(100) NULL,
    state            NVARCHAR(100) NULL,
    postal_code      NVARCHAR(20)  NULL,
    is_inferred      BIT           NOT NULL CONSTRAINT DF_DimOrganization_inferred DEFAULT 0,
    CONSTRAINT PK_DimOrganization PRIMARY KEY CLUSTERED (organization_key),
    CONSTRAINT UQ_DimOrganization_org UNIQUE (organization_id)
);
GO
IF NOT EXISTS (SELECT 1 FROM dw.DimOrganization WHERE organization_key = -1)
BEGIN
    SET IDENTITY_INSERT dw.DimOrganization ON;
    INSERT dw.DimOrganization (organization_key, organization_id, organization_name, is_inferred)
    VALUES (-1, '(unknown)', 'Unknown organization', 0);
    SET IDENTITY_INSERT dw.DimOrganization OFF;
END
GO

/*  DimDiagnosis, DimProcedure, DimMedication and DimObservationCode are all
    (system, code, display) and could be one conformed DimCode. They are kept
    separate because they are used differently: a diagnosis dimension grows
    ICD-10-CA crosswalk columns and CCI groupings, a medication dimension grows
    ATC class and formulary status, and merging them means every one of those
    columns is NULL for three quarters of the rows.                          */

IF OBJECT_ID('dw.DimDiagnosis') IS NULL
CREATE TABLE dw.DimDiagnosis (
    diagnosis_key   INT IDENTITY(1,1) NOT NULL,
    code_system     NVARCHAR(100) NOT NULL,
    code            NVARCHAR(64)  NOT NULL,
    code_display    NVARCHAR(400) NULL,
    is_inferred     BIT           NOT NULL CONSTRAINT DF_DimDiagnosis_inferred DEFAULT 0,
    CONSTRAINT PK_DimDiagnosis PRIMARY KEY CLUSTERED (diagnosis_key),
    CONSTRAINT UQ_DimDiagnosis_code UNIQUE (code_system, code)
);
GO
IF NOT EXISTS (SELECT 1 FROM dw.DimDiagnosis WHERE diagnosis_key = -1)
BEGIN
    SET IDENTITY_INSERT dw.DimDiagnosis ON;
    INSERT dw.DimDiagnosis (diagnosis_key, code_system, code, code_display)
    VALUES (-1, '(unknown)', '(unknown)', 'Unknown diagnosis');
    SET IDENTITY_INSERT dw.DimDiagnosis OFF;
END
GO

IF OBJECT_ID('dw.DimProcedure') IS NULL
CREATE TABLE dw.DimProcedure (
    procedure_key   INT IDENTITY(1,1) NOT NULL,
    code_system     NVARCHAR(100) NOT NULL,
    code            NVARCHAR(64)  NOT NULL,
    code_display    NVARCHAR(400) NULL,
    is_inferred     BIT           NOT NULL CONSTRAINT DF_DimProcedure_inferred DEFAULT 0,
    CONSTRAINT PK_DimProcedure PRIMARY KEY CLUSTERED (procedure_key),
    CONSTRAINT UQ_DimProcedure_code UNIQUE (code_system, code)
);
GO
IF NOT EXISTS (SELECT 1 FROM dw.DimProcedure WHERE procedure_key = -1)
BEGIN
    SET IDENTITY_INSERT dw.DimProcedure ON;
    INSERT dw.DimProcedure (procedure_key, code_system, code, code_display)
    VALUES (-1, '(unknown)', '(unknown)', 'Unknown procedure');
    SET IDENTITY_INSERT dw.DimProcedure OFF;
END
GO

IF OBJECT_ID('dw.DimObservationCode') IS NULL
CREATE TABLE dw.DimObservationCode (
    observation_code_key INT IDENTITY(1,1) NOT NULL,
    code_system     NVARCHAR(100) NOT NULL,
    code            NVARCHAR(64)  NOT NULL,
    code_display    NVARCHAR(400) NULL,
    is_inferred     BIT           NOT NULL CONSTRAINT DF_DimObservationCode_inferred DEFAULT 0,
    CONSTRAINT PK_DimObservationCode PRIMARY KEY CLUSTERED (observation_code_key),
    CONSTRAINT UQ_DimObservationCode_code UNIQUE (code_system, code)
);
GO
IF NOT EXISTS (SELECT 1 FROM dw.DimObservationCode WHERE observation_code_key = -1)
BEGIN
    SET IDENTITY_INSERT dw.DimObservationCode ON;
    INSERT dw.DimObservationCode (observation_code_key, code_system, code, code_display)
    VALUES (-1, '(unknown)', '(unknown)', 'Unknown observation code');
    SET IDENTITY_INSERT dw.DimObservationCode OFF;
END
GO

IF OBJECT_ID('dw.DimMedication') IS NULL
CREATE TABLE dw.DimMedication (
    medication_key  INT IDENTITY(1,1) NOT NULL,
    code_system     NVARCHAR(100) NOT NULL,
    code            NVARCHAR(64)  NOT NULL,
    code_display    NVARCHAR(400) NULL,
    is_inferred     BIT           NOT NULL CONSTRAINT DF_DimMedication_inferred DEFAULT 0,
    CONSTRAINT PK_DimMedication PRIMARY KEY CLUSTERED (medication_key),
    CONSTRAINT UQ_DimMedication_code UNIQUE (code_system, code)
);
GO
IF NOT EXISTS (SELECT 1 FROM dw.DimMedication WHERE medication_key = -1)
BEGIN
    SET IDENTITY_INSERT dw.DimMedication ON;
    INSERT dw.DimMedication (medication_key, code_system, code, code_display)
    VALUES (-1, '(unknown)', '(unknown)', 'Unknown medication');
    SET IDENTITY_INSERT dw.DimMedication OFF;
END
GO

IF OBJECT_ID('dw.DimEncounterType') IS NULL
CREATE TABLE dw.DimEncounterType (
    encounter_type_key INT IDENTITY(1,1) NOT NULL,
    class_code      VARCHAR(20)   NOT NULL,
    class_display   NVARCHAR(100) NULL,
    type_code       NVARCHAR(64)  NOT NULL,
    type_display    NVARCHAR(400) NULL,
    /*  The grouping every operational report actually asks for. Derived once
        here rather than repeated as a CASE in fourteen queries.             */
    care_setting    VARCHAR(20)   NOT NULL,
    is_inferred     BIT           NOT NULL CONSTRAINT DF_DimEncounterType_inferred DEFAULT 0,
    CONSTRAINT PK_DimEncounterType PRIMARY KEY CLUSTERED (encounter_type_key),
    CONSTRAINT UQ_DimEncounterType UNIQUE (class_code, type_code),
    CONSTRAINT CK_DimEncounterType_setting
        CHECK (care_setting IN ('Inpatient', 'Emergency', 'Ambulatory', 'Virtual',
                                'Home', 'Other', 'Unknown'))
);
GO
IF NOT EXISTS (SELECT 1 FROM dw.DimEncounterType WHERE encounter_type_key = -1)
BEGIN
    SET IDENTITY_INSERT dw.DimEncounterType ON;
    INSERT dw.DimEncounterType (encounter_type_key, class_code, class_display,
                                type_code, type_display, care_setting)
    VALUES (-1, 'UNK', 'Unknown', '(unknown)', 'Unknown encounter type', 'Unknown');
    SET IDENTITY_INSERT dw.DimEncounterType OFF;
END
GO

-- ---------------------------------------------------------------------------
-- Facts
-- ---------------------------------------------------------------------------

/*  Grain: one row per encounter. Stated in a comment because a fact table
    without its grain written down is a fact table someone will add a row to at
    a different grain within a year, and every measure silently doubles.      */
IF OBJECT_ID('dw.FactEncounter') IS NULL
CREATE TABLE dw.FactEncounter (
    encounter_key      BIGINT IDENTITY(1,1) NOT NULL,
    encounter_id       VARCHAR(64) NOT NULL,   -- degenerate dimension
    patient_key        BIGINT      NOT NULL,
    provider_key       INT         NOT NULL,
    organization_key   INT         NOT NULL,
    encounter_type_key INT         NOT NULL,
    start_date_key     INT         NOT NULL,
    end_date_key       INT         NOT NULL,
    encounter_status   VARCHAR(20) NOT NULL,
    -- Measures.
    encounter_count    TINYINT     NOT NULL CONSTRAINT DF_FactEncounter_count DEFAULT 1,
    length_of_stay_days DECIMAL(10,4) NULL,
    length_of_stay_minutes INT      NULL,
    is_inpatient       BIT         NOT NULL,
    is_emergency       BIT         NOT NULL,
    is_readmission_30d BIT         NOT NULL CONSTRAINT DF_FactEncounter_readmit DEFAULT 0,
    days_since_prior_discharge INT NULL,
    load_batch_id      BIGINT      NULL,
    CONSTRAINT PK_FactEncounter PRIMARY KEY CLUSTERED (encounter_key),
    CONSTRAINT UQ_FactEncounter_natural UNIQUE (encounter_id),
    CONSTRAINT FK_FactEncounter_patient FOREIGN KEY (patient_key)
        REFERENCES dw.DimPatient (patient_key),
    CONSTRAINT FK_FactEncounter_provider FOREIGN KEY (provider_key)
        REFERENCES dw.DimProvider (provider_key),
    CONSTRAINT FK_FactEncounter_org FOREIGN KEY (organization_key)
        REFERENCES dw.DimOrganization (organization_key),
    CONSTRAINT FK_FactEncounter_type FOREIGN KEY (encounter_type_key)
        REFERENCES dw.DimEncounterType (encounter_type_key),
    CONSTRAINT FK_FactEncounter_start_date FOREIGN KEY (start_date_key)
        REFERENCES dw.DimDate (date_key),
    CONSTRAINT FK_FactEncounter_end_date FOREIGN KEY (end_date_key)
        REFERENCES dw.DimDate (date_key),
    CONSTRAINT CK_FactEncounter_los CHECK (length_of_stay_days IS NULL OR length_of_stay_days >= 0)
);
GO

/*  Grain: one observation *result*.

    An Observation with two components (a blood pressure) is two results, not
    one, and a panel with twenty-one components is twenty-one. component_seq 0
    is the parent's own value[x]; 1..n are its components. Observations that
    carry neither — the container rows Synthea emits for panels — produce no
    fact row at all, and that difference is the documented variance in the
    raw-to-dw reconciliation rather than an unexplained gap.                 */
IF OBJECT_ID('dw.FactObservation') IS NULL
CREATE TABLE dw.FactObservation (
    observation_fact_key BIGINT IDENTITY(1,1) NOT NULL,
    observation_id     VARCHAR(64) NOT NULL,
    component_seq      SMALLINT    NOT NULL,
    patient_key        BIGINT      NOT NULL,
    encounter_key      BIGINT      NULL,
    observation_code_key INT       NOT NULL,
    effective_date_key INT         NOT NULL,
    observation_status VARCHAR(20) NOT NULL,
    -- Measures.
    result_count       TINYINT     NOT NULL CONSTRAINT DF_FactObservation_count DEFAULT 1,
    value_numeric      DECIMAL(18,6) NULL,
    value_unit         NVARCHAR(40)  NULL,
    value_text         NVARCHAR(400) NULL,
    is_numeric         BIT         NOT NULL,
    load_batch_id      BIGINT      NULL,
    CONSTRAINT PK_FactObservation PRIMARY KEY CLUSTERED (observation_fact_key),
    CONSTRAINT UQ_FactObservation_natural UNIQUE (observation_id, component_seq),
    CONSTRAINT FK_FactObservation_patient FOREIGN KEY (patient_key)
        REFERENCES dw.DimPatient (patient_key),
    CONSTRAINT FK_FactObservation_encounter FOREIGN KEY (encounter_key)
        REFERENCES dw.FactEncounter (encounter_key),
    CONSTRAINT FK_FactObservation_code FOREIGN KEY (observation_code_key)
        REFERENCES dw.DimObservationCode (observation_code_key),
    CONSTRAINT FK_FactObservation_date FOREIGN KEY (effective_date_key)
        REFERENCES dw.DimDate (date_key)
);
GO

/*  Grain: one medication order (one MedicationRequest).                     */
IF OBJECT_ID('dw.FactMedicationOrder') IS NULL
CREATE TABLE dw.FactMedicationOrder (
    medication_order_key BIGINT IDENTITY(1,1) NOT NULL,
    medication_request_id VARCHAR(64) NOT NULL,
    patient_key        BIGINT      NOT NULL,
    encounter_key      BIGINT      NULL,
    provider_key       INT         NOT NULL,
    medication_key     INT         NOT NULL,
    authored_date_key  INT         NOT NULL,
    order_status       VARCHAR(20) NOT NULL,
    order_intent       VARCHAR(20) NOT NULL,
    -- Measures.
    order_count        TINYINT     NOT NULL CONSTRAINT DF_FactMedicationOrder_count DEFAULT 1,
    is_active          BIT         NOT NULL,
    load_batch_id      BIGINT      NULL,
    CONSTRAINT PK_FactMedicationOrder PRIMARY KEY CLUSTERED (medication_order_key),
    CONSTRAINT UQ_FactMedicationOrder_natural UNIQUE (medication_request_id),
    CONSTRAINT FK_FactMedicationOrder_patient FOREIGN KEY (patient_key)
        REFERENCES dw.DimPatient (patient_key),
    CONSTRAINT FK_FactMedicationOrder_encounter FOREIGN KEY (encounter_key)
        REFERENCES dw.FactEncounter (encounter_key),
    CONSTRAINT FK_FactMedicationOrder_provider FOREIGN KEY (provider_key)
        REFERENCES dw.DimProvider (provider_key),
    CONSTRAINT FK_FactMedicationOrder_medication FOREIGN KEY (medication_key)
        REFERENCES dw.DimMedication (medication_key),
    CONSTRAINT FK_FactMedicationOrder_date FOREIGN KEY (authored_date_key)
        REFERENCES dw.DimDate (date_key)
);
GO

/*  Bridge from an encounter to the diagnoses recorded on it. A many-to-many
    that does not belong in FactEncounter: an encounter has zero to many
    diagnoses, and putting diagnosis_key on the encounter fact would either
    change its grain or force a primary-diagnosis-only model that cannot answer
    comorbidity questions.                                                    */
IF OBJECT_ID('dw.FactEncounterDiagnosis') IS NULL
CREATE TABLE dw.FactEncounterDiagnosis (
    encounter_diagnosis_key BIGINT IDENTITY(1,1) NOT NULL,
    condition_id       VARCHAR(64) NOT NULL,
    encounter_key      BIGINT      NULL,
    patient_key        BIGINT      NOT NULL,
    diagnosis_key      INT         NOT NULL,
    onset_date_key     INT         NOT NULL,
    clinical_status    VARCHAR(20) NULL,
    diagnosis_count    TINYINT     NOT NULL CONSTRAINT DF_FactEncDx_count DEFAULT 1,
    load_batch_id      BIGINT      NULL,
    CONSTRAINT PK_FactEncounterDiagnosis PRIMARY KEY CLUSTERED (encounter_diagnosis_key),
    CONSTRAINT UQ_FactEncounterDiagnosis_natural UNIQUE (condition_id),
    CONSTRAINT FK_FactEncDx_encounter FOREIGN KEY (encounter_key)
        REFERENCES dw.FactEncounter (encounter_key),
    CONSTRAINT FK_FactEncDx_patient FOREIGN KEY (patient_key)
        REFERENCES dw.DimPatient (patient_key),
    CONSTRAINT FK_FactEncDx_diagnosis FOREIGN KEY (diagnosis_key)
        REFERENCES dw.DimDiagnosis (diagnosis_key),
    CONSTRAINT FK_FactEncDx_date FOREIGN KEY (onset_date_key)
        REFERENCES dw.DimDate (date_key)
);
GO

/*  Grain: one performed procedure.                                          */
IF OBJECT_ID('dw.FactProcedure') IS NULL
CREATE TABLE dw.FactProcedure (
    procedure_fact_key BIGINT IDENTITY(1,1) NOT NULL,
    procedure_id       VARCHAR(64) NOT NULL,
    patient_key        BIGINT      NOT NULL,
    encounter_key      BIGINT      NULL,
    procedure_key      INT         NOT NULL,
    performed_date_key INT         NOT NULL,
    procedure_status   VARCHAR(20) NOT NULL,
    procedure_count    TINYINT     NOT NULL CONSTRAINT DF_FactProcedure_count DEFAULT 1,
    duration_minutes   INT         NULL,
    load_batch_id      BIGINT      NULL,
    CONSTRAINT PK_FactProcedure PRIMARY KEY CLUSTERED (procedure_fact_key),
    CONSTRAINT UQ_FactProcedure_natural UNIQUE (procedure_id),
    CONSTRAINT FK_FactProcedure_patient FOREIGN KEY (patient_key)
        REFERENCES dw.DimPatient (patient_key),
    CONSTRAINT FK_FactProcedure_encounter FOREIGN KEY (encounter_key)
        REFERENCES dw.FactEncounter (encounter_key),
    CONSTRAINT FK_FactProcedure_procedure FOREIGN KEY (procedure_key)
        REFERENCES dw.DimProcedure (procedure_key),
    CONSTRAINT FK_FactProcedure_date FOREIGN KEY (performed_date_key)
        REFERENCES dw.DimDate (date_key)
);
GO
