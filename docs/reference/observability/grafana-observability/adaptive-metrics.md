# Adaptive Metrics: trimming active series

Adaptive Metrics does not delete series; it writes **aggregation rules** that collapse
label dimensions nobody queries. Rules are reversible, but they change what is stored,
so treat the apply step as a real mutation.

Reach it at `<stack>.grafana.net` → the "may reduce your monthly bill" banner →
*View Recommendations*, which lands on
`/a/grafana-adaptive-metrics-app/rule-management`.

## Procedure

1. Read the table: `Metric`, `Received series`, `Current rule impact`,
   `Recommendation impact`.
2. Select all, then **uncheck anything that feeds an alert or a dashboard panel you
   rely on**. Percentile and phase-duration buckets that back latency alerts are the
   usual exceptions — collapsing their labels makes the alert expression miss.
3. Verify the selection by reading the checkboxes back, not by trusting the count
   label: return `[{label, checked}]` for each one you touched. The header counter
   can lag.
4. `auto-apply` on the toolbar is a **menu trigger**, not an action. Its menu holds
   *Apply selected recommendations* — that is the one that applies.
5. The confirmation dialog states the aggregate effect ("27 changes will be applied",
   "−1 134 time series"). Read the number, press Apply, and **report that number** as
   the result.
6. Re-snapshot after a pause: the table re-renders empty for a moment. Applied rows
   then read `0` in `Current rule impact` with recommendation `Keep rule`; skipped rows
   still show their negative impact and `New rule`.

## Why the savings number differs from the banner

Excluding a few recommendations lowers the total (a banner promising ~1.3k series
becomes ~1.1k applied). That is expected, not a partial failure — say which classes you
kept and why.

## Budget context

Free tier is 10k active series over 14 days; a 14-day Pro trial lifts the limits and
then drops back automatically. Trim before the trial ends, not after the cap is hit.
