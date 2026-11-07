# Log pipelines: redaction, log-derived metrics, alerting

Depth behind the Loki section of SKILL.md. One `loki.process` stage can do both
jobs below, in this order.

## Why redact before shipping, not alarm afterwards

Persisted cloud logs are the exact artefact that gets bought in bulk and grepped
for keys years later. Once a value is stored, an alert tells you it already
happened; the value is out. So the value must be destroyed on the host and only
the *fact of an attempt* is allowed to travel.

A fair objection is "the same content already goes to model providers". The
honest answer is that the difference is **retention, not audience**: a provider
sees a request transiently, a log store keeps it for the full retention window
and serves it on query. Say that plainly when the user raises it, then do what
they choose and record the residual risk in the config comment.

## Redaction stage

```alloy
stage.replace {
  expression = "(ghp_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{20,}|glsa_[A-Za-z0-9]{30,}|glc_[A-Za-z0-9+/=]{30,}|sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|eyJ[A-Za-z0-9_-]{10,}[.][A-Za-z0-9_-]{10,}[.][A-Za-z0-9_-]{10,}|ya29[.][A-Za-z0-9_-]{20,}|1//[A-Za-z0-9_-]{20,})"
  replace    = "[REDACTED]"
}
```

Cover the shapes actually in play: GitHub (`ghp_`, `github_pat_`), Grafana Cloud
(`glsa_`, `glc_`), OpenAI-style (`sk-`), AWS (`AKIA`), JWT-like triples, and
**Google OAuth** (`ya29.` access tokens, `1//` refresh tokens) — rclone, gcloud
and any OAuth tool print those verbatim into command output. Every capture group
is replaced by `replace`, so one capture group must span the whole secret.

**Never escape a dot inside an Alloy string.** `\\.` in the config does not reach
the regex as `\.`: the string layer consumes one level, the compiled pattern ends
up matching a literal backslash, that alternative silently never fires, and
`alloy fmt` stays green the whole time. Write `[.]` instead — a character class
has no escaping to get wrong. Apply the same trick to any metacharacter you would
otherwise escape, and read the compiled pattern out of a failed reload message
rather than counting slashes in the file.

## Proving it works: canary, not assertion

Add a dedicated canary file to the same `path_targets` and drive it by hand:

1. Add `.../logs/secret-canary.log` with its own `log_type = "canary"` label.
2. Write one fake key **per token shape**. A shape you never exercised is a shape
   that may not be covered at all: one alternative matching says nothing about
   the others, and an accidentally-working alternative hides a broken one.
3. Query the canary stream and confirm every line reads `[REDACTED]`.
4. Query for the literal fake value across the whole stream and confirm **zero**
hits — this is the step that proves nothing leaked, and it is the one people skip.
5. Confirm the alert below moved to firing.
6. Truncate the canary afterwards so you are not running a permanently firing rule.

Keep the canary file in place: it makes the next verification free.

## Metrics from log lines

Agent logs already carry model, tokens and latency per call; turning them into
series is what lets LLM telemetry share a timeline with host telemetry.

```alloy
stage.regex {
  expression = "API call #(?P<apicall>\\d+): model=(?P<llm_model>\\S+).*?in=(?P<tok_in>\\d+) out=(?P<tok_out>\\d+).*?latency=(?P<llm_latency>[0-9.]+(?:ms|s))"
}

stage.metrics {
  metric.counter { name = "hermes_llm_calls_total"          source = "apicall"      action = "inc" }
  metric.gauge   { name = "hermes_llm_latency_seconds"      source = "llm_latency"  action = "set" }
  metric.counter { name = "hermes_llm_input_tokens_total"   source = "tok_in"       action = "add" }
  metric.counter { name = "hermes_llm_output_tokens_total"  source = "tok_out"      action = "add" }
}
```

