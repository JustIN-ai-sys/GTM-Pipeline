-- Open pipeline by stage: how many deals, how much value, how stale.
-- Parameter: :as_of (YYYY-MM-DD).

SELECT
    COALESCE(s.position, 0) AS position,
    d.stage,
    COUNT(*) AS deals,
    ROUND(SUM(d.value), 0) AS value,
    ROUND(AVG(julianday(:as_of) - julianday(d.last_activity_date)), 1) AS avg_days_since_activity,
    SUM(CASE WHEN julianday(:as_of) - julianday(d.last_activity_date) > 30 THEN 1 ELSE 0 END)
        AS stale_deals
FROM deals AS d
LEFT JOIN stages AS s ON s.name = d.stage
WHERE d.status = 'open'
GROUP BY d.stage
ORDER BY position, d.stage;
