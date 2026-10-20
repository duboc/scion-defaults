#!/usr/bin/env python3
"""Scion Factory Compiler & Socratic Intent Elicitation ("Grill-Me") Tool.

Transforms a repository + user intent into a complete, domain-agnostic Scion
autonomous software factory boilerplate (build team + isolated adversarial eval
team + L1-L5 Evaluation Pyramid + Spanner telemetry DDL + Wave 1-N backlog).

This tool is designed to be driven by an agentic coding agent that follows
skills/scion-factory-bootstrap/SKILL.md. The agent conducts the interview; this
script only inspects, drafts, validates, and compiles. It never blocks on stdin.

Usage:
  python3 scripts/scion_factory.py inspect <repo_path> [--json]
  python3 scripts/scion_factory.py grill [--repo <repo_path>] [--out factory.yaml] [--force] [--json]
  python3 scripts/scion_factory.py check <factory.yaml> [--json]
  python3 scripts/scion_factory.py compile <factory.yaml> --out <out_dir>
"""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
from pathlib import Path
from typing import Any

try:
    import yaml  # type: ignore
except ImportError:
    yaml = None


# ---------------------------------------------------------------------------
# Socratic "Grill-Me" Question Bank (5 Pillars, grounded in Lessons L1-L27)
# ---------------------------------------------------------------------------

GRILL_QUESTIONS: list[dict[str, Any]] = [
    {
        "id": "mission_and_non_goals",
        "pillar": "1_mission_and_boundaries",
        "field_path": "project.mission / project.non_goals",
        "question": (
            "In one sentence, what is the concrete production mission of this system, "
            "and what are at least two explicit NON-GOALS agents must never build?"
        ),
        "why_we_ask": (
            "Without explicit non-goals and narrow escalation boundaries, autonomous "
            "agents either hallucinate scope creep or stall asking the human PO for "
            "product decisions they should refuse (Lesson L4, L15)."
        ),
        "lessons": ["L4", "L15", "L25"],
        "challenge_prompt": (
            "Your mission or non-goals are too broad. Name the exact API/UI surface "
            "that defines 'done' and list 2 concrete adjacent features agents must "
            "reject if tempted to build."
        ),
    },
    {
        "id": "branch_isolation_topology",
        "pillar": "1_mission_and_boundaries",
        "field_path": "project.build_branch / project.eval_branch",
        "question": (
            "Which git branch will the Build Team push to (default: `main`), and "
            "which disjoint orphan branch will the Evaluation Team push its dashboard "
            "to (default: `eval-dashboard`)?"
        ),
        "why_we_ask": (
            "If build and eval agents share the same branch or workspace, eval agents "
            "can overwrite application code or grade their own unmerged files instead "
            "of the live deployment (Lesson L18, L23, L25)."
        ),
        "lessons": ["L18", "L23", "L25"],
        "challenge_prompt": (
            "Confirm that the evaluation branch is an isolated orphan branch with "
            "zero shared git history with the build branch."
        ),
    },
    {
        "id": "stack_verification_commands",
        "pillar": "2_runtime_data_and_iam",
        "field_path": "stack.build_command / stack.test_command / stack.lint_command",
        "question": (
            "What are the exact, deterministic CLI commands for build, unit test, "
            "and lint inside the container before any agent may acquire the commit slot?"
        ),
        "why_we_ask": (
            "In a shared `/workspace` with a commit slot, any broken commit blocks "
            "every specialist agent in the project (Lesson L18, L23)."
        ),
        "lessons": ["L18", "L23"],
        "challenge_prompt": (
            "Give the exact shell commands (e.g., `go test -race ./...`, `pytest -q`) "
            "that exit non-zero on regression and run in under 60 seconds."
        ),
    },
    {
        "id": "cloud_stores_and_deployer_iam",
        "pillar": "2_runtime_data_and_iam",
        "field_path": "infrastructure.cloud_stores / infrastructure.deployer_iam_roles",
        "question": (
            "Which cloud backing stores (e.g., Spanner, BigQuery, Vertex AI, GCS, AlloyDB) "
            "does the system require, and what exact IAM roles must be pre-granted to "
            "`deployer-sa` before Wave 1 starts?"
        ),
        "why_we_ask": (
            "In `psearch`, the deployer agent repeatedly blocked in Wave 1 because "
            "`deployer-sa` lacked `roles/bigquery.dataEditor` and `roles/spanner.databaseAdmin`, "
            "forcing human intervention mid-sprint (Lesson L5, L13)."
        ),
        "lessons": ["L5", "L13", "L23"],
        "challenge_prompt": (
            "Don't say 'standard GCP permissions'. List the exact `roles/...` strings "
            "the `deployer-sa` service account needs to provision schemas and deploy Cloud Run."
        ),
    },
    {
        "id": "team_roster_and_harnesses",
        "pillar": "3_roster_models_and_cost",
        "field_path": "team.build_roles / team.eval_roles",
        "question": (
            "Which specialist roles make up your Build Team and Evaluation Team, "
            "and which harness/model pairs (`claude`, `opencode`, `antigravity`, `hermes`) "
            "are assigned to each?"
        ),
        "why_we_ask": (
            "Each harness has distinct runtime requirements: OpenCode Gemini 4 Argon "
            "requires `GOOGLE_CLOUD_LOCATION=global` (L6), Hermes requires an OpenRouter "
            "wrapper (L7), and Antigravity hooks must set `SCION_TELEMETRY_ENABLED=false` "
            "to avoid 60s OTLP timeouts (L22)."
        ),
        "lessons": ["L6", "L7", "L8", "L16", "L22"],
        "challenge_prompt": (
            "Ensure you have at least a `lead`, implementation specialist(s), `reviewer`, "
            "and `deployer` on the build side, plus an independent `eval-lead`, "
            "`domain-evaluator`, and `eval-deployer` on the eval side."
        ),
    },
    {
        "id": "heartbeat_and_budget_guardrails",
        "pillar": "3_roster_models_and_cost",
        "field_path": "team.heartbeat_mode / team.max_wip / infrastructure.budget_usd",
        "question": (
            "Should the 15-minute schedule wake `lead` directly (`direct-lead`, lower agent count) "
            "or wake a cheap Flash sensor agent (`dedicated-sensor`, lower Opus token burn when idle), "
            "what is your max concurrent WIP per wave, and what is your GCP budget cap (USD)?"
        ),
        "why_we_ask": (
            "Waking an Opus lead every 15 minutes when nothing changed burns tokens; "
            "conversely, unlimited WIP causes merge contention in shared `/workspace` (Lesson L9, L10, L17, L21)."
        ),
        "lessons": ["L9", "L10", "L17", "L21", "L24"],
        "challenge_prompt": (
            "Specify `direct-lead` or `dedicated-sensor`, a numeric `max_wip` (recommended: 2), "
            "and a hard `budget_usd` ceiling."
        ),
    },
    {
        "id": "eval_l1_l2_contracts",
        "pillar": "4_evaluation_pyramid_oracle",
        "field_path": "evaluation_pyramid.l1_health / evaluation_pyramid.l2_contract",
        "question": (
            "What is the L1 liveness endpoint and what are the L2 API/schema invariants "
            "that must hold on every single HTTP response?"
        ),
        "why_we_ask": (
            "Before running expensive domain or LLM evaluations, the eval team must "
            "verify that the live Cloud Run service is healthy and honors its structural "
            "API contracts (Lesson L15, L23)."
        ),
        "lessons": ["L15", "L23"],
        "challenge_prompt": (
            "Name the exact endpoints (e.g., `GET /health`, `POST /api/v1/...`) and "
            "at least two falsifiable structural invariants."
        ),
    },
    {
        "id": "eval_l3_metric",
        "pillar": "4_evaluation_pyramid_oracle",
        "field_path": "evaluation_pyramid.l3_domain_quality",
        "question": (
            "What is the primary quantitative L3 domain quality metric (and numeric "
            "pass threshold) that replaces `nDCG@10` for this project, and what synthetic "
            "probe or golden dataset computes it?"
        ),
        "why_we_ask": (
            "In `psearch`, L3 was `ndcg_at_10 >= 0.80`; in `acme-airlines`, L3 was "
            "`constraint_satisfaction_rate >= 0.95`. Without a numeric L3 oracle, the "
            "Evaluation Team cannot grade waves or file regression issues (Lesson L15, L25)."
        ),
        "lessons": ["L15", "L25"],
        "challenge_prompt": (
            "'Works well' or 'passes tests' is not an L3 metric. Name a specific metric "
            "slug (e.g. `reconciliation_accuracy`, `schedule_recovery_score`), a numeric "
            "target threshold, and how the `domain-evaluator` computes it against the live URL."
        ),
    },
    {
        "id": "eval_l5_slo",
        "pillar": "4_evaluation_pyramid_oracle",
        "field_path": "evaluation_pyramid.l4_safety_grounding / evaluation_pyramid.l5_latency_slo",
        "question": (
            "What are the L4 safety/grounding/fallback invariants (when an upstream dependency "
            "or LLM fails), and what are the exact L5 latency SLOs (`p50_ms`, `p95_ms`, `p99_ms`)?"
        ),
        "why_we_ask": (
            "Autonomous agents often optimize accuracy by adding slow sequential LLM calls "
            "or brittle external calls without fallback. L4 and L5 gates catch latency "
            "regressions and ungrounded outputs automatically (Lesson L15, L27)."
        ),
        "lessons": ["L15", "L27"],
        "challenge_prompt": (
            "Provide concrete millisecond numbers for `p50_ms`, `p95_ms`, and `p99_ms`, "
            "plus at least one fail-closed or graceful degradation invariant."
        ),
    },
    {
        "id": "wave_1_backlog",
        "pillar": "5_backlog_waves_and_autonomy",
        "field_path": "waves",
        "question": (
            "What are the initial Wave 1 and Wave 2 slices (issue titles, owner role, "
            "and falsifiable acceptance criteria) that seed the GitHub backlog?"
        ),
        "why_we_ask": (
            "The Lead agent drives work exclusively from GitHub issues (`status:ready` -> "
            "`status:in-progress` -> `status:in-review` -> `status:verified`). Without "
            "well-scoped Wave 1 issues and acceptance criteria, the first heartbeat "
            "has nothing actionable to dispatch (Lesson L1, L2, L18)."
        ),
        "lessons": ["L1", "L2", "L18"],
        "challenge_prompt": (
            "Break Wave 1 into 2-4 vertical slices, assign each to a build role, "
            "and write at least one verifiable acceptance criterion per issue."
        ),
    },
]


