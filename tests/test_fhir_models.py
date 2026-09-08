"""
The validation gate.

These run without a database and without a network, because the properties
being asserted are properties of the models. Every test names a rule from the
FHIR R4 specification and demonstrates both halves of it: that a conforming
resource passes, and that a non-conforming one is rejected with a reason a human
could act on. A validator that has only ever been shown valid input is
indistinguishable from `return True`.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fhir.models import (  # noqa: E402
    FhirValidationError,
    ObservationModel,
    parse_reference,
    validate_resource,
)


def observation(**overrides) -> dict:
    resource = {
        "resourceType": "Observation",
        "id": "obs-1",
        "status": "final",
        "code": {"coding": [{"system": "http://loinc.org", "code": "8302-2",
                             "display": "Body Height"}]},
        "subject": {"reference": "Patient/pat-1"},
        "effectiveDateTime": "2025-02-27T20:08:24+00:00",
        "valueQuantity": {"value": 161.2, "unit": "cm"},
    }
    resource.update(overrides)
    return resource


def medication_request(**overrides) -> dict:
    resource = {
        "resourceType": "MedicationRequest",
        "id": "mr-1",
        "status": "active",
        "intent": "order",
        "subject": {"reference": "Patient/pat-1"},
        "medicationCodeableConcept": {
            "coding": [{"system": "http://www.nlm.nih.gov/research/umls/rxnorm",
                        "code": "310798"}]
        },
    }
    resource.update(overrides)
    return resource


# ---------------------------------------------------------------------------
# References
# ---------------------------------------------------------------------------

def test_literal_reference_resolves_to_a_type_and_id():
    resolved = parse_reference("Patient/083563aa-d1e4-5201-e6d1-7000d4b794c5")
    assert resolved.resource_type == "Patient"
    assert resolved.resource_id == "083563aa-d1e4-5201-e6d1-7000d4b794c5"
    assert not resolved.is_conditional


def test_absolute_reference_resolves_to_the_same_thing():
    """A server may return the full URL. It is the same reference."""
    resolved = parse_reference("https://hapi.fhir.org/baseR4/Patient/abc-123")
    assert resolved.resource_type == "Patient"
    assert resolved.resource_id == "abc-123"


def test_conditional_reference_resolves_to_a_system_and_value():
    """The form Synthea writes for every provider, and the reason the warehouse
    carries identifier tables at all."""
    resolved = parse_reference(
        "Practitioner?identifier=http://hl7.org/fhir/sid/us-npi|9999979393")
    assert resolved.resource_type == "Practitioner"
    assert resolved.resource_id is None
    assert resolved.is_conditional
    assert resolved.identifier_system == "http://hl7.org/fhir/sid/us-npi"
    assert resolved.identifier_value == "9999979393"


def test_conditional_reference_does_not_masquerade_as_a_literal_one():
    """The regression that matters.

    If the conditional form ever parses as a literal one, `resource_id` comes
    back holding an NPI, every join to Practitioner misses, and every encounter
    silently loses its clinician while the load reports success.
    """
    resolved = parse_reference(
        "Practitioner?identifier=http://hl7.org/fhir/sid/us-npi|9999979393")
    assert resolved.resource_id is None, "a conditional reference has no literal id"


def test_unparseable_reference_is_none_not_a_guess():
    assert parse_reference("urn:uuid:not-a-reference") is None
    assert parse_reference("") is None
    assert parse_reference(None) is None


# ---------------------------------------------------------------------------
# Required cardinality
# ---------------------------------------------------------------------------

def test_a_conforming_observation_validates():
    model = validate_resource(observation())
    assert isinstance(model, ObservationModel)
    assert model.status == "final"
    assert model.code.primary().code == "8302-2"


@pytest.mark.parametrize("element", ["status", "code"])
def test_observation_requires_its_1_1_elements(element):
    resource = observation()
    del resource[element]
    with pytest.raises(FhirValidationError) as caught:
        validate_resource(resource)
    assert element in str(caught.value)


def test_quantity_without_a_unit_is_rejected():
    """A measured number with no unit is not interpretable.

    FHIR permits it — neither Quantity.unit nor Quantity.code is required — so
    this is a stated warehouse policy, not a spec reading. Five observations in
    a 925,283-resource extract take this shape, and before the rule existed they
    reached the database and failed CK_observation_quantity_has_unit five
    hundred seconds into the shred, taking the whole load with them.
    """
    with pytest.raises(FhirValidationError) as caught:
        validate_resource(observation(valueQuantity={"value": 21.998,
                                                     "system": "http://unitsofmeasure.org"}))
    assert "neither unit nor code" in str(caught.value)


def test_quantity_with_only_a_ucum_code_is_accepted():
    """Quantity.code is the coded form of the unit and Quantity.unit the display
    form. Either makes the number interpretable; requiring the display form
    would reject conforming resources."""
    model = validate_resource(observation(valueQuantity={"value": 5.0, "code": "mg",
                                                         "system": "http://unitsofmeasure.org"}))
    assert model.valueQuantity.code == "mg"


def test_component_quantity_without_a_unit_is_rejected():
    with pytest.raises(FhirValidationError) as caught:
        validate_resource(observation(component=[{
            "code": {"coding": [{"system": "http://loinc.org", "code": "8480-6"}]},
            "valueQuantity": {"value": 120},
        }]))
    assert "neither unit nor code" in str(caught.value)


def test_observation_effective_datetime_is_optional():
    """0..1 in R4. A model that requires it rejects valid resources — the
    failure mode nobody notices, because it looks like strictness."""
    resource = observation()
    del resource["effectiveDateTime"]
    assert validate_resource(resource).effectiveDateTime is None


def test_encounter_requires_status_and_class():
    base = {"resourceType": "Encounter", "id": "enc-1", "status": "finished",
            "class": {"code": "AMB", "system": "http://terminology.hl7.org/CodeSystem/v3-ActCode"}}
    assert validate_resource(base).class_.code == "AMB"
    for element in ("status", "class"):
        broken = dict(base)
        del broken[element]
        with pytest.raises(FhirValidationError):
            validate_resource(broken)


# ---------------------------------------------------------------------------
# Required value sets
# ---------------------------------------------------------------------------

def test_status_outside_the_value_set_is_rejected():
    with pytest.raises(FhirValidationError) as caught:
        validate_resource(observation(status="FINAL"))
    assert "value set" in str(caught.value)


def test_patient_gender_outside_the_value_set_is_rejected():
    with pytest.raises(FhirValidationError) as caught:
        validate_resource({"resourceType": "Patient", "id": "p1", "gender": "M"})
    assert "value set" in str(caught.value)


def test_patient_has_no_required_elements():
    """R4 marks every Patient element 0..1 or 0..*. Surprising, and modelling it
    otherwise rejects conforming resources."""
    model = validate_resource({"resourceType": "Patient", "id": "p1"})
    assert model.birthDate is None and model.gender is None and model.address == []


# ---------------------------------------------------------------------------
# Choice types
# ---------------------------------------------------------------------------

def test_medication_request_accepts_the_codeable_concept_branch():
    assert validate_resource(medication_request()).medicationCodeableConcept is not None


def test_medication_request_accepts_the_reference_branch():
    """16% of the rows in the extract take this branch. A model that only knows
    the CodeableConcept branch loses them."""
    resource = medication_request()
    del resource["medicationCodeableConcept"]
    resource["medicationReference"] = {"reference": "Medication/med-1"}
    assert validate_resource(resource).medicationReference is not None


def test_medication_request_rejects_both_branches():
    resource = medication_request()
    resource["medicationReference"] = {"reference": "Medication/med-1"}
    with pytest.raises(FhirValidationError) as caught:
        validate_resource(resource)
    assert "medication[x]" in str(caught.value)


def test_medication_request_rejects_neither_branch():
    resource = medication_request()
    del resource["medicationCodeableConcept"]
    with pytest.raises(FhirValidationError) as caught:
        validate_resource(resource)
    assert "medication[x]" in str(caught.value)


# ---------------------------------------------------------------------------
# Invariants and ids
# ---------------------------------------------------------------------------

def test_organization_without_a_name_is_rejected():
    """R4 invariant org-1, and the case a @field_validator does not catch.

    A field validator runs on a value that was supplied; when the element is
    absent pydantic uses the default and skips it. The version of this check
    written that way passed an empty string and let a *missing* name through,
    which the defect manifest caught by reporting 21 quarantined rows against 24
    injected. Both cases are asserted here so the fix cannot regress to the
    convenient half.
    """
    with pytest.raises(FhirValidationError) as caught:
        validate_resource({"resourceType": "Organization", "id": "o1"})
    assert "org-1" in str(caught.value)

    with pytest.raises(FhirValidationError):
        validate_resource({"resourceType": "Organization", "id": "o1", "name": ""})


def test_organization_with_a_name_validates():
    assert validate_resource(
        {"resourceType": "Organization", "id": "o1", "name": "Massachusetts General"}
    ).name == "Massachusetts General"


def test_malformed_id_is_rejected():
    with pytest.raises(FhirValidationError) as caught:
        validate_resource(observation(id="not a valid id!"))
    assert "valid FHIR id" in str(caught.value)


def test_resource_type_must_match_the_model():
    with pytest.raises(FhirValidationError) as caught:
        validate_resource({"resourceType": "Observation", "id": "x", "status": "final",
                           "code": {"coding": [{"system": "s", "code": "c"}]},
                           "meta": {"versionId": "1"}} | {"resourceType": "Condition"})
    # Routed to ConditionModel, which requires subject.
    assert "subject" in str(caught.value) or "resourceType" in str(caught.value)


def test_unmodelled_resource_type_is_a_rejection_not_a_crash():
    with pytest.raises(FhirValidationError) as caught:
        validate_resource({"resourceType": "Provenance", "id": "prov-1"})
    assert "not modelled" in str(caught.value)


def test_unknown_extensions_are_allowed_through():
    """Rejecting a resource for carrying more than you modelled means rejecting
    every US Core extension in existence."""
    resource = observation()
    resource["extension"] = [{"url": "http://example.org/anything", "valueString": "x"}]
    resource["_status"] = {"extension": []}
    assert validate_resource(resource).status == "final"


# ---------------------------------------------------------------------------
# Version identity
# ---------------------------------------------------------------------------

def test_version_defaults_to_1_when_meta_is_absent():
    """Synthea stamps no meta.versionId. The raw layer's primary key includes
    version_id, so a NULL there would break idempotency exactly when a resource
    is re-sent."""
    assert validate_resource(observation()).version_id == "1"


def test_version_is_taken_from_meta_when_present():
    model = validate_resource(observation(meta={"versionId": "4"}))
    assert model.version_id == "4"


def test_primary_coding_prefers_the_expected_system():
    """0.7% of the observations in the extract carry both a LOINC and a SNOMED
    coding. Taking coding[0] keys some of them on SNOMED and splits one concept
    across two dimension rows."""
    model = validate_resource(observation(code={"coding": [
        {"system": "http://snomed.info/sct", "code": "271649006"},
        {"system": "http://loinc.org", "code": "8480-6"},
    ]}))
    assert model.code.primary(prefer_system="http://loinc.org").code == "8480-6"
    assert model.code.primary().code == "271649006", "no preference means first"


def test_encounter_primary_performer_prefers_pprf():
    """participant[] also holds admitters and translators; PPRF is the
    clinician. Taking participant[0] attributes the encounter to whoever the
    source happened to list first."""
    model = validate_resource({
        "resourceType": "Encounter", "id": "e1", "status": "finished",
        "class": {"code": "AMB"},
        "participant": [
            {"type": [{"coding": [{"code": "ADM"}]}],
             "individual": {"reference": "Practitioner?identifier=sys|admitter"}},
            {"type": [{"coding": [{"code": "PPRF"}]}],
             "individual": {"reference": "Practitioner?identifier=sys|clinician"}},
        ],
    })
    assert model.primary_performer().identifier_value == "clinician"


def test_encounter_without_participants_has_no_performer():
    model = validate_resource({"resourceType": "Encounter", "id": "e1",
                               "status": "finished", "class": {"code": "AMB"}})
    assert model.primary_performer() is None
