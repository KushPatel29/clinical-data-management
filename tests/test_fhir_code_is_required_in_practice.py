"""A resource that cannot be coded is refused at the boundary, not at the load.

`Condition.code` and `Procedure.code` are 0..1 in FHIR R4, but the normalised
tables give them NOT NULL concept columns: an uncoded condition has no diagnosis
to join to. Both models carry a validator saying so.

The validator did not fire on the case that actually arrives. Pydantic skips
validators for a field that was never supplied, so `{"code": null}` and
`{"code": {"coding": []}}` were rejected while OMITTING the key entirely was
accepted with code=None — and that record then failed, or silently vanished,
during normalization instead. The rejection has to happen where the payload
enters, with a message naming the resource.

All four states are asserted for both resources, because the bug lived in the
gap between two of them.
"""
from __future__ import annotations

import pytest

from fhir.models import ConditionModel, ProcedureModel

CONDITION = {"resourceType": "Condition", "id": "c1", "subject": {"reference": "Patient/1"}}
PROCEDURE = {"resourceType": "Procedure", "id": "p1", "status": "completed",
             "subject": {"reference": "Patient/1"}}
VALID_CODE = {"coding": [{"system": "http://snomed.info/sct", "code": "44054006"}]}


def _payload(base: dict, code_state: str) -> dict:
    body = dict(base)
    if code_state == "omitted":
        body.pop("code", None)
    elif code_state == "null":
        body["code"] = None
    elif code_state == "empty-coding":
        body["code"] = {"coding": []}
    elif code_state == "valid":
        body["code"] = VALID_CODE
    return body


@pytest.mark.parametrize("model,base,name", [
    (ConditionModel, CONDITION, "Condition"),
    (ProcedureModel, PROCEDURE, "Procedure"),
])
@pytest.mark.parametrize("code_state", ["omitted", "null", "empty-coding"])
def test_an_uncodeable_resource_is_refused(model, base, name, code_state):
    with pytest.raises(Exception) as caught:
        model.model_validate(_payload(base, code_state))
    assert "coding" in str(caught.value), (
        f"{name} with code {code_state} was refused for some other reason — "
        f"the code check is not what stopped it"
    )


@pytest.mark.parametrize("model,base,name", [
    (ConditionModel, CONDITION, "Condition"),
    (ProcedureModel, PROCEDURE, "Procedure"),
])
def test_a_properly_coded_resource_still_loads(model, base, name):
    """The guard must not become a wall."""
    resource = model.model_validate(_payload(base, "valid"))
    assert resource.code is not None
    assert resource.code.coding[0].code == "44054006"
