"""
Pydantic models for the eight FHIR R4 resource types this warehouse ingests.

These are deliberately *not* a complete FHIR implementation. A full R4 model set
is roughly 150 resources and several thousand elements, it exists already
(fhir.resources on PyPI), and vendoring a partial copy of it would be pretending
to a completeness this does not have.

What these models are is a **gate**: the properties that must hold for a
resource to be loadable, expressed once, so that a resource which would violate
a database constraint is caught at the boundary with a readable reason instead
of at the INSERT with a constraint name. Three kinds of rule are enforced:

  1. Cardinality the FHIR R4 spec marks 1..1. `Observation.status` and
     `Observation.code` are required; `Observation.effective[x]` is not. Getting
     this backwards is how a warehouse ends up rejecting valid resources.

  2. Value sets that are `required` binding strength in R4 — status, intent,
     gender. A code outside them is a genuine error, not a local variant.

  3. Choice types. `MedicationRequest.medication[x]` is exactly one of
     medicationCodeableConcept or medicationReference. Sixteen per cent of the
     rows in this extract take the reference branch, and a model that only knows
     about the CodeableConcept branch silently loses them.

Everything else is `extra="allow"`. A model that rejects unknown fields would
reject every US Core extension, and rejecting data because it carries more than
you modelled is the wrong failure.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

__all__ = [
    "MODELS",
    "FhirValidationError",
    "Coding",
    "Reference",
    "PatientModel",
    "PractitionerModel",
    "OrganizationModel",
    "EncounterModel",
    "ConditionModel",
    "ObservationModel",
    "ProcedureModel",
    "MedicationRequestModel",
    "parse_reference",
    "validate_resource",
]


class FhirValidationError(Exception):
    """A resource that cannot be loaded, carrying why in a form a human reads."""

    def __init__(self, reason: str, detail: str | None = None):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


# ---------------------------------------------------------------------------
# References
# ---------------------------------------------------------------------------

# "Patient/1234-abcd"
_LITERAL_REF = re.compile(r"^(?:.*/)?(?P<type>[A-Z][A-Za-z]+)/(?P<id>[A-Za-z0-9\-.]{1,64})$")
# "Practitioner?identifier=http://hl7.org/fhir/sid/us-npi|9999979393"
_CONDITIONAL_REF = re.compile(
    r"^(?P<type>[A-Z][A-Za-z]+)\?identifier=(?P<system>[^|]+)\|(?P<value>.+)$")


class ResolvedReference(BaseModel):
    """What a FHIR reference string actually points at.

    Two forms reach this warehouse and only one of them is the obvious one:

        Patient/083563aa-...                          -> literal
        Practitioner?identifier=<system>|<npi>        -> conditional

    Synthea writes conditional references for every Practitioner and every
    Organization. The identifier in them is an NPI, which is *not* the
    Practitioner's resource id — so code that assumes the literal form resolves
    nothing, silently, and every encounter loses its clinician.
    """

    resource_type: str
    resource_id: str | None = None
    identifier_system: str | None = None
    identifier_value: str | None = None

    @property
    def is_conditional(self) -> bool:
        return self.resource_id is None


def parse_reference(reference: str | None) -> ResolvedReference | None:
    if not reference:
        return None
    conditional = _CONDITIONAL_REF.match(reference)
    if conditional:
        return ResolvedReference(
            resource_type=conditional.group("type"),
            identifier_system=conditional.group("system"),
            identifier_value=conditional.group("value"),
        )
    literal = _LITERAL_REF.match(reference)
    if literal:
        return ResolvedReference(
            resource_type=literal.group("type"),
            resource_id=literal.group("id"),
        )
    return None


class Reference(BaseModel):
    model_config = ConfigDict(extra="allow")
    reference: str | None = None
    display: str | None = None

    def resolved(self) -> ResolvedReference | None:
        return parse_reference(self.reference)


class Coding(BaseModel):
    model_config = ConfigDict(extra="allow")
    system: str | None = None
    code: str | None = None
    display: str | None = None


class CodeableConcept(BaseModel):
    model_config = ConfigDict(extra="allow")
    coding: list[Coding] = Field(default_factory=list)
    text: str | None = None

    def primary(self, prefer_system: str | None = None) -> Coding | None:
        """The coding a star schema will key on.

        The rule, written down because "the first one" is a rule that changes
        meaning when a source reorders its array: prefer the system this
        resource type is expected to be coded in, otherwise take the first.
        """
        if not self.coding:
            return None
        if prefer_system:
            for coding in self.coding:
                if coding.system == prefer_system:
                    return coding
        return self.coding[0]


class Period(BaseModel):
    model_config = ConfigDict(extra="allow")
    start: datetime | None = None
    end: datetime | None = None


class Quantity(BaseModel):
    model_config = ConfigDict(extra="allow")
    value: float | None = None
    unit: str | None = None
    system: str | None = None
    code: str | None = None


class Identifier(BaseModel):
    model_config = ConfigDict(extra="allow")
    system: str | None = None
    value: str | None = None


class HumanName(BaseModel):
    model_config = ConfigDict(extra="allow")
    use: str | None = None
    family: str | None = None
    given: list[str] = Field(default_factory=list)
    prefix: list[str] = Field(default_factory=list)


class Address(BaseModel):
    model_config = ConfigDict(extra="allow")
    use: str | None = None
    line: list[str] = Field(default_factory=list)
    city: str | None = None
    state: str | None = None
    postalCode: str | None = None
    country: str | None = None


class Meta(BaseModel):
    model_config = ConfigDict(extra="allow")
    versionId: str | None = None
    lastUpdated: datetime | None = None
    profile: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------

# R4 §2.1: an id is 1..64 characters of A-Z a-z 0-9 - and .
_ID = re.compile(r"^[A-Za-z0-9\-.]{1,64}$")

GENDERS = {"male", "female", "other", "unknown"}


class FhirResource(BaseModel):
    """Everything true of every resource: it knows its type and has an id."""

    model_config = ConfigDict(extra="allow")

    resource_type_name: ClassVar[str] = ""

    resourceType: str
    id: str
    meta: Meta | None = None

    @field_validator("id")
    @classmethod
    def _id_is_wellformed(cls, value: str) -> str:
        if not _ID.match(value):
            raise ValueError(
                f"id {value!r} is not a valid FHIR id (R4 requires 1-64 chars of [A-Za-z0-9-.])"
            )
        return value

    @field_validator("resourceType")
    @classmethod
    def _type_matches_model(cls, value: str) -> str:
        expected = cls.resource_type_name
        if expected and value != expected:
            raise ValueError(f"resourceType {value!r} does not match {expected!r}")
        return value

    @property
    def version_id(self) -> str:
        """Synthea does not stamp meta.versionId, and neither do many EHRs.

        Defaulting to '1' rather than NULL keeps the raw-layer primary key
        (type, id, version) usable: a NULL in a key column means the idempotency
        guarantee stops working exactly when a resource is re-sent.
        """
        if self.meta and self.meta.versionId:
            return self.meta.versionId
        return "1"

    @property
    def last_updated(self) -> datetime | None:
        return self.meta.lastUpdated if self.meta else None


class PatientModel(FhirResource):
    resource_type_name: ClassVar[str] = "Patient"

    # Patient has no 1..1 elements in R4. Every field here is optional, and that
    # is the spec's decision, not an oversight in this model.
    name: list[HumanName] = Field(default_factory=list)
    gender: str | None = None
    birthDate: date | None = None
    maritalStatus: CodeableConcept | None = None
    address: list[Address] = Field(default_factory=list)
    identifier: list[Identifier] = Field(default_factory=list)
    deceasedDateTime: datetime | None = None
    deceasedBoolean: bool | None = None

    @field_validator("gender")
    @classmethod
    def _gender_in_value_set(cls, value: str | None) -> str | None:
        if value is not None and value not in GENDERS:
            raise ValueError(
                f"gender {value!r} is outside the required value set {sorted(GENDERS)}")
        return value


class PractitionerModel(FhirResource):
    resource_type_name: ClassVar[str] = "Practitioner"

    name: list[HumanName] = Field(default_factory=list)
    gender: str | None = None
    identifier: list[Identifier] = Field(default_factory=list)
    active: bool | None = None

    @field_validator("gender")
    @classmethod
    def _gender_in_value_set(cls, value: str | None) -> str | None:
        if value is not None and value not in GENDERS:
            raise ValueError(f"gender {value!r} is outside the required value set")
        return value


class OrganizationModel(FhirResource):
    resource_type_name: ClassVar[str] = "Organization"

    name: str | None = None
    type: list[CodeableConcept] = Field(default_factory=list)
    address: list[Address] = Field(default_factory=list)
    identifier: list[Identifier] = Field(default_factory=list)
    active: bool | None = None

    def model_post_init(self, _context: Any) -> None:
        # R4 invariant org-1: "The organization SHALL at least have a name or an
        # identifier, and possibly more than one". norm.organization.name is
        # NOT NULL, so the name half is what this warehouse requires.
        #
        # This lives in model_post_init and not in a @field_validator("name"),
        # and the difference is not stylistic. A field validator runs on a value
        # that was *supplied*; when the element is absent pydantic fills the
        # default and skips the validator entirely. So the version of this check
        # written as a field validator caught `"name": ""` and did not catch a
        # missing name — which is the case that actually occurs. The defect
        # manifest is what surfaced it: 24 resources corrupted, 21 quarantined.
        if not self.name:
            raise ValueError("Organization has no name (R4 invariant org-1)")


ENCOUNTER_STATUS = {
    "planned", "arrived", "triaged", "in-progress", "onleave",
    "finished", "cancelled", "entered-in-error", "unknown",
}


class EncounterModel(FhirResource):
    resource_type_name: ClassVar[str] = "Encounter"

    status: str                       # 1..1
    class_: Coding = Field(alias="class")   # 1..1 — and `class` is a Python keyword
    type: list[CodeableConcept] = Field(default_factory=list)
    subject: Reference | None = None
    participant: list[dict[str, Any]] = Field(default_factory=list)
    period: Period | None = None
    serviceProvider: Reference | None = None

    @field_validator("status")
    @classmethod
    def _status_in_value_set(cls, value: str) -> str:
        if value not in ENCOUNTER_STATUS:
            raise ValueError(f"Encounter.status {value!r} is outside the required value set")
        return value

    def primary_performer(self) -> ResolvedReference | None:
        """The clinician. FHIR puts them in participant[].individual, and the
        participant list also holds admitters, discharge clinicians and
        translators — so the PPRF (primary performer) type is preferred, with a
        fall back to the first participant carrying an individual."""
        fallback = None
        for participant in self.participant:
            individual = participant.get("individual") or {}
            resolved = parse_reference(individual.get("reference"))
            if resolved is None:
                continue
            if fallback is None:
                fallback = resolved
            for type_entry in participant.get("type") or []:
                for coding in type_entry.get("coding") or []:
                    if coding.get("code") == "PPRF":
                        return resolved
        return fallback


CONDITION_CLINICAL = {"active", "recurrence", "relapse", "inactive", "remission", "resolved"}


class ConditionModel(FhirResource):
    resource_type_name: ClassVar[str] = "Condition"

    subject: Reference                # 1..1
    # validate_default: pydantic skips validators on a field that was never
    # supplied, so an omitted code sailed past the check below while an
    # explicit null was caught. Both end at the same NOT NULL column.
    code: CodeableConcept | None = Field(default=None, validate_default=True)
    encounter: Reference | None = None
    clinicalStatus: CodeableConcept | None = None
    verificationStatus: CodeableConcept | None = None
    onsetDateTime: datetime | None = None
    abatementDateTime: datetime | None = None
    recordedDate: datetime | None = None

    @field_validator("code")
    @classmethod
    def _code_has_a_coding(cls, value: CodeableConcept | None) -> CodeableConcept | None:
        # Condition.code is 0..1 in R4, but norm.condition.code_concept_id is
        # NOT NULL: an uncoded condition cannot join to a diagnosis dimension,
        # so it is quarantined rather than loaded as an Unknown diagnosis that
        # inflates every count.
        if value is None or not value.coding:
            raise ValueError("Condition.code carries no coding; cannot resolve a diagnosis")
        return value


OBSERVATION_STATUS = {
    "registered", "preliminary", "final", "amended", "corrected",
    "cancelled", "entered-in-error", "unknown",
}


class ObservationComponent(BaseModel):
    model_config = ConfigDict(extra="allow")
    code: CodeableConcept
    valueQuantity: Quantity | None = None
    valueCodeableConcept: CodeableConcept | None = None
    valueString: str | None = None


class ObservationModel(FhirResource):
    resource_type_name: ClassVar[str] = "Observation"

    status: str                       # 1..1
    code: CodeableConcept             # 1..1
    subject: Reference | None = None
    encounter: Reference | None = None
    category: list[CodeableConcept] = Field(default_factory=list)
    effectiveDateTime: datetime | None = None
    issued: datetime | None = None
    valueQuantity: Quantity | None = None
    valueCodeableConcept: CodeableConcept | None = None
    valueString: str | None = None
    component: list[ObservationComponent] = Field(default_factory=list)

    @field_validator("status")
    @classmethod
    def _status_in_value_set(cls, value: str) -> str:
        if value not in OBSERVATION_STATUS:
            raise ValueError(f"Observation.status {value!r} is outside the required value set")
        return value

    @field_validator("code")
    @classmethod
    def _code_has_a_coding(cls, value: CodeableConcept) -> CodeableConcept:
        if not value.coding:
            raise ValueError("Observation.code carries no coding")
        return value

    def model_post_init(self, _context: Any) -> None:
        """A measured number with no unit is not interpretable, so it is
        quarantined rather than loaded.

        FHIR allows it: `Quantity.value` is 0..1 and neither `unit` (the display
        form) nor `code` (the UCUM form) is required, so a bare `{"value": 21.998}`
        conforms. This warehouse does not accept it, and that is a policy rather
        than a spec reading — 5 mg and 5 g differ by a factor that kills people,
        and a warehouse that averages unlabelled numbers will eventually average
        those two.

        Five observations in a 925,283-resource extract take this shape: a Cobb
        angle and a body-position score carrying a value and a UCUM *system* but
        no unit at all. They are refused here, with the reason, and land in
        stg.ingest_rejects where they stay countable. They are not dropped, and
        they no longer take a fifteen-minute shred down with a check-constraint
        violation five hundred seconds in.
        """
        quantity = self.valueQuantity
        if quantity is not None and quantity.value is not None:
            if not quantity.unit and not quantity.code:
                raise ValueError(
                    "Observation.valueQuantity carries a value with neither unit "
                    "nor code; the measurement is not interpretable"
                )
        for index, component in enumerate(self.component):
            component_quantity = component.valueQuantity
            if component_quantity is not None and component_quantity.value is not None:
                if not component_quantity.unit and not component_quantity.code:
                    raise ValueError(
                        f"Observation.component[{index}].valueQuantity carries a "
                        "value with neither unit nor code"
                    )


PROCEDURE_STATUS = {
    "preparation", "in-progress", "not-done", "on-hold", "stopped",
    "completed", "entered-in-error", "unknown",
}


class ProcedureModel(FhirResource):
    resource_type_name: ClassVar[str] = "Procedure"

    status: str                       # 1..1
    subject: Reference                # 1..1
    # validate_default: pydantic skips validators on a field that was never
    # supplied, so an omitted code sailed past the check below while an
    # explicit null was caught. Both end at the same NOT NULL column.
    code: CodeableConcept | None = Field(default=None, validate_default=True)
    encounter: Reference | None = None
    performedDateTime: datetime | None = None
    performedPeriod: Period | None = None

    @field_validator("status")
    @classmethod
    def _status_in_value_set(cls, value: str) -> str:
        if value not in PROCEDURE_STATUS:
            raise ValueError(f"Procedure.status {value!r} is outside the required value set")
        return value

    @field_validator("code")
    @classmethod
    def _code_has_a_coding(cls, value: CodeableConcept | None) -> CodeableConcept | None:
        if value is None or not value.coding:
            raise ValueError("Procedure.code carries no coding")
        return value


MEDICATION_STATUS = {
    "active", "on-hold", "cancelled", "completed", "entered-in-error",
    "stopped", "draft", "unknown",
}
MEDICATION_INTENT = {
    "proposal", "plan", "order", "original-order", "reflex-order",
    "filler-order", "instance-order", "option",
}


class MedicationRequestModel(FhirResource):
    resource_type_name: ClassVar[str] = "MedicationRequest"

    status: str                       # 1..1
    intent: str                       # 1..1
    subject: Reference                # 1..1
    medicationCodeableConcept: CodeableConcept | None = None
    medicationReference: Reference | None = None
    encounter: Reference | None = None
    authoredOn: datetime | None = None
    requester: Reference | None = None

    @field_validator("status")
    @classmethod
    def _status_in_value_set(cls, value: str) -> str:
        if value not in MEDICATION_STATUS:
            raise ValueError(
                f"MedicationRequest.status {value!r} is outside the required value set")
        return value

    @field_validator("intent")
    @classmethod
    def _intent_in_value_set(cls, value: str) -> str:
        if value not in MEDICATION_INTENT:
            raise ValueError(
                f"MedicationRequest.intent {value!r} is outside the required value set")
        return value

    def model_post_init(self, _context: Any) -> None:
        # medication[x] is 1..1 and a choice: exactly one branch, never both,
        # never neither. This is the same rule as CK_medication_request_choice
        # in sql/04_norm.sql — enforced at the boundary so the failure names the
        # element rather than the constraint.
        chosen = sum(
            1 for branch in (self.medicationCodeableConcept, self.medicationReference)
            if branch is not None
        )
        if chosen != 1:
            raise ValueError(
                "MedicationRequest.medication[x] is 1..1: expected exactly one of "
                f"medicationCodeableConcept or medicationReference, found {chosen}"
            )


MODELS: dict[str, type[FhirResource]] = {
    "Patient": PatientModel,
    "Practitioner": PractitionerModel,
    "Organization": OrganizationModel,
    "Encounter": EncounterModel,
    "Condition": ConditionModel,
    "Observation": ObservationModel,
    "Procedure": ProcedureModel,
    "MedicationRequest": MedicationRequestModel,
}


def _first_error(exc: ValidationError) -> str:
    error = exc.errors()[0]
    location = ".".join(str(part) for part in error["loc"]) or "(root)"
    message = error["msg"]
    # Pydantic prefixes messages raised from validators; the prefix is noise in
    # a quarantine table a human is reading a thousand rows of.
    message = message.removeprefix("Value error, ")
    return f"{location}: {message}"


def validate_resource(payload: dict[str, Any]) -> FhirResource:
    """Validate one resource, or raise FhirValidationError with a readable reason.

    Unmodelled resource types are a rejection rather than a crash: a Bundle from
    a live server contains Provenance, CareTeam and a dozen other things, and
    "not modelled by this warehouse" is a legitimate, countable outcome.
    """
    if not isinstance(payload, dict):
        raise FhirValidationError("payload is not a JSON object")

    resource_type = payload.get("resourceType")
    if not resource_type:
        raise FhirValidationError("resource has no resourceType")

    model = MODELS.get(resource_type)
    if model is None:
        raise FhirValidationError(
            f"resource type {resource_type!r} is not modelled by this warehouse")

    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        raise FhirValidationError(_first_error(exc), detail=exc.json()) from exc
