#!/usr/bin/env bash
# Idempotent setup of the GCP sandbox project the factory builds and deploys into. Runs on YOUR machine, with
# gcloud authenticated as an owner of SANDBOX_PROJECT who can also read the Hub VM in HUB_PROJECT.
#
#   DRY_RUN=1 scripts/setup-sandbox.sh --with-spanner --with-bigquery   # print the plan, change nothing
#   scripts/setup-sandbox.sh --with-spanner --with-bigquery
#   read -rs GITHUB_TOKEN; export GITHUB_TOKEN; scripts/setup-sandbox.sh   # also store the token (stdin, not argv)
#
# What it does (each step is skipped when already in place):
#   1. enables the APIs (explicitly, so gcloud never stops at an interactive "enable API?" prompt; L13)
#   2. creates the deployer SA (DEPLOYER_SA_NAME): the identity Scion assigns to the deployer agents
#   3. creates the runtime SA (RUNTIME_SA_NAME): the product's Cloud Run identity. Never run the product on the
#      default compute SA: it got Vertex AI 403s (L7). The deployer can act as it.
#   4. lets the Hub VM's SA mint tokens for the deployer SA (Scion "assign" identity mode), and gives it what the
#      Hub-side sync and the eval agents (passthrough identity) need: Spanner data access, Firestore, the data
#      bucket, and serviceUsageConsumer so it can bill API calls to the sandbox (L12)
#   5. lets the default compute SA run Cloud Build for `gcloud run deploy --source` (needed when the organization
#      disables the automatic Editor grant)
#   6. --with-spanner: instance (100 processing units), database, factory tables (config/spanner-factory-ddl.sql),
#      read access for the dashboard
#   7. --with-bigquery: the dataset
#   8. Firestore (default) database in Native mode, and the data bucket (uniform access, public access prevented)
#   9. if GITHUB_TOKEN is set: secret `github-token` (read from stdin). An existing secret is left alone unless
#      UPDATE_SECRET=1.
# The dashboard's runtime SA (DASHBOARD_RUNTIME_SA, default: the project's default compute SA) gets read access to
# the factory tables and the secret. A dedicated, read-only SA is better; create one and set DASHBOARD_RUNTIME_SA.
#
# The roles are deliberately broad for a throwaway sandbox (see architecture.md, Identities). Tighten them for
# anything shared.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
env_file="${FACTORY_ENV:-$here/../config/factory.env}"
[[ -f "$env_file" ]] || { echo "missing $env_file (copy config/factory.env.example)" >&2; exit 2; }
# Keep a GITHUB_TOKEN exported by the caller even if factory.env doesn't define one.
caller_token="${GITHUB_TOKEN:-}"
set -a
# shellcheck source=/dev/null
. "$env_file"
set +a
GITHUB_TOKEN="${caller_token:-${GITHUB_TOKEN:-}}"

with_spanner=0
with_bigquery=0
for arg in "$@"; do
  case "$arg" in
    --with-spanner) with_spanner=1 ;;
    --with-bigquery) with_bigquery=1 ;;
    -h|--help) sed -n '2,27p' "$0"; exit 0 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

P="$SANDBOX_PROJECT"
R="$SANDBOX_REGION"
DEPLOYER_SA="$DEPLOYER_SA_NAME@$P.iam.gserviceaccount.com"
RUNTIME_SA="$RUNTIME_SA_NAME@$P.iam.gserviceaccount.com"
DRY_RUN="${DRY_RUN:-0}"
# Never stop at an interactive prompt (e.g. "API not enabled, enable and retry?"): it hangs automation (L13).
export CLOUDSDK_CORE_DISABLE_PROMPTS=1

run() {
  if [[ "$DRY_RUN" == "1" ]]; then
    printf '+ %s\n' "$*" >&2   # stderr, so plans stay visible where callers silence stdout
  else
    "$@"
  fi
}
exists() { "$@" >/dev/null 2>&1; }   # read-only describe calls; they also run in DRY_RUN mode
step() { printf '\n== %s\n' "$*"; }
bind_project() { run gcloud projects add-iam-policy-binding "$P" --member="$1" --role="$2" --condition=None --quiet >/dev/null; }

HUB_VM_SA="${HUB_VM_SA:-$(gcloud compute instances describe "$HUB_VM" --project="$HUB_PROJECT" --zone="$HUB_ZONE" \
  --format='value(serviceAccounts[0].email)' 2>/dev/null || true)}"
