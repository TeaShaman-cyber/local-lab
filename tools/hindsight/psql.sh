#!/bin/sh
# psql wrapper for the embedded pg0 instance inside the hindsight-api container.
#
# Why it exists: the instance has a password, which lives in instance.json
# inside the container. Passing it on a command line would put it in ps and in
# shell history. Reading it inside the container avoids both.
#
# Usage:
#   docker cp tools/hindsight/psql.sh hindsight-api:/tmp/q.sh
#   docker exec hindsight-api /tmp/q.sh -c "SELECT count(*) FROM llm_requests;"
#
# Read-only by habit: these queries inspect state. Nothing here writes.
PW=$(/app/api/.venv/bin/python -c "import json;print(json.load(open('/home/hindsight/.pg0/instances/hindsight/instance.json'))['password'])")
exec env PGPASSWORD="$PW" /home/hindsight/.pg0/installation/18.1.0/bin/psql -U hindsight -d hindsight "$@"
