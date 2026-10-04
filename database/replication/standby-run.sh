#!/usr/bin/env bash
set -euo pipefail
if [ ! -f "$PGDATA/PG_VERSION" ]; then
  # Refuse partially initialized directories rather than deleting existing data.
  if [ -n "$(find "$PGDATA" -mindepth 1 -maxdepth 1 -print -quit)" ]; then
    echo 'Standby directory is not empty. Inspect it before retrying setup.' >&2
    exit 1
  fi
  pg_basebackup -h "${PGPRIMARY:-database}" -p 5432 -U replicator -D "$PGDATA" -R -X stream -c fast -w
fi
exec postgres -D "$PGDATA" -c hot_standby=on -c wal_level=replica -c max_wal_senders=10 -c wal_keep_size=128MB
