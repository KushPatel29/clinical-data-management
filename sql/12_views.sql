/*
    The semantic layer.

    What a Power BI model, a Tableau extract, or an analyst with SQL Server
    Management Studio sits on. Views rather than tables because a materialised
    copy is a second source of truth with a refresh schedule, and views rather
    than direct table access because the star schema's grain and its Unknown
    members are exactly the things a report author gets wrong.

    Two rules, applied throughout:

      *  Every view states its grain in a comment. A view whose grain is not
         written down is one that will be joined to another view at a different
         grain, and the resulting fan-out looks like growth.

      *  Nothing here filters out the Unknown member silently. A report showing
         "Unknown provider: 412" is telling the truth about a data quality
         problem; a view with `WHERE provider_key <> -1` baked in is hiding it
         and making the numbers look better than they are.
*/

USE [$(DatabaseName)];
GO

/*  Grain: one encounter. The workhorse — every dimension flattened onto the
    encounter fact, which is what a report author wants and what stops them
    writing the seven joins themselves and getting one of them wrong.        */
CREATE OR ALTER VIEW dw.vw_encounter
AS
SELECT
    f.encounter_key,
    f.encounter_id,
    -- Patient, as at the time of the encounter.
    p.patient_id,
    p.gender               AS patient_gender,
    p.age_band             AS patient_age_band,
    p.marital_status       AS patient_marital_status,
    p.address_city         AS patient_city,
    p.address_state        AS patient_state,
    p.is_inferred          AS patient_is_inferred,
    -- Provider and site.
    pr.provider_key,
    pr.full_name           AS provider_name,
    pr.npi                 AS provider_npi,
    o.organization_key,
    o.organization_name,
    o.city                 AS organization_city,
    -- Encounter classification.
    et.class_code,
    et.class_display,
    et.type_display        AS encounter_type,
    et.care_setting,
    f.encounter_status,
    -- Dates.
    d.full_date            AS encounter_date,
    d.month_year,
    d.quarter_name,
    d.year_number,
    d.fiscal_year,
    d.fiscal_quarter,
    d.is_weekend,
    -- Measures.
    f.encounter_count,
    f.length_of_stay_days,
    f.length_of_stay_minutes,
    f.is_inpatient,
    f.is_emergency,
    f.is_readmission_30d,
    f.days_since_prior_discharge
FROM dw.FactEncounter        AS f
JOIN dw.DimPatient           AS p  ON p.patient_key = f.patient_key
JOIN dw.DimProvider          AS pr ON pr.provider_key = f.provider_key
JOIN dw.DimOrganization      AS o  ON o.organization_key = f.organization_key
JOIN dw.DimEncounterType     AS et ON et.encounter_type_key = f.encounter_type_key
JOIN dw.DimDate              AS d  ON d.date_key = f.start_date_key;
GO

/*  Grain: one month per care setting. The volume report, pre-aggregated so a
    dashboard tile does not scan the fact table for a number that changes once
    a day.                                                                    */
CREATE OR ALTER VIEW dw.vw_encounter_volume_monthly
AS
SELECT
    d.month_year,
    d.year_number,
    d.fiscal_year,
    d.fiscal_quarter,
    et.care_setting,
    COUNT_BIG(*)                                   AS encounters,
    COUNT(DISTINCT f.patient_key)                  AS distinct_patients,
    SUM(CAST(f.is_readmission_30d AS INT))         AS readmissions_30d,
    AVG(f.length_of_stay_days)                     AS avg_length_of_stay_days
FROM dw.FactEncounter    AS f
JOIN dw.DimDate          AS d  ON d.date_key = f.start_date_key
JOIN dw.DimEncounterType AS et ON et.encounter_type_key = f.encounter_type_key
WHERE f.start_date_key <> -1
GROUP BY d.month_year, d.year_number, d.fiscal_year, d.fiscal_quarter, et.care_setting;
GO

/*  Grain: one observation result — the same grain as FactObservation, which is
    one row per value, counting each component of a blood pressure separately. */
CREATE OR ALTER VIEW dw.vw_observation_result
AS
SELECT
    f.observation_fact_key,
    f.observation_id,
    f.component_seq,
    p.patient_id,
    p.age_band            AS patient_age_band,
    p.gender              AS patient_gender,
    c.code_system,
    c.code,
    c.code_display        AS observation_name,
    d.full_date           AS result_date,
    d.month_year,
    f.observation_status,
    f.value_numeric,
    f.value_unit,
    f.value_text,
    f.is_numeric,
    e.encounter_id,
    e.care_setting
