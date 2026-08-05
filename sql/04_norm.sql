/*
    Normalised transactional model — the OLTP side.

    Third normal form, real keys, and constraints the engine enforces. This
    layer exists because the dimensional model is a *reporting* shape: it
    denormalises deliberately, it carries surrogate keys, and it cannot tell you
    whether an encounter referenced a patient who does not exist. Something has
    to, and it has to be something that refuses the row rather than reporting on
    it. See docs/architecture.md for why this is worth two layers.

    NOT NULL is applied where FHIR R4 says cardinality is 1..1, and nowhere
    else. `Observation.status` and `Observation.code` are required by the spec,
    so they are NOT NULL here; `Observation.effective[x]` is 0..1, so it is
    nullable even though almost every row has one. A column made NOT NULL
    because the current extract happens to be complete is a column that will
    reject a valid resource later.

    Reference resolution, and the thing that is easy to get wrong:
    Synthea — like plenty of real integrations — writes **conditional
    references** for providers and organisations:

        "reference": "Practitioner?identifier=http://hl7.org/fhir/sid/us-npi|9999979393"

    That is not `Practitioner/<id>`. The NPI in that string is not the
    Practitioner's resource id and never matches it. A resolver that assumes
    `Type/id` produces NULL for every provider on every encounter, the load
    succeeds, and the star schema quietly reports that no clinician saw anyone.
    Hence norm.practitioner_identifier and norm.organization_identifier: the
    identifier is a first-class row, and references resolve through it.
*/

USE [$(DatabaseName)];
GO

-- ---------------------------------------------------------------------------
-- Actors
-- ---------------------------------------------------------------------------

IF OBJECT_ID('norm.organization') IS NULL
CREATE TABLE norm.organization (
    organization_id   VARCHAR(64)   NOT NULL,
    name              NVARCHAR(300) NOT NULL,
    type_code_concept_id INT        NULL,
    address_line      NVARCHAR(200) NULL,
    city              NVARCHAR(100) NULL,
    state             NVARCHAR(100) NULL,
    postal_code       NVARCHAR(20)  NULL,
    country           NVARCHAR(100) NULL,
    is_active         BIT           NULL,
    CONSTRAINT PK_organization PRIMARY KEY CLUSTERED (organization_id),
    CONSTRAINT FK_organization_type FOREIGN KEY (type_code_concept_id)
        REFERENCES norm.code_concept (code_concept_id)
);
GO

IF OBJECT_ID('norm.practitioner') IS NULL
CREATE TABLE norm.practitioner (
    practitioner_id   VARCHAR(64)   NOT NULL,
    family_name       NVARCHAR(200) NULL,
    given_name        NVARCHAR(200) NULL,
    name_prefix       NVARCHAR(40)  NULL,
    gender            VARCHAR(16)   NULL,
    is_active         BIT           NULL,
    CONSTRAINT PK_practitioner PRIMARY KEY CLUSTERED (practitioner_id),
    CONSTRAINT CK_practitioner_gender
        CHECK (gender IS NULL OR gender IN ('male', 'female', 'other', 'unknown'))
);
GO

/*  The tables that make conditional references resolvable. PK is (system,
    value) rather than (owner, system, value): an NPI identifies one clinician,
    and if two practitioner rows claim the same NPI that is a data quality
    incident the primary key should surface, not a composite key should hide. */
IF OBJECT_ID('norm.practitioner_identifier') IS NULL
CREATE TABLE norm.practitioner_identifier (
    identifier_system NVARCHAR(200) NOT NULL,
    identifier_value  NVARCHAR(200) NOT NULL,
    practitioner_id   VARCHAR(64)   NOT NULL,
    CONSTRAINT PK_practitioner_identifier PRIMARY KEY CLUSTERED (identifier_system, identifier_value),
    CONSTRAINT FK_practitioner_identifier_practitioner FOREIGN KEY (practitioner_id)
        REFERENCES norm.practitioner (practitioner_id)
);
GO

IF OBJECT_ID('norm.organization_identifier') IS NULL
CREATE TABLE norm.organization_identifier (
    identifier_system NVARCHAR(200) NOT NULL,
    identifier_value  NVARCHAR(200) NOT NULL,
    organization_id   VARCHAR(64)   NOT NULL,
    CONSTRAINT PK_organization_identifier PRIMARY KEY CLUSTERED (identifier_system, identifier_value),
    CONSTRAINT FK_organization_identifier_organization FOREIGN KEY (organization_id)
        REFERENCES norm.organization (organization_id)
);
GO

