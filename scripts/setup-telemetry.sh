#!/usr/bin/env bash
# Provision Cloud Logging, Cloud Monitoring, and Cloud Trace telemetry for the Scion Hub and agents.
#
# Idempotent: checks for existing APIs, service accounts, IAM bindings, credentials, and configuration
# before modifying. Supports DRY_RUN=1.
#
# Reads:
#   config/factory.env (HUB_PROJECT / HUB_PROJECT_ID, HUB_VM, HUB_ZONE, HUB_OS_USER)
#
# Usage:
#   scripts/setup-telemetry.sh
#   DRY_RUN=1 scripts/setup-telemetry.sh
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
env_file="${FACTORY_ENV:-$here/../config/factory.env}"
if [[ ! -f "$env_file" ]]; then
  echo "missing $env_file (copy config/factory.env.example and fill it in)" >&2
  exit 2
fi

set -a
# shellcheck source=/dev/null
. "$env_file"
set +a

HUB_PROJECT_ID="${HUB_PROJECT_ID:-${HUB_PROJECT:-}}"
HUB_VM="${HUB_VM:-}"
HUB_ZONE="${HUB_ZONE:-us-central1-a}"
HUB_OS_USER="${HUB_OS_USER:-scion}"
TELEMETRY_SA_NAME="${TELEMETRY_SA_NAME:-scion-telemetry-writer}"
TELEMETRY_SA="${TELEMETRY_SA_NAME}@${HUB_PROJECT_ID}.iam.gserviceaccount.com"
DRY_RUN="${DRY_RUN:-0}"

if [[ -z "$HUB_PROJECT_ID" ]]; then
  echo "ERROR: HUB_PROJECT_ID (or HUB_PROJECT) must be set in factory.env" >&2
  exit 2
fi

export CLOUDSDK_CORE_DISABLE_PROMPTS=1

run() {
  if [[ "$DRY_RUN" == "1" ]]; then
    printf '+ %s\n' "$*" >&2
  else
    "$@"
  fi
}

exists() { "$@" >/dev/null 2>&1; }
step() { printf '\n== %s\n' "$*"; }

ssh_cmd() {
  gcloud compute ssh "$HUB_VM" --project "$HUB_PROJECT_ID" --zone "$HUB_ZONE" --command "$1" 2>/dev/null
}

echo "=== Scion Factory Telemetry Provisioning ==="
echo "Hub Project: $HUB_PROJECT_ID"
echo "Hub VM:      $HUB_VM ($HUB_ZONE)"
echo "Dry Run:     $DRY_RUN"

# 1. Enable GCP Telemetry APIs
step "1. GCP APIs for Telemetry"
APIS=(
  cloudtrace.googleapis.com
  monitoring.googleapis.com
  logging.googleapis.com
)
for api in "${APIS[@]}"; do
  if gcloud services list --project="$HUB_PROJECT_ID" --filter="name:$api" --format="value(config.name)" 2>/dev/null | grep -q "$api"; then
    echo "  $api: already enabled"
  else
    echo "  $api: enabling..."
    run gcloud services enable "$api" --project="$HUB_PROJECT_ID"
  fi
done

# 2. Service Account for Telemetry Writer
step "2. Telemetry Writer Service Account"
if exists gcloud iam service-accounts describe "$TELEMETRY_SA" --project="$HUB_PROJECT_ID"; then
  echo "  $TELEMETRY_SA: already exists"
else
  echo "  Creating $TELEMETRY_SA..."
  run gcloud iam service-accounts create "$TELEMETRY_SA_NAME" \
    --project="$HUB_PROJECT_ID" \
    --display-name="Scion Telemetry Writer"
fi

WRITER_ROLES=(
  roles/cloudtrace.agent
  roles/logging.logWriter
  roles/monitoring.metricWriter
)
for role in "${WRITER_ROLES[@]}"; do
  echo "  Binding $role to $TELEMETRY_SA..."
  run gcloud projects add-iam-policy-binding "$HUB_PROJECT_ID" \
    --member="serviceAccount:$TELEMETRY_SA" \
    --role="$role" \
    --condition=None --quiet >/dev/null
done

# 3. Hub VM Service Account Permissions
step "3. Hub VM Service Account Permissions"
VM_SA="$(gcloud compute instances describe "$HUB_VM" --project="$HUB_PROJECT_ID" --zone="$HUB_ZONE" --format='value(serviceAccounts[0].email)' 2>/dev/null || true)"
if [[ -z "$VM_SA" ]]; then
  echo "ERROR: could not determine service account for VM $HUB_VM" >&2
  exit 2