FROM dw.FactObservation     AS f
JOIN dw.DimPatient          AS p ON p.patient_key = f.patient_key
JOIN dw.DimObservationCode  AS c ON c.observation_code_key = f.observation_code_key
JOIN dw.DimDate             AS d ON d.date_key = f.effective_date_key
LEFT JOIN dw.vw_encounter   AS e ON e.encounter_key = f.encounter_key;
GO

/*
    Grain: one LOINC code.

    The abnormal-result rate needs a reference range, and this warehouse does not
    have one — Synthea does not emit Observation.referenceRange, and inventing
    clinical thresholds would be inventing clinical claims. So the "abnormal"
    definition here is explicitly *statistical, not clinical*: a result outside
    the 5th-95th percentile of the results this warehouse holds for that code.

    That is a legitimate data-quality and outlier signal and it is not a medical
    finding. The distinction is written into the column name so a report cannot
    quietly relabel it.
*/
CREATE OR ALTER VIEW dw.vw_observation_reference_band
AS
SELECT
    observation_code_key,
    COUNT_BIG(*)  AS result_count,
    MIN(value_numeric) AS min_value,
    MAX(value_numeric) AS max_value,
    AVG(value_numeric) AS mean_value,
    /*  PERCENTILE_CONT is a window function, so it needs OVER() and produces a
        value per row; the outer aggregation collapses it. MIN() over an
        expression that is constant within the group is the standard idiom.  */
    MIN(p05) AS p05_value,
    MIN(p95) AS p95_value
FROM (
    SELECT
        observation_code_key,
        value_numeric,
        PERCENTILE_CONT(0.05) WITHIN GROUP (ORDER BY value_numeric)
            OVER (PARTITION BY observation_code_key) AS p05,
        PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY value_numeric)
            OVER (PARTITION BY observation_code_key) AS p95
    FROM dw.FactObservation
    WHERE is_numeric = 1
) AS ranked
GROUP BY observation_code_key
HAVING COUNT_BIG(*) >= 30;   -- a percentile from 4 results is noise
GO

/*  Grain: one observation result, with its statistical outlier flag.         */
CREATE OR ALTER VIEW dw.vw_observation_outlier
AS
SELECT
    f.observation_fact_key,
    f.observation_id,
    f.patient_key,
    f.observation_code_key,
    c.code_display AS observation_name,
    f.value_numeric,
    f.value_unit,
    b.p05_value,
    b.p95_value,
    CASE WHEN f.value_numeric < b.p05_value OR f.value_numeric > b.p95_value
         THEN 1 ELSE 0 END AS is_statistical_outlier
FROM dw.FactObservation           AS f
JOIN dw.DimObservationCode        AS c ON c.observation_code_key = f.observation_code_key
JOIN dw.vw_observation_reference_band AS b ON b.observation_code_key = f.observation_code_key
WHERE f.is_numeric = 1;
GO

/*  Grain: one medication order.                                              */
CREATE OR ALTER VIEW dw.vw_medication_order
AS
SELECT
    f.medication_order_key,
    f.medication_request_id,
    p.patient_id,
    p.age_band AS patient_age_band,
    m.code_system,
    m.code,
    m.code_display AS medication_name,
    pr.full_name   AS prescriber_name,
    d.full_date    AS authored_date,
    d.month_year,
    f.order_status,
    f.order_intent,
    f.is_active,
    f.order_count,
    e.encounter_id,
    e.care_setting
FROM dw.FactMedicationOrder AS f
JOIN dw.DimPatient          AS p  ON p.patient_key = f.patient_key
JOIN dw.DimMedication       AS m  ON m.medication_key = f.medication_key
JOIN dw.DimProvider         AS pr ON pr.provider_key = f.provider_key
JOIN dw.DimDate             AS d  ON d.date_key = f.authored_date_key
LEFT JOIN dw.vw_encounter   AS e  ON e.encounter_key = f.encounter_key;
GO

/*  Grain: one recorded diagnosis.                                            */
CREATE OR ALTER VIEW dw.vw_diagnosis
AS
SELECT
    f.encounter_diagnosis_key,
    f.condition_id,
    p.patient_id,
    p.age_band AS patient_age_band,
    p.gender   AS patient_gender,
    dx.code_system,
    dx.code,
    dx.code_display AS diagnosis_name,
    d.full_date     AS onset_date,
    d.month_year,
    d.year_number,
    f.clinical_status,
    f.diagnosis_count,
    e.encounter_id,
    e.care_setting,
    e.organization_name
