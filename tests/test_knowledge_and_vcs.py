"""Tests for the knowledge plane and optional-Git bootstrap (docs/plans/2026-10-20-knowledge-plane-and-optional-git.md)."""

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import scion_factory  # noqa: E402


def _starter():
    return scion_factory.load_spec(ROOT / "presets" / "starter-service.factory.yaml")


def _no_git(spec):
    spec = copy.deepcopy(spec)
    spec["vcs"] = {"enabled": False, "provider": "github", "repo": "", "build_branch": "main", "eval_branch": "eval-dashboard"}
    return spec


def _make_learnable_repo(path: Path, with_git: bool) -> Path:
    (path / "docs" / "adr").mkdir(parents=True)
    (path / "docs" / "adr" / "0001-use-postgres.md").write_text("# ADR 1\n")
    (path / "PRODUCT.md").write_text("# Product\n")
    (path / "api").mkdir()
    (path / "api" / "openapi.yaml").write_text("openapi: 3.0.0\n")
    (path / "DESIGN.md").write_text("# Design\n")
    (path / "CONTRIBUTING.md").write_text("# Contributing\n")
    (path / "go.mod").write_text("module example.com/widgets\n\ngo 1.24\n")
    if with_git:
        subprocess.run(["git", "-C", str(path), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(path), "remote", "add", "origin", "git@github.com:acme/widgets.git"], check=True)
    return path


# --- Learning from the target repository -----------------------------------


def test_inspect_detects_knowledge_sources_and_git_remote(tmp_path):
    repo = _make_learnable_repo(tmp_path / "widgets", with_git=True)
    det = scion_factory.inspect_repository(repo)["detected"]

    ks = det["knowledge_sources"]
    assert "docs/adr" in ks["adrs"]
    assert "PRODUCT.md" in ks["requirements"]
    assert "api/openapi.yaml" in ks["contracts"]
    assert "DESIGN.md" in ks["design"]
    assert "CONTRIBUTING.md" in ks["guides"]

    assert det["vcs"]["is_git_repo"] is True
    assert det["vcs"]["github_repo"] == "acme/widgets"


def test_draft_prefills_knowledge_sources_and_vcs(tmp_path):
    with_git = scion_factory.draft_spec_from_repo(_make_learnable_repo(tmp_path / "a", with_git=True))
    assert with_git["knowledge_plane"]["sources"]["adrs"] == ["docs/adr"]
    assert with_git["vcs"]["enabled"] is True
    assert with_git["vcs"]["repo"] == "acme/widgets"

    without_git = scion_factory.draft_spec_from_repo(_make_learnable_repo(tmp_path / "b", with_git=False))
    assert without_git["vcs"]["enabled"] is False
    assert without_git["vcs"]["repo"] == ""
    # Requirements and invariants are intent: the agent proposes them, the human confirms.
    assert without_git["knowledge_plane"]["requirements"] == []
    assert without_git["knowledge_plane"]["invariants"] == []


# --- Readiness rules -------------------------------------------------------


def test_no_git_spec_is_ready_without_repo():
    report = scion_factory.evaluate_spec_readiness(_no_git(_starter()))
    assert report["ready"] is True, report["missing_or_weak"]


def test_git_enabled_requires_repo_and_distinct_branches():
    spec = _starter()
    spec["vcs"]["repo"] = ""
    assert any("vcs.repo" in m for m in scion_factory.evaluate_spec_readiness(spec)["missing_or_weak"])

    spec = _starter()
    spec["vcs"]["eval_branch"] = spec["vcs"]["build_branch"]
    assert any("eval_branch" in m for m in scion_factory.evaluate_spec_readiness(spec)["missing_or_weak"])


def test_knowledge_plane_requires_requirements_and_invariants():
    spec = _starter()
    spec["knowledge_plane"]["requirements"] = []
    spec["knowledge_plane"]["invariants"] = []
    missing = " ".join(scion_factory.evaluate_spec_readiness(spec)["missing_or_weak"])
    assert "knowledge_plane.requirements" in missing
    assert "knowledge_plane.invariants" in missing


def test_wave_issue_citing_unknown_requirement_is_rejected():
    spec = _starter()
    spec["waves"][0]["issues"][0]["requirements"] = ["REQ-999"]
    missing = " ".join(scion_factory.evaluate_spec_readiness(spec)["missing_or_weak"])
    assert "REQ-999" in missing


# --- Generated knowledge plane ---------------------------------------------


