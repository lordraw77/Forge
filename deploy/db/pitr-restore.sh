#!/usr/bin/env bash
# Fetch a base backup into an EMPTY data directory and prepare recovery. Run as the postgres user
# in a container that has the WAL-G settings and the target volume at $PGDATA, then start the
# database normally: it replays the archived WAL up to the target and promotes.
#
#   pitr-restore.sh                              latest backup, replay all archived WAL
#   pitr-restore.sh --time '2026-10-22 09:30:00+00'   stop just before that moment (UTC advised)
#   pitr-restore.sh --backup base_000000010000000000000004 [--time ...]
set -euo pipefail

backup=LATEST
target=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --time) target="$2"; shift 2 ;;
    --backup) backup="$2"; shift 2 ;;
    *) echo "unknown argument $1" >&2; exit 2 ;;
  esac
done
: "${PGDATA:?}"
if [[ -n "$(ls -A "$PGDATA" 2>/dev/null)" ]]; then
  echo "refusing: $PGDATA is not empty (restore into a new volume)" >&2
  exit 1
fi

started=$(date +%s)
wal-g backup-fetch "$PGDATA" "$backup"
chmod 700 "$PGDATA"
{
  echo "restore_command = 'wal-g wal-fetch %f %p'"
  echo "recovery_target_action = 'promote'"
  echo "recovery_target_timeline = 'latest'"
  [[ -n "$target" ]] && echo "recovery_target_time = '$target'"
} >> "$PGDATA/postgresql.auto.conf"
touch "$PGDATA/recovery.signal"
echo "restored base backup $backup in $(( $(date +%s) - started )) s; start the database to replay WAL${target:+ up to $target}"
