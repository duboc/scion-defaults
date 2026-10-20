# `scion-defaults` — Canonical Agent & Operator Guidelines

This repository (`duboc/scion-defaults`) is the canonical blueprint, automation toolkit, and template catalog for deploying and operating an **always-on, self-healing multi-agent software factory on [Scion](https://github.com/GoogleCloudPlatform/scion)**.

## Repository Structure

| Path | Purpose |
|---|---|
| [`README.md`](README.md) | Overview, architecture, what we built, and 12-step replication quickstart |
| [`config/`](config/) | Parameterized environment (`factory.env.example`), Hub `settings.yaml`, OpenCode Vertex AI model catalog (`opencode-models.json`), heartbeat prompt, and Cloud Spanner DDL (`spanner-factory-ddl.sql`) |
| [`scripts/`](scripts/README.md) | Idempotent provisioning, admin API client (`scion-admin.py`), template rendering/pushing, telemetry setup, harness patching, 1-minute Spanner sync, 5-minute self-healing watchdog, backups, and alerting |
| [`templates/`](templates/README.md) | Shared multi-agent coordination protocol (`_shared/agents.md`) + 10 role templates (`lead`, `backend`, `frontend`, `reviewer`, `deployer`, `eval-lead`, `factory-analyst`, `search-evaluator`, `ui-craftsman`, `eval-deployer`) |
| [`docs/`](docs/) | Architecture (`architecture.md`), setup runbook (`setup-runbook.md`), operations runbook (`operations-runbook.md`), agent protocol (`agent-protocol.md`), 5-layer evaluation pyramid (`verification-and-eval.md`), 27 lessons learned (`lessons-learned.md`), and v2 orchestration spec (`specs/`) |
| [`docs/telemetry/`](docs/telemetry/README.md) | ASD-STE100 documentation for reproducing telemetry, the evaluation data pipeline, and harness hooks / stacked Cloud Trace waterfalls |
| [`patches/sciontool-stacked-traces/`](patches/sciontool-stacked-traces/README.md) | Source patch (`sciontool-stacked-traces.patch`) and `tracestate.go` for 3-level stacked Cloud Trace waterfalls and Cloud Monitoring `CUMULATIVE` epoch export |

## Security & Hygiene Rules (Mandatory)

1. **Zero Secrets or Real Project Identifiers in Tracked Files:**
   - Never commit PATs (`ghp_*`, `github_pat_*`, `scion_pat_*`), service account keys, emails, real GCP project IDs/numbers, or VM names.
   - All real environment values live exclusively in the git-ignored `config/factory.env`.
   - Before every commit, run:
     ```bash
     rg -i '(ghp_|github_pat_|scion_pat_|AIza|ya29\.|BEGIN PRIVATE KEY)' . --glob '!config/factory.env'
     ```
2. **Idempotent Scripts with `DRY_RUN=1`:**
   - Every provisioning and patching script in `scripts/` must support `DRY_RUN=1` (or `--dry-run`) and use `set -euo pipefail` + `CLOUDSDK_CORE_DISABLE_PROMPTS=1`.
3. **Never Run `docker restart` on Scion Agent Containers:**
   - Scion creates agent containers with `--rm` (`AutoRemove: true`). Update binaries or credentials in running containers via `docker cp` ([`scripts/patch-sciontool-telemetry.sh`](scripts/patch-sciontool-telemetry.sh)).
