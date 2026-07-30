# Data Validation Specification — SYN-2026-01

| Check | Form | Severity | Description | Protocol |
|---|---|---|---|---|
| `EC-REQ` | * | query | A field marked required on the CRF is blank. | Section 8.1 Data collection |
| `EC-RANGE` | * | query | A numeric value falls outside the clinically plausible range defined on the CRF. | Section 8.3 Range checks |
| `EC-CODE` | * | query | A coded field contains a value outside its controlled terminology. | Section 8.2 Controlled terminology |
| `EC-VS-03` | VS | query | Systolic blood pressure is not greater than diastolic. Almost always a transcription swap. | Section 8.3 Vital signs consistency |
| `EC-AE-01` | AE | query | Adverse event end date precedes its start date. | Section 9.2 Adverse event reporting |
| `EC-AE-04` | AE | query | Adverse event start date precedes the date of informed consent. Such an event is pre-existing and belongs on Medical History, not Adverse Events. | Section 9.1 Definition of an adverse event |
| `EC-AE-05` | AE | query | A serious adverse event has no action recorded for study drug. | Section 9.4 Serious adverse events |
| `EC-AE-06` | AE | warning | Adverse event outcome is 'recovered/resolved' but no end date has been entered. | Section 9.2 Adverse event reporting |
| `EC-DS-01` | DS | query | Subject did not complete the study but no reason for discontinuation was recorded. | Section 7.4 Subject withdrawal |
| `EC-VISIT-01` | * | warning | Visit performed outside the protocol-defined window. A protocol deviation, recorded rather than corrected. | Section 6.1 Schedule of assessments |

## Query text issued to sites

**EC-REQ** — A field marked required on the CRF is blank.

> {label} is required but was not entered. Please complete this field, or confirm the assessment was not performed and provide the reason.

**EC-RANGE** — A numeric value falls outside the clinically plausible range defined on the CRF.

> {label} was recorded as {value} {unit}, which is outside the expected range of {low} to {high} {unit}. Please verify against source and correct, or confirm the value is accurate.

**EC-CODE** — A coded field contains a value outside its controlled terminology.

> {label} contains '{value}', which is not a permitted value. Please select one of the permitted values: {allowed}.

**EC-VS-03** — Systolic blood pressure is not greater than diastolic. Almost always a transcription swap.

> Systolic BP ({sbp} mmHg) is not greater than diastolic BP ({dbp} mmHg). Please verify against source; if the values were transposed, please correct both.

**EC-AE-01** — Adverse event end date precedes its start date.

> The adverse event end date ({end}) is before the start date ({start}). Please verify both dates against source and correct.

**EC-AE-04** — Adverse event start date precedes the date of informed consent. Such an event is pre-existing and belongs on Medical History, not Adverse Events.

> The adverse event start date ({start}) is before the date of informed consent ({consent}). Please confirm the event start date, or confirm the event should be recorded as medical history instead.

**EC-AE-05** — A serious adverse event has no action recorded for study drug.

> This adverse event is recorded as serious, but no action taken with study drug has been entered. Please complete the action taken field.

**EC-AE-06** — Adverse event outcome is 'recovered/resolved' but no end date has been entered.

> The outcome is recorded as recovered/resolved but the event end date is blank. Please enter the date the event resolved.

**EC-DS-01** — Subject did not complete the study but no reason for discontinuation was recorded.

> The subject is recorded as not having completed the study, but no reason for discontinuation was entered. Please provide the primary reason.

**EC-VISIT-01** — Visit performed outside the protocol-defined window. A protocol deviation, recorded rather than corrected.

> The {visit} visit was performed on {actual}, which is {days} day(s) outside the protocol window of +/-{window} days around study day {target}. Please confirm the visit date and complete a protocol deviation form.

