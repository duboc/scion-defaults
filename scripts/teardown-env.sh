#!/usr/bin/env bash
# teardown-env.sh — Idempotent teardown for a Scion HA factory/environment.
#
# Cleans up:
#   1. Shared or dedicated GKE namespace (K8s namespace + workloads)
#   2. Cloud Run Instances in any configured lead regions (e.g. us-east4)
#   3. Cross-project Direct IAP bindings (walgit, farmtable-gcp, etc.)
#   4. Shared AlloyDB PSC allowedConsumerProjects allowlist entry
#   5. Walgit repositories via DELETE /{owner}/{repo}/api
#   6. Farm Table collections & tasks (Spanner + Firestore dual backend)
#   7. Shared GCS bucket / GCP project deletion (opt-in --delete-project)
#
# Supports DRY_RUN=1 / --dry-run (default unless --approve-apply is passed).
set -euo pipefail
export CLOUDSDK_CORE_DISABLE_PROMPTS=1
export PATH="${PATH}:${HOME}/.local/bin"

HUB_PROJECT="${HUB_PROJECT:-}"
SHARED_PREFIX="${SHARED_PREFIX:-tfha}"
HUB_NAME="${HUB_NAME:-main}"
HUB_REGION="${HUB_REGION:-us-central1}"
HUB_GKE_PROJECT="${HUB_GKE_PROJECT:-}"
HUB_GKE_CLUSTER="${HUB_GKE_CLUSTER:-}"
HUB_GKE_REGION="${HUB_GKE_REGION:-$HUB_REGION}"
CLOUDRUN_LOCATIONS="${CLOUDRUN_LOCATIONS:-us-east4}"
SHARED_GCS_BUCKET="${SHARED_GCS_BUCKET:-}"
ALLOYDB_PROJECT="${ALLOYDB_PROJECT:-}"
ALLOYDB_REGION="${ALLOYDB_REGION:-$HUB_REGION}"
ALLOYDB_CLUSTER="${ALLOYDB_CLUSTER:-}"
ALLOYDB_INSTANCE="${ALLOYDB_INSTANCE:-}"
WALGIT_URL="${WALGIT_URL:-}"
WALGIT_REPOS="${WALGIT_REPOS:-}"
WALGIT_IAP_SA="${WALGIT_IAP_SA:-}"
FARMTABLE_COLLECTION_IDS="${FARMTABLE_COLLECTION_IDS:-}"
SPANNER_PROJECT="${SPANNER_PROJECT:-}"
SPANNER_INSTANCE="${SPANNER_INSTANCE:-}"
SPANNER_DATABASE="${SPANNER_DATABASE:-}"
FIRESTORE_PROJECT="${FIRESTORE_PROJECT:-}"
EXTRA_CROSS_IAP_SERVICES="${EXTRA_CROSS_IAP_SERVICES:-}"
DELETE_PROJECT=0
APPROVE_APPLY=0
DRY_RUN="${DRY_RUN:-0}"

