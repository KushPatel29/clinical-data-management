# DAX measures

The semantic layer a Power BI model sits on, and the filter-context decision
behind each measure.

**Scope, stated first.** This is a documented measure specification, not a
shipped `.pbix`. Every measure below is written against the `dw.vw_*` views in
this repository and every one of them has a T-SQL equivalent in
[`validation.sql`](validation.sql) that returns the same number, so the DAX can
be checked against the warehouse rather than trusted. What is *not* here is a
Power BI file with these measures loaded into it — the other repositories in
this portfolio carry hand-authored PBIP reports where that was the point; here
the point is the model underneath, and a `.pbix` would add a binary artefact
without adding a verified claim.

## Model shape

Import mode, one table per view:

| Table | Source view | Grain |
|---|---|---|
| `Encounter` | `dw.vw_encounter` | one encounter |
| `Observation` | `dw.vw_observation_result` | one result |
| `MedicationOrder` | `dw.vw_medication_order` | one order |
| `Diagnosis` | `dw.vw_diagnosis` | one recorded diagnosis |
| `Patient` | `dw.vw_patient_panel` | one patient, current version |
| `Date` | `dw.DimDate` | one day |

`Date` is marked as a date table on `full_date`. Relationships from each fact to
`Date` are single-direction, many-to-one, and only **one is active per table** —
`Encounter[encounter_date]` is the active relationship, and anything that needs
to filter by discharge instead goes through `USERELATIONSHIP`, so a measure
cannot silently pick a different date column than the one the report author
thinks it is using.

---

## 1. Encounter volume

```dax
Encounters =
COUNTROWS ( Encounter )
```

```dax
Encounters YoY % =
VAR Current = [Encounters]
VAR Prior =
    CALCULATE ( [Encounters], SAMEPERIODLASTYEAR ( 'Date'[full_date] ) )
RETURN
    DIVIDE ( Current - Prior, Prior )
```

**Filter context decisions.**

`COUNTROWS` rather than `SUM ( Encounter[encounter_count] )`. Both are correct
here because `encounter_count` is always 1, but `COUNTROWS` cannot be broken by
someone later making the column additive at a different grain. The column exists
in the fact table for tools that require a measure column; the measure does not
depend on it.

`SAMEPERIODLASTYEAR` needs a marked date table with a contiguous date range —
which is why `dw.DimDate` is generated day by day from 1900 with no gaps rather
than derived from the dates that happen to appear in the facts. A date table
built with `DISTINCT` over a fact column has holes on days nothing happened, and
every time-intelligence function silently returns wrong answers on those days.

`DIVIDE` rather than `/`. A division by zero in DAX with `/` returns infinity
and renders as `∞`; `DIVIDE` returns `BLANK()`, which a visual omits. In the
first month of the warehouse's history `Prior` is blank for every row.

---

## 2. 30-day readmission rate

```dax
Inpatient Discharges =
CALCULATE ( COUNTROWS ( Encounter ), Encounter[is_inpatient] = TRUE () )
```

```dax
Readmissions 30d =
CALCULATE ( COUNTROWS ( Encounter ), Encounter[is_readmission_30d] = TRUE () )
```

```dax
Readmission Rate 30d =
DIVIDE ( [Readmissions 30d], [Inpatient Discharges] )
```

**Filter context decisions.**

**The denominator is inpatient discharges, not all encounters.** This is the
single most common way this measure is quoted wrong. Including ambulatory visits
in the denominator makes the rate roughly an order of magnitude smaller and
comparable to nothing — not to the hospital's own prior year, not to any
published benchmark.

`CALCULATE` with a boolean filter rather than `CALCULATETABLE ( FILTER ( ... ) )`.
The boolean form is converted internally to `FILTER ( ALL ( Encounter[is_inpatient] ), ... )`,
which **replaces** any existing filter on that column. That is what makes the
measure behave correctly when the report already has a care-setting slicer
applied: an "Ambulatory" slicer plus this measure returns blank, not a
misleading rate computed over the ambulatory rows. If the intent were to
*intersect* with the slicer instead, `KEEPFILTERS` is the modifier that says so —
and the fact that these two behaviours differ is precisely why the choice
belongs in a document.

**The flag is computed at load time, not here.** `is_readmission_30d` needs the
prior inpatient discharge for the same patient, which in DAX is an `EARLIER` or
a window function over a self-referencing table and is slow at every grain. The
warehouse computes it once with `LAG`. The DAX reads a flag.

**The definition is written down.** "30-day readmission" means at least four
different things in four different hospitals. Here it is: *an inpatient
admission whose start is within 30 days of the discharge of that patient's
previous inpatient encounter.* Not all-cause-any-setting; not
index-admission-anchored; no planned-readmission exclusions, because this
warehouse has no field that identifies a planned admission and inventing one
would be inventing a clinical claim.

