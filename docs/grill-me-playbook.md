# Socratic "Grill-Me" Playbook: Bootstrapping a New Scion Software Factory

This playbook explains how to use `scripts/scion-factory` (and the portable [`skills/scion-factory-bootstrap/SKILL.md`](../skills/scion-factory-bootstrap/SKILL.md) skill) to elicit a project's domain contract and compile a complete, domain-agnostic Scion software factory from the [`psearch`](../presets/psearch.factory.yaml) and [`acme-airlines`](../presets/acme-airlines.factory.yaml) learnings.

---

## 1. Architecture: Kernel Invariants vs. Domain Contract

Empirical comparison across two live autonomous factories (`psearch` with 10 agents and `acme-airlines` with 14 agents) shows that a Scion software factory splits cleanly into two layers:

1. **Invariant Factory Kernel (Domain-Agnostic — Ships in `scion-defaults`):**
   - Hub API & SQLite control-plane automation ([`scripts/scion-admin.py`](../scripts/scion-admin.py))
   - 5-minute watchdog circuit-breaker ([`scripts/factory-watchdog.py`](../scripts/factory-watchdog.py))
   - Stacked multi-turn OpenTelemetry waterfall traces ([`patches/sciontool-stacked-traces/`](../patches/sciontool-stacked-traces/))
   - Disjoint branch isolation (`main` for build vs orphan `eval-dashboard` for evaluation, Lesson L25)
   - Clean `git archive` + `--to-latest` Cloud Run deployment packaging (Lesson L23)
   - `scion message` delivery checks (Lesson L1) and `sciontool status` turn completion (Lesson L2)

2. **Project Domain Contract (`FactorySpec` — Elicited via Grill-Me):**
   - Validated against [`config/factory.schema.json`](../config/factory.schema.json)
   - Captures the 5 project-specific pillars: Mission & Boundaries, Runtime/Data/IAM, Roster & Budget, L1–L5 Evaluation Pyramid Oracle, and Wave 1–N Backlog.

---

## 2. The 4-Step CLI Workflow

### Step 1: Auto-Detect Repository Facts (`STATIC` vs `INTENT`)

Point `scion-factory inspect` at any existing repo so you never ask a human what `go.mod`, `pyproject.toml`, `Dockerfile`, or `Makefile` already state:

```bash
./scripts/scion-factory inspect /path/to/your-repo
```

### Step 2: Run the 5-Pillar Socratic "Grill-Me" Inquiry

Run interactively in a terminal (or let your AI coding agent run the interview one question at a time via [`skills/scion-factory-bootstrap/SKILL.md`](../skills/scion-factory-bootstrap/SKILL.md)):

```bash
./scripts/scion-factory grill --repo /path/to/your-repo --out factory.yaml
```

To inspect all 10 Socratic questions, their lesson citations (`L1`–`L27`), and their challenge prompts without blocking on stdin:

```bash
./scripts/scion-factory grill --non-interactive --out /tmp/starter.factory.yaml
```

### Step 3: Enforce the Readiness Gate

Before generating any files, check whether the specification is concrete enough:

```bash
./scripts/scion-factory check factory.yaml
```

The readiness gate blocks compilation if:
- `project.non_goals` is empty (Lesson L4),
- `infrastructure.deployer_iam_roles` is missing (Lesson L5),
- `evaluation_pyramid.l3_domain_quality.target_threshold` is non-numeric (Lesson L15),
- `evaluation_pyramid.l5_latency_slo.p95_ms` is non-numeric (Lesson L15), or
- `waves[0].issues` lacks falsifiable `acceptance_criteria` (Lesson L18).

### Step 4: Compile the Project Boilerplate

```bash
./scripts/scion-factory compile factory.yaml --out ./my-project-factory
```

This generates 38+ files tailored to your project with **zero domain leaks**:
- `factory.yaml` & `AGENTS.md`
- `config/factory.env.example`, `config/heartbeat-message.txt`, `config/spanner-factory-ddl.sql`
- `templates/_shared/agents.md` and `templates/<role>/{scion-agent.yaml,system-prompt.md,agents.md}` for every build and eval agent
- `waves/issues.json` & `scripts/seed-github-waves.sh`
- `scripts/verify-factory.sh` (executable L1–L5 verification script)

---

## 3. Why Every Grill-Me Question Maps to a Real Failure (`L1`–`L27`)

| Question ID | Pillar | What Goes Wrong If You Skip It | Lesson Reference |
|---|---|---|---|
| `mission_and_non_goals` | `1_mission_and_boundaries` | Agents invent out-of-scope features or spam the PO inbox for product decisions. | [L4, L15, L25](lessons-learned.md) |
| `branch_isolation_topology` | `1_mission_and_boundaries` | Eval agents overwrite application code or grade unmerged local files. | [L18, L23, L25](lessons-learned.md) |
| `stack_verification_commands` | `2_runtime_data_and_iam` | A broken commit in shared `/workspace` blocks every specialist in the project. | [L18, L23](lessons-learned.md) |
| `cloud_stores_and_deployer_iam` | `2_runtime_data_and_iam` | `deployer` halts mid-Wave 1 with `PERMISSION_DENIED` on Spanner/BigQuery/Cloud Run. | [L5, L13, L23](lessons-learned.md) |
| `team_roster_and_harnesses` | `3_roster_models_and_cost` | Vertex Gemini 4 Argon 404s without `GOOGLE_CLOUD_LOCATION=global`; Antigravity hooks hang 60s without `SCION_TELEMETRY_ENABLED=false`. | [L6, L7, L8, L16, L22](lessons-learned.md) |
| `heartbeat_and_budget_guardrails` | `3_roster_models_and_cost` | Waking an Opus lead every 15m on an idle repo wastes tokens; uncapped WIP causes git rebase thrash. | [L9, L10, L17, L21, L24](lessons-learned.md) |
| `eval_l1_l2_contracts` | `4_evaluation_pyramid_oracle` | Expensive L3/L4 evaluations run against broken or unmigrated endpoints. | [L15, L23](lessons-learned.md) |
| `eval_l3_metric` | `4_evaluation_pyramid_oracle` | Without a numeric L3 metric (replacing `psearch`'s `ndcg_at_10`), the Eval Team cannot automatically verify waves or open regression issues. | [L15, L25](lessons-learned.md) |
| `eval_l5_slo` | `4_evaluation_pyramid_oracle` | Agents add sequential LLM calls that pass unit tests but blow production latency budgets. | [L15, L27](lessons-learned.md) |
| `wave_1_backlog` | `5_backlog_waves_and_autonomy` | The first 15-minute lead heartbeat has no `status:ready` issues with verifiable acceptance criteria to dispatch. | [L1, L2, L18](lessons-learned.md) |

---

## 4. Included Reference Presets

- [`presets/starter-service.factory.yaml`](../presets/starter-service.factory.yaml) — **Pulse Ledger** (Go + Cloud Spanner double-entry transaction ledger; L3 metric: `reconciliation_accuracy >= 0.999`).
- [`presets/psearch.factory.yaml`](../presets/psearch.factory.yaml) — **Hybrid Product Search** (Go + Spanner/BigQuery/Vertex AI; 10 agents; L3 metric: `ndcg_at_10 >= 0.80`).
- [`presets/acme-airlines.factory.yaml`](../presets/acme-airlines.factory.yaml) — **Acme Airlines IRROPS Recovery** (Python/FastAPI + React + Spanner; 14 agents with dedicated Flash `heartbeat` sensor agents; L3 metric: `constraint_satisfaction_rate >= 0.95`).