usage() {
  cat <<EOF
Usage: $(basename "$0") [options]

Options:
  --project <id>                    Target GCP project hosting the Hub (HUB_PROJECT)
  --prefix <prefix>                 Shared prefix (default: tfha)
  --hub-name <name>                 Hub service/namespace name (default: main -> <prefix>-main)
  --region <region>                 Hub GCP region (default: us-central1)
  --gke-project <id>                GCP project hosting GKE cluster (default: HUB_PROJECT)
  --gke-cluster <name>              GKE cluster name (default: <prefix>-agents)
  --gke-region <region>             GKE cluster region (default: HUB_REGION)
  --cloudrun-locations <list>       Comma-separated Cloud Run Instance regions to clean (default: us-east4)
  --shared-gcs-bucket <bucket>      Optional shared GCS bucket to delete
  --alloydb <proj:reg:cluster:inst> Shared AlloyDB instance coordinates to prune projectNumber from
  --walgit-url <url>                Walgit base URL (for deleting WALGIT_REPOS)
  --walgit-repos <owner/repo,...>   Comma-separated Walgit repos to delete
  --walgit-iap-sa <email>           Service account used for Direct IAP signJwt against Walgit
  --farmtable-ids <uuid,...>        Comma-separated Farm Table collection UUIDs to delete
  --spanner <proj:inst:db>          Farm Table Cloud Spanner database coordinates
  --firestore-project <id>          Farm Table Firestore project ID
  --cross-iap-services <p:r:s,...>  Comma-separated Cross-project Cloud Run IAP services to clean bindings on
  --delete-project                  Delete the GCP project (--approve-apply required to mutate)
  --approve-apply                   Execute deletions (without this or with --dry-run, only plans)
  --dry-run                         Print planned commands without mutating resources
  -h, --help                        Show this help message
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project) HUB_PROJECT="$2"; shift 2 ;;
    --prefix) SHARED_PREFIX="$2"; shift 2 ;;
    --hub-name) HUB_NAME="$2"; shift 2 ;;
    --region) HUB_REGION="$2"; shift 2 ;;
    --gke-project) HUB_GKE_PROJECT="$2"; shift 2 ;;
    --gke-cluster) HUB_GKE_CLUSTER="$2"; shift 2 ;;
    --gke-region) HUB_GKE_REGION="$2"; shift 2 ;;
    --cloudrun-locations) CLOUDRUN_LOCATIONS="$2"; shift 2 ;;
    --shared-gcs-bucket) SHARED_GCS_BUCKET="$2"; shift 2 ;;
    --alloydb)
      IFS=':' read -r ALLOYDB_PROJECT ALLOYDB_REGION ALLOYDB_CLUSTER ALLOYDB_INSTANCE <<< "$2"
      shift 2
      ;;
    --walgit-url) WALGIT_URL="$2"; shift 2 ;;
    --walgit-repos) WALGIT_REPOS="$2"; shift 2 ;;
    --walgit-iap-sa) WALGIT_IAP_SA="$2"; shift 2 ;;
    --farmtable-ids) FARMTABLE_COLLECTION_IDS="$2"; shift 2 ;;
    --spanner)
      IFS=':' read -r SPANNER_PROJECT SPANNER_INSTANCE SPANNER_DATABASE <<< "$2"
      shift 2
      ;;
    --firestore-project) FIRESTORE_PROJECT="$2"; shift 2 ;;
    --cross-iap-services) EXTRA_CROSS_IAP_SERVICES="$2"; shift 2 ;;
    --delete-project) DELETE_PROJECT=1; shift 1 ;;
    --approve-apply) APPROVE_APPLY=1; shift 1 ;;
    --dry-run) DRY_RUN=1; shift 1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

if [[ -z "$HUB_PROJECT" ]]; then
  echo "--project (or HUB_PROJECT) is required." >&2
  exit 1
fi

if [[ "$HUB_NAME" != "${SHARED_PREFIX}-"* ]]; then
  HUB_NAME="${SHARED_PREFIX}-${HUB_NAME}"
fi
HUB_GKE_PROJECT="${HUB_GKE_PROJECT:-$HUB_PROJECT}"
HUB_GKE_CLUSTER="${HUB_GKE_CLUSTER:-${SHARED_PREFIX}-agents}"
PLAN_ONLY=0
if [[ "$DRY_RUN" == "1" || "$APPROVE_APPLY" != "1" ]]; then
  PLAN_ONLY=1
fi

run() {
  if [[ "$PLAN_ONLY" == "1" ]]; then
    printf '+ %s\n' "$*" >&2
  else
    "$@"
  fi
}

echo "== Teardown Plan for ${HUB_PROJECT} (${HUB_NAME}, gke=${HUB_GKE_PROJECT}/${HUB_GKE_CLUSTER}, plan_only=${PLAN_ONLY}) =="

# 1. Walgit repositories
if [[ -n "$WALGIT_URL" && -n "$WALGIT_REPOS" ]]; then
  echo "== 1. Walgit repos (${WALGIT_REPOS}) =="
  IFS=',' read -ra _W_REPOS <<< "$WALGIT_REPOS"
  for _repo in "${_W_REPOS[@]}"; do
    [[ -n "$_repo" ]] || continue
    if [[ "$PLAN_ONLY" == "1" ]]; then
      printf '+ DELETE %s/%s/api\n' "${WALGIT_URL%/}" "$_repo" >&2
    else
      WALGIT_URL="${WALGIT_URL%/}" WALGIT_REPO="$_repo" WALGIT_IAP_SA="$WALGIT_IAP_SA" python3 - <<'PY'
import json, os, subprocess, tempfile, time, urllib.error, urllib.parse, urllib.request
base = os.environ["WALGIT_URL"]
repo = os.environ["WALGIT_REPO"].strip("/")
sa = os.environ.get("WALGIT_IAP_SA", "").strip()
tok = os.environ.get("WALGIT_TOKEN", "").strip()
if not tok:
    raise RuntimeError("WALGIT_TOKEN must be exported to delete Walgit repos")
