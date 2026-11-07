# Scheduled Honcho maintenance (dedup of memory documents).
#
# Deliberately a Linux container: scheduling Python on Windows means dealing
# with console-subsystem interpreters, pythonw, Task Scheduler and flashing
# windows (see docs/reference/windows-scheduled-tasks.md). A container has none
# of that — `cron -f` runs in the foreground, output goes to the container log,
# and there is no desktop to flash on.
#
# Build context is the repository root, so the job script is the same file that
# lives in tools/ and is reviewed in pull requests.

FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends cron tzdata \
    && rm -rf /var/lib/apt/lists/*

# The client library is the only dependency: no ORM, no scheduler library.
RUN pip install --no-cache-dir "psycopg[binary]>=3.1,<4"

WORKDIR /app
COPY tools/honcho/dedup.py /app/dedup.py
COPY infra/compose/honcho/maintenance/crontab /etc/cron.d/honcho-dedup

RUN chmod 0644 /etc/cron.d/honcho-dedup \
    && mkdir -p /var/log/honcho \
    && touch /var/log/honcho/dedup.log

# -f keeps cron in the foreground so the container stays alive; -l 2 sends job
# output to stdout, where the container runtime collects it.
CMD ["cron", "-f", "-l", "2"]
