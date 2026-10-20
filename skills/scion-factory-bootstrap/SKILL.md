---
name: scion-factory-bootstrap
description: Use when a user wants to bootstrap a new Scion autonomous software factory for any repository or domain, or asks to be grilled/interviewed to generate Scion agent templates, evaluation pyramids, and backlog waves from the psearch and acme-airlines learnings.
---

# Scion Factory Bootstrap & Socratic "Grill-Me" Skill

## Overview

This skill turns any target repository or project idea into a complete, domain-agnostic Scion software factory (`FactorySpec` + agent templates + Spanner telemetry DDL + L1–L5 Evaluation Pyramid + Wave 1..N GitHub backlog) using the empirical lessons (`L1`–`L27`) from `psearch` and `acme-airlines`.

**Core principle:** Separate **STATIC FACTS** (auto-detected from the repository) from **HUMAN INTENT** (elicited through a Socratic 1-question-at-a-time "Grill-Me" interview that challenges vague answers before compiling boilerplate).

---

## Phase 1: Static Repository Inspection (`STATIC` vs `INTENT`)

Before asking the user any stack questions, inspect their target repository (if one exists) using `scripts/scion-factory inspect`:

```bash
./scripts/scion-factory inspect /path/to/target-repo --json
```

Present the auto-detected facts (`primary_language`, `build_command`, `test_command`, `lint_command`, `port`, `cloud_stores`) to the user in a concise summary table and ask only for confirmation or overrides.

---

## Phase 2: The 5-Pillar Socratic "Grill-Me" Interview

Conduct the interview **one pillar at a time** (1–2 questions per turn). Whenever an answer is qualitative or vague (e.g., *"it should be fast"*, *"accurate"*, *"standard permissions"*), push back using the concrete failure mode from `docs/lessons-learned.md` (`L1`–`L27`):

| Pillar | Question Focus | Lesson Pushback (`why_we_ask`) | Required Concrete Output |
|---|---|---|---|
| **1. Mission & Boundaries** | Production mission, explicit non-goals, build branch vs disjoint orphan eval branch | **L4, L15, L25:** Without explicit non-goals and branch isolation, agents hallucinate scope or overwrite app code with eval code. | 1-sentence mission, $\ge 2$ explicit non-goals, `build_branch` (`main`) + orphan `eval_branch` (`eval-dashboard`). |
| **2. Runtime, Data & IAM** | Deterministic build/test/lint commands, cloud stores, and `deployer-sa` IAM roles | **L5, L13, L18:** In `psearch`, Wave 1 blocked because `deployer-sa` lacked `roles/bigquery.dataEditor` and `roles/spanner.databaseAdmin`. | Exact CLI commands ($< 60\text{s}$) and exact `roles/...` list for `deployer-sa`. |
| **3. Roster, Models & Cost** | Build roles + Eval roles, harnesses (`claude`, `opencode`, `antigravity`, `hermes`), heartbeat mode (`direct-lead` vs `dedicated-sensor`), WIP cap | **L6, L7, L9, L22:** OpenCode Argon requires `GOOGLE_CLOUD_LOCATION=global`; Antigravity requires `SCION_TELEMETRY_ENABLED=false` in hooks; idle Opus heartbeats burn budget. | Build + Eval role roster, `heartbeat_mode`, `max_wip` (default `2`), and `budget_usd`. |
| **4. L1–L5 Evaluation Pyramid** | L1 health endpoint, L2 contract invariants, **L3 quantitative domain metric + threshold**, L4 grounding/fallback invariants, L5 latency SLOs (`p50`/`p95`/`p99`) | **L15, L25, L27:** Replaces `psearch`'s `ndcg_at_10 >= 0.80` with the target domain's quantitative oracle. Without a numeric L3 threshold and L5 `p95_ms`, the Eval Team cannot grade waves. | Numeric `primary_metric` + `target_threshold`, probe description, and `p50_ms` / `p95_ms` / `p99_ms`. |
| **5. Wave 1–N Backlog** | Initial vertical slices, owner roles, and falsifiable acceptance criteria | **L1, L2, L18:** Lead agents dispatch exclusively from GitHub issues (`status:ready`). Vague issues stall Wave 1 on the first heartbeat. | Wave 1 & Wave 2 issues with `owner_role` and verifiable `acceptance_criteria`. |

---

## Phase 3: Readiness Gate & Boilerplate Compilation

1. Write the elicited specification to `factory.yaml` (following `config/factory.schema.json` and `presets/starter-service.factory.yaml`).
2. Run the readiness gate:
   ```bash
   ./scripts/scion-factory check factory.yaml
   ```
   Do **not** compile if `ready` is `False`; resolve every item in `missing_or_weak` with the user first.
3. Compile the complete project boilerplate:
   ```bash
   ./scripts/scion-factory compile factory.yaml --out /path/to/output-factory
   ```
4. Run the generated verification script syntax and artifact check:
   ```bash
   /path/to/output-factory/scripts/verify-factory.sh
   ```