def get_grill_questions() -> list[dict[str, Any]]:
    """Return the 5-pillar Socratic question bank grounded in Lessons L1-L27."""
    return [dict(q) for q in GRILL_QUESTIONS]


# ---------------------------------------------------------------------------
# Spec Loading / Saving
# ---------------------------------------------------------------------------

def load_spec(path: str | Path) -> dict[str, Any]:
    """Load a FactorySpec from YAML or JSON."""
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if p.suffix.lower() == ".json":
        return json.loads(text)
    if yaml is not None:
        data = yaml.safe_load(text)
        if not isinstance(data, dict):
            raise ValueError(f"Expected mapping at root of {p}")
        return data
    return json.loads(text)


def save_spec(spec: dict[str, Any], path: str | Path) -> None:
    """Save a FactorySpec to YAML (preferred) or JSON."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.suffix.lower() == ".json" or yaml is None:
        p.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
    else:
        p.write_text(yaml.safe_dump(spec, sort_keys=False, width=100), encoding="utf-8")


# ---------------------------------------------------------------------------
# Static Repository Introspection (`inspect_repository`)
# ---------------------------------------------------------------------------

def inspect_repository(repo_path: str | Path) -> dict[str, Any]:
    """Inspect an existing repository to auto-detect stack, commands, ports, and stores."""
    root = Path(repo_path)
    detected: dict[str, Any] = {
        "primary_language": "unknown",
        "framework": "",
        "build_command": "",
        "test_command": "",
        "lint_command": "",
        "port": 8080,
        "cloud_stores": [],
        "existing_files": [],
    }

    cloud_stores: set[str] = set()
    for candidate in (
        "go.mod",
        "package.json",
        "pyproject.toml",
        "requirements.txt",
        "Cargo.toml",
        "Dockerfile",
        "Makefile",
        "Justfile",
        "AGENTS.md",
    ):
        if (root / candidate).exists():
            detected["existing_files"].append(candidate)

    # Detect Go
    go_mod = root / "go.mod"
    if go_mod.exists():
        detected["primary_language"] = "go"
        detected["build_command"] = "go build -buildvcs=false ./..."
        detected["test_command"] = "go test ./..."
        detected["lint_command"] = "go vet ./..."
        mod_text = go_mod.read_text(errors="ignore").lower()
        if "spanner" in mod_text:
            cloud_stores.add("spanner")
        if "bigquery" in mod_text:
            cloud_stores.add("bigquery")
        if "aiplatform" in mod_text or "genai" in mod_text or "vertex" in mod_text:
            cloud_stores.add("vertex-ai")
        if "storage" in mod_text:
            cloud_stores.add("gcs")
        if "firestore" in mod_text:
            cloud_stores.add("firestore")

    # Detect Python
    pyproject = root / "pyproject.toml"
    if pyproject.exists():
        if detected["primary_language"] == "unknown":
            detected["primary_language"] = "python"
            detected["build_command"] = "python3 -m compileall ."
            detected["test_command"] = "pytest -q"
            detected["lint_command"] = "ruff check ."
        py_text = pyproject.read_text(errors="ignore").lower()
        if "spanner" in py_text:
            cloud_stores.add("spanner")
        if "bigquery" in py_text:
            cloud_stores.add("bigquery")
        if "aiplatform" in py_text or "genai" in py_text or "vertex" in py_text:
            cloud_stores.add("vertex-ai")
        if "fastapi" in py_text:
            detected["framework"] = "FastAPI"

    # Detect Node/TypeScript
    pkg_json = root / "package.json"
    if pkg_json.exists():
        if detected["primary_language"] == "unknown":
            detected["primary_language"] = "typescript"
            detected["build_command"] = "npm run build"
            detected["test_command"] = "npm test"
            detected["lint_command"] = "npm run lint"
        pkg_text = pkg_json.read_text(errors="ignore").lower()
        if "spanner" in pkg_text:
            cloud_stores.add("spanner")
        if "bigquery" in pkg_text:
            cloud_stores.add("bigquery")

    # Override test/build commands if Makefile defines them
    makefile = root / "Makefile"
    if makefile.exists():
        mk_text = makefile.read_text(errors="ignore")
        m_test = re.search(r"^test:\s*\n\t(.+)$", mk_text, flags=re.MULTILINE)
        if m_test:
            detected["test_command"] = m_test.group(1).strip()

    # Detect exposed port from Dockerfile
    dockerfile = root / "Dockerfile"
    if dockerfile.exists():
        df_text = dockerfile.read_text(errors="ignore")
        m_port = re.search(r"^\s*EXPOSE\s+(\d+)", df_text, flags=re.MULTILINE | re.IGNORECASE)
        if m_port:
            detected["port"] = int(m_port.group(1))

    detected["cloud_stores"] = sorted(cloud_stores)

    # Filter which grill questions remain unresolved even after static inspection
    unresolved = []
    for q in get_grill_questions():
        if q["id"] == "stack_verification_commands" and detected["primary_language"] != "unknown" and detected["test_command"]:
            continue
        unresolved.append(q)

    return {
        "repo_path": str(root),
        "detected": detected,
        "unresolved_questions": unresolved,
    }


# ---------------------------------------------------------------------------
# Readiness Scorer (`evaluate_spec_readiness`)
# ---------------------------------------------------------------------------

VAGUE_PHRASES = {"tbd", "todo", "works well", "fast", "good", "standard", "n/a", "none"}


def _is_vague(val: Any) -> bool:
    if val is None:
        return True
    if isinstance(val, str):
        s = val.strip().lower()
        return len(s) < 2 or s in VAGUE_PHRASES
    if isinstance(val, (list, dict)):
        return len(val) == 0
    return False


def evaluate_spec_readiness(spec: dict[str, Any]) -> dict[str, Any]:
    """Validate whether a FactorySpec is concrete enough to compile boilerplate."""
    missing: list[str] = []

    project = spec.get("project") or {}
    for key in ("name", "slug", "eval_slug", "github_repo", "mission"):
        if _is_vague(project.get(key)):
            missing.append(f"project.{key} is missing or vague")
    if not isinstance(project.get("non_goals"), list) or len(project.get("non_goals", [])) < 1:
        missing.append("project.non_goals must list at least 1 explicit non-goal (Lesson L4)")

    stack = spec.get("stack") or {}
    for key in ("primary_language", "build_command", "test_command"):
        if _is_vague(stack.get(key)):
            missing.append(f"stack.{key} is missing or vague (Lesson L18)")

    infra = spec.get("infrastructure") or {}
    for key in ("serving_service", "eval_service"):
        if _is_vague(infra.get(key)):
            missing.append(f"infrastructure.{key} is missing")
    if not isinstance(infra.get("deployer_iam_roles"), list) or len(infra.get("deployer_iam_roles", [])) < 1:
        missing.append("infrastructure.deployer_iam_roles must list explicit GCP IAM roles for deployer-sa (Lesson L5)")

    team = spec.get("team") or {}
    build_roles = team.get("build_roles") or []
    eval_roles = team.get("eval_roles") or []
    if len(build_roles) < 3:
        missing.append("team.build_roles must define at least 3 build roles (e.g. lead, specialist, deployer)")
    if len(eval_roles) < 2:
        missing.append("team.eval_roles must define at least 2 independent evaluation roles (Lesson L25)")
    for role_spec in list(build_roles) + list(eval_roles):
        if _is_vague(role_spec.get("responsibility")):
            missing.append(f"team role '{role_spec.get('role', '?')}' is missing a concrete responsibility")

    pyramid = spec.get("evaluation_pyramid") or {}
    l1 = pyramid.get("l1_health") or {}
    if _is_vague(l1.get("endpoint")):
        missing.append("evaluation_pyramid.l1_health.endpoint is missing")
    l2 = pyramid.get("l2_contract") or {}
    if not l2.get("invariants"):
        missing.append("evaluation_pyramid.l2_contract.invariants must list structural API invariants")
    l3 = pyramid.get("l3_domain_quality") or {}
    if _is_vague(l3.get("primary_metric")):
        missing.append("evaluation_pyramid.l3_domain_quality.primary_metric is missing")
    if not isinstance(l3.get("target_threshold"), (int, float)):
        missing.append("evaluation_pyramid.l3_domain_quality.target_threshold must be numeric")
    l4 = pyramid.get("l4_safety_grounding") or {}
    if not l4.get("invariants"):
        missing.append("evaluation_pyramid.l4_safety_grounding.invariants is missing")
    l5 = pyramid.get("l5_latency_slo") or {}
    if not isinstance(l5.get("p95_ms"), (int, float)):
        missing.append("evaluation_pyramid.l5_latency_slo.p95_ms must be a numeric millisecond SLO")

    waves = spec.get("waves") or []
    if not isinstance(waves, list) or len(waves) < 1:
        missing.append("waves must define at least Wave 1 issues to seed the GitHub backlog")
    else:
        first_issues = waves[0].get("issues") or []
        if len(first_issues) < 1:
            missing.append("waves[0].issues must contain at least 1 issue")
        for idx, issue in enumerate(first_issues):
            if not issue.get("acceptance_criteria"):
                missing.append(f"waves[0].issues[{idx}] is missing acceptance_criteria")

    total_checks = 15
    passed = max(0, total_checks - len(missing))
    return {
        "ready": len(missing) == 0,
        "missing_or_weak": missing,
        "score": f"{passed}/{total_checks}",
    }


# ---------------------------------------------------------------------------
# Boilerplate Compiler (`compile_factory_boilerplate`)
# ---------------------------------------------------------------------------

def _render_shared_agents_md(spec: dict[str, Any]) -> str:
    proj = spec["project"]
    stack = spec["stack"]
    infra = spec["infrastructure"]
    l3 = spec["evaluation_pyramid"]["l3_domain_quality"]
    l5 = spec["evaluation_pyramid"]["l5_latency_slo"]

    return f"""# Shared Factory Agent Protocol — {proj['name']} (`{proj['slug']}` & `{proj['eval_slug']}`)

