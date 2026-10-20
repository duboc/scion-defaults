#!/usr/bin/env bash
# Provision Cloud Monitoring email alerts and GCP billing budgets for the Scion factory.
#
# Idempotent: checks for existing channels, alert policies, and billing budgets before creating.
# Supports DRY_RUN=1.
#
# Reads:
#   config/factory.env (HUB_PROJECT / HUB_PROJECT_ID, SANDBOX_PROJECT / SANDBOX_PROJECT_ID, PO_EMAIL / ADMIN_EMAIL,
#                       optional HUB_MONTHLY_BUDGET_USD, SANDBOX_MONTHLY_BUDGET_USD)
#
# Usage:
#   scripts/setup-alerts-and-budgets.sh
#   DRY_RUN=1 scripts/setup-alerts-and-budgets.sh
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
SANDBOX_PROJECT_ID="${SANDBOX_PROJECT_ID:-${SANDBOX_PROJECT:-}}"
ADMIN_EMAIL="${ADMIN_EMAIL:-${PO_EMAIL:-}}"
HUB_MONTHLY_BUDGET_USD="${HUB_MONTHLY_BUDGET_USD:-150}"
SANDBOX_MONTHLY_BUDGET_USD="${SANDBOX_MONTHLY_BUDGET_USD:-150}"
DRY_RUN="${DRY_RUN:-0}"

if [[ -z "$HUB_PROJECT_ID" || -z "$SANDBOX_PROJECT_ID" || -z "$ADMIN_EMAIL" ]]; then
  echo "ERROR: HUB_PROJECT_ID, SANDBOX_PROJECT_ID, and ADMIN_EMAIL (or PO_EMAIL) must be set in factory.env" >&2
  exit 2
fi

echo "=== Scion Factory Alerts & Budgets Provisioning ==="
echo "Hub Project:     $HUB_PROJECT_ID"
echo "Sandbox Project: $SANDBOX_PROJECT_ID"
echo "Admin Email:     $ADMIN_EMAIL"
echo "Monthly Budgets: Hub=\$${HUB_MONTHLY_BUDGET_USD}, Sandbox=\$${SANDBOX_MONTHLY_BUDGET_USD}"
echo "Dry Run:         $DRY_RUN"
echo ""

# -----------------------------------------------------------------------------
# Step A: Email Notification Channel in HUB_PROJECT_ID
# -----------------------------------------------------------------------------
echo "--- Step A: Cloud Monitoring Notification Channel ($HUB_PROJECT_ID) ---"
CHANNEL_ID=""
CHANNEL_ID="$(gcloud beta monitoring channels list --project="$HUB_PROJECT_ID" \
  --filter="type='email' AND labels.email_address='$ADMIN_EMAIL'" \
  --format="value(name)" 2>/dev/null || true)"

if [[ -n "$CHANNEL_ID" ]]; then
  echo "Notification channel already exists: $CHANNEL_ID"
else
  echo "Creating email notification channel for $ADMIN_EMAIL in $HUB_PROJECT_ID..."
  if [[ "$DRY_RUN" == "1" ]]; then
    echo "[DRY RUN] Would run: gcloud beta monitoring channels create --project=$HUB_PROJECT_ID --type=email ..."
    CHANNEL_ID="projects/$HUB_PROJECT_ID/notificationChannels/dry-run-channel"
  else
    CHANNEL_ID="$(gcloud beta monitoring channels create \
      --project="$HUB_PROJECT_ID" \
      --display-name="Scion Factory PO Email" \
      --type=email \
      --channel-labels="email_address=$ADMIN_EMAIL" \
      --format="value(name)")"
    echo "Created notification channel: $CHANNEL_ID"
  fi
fi
echo ""

# -----------------------------------------------------------------------------
# Step B: Log-Based Alert Policy in HUB_PROJECT_ID
# -----------------------------------------------------------------------------
echo "--- Step B: Log-Based Alert Policy ($HUB_PROJECT_ID) ---"
POLICY_ID=""
POLICY_ID="$(gcloud alpha monitoring policies list --project="$HUB_PROJECT_ID" \
  --filter="displayName='Scion Factory Watchdog & PO Escalation Alerts'" \
  --format="value(name)" 2>/dev/null || true)"

if [[ -n "$POLICY_ID" ]]; then
  echo "Alert policy already exists: $POLICY_ID"
