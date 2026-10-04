#!/usr/bin/env bash
set -euo pipefail
mkdir -p "$PGDATA"
chown postgres:postgres "$PGDATA"
chmod 700 "$PGDATA"
exec gosu postgres bash /replication/standby-run.sh