-- ---------------------------------------------------------------------------
-- Patient
-- ---------------------------------------------------------------------------

IF OBJECT_ID('norm.patient') IS NULL
CREATE TABLE norm.patient (
    patient_id            VARCHAR(64)   NOT NULL,
    family_name           NVARCHAR(200) NULL,
    given_name            NVARCHAR(200) NULL,
    birth_date            DATE          NULL,
    gender                VARCHAR(16)   NULL,
    marital_status_code   VARCHAR(16)   NULL,
    marital_status_display NVARCHAR(100) NULL,
    deceased_datetime     DATETIME2(3)  NULL,
    source_version_id     VARCHAR(32)   NOT NULL,
    source_last_updated   DATETIME2(3)  NULL,
    CONSTRAINT PK_patient PRIMARY KEY CLUSTERED (patient_id),
    /*  Patient has no 1..1 elements in R4 — every element is optional, which
        surprises people who expect a birth date to be mandatory. So no NOT NULL
        here beyond the key, and the value set is still checked.               */
    CONSTRAINT CK_patient_gender
        CHECK (gender IS NULL OR gender IN ('male', 'female', 'other', 'unknown')),
    CONSTRAINT CK_patient_birth_before_death
        CHECK (deceased_datetime IS NULL OR birth_date IS NULL OR deceased_datetime >= birth_date)
);
GO

/*  Address is 0..* in FHIR and it is the attribute the warehouse tracks over
    time, so it cannot live as three columns on norm.patient.                 */
IF OBJECT_ID('norm.patient_address') IS NULL
CREATE TABLE norm.patient_address (
    patient_id    VARCHAR(64)   NOT NULL,
    address_seq   SMALLINT      NOT NULL,
    address_use   VARCHAR(20)   NULL,
    address_line  NVARCHAR(200) NULL,
    city          NVARCHAR(100) NULL,
    state         NVARCHAR(100) NULL,
    postal_code   NVARCHAR(20)  NULL,
    country       NVARCHAR(100) NULL,
    CONSTRAINT PK_patient_address PRIMARY KEY CLUSTERED (patient_id, address_seq),
    CONSTRAINT FK_patient_address_patient FOREIGN KEY (patient_id)
        REFERENCES norm.patient (patient_id) ON DELETE CASCADE
);
GO

-- ---------------------------------------------------------------------------
-- Clinical events
-- ---------------------------------------------------------------------------

IF OBJECT_ID('norm.encounter') IS NULL
CREATE TABLE norm.encounter (
    encounter_id          VARCHAR(64)   NOT NULL,
    patient_id            VARCHAR(64)   NOT NULL,   -- Encounter.subject, 0..1 in R4 but
                                                    -- 1..1 in US Core and in every row here
    status                VARCHAR(20)   NOT NULL,   -- 1..1
    class_code            VARCHAR(20)   NOT NULL,   -- 1..1
    class_display         NVARCHAR(100) NULL,
    type_code_concept_id  INT           NULL,
    period_start          DATETIME2(3)  NULL,
    period_end            DATETIME2(3)  NULL,
    service_provider_id   VARCHAR(64)   NULL,
    primary_performer_id  VARCHAR(64)   NULL,
    CONSTRAINT PK_encounter PRIMARY KEY CLUSTERED (encounter_id),
    CONSTRAINT FK_encounter_patient FOREIGN KEY (patient_id)
        REFERENCES norm.patient (patient_id),
    CONSTRAINT FK_encounter_org FOREIGN KEY (service_provider_id)
        REFERENCES norm.organization (organization_id),
    CONSTRAINT FK_encounter_practitioner FOREIGN KEY (primary_performer_id)
        REFERENCES norm.practitioner (practitioner_id),
    CONSTRAINT FK_encounter_type FOREIGN KEY (type_code_concept_id)
        REFERENCES norm.code_concept (code_concept_id),
    CONSTRAINT CK_encounter_status CHECK (status IN (
        'planned', 'arrived', 'triaged', 'in-progress', 'onleave',
        'finished', 'cancelled', 'entered-in-error', 'unknown')),
    CONSTRAINT CK_encounter_class CHECK (class_code IN (
        'AMB', 'EMER', 'FLD', 'HH', 'IMP', 'ACUTE', 'NONAC', 'OBSENC',
        'PRENC', 'SS', 'VR')),
    /*  An encounter that ends before it starts is not a late arrival, it is a
        corrupt row, and a length-of-stay measure built on it goes negative.  */
    CONSTRAINT CK_encounter_period CHECK (period_end IS NULL OR period_start IS NULL
                                          OR period_end >= period_start)
);
GO

