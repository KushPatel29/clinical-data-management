"""
The data map, as data.

Every column in the warehouse traced back to the FHIR element it came from.
This is the artefact the job posting calls a "data map", and the reason it is a
Python structure rather than a Markdown table is that a Markdown table cannot be
checked. `tests/test_docs.py` asserts that every `norm` and `dw` column named
here exists in the database, and that every column in the database is named
here — so a column added without a lineage entry fails the build, and a lineage
entry for a column that was renamed fails it too.

A data map that is not verified is a data map that was accurate once.

`fhir_path` is FHIRPath-ish: the element path within the resource, using `[0]`
where this warehouse takes the first of a repeating element and `[*]` where it
takes all of them. `note` carries the decision when the mapping is not a
straight copy — those notes are the actual content of the document.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Mapping:
    resource: str          # FHIR resource type, or "" for derived columns
    fhir_path: str         # element path within that resource
    norm_column: str       # schema.table.column
    dw_column: str = ""    # schema.table.column, or "" if it stops at norm
    note: str = ""


# ---------------------------------------------------------------------------
# Patient
# ---------------------------------------------------------------------------

PATIENT = [
    Mapping("Patient", "id", "norm.patient.patient_id", "dw.DimPatient.patient_id",
            "Business key. dw.DimPatient is keyed by a surrogate; this is the natural key "
            "that repeats across Type 2 versions."),
    Mapping("Patient", "name[0].family", "norm.patient.family_name", "",
            "Deliberately not carried into dw. A name is not an analytical attribute and "
            "putting it in a dimension puts it in every extract of that dimension."),
    Mapping("Patient", "name[0].given[0]", "norm.patient.given_name", ""),
    Mapping("Patient", "birthDate", "norm.patient.birth_date", "dw.DimPatient.birth_date"),
    Mapping("Patient", "gender", "norm.patient.gender", "dw.DimPatient.gender",
            "Required value set: male | female | other | unknown."),
    Mapping("Patient", "maritalStatus.coding[0].code",
            "norm.patient.marital_status_code", "",
            "The code is kept in norm; the dimension carries the display text because that "
            "is what a report renders."),
    Mapping("Patient", "maritalStatus.coding[0].display",
            "norm.patient.marital_status_display", "dw.DimPatient.marital_status",
            "TRACKED by SCD Type 2."),
    Mapping("Patient", "deceasedDateTime", "norm.patient.deceased_datetime", ""),
    Mapping("Patient", "meta.versionId", "norm.patient.source_version_id", "",
            "Defaults to '1' when the source stamps no version, which Synthea does not."),
    Mapping("Patient", "meta.lastUpdated", "norm.patient.source_last_updated", ""),
    Mapping("Patient", "address[*]", "norm.patient_address", "",
            "0..* in FHIR, so it is a child table, not columns on the patient."),
    Mapping("Patient", "address[*].line[0]", "norm.patient_address.address_line", ""),
    Mapping("Patient", "address[*].city", "norm.patient_address.city",
            "dw.DimPatient.address_city", "TRACKED by SCD Type 2. The dimension carries the "
            "first address; norm keeps all of them."),
    Mapping("Patient", "address[*].state", "norm.patient_address.state",
            "dw.DimPatient.address_state", "TRACKED by SCD Type 2."),
    Mapping("Patient", "address[*].postalCode", "norm.patient_address.postal_code",
            "dw.DimPatient.address_postal_code", "TRACKED by SCD Type 2."),
    Mapping("Patient", "address[*].country", "norm.patient_address.country", ""),
    Mapping("Patient", "address[*].use", "norm.patient_address.address_use", ""),
    Mapping("", "derived", "", "dw.DimPatient.age_band",
            "Banded from birth_date at load time: 00-17 / 18-44 / 45-64 / 65-79 / 80+."),
    Mapping("", "derived", "", "dw.DimPatient.effective_from",
            "SCD2. The first version opens at 1900-01-01, not the load date — see "
            "sql/11_load_dw.sql for why that distinction decides whether the dimension works."),
    Mapping("", "derived", "", "dw.DimPatient.effective_to",
            "SCD2. 9999-12-31 while current; otherwise the day before the successor opens."),
    Mapping("", "derived", "", "dw.DimPatient.is_current"),
    Mapping("", "derived", "", "dw.DimPatient.version_number"),
    Mapping("", "derived", "", "dw.DimPatient.row_hash",
            "SHA2_256 over the tracked attributes only, delimited and NULL-marked."),
    Mapping("", "derived", "", "dw.DimPatient.is_inferred",
            "1 while the patient exists only because a fact referenced them."),
    Mapping("", "derived", "", "dw.DimPatient.patient_key",
            "Surrogate key. IDENTITY; -1 is Unknown."),
]

# ---------------------------------------------------------------------------
# Practitioner and Organization
# ---------------------------------------------------------------------------

ACTORS = [
    Mapping("Practitioner", "id", "norm.practitioner.practitioner_id",
            "dw.DimProvider.practitioner_id"),
    Mapping("Practitioner", "name[0].family", "norm.practitioner.family_name", ""),
    Mapping("Practitioner", "name[0].given[0]", "norm.practitioner.given_name", ""),
    Mapping("Practitioner", "name[0].prefix[0]", "norm.practitioner.name_prefix", ""),
    Mapping("", "derived", "", "dw.DimProvider.full_name",
            "prefix + given + family, concatenated at load time."),
    Mapping("Practitioner", "gender", "norm.practitioner.gender", "dw.DimProvider.gender"),
    Mapping("Practitioner", "active", "norm.practitioner.is_active", ""),
    Mapping("Practitioner", "identifier[*].system",
            "norm.practitioner_identifier.identifier_system", ""),
    Mapping("Practitioner", "identifier[*].value",
            "norm.practitioner_identifier.identifier_value", "dw.DimProvider.npi",
            "The NPI. This table is what makes conditional references resolvable — the value "
            "in Practitioner?identifier=<system>|<npi> is NOT the resource id."),
    Mapping("", "derived", "", "dw.DimProvider.provider_key", "Surrogate key; -1 is Unknown."),
    Mapping("", "derived", "", "dw.DimProvider.is_inferred", "1 for a late-arriving stub."),

    Mapping("Organization", "id", "norm.organization.organization_id",
            "dw.DimOrganization.organization_id"),
    Mapping("Organization", "name", "norm.organization.name",
            "dw.DimOrganization.organization_name",
            "NOT NULL: R4 invariant org-1 requires a name or an identifier, and this "
            "warehouse requires the name."),
    Mapping("Organization", "type[0].coding[0]", "norm.organization.type_code_concept_id",
            "dw.DimOrganization.organization_type", "Resolved to a concept key in norm; "
            "flattened to its display text in dw."),
    Mapping("Organization", "address[0].line[0]", "norm.organization.address_line", ""),
    Mapping("Organization", "address[0].city", "norm.organization.city",
            "dw.DimOrganization.city"),
    Mapping("Organization", "address[0].state", "norm.organization.state",
            "dw.DimOrganization.state"),
    Mapping("Organization", "address[0].postalCode", "norm.organization.postal_code",
            "dw.DimOrganization.postal_code"),
    Mapping("Organization", "address[0].country", "norm.organization.country", ""),
    Mapping("Organization", "active", "norm.organization.is_active", ""),
    Mapping("Organization", "identifier[*].system",
            "norm.organization_identifier.identifier_system", ""),
    Mapping("Organization", "identifier[*].value",
            "norm.organization_identifier.identifier_value", ""),
    Mapping("", "derived", "", "dw.DimOrganization.organization_key",
            "Surrogate key; -1 is Unknown."),
    Mapping("", "derived", "", "dw.DimOrganization.is_inferred"),
]

# ---------------------------------------------------------------------------
# Encounter
# ---------------------------------------------------------------------------

ENCOUNTER = [
    Mapping("Encounter", "id", "norm.encounter.encounter_id", "dw.FactEncounter.encounter_id",
            "Degenerate dimension — a business key carried on the fact with no dimension "
            "table behind it."),
    Mapping("Encounter", "subject.reference", "norm.encounter.patient_id",
            "dw.FactEncounter.patient_key",
            "Literal reference (Patient/<id>). Resolved to the patient version current at "
            "period_start, not to the version current now."),
    Mapping("Encounter", "status", "norm.encounter.status",
            "dw.FactEncounter.encounter_status", "1..1. Required value set."),
    Mapping("Encounter", "class.code", "norm.encounter.class_code",
            "dw.DimEncounterType.class_code",
            "1..1, and a bare Coding rather than a CodeableConcept — there is no .coding "
            "array to descend into."),
    Mapping("Encounter", "class.display", "norm.encounter.class_display",
            "dw.DimEncounterType.class_display"),
    Mapping("Encounter", "type[0].coding[0]", "norm.encounter.type_code_concept_id",
            "dw.DimEncounterType.type_code"),
    Mapping("", "derived", "", "dw.DimEncounterType.type_display"),
    Mapping("", "derived", "", "dw.DimEncounterType.care_setting",
            "Grouping derived from class_code: Inpatient | Emergency | Ambulatory | Virtual "
            "| Home | Other | Unknown. Derived once here rather than as a CASE in every query."),
    Mapping("", "derived", "", "dw.DimEncounterType.encounter_type_key"),
    Mapping("", "derived", "", "dw.DimEncounterType.is_inferred"),
    Mapping("", "derived", "", "dw.FactEncounter.encounter_type_key",
            "Foreign key to DimEncounterType, resolved from (class_code, type_code)."),
    Mapping("Encounter", "period.start", "norm.encounter.period_start",
            "dw.FactEncounter.start_date_key",
            "Looked up in DimDate rather than computed, so a date outside the dimension "
            "degrades to Unknown instead of failing the foreign key and the whole load."),
    Mapping("Encounter", "period.end", "norm.encounter.period_end",
            "dw.FactEncounter.end_date_key"),
    Mapping("Encounter", "serviceProvider.reference", "norm.encounter.service_provider_id",
            "dw.FactEncounter.organization_key",
            "CONDITIONAL reference: Organization?identifier=<system>|<value>. Resolved via "
            "norm.organization_identifier."),
    Mapping("Encounter", "participant[*].individual.reference",
            "norm.encounter.primary_performer_id", "dw.FactEncounter.provider_key",
            "CONDITIONAL reference on NPI. The participant whose type is PPRF is preferred; "
            "the list also holds admitters and translators."),
    Mapping("", "derived", "", "dw.FactEncounter.length_of_stay_days",
            "DATEDIFF(SECOND) / 86400 — seconds, not days, so a same-day stay is a fraction "
            "rather than zero."),
    Mapping("", "derived", "", "dw.FactEncounter.length_of_stay_minutes"),
    Mapping("", "derived", "", "dw.FactEncounter.is_inpatient", "class_code in IMP, ACUTE, NONAC."),
    Mapping("", "derived", "", "dw.FactEncounter.is_emergency", "class_code = EMER."),
    Mapping("", "derived", "", "dw.FactEncounter.is_readmission_30d",
            "An inpatient admission starting within 30 days of that patient's previous "
            "inpatient discharge. Computed with LAG at load time, not in the report."),
    Mapping("", "derived", "", "dw.FactEncounter.days_since_prior_discharge"),
    Mapping("", "derived", "", "dw.FactEncounter.encounter_count", "Always 1; the additive count."),
    Mapping("", "derived", "", "dw.FactEncounter.encounter_key", "Surrogate key."),
    Mapping("", "derived", "", "dw.FactEncounter.load_batch_id", "meta.load_batch lineage."),
]

# ---------------------------------------------------------------------------
# Condition, Observation, Procedure, MedicationRequest
# ---------------------------------------------------------------------------

CLINICAL = [
    Mapping("Condition", "id", "norm.condition.condition_id",
            "dw.FactEncounterDiagnosis.condition_id"),
    Mapping("Condition", "subject.reference", "norm.condition.patient_id",
            "dw.FactEncounterDiagnosis.patient_key"),
    Mapping("Condition", "encounter.reference", "norm.condition.encounter_id",
            "dw.FactEncounterDiagnosis.encounter_key"),
    Mapping("Condition", "code.coding[0]", "norm.condition.code_concept_id",
            "dw.FactEncounterDiagnosis.diagnosis_key",
            "SNOMED CT. NOT NULL — an uncoded condition is quarantined rather than loaded "
            "as an Unknown diagnosis that would inflate every count."),
    Mapping("", "derived", "", "dw.DimDiagnosis.code_system"),
    Mapping("", "derived", "", "dw.DimDiagnosis.code"),
    Mapping("", "derived", "", "dw.DimDiagnosis.code_display"),
    Mapping("", "derived", "", "dw.DimDiagnosis.diagnosis_key"),
    Mapping("", "derived", "", "dw.DimDiagnosis.is_inferred"),
    Mapping("Condition", "clinicalStatus.coding[0].code", "norm.condition.clinical_status",
            "dw.FactEncounterDiagnosis.clinical_status"),
    Mapping("Condition", "verificationStatus.coding[0].code",
            "norm.condition.verification_status", ""),
    Mapping("Condition", "onsetDateTime", "norm.condition.onset_datetime",
            "dw.FactEncounterDiagnosis.onset_date_key"),
    Mapping("Condition", "abatementDateTime", "norm.condition.abatement_datetime", ""),
    Mapping("Condition", "recordedDate", "norm.condition.recorded_date", "",
            "Used as the onset date when onsetDateTime is absent."),
    Mapping("", "derived", "", "dw.FactEncounterDiagnosis.diagnosis_count"),
    Mapping("", "derived", "", "dw.FactEncounterDiagnosis.encounter_diagnosis_key"),
    Mapping("", "derived", "", "dw.FactEncounterDiagnosis.load_batch_id"),

    Mapping("Observation", "id", "norm.observation.observation_id",
            "dw.FactObservation.observation_id"),
    Mapping("Observation", "subject.reference", "norm.observation.patient_id",
            "dw.FactObservation.patient_key"),
    Mapping("Observation", "encounter.reference", "norm.observation.encounter_id",
            "dw.FactObservation.encounter_key", "Nullable: a result ordered outside an "
            "encounter is legitimate."),
    Mapping("Observation", "code.coding[*]", "norm.observation.code_concept_id",
            "dw.FactObservation.observation_code_key",
            "1..1. LOINC preferred where a concept carries several codings; the ones not "
            "chosen are kept in norm.resource_coding rather than discarded."),
    Mapping("", "derived", "", "dw.DimObservationCode.code_system"),
    Mapping("", "derived", "", "dw.DimObservationCode.code"),
    Mapping("", "derived", "", "dw.DimObservationCode.code_display"),
    Mapping("", "derived", "", "dw.DimObservationCode.observation_code_key"),
    Mapping("", "derived", "", "dw.DimObservationCode.is_inferred"),
    Mapping("Observation", "category[0].coding[0].code", "norm.observation.category_code", ""),
    Mapping("Observation", "status", "norm.observation.status",
            "dw.FactObservation.observation_status", "1..1."),
    Mapping("Observation", "effectiveDateTime", "norm.observation.effective_datetime",
            "dw.FactObservation.effective_date_key"),
    Mapping("Observation", "issued", "norm.observation.issued", ""),
    Mapping("Observation", "valueQuantity.value", "norm.observation.value_quantity",
            "dw.FactObservation.value_numeric",
            "value[x] is a choice of eleven types; three occur here and each has its own "
            "typed column rather than one NVARCHAR holding all of them."),
    Mapping("Observation", "valueQuantity.unit", "norm.observation.value_unit",
            "dw.FactObservation.value_unit",
            "A quantity with no unit is refused by a check constraint."),
    Mapping("Observation", "valueCodeableConcept.coding[0]",
            "norm.observation.value_code_concept_id", "dw.FactObservation.value_text"),
    Mapping("Observation", "valueString", "norm.observation.value_string",
            "dw.FactObservation.value_text"),
    Mapping("Observation", "component[*]", "norm.observation_component", "",
            "A blood pressure is one Observation with two components; a survey panel has "
            "twenty-one."),
    Mapping("Observation", "component[*].code.coding[0]",
            "norm.observation_component.code_concept_id", ""),
    Mapping("Observation", "component[*].valueQuantity.value",
            "norm.observation_component.value_quantity", ""),
    Mapping("Observation", "component[*].valueQuantity.unit",
            "norm.observation_component.value_unit", ""),
    Mapping("Observation", "component[*].valueCodeableConcept.coding[0]",
            "norm.observation_component.value_code_concept_id", ""),
    Mapping("", "derived", "norm.observation.has_components", ""),
    Mapping("", "derived", "norm.observation_component.observation_id", ""),
    Mapping("", "derived", "norm.observation_component.component_seq",
            "1-based position within component[]."),
    Mapping("", "derived", "", "dw.FactObservation.component_seq",
            "0 for the observation's own value[x]; 1..n for its components. Part of the "
            "natural key, and what makes the fact grain 'one result'."),
    Mapping("", "derived", "", "dw.FactObservation.is_numeric"),
    Mapping("", "derived", "", "dw.FactObservation.result_count"),
    Mapping("", "derived", "", "dw.FactObservation.observation_fact_key"),
    Mapping("", "derived", "", "dw.FactObservation.load_batch_id"),

    Mapping("Procedure", "id", "norm.procedure.procedure_id", "dw.FactProcedure.procedure_id"),
    Mapping("Procedure", "subject.reference", "norm.procedure.patient_id",
            "dw.FactProcedure.patient_key"),
    Mapping("Procedure", "encounter.reference", "norm.procedure.encounter_id",
            "dw.FactProcedure.encounter_key"),
    Mapping("Procedure", "code.coding[0]", "norm.procedure.code_concept_id",
            "dw.FactProcedure.procedure_key", "SNOMED CT."),
    Mapping("", "derived", "", "dw.DimProcedure.code_system"),
    Mapping("", "derived", "", "dw.DimProcedure.code"),
    Mapping("", "derived", "", "dw.DimProcedure.code_display"),
    Mapping("", "derived", "", "dw.DimProcedure.procedure_key"),
    Mapping("", "derived", "", "dw.DimProcedure.is_inferred"),
    Mapping("Procedure", "status", "norm.procedure.status", "dw.FactProcedure.procedure_status"),
    Mapping("Procedure", "performedDateTime | performedPeriod.start",
            "norm.procedure.performed_start", "dw.FactProcedure.performed_date_key",
            "performed[x] is a choice; both branches are coalesced, because a procedure "
            "with a period and no dateTime is not a procedure with no date."),
    Mapping("Procedure", "performedPeriod.end", "norm.procedure.performed_end", ""),
    Mapping("", "derived", "", "dw.FactProcedure.duration_minutes"),
    Mapping("", "derived", "", "dw.FactProcedure.procedure_count"),
    Mapping("", "derived", "", "dw.FactProcedure.procedure_fact_key"),
    Mapping("", "derived", "", "dw.FactProcedure.load_batch_id"),

    Mapping("MedicationRequest", "id", "norm.medication_request.medication_request_id",
            "dw.FactMedicationOrder.medication_request_id"),
    Mapping("MedicationRequest", "subject.reference", "norm.medication_request.patient_id",
            "dw.FactMedicationOrder.patient_key"),
    Mapping("MedicationRequest", "encounter.reference", "norm.medication_request.encounter_id",
            "dw.FactMedicationOrder.encounter_key"),
    Mapping("MedicationRequest", "status", "norm.medication_request.status",
            "dw.FactMedicationOrder.order_status", "1..1."),
    Mapping("MedicationRequest", "intent", "norm.medication_request.intent",
            "dw.FactMedicationOrder.order_intent", "1..1."),
    Mapping("MedicationRequest", "medicationCodeableConcept.coding[0]",
            "norm.medication_request.code_concept_id", "dw.FactMedicationOrder.medication_key",
            "medication[x] branch one. RxNorm."),
    Mapping("MedicationRequest", "medicationReference.reference",
            "norm.medication_request.medication_reference_id", "",
            "medication[x] branch two, taken by 16% of the rows in this extract. A check "
            "constraint enforces exactly one branch, which is the FHIR rule written down."),
    Mapping("", "derived", "", "dw.DimMedication.code_system"),
    Mapping("", "derived", "", "dw.DimMedication.code"),
    Mapping("", "derived", "", "dw.DimMedication.code_display"),
    Mapping("", "derived", "", "dw.DimMedication.medication_key"),
    Mapping("", "derived", "", "dw.DimMedication.is_inferred"),
    Mapping("MedicationRequest", "authoredOn", "norm.medication_request.authored_on",
            "dw.FactMedicationOrder.authored_date_key"),
    Mapping("MedicationRequest", "requester.reference", "norm.medication_request.requester_id",
            "dw.FactMedicationOrder.provider_key", "CONDITIONAL reference on NPI."),
    Mapping("", "derived", "", "dw.FactMedicationOrder.is_active", "status = 'active'."),
    Mapping("", "derived", "", "dw.FactMedicationOrder.order_count"),
    Mapping("", "derived", "", "dw.FactMedicationOrder.medication_order_key"),
    Mapping("", "derived", "", "dw.FactMedicationOrder.load_batch_id"),
]

# ---------------------------------------------------------------------------
# Terminology, raw, quarantine, calendar
# ---------------------------------------------------------------------------

INFRASTRUCTURE = [
    Mapping("", "any .coding[*].system", "norm.code_system.system_uri", ""),
    Mapping("", "derived", "norm.code_system.system_name", ""),
    Mapping("", "derived", "norm.code_system.code_system_id", ""),
    Mapping("", "derived", "norm.code_system.is_licensed", ""),
    Mapping("", "derived", "norm.code_system.notes", ""),
    Mapping("", "any .coding[*].code", "norm.code_concept.code", ""),
    Mapping("", "any .coding[*].display", "norm.code_concept.display", ""),
    Mapping("", "derived", "norm.code_concept.code_concept_id", ""),
    Mapping("", "derived", "norm.code_concept.code_system_id", ""),
    Mapping("", "derived", "norm.code_concept.first_seen_at", ""),
    Mapping("", "any .coding[*]", "norm.resource_coding.code_concept_id", "",
            "Every coding, including the ones not chosen as primary."),
    Mapping("", "derived", "norm.resource_coding.resource_type", ""),
    Mapping("", "derived", "norm.resource_coding.resource_id", ""),
    Mapping("", "derived", "norm.resource_coding.coding_seq", ""),
    Mapping("", "derived", "norm.resource_coding.is_primary", ""),
    Mapping("", "derived", "norm.practitioner_identifier.practitioner_id", ""),
    Mapping("", "derived", "norm.organization_identifier.organization_id", ""),
    Mapping("", "derived", "norm.patient_address.patient_id", ""),
    Mapping("", "derived", "norm.patient_address.address_seq", ""),
]

CALENDAR = [
    Mapping("", "generated", "", f"dw.DimDate.{column}", note)
    for column, note in [
        ("date_key", "YYYYMMDD as an integer. -1 is Unknown."),
        ("full_date", ""), ("day_of_month", ""), ("day_of_week", ""), ("day_name", ""),
        ("day_of_year", ""), ("week_of_year", "ISO week."), ("month_number", ""),
        ("month_name", ""), ("month_year", "YYYY-MM, so text sorts chronologically."),
        ("quarter_number", ""), ("quarter_name", ""), ("year_number", ""),
        ("is_weekend", ""),
        ("fiscal_year", "Health-sector fiscal year, starting 1 April."),
        ("fiscal_quarter", "1 = Apr-Jun."),
    ]
]

MAPPINGS: list[Mapping] = PATIENT + ACTORS + ENCOUNTER + CLINICAL + INFRASTRUCTURE + CALENDAR


# Columns that exist for plumbing and are deliberately absent from the map,
# with the reason. Keeping this list explicit is what lets the test demand that
# every other column is documented.
UNMAPPED_ALLOWED = {
    "raw.fhir_resource": "Holds the source document itself; its columns are described in "
                         "sql/01_raw.sql and are not warehouse attributes.",
    "stg.ingest_rejects": "Quarantine metadata, not clinical data.",
    "meta.load_batch": "Load audit.",
}