if [[ -z "$HUB_VM_SA" ]]; then
  [[ "$DRY_RUN" == "1" ]] || { echo "cannot read the Hub VM's service account; set HUB_VM_SA" >&2; exit 1; }
  HUB_VM_SA="<hub-vm-service-account>"
fi
PROJECT_NUMBER="$(gcloud projects describe "$P" --format='value(projectNumber)' 2>/dev/null || echo '<project-number>')"
COMPUTE_SA="$PROJECT_NUMBER-compute@developer.gserviceaccount.com"
DASHBOARD_RUNTIME_SA="${DASHBOARD_RUNTIME_SA:-$COMPUTE_SA}"
echo "sandbox=$P region=$R deployer=$DEPLOYER_SA runtime=$RUNTIME_SA hub_vm_sa=$HUB_VM_SA dry_run=$DRY_RUN"

step "1. APIs"
apis=(run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com aiplatform.googleapis.com
      iam.googleapis.com iamcredentials.googleapis.com secretmanager.googleapis.com firestore.googleapis.com
      storage.googleapis.com logging.googleapis.com serviceusage.googleapis.com)
[[ "$with_spanner" == 1 ]] && apis+=(spanner.googleapis.com)
[[ "$with_bigquery" == 1 ]] && apis+=(bigquery.googleapis.com)
run gcloud services enable "${apis[@]}" --project="$P"

step "2-3. service accounts"
for sa in "$DEPLOYER_SA_NAME:Scion deployer agents" "$RUNTIME_SA_NAME:Cloud Run runtime of the product"; do
  name="${sa%%:*}"
  if exists gcloud iam service-accounts describe "$name@$P.iam.gserviceaccount.com" --project="$P"; then
    echo "$name: exists"
  else
    run gcloud iam service-accounts create "$name" --project="$P" --display-name="${sa#*:}"
  fi
done

deployer_roles=(roles/run.admin roles/cloudbuild.builds.editor roles/artifactregistry.admin roles/storage.admin
                roles/logging.viewer roles/serviceusage.serviceUsageConsumer roles/aiplatform.user
                roles/iam.serviceAccountUser roles/datastore.user)
[[ "$with_spanner" == 1 ]] && deployer_roles+=(roles/spanner.admin roles/spanner.databaseAdmin)
[[ "$with_bigquery" == 1 ]] && deployer_roles+=(roles/bigquery.admin)
for role in "${deployer_roles[@]}"; do bind_project "serviceAccount:$DEPLOYER_SA" "$role"; done
echo "deployer project roles: ${deployer_roles[*]}"

bind_project "serviceAccount:$RUNTIME_SA" roles/aiplatform.user
run gcloud iam service-accounts add-iam-policy-binding "$RUNTIME_SA" --project="$P" \
  --member="serviceAccount:$DEPLOYER_SA" --role=roles/iam.serviceAccountUser --quiet >/dev/null
echo "runtime SA: roles/aiplatform.user; deployer can act as it"

step "4. Hub VM service account"
for role in roles/iam.serviceAccountTokenCreator roles/iam.serviceAccountUser; do
  run gcloud iam service-accounts add-iam-policy-binding "$DEPLOYER_SA" --project="$P" \
    --member="serviceAccount:$HUB_VM_SA" --role="$role" --quiet >/dev/null
done
for role in roles/serviceusage.serviceUsageConsumer roles/datastore.user; do
  bind_project "serviceAccount:$HUB_VM_SA" "$role"
done
echo "VM SA: token creator + user on the deployer SA; serviceUsageConsumer + datastore.user on $P"

step "5. Cloud Build identity for source deploys"
bind_project "serviceAccount:$COMPUTE_SA" roles/cloudbuild.builds.builder
echo "default compute SA: cloudbuild.builds.builder"