IF OBJECT_ID('norm.condition') IS NULL
CREATE TABLE norm.condition (
    condition_id          VARCHAR(64)   NOT NULL,
    patient_id            VARCHAR(64)   NOT NULL,   -- Condition.subject 1..1
    encounter_id          VARCHAR(64)   NULL,
    code_concept_id       INT           NOT NULL,
    clinical_status       VARCHAR(20)   NULL,
    verification_status   VARCHAR(20)   NULL,
    onset_datetime        DATETIME2(3)  NULL,
    abatement_datetime    DATETIME2(3)  NULL,
    recorded_date         DATETIME2(3)  NULL,
    CONSTRAINT PK_condition PRIMARY KEY CLUSTERED (condition_id),
    CONSTRAINT FK_condition_patient FOREIGN KEY (patient_id)
        REFERENCES norm.patient (patient_id),
    CONSTRAINT FK_condition_encounter FOREIGN KEY (encounter_id)
        REFERENCES norm.encounter (encounter_id),
    CONSTRAINT FK_condition_code FOREIGN KEY (code_concept_id)
        REFERENCES norm.code_concept (code_concept_id),
    CONSTRAINT CK_condition_clinical_status CHECK (clinical_status IS NULL OR clinical_status IN (
        'active', 'recurrence', 'relapse', 'inactive', 'remission', 'resolved')),
    CONSTRAINT CK_condition_verification_status CHECK (verification_status IS NULL OR verification_status IN (
        'unconfirmed', 'provisional', 'differential', 'confirmed', 'refuted', 'entered-in-error')),
    CONSTRAINT CK_condition_abatement CHECK (abatement_datetime IS NULL OR onset_datetime IS NULL
                                             OR abatement_datetime >= onset_datetime)
);
GO

IF OBJECT_ID('norm.observation') IS NULL
CREATE TABLE norm.observation (
    observation_id        VARCHAR(64)   NOT NULL,
    patient_id            VARCHAR(64)   NOT NULL,
    encounter_id          VARCHAR(64)   NULL,
    code_concept_id       INT           NOT NULL,   -- Observation.code 1..1
    category_code         VARCHAR(40)   NULL,
    status                VARCHAR(20)   NOT NULL,   -- Observation.status 1..1
    effective_datetime    DATETIME2(3)  NULL,
    issued                DATETIME2(3)  NULL,
    /*  value[x] is a choice of eleven types. Three appear in this extract and
        each gets its own typed column; a single NVARCHAR holding "128" and
        "Never smoker" and "2021-03-04" would make every numeric aggregate a
        TRY_CONVERT and every one of them a silent NULL on bad input.        */
    value_quantity        DECIMAL(18,6) NULL,
    value_unit            NVARCHAR(40)  NULL,
    value_code_concept_id INT           NULL,
    value_string          NVARCHAR(400) NULL,
    has_components        BIT           NOT NULL CONSTRAINT DF_observation_components DEFAULT 0,
    CONSTRAINT PK_observation PRIMARY KEY CLUSTERED (observation_id),
    CONSTRAINT FK_observation_patient FOREIGN KEY (patient_id)
        REFERENCES norm.patient (patient_id),
    CONSTRAINT FK_observation_encounter FOREIGN KEY (encounter_id)
        REFERENCES norm.encounter (encounter_id),
    CONSTRAINT FK_observation_code FOREIGN KEY (code_concept_id)
        REFERENCES norm.code_concept (code_concept_id),
    CONSTRAINT FK_observation_value_code FOREIGN KEY (value_code_concept_id)
        REFERENCES norm.code_concept (code_concept_id),
    CONSTRAINT CK_observation_status CHECK (status IN (
        'registered', 'preliminary', 'final', 'amended', 'corrected',
        'cancelled', 'entered-in-error', 'unknown')),
    /*  A quantity without its unit is a number with no meaning — 5 mg and
        5 g differ by a factor that kills people. Enforced, not assumed.     */
    CONSTRAINT CK_observation_quantity_has_unit
        CHECK (value_quantity IS NULL OR value_unit IS NOT NULL)
);
GO

