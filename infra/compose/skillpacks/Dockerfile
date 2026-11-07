# Skill-pack freshness check on a schedule.
#
# Same reasoning as infra/compose/honcho/maintenance: scheduling Python on
# Windows means Task Scheduler, console-subsystem interpreters and flashing
# windows (docs/reference/windows-scheduled-tasks.md). A Linux container has no
# desktop to flash on, `supercronic` runs in the foreground, and output goes to
# the container log where Alloy already collects it.
#
# Build context is the repository root so the job script is the reviewed file in
# tools/, not a copy that drifts.

FROM python:3.13-slim

ARG SUPERCRONIC_VERSION=v0.2.51

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates git tzdata \
    && rm -rf /var/lib/apt/lists/*

# The clones arrive through bind mounts from a Windows host, so their owner does
# not match the container user. Without this, every `git fetch` fails with
# "dubious ownership" and the check reports every pack as unknown.
RUN git config --system --add safe.directory '*'

RUN curl -fsSLo /usr/local/bin/supercronic \
      "https://github.com/aptible/supercronic/releases/download/${SUPERCRONIC_VERSION}/supercronic-linux-amd64" \
    && chmod +x /usr/local/bin/supercronic

# The job script is NOT copied into the image: the repository is mounted at
# /repo, so the container runs the reviewed file itself. A baked copy would keep
# running the old version after every edit until someone remembered to rebuild.
COPY infra/compose/skillpacks/crontab /etc/cron.d/skillpacks

ENTRYPOINT ["supercronic", "-passthrough-logs", "/etc/cron.d/skillpacks"]
