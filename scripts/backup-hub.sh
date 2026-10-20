#!/usr/bin/env bash
# Consistent online backup of Scion Hub database and state.
#
# Runs on the Hub VM as HUB_OS_USER (scion).
#
# Usage:
#   backup-hub.sh                  # perform one backup
#   backup-hub.sh --install-cron   # install to ~/.scion/backup-hub.sh and crontab, then run backup
#
# Configurable via environment variables:
#   BACKUP_DIR     Directory where backups are stored (default: $HOME/backups)
#   RETENTION_DAYS Number of days to retain backups (default: 14)

set -euo pipefail
umask 077
cd "$HOME"

BACKUP_DIR="${BACKUP_DIR:-$HOME/backups}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"

if [[ "${1:-}" == "--install-cron" ]]; then
  mkdir -p "$HOME/.scion"
  cp "$0" "$HOME/.scion/backup-hub.sh"
  chmod 700 "$HOME/.scion/backup-hub.sh"

  cron_cmd="$HOME/.scion/backup-hub.sh >> $HOME/.scion/backup.log 2>&1"
  cron_entry="15 4 * * * $cron_cmd"
  current_crontab="$(crontab -l 2>/dev/null || true)"

  if echo "$current_crontab" | grep -Fq "$HOME/.scion/backup-hub.sh"; then
    echo "Cron job already present in crontab."
  else
    (echo "$current_crontab" | grep -v '^$'; echo "$cron_entry") | crontab -
    echo "Installed cron job: $cron_entry"
  fi
fi

mkdir -p "$BACKUP_DIR"
ts="$(date -u +%Y%m%d-%H%M%S)"

# 1. Consistent online SQLite backup
db_file="$HOME/.scion/hub.db"
db_backup="$BACKUP_DIR/hub-$ts.db"
if [[ -f "$db_file" ]]; then
  sqlite3 "$db_file" ".backup '$db_backup'"
  gzip -f "$db_backup"
fi

# 2. Archive Hub state files and directories
candidate_paths=(
  ".scion/hub.env"
  ".scion/settings.yaml"
  ".scion/sync.env"
  ".scion/harness-configs"
  ".scion/workspace-storage"
  ".scion/projects"
)

existing_paths=()
for rel in "${candidate_paths[@]}"; do
  if [[ -e "$HOME/$rel" ]]; then
    existing_paths+=("$rel")
  fi
done

state_tar="$BACKUP_DIR/state-$ts.tgz"
if [[ ${#existing_paths[@]} -gt 0 ]]; then
  tar -czf "$state_tar" -C "$HOME" \
    --exclude='node_modules' \
    --exclude='.venv' \
    "${existing_paths[@]}"
fi

# 3. Prune old backups older than RETENTION_DAYS days
find "$BACKUP_DIR" -maxdepth 1 -type f \( -name 'hub-*.db.gz' -o -name 'state-*.tgz' \) -mtime +"$RETENTION_DAYS" -delete

# 4. Print concise summary line with timestamp and file sizes
db_sz="$(ls -lh "$BACKUP_DIR/hub-$ts.db.gz" 2>/dev/null | awk '{print $5}' || echo "N/A")"
state_sz="$(ls -lh "$state_tar" 2>/dev/null | awk '{print $5}' || echo "N/A")"

echo "[$ts] Backup completed: hub-$ts.db.gz ($db_sz), state-$ts.tgz ($state_sz) in $BACKUP_DIR (retention: ${RETENTION_DAYS}d)"
