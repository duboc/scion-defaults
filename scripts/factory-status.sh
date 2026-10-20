#!/usr/bin/env bash
# Read-only snapshot of the factory, run ON the Hub VM as the Hub's OS user.
#
#   scripts/run-on-hub.sh scripts/factory-status.sh                 # agents, schedules, last 60 min of messages
#   scripts/run-on-hub.sh scripts/factory-status.sh --since 180 --panes 15
#
# Sections: agents (phase, activity, minutes since last activity), heartbeat schedules, recent agent messages,
# messages addressed to users (the PO inbox), failed message dispatches, git state of each project workspace,
# containers, and optionally the last N lines of every agent's terminal.
# It only reads: the Hub DB is opened read-only, git does not fetch, and tmux panes are captured, not touched.
#
# Projects: FACTORY_PROJECTS (comma-separated) or BUILD_PROJECT + EVAL_PROJECT from factory.env, else all.
set -euo pipefail

since=60
panes=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --since) since="$2"; shift 2 ;;
    --panes) panes="$2"; shift 2 ;;
    -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
[[ "$since" =~ ^[0-9]+$ && "$panes" =~ ^[0-9]+$ ]] || { echo "--since and --panes take whole numbers" >&2; exit 2; }

scion_home="${SCION_HOME:-$HOME/.scion}"
db="file:$scion_home/hub.db?mode=ro"
command -v sqlite3 >/dev/null || { echo "sqlite3 is required on the Hub VM (apt-get install sqlite3)" >&2; exit 1; }

projects_csv="${FACTORY_PROJECTS:-}"
if [[ -z "$projects_csv" ]]; then
  projects_csv="$(printf '%s,%s' "${BUILD_PROJECT:-}" "${EVAL_PROJECT:-}" | sed 's/^,//; s/,$//')"
fi
if [[ -z "$projects_csv" ]]; then
  projects_csv="$(sqlite3 "$db" "SELECT group_concat(slug, ',') FROM projects;")"
fi
IFS=',' read -r -a projects <<< "$projects_csv"
in_list=""
for p in "${projects[@]}"; do
  [[ "$p" =~ ^[a-z0-9][a-z0-9-]*$ ]] || { echo "invalid project slug: $p" >&2; exit 2; }
  in_list+="${in_list:+,}'$p'"
done

q() { sqlite3 -header -column "$db" "$1"; }
section() { printf '\n=== %s ===\n' "$1"; }

printf 'factory status at %s UTC, projects: %s\n' "$(date -u +%FT%TZ)" "$projects_csv"
pgrep -fa 'scion server start' >/dev/null && echo "scion server: running" || echo "scion server: NOT RUNNING"

section "agents (idle_min = minutes since the agent's last activity event)"
q "SELECT p.slug AS project, a.name, a.template,
          COALESCE(json_extract(a.applied_config, '\$.harnessConfig'), a.harness_config) AS harness,
          a.phase, a.activity,
          CAST((strftime('%s','now') - strftime('%s', substr(a.last_activity_event, 1, 19))) / 60 AS INTEGER) AS idle_min,
          substr(replace(COALESCE(NULLIF(a.task_summary, ''), a.message, ''), char(10), ' '), 1, 70) AS summary
   FROM agents a JOIN projects p ON p.id = a.project_id
   WHERE a.deleted_at IS NULL AND p.slug IN ($in_list)
   ORDER BY p.slug, a.name;"

section "schedules (heartbeats; times in UTC)"
q "SELECT p.slug AS project, s.name, s.cron_expr AS cron, s.status,
          substr(s.last_run_at, 1, 19) AS last_run, s.last_run_status AS last_status,
          s.run_count AS runs, s.error_count AS errors, json_extract(s.payload, '\$.agentName') AS agent
   FROM schedules s JOIN projects p ON p.id = s.project_id
   WHERE p.slug IN ($in_list) ORDER BY p.slug, s.name;"

section "messages in the last $since min (newest first)"
q "SELECT substr(m.created, 12, 8) AS utc, p.slug AS project, m.sender, m.recipient, m.type, m.dispatch_state AS state,
          substr(replace(m.msg, char(10), ' '), 1, 100) AS msg
   FROM messages m JOIN projects p ON p.id = m.project_id
   WHERE p.slug IN ($in_list) AND substr(m.created, 1, 19) >= datetime('now', '-$since minutes')
   ORDER BY m.created DESC LIMIT 60;"

section "messages to users in the last 24 h (the PO inbox)"
q "SELECT substr(m.created, 1, 19) AS utc, p.slug AS project, m.sender, m.type,
          substr(replace(m.msg, char(10), ' '), 1, 110) AS msg
   FROM messages m JOIN projects p ON p.id = m.project_id
   WHERE p.slug IN ($in_list) AND m.recipient LIKE 'user:%'
     AND substr(m.created, 1, 19) >= datetime('now', '-24 hours')
   ORDER BY m.created DESC LIMIT 20;"

section "failed message dispatches in the last 24 h"
q "SELECT substr(m.created, 1, 19) AS utc, p.slug AS project, m.sender, m.recipient,
          substr(COALESCE(m.dispatch_failure_reason, ''), 1, 80) AS reason
   FROM messages m JOIN projects p ON p.id = m.project_id
   WHERE p.slug IN ($in_list) AND m.dispatch_state = 'failed'
     AND substr(m.created, 1, 19) >= datetime('now', '-24 hours')
   ORDER BY m.created DESC LIMIT 20;"

section "project workspaces (no fetch: ahead/behind is relative to the last fetched origin)"
for p in "${projects[@]}"; do
  ws="$scion_home/projects/$p"
  if [[ ! -d "$ws/.git" ]]; then
    echo "$p: $ws is not a git checkout"
    continue
  fi
  branch="$(git -C "$ws" rev-parse --abbrev-ref HEAD 2>/dev/null || echo '?')"
  dirty="$(git -C "$ws" status --porcelain 2>/dev/null | wc -l | tr -d ' ')"
  counts="$(git -C "$ws" rev-list --left-right --count "HEAD...origin/$branch" 2>/dev/null || echo '? ?')"
  echo "$p: branch=$branch uncommitted_files=$dirty ahead/behind origin=${counts//$'\t'/\/}"
  git -C "$ws" log --oneline -3 --format='  %h %cr %s' 2>/dev/null || true
done

section "containers"
docker ps --format '{{.Names}}\t{{.Status}}' 2>/dev/null | grep -E "^($(IFS='|'; echo "${projects[*]}"))--" || echo "(none, or no docker access)"

if [[ "$panes" -gt 0 ]]; then
  for c in $(docker ps --format '{{.Names}}' 2>/dev/null | grep -E "^($(IFS='|'; echo "${projects[*]}"))--" || true); do
    section "pane $c (last $panes lines)"
    docker exec -u scion "$c" tmux capture-pane -p -t scion:0.0 -S "-$panes" 2>/dev/null | tail -n "$panes" || echo "(no tmux pane)"
  done
fi
