#!/usr/bin/env bash
# Run a local script on the Scion Hub VM as the Hub's OS user.
#
# Why: passing multi-line scripts or markdown through `gcloud compute ssh --command "..."` gets mangled by the
# outer shell (backticks run as commands, quotes break). This helper streams the script over SSH stdin into a
# temporary file on the VM. See lessons-learned.md (L1).
#
# Usage:
#   scripts/run-on-hub.sh scripts/factory-status.sh [args...]
#   scripts/run-on-hub.sh scripts/scion-admin.py schedule list --project psearch
#
# The interpreter is chosen by extension (.py -> python3, anything else -> bash). These factory.env variables
# are forwarded to the remote script's environment: SANDBOX_PROJECT SANDBOX_REGION SPANNER_INSTANCE
# SPANNER_DATABASE BUILD_PROJECT EVAL_PROJECT FACTORY_PROJECTS BILLING_PROJECT GITHUB_REPO PO_EMAIL HUB_LOCAL_URL.
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

if [[ $# -lt 1 ]]; then
  echo "usage: $0 <script> [args...]" >&2
  exit 2
fi
script="$1"
shift
[[ -f "$script" ]] || { echo "no such script: $script" >&2; exit 2; }

interp=bash
[[ "$script" == *.py ]] && interp=python3

forward=()
for var in HUB_PROJECT HUB_PROJECT_ID ADMIN_EMAIL PO_EMAIL SANDBOX_PROJECT SANDBOX_REGION \
           SPANNER_INSTANCE SPANNER_DATABASE BUILD_PROJECT EVAL_PROJECT FACTORY_PROJECTS \
           BILLING_PROJECT GITHUB_REPO HUB_LOCAL_URL; do
  if [[ -n "${!var:-}" ]]; then
    forward+=("$(printf '%s=%q' "$var" "${!var}")")
  fi
done

args=""
if [[ $# -gt 0 ]]; then
  args="$(printf '%q ' "$@")"
fi

user="${HUB_OS_USER:-scion}"
if [[ ! "$user" =~ ^[a-z_][a-z0-9_-]*$ ]]; then
  echo "invalid HUB_OS_USER: $user" >&2
  exit 2
fi

remote="set -e; tmp=\$(sudo -u $user mktemp /tmp/scion-run-XXXXXX); \
trap 'sudo -u $user rm -f \"\$tmp\"' EXIT; \
sudo -u $user tee \"\$tmp\" >/dev/null; \
sudo -u $user chmod 700 \"\$tmp\"; \
sudo -u $user env ${forward[*]} $interp \"\$tmp\" $args"

gcloud compute ssh "$HUB_VM" --project "$HUB_PROJECT" --zone "$HUB_ZONE" --command "$remote" < "$script" 2> >(
  grep -v -e 'NumPy' -e 'increasing_the_tcp' -e 'External IP address was not found' \
          -e '^WARNING: *$' -e 'enterprise-certificate-proxy' >&2
)