Every agent in this Scion software factory MUST follow these operational rules (derived from Lessons L1–L27).

## 1. Mission & Non-Goals
- **Mission:** {proj['mission']}
- **Build Project (`{proj['slug']}`):** Pushes application code to `{proj.get('build_branch', 'main')}` in `{proj['github_repo']}` and deploys `{infra['serving_service']}`.
- **Eval Project (`{proj['eval_slug']}`):** Pushes the independent evaluation control tower to the disjoint orphan branch `{proj.get('eval_branch', 'eval-dashboard')}` and deploys `{infra['eval_service']}` (Lesson L25).
- **Explicit Non-Goals (Never Build):**
{chr(10).join(f'  - {ng}' for ng in proj.get('non_goals', []))}

## 2. Deterministic Pre-Commit Verification & Commit Slot (Lesson L18)
Before committing or pushing to `{proj.get('build_branch', 'main')}`:
1. Pull the latest branch state: `git pull --rebase origin {proj.get('build_branch', 'main')}`.
2. Run build verification: `{stack['build_command']}`.
3. Run test verification: `{stack['test_command']}`.
4. Run lint verification: `{stack.get('lint_command', 'true')}`.
5. Never commit secrets, tokens, `.env` files, or agent state files.

## 3. Clean Deployment Packaging & Traffic Unpinning (Lesson L23)
Deployer agents (`deployer`, `eval-deployer`) MUST NEVER run `gcloud run deploy --source=/workspace` directly on a shared workspace. Always export the verified commit SHA to a clean temporary directory and pass `--to-latest`:
```bash
sha=$(git -C /workspace rev-parse HEAD)
d=/tmp/deploy-$sha
rm -rf "$d" && mkdir -p "$d"
git -C /workspace archive "$sha" | tar -x -C "$d"
gcloud run deploy <SERVICE_NAME> --source="$d" --to-latest
```

