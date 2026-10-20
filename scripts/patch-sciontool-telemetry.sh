#!/usr/bin/env bash
# Install /usr/local/bin/sciontool wrapper in all 10 running agent containers and base images.
#
# Why: Docker containers have baked .Config.Env from creation time. Installing a wrapper script
# at /usr/local/bin/sciontool that exports SCION_GCP_PROJECT_ID, SCION_OTEL_GCP_CREDENTIALS,
# SCION_TELEMETRY_ENABLED=true, SCION_TELEMETRY_CLOUD_PROVIDER=gcp, and SCION_USAGE_SOURCE=native
# before delegating to /usr/local/bin/sciontool.real "$@" guarantees that sciontool init (PID 1)
# and sciontool hook receive the necessary telemetry environment across restarts.
#
# Usage:
#   scripts/patch-sciontool-telemetry.sh
#   DRY_RUN=1 scripts/patch-sciontool-telemetry.sh
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
DRY_RUN="${DRY_RUN:-0}"

if [[ -z "$HUB_PROJECT_ID" ]]; then
  echo "ERROR: HUB_PROJECT_ID (or HUB_PROJECT) must be set in factory.env" >&2
  exit 2
fi

export CLOUDSDK_CORE_DISABLE_PROMPTS=1

step() { printf '\n== %s\n' "$*"; }

ssh_cmd() {
  gcloud compute ssh "$HUB_VM" --project "$HUB_PROJECT_ID" --zone "$HUB_ZONE" --command "$1" 2>/dev/null
}

echo "=== Patching Sciontool Telemetry Wrapper ==="
echo "Hub Project: $HUB_PROJECT_ID"
echo "Hub VM:      $HUB_VM ($HUB_ZONE)"
echo "Dry Run:     $DRY_RUN"

CONTAINERS=(
  psearch--lead
  psearch--backend
  psearch--frontend
  psearch--deployer
  psearch--reviewer
  psearch-eval--eval-lead
  psearch-eval--factory-analyst
  psearch-eval--search-evaluator
  psearch-eval--ui-craftsman
  psearch-eval--eval-deployer
)

BASE_IMAGES=(
  scion-antigravity
  scion-claude
  scion-opencode
  scion-hermes
)

step "1. Patching 10 Running Agent Containers"
if [[ "$DRY_RUN" == "1" ]]; then
  for c in "${CONTAINERS[@]}"; do
    echo "  + [dry-run] patch /usr/local/bin/sciontool in container $c"
  done
else
  REMOTE_PATCH_CONTAINERS_SCRIPT='
PID="'"${HUB_PROJECT_ID}"'"
KEY="/home/'"${HUB_OS_USER}"'/.scion/telemetry-gcp-credentials.json"

for c in '"${CONTAINERS[*]}"'; do
  if ! docker inspect "$c" >/dev/null 2>&1; then
    echo "  Container $c not found, skipping"
    continue
  fi

  echo "  Patching container: $c"

  # Ensure credentials exist in container
  docker exec -u 0 "$c" mkdir -p /home/scion/.scion /root/.scion
  docker exec -u 0 "$c" sh -c "chmod 666 /tmp/scion-trace-state.json /tmp/scion-trace-state.json.lock 2>/dev/null || true; rm -f /tmp/scion-trace-state.json.tmp"
  if [ -f "$KEY" ]; then
    docker cp "$KEY" "$c:/home/scion/.scion/telemetry-gcp-credentials.json"
    docker cp "$KEY" "$c:/root/.scion/telemetry-gcp-credentials.json"
    docker exec -u 0 "$c" chmod 644 /home/scion/.scion/telemetry-gcp-credentials.json /root/.scion/telemetry-gcp-credentials.json
    docker exec -u 0 "$c" chown -R scion:scion /home/scion/.scion
  fi

  # Backup real sciontool if not already backed up, or install staged sciontool.real from host
  if [ -s "/home/'"${HUB_OS_USER}"'/.scion/sciontool.real" ]; then
    docker exec -u 0 "$c" rm -f /usr/local/bin/sciontool.real
    docker cp "/home/'"${HUB_OS_USER}"'/.scion/sciontool.real" "$c:/usr/local/bin/sciontool.real"
    docker exec -u 0 "$c" chmod 755 /usr/local/bin/sciontool.real
  else
    docker exec -u 0 "$c" sh -c '\''
      if [ ! -f /usr/local/bin/sciontool.real ]; then
        if [ -f /usr/local/bin/sciontool ]; then
          cp -p /usr/local/bin/sciontool /usr/local/bin/sciontool.real
          chmod 755 /usr/local/bin/sciontool.real
        fi
      fi
    '\''
  fi

  # Install wrapper script (unlink first to avoid ETXTBSY if PID 1 is running the binary)
  docker exec -u 0 "$c" rm -f /usr/local/bin/sciontool
  docker exec -u 0 "$c" sh -c '\''cat << "EOF" > /usr/local/bin/sciontool
