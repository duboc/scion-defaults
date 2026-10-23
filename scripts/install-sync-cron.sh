#!/usr/bin/env bash
# Install (or update) the Hub -> Firestore agent-status sync on the Hub VM. Runs on YOUR machine.
#
#   scripts/install-sync-cron.sh            # install/update and run the sync once
#   scripts/install-sync-cron.sh --dry-run  # print the remote script, change nothing
#
# On the VM, as HUB_OS_USER, it writes ~/factory-sync/sync_factory_agents.py (from sync-factory-agents.py),
# ~/factory-sync/factory_store.py (the Firestore REST client it imports), and ~/factory-sync/sync.env, replaces any previous factory-sync crontab line with
#   * * * * * set -a; . ~/factory-sync/sync.env; set +a; /usr/bin/python3 ~/factory-sync/sync_factory_agents.py
# (logging to ~/.scion/factory-sync.log), then runs one sync so you see the result.
#
# Prerequisites (setup-sandbox.sh grants them): the VM's service account has roles/datastore.user and
# roles/serviceusage.serviceUsageConsumer on the Firestore project, and the indexes from
# config/firestore/firestore.indexes.json exist.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
env_file="${FACTORY_ENV:-$here/../config/factory.env}"
[[ -f "$env_file" ]] || { echo "missing $env_file (copy config/factory.env.example)" >&2; exit 2; }
# A value set on the command line wins over factory.env, e.g. FACTORY_PROJECTS=... for a Hub with several factories.
overrides=()
for var in FACTORY_PROJECTS KNOWLEDGE_PLANE_SOURCES KNOWLEDGE_PLANE_SOURCE_PROJECT; do
  if [[ -n "${!var:-}" ]]; then overrides+=("$var=${!var}"); fi
done
set -a
# shellcheck source=/dev/null
. "$env_file"
set +a
for kv in ${overrides[@]+"${overrides[@]}"}; do export "$kv"; done

dry_run=0
[[ "${1:-}" == "--dry-run" ]] && dry_run=1

FACTORY_PROJECTS="${FACTORY_PROJECTS:-$BUILD_PROJECT,$EVAL_PROJECT}"
BILLING_PROJECT="${BILLING_PROJECT:-$SANDBOX_PROJECT}"
FIRESTORE_PROJECT="${FIRESTORE_PROJECT:-$SANDBOX_PROJECT}"
FIRESTORE_DATABASE="${FIRESTORE_DATABASE:-(default)}"
sync_env=""
for var in SANDBOX_PROJECT FIRESTORE_PROJECT FIRESTORE_DATABASE FACTORY_PROJECTS BILLING_PROJECT; do
  value="${!var:-}"
  [[ "$value" =~ ^[A-Za-z0-9._,:@/()-]+$ ]] || { echo "$var is empty or has unexpected characters: '$value'" >&2; exit 2; }
  sync_env+="$var='$value'"$'\n'
done

payload="$(base64 < "$here/sync-factory-agents.py" | tr -d '\n')"
store_payload="$(base64 < "$here/factory_store.py" | tr -d '\n')"
if [[ "$dry_run" -eq 1 ]]; then
  payload="<base64 of sync-factory-agents.py>"
  store_payload="<base64 of factory_store.py>"
fi
remote="$(mktemp)"
trap 'rm -f "$remote"' EXIT
cat > "$remote" <<EOF
set -euo pipefail
dir="\$HOME/factory-sync"
mkdir -p "\$dir" "\$HOME/.scion"
echo '$payload' | base64 -d > "\$dir/sync_factory_agents.py"
echo '$store_payload' | base64 -d > "\$dir/factory_store.py"
chmod 755 "\$dir/sync_factory_agents.py" "\$dir/factory_store.py"

gcloud_bin_dir="\$(dirname "\$(command -v gcloud || echo "/usr/bin/gcloud")")"
sync_env_content="PATH=\${gcloud_bin_dir}:/usr/local/bin:/usr/bin:/bin"$'\n'"${sync_env}"

echo "\$sync_env_content" > "\$dir/sync.env"
echo "\$sync_env_content" > "\$HOME/.scion/sync.env"
chmod 600 "\$dir/sync.env" "\$HOME/.scion/sync.env"

line="* * * * * set -a; . \$dir/sync.env; set +a; /usr/bin/python3 \$dir/sync_factory_agents.py >> \$HOME/.scion/factory-sync.log 2>&1"
{ crontab -l 2>/dev/null | grep -v 'sync_factory_agents.py' || true; echo "\$line"; } | crontab -
echo "crontab:"
crontab -l
echo "first run:"
set -a; . "\$dir/sync.env"; set +a
/usr/bin/python3 "\$dir/sync_factory_agents.py"
EOF

if [[ "$dry_run" -eq 1 ]]; then
  cat "$remote"
  exit 0
fi
"$here/run-on-hub.sh" "$remote"