hdrs = {"Authorization": f"Bearer {tok}"}
if sa:
    now = int(time.time())
    p = urllib.parse.urlparse(base)
    aud = f"https://{p.netloc}/*"
    payload = json.dumps({"iss": sa, "sub": sa, "aud": aud, "iat": now, "exp": now + 300})
    with tempfile.NamedTemporaryFile("w") as inf, tempfile.NamedTemporaryFile("r") as outf:
        inf.write(payload); inf.flush()
        subprocess.check_call(["gcloud", "iam", "service-accounts", "sign-jwt", "--iam-account", sa, "--quiet", inf.name, outf.name], stderr=subprocess.DEVNULL)
        hdrs["Proxy-Authorization"] = f"Bearer {outf.read().strip()}"
req = urllib.request.Request(f"{base}/{repo}/api", method="DELETE", headers=hdrs)
try:
    with urllib.request.urlopen(req, timeout=20) as resp:
        print(f"Deleted Walgit repo {repo} (HTTP {resp.status})")
except urllib.error.HTTPError as e:
    if e.code == 404:
        print(f"Walgit repo {repo} already absent (HTTP 404)")
    else:
        raise RuntimeError(f"DELETE {base}/{repo}/api -> HTTP {e.code}: {e.read().decode()}")
PY
    fi
  done
fi

# 2. Farm Table collections (Spanner + Firestore)
if [[ -n "$FARMTABLE_COLLECTION_IDS" ]]; then
  echo "== 2. Farm Table collections (${FARMTABLE_COLLECTION_IDS}) =="
  if [[ -n "$SPANNER_PROJECT" && -n "$SPANNER_INSTANCE" && -n "$SPANNER_DATABASE" ]]; then
    IFS=',' read -ra _CIDS <<< "$FARMTABLE_COLLECTION_IDS"
    SQL_LIST=""
    for _cid in "${_CIDS[@]}"; do
      [[ -n "$_cid" ]] || continue
      SQL_LIST+="${SQL_LIST:+, }'${_cid}'"
    done
    if [[ -n "$SQL_LIST" ]]; then
      for _tbl in WatchEventsLog LinkedAccounts Tasks Collections; do
        run gcloud spanner databases execute-sql "$SPANNER_DATABASE" \
          --instance="$SPANNER_INSTANCE" --project="$SPANNER_PROJECT" \
          --sql="DELETE FROM ${_tbl} WHERE CollectionId IN (${SQL_LIST})" --quiet
      done
    fi
  fi
  if [[ -n "$FIRESTORE_PROJECT" ]]; then
    if [[ "$PLAN_ONLY" == "1" ]]; then
      printf '+ firestore delete collections=[%s] project=%s\n' "$FARMTABLE_COLLECTION_IDS" "$FIRESTORE_PROJECT" >&2
    else
      FS_PROJECT="$FIRESTORE_PROJECT" FS_CIDS="$FARMTABLE_COLLECTION_IDS" python3 - <<'PY'
import json, os, subprocess, urllib.error, urllib.request
proj = os.environ["FS_PROJECT"]
cids = [c.strip() for c in os.environ["FS_CIDS"].split(",") if c.strip()]
tok = subprocess.check_output(["gcloud", "auth", "print-access-token"]).decode().strip()
base = f"https://firestore.googleapis.com/v1/projects/{proj}/databases/(default)/documents"
def fs_req(method, url, body=None):
    data = json.dumps(body).encode() if body is not None else None
    hdrs = {"Authorization": f"Bearer {tok}", "x-goog-user-project": proj}
    if data is not None:
        hdrs["Content-Type"] = "application/json"
    r = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    with urllib.request.urlopen(r, timeout=30) as resp:
        txt = resp.read().decode()
        return json.loads(txt) if txt.strip() else {}
deleted = 0
for col in ("tasks", "comments", "changes", "linked_accounts", "watch_events"):
    res = fs_req("POST", f"{base}:runQuery", {
        "structuredQuery": {
            "from": [{"collectionId": col}],
            "where": {"fieldFilter": {"field": {"fieldPath": "collection_id"}, "op": "IN", "value": {"arrayValue": {"values": [{"stringValue": c} for c in cids]}}}},
        }
    })
    for row in res:
        doc = row.get("document")
        if not doc:
            continue
        name = doc["name"]
        if col == "tasks":
            try:
                subs = fs_req("GET", f"https://firestore.googleapis.com/v1/{name}/relationships")
                for sd in subs.get("documents", []):
                    fs_req("DELETE", f"https://firestore.googleapis.com/v1/{sd['name']}")
                    deleted += 1
            except Exception:
                pass
        fs_req("DELETE", f"https://firestore.googleapis.com/v1/{name}")
        deleted += 1