fi
echo "  Hub VM Service Account: $VM_SA"

HUB_VM_ROLES=(
  roles/cloudtrace.agent
  roles/logging.logWriter
  roles/monitoring.metricWriter
  roles/monitoring.viewer
  roles/logging.viewer
  roles/cloudtrace.user
)
for role in "${HUB_VM_ROLES[@]}"; do
  echo "  Binding $role to $VM_SA..."
  run gcloud projects add-iam-policy-binding "$HUB_PROJECT_ID" \
    --member="serviceAccount:$VM_SA" \
    --role="$role" \
    --condition=None --quiet >/dev/null
done

# 4. Provision Service Account Credentials on Hub VM
step "4. Provision Telemetry Credentials on Hub VM"
REMOTE_KEY_PATH="/home/${HUB_OS_USER}/.scion/telemetry-gcp-credentials.json"
KEY_EXISTS=$(ssh_cmd "sudo test -s $REMOTE_KEY_PATH && echo yes || echo no")

if [[ "$KEY_EXISTS" == "yes" ]]; then
  echo "  $REMOTE_KEY_PATH already exists on $HUB_VM"
else
  echo "  Generating key for $TELEMETRY_SA and deploying to $HUB_VM..."
  if [[ "$DRY_RUN" == "1" ]]; then
    echo "+ generate key and copy to $HUB_VM:$REMOTE_KEY_PATH" >&2
  else
    tmp_key="$(mktemp /tmp/scion-telemetry-sa-XXXXXX.json)"
    trap 'rm -f "$tmp_key"' EXIT
    gcloud iam service-accounts keys create "$tmp_key" \
      --iam-account="$TELEMETRY_SA" \
      --project="$HUB_PROJECT_ID" >/dev/null

    # Upload key to Hub VM
    gcloud compute scp "$tmp_key" "${HUB_VM}:/tmp/telemetry-credentials.json" \
      --project="$HUB_PROJECT_ID" --zone="$HUB_ZONE" --quiet 2>/dev/null

    ssh_cmd "sudo mkdir -p /home/${HUB_OS_USER}/.scion && \
             sudo mv /tmp/telemetry-credentials.json $REMOTE_KEY_PATH && \
             sudo chown ${HUB_OS_USER}:${HUB_OS_USER} $REMOTE_KEY_PATH && \
             sudo chmod 644 $REMOTE_KEY_PATH"
    rm -f "$tmp_key"
    trap - EXIT
    echo "  $REMOTE_KEY_PATH deployed successfully (mode 644)"
  fi
fi

# Distribute key to all agent home directories and containers on Hub VM
step "5. Distribute Credentials to Agent Homes & Running Containers"
if [[ "$DRY_RUN" == "1" ]]; then
  echo "+ distribute key across agent homes and docker containers on $HUB_VM" >&2
else
  DIST_SCRIPT='
KEY="/home/'"${HUB_OS_USER}"'/.scion/telemetry-gcp-credentials.json"
if [ ! -f "$KEY" ]; then
  echo "Missing $KEY" >&2
  exit 1
fi

echo "Distributing to agent homes..."
for d in /home/'"${HUB_OS_USER}"'/.scion/projects/*/.agents/*/home/.scion \
         /home/'"${HUB_OS_USER}"'/.scion/project-configs/*/.scion/agents/*/home/.scion; do
  if [ -d "$d" ]; then
    cp -p "$KEY" "$d/telemetry-gcp-credentials.json"
    chown -R '"${HUB_OS_USER}:${HUB_OS_USER}"' "$d/telemetry-gcp-credentials.json" 2>/dev/null || true
    chmod 644 "$d/telemetry-gcp-credentials.json"
  fi
done

echo "Distributing to docker containers..."
for c in $(docker ps -q); do
  cname=$(docker inspect "$c" --format "{{.Name}}" | sed "s#^/##")
  docker exec -u 0 "$c" mkdir -p /home/scion/.scion /root/.scion 2>/dev/null || true
  docker cp "$KEY" "$c:/home/scion/.scion/telemetry-gcp-credentials.json" 2>/dev/null || true
  docker cp "$KEY" "$c:/root/.scion/telemetry-gcp-credentials.json" 2>/dev/null || true
  docker exec -u 0 "$c" chmod 644 /home/scion/.scion/telemetry-gcp-credentials.json /root/.scion/telemetry-gcp-credentials.json 2>/dev/null || true
  docker exec -u 0 "$c" chown -R scion:scion /home/scion/.scion 2>/dev/null || true
  echo "  Copied key to container: $cname"