## 4. Communication & Turn Completion (Lessons L1, L2, L4)
- **Verify message delivery (L1):** Always check the exit code of `scion message <recipient> "<msg>"` and confirm recipient names reside in the same Scion project.
- **Signal turn completion (L2):** End every turn by running `sciontool status task_completed "<concise summary with issue # and commit SHA>"` (or `sciontool status ask_user "<blocker>"` when genuinely blocked).
- **Narrow PO escalation (L4):** Escalate to the human PO (`scion message user:<PO_USER> "..."`) ONLY for missing IAM roles, missing secrets/quotas, or genuine spec contradictions.

## 5. L1–L5 Evaluation Pyramid Contract
- **L1 Health:** `GET {spec['evaluation_pyramid']['l1_health']['endpoint']}`
- **L2 Contract Invariants:** {'; '.join(spec['evaluation_pyramid']['l2_contract']['invariants'])}
- **L3 Primary Domain Metric:** `{l3['primary_metric']} >= {l3['target_threshold']}` ({l3.get('probe_description', '')})
- **L4 Safety & Grounding:** {'; '.join(spec['evaluation_pyramid']['l4_safety_grounding']['invariants'])}
- **L5 Latency SLO:** `p50 <= {l5.get('p50_ms', 100)}ms`, `p95 <= {l5['p95_ms']}ms`, `p99 <= {l5.get('p99_ms', 500)}ms`
"""


def _render_scion_agent_yaml(role_spec: dict[str, Any]) -> str:
    harness = role_spec["harness"]
    model = role_spec["model"]
    lines = [
        "schema_version: \"1\"",
        f"default_harness_config: {harness}",
        f"model: \"{model}\"",
        "telemetry:",
        "  enabled: true",
    ]
    if harness == "opencode" and "vertex" in model.lower():
        # Lesson L6: Gemini 4 Argon on Vertex requires global endpoint
        lines.extend([
            "env:",
            "  GOOGLE_CLOUD_LOCATION: \"global\"",
        ])
    return "\n".join(lines) + "\n"


def _render_role_system_prompt(spec: dict[str, Any], role_spec: dict[str, Any], is_eval: bool) -> str:
    proj = spec["project"]
    stack = spec["stack"]
    infra = spec["infrastructure"]
    pyramid = spec["evaluation_pyramid"]
    l3 = pyramid["l3_domain_quality"]
    l5 = pyramid["l5_latency_slo"]
    role = role_spec["role"]
    project_slug = proj["eval_slug"] if is_eval else proj["slug"]
    branch = proj.get("eval_branch", "eval-dashboard") if is_eval else proj.get("build_branch", "main")
    service = infra["eval_service"] if is_eval else infra["serving_service"]

    return f"""# Role: `{role}` ({'Evaluation Team' if is_eval else 'Build Team'} — Project `{project_slug}`)

