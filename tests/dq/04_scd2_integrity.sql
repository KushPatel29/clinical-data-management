-- @check: SCD-01
-- @severity: error
-- @description: DimPatient's Type 2 history is well formed.
--
-- Five ways a slowly changing dimension goes wrong, four of which produce no
-- error and a report that is quietly double-counting.
--
--   overlapping_versions   two versions of one patient valid on the same day.
--                          A BETWEEN join then matches both and every measure
--                          for that patient doubles for the overlap.
--   multiple_current       two rows flagged is_current. A filtered unique index
--                          makes this impossible, so a row here means the index
--                          is missing -- which is worth knowing.
--   no_current             a patient with history and no open version. Every
--                          fact dated after the last close falls off the end.
--   gap_between_versions   version 1 closes before version 2 opens, leaving
--                          days where the patient does not exist. Facts on
--                          those days land on the Unknown member.
--   inverted_range         effective_to before effective_from.
--
-- The gap check is the subtle one: closing a version at the change date rather
-- than the day before it produces an overlap, and closing it two days early
-- produces a gap. Only "day before" is right, and only these two checks
-- together pin it.
WITH versions AS (
    SELECT patient_key, patient_id, effective_from, effective_to, is_current, version_number,
           LEAD(effective_from) OVER (PARTITION BY patient_id ORDER BY effective_from)
               AS next_effective_from
    FROM dw.DimPatient
    WHERE patient_key <> -1
),
counts AS (
    SELECT patient_id,
           COUNT(*)                              AS version_count,
           SUM(CAST(is_current AS INT))          AS current_count
    FROM dw.DimPatient
    WHERE patient_key <> -1
    GROUP BY patient_id
)
SELECT 'overlapping_versions' AS failure, v.patient_id, v.patient_key,
       CAST(v.effective_from AS VARCHAR(10)) AS detail_1,
       CAST(v.effective_to AS VARCHAR(10))   AS detail_2,
       CAST(v.next_effective_from AS VARCHAR(10)) AS detail_3
FROM versions AS v
WHERE v.next_effective_from IS NOT NULL
  AND v.effective_to >= v.next_effective_from

UNION ALL
SELECT 'gap_between_versions', v.patient_id, v.patient_key,
       CAST(v.effective_to AS VARCHAR(10)),
       CAST(v.next_effective_from AS VARCHAR(10)),
       CAST(DATEDIFF(DAY, v.effective_to, v.next_effective_from) AS VARCHAR(10))
FROM versions AS v
WHERE v.next_effective_from IS NOT NULL
  AND DATEDIFF(DAY, v.effective_to, v.next_effective_from) <> 1

UNION ALL
SELECT 'multiple_current', c.patient_id, NULL,
       CAST(c.current_count AS VARCHAR(10)), NULL, NULL
FROM counts AS c WHERE c.current_count > 1

UNION ALL
SELECT 'no_current', c.patient_id, NULL,
       CAST(c.version_count AS VARCHAR(10)), NULL, NULL
FROM counts AS c WHERE c.current_count = 0

UNION ALL
SELECT 'inverted_range', v.patient_id, v.patient_key,
       CAST(v.effective_from AS VARCHAR(10)), CAST(v.effective_to AS VARCHAR(10)), NULL
FROM versions AS v WHERE v.effective_to < v.effective_from

UNION ALL
-- A closed version must not be flagged open and vice versa. Enforced by a check
-- constraint; verified here because a constraint someone disabled is a
-- constraint that is not enforcing anything.
SELECT 'current_flag_disagrees_with_end_date', v.patient_id, v.patient_key,
       CAST(v.is_current AS VARCHAR(10)), CAST(v.effective_to AS VARCHAR(10)), NULL
FROM versions AS v
WHERE (v.is_current = 1 AND v.effective_to <> '9999-12-31')
   OR (v.is_current = 0 AND v.effective_to = '9999-12-31')

UNION ALL
-- Versions must be numbered 1..n with no repeats and no holes, or "the second
-- version of this patient" stops being answerable.
SELECT 'version_numbering', x.patient_id, NULL,
       CAST(x.version_count AS VARCHAR(10)), CAST(x.max_version AS VARCHAR(10)),
       CAST(x.distinct_versions AS VARCHAR(10))
FROM (
    SELECT patient_id, COUNT(*) AS version_count, MAX(version_number) AS max_version,
           COUNT(DISTINCT version_number) AS distinct_versions
    FROM dw.DimPatient WHERE patient_key <> -1 GROUP BY patient_id
) AS x
WHERE x.version_count <> x.max_version OR x.version_count <> x.distinct_versions;
