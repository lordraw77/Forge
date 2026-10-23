#!/usr/bin/env bash
# Base backups for point-in-time recovery: the first as soon as the database answers and none
# exists, then every BACKUP_INTERVAL_HOURS (default 24), keeping the last BACKUP_RETAIN_FULL full
# backups (default 7) and the WAL they need. WAL itself is archived continuously by the database
# (`archive_command = wal-g wal-push %p`); this loop only takes the base backups and prunes.
# Runs next to the database, sharing its data directory (read-only) and talking to it over TCP.
set -euo pipefail

interval=$(( ${BACKUP_INTERVAL_HOURS:-24} * 3600 ))
retain="${BACKUP_RETAIN_FULL:-7}"
log() { printf '%s backup: %s\n' "$(date -u +%FT%TZ)" "$*"; }

until pg_isready -h "${PGHOST:?}" -U "${PGUSER:-postgres}" -q; do sleep 2; done

take() {
  log "base backup starting"
  if wal-g backup-push "${PGDATA:?}"; then
    log "base backup done"
    wal-g delete retain FULL "$retain" --confirm && log "retention applied (last $retain full backups)"
  else
    log "base backup FAILED"
    return 1
  fi
}

# Nothing archived yet (first start): take one now. A failure is retried in a minute, not an hour.
if ! wal-g backup-list 2>/dev/null | grep -q '^base_'; then
  until take; do sleep 60; done
fi
while true; do
  sleep "$interval"
  take || log "will try again at the next interval"
done