done
'
  ssh_cmd "sudo bash -c $(printf '%q' "$DIST_SCRIPT")"
fi

# 6. Update Hub VM hub.env and settings.yaml
step "6. Update Hub VM hub.env and settings.yaml"
if [[ "$DRY_RUN" == "1" ]]; then
  echo "+ update hub.env and settings.yaml on $HUB_VM" >&2
else
  UPDATE_CONFIG_SCRIPT='
HUB_ENV="/home/'"${HUB_OS_USER}"'/.scion/hub.env"
SETTINGS="/home/'"${HUB_OS_USER}"'/.scion/settings.yaml"
PID="'"${HUB_PROJECT_ID}"'"

echo "Updating $HUB_ENV..."
set_env() {
  key="$1"
  val="$2"
  if grep -q "^${key}=" "$HUB_ENV"; then
    sed -i "s#^${key}=.*#${key}=${val}#" "$HUB_ENV"
  else
    echo "${key}=${val}" >> "$HUB_ENV"
  fi
}

set_env "SCION_GCP_PROJECT_ID" "$PID"
set_env "GOOGLE_CLOUD_PROJECT" "$PID"
set_env "SCION_CLOUD_LOGGING" "true"
set_env "SCION_OTEL_LOG_ENABLED" "true"
set_env "SCION_TELEMETRY_ENABLED" "true"
set_env "SCION_TELEMETRY_CLOUD_PROVIDER" "gcp"
set_env "SCION_OTEL_GCP_CREDENTIALS" "/home/'"${HUB_OS_USER}"'/.scion/telemetry-gcp-credentials.json"
set_env "SCION_USAGE_SOURCE" "native"

echo "Updating $SETTINGS..."
python3 -c "
import yaml

with open(\"$SETTINGS\", \"r\") as f:
    cfg = yaml.safe_load(f)

if \"telemetry\" not in cfg:
    cfg[\"telemetry\"] = {}
cfg[\"telemetry\"][\"enabled\"] = True

if \"cloud\" not in cfg[\"telemetry\"]:
    cfg[\"telemetry\"][\"cloud\"] = {}
cfg[\"telemetry\"][\"cloud\"][\"enabled\"] = True
cfg[\"telemetry\"][\"cloud\"][\"provider\"] = \"gcp\"
cfg[\"telemetry\"][\"cloud\"][\"endpoint\"] = \"monitoring.googleapis.com:443\"
cfg[\"telemetry\"][\"cloud\"][\"gcp_project_id\"] = \"$PID\"

# Also ensure harness_configs inherit GCP project and telemetry settings
for h_name, h_cfg in cfg.get(\"harness_configs\", {}).items():
    if \"env\" not in h_cfg:
        h_cfg[\"env\"] = {}
    h_cfg[\"env\"][\"SCION_GCP_PROJECT_ID\"] = \"$PID\"
    h_cfg[\"env\"][\"SCION_TELEMETRY_ENABLED\"] = \"true\"
    h_cfg[\"env\"][\"SCION_TELEMETRY_CLOUD_PROVIDER\"] = \"gcp\"
    h_cfg[\"env\"][\"SCION_USAGE_SOURCE\"] = \"native\"

with open(\"$SETTINGS\", \"w\") as f:
    yaml.dump(cfg, f, default_flow_style=False, sort_keys=False)
"
'
  ssh_cmd "sudo bash -c $(printf '%q' "$UPDATE_CONFIG_SCRIPT")"
fi

# 7. Restart scion-hub service
step "7. Restart scion-hub Service on Hub VM"
if [[ "$DRY_RUN" == "1" ]]; then
  echo "+ restart scion-hub systemd service on $HUB_VM" >&2
else
  ssh_cmd "sudo systemctl restart scion-hub"
  sleep 3
  STATUS=$(ssh_cmd "sudo systemctl is-active scion-hub")
  echo "  scion-hub status: $STATUS"
  ssh_cmd "sudo journalctl -u scion-hub -n 20 --no-pager"
fi

echo -e "\n=== Setup Telemetry Completed Successfully ==="
