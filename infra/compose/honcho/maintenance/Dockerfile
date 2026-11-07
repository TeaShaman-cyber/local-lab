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

ARG SUPERCRONIC_VERSION=v0.2.51

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates tzdata \
    && curl -fsSL -o /usr/local/bin/supercronic \
       "https://github.com/aptible/supercronic/releases/download/${SUPERCRONIC_VERSION}/supercronic-linux-amd64" \
    && chmod +x /usr/local/bin/supercronic \
    && apt-get purge -y curl \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

# The client library is the only runtime dependency: no ORM, no scheduler lib.
RUN pip install --no-cache-dir "psycopg[binary]>=3.1,<4"

WORKDIR /app
COPY tools/honcho/dedup.py /app/dedup.py
COPY infra/compose/honcho/maintenance/crontab /etc/cron.d/honcho-dedup

RUN mkdir -p /var/log/honcho && touch /var/log/honcho/dedup.log

# -passthrough-logs sends job output to stdout, where the container runtime
# collects it; there is no log file to rotate inside the image.
CMD ["/usr/local/bin/supercronic", "-passthrough-logs", "/etc/cron.d/honcho-dedup"]
