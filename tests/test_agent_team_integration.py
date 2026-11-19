"""Tests for integrating https://github.com/scion-frontiers/agent-team roles, URI skills, and workflow rules."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import discovery  # noqa: E402
import role_library  # noqa: E402
import scion_factory  # noqa: E402
import skill_curation  # noqa: E402
import sub_coordinators  # noqa: E402

PRESET = ROOT / "presets" / "starter-service.factory.yaml"


def test_agent_team_catalog_roles_and_uri_mappings():
    for role in ("architect", "eng-manager", "qa-tester", "web-builder"):
        assert role in role_library.ROLES
        assert role_library.role_protocol(role)

    assert sub_coordinators.is_sub_coordinator(role_library.library_role_entry("eng-manager"))
    assert role_library.ROLES["qa-tester"]["needs_service"] is True
    assert role_library.ROLES["architect"]["needs_service"] is False

    assert role_library.agent_team_template_uri("lead") == "gh://scion-frontiers/agent-team/templates/coordinator"
    assert role_library.agent_team_template_uri("reviewer") == "gh://scion-frontiers/agent-team/templates/code-reviewer"
    assert role_library.agent_team_template_uri("backend") == "gh://scion-frontiers/agent-team/templates/developer"
    assert role_library.agent_team_template_uri("architect") == "gh://scion-frontiers/agent-team/templates/architect"
    assert role_library.agent_team_template_uri("eng-manager") == "gh://scion-frontiers/agent-team/templates/eng-manager"
    assert role_library.agent_team_template_uri("qa-tester") == "gh://scion-frontiers/agent-team/templates/qa-tester"
    assert role_library.agent_team_template_uri("lead", registry="none") is None

    lead_uris = role_library.agent_team_skill_uris("lead")
    assert "gh://scion-frontiers/agent-team/artifact-durability" in lead_uris
    assert "gh://scion-frontiers/agent-team/agent-state-continuity" in lead_uris
    assert "gh://scion-frontiers/agent-team/software-engineering-process" in lead_uris

    rev_uris = role_library.agent_team_skill_uris("reviewer")
    assert rev_uris == [
        "gh://scion-frontiers/agent-team/code-review",
        "gh://scion-frontiers/agent-team/software-engineering-process",
    ]
    qa_uris = role_library.agent_team_skill_uris("qa-tester")
    assert "gh://microsoft/playwright-cli/playwright-cli" in qa_uris
    assert role_library.agent_team_skill_uris("lead", registry="none") == []


def test_compiled_scion_agent_yaml_emits_uri_skills_and_qa_chromium_env(tmp_path):
    spec = scion_factory.load_spec(PRESET)
    spec_path = tmp_path / "factory.yaml"
    scion_factory.save_spec(spec, spec_path)

    scion_factory.add_role(spec_path, "architect", evidence=[".design/arch.md"])
    scion_factory.add_role(spec_path, "qa-tester", evidence=["playwright.config.ts"])
    scion_factory.add_role(spec_path, "web-builder", evidence=["astro.config.mjs"])

    updated = scion_factory.load_spec(spec_path)
    out = tmp_path / "kit"
    scion_factory.compile_factory_boilerplate(updated, out)

    lead_cfg = yaml.safe_load((out / "templates" / "lead" / "scion-agent.yaml").read_text())
    assert {"uri": "gh://scion-frontiers/agent-team/software-engineering-process"} in lead_cfg["skills"]
    assert {"uri": "gh://scion-frontiers/agent-team/artifact-durability"} in lead_cfg["skills"]

    rev_cfg = yaml.safe_load((out / "templates" / "reviewer" / "scion-agent.yaml").read_text())
    assert {"uri": "gh://scion-frontiers/agent-team/code-review"} in rev_cfg["skills"]

    qa_cfg = yaml.safe_load((out / "templates" / "qa-tester" / "scion-agent.yaml").read_text())
    assert qa_cfg["env"]["PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH"] == "/usr/bin/chromium"
    assert {"uri": "gh://microsoft/playwright-cli/playwright-cli"} in qa_cfg["skills"]

    # Architect and qa-tester are read-only in compiled Operating Loop
    assert "never change product code" in (out / "templates" / "architect" / "system-prompt.md").read_text()
    assert "never change product code" in (out / "templates" / "qa-tester" / "system-prompt.md").read_text()

    # Compiled shared agents.md and lead/reviewer prompts carry agent-team methodology
    shared = (out / "templates" / "_shared" / "agents.md").read_text()
    assert "survives container deletion" in shared
    assert "/workspace/.scratch/" in shared
    assert "~40% context utilization" in shared
    assert 'scion message <agent> "continue"' in shared

    lead_prompt = (out / "templates" / "lead" / "system-prompt.md").read_text()
    assert "architect" in lead_prompt
    assert "Prose-only docs, specs, and ADRs" in lead_prompt
    assert "Collapse to the minimum roles needed" in lead_prompt
    assert "reports/<issue>-design.md" in lead_prompt

    rev_prompt = (out / "templates" / "reviewer" / "system-prompt.md").read_text()
    assert "`Critical`" in rev_prompt and "`Required`" in rev_prompt and "`Nit`" in rev_prompt


def test_skill_registry_none_disables_default_uri_skills(tmp_path):
    spec = scion_factory.load_spec(PRESET)
    spec["team"]["skill_registry"] = "none"
    out = tmp_path / "kit"
    scion_factory.compile_factory_boilerplate(spec, out)

    lead_cfg = yaml.safe_load((out / "templates" / "lead" / "scion-agent.yaml").read_text())
    assert "skills" not in lead_cfg


def test_add_role_from_agent_team_binds_remote_template_uri_in_chain_of_command(tmp_path):
    spec = scion_factory.load_spec(PRESET)
    spec_path = tmp_path / "factory.yaml"
    scion_factory.save_spec(spec, spec_path)

    res = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "scion_factory.py"),
            "add-role",
            str(spec_path),
            "--role",
            "architect",
            "--from-agent-team",
            "--skill",
            "gh://scion-frontiers/agent-team/backlog-stewardship",
        ],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, res.stderr

    updated = scion_factory.load_spec(spec_path)
    arch = next(r for r in updated["team"]["build_roles"] if r["role"] == "architect")
    assert arch["template_uri"] == "gh://scion-frontiers/agent-team/templates/architect"
    assert "gh://scion-frontiers/agent-team/backlog-stewardship" in arch["skills"]

    out = tmp_path / "kit"
    scion_factory.compile_factory_boilerplate(updated, out)
    lead_prompt = (out / "templates" / "lead" / "system-prompt.md").read_text()
    assert "scion start architect --type gh://scion-frontiers/agent-team/templates/architect" in lead_prompt


def test_audit_skill_uris_rejects_conflicting_reviewer_skill_and_bad_uri(tmp_path):
    spec = scion_factory.load_spec(PRESET)
    for r in spec["team"]["build_roles"]:
        if r["role"] == "reviewer":
            r["skills"] = ["gh://addyosmani/agent-skills/code-review-and-quality"]
    report = scion_factory.evaluate_spec_readiness(spec)
    assert report["ready"] is False
    assert any("code-review-and-quality" in msg for msg in report["missing_or_weak"])

    # Invalid URI format is also rejected
    spec2 = scion_factory.load_spec(PRESET)
    spec2["team"]["build_roles"][0]["skills"] = ["gh://broken"]
    errs, _ = skill_curation.audit_skill_uris(spec2)
    assert any("invalid skill URI" in e for e in errs)


def test_local_skill_bank_injection_ignores_remote_uri_skills(tmp_path):
    bank = tmp_path / "bank"
    local_skill = bank / "custom-lint"
    local_skill.mkdir(parents=True)
    (local_skill / "SKILL.md").write_text("---\nname: custom-lint\ndescription: Custom lint\n---\n# Lint\n")

    spec = scion_factory.load_spec(PRESET)
    for r in spec["team"]["build_roles"]:
        if r["role"] == "backend":
            r["skills"] = ["custom-lint", "gh://scion-frontiers/agent-team/backlog-stewardship"]
    out = tmp_path / "kit"
    scion_factory.compile_factory_boilerplate(spec, out, skill_bank=bank)

    assert (out / "templates" / "backend" / "skills" / "custom-lint" / "SKILL.md").is_file()
    be_cfg = yaml.safe_load((out / "templates" / "backend" / "scion-agent.yaml").read_text())
    assert {"uri": "gh://scion-frontiers/agent-team/backlog-stewardship"} in be_cfg["skills"]


def test_discovery_proposes_agent_team_roles_from_repo_signals(tmp_path):
    (tmp_path / ".design").mkdir()
    (tmp_path / ".design" / "overview.md").write_text("# Design\n")
    (tmp_path / "playwright.config.ts").write_text("export default {};\n")
    (tmp_path / "astro.config.mjs").write_text("export default {};\n")
    for svc in ("auth", "billing", "catalog"):
        (tmp_path / "services" / svc).mkdir(parents=True)
        (tmp_path / "services" / svc / "main.py").write_text("pass\n")

    roles_service = {r["role"] for r in discovery.discover_roles(tmp_path, deploy="service")}
    assert {"architect", "eng-manager", "qa-tester", "web-builder"} <= roles_service

    roles_none = {r["role"] for r in discovery.discover_roles(tmp_path, deploy="none")}
    assert {"architect", "eng-manager", "web-builder"} <= roles_none
    assert "qa-tester" not in roles_none
