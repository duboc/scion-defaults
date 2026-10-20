# Spec: Serverless factory control plane on Firestore

- **Status:** Accepted for implementation (2026-10-20)
- **Related:** [knowledge plane and optional Git](2026-10-20-knowledge-plane-and-optional-git.md),
  [`docs/telemetry/eval-data-pipeline.md`](../telemetry/eval-data-pipeline.md)

## Goal

The factory doesn't use Cloud Spanner and prefers serverless services. Agent status and evaluation runs move from
Spanner tables to Firestore (Native mode) collections. Nothing in the factory control plane needs a provisioned
instance.

## Non-goals

- Migrating the live reference factory on its Hub. That is an operator task; this change ships the tools for it.
- Rewriting the historical product stacks of the `psearch` and `acme-airlines` presets or the lessons learned. They
  record what ran. Their control planes use Firestore like every other kit.

## Design

### Data contract

Database: `FIRESTORE_DATABASE` (default `(default)`) in `FIRESTORE_PROJECT` (default `SANDBOX_PROJECT`). Server-side
access only: security rules deny all client access; the Hub sync, the eval agents, and the dashboard use IAM.

| Collection | Document ID | Writer | Fields |
|---|---|---|---|
| `factory_agent_status` | `<project_slug>__<agent_name>` | Hub sync (every minute) | `project_slug`, `agent_name`, `agent_id`, `template`, `harness`, `phase`, `activity`, `parent_agent`, `current_turns`, `model_calls` (null when unknown, L9), `last_summary`, `last_activity_at`, `synced_at` (server time) |
| `eval_runs` | `<run_id>` | Eval agents | `run_id`, `project_slug`, `source` (commit SHA or snapshot SHA-256), `wave`, `overall_status`, `l1_passed`…`l5_passed`, `primary_metric_name`, `primary_metric_value`, `primary_metric_target`, `secondary_metric_name`, `secondary_metric_value`, `p50_ms`, `p95_ms`, `p99_ms`, `layer_results` (map), `requirements` (array of `{id, status}`), `diagnostics` (map), `evaluated_at` (server time) |

Composite indexes: `factory_agent_status (project_slug ASC, agent_name ASC)` and
`eval_runs (project_slug ASC, evaluated_at DESC)`. The prune query (`synced_at < cutoff`) uses the automatic
single-field index.

### Components

- `scripts/factory_store.py`: standard-library Firestore REST client and CLI (`list-agents`, `list-eval-runs`,
  `put-eval-run`). Token from the metadata server, falling back to `gcloud auth print-access-token`. Sends
  `x-goog-user-project` with `BILLING_PROJECT` (L12). `--dry-run` prints requests.
- `scripts/sync-factory-agents.py`: same Hub query as before; upserts documents with one `documents:commit` per 500
  writes, then deletes documents whose `synced_at` is older than `PRUNE_MINUTES`.
- `scripts/install-sync-cron.sh`: ships both files to the VM.
- `scripts/setup-sandbox.sh`: creates the composite indexes and grants the dashboard SA `roles/datastore.viewer`.
  `--with-spanner` stays as an opt-in for products that justify it; it no longer creates factory tables.
- Kit (`compile`): `config/firestore/{README.md,firestore.indexes.json,firestore.rules}` replace
  `config/spanner-factory-ddl.sql`; `eval-workspace/tools/factory_store.py` lets eval agents record runs; eval role
  prompts reference it.
- Interview: the cloud-stores question is serverless-first. A provisioned store (Spanner, AlloyDB, Cloud SQL) needs a
  stated reason.
- Starter preset: Cloud Run + Firestore + BigQuery.

## Verification

- Unit tests for value encoding, agent status writes, eval run validation, and the sync dry run against a fake Hub DB.
- The compiled starter kit contains no `spanner` string; Firestore contract files exist; indexes JSON parses.
- Existing tests stay green.
