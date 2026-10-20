# How Harness Hooks and Stacked Traces Work

Each agent container runs `sciontool init` as PID 1 and executes an LLM harness (`antigravity`, `opencode`, `claude`, or `hermes`).
When the harness runs a model call, executes a tool, or ends a turn, it triggers a **hook** that pipes a JSON event to `sciontool hook --dialect=<harness>`.

---

## 1. How Each Harness Invokes `sciontool hook`

| Harness | Hook Config File | How Events Reach `sciontool` | `SCION_USAGE_SOURCE` |
|---|---|---|---|
| **Antigravity** (`agy`) | `/workspace/.agents/hooks.json` | Pipes JSON (`PreInvocation`, `PostInvocation`, `PreToolUse`, `PostToolUse`, `Stop`) to `sciontool hook --dialect=antigravity` | `hooks` |
| **OpenCode** | `~/.config/opencode/plugins/scion-bridge.js` | Plugin pipes JSON (`session.created`, `message.part.updated.step-finish`, `tool.execute.before`, `tool.execute.after`, `session.idle`) to `sciontool hook --dialect=opencode` | `hooks` |
| **Claude Code** | `~/.claude/settings.json` + OTLP (`127.0.0.1:4317`) | Pipes lifecycle/tool hooks to `sciontool hook --dialect=claude` AND sends native OTLP logs (`api_request`) to PID 1 on `127.0.0.1:4317` | `native` |
| **Hermes** | `~/.hermes/config.yaml` | Pipes hook events to `sciontool hook --dialect=hermes` | `hooks` |

---

## 2. How the `/usr/local/bin/sciontool` Wrapper Works

Docker containers keep the environment variables from container creation time.
To make sure every hook subprocess and PID 1 have the telemetry settings without recreating containers, [`scripts/patch-sciontool-telemetry.sh`](../../scripts/patch-sciontool-telemetry.sh) moves the binary to `/usr/local/bin/sciontool.real` and installs a shell wrapper at `/usr/local/bin/sciontool`:

1. Sets `SCION_TELEMETRY_ENABLED=true`, `SCION_TELEMETRY_CLOUD_ENABLED=true`, and `SCION_TELEMETRY_CLOUD_PROVIDER=gcp`.
2. Sets `SCION_GCP_PROJECT_ID` and `SCION_OTEL_GCP_CREDENTIALS=/home/scion/.scion/telemetry-gcp-credentials.json`.
3. Detects `--dialect=<harness>` from the command arguments and sets `SCION_HARNESS`, `SCION_MODEL`, and `SCION_USAGE_SOURCE`:
   - `claude`: `SCION_USAGE_SOURCE=native` (prevents double-counting tokens between OTLP logs and hooks).
   - `antigravity`, `opencode`, `hermes`: `SCION_USAGE_SOURCE=hooks` (records token usage directly from hook JSON payloads).
4. Executes `/usr/local/bin/sciontool.real "$@"`.

---

## 3. Why Hook Subprocesses Needed Cross-Process State

Every hook event runs in a **new, short-lived OS process** (`sciontool hook`).
Originally, `sciontool` stored active spans in an in-memory `sync.Map`:

- Start hooks (`PreInvocation`, `PreToolUse`) exited before `span.End()` ran, so their spans were lost.
- End hooks (`PostInvocation`, `PostToolUse`, `Stop`) started with an empty `sync.Map` and emitted flat ~0ms root spans with random `TraceID`s and `parentSpanId: null`.

### How `/home/scion/.scion/trace-state.json` Solves This

`sciontool` now persists turn state in `/home/scion/.scion/trace-state.json` (protected by `syscall.Flock` on `trace-state.json.lock`) and uses an `ExplicitIDGenerator` in the OpenTelemetry `TracerProvider`:

1. **Turn Start (`PreInvocation` / `session.created` / `UserPromptSubmit`):**
   - Generates one `TraceID` (32-char hex) and one root `TurnSpanID` (16-char hex) for the turn.
   - Records `turn_start_nano`, `turn_index`, and the previous turn's `prev_trace_id` and `prev_turn_span_id`.
2. **Model Calls (`PreInvocation` -> `PostInvocation` / `step-finish` / Claude `api_request`):**
   - `PreInvocation` saves `PendingModel` (`SpanID`, `StartTimeNano`, `Model`).
   - `PostInvocation` reads `PendingModel` and emits a child span `gen_ai.api.response/<model>` (`ParentSpanID = TurnSpanID`) covering the real `[StartTime, EndTime]` duration and token counts.
   - For Claude Code, PID 1 (`CorrelateAndSynthesizeClaudeSpans`) attaches the active turn's `TraceID` to incoming OTLP `api_request` logs and synthesizes a `gen_ai.api.response/<model>` child span.
3. **Tool Calls (`PreToolUse` -> `PostToolUse` / `tool.execute.before` -> `tool.execute.after`):**
   - `PreToolUse` pushes a `PendingSpan` (`SpanID`, `StartTimeNano`, `ToolName`) onto `PendingTools`.
   - `PostToolUse` pops the matching `PendingSpan`, restores `tool_name` (which Antigravity omits in `PostToolUse`), and emits a child span `agent.tool.result/<tool_name>` (`ParentSpanID = TurnSpanID`) with the real `[StartTime, EndTime]` duration.
4. **Status Calls (`sciontool status <type> "<message>"`):**
   - When an agent runs `sciontool status` inside a tool call, `EmitStatusTelemetry` parents `agent.status/<type>` under the active tool span (`PendingTools[last].SpanID`), creating a 3-level stack.
5. **Turn End (`Stop` / `session.idle`):**
   - Emits the root span `agent.turn/<agent_slug>` (`SpanID = TurnSpanID`, `ParentSpanID = nil`) covering `[TurnStartTime, TurnEndTime]`.
   - Attaches summary attributes (`turn.index`, `turn.model_calls`, `turn.tool_calls`, cumulative token counts, `agent.status`, `scion.previous_trace_id`) and an OpenTelemetry **Span Link** (`link.type = "previous_turn"`) to the previous turn in the same conversation.

```text
agent.turn/<agent_slug>                         [Level 1: Root Span for Turn N]
├── agent.session.start / agent.turn.prompt     [Level 2: Child Span]
├── gen_ai.api.response/<model>                 [Level 2: Child Span with duration + tokens]
├── agent.tool.result/<tool_name>               [Level 2: Child Span with duration + tool_name]
│   └── agent.status/<status_type>              [Level 3: Grandchild Span from sciontool status]
```

---

## 4. How Short-Lived Hook Processes Export `CUMULATIVE` Metrics to Cloud Monitoring

Google Cloud Monitoring requires every `CUMULATIVE` time series point (`gen_ai.tokens.*`, `gen_ai.api.calls`, `agent.tool.calls`) to have an interval `[startTime, endTime]` of at least `1ms` (`endTime > startTime`).

Because `sciontool hook` runs and flushes in a few milliseconds:
- Setting `startTime = endTime` fails in Cloud Monitoring with `Invalid timeSeries[i].points[0].interval`.
- Setting `startTime` to a sliding window (`now - 60s`) causes Cloud Monitoring to reject later hook runs within 60 seconds due to overlapping `startTime`s (`Points must be written in order`).

To satisfy both rules, `metric_streams.go` records `collectorEpoch = time.Now() - 1s` when the process starts and exports `[collectorEpoch, flushTime]` for each short-lived hook invocation.