if [[ "$with_spanner" == 1 ]]; then
  step "6. Spanner"
  if exists gcloud spanner instances describe "$SPANNER_INSTANCE" --project="$P"; then
    echo "instance $SPANNER_INSTANCE: exists"
  else
    run gcloud spanner instances create "$SPANNER_INSTANCE" --project="$P" --config="regional-$R" \
      --processing-units=100 --description="factory sandbox"
  fi
  if exists gcloud spanner databases describe "$SPANNER_DATABASE" --instance="$SPANNER_INSTANCE" --project="$P"; then
    echo "database $SPANNER_DATABASE: exists"
  else
    run gcloud spanner databases create "$SPANNER_DATABASE" --instance="$SPANNER_INSTANCE" --project="$P"
  fi
  ddl_file="$here/../config/spanner-factory-ddl.sql"
  current_ddl="$(gcloud spanner databases ddl describe "$SPANNER_DATABASE" --instance="$SPANNER_INSTANCE" \
    --project="$P" 2>/dev/null || true)"
  while read -r table; do
    if grep -q "CREATE TABLE $table " <<< "$current_ddl"; then
      echo "table $table: exists"
      continue
    fi
    stmt="$(sed -e '/^[[:space:]]*--/d' "$ddl_file" | awk -v t="$table" 'BEGIN{RS=";"} $0 ~ ("CREATE TABLE " t " ") {print; exit}')"
    run gcloud spanner databases ddl update "$SPANNER_DATABASE" --instance="$SPANNER_INSTANCE" --project="$P" --ddl="$stmt" </dev/null
  done < <(grep -o 'CREATE TABLE [a-z_]*' "$ddl_file" | awk '{print $3}')
  for sa in "$RUNTIME_SA" "$HUB_VM_SA"; do
    run gcloud spanner databases add-iam-policy-binding "$SPANNER_DATABASE" --instance="$SPANNER_INSTANCE" \
      --project="$P" --member="serviceAccount:$sa" --role=roles/spanner.databaseUser --quiet >/dev/null
  done
  echo "databaseUser on $SPANNER_DATABASE: runtime SA, VM SA"
  run gcloud spanner databases add-iam-policy-binding "$SPANNER_DATABASE" --instance="$SPANNER_INSTANCE" \
    --project="$P" --member="serviceAccount:$DASHBOARD_RUNTIME_SA" --role=roles/spanner.databaseReader --quiet >/dev/null
  echo "databaseReader on $SPANNER_DATABASE: $DASHBOARD_RUNTIME_SA (dashboard)"
fi

if [[ "$with_bigquery" == 1 ]]; then
  step "7. BigQuery"
  if exists bq --project_id="$P" show "$BQ_DATASET"; then
    echo "dataset $BQ_DATASET: exists"
  else
    run bq --project_id="$P" mk --dataset --location="${BQ_LOCATION:-$R}" "$P:$BQ_DATASET"
  fi
fi

step "8. Firestore and data bucket"
if exists gcloud firestore databases describe --database='(default)' --project="$P"; then
  echo "firestore (default): exists"
else
  run gcloud firestore databases create --database='(default)' --location="$R" --type=firestore-native --project="$P"
fi
if exists gcloud storage buckets describe "gs://$DATA_BUCKET" --project="$P"; then
  echo "bucket gs://$DATA_BUCKET: exists"
else
  run gcloud storage buckets create "gs://$DATA_BUCKET" --project="$P" --location="$R" \
    --uniform-bucket-level-access --public-access-prevention
fi
run gcloud storage buckets add-iam-policy-binding "gs://$DATA_BUCKET" --member="serviceAccount:$HUB_VM_SA" \
  --role=roles/storage.objectAdmin --quiet >/dev/null
echo "VM SA: storage.objectAdmin on gs://$DATA_BUCKET"

step "9. GitHub token secret"
if [[ -z "$GITHUB_TOKEN" ]]; then
  echo "GITHUB_TOKEN not set: skipped (export it to store it as secret github-token)"
else
  secret_exists=0
  exists gcloud secrets describe github-token --project="$P" && secret_exists=1
  if [[ "$secret_exists" == 0 ]]; then
    run gcloud secrets create github-token --project="$P" --replication-policy=automatic
  fi
  if [[ "$secret_exists" == 0 || "${UPDATE_SECRET:-0}" == 1 ]]; then
    if [[ "$DRY_RUN" == "1" ]]; then
      echo "+ printf '%s' \"\$GITHUB_TOKEN\" | gcloud secrets versions add github-token --project=$P --data-file=-"
    else
      printf '%s' "$GITHUB_TOKEN" | gcloud secrets versions add github-token --project="$P" --data-file=- >/dev/null
      echo "github-token: new version added"
    fi
  else
    echo "github-token: exists (UPDATE_SECRET=1 adds a new version)"
  fi
  run gcloud secrets add-iam-policy-binding github-token --project="$P" \
    --member="serviceAccount:$DASHBOARD_RUNTIME_SA" --role=roles/secretmanager.secretAccessor --quiet >/dev/null
  echo "secretAccessor on github-token: $DASHBOARD_RUNTIME_SA"
fi

printf '\ndone%s\n' "$([[ "$DRY_RUN" == "1" ]] && echo ' (dry run: nothing was changed)')"
