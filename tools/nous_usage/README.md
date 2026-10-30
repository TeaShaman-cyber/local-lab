# Nous credit monitor

`push_nous_usage.py` reads the authenticated Nous Portal account through Hermes'
stateful `hermes_cli.nous_account.get_nous_portal_account_info(force_fresh=True)`
and pushes only normalized credit availability and read-health metrics to the
existing Grafana Loki endpoint.

It never sends an inference request and never exports account identity, raw
claims, cookies, or tokens. Missing credit fields remain `*_known=0` and are
not converted to zero.

## Runtime

Windows Task Scheduler task: `Theseus Nous credits`, every 15 minutes, using
Hermes' Python environment:

`%LOCALAPPDATA%\\hermes\\hermes-agent\\venv\\Scripts\\python.exe`

The task is independent of the active Hermes model/provider. Current manual
verification: task result `0`, `source=account_api`, `fresh=True`, and Loki
read-back `metric=read_ok value=1`.

## Loki query

```logql
{service_name="nous", log_type="billing", job="nous-credits"}
```

The values are remaining credits, not historical spend. A `read_ok=0` sample
means the account read was stale or failed and must not be interpreted as a
zero balance.
