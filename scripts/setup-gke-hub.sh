#!/usr/bin/env bash
set -euo pipefail
export CLOUDSDK_CORE_DISABLE_PROMPTS=1

TIER="terraform-ha"
HUB_MODE="cloudrun"
SCION_REPO=""
HARNESSES="claude,antigravity"
IMAGE_TAG="${IMAGE_TAG:-v$(date -u +%Y%m%d%H%M%S)}"
WORK_DIR="build/gke-hub"
APPROVE_APPLY=0
DRY_RUN="${DRY_RUN:-0}"
ENV_FILE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env) ENV_FILE="$2"; shift 2 ;;
    --tier) TIER="$2"; shift 2 ;;
    --hub-mode) HUB_MODE="$2"; shift 2 ;;
    --scion-repo) SCION_REPO="$2"; shift 2 ;;
    --harnesses) HARNESSES="$2"; shift 2 ;;
    --image-tag) IMAGE_TAG="$2"; shift 2 ;;
    --work-dir) WORK_DIR="$2"; shift 2 ;;
    --approve-apply) APPROVE_APPLY=1; shift 1 ;;
    --dry-run) DRY_RUN=1; shift 1 ;;
    -h|--help) echo "Usage: $0 [options]"; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

if [[ -n "$ENV_FILE" && -f "$ENV_FILE" ]]; then
  set -a
  source "$ENV_FILE"
  set +a
fi

HUB_PROJECT="${HUB_PROJECT:-}"
HUB_NAME="${HUB_NAME:-prod}"
PO_EMAIL="${PO_EMAIL:-}"

if [[ -z "$HUB_PROJECT" || -z "$PO_EMAIL" ]]; then
  echo "HUB_PROJECT and PO_EMAIL must be set." >&2
  exit 1
fi

HUB_REGION="${HUB_REGION:-${SANDBOX_REGION:-us-central1}}"
IMAGE_REGISTRY="${IMAGE_REGISTRY:-${HUB_REGION}-docker.pkg.dev/${HUB_PROJECT}/scion}"

if [[ "$TIER" == "terraform-ha" ]]; then
  if [[ "$HUB_MODE" == "cloudrun" ]]; then
    HUB_TF_DIR="hub"
    HUB_K8S_NAMESPACE="scion-${HUB_NAME}"
  else
    HUB_TF_DIR="hub-gke"
    HUB_K8S_NAMESPACE="${HUB_NAME}"
  fi
else
  HUB_K8S_NAMESPACE="scion-hub-${HUB_NAME}"
fi

run() {
  if [[ "$DRY_RUN" == "1" ]]; then
    printf '+ %s\n' "$*" >&2
  else
    "$@"
  fi
}

echo "== 1. Enable APIs & Artifact Registry"
run gcloud services enable --project="$HUB_PROJECT" container.googleapis.com run.googleapis.com \
  file.googleapis.com sqladmin.googleapis.com secretmanager.googleapis.com artifactregistry.googleapis.com \
  cloudbuild.googleapis.com iap.googleapis.com aiplatform.googleapis.com iam.googleapis.com iamcredentials.googleapis.com

REPO_NAME=$(basename "$IMAGE_REGISTRY")
registry_host="${IMAGE_REGISTRY%%/*}"
if [[ "$registry_host" == *-docker.pkg.dev ]]; then
  REPO_LOC="${registry_host%-docker.pkg.dev}"
else
  REPO_LOC="$HUB_REGION"
fi

if [[ "$DRY_RUN" == "0" ]]; then
  if ! gcloud artifacts repositories describe "$REPO_NAME" --project="$HUB_PROJECT" --location="$REPO_LOC" >/dev/null 2>&1; then
    run gcloud artifacts repositories create "$REPO_NAME" --project="$HUB_PROJECT" --location="$REPO_LOC" --repository-format=docker || true
  fi
else
  run gcloud artifacts repositories create "$REPO_NAME" --project="$HUB_PROJECT" --location="$REPO_LOC" --repository-format=docker
fi

echo "== 2. Build multi-arch harness images"
if [[ -z "$SCION_REPO" ]]; then
  echo "--scion-repo is required to build images." >&2
  exit 1
fi

# Build core and base images
for img in core-base scion-base; do
  run "$SCION_REPO/image-build/scripts/build-images.sh" --builder cloud-build --registry "$IMAGE_REGISTRY" --tag "$IMAGE_TAG" --push "$img"
done

# Build harnesses
IFS=',' read -ra H_ARR <<< "$HARNESSES"
for h in "${H_ARR[@]}"; do
  run "$SCION_REPO/image-build/scripts/build-images.sh" --builder cloud-build --registry "$IMAGE_REGISTRY" --tag "$IMAGE_TAG" --push "$h"