#!/bin/sh
# Sciontool wrapper injecting telemetry environment variables for PID 1 init and hooks

export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/home/scion/.local/bin:${PATH:-}"
export SCION_TELEMETRY_ENABLED="${SCION_TELEMETRY_ENABLED:-true}"
export SCION_TELEMETRY_CLOUD_ENABLED="${SCION_TELEMETRY_CLOUD_ENABLED:-true}"
export SCION_TELEMETRY_CLOUD_PROVIDER="${SCION_TELEMETRY_CLOUD_PROVIDER:-gcp}"
export SCION_GCP_PROJECT_ID="${SCION_GCP_PROJECT_ID:-'"$HUB_PROJECT_ID"'}"
export SCION_OTEL_GCP_CREDENTIALS="${SCION_OTEL_GCP_CREDENTIALS:-/home/scion/.scion/telemetry-gcp-credentials.json}"
if [ -z "${SCION_HARNESS:-}" ]; then
  for arg in "$@"; do
    case "$arg" in
      --dialect=*) export SCION_HARNESS="${arg#--dialect=}" ;;
    esac
  done
fi
case "${SCION_HARNESS:-}" in
  claude)
    export SCION_USAGE_SOURCE="native"
    export SCION_MODEL="${SCION_MODEL:-opus}"
    ;;
  codex|copilot)
    export SCION_USAGE_SOURCE="native"
    ;;
  opencode)
    export SCION_USAGE_SOURCE="hooks"
    export SCION_MODEL="${SCION_MODEL:-google-vertex/gemini-4-argon}"
    ;;
  antigravity)
    export SCION_USAGE_SOURCE="hooks"
    export SCION_MODEL="${SCION_MODEL:-Gemini 4 Argon (High)}"
    ;;
  hermes)
    export SCION_USAGE_SOURCE="hooks"
    export SCION_MODEL="${SCION_MODEL:-google/gemini-4-argon}"
    ;;
  *)
    export SCION_USAGE_SOURCE="${SCION_USAGE_SOURCE:-hooks}"
    ;;
esac

exec /usr/local/bin/sciontool.real "$@"
EOF
chmod 755 /usr/local/bin/sciontool
'\''
  echo "    Successfully installed wrapper in $c"
done
'
  ssh_cmd "sudo bash -c $(printf '%q' "$REMOTE_PATCH_CONTAINERS_SCRIPT")"
fi

step "2. Patching Base Docker Images"
if [[ "$DRY_RUN" == "1" ]]; then
  for img in "${BASE_IMAGES[@]}"; do
    echo "  + [dry-run] patch image $img:latest and localhost/scion/$img:latest"
  done
else
  REMOTE_PATCH_IMAGES_SCRIPT='
PID="'"${HUB_PROJECT_ID}"'"
KEY="/home/'"${HUB_OS_USER}"'/.scion/telemetry-gcp-credentials.json"