/*  Blood pressure is one Observation with two components, and a panel is one
    with twenty-one. Flattening components onto the parent would need a column
    per component position; this is the 3NF answer.                          */
IF OBJECT_ID('norm.observation_component') IS NULL
CREATE TABLE norm.observation_component (
    observation_id        VARCHAR(64)   NOT NULL,
    component_seq         SMALLINT      NOT NULL,
    code_concept_id       INT           NOT NULL,
    value_quantity        DECIMAL(18,6) NULL,
    value_unit            NVARCHAR(40)  NULL,
    value_code_concept_id INT           NULL,
    CONSTRAINT PK_observation_component PRIMARY KEY CLUSTERED (observation_id, component_seq),
    CONSTRAINT FK_observation_component_parent FOREIGN KEY (observation_id)
        REFERENCES norm.observation (observation_id) ON DELETE CASCADE,
    CONSTRAINT FK_observation_component_code FOREIGN KEY (code_concept_id)
        REFERENCES norm.code_concept (code_concept_id),
    CONSTRAINT FK_observation_component_value_code FOREIGN KEY (value_code_concept_id)
        REFERENCES norm.code_concept (code_concept_id)
);
GO

/*  `procedure` is a reserved word in T-SQL, so the table is bracketed
    everywhere it appears. Renaming it to dodge the brackets would be renaming a
    FHIR resource type inside a FHIR warehouse, which costs more in confusion
    than the brackets cost in noise.                                          */
IF OBJECT_ID('norm.procedure') IS NULL
CREATE TABLE norm.[procedure] (
    procedure_id          VARCHAR(64)   NOT NULL,
    patient_id            VARCHAR(64)   NOT NULL,   -- Procedure.subject 1..1
    encounter_id          VARCHAR(64)   NULL,
    code_concept_id       INT           NOT NULL,
    status                VARCHAR(20)   NOT NULL,   -- Procedure.status 1..1
    performed_start       DATETIME2(3)  NULL,
    performed_end         DATETIME2(3)  NULL,
    CONSTRAINT PK_procedure PRIMARY KEY CLUSTERED (procedure_id),
    CONSTRAINT FK_procedure_patient FOREIGN KEY (patient_id)
        REFERENCES norm.patient (patient_id),
    CONSTRAINT FK_procedure_encounter FOREIGN KEY (encounter_id)
        REFERENCES norm.encounter (encounter_id),
    CONSTRAINT FK_procedure_code FOREIGN KEY (code_concept_id)
        REFERENCES norm.code_concept (code_concept_id),
    CONSTRAINT CK_procedure_status CHECK (status IN (
        'preparation', 'in-progress', 'not-done', 'on-hold', 'stopped',
        'completed', 'entered-in-error', 'unknown')),
    CONSTRAINT CK_procedure_period CHECK (performed_end IS NULL OR performed_start IS NULL
                                          OR performed_end >= performed_start)
);
GO