FROM dw.FactEncounterDiagnosis AS f
JOIN dw.DimPatient             AS p  ON p.patient_key = f.patient_key
JOIN dw.DimDiagnosis           AS dx ON dx.diagnosis_key = f.diagnosis_key
JOIN dw.DimDate                AS d  ON d.date_key = f.onset_date_key
LEFT JOIN dw.vw_encounter      AS e  ON e.encounter_key = f.encounter_key;
GO

/*
    Grain: one patient (current version only).

    The active panel. "Active" needs a definition and this one is: seen at least
    once in the 24 months before the warehouse's most recent encounter. Anchored
    to the data's own maximum date rather than to GETDATE(), because a warehouse
    loaded from a two-year extract has no encounters in the last month and a
    panel measured against today's date would report zero.
*/
CREATE OR ALTER VIEW dw.vw_patient_panel
AS
WITH anchor AS (
    SELECT MAX(d.full_date) AS as_of
    FROM dw.FactEncounter AS f
    JOIN dw.DimDate AS d ON d.date_key = f.start_date_key
    WHERE f.start_date_key <> -1
)
SELECT
    p.patient_key,
    p.patient_id,
    p.gender,
    p.age_band,
    p.marital_status,
    p.address_city,
    p.address_state,
    a.as_of,
    activity.last_encounter_date,
    activity.encounters_24m,
    CASE WHEN activity.encounters_24m > 0 THEN 1 ELSE 0 END AS is_active,
    p.is_inferred
FROM dw.DimPatient AS p
CROSS JOIN anchor  AS a
OUTER APPLY (
    SELECT MAX(d.full_date) AS last_encounter_date,
           COUNT_BIG(*)     AS encounters_24m
    FROM dw.FactEncounter AS f
    JOIN dw.DimDate AS d ON d.date_key = f.start_date_key
    WHERE f.patient_key = p.patient_key
      AND d.full_date > DATEADD(MONTH, -24, a.as_of)
) AS activity
WHERE p.is_current = 1
  AND p.patient_key <> -1;
GO

/*
    Grain: one month.

    30-day readmission rate, as a rate rather than as two counts a report author
    has to divide. The denominator is inpatient discharges, not all encounters —
    the most common way this measure is quoted wrong.
*/
CREATE OR ALTER VIEW dw.vw_readmission_rate_monthly
AS
SELECT
    d.month_year,
    d.fiscal_year,
    COUNT_BIG(*)                                AS inpatient_encounters,
    SUM(CAST(f.is_readmission_30d AS INT))      AS readmissions_30d,
    CAST(SUM(CAST(f.is_readmission_30d AS INT)) AS DECIMAL(18,6))
        / NULLIF(COUNT_BIG(*), 0)               AS readmission_rate
FROM dw.FactEncounter AS f
JOIN dw.DimDate       AS d ON d.date_key = f.start_date_key
WHERE f.is_inpatient = 1
  AND f.start_date_key <> -1
GROUP BY d.month_year, d.fiscal_year;
GO

/*
    Grain: one organisation.

    The operational scorecard a site director opens.
*/
CREATE OR ALTER VIEW dw.vw_organization_scorecard
AS
SELECT
    o.organization_key,
    o.organization_name,
    o.city,
    o.state,
    COUNT_BIG(*)                              AS encounters,
    COUNT(DISTINCT f.patient_key)             AS distinct_patients,
    COUNT(DISTINCT f.provider_key)            AS distinct_providers,
    SUM(CAST(f.is_inpatient AS INT))          AS inpatient_encounters,
    SUM(CAST(f.is_emergency AS INT))          AS emergency_encounters,
    SUM(CAST(f.is_readmission_30d AS INT))    AS readmissions_30d,
    AVG(f.length_of_stay_days)                AS avg_length_of_stay_days
FROM dw.FactEncounter   AS f
JOIN dw.DimOrganization AS o ON o.organization_key = f.organization_key
GROUP BY o.organization_key, o.organization_name, o.city, o.state;
GO