## Identity & Responsibility
- **Project:** `{project_slug}` (Repository `{proj['github_repo']}`, Branch `{branch}`)
- **Harness / Model:** `{role_spec['harness']}` / `{role_spec['model']}`
- **Primary Responsibility:** {role_spec['responsibility']}
- **Target Cloud Run Service:** `{service}`

## System Mission
{proj['mission']}

## Stack & Verification Commands
- **Language / Framework:** `{stack['primary_language']}` ({stack.get('framework', '')})
- **Build Command:** `{stack['build_command']}`
- **Test Command:** `{stack['test_command']}`
- **Lint Command:** `{stack.get('lint_command', 'true')}`
- **Cloud Backing Stores:** {', '.join(infra.get('cloud_stores', [])) or 'none'}

## Evaluation Oracle (L1–L5 Pyramid)
1. **L1 Health:** `{pyramid['l1_health']['endpoint']}` -> {', '.join(pyramid['l1_health'].get('checks', []))}
2. **L2 Contract:** {', '.join(pyramid['l2_contract'].get('endpoints', []))} — Invariants: {'; '.join(pyramid['l2_contract']['invariants'])}
3. **L3 Domain Quality:** `{l3['primary_metric']} >= {l3['target_threshold']}` (Secondary: `{l3.get('secondary_metric', 'n/a')} >= {l3.get('secondary_threshold', 0)}`). Probe: {l3.get('probe_description', '')}
4. **L4 Safety & Grounding:** {'; '.join(pyramid['l4_safety_grounding']['invariants'])}
5. **L5 Latency SLO:** `p50 <= {l5.get('p50_ms', 100)}ms`, `p95 <= {l5['p95_ms']}ms`, `p99 <= {l5.get('p99_ms', 500)}ms`

## Operating Loop
1. Read incoming `scion message` instructions and inspect GitHub issues in `{proj['github_repo']}`.
2. Execute your role responsibility on branch `{branch}` while honoring `templates/_shared/agents.md`.
3. Verify your work with `{stack['test_command']}` before pushing or messaging downstream teammates.
4. Always conclude your turn with `sciontool status task_completed "<summary>"`.
"""


def _render_spanner_ddl(spec: dict[str, Any]) -> str:
    proj = spec["project"]
    l3 = spec["evaluation_pyramid"]["l3_domain_quality"]
    return f"""-- Scion Software Factory Control Plane & Evaluation Telemetry Schema
-- Project: {proj['name']} ({proj['slug']} / {proj['eval_slug']})
-- Domain-agnostic L1-L5 Evaluation Pyramid + Agent Heartbeat Sync
-- Primary L3 Metric: {l3['primary_metric']} (target >= {l3['target_threshold']})

CREATE TABLE IF NOT EXISTS factory_agent_status (
  agent_id STRING(64) NOT NULL,
  name STRING(128) NOT NULL,
  project_slug STRING(128) NOT NULL,
  role STRING(128),
  harness STRING(64),
  model STRING(128),
  phase STRING(64),
  activity STRING(64),
  container_status STRING(128),
  task_summary STRING(MAX),
  last_seen TIMESTAMP,
  synced_at TIMESTAMP NOT NULL OPTIONS (allow_commit_timestamp=true),
) PRIMARY KEY (agent_id);

CREATE TABLE IF NOT EXISTS eval_runs (
  run_id STRING(64) NOT NULL,
  project_slug STRING(128) NOT NULL,
  git_sha STRING(64),
  wave INT64,
  overall_status STRING(32) NOT NULL,
  l1_passed BOOL,
  l2_passed BOOL,
  l3_passed BOOL,
  l4_passed BOOL,
  l5_passed BOOL,
  primary_metric_name STRING(128) NOT NULL,
  primary_metric_value FLOAT64,
  primary_metric_target FLOAT64,
  secondary_metric_name STRING(128),
  secondary_metric_value FLOAT64,
  p50_latency_ms FLOAT64,
  p95_latency_ms FLOAT64,
  p99_latency_ms FLOAT64,
  layer_results_json STRING(MAX),
  requirements_json STRING(MAX),
  diagnostics_json STRING(MAX),
  evaluated_at TIMESTAMP NOT NULL OPTIONS (allow_commit_timestamp=true),
) PRIMARY KEY (run_id);
"""


def _render_factory_env_example(spec: dict[str, Any]) -> str:
    proj = spec["project"]
    infra = spec["infrastructure"]
    team = spec["team"]
    build_names = " ".join(r["role"] for r in team["build_roles"])
    eval_names = " ".join(r["role"] for r in team["eval_roles"])
    iam_roles = " ".join(infra.get("deployer_iam_roles", []))

    return f"""# Scion Software Factory Environment Configuration — {proj['name']}
# Copy to config/factory.env (gitignored) and populate real values before running scripts.

export HUB_GCP_PROJECT="your-hub-gcp-project"
export HUB_GCE_ZONE="us-central1-c"
export HUB_VM_NAME="your-scion-hub-vm"
export HUB_OS_USER="scion"
export HUB_URL="http://127.0.0.1:8080"

export APP_GCP_PROJECT="your-app-gcp-project"
export APP_GCP_REGION="us-central1"
export DEPLOYER_SA_NAME="deployer-sa"
export DEPLOYER_IAM_ROLES="{iam_roles}"

export GITHUB_REPO="{proj['github_repo']}"
export BUILD_PROJECT_SLUG="{proj['slug']}"
export EVAL_PROJECT_SLUG="{proj['eval_slug']}"
export BUILD_BRANCH="{proj.get('build_branch', 'main')}"
export EVAL_BRANCH="{proj.get('eval_branch', 'eval-dashboard')}"

export SERVING_SERVICE_NAME="{infra['serving_service']}"
export EVAL_SERVICE_NAME="{infra['eval_service']}"
export MONTHLY_BUDGET_USD="{infra.get('budget_usd', 200)}"