for cid in cids:
    try:
        fs_req("DELETE", f"{base}/collections/{cid}")
        deleted += 1
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
print(f"Firestore documents deleted: {deleted}")
PY
    fi
  fi
fi

# 3. Shared/Dedicated GKE Namespace
echo "== 3. GKE namespace (${HUB_NAME}) =="
run gcloud container clusters get-credentials "$HUB_GKE_CLUSTER" --region="$HUB_GKE_REGION" --project="$HUB_GKE_PROJECT" --quiet
run kubectl delete namespace "$HUB_NAME" --ignore-not-found

# 4. Cross-project IAP bindings cleanup
if [[ -n "$EXTRA_CROSS_IAP_SERVICES" ]]; then
  echo "== 4. Cross-project IAP bindings =="
  IFS=',' read -ra _X_IAP <<< "$EXTRA_CROSS_IAP_SERVICES"
  for _xs in "${_X_IAP[@]}"; do
    IFS=':' read -r _xp _xr _xn <<< "$_xs"
    [[ -n "$_xp" && -n "$_xr" && -n "$_xn" ]] || continue
    for _sa in "${HUB_NAME}-agent@${HUB_PROJECT}.iam.gserviceaccount.com" "${HUB_NAME}-hub@${HUB_PROJECT}.iam.gserviceaccount.com" "${SHARED_PREFIX}-operator@${HUB_PROJECT}.iam.gserviceaccount.com"; do
      run gcloud iap web remove-iam-policy-binding --resource-type=cloud-run --service="$_xn" --region="$_xr" --project="$_xp" --role="roles/iap.httpsResourceAccessor" --member="serviceAccount:${_sa}" --quiet || true
    done
  done
fi

# 5. AlloyDB PSC allowedConsumerProjects cleanup
PROJ_NUM=""
if [[ "$PLAN_ONLY" == "1" ]]; then
  PROJ_NUM="<projectNumber:${HUB_PROJECT}>"
else
  PROJ_NUM="$(gcloud projects describe "$HUB_PROJECT" --format='value(projectNumber)' 2>/dev/null || true)"
fi
if [[ -n "$ALLOYDB_PROJECT" && -n "$ALLOYDB_CLUSTER" && -n "$ALLOYDB_INSTANCE" && -n "$PROJ_NUM" ]]; then
  echo "== 5. AlloyDB PSC allowlist (${ALLOYDB_INSTANCE}) =="
  if [[ "$PLAN_ONLY" == "1" ]]; then
    run gcloud alloydb instances update "$ALLOYDB_INSTANCE" --cluster="$ALLOYDB_CLUSTER" --region="$ALLOYDB_REGION" --project="$ALLOYDB_PROJECT" --allowed-psc-projects="<remaining-without-${PROJ_NUM}>" --async --quiet
  else
    CUR_PSC="$(gcloud alloydb instances describe "$ALLOYDB_INSTANCE" --cluster="$ALLOYDB_CLUSTER" --region="$ALLOYDB_REGION" --project="$ALLOYDB_PROJECT" --format='value(pscInstanceConfig.allowedConsumerProjects)' 2>/dev/null || true)"
    NEW_PSC="$(python3 -c "import sys; vals=[x for x in sys.argv[1].split(';') if x and x != sys.argv[2]]; print(','.join(vals))" "$CUR_PSC" "$PROJ_NUM")"
    if [[ -n "$CUR_PSC" && "$NEW_PSC" != "${CUR_PSC//;/,}" ]]; then
      gcloud alloydb instances update "$ALLOYDB_INSTANCE" --cluster="$ALLOYDB_CLUSTER" --region="$ALLOYDB_REGION" --project="$ALLOYDB_PROJECT" --allowed-psc-projects="$NEW_PSC" --async --quiet || true
    fi
  fi
fi

# 6. Shared GCS bucket + GCP project deletion
if [[ -n "$SHARED_GCS_BUCKET" ]]; then
  echo "== 6. Shared GCS bucket (gs://${SHARED_GCS_BUCKET}) =="
  run gcloud storage rm -r "gs://${SHARED_GCS_BUCKET}" --quiet || true
fi

if [[ "$DELETE_PROJECT" == "1" ]]; then
  echo "== 7. GCP Project (${HUB_PROJECT}) =="
  run gcloud projects delete "$HUB_PROJECT" --quiet
fi

echo "== Teardown complete for ${HUB_PROJECT} =="