---

## 3. Average length of stay

```dax
Average Length of Stay =
CALCULATE (
    AVERAGE ( Encounter[length_of_stay_days] ),
    Encounter[is_inpatient] = TRUE ()
)
```

```dax
Median Length of Stay =
CALCULATE (
    MEDIAN ( Encounter[length_of_stay_days] ),
    Encounter[is_inpatient] = TRUE ()
)
```

**Filter context decisions.**

`AVERAGE` ignores blanks, and that is the correct behaviour here rather than a
convenience: an encounter with no discharge date is still open, and an open stay
has no length yet. Coalescing it to zero would drag the mean down by however
many patients are currently admitted. The null rate on this column has a
threshold in the data quality suite for the same reason.

**Both mean and median are published, and the median is the honest one.** Length
of stay is strongly right-skewed — a handful of very long stays pull the mean
above the point where most of the distribution sits. A dashboard that shows only
the mean invites the reader to treat it as typical. `MEDIAN` in DAX is an
expensive operator over a large fact table, which is the trade being made.

The `is_inpatient` filter, again with `CALCULATE`'s replacing semantics: an
ambulatory encounter has a length of stay measured in minutes and averaging it
with an inpatient stay measured in days produces a number that describes
neither.

---

## 4. Active patient panel

```dax
Active Patients =
CALCULATE ( COUNTROWS ( Patient ), Patient[is_active] = TRUE () )
```

```dax
Patients Seen =
DISTINCTCOUNT ( Encounter[patient_id] )
```

**Filter context decisions.**

These are two different questions and they are deliberately two measures.
`Active Patients` is a property of the *panel* — how many people this
organisation is responsible for — and it does not respond to a date slicer,
because a panel is a standing population, not a period measure. `Patients Seen`
is a period measure and does respond.

Putting both on one page and letting the reader see they differ is better than
picking one and letting them assume it answers both.

`DISTINCTCOUNT ( Encounter[patient_id] )` on the **business key**, not on
`patient_key`. This matters because `DimPatient` is Type 2: a patient who moved
mid-year has two surrogate keys, and counting those would count the patient
twice. This is the specific trap Type 2 sets for every distinct count in the
model, and the reason `patient_id` is carried onto `dw.vw_encounter` at all.

**`is_active` is anchored to the data, not to today.** The underlying view
defines active as "seen in the 24 months before the warehouse's most recent
encounter". A warehouse loaded from a two-year extract has no encounters in the
last month, and a panel measured against `TODAY()` would report zero the day
after the extract was taken.

---

## 5. Abnormal result rate

```dax
Numeric Results =
CALCULATE ( COUNTROWS ( Observation ), Observation[is_numeric] = TRUE () )
```

```dax
Statistical Outlier Results =
CALCULATE (
    COUNTROWS ( Observation ),
    Observation[is_statistical_outlier] = TRUE ()
)
```

```dax
Statistical Outlier Rate =
DIVIDE ( [Statistical Outlier Results], [Numeric Results] )
```

**Filter context decisions.**

**The name is `Statistical Outlier Rate`, not `Abnormal Result Rate`, and that is
the most important decision on this page.** An abnormal result is a clinical
judgement against a reference range. This warehouse has no reference ranges:
Synthea does not emit `Observation.referenceRange`, and inventing thresholds for
haemoglobin or creatinine would be inventing clinical claims in a repository
whose entire premise is that claims are verified.

What the warehouse can compute is whether a result sits outside the 5th–95th
percentile of the results it holds for that same LOINC code. That is a
legitimate data quality and outlier signal, it is genuinely useful for spotting
a mis-calibrated analyser or a unit conversion error, and it is not a medical
finding. The distinction is carried in the column name, the view name, the
measure name and the visual title, so that no layer of the stack is the one
place where it quietly becomes "abnormal".

The percentile band is **per code**, not global — comparing a systolic blood
pressure to the distribution of every numeric result in the warehouse is
meaningless — and codes with fewer than 30 results are excluded, because a 5th
percentile computed from four values is noise.

`is_numeric` in the denominator. A result recorded as a coded concept
("Never smoker") has no percentile and belongs in neither the numerator nor the
denominator; leaving it in the denominator understates the rate by whatever
proportion of results are categorical.

---

## Checking these against the warehouse

Every measure above has a T-SQL equivalent in [`validation.sql`](validation.sql).
Run it and compare: a DAX measure that disagrees with the SQL is a measure with a
filter-context bug, and finding out from a query is cheaper than finding out from
a director.

```bash
sqlcmd -S localhost -d ClinicalWarehouse -E -i powerbi/validation.sql
```
