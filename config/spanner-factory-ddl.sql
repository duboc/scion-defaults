-- Factory tables in the sandbox Spanner database (the same database the product uses).
-- Apply with:
--   gcloud spanner databases ddl update "$SPANNER_DATABASE" --instance="$SPANNER_INSTANCE" \
--     --project="$SANDBOX_PROJECT" --ddl-file=config/spanner-factory-ddl.sql
-- (setup-sandbox.sh --with-spanner does this when the tables are missing.)

-- One row per live agent, upserted every minute by scripts/sync-factory-agents.py from the Hub DB.
-- current_turns / model_calls stay NULL while the Hub doesn't populate them (unknown is not zero).
CREATE TABLE factory_agent_status (
  project_slug STRING(128) NOT NULL,
  agent_name STRING(128) NOT NULL,
  agent_id STRING(128),
  template STRING(128),
  harness STRING(128),
  phase STRING(64),
  activity STRING(64),
  parent_agent STRING(128),
  current_turns INT64,
  model_calls INT64,
  last_summary STRING(MAX),
  updated_at TIMESTAMP NOT NULL OPTIONS (allow_commit_timestamp = true),
  last_activity_at TIMESTAMP,
) PRIMARY KEY(project_slug, agent_name);

-- One row per evaluation run, written by the eval team's probe scripts against the live serving URL.
CREATE TABLE eval_runs (
  run_id STRING(128) NOT NULL,
  git_commit STRING(64),
  overall_score FLOAT64,
  ndcg_at_10 FLOAT64,
  mrr_at_10 FLOAT64,
  p50_ms FLOAT64,
  p95_ms FLOAT64,
  p99_ms FLOAT64,
  requirements_json JSON,
  created_at TIMESTAMP NOT NULL OPTIONS (allow_commit_timestamp = true),
) PRIMARY KEY(run_id);