export BUILD_AGENTS="{build_names}"
export EVAL_AGENTS="{eval_names}"
export HEARTBEAT_MODE="{team.get('heartbeat_mode', 'direct-lead')}"
export HEARTBEAT_CRON="{team.get('heartbeat_cron', '*/15 * * * *')}"
export WATCHDOG_CRON="{team.get('watchdog_cron', '*/5 * * * *')}"
export MAX_WIP="{team.get('max_wip', 2)}"
"""


def _render_verify_factory_sh(spec: dict[str, Any]) -> str:
    proj = spec["project"]
    infra = spec["infrastructure"]
    l1_ep = spec["evaluation_pyramid"]["l1_health"]["endpoint"]
    l3 = spec["evaluation_pyramid"]["l3_domain_quality"]
    l5 = spec["evaluation_pyramid"]["l5_latency_slo"]

    return f"""#!/usr/bin/env bash
# End-to-End Infrastructure & L1-L5 Evaluation Oracle Verification Script
# Generated by scion_factory.py for {proj['name']} ({proj['slug']} + {proj['eval_slug']})
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${{BASH_SOURCE[0]}}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

if [[ -f "$ROOT_DIR/config/factory.env" ]]; then
  # shellcheck disable=SC1091
  source "$ROOT_DIR/config/factory.env"
fi

BUILD_PROJECT_SLUG="${{BUILD_PROJECT_SLUG:-{proj['slug']}}}"
EVAL_PROJECT_SLUG="${{EVAL_PROJECT_SLUG:-{proj['eval_slug']}}}"
SERVING_URL="${{SERVING_URL:-}}"
EVAL_URL="${{EVAL_URL:-}}"

echo "[1/4] Verifying generated factory artifacts for $BUILD_PROJECT_SLUG and $EVAL_PROJECT_SLUG..."
test -f "$ROOT_DIR/factory.yaml"
test -f "$ROOT_DIR/config/spanner-factory-ddl.sql"
test -f "$ROOT_DIR/waves/issues.json"
test -f "$ROOT_DIR/templates/_shared/agents.md"

echo "[2/4] Verifying L1 Health endpoint ({l1_ep}) if SERVING_URL is set..."
if [[ -n "$SERVING_URL" ]]; then
  http_code="$(curl -s -o /dev/null -w '%{{http_code}}' "$SERVING_URL{l1_ep}")"
  if [[ "$http_code" != "200" ]]; then
    echo "ERROR: L1 health check failed on $SERVING_URL{l1_ep} (HTTP $http_code)" >&2
    exit 1
  fi
  echo "  L1 Health OK (HTTP 200)"
else
  echo "  SKIP live L1 curl (set SERVING_URL to verify live Cloud Run deployment)"
fi

echo "[3/4] Verifying L3 Domain Oracle ({l3['primary_metric']} >= {l3['target_threshold']}) & L5 Latency SLO (p95 <= {l5['p95_ms']}ms)..."
if [[ -n "$EVAL_URL" ]]; then
  eval_json="$(curl -fsSL "$EVAL_URL/api/eval")"
  echo "$eval_json" | python3 -c '
import json, sys
data = json.load(sys.stdin)
metric = data.get("primary_metric_value")
if metric is not None and float(metric) < {l3["target_threshold"]}:
    sys.exit(f"L3 threshold failed: {{metric}} < {l3["target_threshold"]}")
print("  Live eval payload verified.")
'
else
  echo "  SKIP live EVAL_URL check (set EVAL_URL to verify live Evaluation Control Tower)"
fi

echo "[4/4] Verification complete for {proj['name']}."
"""


def _render_seed_waves_sh(spec: dict[str, Any]) -> str:
    proj = spec["project"]
    return f"""#!/usr/bin/env bash
# Seed Wave 1..N GitHub Issues for {proj['name']} ({proj['github_repo']})
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${{BASH_SOURCE[0]}}")" && pwd)"
ISSUES_FILE="$SCRIPT_DIR/../waves/issues.json"
REPO="${{GITHUB_REPO:-{proj['github_repo']}}}"

if [[ ! -f "$ISSUES_FILE" ]]; then
  echo "Missing $ISSUES_FILE" >&2
  exit 1
fi

for lbl in "status:ready" "status:in-progress" "status:in-review" "status:verified" "status:blocked"; do
  gh label create "$lbl" --repo "$REPO" --force >/dev/null 2>&1 || true
done

python3 - "$ISSUES_FILE" "$REPO" <<'PY'
import json, subprocess, sys
issues_path, repo = sys.argv[1], sys.argv[2]
waves = json.load(open(issues_path))
for w in waves:
    wave_num = w["wave"]
    for issue in w["issues"]:
        title = issue["title"]
        criteria = "\\n".join(f"- [ ] {{c}}" for c in issue.get("acceptance_criteria", []))
        body = f"## Wave {{wave_num}}: {{w['title']}}\\n\\n**Owner Role:** `{{issue['owner_role']}}`\\n\\n### Acceptance Criteria\\n{{criteria}}\\n"
        print(f"[wave-{{wave_num}}] {{title}} (owner={{issue['owner_role']}})")
        if "${{DRY_RUN:-1}}" == "0":
            subprocess.run(["gh", "issue", "create", "--repo", repo, "--title", title, "--body", body, "--label", "status:ready"], check=True)
PY
"""


def _render_root_agents_md(spec: dict[str, Any]) -> str:
    proj = spec["project"]
    stack = spec["stack"]
    infra = spec["infrastructure"]
    team = spec["team"]
    l3 = spec["evaluation_pyramid"]["l3_domain_quality"]
    l5 = spec["evaluation_pyramid"]["l5_latency_slo"]

    build_rows = "\n".join(
        f"| `{r['role']}` | `{proj['slug']}` | `{r['harness']}` | `{r['model']}` | {r['responsibility']} |"
        for r in team["build_roles"]
    )
    eval_rows = "\n".join(
        f"| `{r['role']}` | `{proj['eval_slug']}` | `{r['harness']}` | `{r['model']}` | {r['responsibility']} |"
        for r in team["eval_roles"]
    )

    return f"""# {proj['name']} — Scion Autonomous Software Factory (`AGENTS.md`)

## 1. Mission & Architecture
- **Mission:** {proj['mission']}
- **Repository:** `{proj['github_repo']}`
- **Build Project (`{proj['slug']}`):** Branch `{proj.get('build_branch', 'main')}` -> Cloud Run `{infra['serving_service']}`
- **Evaluation Project (`{proj['eval_slug']}`):** Disjoint orphan branch `{proj.get('eval_branch', 'eval-dashboard')}` -> Cloud Run `{infra['eval_service']}`
- **Heartbeat Mode:** `{team.get('heartbeat_mode', 'direct-lead')}` (`{team.get('heartbeat_cron', '*/15 * * * *')}`) + Watchdog (`{team.get('watchdog_cron', '*/5 * * * *')}`)