def test_compile_renders_knowledge_plane(tmp_path):
    out = tmp_path / "kit"
    scion_factory.compile_factory_boilerplate(_starter(), out)
    kp = out / "knowledge-plane"
    for rel in ("INDEX.md", "product/requirements.md", "adr/_template.md", "invariants.md",
                "contracts/README.md", "glossary.md", "playbooks/README.md", "state/LIVE.md"):
        assert (kp / rel).exists(), rel
    assert list((kp / "adr").glob("ADR-001-*.md")), "seeded decisions must become ADR files"
    assert "REQ-001" in (kp / "product" / "requirements.md").read_text()
    # Golden data belongs to the eval team only.
    assert (out / "eval-workspace" / "golden" / "README.md").exists()
    assert not list(kp.rglob("*golden*"))
    # Agents are routed to the knowledge plane.
    assert "knowledge-plane/INDEX.md" in (out / "templates" / "_shared" / "agents.md").read_text()


def test_compile_never_overwrites_learned_knowledge(tmp_path):
    out = tmp_path / "kit"
    spec = _starter()
    scion_factory.compile_factory_boilerplate(spec, out)
    inv = out / "knowledge-plane" / "invariants.md"
    inv.write_text("EDITED BY AGENTS\n")

    scion_factory.compile_factory_boilerplate(spec, out)
    assert inv.read_text() == "EDITED BY AGENTS\n"

    scion_factory.compile_factory_boilerplate(spec, out, force_knowledge=True)
    assert inv.read_text() != "EDITED BY AGENTS\n"


def test_kp_check_passes_on_kit_and_catches_broken_links(tmp_path):
    out = tmp_path / "kit"
    scion_factory.compile_factory_boilerplate(_starter(), out)
    check = out / "knowledge-plane" / "kp-check.py"
    ok = subprocess.run([sys.executable, str(check), str(out / "knowledge-plane")], capture_output=True, text=True)
    assert ok.returncode == 0, ok.stdout + ok.stderr

    index = out / "knowledge-plane" / "INDEX.md"
    index.write_text(index.read_text() + "\n- [Guessed path](adr/ADR-001.md)\n")
    bad = subprocess.run([sys.executable, str(check), str(out / "knowledge-plane")], capture_output=True, text=True)
    assert bad.returncode != 0
    assert "ADR-001.md" in bad.stdout + bad.stderr


# --- Without Git, and adding it later --------------------------------------


def test_compile_without_git_uses_file_backlog_and_snapshot_deploys(tmp_path):
    out = tmp_path / "kit"
    scion_factory.compile_factory_boilerplate(_no_git(_starter()), out)

    backlog = json.loads((out / "backlog" / "issues.json").read_text())
    assert backlog and all(item["status"] == "ready" for item in backlog)
    assert all(item["id"] for item in backlog)

    assert not (out / "scripts" / "seed-github-waves.sh").exists()
    enable = out / "scripts" / "enable-git.sh"
    assert enable.exists()
    assert subprocess.run(["bash", "-n", str(enable)]).returncode == 0

    shared = (out / "templates" / "_shared" / "agents.md").read_text()
    assert "backlog/issues.json" in shared
    assert "tar" in shared and "state/LIVE.md" in shared
    assert "git archive" not in shared
    for path in (out / "templates").rglob("*.md"):
        assert "gh issue" not in path.read_text(), path


def test_enable_git_round_trip(tmp_path):
    spec_path = tmp_path / "factory.yaml"
    scion_factory.save_spec(_no_git(_starter()), spec_path)
    out = tmp_path / "kit"
    scion_factory.compile_factory_boilerplate(scion_factory.load_spec(spec_path), out)
    assert (out / "scripts" / "enable-git.sh").exists()

    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "scion_factory.py"), "enable-git", str(spec_path),
         "--repo", "acme/widgets"],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    spec = scion_factory.load_spec(spec_path)
    assert spec["vcs"]["enabled"] is True and spec["vcs"]["repo"] == "acme/widgets"

    scion_factory.compile_factory_boilerplate(spec, out)
    assert (out / "scripts" / "seed-github-waves.sh").exists()
    assert not (out / "scripts" / "enable-git.sh").exists(), "stale no-git script must be removed"
    assert "git archive" in (out / "templates" / "_shared" / "agents.md").read_text()
    # Backlog and knowledge plane survive the switch.
    assert (out / "backlog" / "issues.json").exists()


def test_presets_and_compiled_specs_match_schema(tmp_path):
    jsonschema = pytest.importorskip("jsonschema", reason="run with: uv run --with jsonschema ...")
    schema = json.loads((ROOT / "config" / "factory.schema.json").read_text())
    for preset in sorted((ROOT / "presets").glob("*.factory.yaml")):
        jsonschema.validate(scion_factory.load_spec(preset), schema)
    jsonschema.validate(_no_git(_starter()), schema)
