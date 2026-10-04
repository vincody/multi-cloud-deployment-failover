#!/usr/bin/env bash
set -euo pipefail
entry='host replication replicator samenet scram-sha-256'
if ! grep -Fxq "$entry" "$PGDATA/pg_hba.conf"; then
  printf '\n%s\n' "$entry" >> "$PGDATA/pg_hba.conf"
fi