else
  echo "Creating alert policy 'Scion Factory Watchdog & PO Escalation Alerts'..."
  TMP_POLICY="$(mktemp /tmp/scion-alert-policy-XXXXXX.json)"
  cat << EOF > "$TMP_POLICY"
{
  "displayName": "Scion Factory Watchdog & PO Escalation Alerts",
  "combiner": "OR",
  "conditions": [
    {
      "displayName": "Log match: scion-factory-alerts severity >= ERROR",
      "conditionMatchedLog": {
        "filter": "logName=\"projects/${HUB_PROJECT_ID}/logs/scion-factory-alerts\" AND severity>=ERROR"
      }
    }
  ],
  "notificationChannels": [
    "${CHANNEL_ID}"
  ],
  "alertStrategy": {
    "notificationRateLimit": {
      "period": "300s"
    },
    "autoClose": "1800s"
  },
  "documentation": {
    "content": "Scion Software Factory PO Escalation or Watchdog Circuit Breaker event in project ${HUB_PROJECT_ID}. Check Cloud Logging (logName=projects/${HUB_PROJECT_ID}/logs/scion-factory-alerts) or ~/.scion/watchdog.log on the Hub VM.",
    "mimeType": "text/markdown"
  },
  "enabled": true
}
EOF

  if [[ "$DRY_RUN" == "1" ]]; then
    echo "[DRY RUN] Would create policy from $TMP_POLICY"
    cat "$TMP_POLICY"
    rm -f "$TMP_POLICY"
  else
    POLICY_ID="$(gcloud alpha monitoring policies create \
      --project="$HUB_PROJECT_ID" \
      --policy-from-file="$TMP_POLICY" \
      --format="value(name)")"
    rm -f "$TMP_POLICY"
    echo "Created alert policy: $POLICY_ID"
  fi
fi
echo ""

# -----------------------------------------------------------------------------
# Step C: Monthly GCP Billing Budgets
# -----------------------------------------------------------------------------
echo "--- Step C: Monthly GCP Billing Budgets ---"
BA_RAW="$(gcloud billing projects describe "$HUB_PROJECT_ID" --format="value(billingAccountName)" 2>/dev/null || true)"
if [[ -z "$BA_RAW" ]]; then
  echo "WARNING: Could not discover billing account for $HUB_PROJECT_ID. Skipping budget provisioning." >&2
else
  BA="${BA_RAW#billingAccounts/}"
  echo "Discovered Billing Account: $BA"

  for item in "${HUB_PROJECT_ID}:${HUB_MONTHLY_BUDGET_USD}" "${SANDBOX_PROJECT_ID}:${SANDBOX_MONTHLY_BUDGET_USD}"; do
    proj="${item%%:*}"
    budget_usd="${item##*:}"
    budget_display="Scion Factory - ${proj}"

    echo "Checking budget '${budget_display}' on billing account ${BA}..."
    EXISTING_BUDGET="$(gcloud billing budgets list --billing-account="$BA" \
      --filter="displayName='${budget_display}'" \
      --format="value(name)" 2>/dev/null || true)"

    if [[ -n "$EXISTING_BUDGET" ]]; then
      echo "  Budget already exists: $EXISTING_BUDGET"
    else
      echo "  Creating budget '${budget_display}' amount ${budget_usd} USD..."
      if [[ "$DRY_RUN" == "1" ]]; then
        echo "  [DRY RUN] Would run: gcloud billing budgets create --billing-account=$BA --display-name='${budget_display}' --budget-amount=${budget_usd}USD ..."
      else
        # Try attaching monitoring channel if creating for HUB_PROJECT_ID and channel is available
        extra_flags=()
        if [[ "$proj" == "$HUB_PROJECT_ID" && -n "$CHANNEL_ID" && "$CHANNEL_ID" != *"dry-run"* ]]; then
          extra_flags=("--notifications-rule-monitoring-notification-channels=${CHANNEL_ID}")
        fi

        set +e
        BUDGET_RES="$(gcloud billing budgets create \
          --billing-account="$BA" \
          --display-name="${budget_display}" \
          --filter-projects="projects/${proj}" \
          --budget-amount="${budget_usd}USD" \
          --calendar-period=month \
          --threshold-rule=percent=0.5,basis=current-spend \
          --threshold-rule=percent=0.8,basis=current-spend \
          --threshold-rule=percent=1.0,basis=current-spend \
          --threshold-rule=percent=1.2,basis=forecasted-spend \
          "${extra_flags[@]}" \
          --format="value(name)" 2>&1)"
        code=$?
        set -e

        if [[ $code -ne 0 && "${#extra_flags[@]}" -gt 0 ]]; then
          echo "  (retrying without monitoring channel flag due to cross-resource restriction: $BUDGET_RES)"
          BUDGET_RES="$(gcloud billing budgets create \
            --billing-account="$BA" \
            --display-name="${budget_display}" \
            --filter-projects="projects/${proj}" \
            --budget-amount="${budget_usd}USD" \
            --calendar-period=month \
            --threshold-rule=percent=0.5,basis=current-spend \
            --threshold-rule=percent=0.8,basis=current-spend \
            --threshold-rule=percent=1.0,basis=current-spend \
            --threshold-rule=percent=1.2,basis=forecasted-spend \
            --format="value(name)")"
        fi
        echo "  Created budget: $BUDGET_RES"
      fi
    fi
  done
fi

echo ""
echo "=== Alerts & Budgets Verification Complete ==="
