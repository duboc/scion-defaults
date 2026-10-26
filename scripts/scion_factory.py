#!/usr/bin/env python3
"""Scion Factory Compiler & Socratic Intent Elicitation ("Grill-Me") Tool.

Transforms a repository + user intent into a complete, domain-agnostic Scion
autonomous software factory boilerplate (build team + isolated adversarial eval
team + L1-L5 Evaluation Pyramid + Firestore control plane + Wave 1-N backlog).

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
import copy
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
import discovery  # noqa: E402
import knowledge_plane as kp  # noqa: E402
import role_library  # noqa: E402


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
        "field_path": "vcs.enabled / vcs.repo / vcs.build_branch / vcs.eval_branch",
        "question": (
            "Do you want Git and GitHub now, or start without Git and add it later? "
            "With Git: which repository, which branch does the Build Team push to "
            "(default: `main`), and which disjoint orphan branch does the Evaluation "
            "Team use (default: `eval-dashboard`)? Without Git: the factory uses a file "
            "backlog and snapshot deploys, and `scion-factory enable-git` adds Git later."
        ),
        "why_we_ask": (
            "If build and eval agents share the same branch or workspace, eval agents "
            "can overwrite application code or grade their own unmerged files instead "
            "of the live deployment (Lesson L18, L23, L25)."
        ),
        "lessons": ["L18", "L23", "L25"],
        "challenge_prompt": (
            "If you choose Git, confirm that the evaluation branch is an isolated orphan "
            "branch with zero shared history with the build branch. If you skip Git, "
            "confirm that the evaluation team gets its own Hub project workspace."
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
            "Which cloud backing stores does the system require, and what exact IAM roles "
            "must be pre-granted to `deployer-sa` before Wave 1 starts? Default to serverless "
            "services: Cloud Run, Firestore, BigQuery, Cloud Storage, and Vertex AI."
        ),
        "why_we_ask": (
            "In the reference factory, the deployer agent repeatedly blocked in Wave 1 because "
            "`deployer-sa` lacked data-store roles such as `roles/bigquery.dataEditor`, "
            "forcing human intervention mid-sprint (Lesson L5, L13). Serverless stores also "
            "remove instance sizing and idle cost from the factory's blast radius."
        ),
        "lessons": ["L5", "L13", "L23"],
        "challenge_prompt": (
            "Don't say 'standard GCP permissions'. List the exact `roles/...` strings the "
            "`deployer-sa` service account needs to create indexes or datasets and deploy "
            "Cloud Run. If you want a provisioned store (Spanner, AlloyDB, Cloud SQL) instead "
            "of a serverless one such as Firestore, state the requirement that Firestore or "
            "BigQuery can't meet."
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
            "falsifiable acceptance criteria, and the REQ- IDs each issue delivers) that "
            "seed the backlog (GitHub issues with Git, backlog/issues.json without)?"
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


GRILL_QUESTIONS.extend([
    {
        "id": "knowledge_sources",
        "pillar": "6_knowledge_plane",
        "field_path": "knowledge_plane.sources",
        "question": (
            "Which existing documents define this product: requirements or PRD, "
            "architecture decisions, API or data contracts, design docs, and contributor "
            "guides? Confirm the sources detected in the repository, or name the ones "
            "that live elsewhere."
        ),
        "why_we_ask": (
            "Agents are only as good as the knowledge they can open. When the reviewer "
            "couldn't find the real ADR and migration paths, it guessed file names, "
            "issued a wrong FAIL, and the factory deadlocked for about 60 hours (Lesson L4)."
        ),
        "lessons": ["L4", "L15"],
        "challenge_prompt": (
            "'It's all in people's heads' means the agents start blind. Point to at least "
            "one file per kind, or agree that we write the missing pieces now in this interview."
        ),
    },
    {
        "id": "knowledge_requirements_invariants",
        "pillar": "6_knowledge_plane",
        "field_path": "knowledge_plane.requirements / knowledge_plane.invariants / knowledge_plane.decisions",
        "question": (
            "From those sources (or from scratch), what are the requirements with stable "
            "IDs and acceptance criteria, which MUST rules every change keeps, and which "
            "architecture decisions are already made?"
        ),
        "why_we_ask": (
            "Requirement IDs connect backlog items, ADRs, reviews, and evaluation results. "
            "Invariants are the reviewer's checklist; without them reviews are opinions "
            "and regressions ship (Lesson L15, L18, L25)."
        ),
        "lessons": ["L15", "L18", "L25"],
        "challenge_prompt": (
            "Each requirement needs an ID like REQ-001, a title, and at least one testable "
            "acceptance criterion. Each invariant must be something a reviewer can verify "
            "in a diff or with a command."
        ),
    },
])


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

    # Detect Python (pyproject.toml and/or requirements.txt)
    py_files = [p for p in (root / "pyproject.toml", root / "requirements.txt") if p.exists()]
    if py_files:
        if detected["primary_language"] == "unknown":
            detected["primary_language"] = "python"
            detected["build_command"] = "python3 -m compileall ."
            detected["test_command"] = "pytest -q"
            detected["lint_command"] = "ruff check ."
        py_text = "\n".join(p.read_text(errors="ignore") for p in py_files).lower()
        if "spanner" in py_text:
            cloud_stores.add("spanner")
        if "bigquery" in py_text:
            cloud_stores.add("bigquery")
        if "aiplatform" in py_text or "genai" in py_text or "vertex" in py_text:
            cloud_stores.add("vertex-ai")
        if "firestore" in py_text:
            cloud_stores.add("firestore")
        if "google-cloud-storage" in py_text:
            cloud_stores.add("gcs")
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
    detected["knowledge_sources"] = kp.detect_knowledge_sources(root)
    detected["vcs"] = kp.detect_vcs(root)
    detected["proposed_roles"] = discovery.discover_roles(root)

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
# Team Model: default specialists, explicit drops, autonomy, deploy
# ---------------------------------------------------------------------------
#
# Every roster carries the library's always-roles (role_library.ALWAYS_BUILD / ALWAYS_EVAL). New drafts get them with
# `default: true`; older specs and presets get the missing ones at compile time (`normalize_team`), with a note in the
# kit's AGENTS.md. The PO drops a default only explicitly: `team.dropped_defaults: [{role, reason}]`. A renamed role
# with the same `default_kind` (for example `go-dev` with `default_kind: implementer`) counts as the default.

DEPLOY_MODES = ("service", "none")
AUTONOMY_LEVELS = ("supervised", "standard", "autonomous")


def deploy_mode(spec: dict[str, Any]) -> str:
    """`infrastructure.deploy`: `service` when absent (older specs), otherwise the raw value (the draft's `""` too)."""
    infra = spec.get("infrastructure") or {}
    if "deploy" not in infra:
        return "service"
    value = infra["deploy"]
    return value if isinstance(value, str) else str(value)


def autonomy(spec: dict[str, Any]) -> str:
    """`team.autonomy`, defaulting to `standard` (spec §7.4)."""
    value = (spec.get("team") or {}).get("autonomy")
    if value is None:
        return "standard"
    return value if isinstance(value, str) else str(value)


def _dropped_defaults(spec: dict[str, Any]) -> set[str]:
    drops = (spec.get("team") or {}).get("dropped_defaults") or []
    return {str(d.get("role")) for d in drops if isinstance(d, dict) and d.get("role")}


def _role_kind(role_spec: dict[str, Any]) -> str:
    """A role's `default_kind`: explicit, else the library's for a library role, else the role name."""
    role = str(role_spec.get("role", ""))
    if role_spec.get("default_kind"):
        return str(role_spec["default_kind"])
    lib = role_library.ROLES.get(role)
    return lib["default_kind"] if lib else role


def normalize_team(spec: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Return a deep copy of `spec` with every missing always-role appended, plus notes on what was added.

    A role in `team.dropped_defaults` is never added. A roster role with the same `default_kind` as a missing
    always-role counts as that role, so a renamed implementer doesn't get a second `backend`.
    """
    out = copy.deepcopy(spec)
    team = out.setdefault("team", {})
    dropped = _dropped_defaults(out)
    notes: list[str] = []
    for key, always in (("build_roles", role_library.ALWAYS_BUILD), ("eval_roles", role_library.ALWAYS_EVAL)):
        roster = team.get(key)
        if not isinstance(roster, list):
            roster = []
            team[key] = roster
        names = {str(r.get("role")) for r in roster if isinstance(r, dict)}
        kinds = {_role_kind(r) for r in roster if isinstance(r, dict)}
        for role in always:
            if role in dropped or role in names or role_library.ROLES[role]["default_kind"] in kinds:
                continue
            roster.append(dict(role_library.library_role_entry(role), default=True))
            names.add(role)
            kinds.add(role_library.ROLES[role]["default_kind"])
            notes.append(f"added default role {role}")
    return out, notes


def add_role(spec_path: str | Path, role: str, team: str | None = None, evidence: list[str] | tuple[str, ...] = ()
             ) -> dict[str, Any]:
    """Add a library role (with the evidence discovery found) to a FactorySpec in place. Recompile the kit afterwards.

    Idempotent: a role already in either roster is left as is. Raises ValueError for an unknown role, a bad team, or
    a role that needs a deployed service when the spec says `infrastructure.deploy: none`.
    """
    lib = role_library.ROLES.get(role)
    if lib is None:
        raise ValueError(f"unknown role {role!r}; library roles: {', '.join(sorted(role_library.ROLES))}")
    team_name = team or lib["team"]
    if team_name not in ("build", "eval"):
        raise ValueError(f"--team must be build or eval, not {team_name!r}")
    spec = load_spec(spec_path)
    if lib["needs_service"] and deploy_mode(spec) == "none":
        raise ValueError(f"{role} needs a deployed service, but this factory has infrastructure.deploy: none")
    roster_team = spec.setdefault("team", {})
    existing = [r.get("role") for key in ("build_roles", "eval_roles") for r in (roster_team.get(key) or [])
                if isinstance(r, dict)]
    if role in existing:
        return spec
    entry = dict(role_library.library_role_entry(role), default=lib["kind"] == "always")
    if evidence:
        entry["evidence"] = [str(e) for e in evidence]
    roster_team.setdefault(f"{team_name}_roles", []).append(entry)
    # Adding a dropped default back undoes the drop.
    if roster_team.get("dropped_defaults"):
        roster_team["dropped_defaults"] = [d for d in roster_team["dropped_defaults"]
                                           if not (isinstance(d, dict) and d.get("role") == role)]
    save_spec(spec, spec_path)
    return spec


def _team_readiness(spec: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Errors and warnings for the deploy / autonomy enums, explicit drops, and missing default roles."""
    errors: list[str] = []
    mode = deploy_mode(spec)
    if mode not in DEPLOY_MODES:
        errors.append(f"infrastructure.deploy must be `service` or `none` (got {mode!r}): "
                      "does this factory deploy a service?")
    level = autonomy(spec)
    if level not in AUTONOMY_LEVELS:
        errors.append(f"team.autonomy must be one of {', '.join(AUTONOMY_LEVELS)} (got {level!r})")
    drops = (spec.get("team") or {}).get("dropped_defaults")
    if drops is not None and not isinstance(drops, list):
        errors.append("team.dropped_defaults must be a list of {role, reason}")
        drops = []
    for drop in drops or []:
        if not isinstance(drop, dict) or not drop.get("role"):
            errors.append("team.dropped_defaults entries need a role and a reason")
        elif not str(drop.get("reason") or "").strip():
            errors.append(f"dropped_defaults: {drop['role']} needs a reason")
    warnings = [
        f"team is missing default role {note.removeprefix('added default role ')}: compile adds it "
        "(drop it in team.dropped_defaults with a reason to leave it out)"
        for note in normalize_team(spec)[1]
    ]
    return errors, warnings


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
    for key in ("name", "slug", "eval_slug", "mission"):
        if _is_vague(project.get(key)):
            missing.append(f"project.{key} is missing or vague")
    if not isinstance(project.get("non_goals"), list) or len(project.get("non_goals", [])) < 1:
        missing.append("project.non_goals must list at least 1 explicit non-goal (Lesson L4)")

    vcs = spec.get("vcs") or {}
    if vcs.get("enabled"):
        if not re.fullmatch(r"[\w.-]+/[\w.-]+", str(vcs.get("repo") or "")):
            missing.append("vcs.repo must be OWNER/NAME when vcs.enabled is true")
        for key in ("build_branch", "eval_branch"):
            if _is_vague(vcs.get(key)):
                missing.append(f"vcs.{key} is missing")
        if vcs.get("build_branch") and vcs.get("build_branch") == vcs.get("eval_branch"):
            missing.append("vcs.eval_branch must differ from vcs.build_branch (orphan branch isolation, Lesson L25)")

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
        missing.append("waves must define at least Wave 1 issues to seed the backlog")
    else:
        first_issues = waves[0].get("issues") or []
        if len(first_issues) < 1:
            missing.append("waves[0].issues must contain at least 1 issue")
        for idx, issue in enumerate(first_issues):
            if not issue.get("acceptance_criteria"):
                missing.append(f"waves[0].issues[{idx}] is missing acceptance_criteria")

    if spec.get("monorepo") is not None:
        prefix, subdir = _monorepo(spec)
        parts = subdir.split("/")
        if not subdir or str((spec.get("monorepo") or {}).get("subdir", "")).startswith("/") or ".." in parts:
            missing.append("monorepo.subdir must be the product folder, relative to the repository root (e.g. pkg/kubelet)")
        if not _SLUG_RE.match(prefix):
            missing.append("monorepo.agent_prefix must be a slug (lowercase letters, digits, single dashes), "
                           "normally the build project slug")

    missing.extend(kp.validate_knowledge_plane(spec))
    team_errors, warnings = _team_readiness(spec)
    missing.extend(team_errors)

    total_checks = 17
    passed = max(0, total_checks - len(missing))
    return {
        "ready": len(missing) == 0,
        "missing_or_weak": missing,
        "score": f"{passed}/{total_checks}",
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# Boilerplate Compiler (`compile_factory_boilerplate`)
# ---------------------------------------------------------------------------

def _vcs_enabled(spec: dict[str, Any]) -> bool:
    return bool((spec.get("vcs") or {}).get("enabled"))


# Monorepo factories (docs/plans/2026-10-22-monorepo-factories.md): `monorepo: {subdir, agent_prefix}` makes the kit
# start every agent in `subdir` (factory-cd.sh in the <harness>-factory harness-configs), read the knowledge plane at
# `subdir/knowledge-plane`, and name agents `<agent_prefix>-<role>`.
_SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def _monorepo(spec: dict[str, Any]) -> tuple[str, str]:
    mono = spec.get("monorepo") or {}
    return str(mono.get("agent_prefix") or ""), str(mono.get("subdir") or "").strip().strip("/")


def _ws(spec: dict[str, Any]) -> str:
    """The factory's root inside the checkout: /workspace, or /workspace/<subdir> for a monorepo factory."""
    subdir = _monorepo(spec)[1]
    return f"/workspace/{subdir}" if subdir else "/workspace"


def _agent(spec: dict[str, Any], role: str) -> str:
    prefix = _monorepo(spec)[0]
    return f"{prefix}-{role}" if prefix else role


def _factory_label(spec: dict[str, Any]) -> str:
    """GitHub label that scopes a monorepo factory's backlog: factories in one repository share its issues."""
    prefix, subdir = _monorepo(spec)
    return f"factory:{prefix or spec['project']['slug']}" if subdir else ""


def _monorepo_paths(spec: dict[str, Any], text: str) -> str:
    """Point knowledge-plane and eval-tool paths at the product folder, and name deployers with the prefix."""
    if not _monorepo(spec)[1]:
        return text
    ws = _ws(spec)
    text = text.replace("--label status:ready`", f"--label status:ready --label {_factory_label(spec)}`")
    text = text.replace("/workspace/knowledge-plane", f"{ws}/knowledge-plane")
    text = text.replace("/workspace/tools/", f"{ws}/tools/")
    return text.replace("(`deployer`, `eval-deployer`)",
                        f"(`{_agent(spec, 'deployer')}`, `{_agent(spec, 'eval-deployer')}`)")


def _render_shared_agents_md(spec: dict[str, Any]) -> str:
    text = _render_shared_agents_md_base(spec)
    prefix, subdir = _monorepo(spec)
    if not subdir:
        return text
    team = ", ".join(f"`{_agent(spec, r['role'])}`" for r in spec["team"]["build_roles"])
    evals = ", ".join(f"`{_agent(spec, r['role'])}`" for r in spec["team"]["eval_roles"])
    return _monorepo_paths(spec, text) + f"""
## 8. Monorepo Product Folder
- This factory owns `{subdir}` in the repository. Your terminal starts there; the rest of the repository is read-only
  context unless the lead assigns work outside the folder. Use `git rev-parse --show-toplevel` for the root.
- Agent names carry the factory prefix: build team {team}; evaluation team {evals}. Message them with these names.
- Build and test only what the work touches, inside `{subdir}`; a whole-repository build is not a verification step.
- Other factories share this repository's issues. Your backlog is the issues labeled `{_factory_label(spec)}`: add
  `--label {_factory_label(spec)}` to every `gh issue list` and `gh issue create`, and leave other issues alone.
"""


def _render_shared_agents_md_base(spec: dict[str, Any]) -> str:
    proj = spec["project"]
    stack = spec["stack"]
    infra = spec["infrastructure"]
    vcs = spec.get("vcs") or {}
    l3 = spec["evaluation_pyramid"]["l3_domain_quality"]
    l5 = spec["evaluation_pyramid"]["l5_latency_slo"]
    lint = stack.get("lint_command") or "true"
    git = _vcs_enabled(spec)

    if git:
        topology = (
            f"- **Build Project (`{proj['slug']}`):** Pushes application code to `{vcs['build_branch']}` in "
            f"`{vcs['repo']}` and deploys `{infra['serving_service']}`.\n"
            f"- **Eval Project (`{proj['eval_slug']}`):** Pushes the independent evaluation control tower to the "
            f"disjoint orphan branch `{vcs['eval_branch']}` and deploys `{infra['eval_service']}` (Lesson L25)."
        )
        backlog = f"""## 3. Backlog & Status (Lessons L1, L18)
- The backlog is GitHub issues in `{vcs['repo']}`. Status labels move `status:ready` -> `status:in-progress` -> `status:in-review` -> `status:verified` (or `status:blocked`).
- The lead assigns work and moves labels. Every issue cites the `REQ-` IDs it delivers.
- The PO accepts work by closing the issue: at `status:verified`, the lead comments the evidence (commit, review,
  checks) and messages the PO, and never close an issue yourself.
"""
        integrate = f"""## 4. Pre-Commit Verification & Commit Slot (Lesson L18)
Before committing or pushing to `{vcs['build_branch']}`, ask the lead for the commit slot, then:
1. Pull the latest branch state: `git pull --rebase origin {vcs['build_branch']}`.
2. Run build verification: `{stack['build_command']}`.
3. Run test verification: `{stack['test_command']}`.
4. Run lint verification: `{lint}`.
5. Never commit secrets, tokens, `.env` files, or agent state files.
"""
        deploy = """## 5. Clean Deployment Packaging & Traffic Unpinning (Lesson L23)
Deployer agents (`deployer`, `eval-deployer`) MUST NEVER run `gcloud run deploy --source=/workspace` directly on a shared workspace. Always export the verified commit with `git archive` to a clean temporary directory and pass `--to-latest`:
```bash
sha=$(git -C /workspace rev-parse HEAD)
d=/tmp/deploy-$sha
rm -rf "$d" && mkdir -p "$d"
git -C /workspace archive "$sha" | tar -x -C "$d"
gcloud run deploy <SERVICE_NAME> --source="$d" --to-latest
```
After the deploy, record the revision, the rollback revision, and the commit in `/workspace/knowledge-plane/state/LIVE.md`.
"""
        turn_ref = "issue # and commit SHA"
    else:
        topology = (
            f"- **Build Project (`{proj['slug']}`):** Works in the shared `/workspace` (no version control yet) and "
            f"deploys `{infra['serving_service']}`.\n"
            f"- **Eval Project (`{proj['eval_slug']}`):** Works in its own Hub project workspace, isolated from the "
            f"build workspace, and deploys `{infra['eval_service']}` (Lesson L25)."
        )
        backlog = """## 3. Backlog & Status (Lessons L1, L18)
- The backlog is `/workspace/backlog/issues.json`. Each item has an `id`, `title`, `owner_role`, `requirements`, `acceptance_criteria`, and `status` (`ready` -> `in-progress` -> `in-review` -> `verified`, or `blocked`).
- The lead is the only writer of `backlog/issues.json`. Workers never edit it; they report progress and results to the lead with `scion message`.
- Every item cites the `REQ-` IDs it delivers.
"""
        integrate = f"""## 4. Edit Slot & Verification (Lesson L18)
The shared `/workspace` has no version control, so a bad edit can't be reverted with Git.
1. Ask the lead for the edit slot before you change files outside the paths assigned to you.
2. Run build verification: `{stack['build_command']}`.
3. Run test verification: `{stack['test_command']}`.
4. Run lint verification: `{lint}`.
5. Report the changed paths to the lead, then release the slot. Never write secrets, tokens, or `.env` files.
"""
        deploy = """## 5. Snapshot Deployment Packaging & Traffic Unpinning (Lesson L23)
Deployer agents (`deployer`, `eval-deployer`) MUST NEVER run `gcloud run deploy --source=/workspace` directly. After the reviewer's PASS, take a snapshot while no edit slot is open, deploy the snapshot, and pass `--to-latest`:
```bash
ts=$(date -u +%Y%m%dT%H%M%SZ)
snap=/tmp/deploy-$ts.tgz
tar -C /workspace --exclude=./.scion --exclude=./node_modules --exclude=./.venv -czf "$snap" .
sum=$(sha256sum "$snap" | cut -d' ' -f1)
d=/tmp/deploy-$ts && mkdir -p "$d" && tar -xzf "$snap" -C "$d"
gcloud run deploy <SERVICE_NAME> --source="$d" --to-latest
```
After the deploy, record the revision, the rollback revision, the snapshot path, and its SHA-256 in `/workspace/knowledge-plane/state/LIVE.md`. Keep the last three snapshots for rollback.
"""
        turn_ref = "backlog item ID and snapshot SHA-256"

    return f"""# Shared Factory Agent Protocol — {proj['name']} (`{proj['slug']}` & `{proj['eval_slug']}`)

Every agent in this Scion software factory MUST follow these operational rules (derived from Lessons L1–L27).

## 1. Mission & Non-Goals
- **Mission:** {proj['mission']}
{topology}
- **Explicit Non-Goals (Never Build):**
{chr(10).join(f'  - {ng}' for ng in proj.get('non_goals', []))}

## 2. Knowledge Plane (Lesson L4)
- Build agents read `/workspace/knowledge-plane/INDEX.md` before planning, implementing, reviewing, or deploying. Evaluation agents read the same knowledge plane read-only at `{kp.EVAL_KP_MOUNT}/INDEX.md`.
- Open files through the paths in the index. Never guess a file name.
- Cite `REQ-` and `ADR-` IDs in work items, reviews, and evaluation results. Record new decisions with `knowledge-plane/adr/_template.md`.
- Only the deployer writes `knowledge-plane/state/LIVE.md`. Evaluation golden datasets never go into the build knowledge plane.
- After you change the knowledge plane, run `python3 /workspace/knowledge-plane/kp-check.py` and fix every problem it reports.

{backlog}
{integrate}
{deploy}
## 6. Communication & Turn Completion (Lessons L1, L2, L4)
- **Verify message delivery (L1):** Always check the exit code of `scion message <recipient> "<msg>"` and confirm recipient names reside in the same Scion project.
- **Signal turn completion (L2):** End every turn by running `sciontool status task_completed "<concise summary with {turn_ref}>"` (or `sciontool status ask_user "<blocker>"` when genuinely blocked).
- **Narrow PO escalation (L4):** Escalate to the human PO (`scion message user:<PO_USER> "..."`) ONLY for missing IAM roles, missing secrets/quotas, or genuine spec contradictions.

## 7. L1–L5 Evaluation Pyramid Contract
- **L1 Health:** `GET {spec['evaluation_pyramid']['l1_health']['endpoint']}`
- **L2 Contract Invariants:** {'; '.join(spec['evaluation_pyramid']['l2_contract']['invariants'])}
- **L3 Primary Domain Metric:** `{l3['primary_metric']} >= {l3['target_threshold']}` ({l3.get('probe_description', '')})
- **L4 Safety & Grounding:** {'; '.join(spec['evaluation_pyramid']['l4_safety_grounding']['invariants'])}
- **L5 Latency SLO:** `p50 <= {l5.get('p50_ms', 100)}ms`, `p95 <= {l5['p95_ms']}ms`, `p99 <= {l5.get('p99_ms', 500)}ms`
"""


def _render_scion_agent_yaml(role_spec: dict[str, Any], spec: dict[str, Any] | None = None) -> str:
    harness = role_spec["harness"]
    model = role_spec["model"]
    subdir = _monorepo(spec)[1] if spec else ""
    lines = [
        "schema_version: \"1\"",
        f"default_harness_config: {harness}-factory" if subdir else f"default_harness_config: {harness}",
        f"model: \"{model}\"",
        "telemetry:",
        "  enabled: true",
    ]
    env = []
    if harness == "opencode" and "vertex" in model.lower():
        # Lesson L6: Gemini 4 Argon on Vertex requires global endpoint
        env.append("  GOOGLE_CLOUD_LOCATION: \"global\"")
    if subdir:
        env.append(f"  FACTORY_SUBDIR: \"{subdir}\"   # product folder the harness starts in (factory-cd.sh)")
    if env:
        lines.extend(["env:", *env])
    return "\n".join(lines) + "\n"


def _team_lead(roles: list[dict[str, Any]], preferred: str) -> str:
    """The role that leads a team: `lead` / `eval-lead` when the roster has it, else the first role."""
    names = [r["role"] for r in roles]
    return preferred if preferred in names else names[0]


# Role prompts (spec §5.4, §6): each role carries its library protocol and a repo focus; the lead sizes every issue
# against the roles present in its roster and briefs teammates through files in the scratchpad, never inline (shell
# metacharacters and long prompts break).
BRIEFS_DIR = "/scion-volumes/scratchpad/briefs"
DURABILITY_RULE = "A task is done only when its result is pushed or sent in a message."

_FOCUS_COMMAND_ROLES = ("backend", "test-engineer", "investigator")
_FOCUS_DOC_ROLES = ("doc-writer", "researcher")
_FOCUS_KEYS = ("paths", "commands", "docs")

# (role that must be in the roster, sizing line). `{role}` placeholders become agent names.
_SIZING_LINES = (
    ("investigator", "bug → {investigator} first"),
    ("researcher", "unfamiliar ground → {researcher} first"),
    ("doc-writer", "user-visible change → {doc-writer} after the commit"),
    ("security-auditor", "auth/crypto/secrets → {security-auditor} in parallel with {reviewer}"),
    ("frontend", "UI → {frontend}"),
    ("migration-specialist", "schema/IaC → {migration-specialist}"),
    ("performance-analyst", "perf-sensitive → {performance-analyst}"),
    ("release-manager", "release → {release-manager}"),
)


def role_focus(spec: dict[str, Any], role_spec: dict[str, Any]) -> dict[str, list[str]]:
    """The repo scope a role works in: `{paths, commands, docs}`.

    An explicit `focus` in the roster entry wins. Otherwise every role gets `paths=[subdir or "."]`; backend,
    test-engineer and investigator get the build, test and lint commands; doc-writer and researcher get the
    knowledge-plane sources plus the knowledge plane's INDEX.md.
    """
    explicit = role_spec.get("focus")
    if isinstance(explicit, dict):
        return {key: [str(v) for v in (explicit.get(key) or [])] for key in _FOCUS_KEYS}
    role = role_spec.get("role")
    stack = spec.get("stack") or {}
    focus: dict[str, list[str]] = {"paths": [_monorepo(spec)[1] or "."], "commands": [], "docs": []}
    if role in _FOCUS_COMMAND_ROLES:
        cmds = (stack.get("build_command"), stack.get("test_command"), stack.get("lint_command"))
        focus["commands"] = [str(c) for c in cmds if c]
    if role in _FOCUS_DOC_ROLES:
        sources = (spec.get("knowledge_plane") or {}).get("sources") or {}
        focus["docs"] = [f"/workspace/{src}" for kind in kp.SOURCE_KINDS for src in (sources.get(kind) or [])]
        focus["docs"].append(f"{_ws(spec)}/knowledge-plane/INDEX.md")
    return focus


def _render_role_protocol(spec: dict[str, Any], role_spec: dict[str, Any]) -> str:
    protocol = role_library.role_protocol(role_spec["role"])
    if not protocol:
        return ""
    protocol = protocol.replace("<knowledge plane>", f"{_ws(spec)}/knowledge-plane")
    return f"## Role protocol\n{protocol.strip()}\n\n"


def _render_focus(spec: dict[str, Any], role_spec: dict[str, Any]) -> str:
    focus = role_focus(spec, role_spec)
    labels = {"paths": "Paths in scope", "commands": "Commands you run", "docs": "Documents you keep"}
    lines = [f"- **{labels[key]}:** " + ", ".join(f"`{v}`" for v in focus[key]) for key in _FOCUS_KEYS if focus[key]]
    return "## Focus\n" + "\n".join(lines) + "\n\n" if lines else ""


def _render_sizing(spec: dict[str, Any], team: list[dict[str, Any]], is_eval: bool) -> str:
    names = {r["role"] for r in team}
    agents = {role: f"`{_agent(spec, role)}`" for role in names | {"reviewer"}}
    lines = [f"- {line.format_map(agents)}" for role, line in _SIZING_LINES if role in names]
    if is_eval and not lines:
        return ""
    intro = ("Read the issue and the knowledge plane, then decide who is needed. A small change needs only the\n"
             "implementer and the reviewer; add a teammate when its line applies:")
    return "## Sizing an issue\n" + intro + "\n" + "\n".join(lines) + "\n\n"


def _render_briefs(spec: dict[str, Any], lead: str) -> str:
    ref = "#N" if _vcs_enabled(spec) else "<item ID>"
    return f"""## Briefs
- Write each brief to `{BRIEFS_DIR}/<issue>-<agent>.md`, then send
  `scion message <agent> "{lead} -> <agent>: {ref}: read {BRIEFS_DIR}/<file>"`.
- Never inline a brief in the message: shell metacharacters and long prompts break.
- A brief has the key locations, the deliverables (exact paths), the acceptance checks, and a termination line
  ("You MUST … then report to `{lead}`").

"""


def _render_chain_of_command(spec: dict[str, Any], role_spec: dict[str, Any], is_eval: bool) -> str:
    """The PO starts only the two leads; each lead starts its team, so Scion records the lead as the team's parent.

    Scion keeps each agent's creator (ancestry root -> parent -> child): the Hub's tree view shows the lead on top, and
    the Hub sends an agent's notifications to its creator, so the lead hears when a teammate stops or fails.
    """
    team = spec["team"]["eval_roles"] if is_eval else spec["team"]["build_roles"]
    lead_role = _team_lead(team, "eval-lead" if is_eval else "lead")
    lead = _agent(spec, lead_role)
    if role_spec["role"] != lead_role:
        return f"""## Chain of command
- You report to `{lead}`, who started you. Take work from `{lead}` (or the PO), and send every result, blocker and
  verdict to `{lead}` with `scion message {lead} "<your name> -> {lead}: ..."`.
- Read the brief file named in the message (`{BRIEFS_DIR}/<issue>-<agent>.md`) before you start:
  it has the key locations, the deliverables, the acceptance checks and when you are done.
- When a brief tells you to hand work straight to a teammate, do it and copy `{lead}`.
- Never start, stop or delete agents: staffing is `{lead}`'s job.
- {DURABILITY_RULE}
  The scratchpad lasts as long as the Hub project, but anything the next issue needs goes to the knowledge plane or
  the repository.

"""
    rows = []
    for r in team:
        if r["role"] == lead_role:
            continue
        cmd = f'scion start {_agent(spec, r["role"])} -t {r["role"]} --harness-auth vertex-ai'
        if r.get("gcp_identity"):
            cmd += " --service-account <ID>"
        rows.append(f'| `{_agent(spec, r["role"])}` | `{cmd} "Read your instructions, then run your wake-up steps."` |')
    table = "\n".join(rows)
    sa_note = (
        "\n- `<ID>` is the deployer service account's ID in `scion project service-accounts list` (the PO registers it, "
        "setup runbook step 6). Without one, ask the PO before you start that agent."
        if any(r.get("gcp_identity") for r in team if r["role"] != lead_role) else ""
    )
    return f"""## Chain of command
You lead this team and you start it: the PO starts only you. Every agent you start is your child in Scion, so the
Hub shows you on top of your team and sends you its notifications (stopped, failed, waiting for input).

| Teammate | Start it with |
|---|---|
{table}

- At every wake-up, run `scion list` first. Start each teammate that is missing; `scion resume <name>` a stopped one
  and re-send its brief (a resumed agent starts a fresh session); `scion resume --force <name>` one in `error`.{sa_note}
- Start only the agents in this table. Your team reports to you: you assign every task and accept every result.

{_render_sizing(spec, team, is_eval)}{_render_briefs(spec, lead)}"""


def _render_role_system_prompt(spec: dict[str, Any], role_spec: dict[str, Any], is_eval: bool) -> str:
    text = _monorepo_paths(spec, _render_role_system_prompt_base(spec, role_spec, is_eval))
    sections = (_render_role_protocol(spec, role_spec) + _render_focus(spec, role_spec)
                + _render_chain_of_command(spec, role_spec, is_eval))
    text = text.replace("## Operating Loop\n", sections + "## Operating Loop\n", 1)
    prefix, subdir = _monorepo(spec)
    if not subdir:
        return text
    team = spec["team"]["eval_roles"] if is_eval else spec["team"]["build_roles"]
    mates = ", ".join(f"`{_agent(spec, r['role'])}`" for r in team if r["role"] != role_spec["role"])
    return text.replace("## Identity & Responsibility\n", f"""## Identity & Responsibility
- **Your agent name is `{_agent(spec, role_spec['role'])}`.** Teammates: {mates}.
- **Product folder:** `{subdir}` (you start in `/workspace/{subdir}`).
""", 1)


def _render_role_system_prompt_base(spec: dict[str, Any], role_spec: dict[str, Any], is_eval: bool) -> str:
    proj = spec["project"]
    stack = spec["stack"]
    infra = spec["infrastructure"]
    pyramid = spec["evaluation_pyramid"]
    l3 = pyramid["l3_domain_quality"]
    l5 = pyramid["l5_latency_slo"]
    role = role_spec["role"]
    project_slug = proj["eval_slug"] if is_eval else proj["slug"]
    service = infra["eval_service"] if is_eval else infra["serving_service"]
    vcs = spec.get("vcs") or {}
    kp_index = f"{kp.EVAL_KP_MOUNT}/INDEX.md" if is_eval else "/workspace/knowledge-plane/INDEX.md"
    if _vcs_enabled(spec):
        branch = vcs["eval_branch"] if is_eval else vcs["build_branch"]
        location = f"Repository `{vcs['repo']}`, Branch `{branch}`"
        intake = f"inspect GitHub issues in `{vcs['repo']}` (`gh issue list -R {vcs['repo']} --label status:ready`)"
        where = f"on branch `{branch}`"
        handoff = "pushing or messaging downstream teammates"
    else:
        location = "Shared Hub project workspace `/workspace`, no version control"
        intake = (
            "follow the eval-lead's instructions" if is_eval
            else "read `/workspace/backlog/issues.json` (only the lead edits it)"
        )
        where = "in `/workspace` while holding the edit slot when required"
        handoff = "reporting results to the lead"
    factory_data = ""
    if is_eval:
        factory_data = f"""## Factory Data (Firestore)
The control plane is serverless Firestore; see `config/firestore/README.md` in the kit for the data contract.
- Agent status of both teams: `python3 /workspace/tools/factory_store.py list-agents --project-slug {proj['slug']}`
- Recent evaluation runs: `python3 /workspace/tools/factory_store.py list-eval-runs --project-slug {proj['slug']}`
- Record each evaluation run as one JSON object (`run_id`, `project_slug`, `source`, `overall_status`, `l1_passed`…`l5_passed`, `primary_metric_name` = `{l3['primary_metric']}`, `primary_metric_value`, `p50_ms`/`p95_ms`/`p99_ms`, `requirements`): `python3 /workspace/tools/factory_store.py put-eval-run --file run.json`. Use `--dry-run` first when you change the payload shape.

"""

    return f"""# Role: `{role}` ({'Evaluation Team' if is_eval else 'Build Team'} — Project `{project_slug}`)

## Identity & Responsibility
- **Project:** `{project_slug}` ({location})
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

{factory_data}## Operating Loop
1. Read incoming `scion message` instructions and {intake}.
2. Read `{kp_index}` and the requirements and ADRs that the work item cites.
3. Execute your role responsibility {where} while honoring `templates/_shared/agents.md`.
4. Verify your work with `{stack['test_command']}` before {handoff}.
5. Always conclude your turn with `sciontool status task_completed "<summary>"`.
"""


FIRESTORE_INDEXES = {
    "indexes": [
        {
            "collectionGroup": "factory_agent_status",
            "queryScope": "COLLECTION",
            "fields": [
                {"fieldPath": "project_slug", "order": "ASCENDING"},
                {"fieldPath": "agent_name", "order": "ASCENDING"},
            ],
        },
        {
            "collectionGroup": "eval_runs",
            "queryScope": "COLLECTION",
            "fields": [
                {"fieldPath": "project_slug", "order": "ASCENDING"},
                {"fieldPath": "evaluated_at", "order": "DESCENDING"},
            ],
        },
    ],
    "fieldOverrides": [],
}

FIRESTORE_RULES = """rules_version = '2';
// The factory control plane is server-side only: the Hub sync, eval agents, and the dashboard
// use IAM service accounts, which bypass these rules. Deny every client SDK request.
service cloud.firestore {
  match /databases/{database}/documents {
    match /{document=**} {
      allow read, write: if false;
    }
  }
}
"""


def _render_firestore_contract(spec: dict[str, Any]) -> str:
    proj = spec["project"]
    l3 = spec["evaluation_pyramid"]["l3_domain_quality"]
    return f"""# Factory control plane on Firestore

{proj['name']} (`{proj['slug']}` and `{proj['eval_slug']}`) stores agent status and evaluation runs in Firestore
(Native mode). Firestore is serverless: there is no instance to size, and idle cost is zero.

- **Project:** `FIRESTORE_PROJECT` (default: the application project)
- **Database:** `FIRESTORE_DATABASE` (default: `(default)`)
- **Access:** server side only, through IAM. [`firestore.rules`](firestore.rules) denies every client SDK request.
- **Indexes:** [`firestore.indexes.json`](firestore.indexes.json)

## `factory_agent_status`

One document per live agent, upserted every minute by the Hub sync (`scripts/sync-factory-agents.py` in
scion-defaults). Document ID: `<project_slug>__<agent_name>`. Documents not refreshed for `PRUNE_MINUTES` are deleted.

| Field | Type | Notes |
|---|---|---|
| `project_slug`, `agent_name`, `agent_id` | string | Identity |
| `template`, `harness`, `phase`, `activity` | string | Hub state |
| `parent_agent` | string or null | The agent that created this agent |
| `current_turns`, `model_calls` | integer or null | Null means unknown, not zero (L9) |
| `last_summary` | string | Up to 500 characters |
| `last_activity_at` | timestamp or null | Last Hub activity event |
| `synced_at` | timestamp | Server time of the last sync |

## `eval_runs`

One document per evaluation run, written by the evaluation team with `tools/factory_store.py put-eval-run`.
Document ID: `run_id`.

| Field | Type | Notes |
|---|---|---|
| `run_id`, `project_slug` | string | Required |
| `source` | string | Commit SHA (with Git) or snapshot SHA-256 (without Git) of the deployed service |
| `wave` | integer | Backlog wave under evaluation |
| `overall_status` | string | Required: `pass`, `fail`, or `error` |
| `l1_passed` … `l5_passed` | boolean | One per pyramid layer |
| `primary_metric_name`, `primary_metric_value` | string, number | Required. For this project: `{l3['primary_metric']}`, target `{l3['target_threshold']}` |
| `primary_metric_target` | number | The threshold used for this run |
| `secondary_metric_name`, `secondary_metric_value` | string, number | Optional |
| `p50_ms`, `p95_ms`, `p99_ms` | number | Measured latency |
| `layer_results` | map | Per-layer details |
| `requirements` | array of `{{id, status}}` | One entry per `REQ-` ID evaluated |
| `diagnostics` | map | Free-form evidence |
| `evaluated_at` | timestamp | Server time of the write |

## Set up

```bash
gcloud firestore databases create --database="$FIRESTORE_DATABASE" --location=REGION --type=firestore-native \
  --project="$FIRESTORE_PROJECT"
gcloud firestore indexes composite create --project="$FIRESTORE_PROJECT" --database="$FIRESTORE_DATABASE" \
  --collection-group=factory_agent_status --field-config=field-path=project_slug,order=ascending \
  --field-config=field-path=agent_name,order=ascending
gcloud firestore indexes composite create --project="$FIRESTORE_PROJECT" --database="$FIRESTORE_DATABASE" \
  --collection-group=eval_runs --field-config=field-path=project_slug,order=ascending \
  --field-config=field-path=evaluated_at,order=descending
```

Grant `roles/datastore.user` to the identities that write (the Hub VM service account) and `roles/datastore.viewer`
to the dashboard's runtime service account.
"""


def _render_factory_env_example(spec: dict[str, Any]) -> str:
    proj = spec["project"]
    infra = spec["infrastructure"]
    team = spec["team"]
    build_names = " ".join(r["role"] for r in team["build_roles"])
    eval_names = " ".join(r["role"] for r in team["eval_roles"])
    iam_roles = " ".join(infra.get("deployer_iam_roles", []))
    vcs = spec.get("vcs") or {}

    prefix, subdir = _monorepo(spec)
    monorepo = (
        f"""
# --- Monorepo factory (agents named <AGENT_PREFIX>-<role>, starting in FACTORY_SUBDIR) ---
AGENT_PREFIX={prefix}
FACTORY_SUBDIR={subdir}
KNOWLEDGE_PLANE_SOURCES={proj['slug']}={subdir}/knowledge-plane
""" if subdir else ""
    )
    return f"""# Scion software factory configuration — {proj['name']}
# Copy this file to config/factory.env in your scion-defaults checkout (git-ignored), replace the your-* values, and
# deploy with docs/setup-runbook.md. The names match what scripts/run-on-hub.sh, render-templates.py and
# push-templates.py read (KEY=value lines, no `export`).

# --- Scion Hub (self-hosted on a GCE VM) ---
HUB_PROJECT=your-hub-gcp-project
HUB_ZONE=us-central1-c
HUB_VM=scion-hub
HUB_OS_USER=scion
HUB_LOCAL_URL=http://localhost:8080

# --- GCP sandbox the agents build and deploy into ---
SANDBOX_PROJECT=your-sandbox-gcp-project
SANDBOX_REGION=us-central1
FIRESTORE_PROJECT=your-sandbox-gcp-project   # factory control plane: agent status and eval runs
FIRESTORE_DATABASE='(default)'
DATA_BUCKET=your-sandbox-gcp-project-factory-data   # factory data bucket (scripts/setup-sandbox.sh creates it)
BQ_DATASET={proj['slug'].replace('-', '_')}_dataset          # only with scripts/setup-sandbox.sh --with-bigquery
DEPLOYER_SA_NAME=deployer-sa
RUNTIME_SA_NAME={infra['serving_service']}
DEPLOYER_IAM_ROLES="{iam_roles}"
SERVING_SERVICE={infra['serving_service']}
DASHBOARD_SERVICE={infra['eval_service']}

# --- Factory ---
VCS_ENABLED={'true' if vcs.get('enabled') else 'false'}
GITHUB_REPO={vcs.get('repo', '')}
BUILD_PROJECT={proj['slug']}
EVAL_PROJECT={proj['eval_slug']}
BUILD_BRANCH={vcs.get('build_branch', 'main')}
EVAL_BRANCH={vcs.get('eval_branch', 'eval-dashboard')}
PO_EMAIL=po@example.com                      # Hub admin account that acts as the product owner
FACTORY_PROJECTS={proj['slug']},{proj['eval_slug']}
KNOWLEDGE_PLANE_SOURCE_PROJECT={proj['slug']}
MONTHLY_BUDGET_USD={infra.get('budget_usd', 200)}

# --- Roster (agent names = role names; start one agent per role) ---
BUILD_AGENTS="{build_names}"
EVAL_AGENTS="{eval_names}"
HEARTBEAT_MODE={team.get('heartbeat_mode', 'direct-lead')}
HEARTBEAT_CRON="{team.get('heartbeat_cron', '*/15 * * * *')}"
WATCHDOG_CRON="{team.get('watchdog_cron', '*/5 * * * *')}"
MAX_WIP={team.get('max_wip', 2)}
{monorepo}"""


def _render_verify_factory_sh(spec: dict[str, Any]) -> str:
    proj = spec["project"]
    backlog_check = "" if _vcs_enabled(spec) else 'test -f "$ROOT_DIR/backlog/issues.json"'

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

BUILD_PROJECT_SLUG="${{BUILD_PROJECT:-{proj['slug']}}}"
EVAL_PROJECT_SLUG="${{EVAL_PROJECT:-{proj['eval_slug']}}}"
SERVING_URL="${{SERVING_URL:-}}"
EVAL_URL="${{EVAL_URL:-}}"

echo "[1/4] Verifying generated factory artifacts for $BUILD_PROJECT_SLUG and $EVAL_PROJECT_SLUG..."
test -f "$ROOT_DIR/factory.yaml"
test -f "$ROOT_DIR/config/firestore/README.md"
test -f "$ROOT_DIR/config/firestore/firestore.indexes.json"
test -f "$ROOT_DIR/eval-workspace/tools/factory_store.py"
test -f "$ROOT_DIR/waves/issues.json"
test -f "$ROOT_DIR/templates/_shared/agents.md"
test -f "$ROOT_DIR/knowledge-plane/INDEX.md"
test -f "$ROOT_DIR/knowledge-plane/kp-check.py"
{backlog_check}
echo "  Knowledge plane present. In the build workspace, run: python3 knowledge-plane/kp-check.py"

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
    repo = spec["vcs"]["repo"]
    flabel = _factory_label(spec)
    labels = ["status:ready", "status:in-progress", "status:in-review", "status:verified", "status:blocked"]
    labels += [flabel] if flabel else []
    create_labels = '"status:ready"' + (f', "--label", "{flabel}"' if flabel else "")
    return f"""#!/usr/bin/env bash
# Seed Wave 1..N GitHub Issues for {proj['name']} ({repo})
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${{BASH_SOURCE[0]}}")" && pwd)"
ISSUES_FILE="$SCRIPT_DIR/../waves/issues.json"
REPO="${{GITHUB_REPO:-{repo}}}"

if [[ ! -f "$ISSUES_FILE" ]]; then
  echo "Missing $ISSUES_FILE" >&2
  exit 1
fi

for lbl in {" ".join(f'"{l}"' for l in labels)}; do
  if [[ "${{DRY_RUN:-1}}" == "0" ]]; then
    gh label create "$lbl" --repo "$REPO" --force >/dev/null 2>&1 || true
  else
    echo "[dry-run] gh label create $lbl --repo $REPO"
  fi
done

# DRY_RUN goes in as an argument: the quoted heredoc below is not expanded by the shell.
python3 - "$ISSUES_FILE" "$REPO" "${{DRY_RUN:-1}}" <<'PY'
import json, subprocess, sys
issues_path, repo, dry_run = sys.argv[1], sys.argv[2], sys.argv[3]
waves = json.load(open(issues_path))
for w in waves:
    wave_num = w["wave"]
    for issue in w["issues"]:
        title = issue["title"]
        criteria = "\\n".join(f"- [ ] {{c}}" for c in issue.get("acceptance_criteria", []))
        body = f"## Wave {{wave_num}}: {{w['title']}}\\n\\n**Owner Role:** `{{issue['owner_role']}}`\\n\\n### Acceptance Criteria\\n{{criteria}}\\n"
        print(f"[wave-{{wave_num}}] {{title}} (owner={{issue['owner_role']}})")
        if dry_run == "0":
            subprocess.run(["gh", "issue", "create", "--repo", repo, "--title", title, "--body", body, "--label", {create_labels}], check=True)
PY
"""


def _render_root_agents_md(spec: dict[str, Any], roster_notes: list[str] | None = None) -> str:
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
    notes_section = ""
    if roster_notes:
        notes_section = "\n### Roster notes\n" + "\n".join(f"- {n}" for n in roster_notes) + (
            "\n\nThe compiler added these default specialists because the spec doesn't list them. To leave one out, "
            "add it to `team.dropped_defaults` with a reason and recompile.\n"
        )
    vcs = spec.get("vcs") or {}
    if vcs.get("enabled"):
        vcs_lines = (
            f"- **Repository:** `{vcs['repo']}`\n"
            f"- **Build Project (`{proj['slug']}`):** Branch `{vcs['build_branch']}` -> Cloud Run `{infra['serving_service']}`\n"
            f"- **Evaluation Project (`{proj['eval_slug']}`):** Disjoint orphan branch `{vcs['eval_branch']}` -> Cloud Run `{infra['eval_service']}`"
        )
    else:
        vcs_lines = (
            "- **Version control:** Not configured yet. Backlog in `backlog/issues.json`; deploys from workspace snapshots. "
            "Run `scion-factory enable-git` and `scripts/enable-git.sh` to add Git later.\n"
            f"- **Build Project (`{proj['slug']}`):** Shared workspace -> Cloud Run `{infra['serving_service']}`\n"
            f"- **Evaluation Project (`{proj['eval_slug']}`):** Separate workspace -> Cloud Run `{infra['eval_service']}`"
        )

    return f"""# {proj['name']} — Scion Autonomous Software Factory (`AGENTS.md`)

## 1. Mission & Architecture
- **Mission:** {proj['mission']}
{vcs_lines}
- **Knowledge Plane:** [`knowledge-plane/INDEX.md`](knowledge-plane/INDEX.md) (copy `knowledge-plane/` to the root of the build workspace)
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
{notes_section}
## 4. L1–L5 Evaluation Pyramid Oracle
- **L1 Health:** `{spec['evaluation_pyramid']['l1_health']['endpoint']}`
- **L2 Contract:** {'; '.join(spec['evaluation_pyramid']['l2_contract']['invariants'])}
- **L3 Domain Metric:** `{l3['primary_metric']} >= {l3['target_threshold']}`
- **L4 Safety & Grounding:** {'; '.join(spec['evaluation_pyramid']['l4_safety_grounding']['invariants'])}
- **L5 Latency SLO:** `p50 <= {l5.get('p50_ms', 100)}ms`, `p95 <= {l5['p95_ms']}ms`, `p99 <= {l5.get('p99_ms', 500)}ms`
"""


def _render_backlog(spec: dict[str, Any]) -> list[dict[str, Any]]:
    items = []
    for wave in spec["waves"]:
        for n, issue in enumerate(wave.get("issues") or [], start=1):
            items.append({
                "id": f"W{wave['wave']}-{n}",
                "wave": wave["wave"],
                "title": issue["title"],
                "owner_role": issue.get("owner_role", "lead"),
                "requirements": list(issue.get("requirements") or []),
                "acceptance_criteria": list(issue.get("acceptance_criteria") or []),
                "status": "ready",
            })
    return items


ENABLE_GIT_SH = r'''#!/usr/bin/env bash
# Add Git and GitHub to a __NAME__ factory that started without version control.
#
# Run on the Hub VM as the Hub OS user, from this kit directory. Dry run unless DRY_RUN=0.
# Before you run it with DRY_RUN=0:
#   1. Create the empty GitHub repository OWNER/NAME.
#   2. Make sure the Hub OS user can push to it (for example, `gh auth setup-git`) and has a Git identity.
#   3. Run `scion-factory enable-git factory.yaml --repo OWNER/NAME`, recompile the kit, and push the new templates.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
if [[ -f "$ROOT_DIR/config/factory.env" ]]; then
  # shellcheck disable=SC1091
  source "$ROOT_DIR/config/factory.env"
fi

DRY_RUN="${DRY_RUN:-1}"
REPO="${GITHUB_REPO:-${1:-}}"
BUILD_BRANCH="${BUILD_BRANCH:-main}"
EVAL_BRANCH="${EVAL_BRANCH:-eval-dashboard}"
BUILD_WORKSPACE="${BUILD_WORKSPACE:-$HOME/.scion/projects/__SLUG__}"
EVAL_WORKSPACE="${EVAL_WORKSPACE:-$HOME/.scion/projects/__EVAL_SLUG__}"

if [[ -z "$REPO" ]]; then
  echo "usage: GITHUB_REPO=OWNER/NAME $0   (or: $0 OWNER/NAME)" >&2
  exit 2
fi
if [[ "$BUILD_BRANCH" == "$EVAL_BRANCH" ]]; then
  echo "BUILD_BRANCH and EVAL_BRANCH must differ (orphan branch isolation, L25)" >&2
  exit 2
fi

run() {
  if [[ "$DRY_RUN" == "0" ]]; then
    "$@"
  else
    printf '[dry-run]'
    printf ' %q' "$@"
    printf '\n'
  fi
}

ensure_gitignore() {
  local ws="$1"
  local f="$ws/.gitignore"
  for pattern in ".env" ".env.*" ".envrc" ".scion/" "node_modules/" ".venv/"; do
    if ! grep -qxF "$pattern" "$f" 2>/dev/null; then
      run sh -c "printf '%s\n' '$pattern' >> '$f'"
    fi
  done
}

import_workspace() {
  local ws="$1" branch="$2" message="$3"
  if [[ ! -d "$ws" ]]; then
    echo "workspace not found: $ws" >&2
    exit 1
  fi
  if [[ -d "$ws/.git" ]]; then
    echo "  $ws is already a Git repository; skipping init"
  else
    run git -C "$ws" init -q -b "$branch"
  fi
  ensure_gitignore "$ws"
  run git -C "$ws" add -A
  run git -C "$ws" commit -q -m "$message"
  if ! git -C "$ws" remote get-url origin >/dev/null 2>&1; then
    run git -C "$ws" remote add origin "https://github.com/$REPO.git"
  fi
  run git -C "$ws" push -u origin "$branch"
}

echo "[1/3] Migrate backlog/issues.json to GitHub issues and repoint the knowledge plane"
BACKLOG="$BUILD_WORKSPACE/backlog/issues.json"
if [[ ! -f "$BACKLOG" ]]; then
  echo "  no backlog at $BACKLOG; skipping"
else
  python3 - "$BACKLOG" "$REPO" "$DRY_RUN" <<'PY'
import json, subprocess, sys
path, repo, dry_run = sys.argv[1], sys.argv[2], sys.argv[3] != "0"
items = json.load(open(path))

def run(cmd):
    if dry_run:
        print("[dry-run] " + " ".join(cmd))
    else:
        subprocess.run(cmd, check=True)

for status in ("ready", "in-progress", "in-review", "verified", "blocked"):
    run(["gh", "label", "create", f"status:{status}", "--repo", repo, "--force"])
for item in items:
    criteria = "\n".join(f"- [ ] {c}" for c in item.get("acceptance_criteria", []))
    reqs = ", ".join(item.get("requirements", [])) or "none"
    body = (f"Migrated from backlog item `{item['id']}`.\n\n**Owner role:** `{item['owner_role']}`\n"
            f"**Requirements:** {reqs}\n\n### Acceptance criteria\n{criteria}\n")
    run(["gh", "issue", "create", "--repo", repo, "--title", f"[{item['id']}] {item['title']}",
         "--body", body, "--label", f"status:{item.get('status', 'ready')}"])
print(f"{len(items)} backlog item(s) processed")
PY
  run mv "$BACKLOG" "$BUILD_WORKSPACE/backlog/issues.migrated.json"
fi

INDEX="$BUILD_WORKSPACE/knowledge-plane/INDEX.md"
if [[ -f "$INDEX" ]]; then
  run python3 - "$INDEX" "$REPO" <<'PY'
import sys
path, repo = sys.argv[1], sys.argv[2]
lines = open(path).read().splitlines()
row = f"| Find the backlog | GitHub issues in `{repo}` (`status:*` labels) |"
lines = [row if line.startswith("| Find the backlog |") else line for line in lines]
open(path, "w").write("\n".join(lines) + "\n")
PY
fi

echo "[2/3] Import the build workspace into $REPO on branch $BUILD_BRANCH"
import_workspace "$BUILD_WORKSPACE" "$BUILD_BRANCH" "chore: import factory build workspace into Git"

echo "[3/3] Import the evaluation workspace as the orphan branch $EVAL_BRANCH (no shared history)"
import_workspace "$EVAL_WORKSPACE" "$EVAL_BRANCH" "chore: import factory evaluation workspace into Git"

echo "Done. Next: recompile the kit after 'scion-factory enable-git', push the templates, and restart the agents."
'''


def _render_enable_git_sh(spec: dict[str, Any]) -> str:
    proj = spec["project"]
    return (ENABLE_GIT_SH.replace("__NAME__", proj["name"])
            .replace("__SLUG__", proj["slug"]).replace("__EVAL_SLUG__", proj["eval_slug"]))


def compile_factory_boilerplate(
    spec: dict[str, Any], out_dir: str | Path, force_knowledge: bool = False
) -> dict[str, Any]:
    """Compile a validated FactorySpec into a complete Scion factory directory.

    Files under knowledge-plane/, backlog/, and eval-workspace/ are seeded once and never
    overwritten, because agents edit them after bootstrap. Pass force_knowledge=True to reseed.
    """
    readiness = evaluate_spec_readiness(spec)
    if not readiness["ready"]:
        raise ValueError(f"FactorySpec is not ready to compile: {readiness['missing_or_weak']}")
    # Older specs and presets get the missing default specialists; the kit's factory.yaml records the full roster.
    spec, roster_notes = normalize_team(spec)

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written_files: list[str] = []

    skipped_files: list[str] = []

    def _write(rel_path: str, content: str, executable: bool = False, seed_once: bool = False) -> None:
        target = out / rel_path
        if seed_once and target.exists() and not force_knowledge:
            skipped_files.append(rel_path)
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        if executable:
            target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        written_files.append(rel_path)

    # 1. Canonical spec & root AGENTS.md
    save_spec(spec, out / "factory.yaml")
    written_files.append("factory.yaml")
    _write("AGENTS.md", _render_root_agents_md(spec, roster_notes))

    git = _vcs_enabled(spec)
    backlog_ref = (
        f"open GitHub issues in {spec['vcs']['repo']}" if git
        else f"/workspace/backlog/issues.json and {_ws(spec)}/knowledge-plane/INDEX.md"
    )

    # 2. Config files
    _write("config/factory.env.example", _render_factory_env_example(spec))
    _write("config/firestore/README.md", _render_firestore_contract(spec))
    _write("config/firestore/firestore.indexes.json", json.dumps(FIRESTORE_INDEXES, indent=2) + "\n")
    _write("config/firestore/firestore.rules", FIRESTORE_RULES)
    # The eval team's Firestore client; refreshed on every compile (it's code, not knowledge).
    _write("eval-workspace/tools/factory_store.py",
           (Path(__file__).resolve().parent / "factory_store.py").read_text(), executable=True)
    _write(
        "config/heartbeat-message.txt",
        (
            f"[HEARTBEAT] Inspect {backlog_ref}, "
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
            _write(f"templates/{role}/scion-agent.yaml", _render_scion_agent_yaml(role_spec, spec))
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

    # 5. Knowledge plane (seeded once) and evaluation-only golden data notes
    for rel, content in kp.render_knowledge_plane(spec, git).items():
        _write(rel, content, executable=rel.endswith(".py"), seed_once=True)
    _write("eval-workspace/golden/README.md", kp.render_eval_golden_readme(spec), seed_once=True)

    # 6. Waves, backlog & operational scripts (mode-specific; stale scripts from the other mode are removed)
    _write("waves/issues.json", json.dumps(spec["waves"], indent=2) + "\n")
    if git:
        _write("scripts/seed-github-waves.sh", _render_seed_waves_sh(spec), executable=True)
        stale = out / "scripts" / "enable-git.sh"
    else:
        _write("backlog/issues.json", json.dumps(_render_backlog(spec), indent=2) + "\n", seed_once=True)
        _write("scripts/enable-git.sh", _render_enable_git_sh(spec), executable=True)
        stale = out / "scripts" / "seed-github-waves.sh"
    if stale.exists():
        stale.unlink()
    _write("scripts/verify-factory.sh", _render_verify_factory_sh(spec), executable=True)

    return {
        "out_dir": str(out),
        "files_written": len(written_files),
        "files": written_files,
        "preserved": skipped_files,
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

# Claude Code and Antigravity run on a stock Hub without harness patches (full e2e, lessons-learned.md L28): the
# bundled Hermes config crashes on a fresh image and the bundled OpenCode launcher stops at a permission prompt.
# The interview can still choose OpenCode or Hermes per role; see docs/setup-runbook.md step 7 for their patches.
# The draft roster is exactly the role library's always-roles (spec §5.1-§5.3); catalog specialists such as frontend
# or deployer come from discovery (`proposed_roles`) and `scion-factory add-role`.
DEFAULT_BUILD_ROLES: list[dict[str, Any]] = [role_library.library_role_entry(r) for r in role_library.ALWAYS_BUILD]

DEFAULT_EVAL_ROLES: list[dict[str, Any]] = [role_library.library_role_entry(r) for r in role_library.ALWAYS_EVAL]


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
            "mission": "",
            "non_goals": [],
        },
        "vcs": {
            "enabled": bool((detected.get("vcs") or {}).get("github_repo")),
            "provider": "github",
            "repo": (detected.get("vcs") or {}).get("github_repo", ""),
            "build_branch": "main",
            "eval_branch": "eval-dashboard",
        },
        "knowledge_plane": kp.empty_knowledge_plane(detected.get("knowledge_sources")),
        "stack": {
            "primary_language": "" if language in ("", "unknown") else language,
            "framework": detected.get("framework", ""),
            "build_command": detected.get("build_command", ""),
            "test_command": detected.get("test_command", ""),
            "lint_command": detected.get("lint_command", ""),
            "port": detected.get("port", 8080),
        },
        "infrastructure": {
            "deploy": "",  # intent: `service` or `none` (spec §8); the interview must answer
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
            "autonomy": "standard",
            "dropped_defaults": [],
            "build_roles": [dict(r, responsibility="", default=True) for r in DEFAULT_BUILD_ROLES],
            "eval_roles": [dict(r, responsibility="", default=True) for r in DEFAULT_EVAL_ROLES],
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
        # Until the interview answers infrastructure.deploy, propose as for a service.
        mode = deploy_mode(spec)
        proposed = discovery.discover_roles(Path(repo_path), subdir=_monorepo(spec)[1],
                                            deploy=mode if mode in DEPLOY_MODES else "service")
    else:
        detected, questions, proposed = {}, get_grill_questions(), []

    return {
        "draft_path": str(out),
        "detected": detected,
        "readiness": evaluate_spec_readiness(spec),
        "questions": questions,
        "proposed_roles": proposed,
    }


def enable_git(spec_path: str | Path, repo: str, build_branch: str = "main",
               eval_branch: str = "eval-dashboard") -> dict[str, Any]:
    """Switch a FactorySpec to Git mode in place. Recompile the kit afterwards."""
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
        raise ValueError("--repo must be OWNER/NAME")
    if build_branch == eval_branch:
        raise ValueError("--eval-branch must differ from --build-branch (orphan branch isolation, L25)")
    spec = load_spec(spec_path)
    spec["vcs"] = {
        "enabled": True,
        "provider": "github",
        "repo": repo,
        "build_branch": build_branch,
        "eval_branch": eval_branch,
    }
    save_spec(spec, spec_path)
    return spec


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
    p_compile.add_argument("--force-knowledge", action="store_true",
                           help="Reseed knowledge-plane/, backlog/, and eval-workspace/ even if they exist")

    p_git = sub.add_parser("enable-git", help="Switch a factory that started without Git to Git mode")
    p_git.add_argument("spec_path", help="Path to factory.yaml")
    p_git.add_argument("--repo", required=True, help="GitHub repository OWNER/NAME")
    p_git.add_argument("--build-branch", default="main")
    p_git.add_argument("--eval-branch", default="eval-dashboard")

    p_role = sub.add_parser("add-role", help="Add a library specialist (for example one discovery proposed) to a FactorySpec")
    p_role.add_argument("spec_path", help="Path to factory.yaml")
    p_role.add_argument("--role", required=True, help="Library role, e.g. security-auditor")
    p_role.add_argument("--team", choices=("build", "eval"), default=None,
                        help="Roster to add it to (default: the role's team in the library)")
    p_role.add_argument("--evidence", action="append", default=[],
                        help="Why the role is needed (repeatable), e.g. a path discovery found")

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
            for item in report["warnings"]:
                print(f"  - WARNING: {item}")
        return 0 if report["ready"] else 1

    if args.cmd == "compile":
        spec = load_spec(args.spec_path)
        res = compile_factory_boilerplate(spec, args.out, force_knowledge=args.force_knowledge)
        print(f"Compiled {res['files_written']} boilerplate files into {res['out_dir']}")
        if res["preserved"]:
            print(f"Preserved {len(res['preserved'])} existing knowledge files (use --force-knowledge to reseed)")
        return 0

    if args.cmd == "enable-git":
        try:
            spec = enable_git(args.spec_path, args.repo, args.build_branch, args.eval_branch)
        except ValueError as err:
            print(f"error: {err}", file=sys.stderr)
            return 2
        report = evaluate_spec_readiness(spec)
        print(f"Enabled Git for {args.spec_path}: {args.repo} ({args.build_branch} / {args.eval_branch}); ready={report['ready']}")
        print("Next: run scripts/enable-git.sh from the current kit (dry run first), then recompile the kit and push templates.")
        return 0 if report["ready"] else 1

    if args.cmd == "add-role":
        before = load_spec(args.spec_path)
        present = {r.get("role") for key in ("build_roles", "eval_roles")
                   for r in ((before.get("team") or {}).get(key) or []) if isinstance(r, dict)}
        try:
            spec = add_role(args.spec_path, args.role, team=args.team, evidence=args.evidence)
        except ValueError as err:
            print(f"error: {err}", file=sys.stderr)
            return 2
        team_name = next(t for t in ("build", "eval")
                         if any(r.get("role") == args.role for r in spec["team"].get(f"{t}_roles") or []))
        report = evaluate_spec_readiness(spec)
        print(json.dumps({
            "spec_path": str(args.spec_path),
            "role": args.role,
            "team": team_name,
            "added": args.role not in present,
            "ready": report["ready"],
            "next": "recompile the kit and push the new template",
        }, indent=2))
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