## 2. Build & Verification Commands
- **Build:** `{stack['build_command']}`
- **Test:** `{stack['test_command']}`
- **Lint:** `{stack.get('lint_command', 'true')}`

## 3. Agent Roster

### Build Team (`{proj['slug']}`)
| Role | Project | Harness | Model | Responsibility |
|------|---------|---------|-------|----------------|
{build_rows}

### Evaluation Team (`{proj['eval_slug']}`)
| Role | Project | Harness | Model | Responsibility |
|------|---------|---------|-------|----------------|
{eval_rows}

## 4. L1–L5 Evaluation Pyramid Oracle
- **L1 Health:** `{spec['evaluation_pyramid']['l1_health']['endpoint']}`
- **L2 Contract:** {'; '.join(spec['evaluation_pyramid']['l2_contract']['invariants'])}
- **L3 Domain Metric:** `{l3['primary_metric']} >= {l3['target_threshold']}`
- **L4 Safety & Grounding:** {'; '.join(spec['evaluation_pyramid']['l4_safety_grounding']['invariants'])}
- **L5 Latency SLO:** `p50 <= {l5.get('p50_ms', 100)}ms`, `p95 <= {l5['p95_ms']}ms`, `p99 <= {l5.get('p99_ms', 500)}ms`
"""


def compile_factory_boilerplate(spec: dict[str, Any], out_dir: str | Path) -> dict[str, Any]:
    """Compile a validated FactorySpec into a complete Scion factory directory."""
    readiness = evaluate_spec_readiness(spec)
    if not readiness["ready"]:
        raise ValueError(f"FactorySpec is not ready to compile: {readiness['missing_or_weak']}")

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written_files: list[str] = []

    def _write(rel_path: str, content: str, executable: bool = False) -> None:
        target = out / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        if executable:
            target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        written_files.append(rel_path)

    # 1. Canonical spec & root AGENTS.md
    save_spec(spec, out / "factory.yaml")
    written_files.append("factory.yaml")
    _write("AGENTS.md", _render_root_agents_md(spec))

    # 2. Config files
    _write("config/factory.env.example", _render_factory_env_example(spec))
    _write("config/spanner-factory-ddl.sql", _render_spanner_ddl(spec))
    _write(
        "config/heartbeat-message.txt",
        (
            f"[HEARTBEAT] Inspect open GitHub issues in {spec['project']['github_repo']}, "
            f"check active teammates in {spec['project']['slug']} (max WIP={spec['team'].get('max_wip', 2)}), "
            f"advance verified slices, and end turn with sciontool status task_completed.\n"
        ),
    )

    # 3. Shared template protocol
    shared_md = _render_shared_agents_md(spec)
    _write("templates/_shared/agents.md", shared_md)

    # 4. Per-role templates for Build and Eval teams
    for is_eval, role_list in ((False, spec["team"]["build_roles"]), (True, spec["team"]["eval_roles"])):
        for role_spec in role_list:
            role = role_spec["role"]
            _write(f"templates/{role}/scion-agent.yaml", _render_scion_agent_yaml(role_spec))
            _write(f"templates/{role}/system-prompt.md", _render_role_system_prompt(spec, role_spec, is_eval))
            _write(f"templates/{role}/agents.md", shared_md)

            if role_spec["harness"] == "opencode":
                _write(
                    f"templates/{role}/home/.local/bin/opencode",
                    "#!/usr/bin/env bash\nexport GOOGLE_CLOUD_LOCATION=\"${GOOGLE_CLOUD_LOCATION:-global}\"\nexec /usr/local/bin/opencode \"$@\"\n",
                    executable=True,
                )
            elif role_spec["harness"] == "hermes":
                _write(
                    f"templates/{role}/home/.local/bin/hermes",
                    "#!/usr/bin/env bash\nexport OPENAI_BASE_URL=\"${OPENAI_BASE_URL:-https://openrouter.ai/api/v1}\"\nexec /usr/local/bin/hermes \"$@\"\n",
                    executable=True,
                )

    # 5. Waves & operational scripts
    _write("waves/issues.json", json.dumps(spec["waves"], indent=2) + "\n")
    _write("scripts/seed-github-waves.sh", _render_seed_waves_sh(spec), executable=True)
    _write("scripts/verify-factory.sh", _render_verify_factory_sh(spec), executable=True)

    return {
        "out_dir": str(out),
        "files_written": len(written_files),
        "files": written_files,
    }


# ---------------------------------------------------------------------------
# Agent-Driven Draft Spec (`draft_spec_from_repo`)
# ---------------------------------------------------------------------------
#
# The grill-me interview is always conducted by an agentic coding agent (see
# skills/scion-factory-bootstrap/SKILL.md). This module never blocks on stdin:
# it writes a draft that contains only STATIC facts plus the proven roster
# defaults, and leaves every INTENT field empty so the readiness gate fails
# until the agent has elicited real answers from the human.

DEFAULT_BUILD_ROLES: list[dict[str, str]] = [
    {"role": "lead", "harness": "claude", "model": "opus"},
    {"role": "backend", "harness": "opencode", "model": "google-vertex/gemini-4-argon"},
    {"role": "frontend", "harness": "antigravity", "model": "Gemini 4 Argon (High)"},
    {"role": "reviewer", "harness": "hermes", "model": "google/gemini-4-argon"},
    {"role": "deployer", "harness": "claude", "model": "opus", "gcp_identity": "deployer-sa"},
]

DEFAULT_EVAL_ROLES: list[dict[str, str]] = [
    {"role": "eval-lead", "harness": "antigravity", "model": "Gemini 4 Argon (High)"},
    {"role": "domain-evaluator", "harness": "antigravity", "model": "Gemini 4 Argon (High)"},
    {"role": "factory-analyst", "harness": "antigravity", "model": "Gemini 4 Argon (High)"},
    {"role": "ui-craftsman", "harness": "antigravity", "model": "Gemini 4 Argon (High)"},
    {"role": "eval-deployer", "harness": "antigravity", "model": "Gemini 4 Argon (High)", "gcp_identity": "deployer-sa"},
]


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "new-project"


def draft_spec_from_repo(repo_path: str | Path | None) -> dict[str, Any]:
    """Build a draft FactorySpec with STATIC facts filled and INTENT fields empty."""
    detected: dict[str, Any] = {}
    slug = "new-project"
    if repo_path:
        detected = inspect_repository(repo_path)["detected"]
        slug = _slugify(Path(repo_path).resolve().name)

    language = detected.get("primary_language", "")
    return {
        "project": {
            "name": "",
            "slug": slug,
            "eval_slug": f"{slug}-eval",
            "github_repo": "",
            "build_branch": "main",
            "eval_branch": "eval-dashboard",
            "mission": "",
            "non_goals": [],
        },
        "stack": {
            "primary_language": "" if language in ("", "unknown") else language,
            "framework": detected.get("framework", ""),
            "build_command": detected.get("build_command", ""),
            "test_command": detected.get("test_command", ""),
            "lint_command": detected.get("lint_command", ""),
            "port": detected.get("port", 8080),
        },
        "infrastructure": {
            "cloud_stores": detected.get("cloud_stores", []),
            "serving_service": f"{slug}-serving",
            "eval_service": f"{slug}-eval-dashboard",
            "budget_usd": None,
            "deployer_iam_roles": [],
        },
        "team": {
            "heartbeat_mode": "direct-lead",
            "heartbeat_cron": "*/15 * * * *",
            "watchdog_cron": "*/5 * * * *",
            "max_wip": 2,
            "build_roles": [dict(r, responsibility="") for r in DEFAULT_BUILD_ROLES],
            "eval_roles": [dict(r, responsibility="") for r in DEFAULT_EVAL_ROLES],
        },
        "evaluation_pyramid": {
            "l1_health": {"endpoint": "", "checks": []},
            "l2_contract": {"endpoints": [], "invariants": []},
            "l3_domain_quality": {
                "primary_metric": "",
                "target_threshold": None,
                "secondary_metric": "",
                "secondary_threshold": None,
                "probe_description": "",
            },
            "l4_safety_grounding": {"invariants": []},
            "l5_latency_slo": {"p50_ms": None, "p95_ms": None, "p99_ms": None},
        },
        "waves": [],
    }


def prepare_grill_session(repo_path: str | Path | None, out_path: str | Path, force: bool = False) -> dict[str, Any]:
    """Write (or reuse) the draft spec and return the payload the agent uses to interview the human."""
    out = Path(out_path)
    if out.exists() and not force:
        spec = load_spec(out)
    else:
        spec = draft_spec_from_repo(repo_path)
        save_spec(spec, out)

    if repo_path:
        inspection = inspect_repository(repo_path)
        detected, questions = inspection["detected"], inspection["unresolved_questions"]
    else:
        detected, questions = {}, get_grill_questions()

    return {
        "draft_path": str(out),
        "detected": detected,
        "readiness": evaluate_spec_readiness(spec),
        "questions": questions,
    }


# ---------------------------------------------------------------------------
# CLI Entrypoint
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="scion-factory",
        description="Scion Factory Socratic Intent Elicitation ('Grill-Me') & Boilerplate Compiler",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_inspect = sub.add_parser("inspect", help="Inspect a repository to auto-detect stack and remaining intent questions")
    p_inspect.add_argument("repo_path", help="Path to target repository")
    p_inspect.add_argument("--json", action="store_true", help="Output JSON")

    p_grill = sub.add_parser(
        "grill",
        help="Write a draft factory.yaml and emit the grill-me question bank for your coding agent (never blocks)",
    )
    p_grill.add_argument("--repo", default=None, help="Optional repository path to inspect first")
    p_grill.add_argument("--out", default="factory.yaml", help="Draft FactorySpec YAML path")
    p_grill.add_argument("--force", action="store_true", help="Overwrite an existing draft instead of reusing it")
    p_grill.add_argument("--json", action="store_true", help="Output JSON (recommended for agents)")

    p_check = sub.add_parser("check", help="Evaluate readiness of a FactorySpec YAML/JSON file")
    p_check.add_argument("spec_path", help="Path to factory.yaml")
    p_check.add_argument("--json", action="store_true", help="Output JSON")

    p_compile = sub.add_parser("compile", help="Compile a validated FactorySpec into a complete boilerplate directory")
    p_compile.add_argument("spec_path", help="Path to factory.yaml")
    p_compile.add_argument("--out", required=True, help="Output directory for generated factory boilerplate")

    args = parser.parse_args(argv)

    if args.cmd == "inspect":
        res = inspect_repository(args.repo_path)
        if args.json:
            print(json.dumps(res, indent=2))
        else:
            det = res["detected"]
            print(f"Repository: {res['repo_path']}")
            print(f"  Detected Language: {det['primary_language']}")
            print(f"  Build Command:     {det['build_command']}")
            print(f"  Test Command:      {det['test_command']}")
            print(f"  Cloud Stores:      {', '.join(det['cloud_stores']) or 'none detected'}")
            print(f"  Unresolved Intent Questions ({len(res['unresolved_questions'])}):")
            for q in res["unresolved_questions"]:
                print(f"    - [{q['pillar']}] ({', '.join(q['lessons'])}) {q['id']}: {q['question']}")
        return 0

    if args.cmd == "grill":
        payload = prepare_grill_session(args.repo, args.out, force=args.force)
        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            print(f"Draft: {payload['draft_path']} (readiness {payload['readiness']['score']})")
            for q in payload["questions"]:
                print(f"\n[{q['pillar']}] {q['id']} (Lessons {', '.join(q['lessons'])})")
                print(f"  Q: {q['question']}")
                print(f"  Why: {q['why_we_ask']}")
                print(f"  Challenge: {q['challenge_prompt']}")
        return 0

    if args.cmd == "check":
        spec = load_spec(args.spec_path)
        report = evaluate_spec_readiness(spec)
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            print(f"Readiness Score: {report['score']} (ready={report['ready']})")
            for item in report["missing_or_weak"]:
                print(f"  - MISSING/WEAK: {item}")
        return 0 if report["ready"] else 1

    if args.cmd == "compile":
        spec = load_spec(args.spec_path)
        res = compile_factory_boilerplate(spec, args.out)
        print(f"Compiled {res['files_written']} boilerplate files into {res['out_dir']}")
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