- `action = add` converts the extracted value to a float and accumulates it —
counter semantics for token counts.
- `action = set` for the gauge (last call's latency).
- Duration strings are converted for you: `11.1s` becomes `11.1`, `0.5ms` becomes
`0.0005`.
- Labels are **inherited from the entry's label set**, not set on the metric.
Use `stage.labels` / `stage.static_labels` earlier if a metric needs a label.
- Only lines matching the regex carry the fields; entries without them add zero.

**These metrics live on Alloy's own `/metrics` endpoint and go nowhere by
themselves.** Add a scrape of Alloy itself, or the whole `stage.metrics` effort
is invisible in Grafana Cloud:

```alloy
prometheus.scrape "alloy_self" {
  targets         = [{ __address__ = "127.0.0.1:12345", job = "alloy-self" }]
  forward_to      = [prometheus.remote_write.cloud.receiver]
  scrape_interval = "30s"
}
```

Check locally before trusting the cloud: Alloy's `/metrics` shows
`loki_process_custom_<name>` — that prefix is the default and worth confirming
rather than assuming.

## Metrics from a one-shot job (scheduler in a container)

A maintenance job that runs once a night has no `/metrics` endpoint. The clean
route is one logfmt line per run, parsed into series — but six things bite, and
each one silently produces a green panel that protects nothing.

```alloy
stage.match {
  selector = "{service=\"dedup\"} |= \"event=run_finished\""

  stage.logfmt {
    mapping = { event = "", status = "", deleted = "", live = "", ts = "" }
  }

  stage.labels {                     // 2
    values = { status = "" }
  }

  stage.metrics {
    metric.counter {
      name = "dedup_runs_total"; prefix = "honcho_"
      source = "event"; value = "run_finished"; action = "inc"
      max_idle_duration = "720h"     // 1
    }
    metric.counter {
      name = "dedup_deleted_total"; prefix = "honcho_"
      source = "deleted"; action = "add"
      max_idle_duration = "720h"
    }
  }

  stage.match {                      // 7
    selector = "{service=\"dedup\"} |~ \"status=(ok|clean)\""
    stage.metrics {
      metric.gauge {
        name = "dedup_last_success_timestamp_seconds"; prefix = "honcho_"
        source = "ts"; action = "set"   // 3
        max_idle_duration = "720h"
      }
    }
  }
}
```

1. **`max_idle_duration` must exceed the run interval.** Default is `5m`; a
   nightly job is 24h apart, so the series is marked stale and every panel reads
   "no data" for the rest of the day.
2. **`stage.metrics` inherits labels from the entry, not from parsed fields.**
   Without `stage.labels` the runs counter has no `status` dimension and all
   outcomes collapse into one series.
3. **The timestamp must come from the event, not from the collector.** A gauge
   can only `set` an extracted value, and the obvious workaround —
   `stage.template { template = "{{ now | unixEpoch }}" }` — stamps the time of
   *parsing*. If Alloy was stopped while Docker kept writing, queued lines are
   read on restart and an old failure looks fresh (and an old success looks
   fresh, hiding a dead scheduler). Have the producer print `ts=<unix seconds>`.
4. **`increase()` on the first sample is 0.** A status-specific counter is
   created by the very first run with that outcome, holding 1 from then on, so
   `increase(...[25h]) > 0` never fires on an isolated failure — precisely the
   case the rule exists for. Alert on a timestamp gauge instead:
   `time() - max(<last_failure_timestamp>) < 86400`.
5. **Presence outlives the event.** A counter stays scraped until
   `max_idle_duration` expires, so "the series exists" would keep a rule firing
   for days after triage. Only a timestamp answers *when*.
6. **`noDataState=Ok` hides a dead collector.** "No data" means both "laptop
   off" and "the pipeline broke". Split them: require the host to be visible —
   `(time() - max(last_success) > 108000) and on() (max(up{job="alloy-self"}) == 1)`.
   When Alloy is down, `up` is empty, the rule falls to no-data and stays quiet;
   that is honest, not healthy.
7. **Success has several shapes.** A job that removed nothing and a job that
   failed are different outcomes (`ok` vs `clean`), and matching only one freezes
   the success gauge on every quiet night.

**Running the job by hand so it lands in Loki.** `docker compose exec` does not
write to the container log — those belong to PID 1 — and `docker compose run
--rm` disappears before Alloy's 30s discovery cycle reads it. The only manual
run that is visible in Grafana:

```bash
docker compose exec dedup sh -c 'python /app/dedup.py >> /proc/1/fd/1 2>&1'
```

## Alerting on the redaction marker

The signal is the marker itself, so no extra Alloy counter is needed:

```logql
sum(count_over_time({job="hermes"} |= "[REDACTED]" [5m]))
```

Creating it, in the order that works on Grafana Cloud:

1. `managed_by="datasource"` **fails**:
   `no compatible Mimir/Cortex or Loki ruler datasource found for creation`.
   Grafana Cloud's Loki datasource exposes no ruler route. Do not retry it.
2. Create a **folder** first — `mcp__grafana__create_folder`.
   A rule with `folder_uid` set to the root is rejected:
   `folderUID cannot be the root folder`.
3. Create the rule Grafana-managed with three data steps, because a raw log
   query returns a series and the threshold only accepts reduced data:
   `A` = Loki query with `instant: true`, `B` = `__expr__` `type: reduce`
   (`reducer: last`), `C` = `__expr__` `type: threshold` (`gt 0`),
   and `condition: "C"`.
   Without step `B` the rule evaluates to
   `invalid format of evaluation results ... only reduced data can be alerted on`.
4. Set `no_data_state` and `exec_err_state` to `OK` so an empty window is not an
   alarm, and `for: "0s"` because a leaked key is not something to wait out.
5. Evaluation runs on the rule group interval (~1 min). A rule created a moment
   ago reports `state: unknown` with `last_evaluation: null` — that is not
   failure, it is not-yet-evaluated. Wait one cycle, then read it back with
   `alerting_rules_read(operation="get", rule_uid=...)` and expect
   `state: firing` while the canary line is inside the window.

A firing result with `value: "1e+00"` on a `count_over_time` threshold means one
matching log line in the window — the number is a count, not a severity.
