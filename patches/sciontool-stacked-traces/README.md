# `sciontool` Patch: Stacked Cloud Trace Waterfalls and Cloud Monitoring Cumulative Epochs

This folder contains the Go source patch (`sciontool-stacked-traces.patch`) and cross-process trace state module (`tracestate.go`) against Scion commit `69c0be79` (nightly `20261002`).

## What This Patch Fixes

1. **3-Level Stacked Conversation Traces in Cloud Trace (`tracestate.go`, `telemetry.go`, `providers.go`):**
   - Persists turn state in `/home/scion/.scion/trace-state.json` (protected by `syscall.Flock`) and wires `ExplicitIDGenerator` into the OpenTelemetry `TracerProvider`.
   - Emits one root span per turn (`agent.turn/<agent_slug>`) with child spans (`gen_ai.api.response/<model>`, `agent.tool.result/<tool_name>`) and 3-level nested status spans (`agent.status/<status_type>`).
   - Links consecutive turns in a conversation using `scion.previous_trace_id` and OpenTelemetry Span Links (`link.type = "previous_turn"`).
2. **Claude Code Native OTLP Log-to-Span Synthesis (`tracestate.go`, `pipeline.go`):**
   - Attaches the active turn's `TraceID`/`SpanID` to native OTLP logs arriving on `127.0.0.1:4317` and synthesizes `gen_ai.api.response/<model>` child spans for `api_request` events.
3. **Inline Cloud Logging Correlation (`proto_adapter.go`):**
   - Populates top-level `Entry.SpanID`, `Entry.TraceSampled = true`, and `SCION_GCP_PROJECT_ID` fallback so logs appear inline inside Cloud Trace waterfalls.
4. **Cloud Monitoring `CUMULATIVE` Epoch Fix (`metric_streams.go`, `pipeline.go`):**
   - Sets `collectorEpoch = processStartTime - 1s` so short-lived `sciontool hook` processes export valid, non-overlapping `[startTime, endTime]` intervals (`endTime > startTime`) to Google Cloud Monitoring.

## How to Build and Deploy `sciontool.real`

```bash
# 1. Create a detached worktree at 69c0be79 in your Scion checkout and apply the patch
git -C /path/to/scion worktree add --detach /tmp/scion-build-trace 69c0be79
git -C /tmp/scion-build-trace apply /path/to/scion-defaults/patches/sciontool-stacked-traces/sciontool-stacked-traces.patch

# 2. Run the unit tests and build the Linux amd64 binary
cd /tmp/scion-build-trace
TMPDIR=/private/tmp go test -v ./pkg/sciontool/hooks/handlers -run TestTelemetryHandler_CrossProcessStackedTurnWaterfall
GOOS=linux GOARCH=amd64 CGO_ENABLED=0 go build -buildvcs=false -o /tmp/sciontool.real ./cmd/sciontool
git -C /path/to/scion worktree remove --force /tmp/scion-build-trace

# 3. Upload to the Hub VM and patch all running containers and base Docker images
source /path/to/scion-defaults/config/factory.env
HUB_PROJECT_ID="${HUB_PROJECT_ID:-$HUB_PROJECT}"
gcloud compute scp /tmp/sciontool.real "${HUB_VM}:/tmp/sciontool.real" --project "$HUB_PROJECT_ID" --zone "$HUB_ZONE"
gcloud compute ssh "$HUB_VM" --project "$HUB_PROJECT_ID" --zone "$HUB_ZONE" --command \
  "sudo mv /tmp/sciontool.real /home/scion/.scion/sciontool.real && sudo chown scion:scion /home/scion/.scion/sciontool.real && sudo chmod 755 /home/scion/.scion/sciontool.real"
/path/to/scion-defaults/scripts/patch-sciontool-telemetry.sh
```
