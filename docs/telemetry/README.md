# Scion Telemetry and Evaluation Documentation (ASD-STE100)

This folder explains how telemetry, harness hooks, and evaluation data work in the Scion software factory.
All documents in this folder follow the **ASD-STE100** (Simplified Technical English) standard.

> [!IMPORTANT]
> Do not write secrets, tokens, or real project IDs in tracked files.
> Put your environment values in [`../../config/factory.env`](../../config/factory.env.example).

## Document Map

| Document | Topic |
|---|---|
| [reproduce-telemetry.md](reproduce-telemetry.md) | How to configure and verify Cloud Logging, Cloud Monitoring, and Cloud Trace |
| [eval-data-pipeline.md](eval-data-pipeline.md) | How the Hub and the evaluation runner send data to Cloud Spanner and the dashboard |
| [hooks-and-traces.md](hooks-and-traces.md) | How harness hooks, the `sciontool` wrapper, and stacked Cloud Trace waterfalls work |

## Architecture Overview

The telemetry system has three data paths:

```mermaid
flowchart TD
    subgraph Container["Agent Container"]
        H["LLM Harness (Antigravity / OpenCode / Claude / Hermes)"]
        W["/usr/local/bin/sciontool (Wrapper -> sciontool.real)"]
        TS["/home/scion/.scion/trace-state.json (Flock State)"]
        P1["PID 1: sciontool init (OTLP 127.0.0.1:4317 / :4318)"]
        H -->|"Hook JSON on stdin"| W
        W <-->|"Read / Write TraceID & SpanID"| TS
        H -->|"Native OTLP (Claude)"| P1
        P1 <-->|"Correlate Turn"| TS
    end

    subgraph GCP["Google Cloud Observability (HUB_PROJECT_ID)"]
        CT["Cloud Trace (3-Level Stacked Waterfalls)"]
        CM["Cloud Monitoring (Token & Call Counters)"]
        CL["Cloud Logging (scion-agents & scion_hub)"]
    end

    subgraph Eval["Evaluation Data Pipeline (SANDBOX_PROJECT)"]
        DB[("Hub SQLite (hub.db)")]
        SYNC["sync-factory-agents.py (1-min Cron)"]
        SP[("Cloud Spanner (factory_agent_status & eval_runs)")]
        DASH["psearch-eval-dashboard (Cloud Run)"]
    end

    W -->|"Direct GCP Export"| CT
    W -->|"Direct GCP Export"| CM
    W -->|"Direct GCP Export"| CL
    P1 -->|"GCP Export"| CT
    P1 -->|"GCP Export"| CM
    P1 -->|"GCP Export"| CL

    DB -->|"Read Agent State"| SYNC
    CM -->|"Read API & Tool Counts"| SYNC
    SYNC -->|"Upsert Every 1 Min"| SP
    SP -->|"Read Status & Runs"| DASH
```

## Quick Start (4 Steps)

Run these commands from [`docs/scion/`](../../) on your workstation:

1. Copy [`config/factory.env.example`](../../config/factory.env.example) to `config/factory.env` and set your project variables.
2. Run [`scripts/setup-telemetry.sh`](../../scripts/setup-telemetry.sh) to enable GCP APIs, create the service account, and configure the Hub VM.
3. Run [`scripts/patch-sciontool-telemetry.sh`](../../scripts/patch-sciontool-telemetry.sh) to install the telemetry wrapper in all containers and base images.
4. Run [`scripts/install-sync-cron.sh`](../../scripts/install-sync-cron.sh) to send agent telemetry and status to Cloud Spanner every minute.
