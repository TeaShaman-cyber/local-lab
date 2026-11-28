-- Analysis-only projection of the pinned f4212a7 EC tach assessment.
-- Keep the complete observed words and readiness flags in polls.
CREATE OR REPLACE VIEW poll_tach_quality AS
WITH qualified AS (
    SELECT *,
        coalesce(ok=1 AND ordered=1 AND epoch=poll_epoch
            AND begin_ms>0 AND begin_ms<=end_ms AND end_ms<=now
            AND now-begin_ms<=2000, false) AS fresh,
        coalesce(lo_a=1 AND hi_a=1 AND rpm_a BETWEEN 0 AND 8191, false) AS tach_a_valid,
        coalesce(lo_b=1 AND hi_b=1 AND rpm_b BETWEEN 0 AND 8191, false) AS tach_b_valid
    FROM polls
), compared AS (
    SELECT *, CASE WHEN tach_a_valid AND tach_b_valid THEN abs(rpm_a-rpm_b) END AS tach_delta
    FROM qualified
), classified AS (
    SELECT *, CASE
        WHEN NOT fresh THEN 'stale'
        WHEN NOT tach_a_valid AND NOT tach_b_valid THEN 'unknown'
        WHEN tach_a_valid AND tach_b_valid AND (tach_delta>200 OR (rpm_a=0)<>(rpm_b=0)) THEN 'disagree'
        WHEN tach_a_valid AND tach_b_valid THEN 'dual'
        ELSE 'single'
    END AS tach_quality
    FROM compared
)
SELECT *,
    CASE WHEN tach_quality IN ('single','dual') THEN
        CASE WHEN tach_b_valid THEN rpm_b ELSE rpm_a END
    END AS tach_rpm,
    CASE WHEN tach_quality IN ('single','dual') THEN
        CASE WHEN tach_b_valid THEN 'B' ELSE 'A' END
    END AS tach_source
FROM classified;