/*
    Grain: one criterion, plus a final row for the intersection.

    The bridge between the two halves of this repository, and the reason a
    sponsor asks a hospital for a FHIR extract at all: before a trial opens at a
    site, somebody has to answer "how many of your patients could even be
    eligible". That question is answered against the health system's warehouse,
    not against the EDC — the EDC has no patients in it yet.

    Scope, stated so this is not read as more than it is: the criteria below are
    written here for the demonstration. `crf/study_metadata.py` defines forms,
    items, codelists and a visit schedule; it does not carry eligibility
    criteria, and inventing a protocol section to reference would be inventing
    a protocol. These are the shape of a feasibility screen, with real SNOMED
    codes from the extract, not study SYN-2026-01's actual inclusion criteria.

    It is also a *count*, deliberately. A screening list naming patients is a
    different artefact with a different approval attached to it.
*/
CREATE OR ALTER VIEW dw.vw_trial_feasibility
AS
WITH panel AS (
    SELECT * FROM dw.vw_patient_panel WHERE is_active = 1
),
adults AS (
    SELECT patient_key FROM panel WHERE age_band IN ('18-44', '45-64')
),
with_diagnosis AS (
    SELECT DISTINCT f.patient_key
    FROM dw.FactEncounterDiagnosis AS f
    JOIN dw.DimDiagnosis AS dx ON dx.diagnosis_key = f.diagnosis_key
    WHERE dx.code_display LIKE '%diabetes%'
       OR dx.code_display LIKE '%hypertension%'
),
recent_labs AS (
    SELECT DISTINCT patient_key FROM dw.FactObservation WHERE is_numeric = 1
),
eligible AS (
    SELECT a.patient_key
    FROM adults AS a
    JOIN with_diagnosis AS d ON d.patient_key = a.patient_key
    JOIN recent_labs    AS l ON l.patient_key = a.patient_key
)
SELECT 1 AS criterion_order, 'Active patient panel' AS criterion,
       COUNT_BIG(*) AS patients FROM panel
UNION ALL
SELECT 2, 'Aged 18-64', COUNT_BIG(*) FROM adults
UNION ALL
SELECT 3, 'With a qualifying diagnosis', COUNT_BIG(*) FROM with_diagnosis
UNION ALL
SELECT 4, 'With at least one numeric lab result', COUNT_BIG(*) FROM recent_labs
UNION ALL
SELECT 5, 'Meeting all criteria', COUNT_BIG(*) FROM eligible;
GO

/*
    Grain: one layer.

    Row-count reconciliation across the pipeline, as a view so it can be read
    from a dashboard as easily as from the data-quality suite. Every documented
    variance between two layers has a reason next to it in
    docs/architecture.md — an unexplained difference between raw and dw is the
    single most common way a warehouse loses rows without anyone noticing.
*/
CREATE OR ALTER VIEW dw.vw_pipeline_reconciliation
AS
SELECT 'Patient' AS resource_type,
       (SELECT COUNT_BIG(*) FROM raw.vw_current_resource WHERE resource_type = 'Patient') AS raw_rows,
       (SELECT COUNT_BIG(*) FROM norm.patient) AS norm_rows,
       (SELECT COUNT_BIG(*) FROM dw.DimPatient WHERE is_current = 1 AND patient_key <> -1) AS dw_rows
UNION ALL
SELECT 'Encounter',
       (SELECT COUNT_BIG(*) FROM raw.vw_current_resource WHERE resource_type = 'Encounter'),
       (SELECT COUNT_BIG(*) FROM norm.encounter),
       (SELECT COUNT_BIG(*) FROM dw.FactEncounter)
UNION ALL
SELECT 'Condition',
       (SELECT COUNT_BIG(*) FROM raw.vw_current_resource WHERE resource_type = 'Condition'),
       (SELECT COUNT_BIG(*) FROM norm.condition),
       (SELECT COUNT_BIG(*) FROM dw.FactEncounterDiagnosis)
UNION ALL
SELECT 'Procedure',
       (SELECT COUNT_BIG(*) FROM raw.vw_current_resource WHERE resource_type = 'Procedure'),
       (SELECT COUNT_BIG(*) FROM norm.[procedure]),
       (SELECT COUNT_BIG(*) FROM dw.FactProcedure)
UNION ALL
SELECT 'MedicationRequest',
       (SELECT COUNT_BIG(*) FROM raw.vw_current_resource WHERE resource_type = 'MedicationRequest'),
       (SELECT COUNT_BIG(*) FROM norm.medication_request),
       (SELECT COUNT_BIG(*) FROM dw.FactMedicationOrder)
UNION ALL
/*  Observation is the one row where dw exceeds norm, and it is by design: the
    fact grain is one *result*, so a blood pressure with two components becomes
    two rows and a 21-item panel becomes 21. Observations carrying no value at
    all produce none. Both differences are asserted numerically by the
    reconciliation check in tests/dq.                                         */
SELECT 'Observation',
       (SELECT COUNT_BIG(*) FROM raw.vw_current_resource WHERE resource_type = 'Observation'),
       (SELECT COUNT_BIG(*) FROM norm.observation),
       (SELECT COUNT_BIG(*) FROM dw.FactObservation);
GO