cat << "EOF" > /tmp/sciontool-wrapper
#!/bin/sh
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/home/scion/.local/bin:${PATH:-}"
export SCION_TELEMETRY_ENABLED="${SCION_TELEMETRY_ENABLED:-true}"
export SCION_TELEMETRY_CLOUD_ENABLED="${SCION_TELEMETRY_CLOUD_ENABLED:-true}"
export SCION_TELEMETRY_CLOUD_PROVIDER="${SCION_TELEMETRY_CLOUD_PROVIDER:-gcp}"
export SCION_GCP_PROJECT_ID="${SCION_GCP_PROJECT_ID:-'"$HUB_PROJECT_ID"'}"
export SCION_OTEL_GCP_CREDENTIALS="${SCION_OTEL_GCP_CREDENTIALS:-/home/scion/.scion/telemetry-gcp-credentials.json}"
if [ -z "${SCION_HARNESS:-}" ]; then
  for arg in "$@"; do
    case "$arg" in
      --dialect=*) export SCION_HARNESS="${arg#--dialect=}" ;;
    esac
  done
fi
case "${SCION_HARNESS:-}" in
  claude)
    export SCION_USAGE_SOURCE="native"
    export SCION_MODEL="${SCION_MODEL:-opus}"
    ;;
  codex|copilot)
    export SCION_USAGE_SOURCE="native"
    ;;
  opencode)
    export SCION_USAGE_SOURCE="hooks"
    export SCION_MODEL="${SCION_MODEL:-google-vertex/gemini-4-argon}"
    ;;
  antigravity)
    export SCION_USAGE_SOURCE="hooks"
    export SCION_MODEL="${SCION_MODEL:-Gemini 4 Argon (High)}"
    ;;
  hermes)
    export SCION_USAGE_SOURCE="hooks"
    export SCION_MODEL="${SCION_MODEL:-google/gemini-4-argon}"
    ;;
  *)
    export SCION_USAGE_SOURCE="${SCION_USAGE_SOURCE:-hooks}"
    ;;
esac
exec /usr/local/bin/sciontool.real "$@"
EOF
chmod 755 /tmp/sciontool-wrapper

for img in '"${BASE_IMAGES[*]}"'; do
  if ! docker image inspect "$img:latest" >/dev/null 2>&1; then
    echo "  Image $img:latest not found, skipping"
    continue
  fi

  echo "  Patching base image: $img:latest"
  cid=$(docker create "$img:latest")

  # Extract binary to back it up as sciontool.real if needed, or install staged sciontool.real from host
  if [ -s "/home/'"${HUB_OS_USER}"'/.scion/sciontool.real" ]; then
    docker cp "/home/'"${HUB_OS_USER}"'/.scion/sciontool.real" "$cid:/usr/local/bin/sciontool.real"
  else
    docker cp "$cid:/usr/local/bin/sciontool.real" /tmp/test-real 2>/dev/null || true
    if [ ! -s /tmp/test-real ]; then
      docker cp "$cid:/usr/local/bin/sciontool" /tmp/sciontool-bin 2>/dev/null || true
      if [ -s /tmp/sciontool-bin ]; then
        chmod 755 /tmp/sciontool-bin
        docker cp /tmp/sciontool-bin "$cid:/usr/local/bin/sciontool.real"
        rm -f /tmp/sciontool-bin
      fi
    fi
    rm -f /tmp/test-real
  fi

  # Copy wrapper
  docker cp /tmp/sciontool-wrapper "$cid:/usr/local/bin/sciontool"

  # Copy credentials
  if [ -f "$KEY" ]; then
    docker cp "$KEY" "$cid:/home/scion/.scion/telemetry-gcp-credentials.json" 2>/dev/null || true
    docker cp "$KEY" "$cid:/root/.scion/telemetry-gcp-credentials.json" 2>/dev/null || true
  fi

  docker commit "$cid" "$img:latest" >/dev/null
  docker tag "$img:latest" "localhost/scion/$img:latest" >/dev/null 2>&1 || true
  docker rm "$cid" >/dev/null 2>&1 || true
  echo "    Successfully updated image $img:latest"
done
rm -f /tmp/sciontool-wrapper
'
  ssh_cmd "sudo bash -c $(printf '%q' "$REMOTE_PATCH_IMAGES_SCRIPT")"
fi

echo -e "\n=== Sciontool Wrapper Patching Complete ==="