IF OBJECT_ID('norm.medication_request') IS NULL
CREATE TABLE norm.medication_request (
    medication_request_id VARCHAR(64)   NOT NULL,
    patient_id            VARCHAR(64)   NOT NULL,   -- 1..1
    encounter_id          VARCHAR(64)   NULL,
    status                VARCHAR(20)   NOT NULL,   -- 1..1
    intent                VARCHAR(20)   NOT NULL,   -- 1..1
    /*  medication[x] is a *choice* with cardinality 1..1: exactly one of
        medicationCodeableConcept or medicationReference, never both, never
        neither. 16% of the rows in this extract take the reference branch.
        The check constraint below is that FHIR rule, written down. Modelling
        only the CodeableConcept branch — the obvious thing to do, since it is
        the common case — drops those rows or nulls their drug silently.     */
    code_concept_id       INT           NULL,
    medication_reference_id VARCHAR(64) NULL,
    authored_on           DATETIME2(3)  NULL,
    requester_id          VARCHAR(64)   NULL,
    CONSTRAINT PK_medication_request PRIMARY KEY CLUSTERED (medication_request_id),
    CONSTRAINT FK_medication_request_patient FOREIGN KEY (patient_id)
        REFERENCES norm.patient (patient_id),
    CONSTRAINT FK_medication_request_encounter FOREIGN KEY (encounter_id)
        REFERENCES norm.encounter (encounter_id),
    CONSTRAINT FK_medication_request_code FOREIGN KEY (code_concept_id)
        REFERENCES norm.code_concept (code_concept_id),
    CONSTRAINT FK_medication_request_requester FOREIGN KEY (requester_id)
        REFERENCES norm.practitioner (practitioner_id),
    CONSTRAINT CK_medication_request_status CHECK (status IN (
        'active', 'on-hold', 'cancelled', 'completed', 'entered-in-error',
        'stopped', 'draft', 'unknown')),
    CONSTRAINT CK_medication_request_intent CHECK (intent IN (
        'proposal', 'plan', 'order', 'original-order', 'reflex-order',
        'filler-order', 'instance-order', 'option')),
    CONSTRAINT CK_medication_request_choice CHECK (
        (CASE WHEN code_concept_id IS NULL THEN 0 ELSE 1 END) +
        (CASE WHEN medication_reference_id IS NULL THEN 0 ELSE 1 END) = 1)
);
GO

/*
    Every coding on every clinical resource, not just the primary one.

    Observation.code.coding is 0..*; in this extract 0.7% of observations carry
    both a LOINC and a SNOMED coding for the same concept. The clinical tables
    above hold one code_concept_id because a star schema needs one key, and the
    rule for picking it is written down in usp_load_norm_observation. This table
    is where the codings that were not picked go, so "coded in both LOINC and
    SNOMED" stays an answerable question instead of becoming a lost fact.
*/
IF OBJECT_ID('norm.resource_coding') IS NULL
CREATE TABLE norm.resource_coding (
    resource_type   VARCHAR(32) NOT NULL,
    resource_id     VARCHAR(64) NOT NULL,
    coding_seq      SMALLINT    NOT NULL,
    code_concept_id INT         NOT NULL,
    is_primary      BIT         NOT NULL,
    CONSTRAINT PK_resource_coding PRIMARY KEY CLUSTERED (resource_type, resource_id, coding_seq),
    CONSTRAINT FK_resource_coding_code FOREIGN KEY (code_concept_id)
        REFERENCES norm.code_concept (code_concept_id)
);
GO

-- ---------------------------------------------------------------------------
-- Foreign key indexes.
--
-- SQL Server indexes the primary key and nothing else. An unindexed foreign key
-- is a table scan on every join and a lock escalation risk on every parent
-- delete, and these are the joins the whole warehouse is built out of.
-- ---------------------------------------------------------------------------

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_encounter_patient')
CREATE INDEX IX_encounter_patient ON norm.encounter (patient_id) INCLUDE (period_start, class_code);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_encounter_org')
CREATE INDEX IX_encounter_org ON norm.encounter (service_provider_id);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_encounter_performer')
CREATE INDEX IX_encounter_performer ON norm.encounter (primary_performer_id);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_condition_patient')
CREATE INDEX IX_condition_patient ON norm.condition (patient_id) INCLUDE (code_concept_id, onset_datetime);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_condition_encounter')
CREATE INDEX IX_condition_encounter ON norm.condition (encounter_id);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_observation_patient')
CREATE INDEX IX_observation_patient ON norm.observation (patient_id) INCLUDE (code_concept_id, effective_datetime);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_observation_encounter')
CREATE INDEX IX_observation_encounter ON norm.observation (encounter_id);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_procedure_patient')
CREATE INDEX IX_procedure_patient ON norm.[procedure] (patient_id);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_procedure_encounter')
CREATE INDEX IX_procedure_encounter ON norm.[procedure] (encounter_id);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_medication_request_patient')
CREATE INDEX IX_medication_request_patient ON norm.medication_request (patient_id);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_medication_request_encounter')
CREATE INDEX IX_medication_request_encounter ON norm.medication_request (encounter_id);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_medication_request_requester')
CREATE INDEX IX_medication_request_requester ON norm.medication_request (requester_id);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_resource_coding_concept')
CREATE INDEX IX_resource_coding_concept ON norm.resource_coding (code_concept_id) INCLUDE (resource_type, resource_id);
GO