done

mkdir -p "$WORK_DIR"

if [[ "$TIER" == "terraform-ha" ]]; then
  echo "== 3. Terraform HA provisioning ($HUB_TF_DIR)"
  cat > "$WORK_DIR/shared-infra.auto.tfvars" <<EOF
project_id = "${HUB_PROJECT}"
region     = "${HUB_REGION}"
EOF
  cat > "$WORK_DIR/hub.auto.tfvars" <<EOF
project_id = "${HUB_PROJECT}"
region     = "${HUB_REGION}"
hub_name   = "${HUB_NAME}"
po_email   = "${PO_EMAIL}"
image_registry = "${IMAGE_REGISTRY}"
image_tag      = "${IMAGE_TAG}"
EOF

  ABS_WORK_DIR="$(cd "$WORK_DIR" && pwd)"
  run terraform -chdir="${SCION_REPO}/deploy/terraform/configurations/shared-infra" init
  run terraform -chdir="${SCION_REPO}/deploy/terraform/configurations/shared-infra" plan -var-file="${ABS_WORK_DIR}/shared-infra.auto.tfvars"
  if [[ "$APPROVE_APPLY" == 1 && "$DRY_RUN" == 0 ]]; then
    run terraform -chdir="${SCION_REPO}/deploy/terraform/configurations/shared-infra" apply -auto-approve -var-file="${ABS_WORK_DIR}/shared-infra.auto.tfvars"
  fi

  run terraform -chdir="${SCION_REPO}/deploy/terraform/configurations/${HUB_TF_DIR}" init
  run terraform -chdir="${SCION_REPO}/deploy/terraform/configurations/${HUB_TF_DIR}" plan -var-file="${ABS_WORK_DIR}/hub.auto.tfvars"
  if [[ "$APPROVE_APPLY" == 1 && "$DRY_RUN" == 0 ]]; then
    run terraform -chdir="${SCION_REPO}/deploy/terraform/configurations/${HUB_TF_DIR}" apply -auto-approve -var-file="${ABS_WORK_DIR}/hub.auto.tfvars"
  fi

  # Post-apply
  TARGET_PROJECT="${SANDBOX_PROJECT:-$HUB_PROJECT}"
  TARGET_REGION="${SANDBOX_REGION:-$HUB_REGION}"
  if [[ "$HUB_MODE" == "cloudrun" ]]; then
    run gcloud run services update "scion-hub-${HUB_NAME}" --project="$HUB_PROJECT" --region="$HUB_REGION" \
      --update-env-vars="GOOGLE_CLOUD_PROJECT=$TARGET_PROJECT,GOOGLE_CLOUD_REGION=$TARGET_REGION"
  else
    run gcloud container clusters get-credentials "scion-hub-${HUB_NAME}" --project="$HUB_PROJECT" --region="$HUB_REGION"
    run kubectl get nodes
    run kubectl get namespace "$HUB_K8S_NAMESPACE"
    run kubectl set env deployment/scion-hub -n "$HUB_K8S_NAMESPACE" "GOOGLE_CLOUD_PROJECT=$TARGET_PROJECT" "GOOGLE_CLOUD_REGION=$TARGET_REGION"
  fi

elif [[ "$TIER" == "hybrid-gke" ]]; then
  echo "== 3. Hybrid GKE provisioning"
  cat > "$WORK_DIR/deploy-config.json" <<EOF
{
  "gke_target": {
    "cluster_name": "scion-hub-${HUB_NAME}",
    "location": "${HUB_REGION}",
    "namespace": "${HUB_K8S_NAMESPACE}"
  },
  "container_images": {
    "source": "registry",
    "registry": "${IMAGE_REGISTRY}",
    "tag": "${IMAGE_TAG}"
  }
}
EOF
  if [[ "$APPROVE_APPLY" == 1 && "$DRY_RUN" == 0 ]]; then
    run "$SCION_REPO/scripts/single-node-vm/deploy.sh" --config "$WORK_DIR/deploy-config.json"
  else
    echo "Plan: would run $SCION_REPO/scripts/single-node-vm/deploy.sh --config $WORK_DIR/deploy-config.json"
  fi
fi

echo ""
echo "=== factory.env additions ==="
echo "HUB_URL=https://..."
echo "AGENT_RUNTIME=kubernetes"
echo "HUB_K8S_NAMESPACE=${HUB_K8S_NAMESPACE}"
echo "IMAGE_REGISTRY=${IMAGE_REGISTRY}"
