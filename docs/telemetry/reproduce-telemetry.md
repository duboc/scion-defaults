# How to Reproduce the Telemetry Configuration

This procedure configures Cloud Logging, Cloud Monitoring, and Cloud Trace for the Scion Hub and all agent containers.

## Prerequisites

1. Open a terminal in [`docs/scion/`](../../).
2. Copy [`config/factory.env.example`](../../config/factory.env.example) to `config/factory.env`.
3. Set `HUB_PROJECT_ID`, `HUB_VM`, `HUB_ZONE`, `HUB_OS_USER`, and `SANDBOX_PROJECT` in `config/factory.env`.
4. Authenticate the `gcloud` CLI with a user account that has IAM Admin permissions on `HUB_PROJECT_ID`.

---

## Step 1: Provision GCP Telemetry and Configure the Hub VM

Run [`scripts/setup-telemetry.sh`](../../scripts/setup-telemetry.sh) from [`docs/scion/`](../../):

```bash
DRY_RUN=1 ./scripts/setup-telemetry.sh
./scripts/setup-telemetry.sh
```

This script performs seven actions:

1. Enables `cloudtrace.googleapis.com`, `monitoring.googleapis.com`, and `logging.googleapis.com` in `HUB_PROJECT_ID`.
2. Creates the `scion-telemetry-writer` service account in `HUB_PROJECT_ID`.
3. Grants `roles/cloudtrace.agent`, `roles/logging.logWriter`, and `roles/monitoring.metricWriter` to `scion-telemetry-writer`.
4. Grants writer and viewer roles (`roles/monitoring.viewer`, `roles/logging.viewer`, `roles/cloudtrace.user`) to the Hub VM service account.
5. Creates a key file at `/home/scion/.scion/telemetry-gcp-credentials.json` (mode `0644`) on the Hub VM and copies it into all agent homes and containers.
6. Writes the telemetry variables to `/home/scion/.scion/hub.env` and `/home/scion/.scion/settings.yaml`.
7. Restarts the `scion-hub` systemd service on the Hub VM.

---

## Step 2: Patch `sciontool` in Containers and Base Images

> [!CAUTION]
> Do not run `docker restart` on Scion agent containers.
> Scion creates agent containers with `--rm` (`AutoRemove: true`), so `docker restart` can delete the container.

1. (Optional) If you built a custom Linux `amd64` `sciontool` binary with stacked trace support, copy it to `/home/scion/.scion/sciontool.real` on the Hub VM.
2. Run [`scripts/patch-sciontool-telemetry.sh`](../../scripts/patch-sciontool-telemetry.sh):

```bash
DRY_RUN=1 ./scripts/patch-sciontool-telemetry.sh
./scripts/patch-sciontool-telemetry.sh
```

This script performs three actions:

1. Copies `/home/scion/.scion/sciontool.real` (or backs up `/usr/local/bin/sciontool`) to `/usr/local/bin/sciontool.real` in all 10 containers.
2. Installs the `/usr/local/bin/sciontool` wrapper script in all 10 containers so every hook gets the telemetry environment variables.
3. Commits the same wrapper, binary, and credentials into the four base Docker images (`scion-antigravity:latest`, `scion-claude:latest`, `scion-opencode:latest`, `scion-hermes:latest`).

---

## Step 3: Patch the Harness Hooks

Run the harness patch scripts from [`docs/scion/`](../../) to make sure all harnesses invoke `sciontool hook` with telemetry enabled:

```bash
./scripts/run-on-hub.sh ./scripts/patch-antigravity-hooks.py --apply
./scripts/run-on-hub.sh ./scripts/patch-opencode-and-hermes.py --apply
```

---

## Step 4: Verify Telemetry in GCP

### 1. Check Cloud Trace
Open Cloud Trace Explorer (`https://console.cloud.google.com/traces/list?project=<HUB_PROJECT_ID>`) or run:

```bash
TOKEN=$(gcloud auth print-access-token)
curl -s -H "Authorization: Bearer $TOKEN" \
  "https://cloudtrace.googleapis.com/v1/projects/$HUB_PROJECT_ID/traces?pageSize=5" | jq .
```

- Filter by `RootSpan:agent.turn/` to see complete agent turns.
- Filter by `scion.agent.slug:<agent_name>` to follow one agent.
- Filter by `session_id:<hash>` to follow one conversation across turns.

### 2. Check Cloud Monitoring Metrics
Open Metrics Explorer (`https://console.cloud.google.com/monitoring/metrics-explorer?project=<HUB_PROJECT_ID>`) and query these custom metrics under the `generic_task` resource:

| Metric Type | Description |
|---|---|
| `workload.googleapis.com/gen_ai.tokens.input` | Input tokens used per agent and model |
| `workload.googleapis.com/gen_ai.tokens.output` | Output tokens used per agent and model |
| `workload.googleapis.com/gen_ai.tokens.cached` | Cached input tokens read per agent and model |
| `workload.googleapis.com/gen_ai.tokens.thinking` | Reasoning / thinking tokens used |
| `workload.googleapis.com/gen_ai.api.calls` | Count of LLM API calls |
| `workload.googleapis.com/gen_ai.api.duration` | Latency distribution of LLM API calls (ms) |
| `workload.googleapis.com/agent.tool.calls` | Count of tool calls by `tool_name` and `status` |
| `workload.googleapis.com/agent.tool.duration` | Latency distribution of tool calls (ms) |
| `workload.googleapis.com/agent.session.count` | Count of agent sessions started |

### 3. Check Cloud Logging
Open Logs Explorer (`https://console.cloud.google.com/logs/query?project=<HUB_PROJECT_ID>`) and run:

```text
logName="projects/<HUB_PROJECT_ID>/logs/scion-agents" OR logName="projects/<HUB_PROJECT_ID>/logs/scion_hub"
```

### 4. Check the Scion Hub Metrics API
Run this command on the Hub VM to read per-agent token usage through the Hub API:

```bash
./scripts/run-on-hub.sh ./scripts/scion-admin.py projects
```
Or call `GET /api/v1/agents/<agent-id>/metrics?window=24h` on `http://localhost:8080`.
