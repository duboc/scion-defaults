---
name: scion-factory-bootstrap
description: Use when a user wants to bootstrap a new Scion autonomous software factory for any repository or domain, or asks to be grilled or interviewed to generate Scion agent templates, an evaluation pyramid, and backlog waves from the scion-defaults lessons.
---

# Scion factory bootstrap

You conduct a structured interview with the human product owner, record the answers in `factory.yaml`, and compile a
Scion software factory kit with the deterministic tool in this repository. The tool never interviews the human and
never waits for keyboard input. You own the conversation; the tool owns detection, validation, and compilation.

Run all commands from the root of the `scion-defaults` repository.

## Rules

- Ask about one topic per turn. Don't send the whole question bank at once.
- Never invent intent. Mission, non-goals, IAM roles, metrics, thresholds, latency targets, and backlog issues come
  from the human. You can propose candidates, but the human must confirm each one.
- Challenge vague answers. When an answer isn't testable, quote the question's `challenge_prompt` and cite its
  lessons from `docs/lessons-learned.md`.
- Don't ask about static facts that the tool already detected. Ask the human to confirm them instead.
- Don't compile until `check` reports `ready=True`.

## Step 1: Detect static facts and write the draft

Run the following command. Replace `TARGET_REPO_PATH` with the target repository, or omit `--repo` if no repository
exists yet:

```bash
./scripts/scion-factory grill --repo TARGET_REPO_PATH --out factory.yaml --json
```

The JSON output contains:

- `draft_path`: The draft specification. Static facts are filled in; intent fields are empty.
- `detected`: The language, commands, port, and cloud stores found in the repository.
- `readiness`: The current readiness report. It fails for a new draft.
- `questions`: The unresolved interview questions, each with `pillar`, `question`, `why_we_ask`, `lessons`,
  `challenge_prompt`, and `field_path`.

If `factory.yaml` already exists, the command reuses it. Add `--force` only when the human wants to start over.

Show the human a short table of the detected facts, and ask them to confirm or correct it.

## Step 2: Interview the human, one topic at a time

Work through the `questions` in pillar order. For each question:

1. Ask the question in plain language, and explain `why_we_ask` in one sentence.
1. If the answer is vague, quote the `challenge_prompt` and ask again.
1. Write the confirmed answer into `factory.yaml` at `field_path`.

| Pillar | Fields to fill |
|---|---|
| `1_mission_and_boundaries` | `project.name`, `project.github_repo`, `project.mission`, `project.non_goals`, branches |
| `2_runtime_data_and_iam` | `stack.*` commands, `infrastructure.cloud_stores`, `infrastructure.deployer_iam_roles` |
| `3_roster_models_and_cost` | `team.build_roles[*].responsibility`, `team.eval_roles[*].responsibility`, `team.heartbeat_mode`, `team.max_wip`, `infrastructure.budget_usd` |
| `4_evaluation_pyramid_oracle` | `evaluation_pyramid.l1_health` through `evaluation_pyramid.l5_latency_slo` |
| `5_backlog_waves_and_autonomy` | `waves[*].issues[*]` with `owner_role` and `acceptance_criteria` |

The draft includes the proven default roster. Ask the human whether to keep, rename, add, or remove roles before you
fill in responsibilities. Use `presets/*.factory.yaml` as worked examples when the human needs inspiration.

## Step 3: Pass the readiness gate

```bash
./scripts/scion-factory check factory.yaml --json
```

For each entry in `missing_or_weak`, return to the matching pillar and resolve it with the human. Repeat until the
report shows `"ready": true`.

## Step 4: Compile and verify the kit

Ask the human for an output directory, `OUTPUT_DIR`, and then run:

```bash
./scripts/scion-factory compile factory.yaml --out OUTPUT_DIR
OUTPUT_DIR/scripts/verify-factory.sh
```

Summarize what was generated, and point the human to `docs/setup-runbook.md` to deploy the factory to a Scion Hub.
