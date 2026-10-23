"""Chain of command: the PO starts only the two leads; each lead starts its own team as its Scion children.

Scion records the creator of every agent (ancestry root -> parent -> child) and the Hub's tree view shows it; the
Hub also sends an agent's notifications to its creator. A lead that starts its team is therefore on top of the tree
and hears about its team, and every team member reports to it (Kubernetes monorepo run, 2026-10-23).
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import scion_factory  # noqa: E402

PRESET = ROOT / "presets" / "starter-service.factory.yaml"


def _compile(tmp_path, **monorepo):
    spec = scion_factory.load_spec(PRESET)
    if monorepo:
        spec["monorepo"] = monorepo
    out = tmp_path / "kit"
    scion_factory.compile_factory_boilerplate(spec, out)
    return spec, out


def _prompt(out, role):
    return (out / "templates" / role / "system-prompt.md").read_text()


@pytest.fixture
def mono(tmp_path):
    return _compile(tmp_path, subdir="pkg/kubelet", agent_prefix="kubelet")


def test_build_lead_starts_each_teammate_as_its_child(mono):
    spec, out = mono
    lead = _prompt(out, "lead")
    assert "## Chain of command" in lead
    for r in spec["team"]["build_roles"]:
        if r["role"] == "lead":
            continue
        assert f'scion start kubelet-{r["role"]} -t {r["role"]} --harness-auth vertex-ai' in lead
    assert "scion start kubelet-lead" not in lead
    assert "scion resume" in lead


def test_eval_lead_starts_the_evaluation_team(mono):
    spec, out = mono
    eval_lead = _prompt(out, "eval-lead")
    for r in spec["team"]["eval_roles"]:
        if r["role"] == "eval-lead":
            continue
        assert f'scion start kubelet-{r["role"]} -t {r["role"]} --harness-auth vertex-ai' in eval_lead
    # The evaluation team never starts or commands build agents.
    assert "scion start kubelet-backend" not in eval_lead


def test_team_members_report_to_their_lead_and_start_no_agents(mono):
    spec, out = mono
    for r in spec["team"]["build_roles"]:
        if r["role"] == "lead":
            continue
        text = _prompt(out, r["role"])
        assert "You report to `kubelet-lead`" in text
        assert "scion start" not in text
    for r in spec["team"]["eval_roles"]:
        if r["role"] == "eval-lead":
            continue
        assert "You report to `kubelet-eval-lead`" in _prompt(out, r["role"])


def test_single_repo_kit_uses_plain_role_names(tmp_path):
    spec, out = _compile(tmp_path)
    lead = _prompt(out, "lead")
    mates = [r["role"] for r in spec["team"]["build_roles"] if r["role"] != "lead"]
    assert mates
    assert f"scion start {mates[0]} -t {mates[0]} --harness-auth vertex-ai" in lead
    assert "You report to `lead`" in _prompt(out, mates[0])


def test_readme_starts_only_the_two_leads():
    readme = (ROOT / "README.md").read_text()
    step8 = readme.split("### Step 8", 1)[1].split("\n### ", 1)[0]
    assert 'echo "$BUILD_AGENTS"' not in step8
    assert '-t lead --project="$BUILD_PROJECT"' in step8
    assert '-t eval-lead --project="$EVAL_PROJECT"' in step8


def test_repo_lead_templates_start_their_team():
    for role in ("lead", "eval-lead"):
        text = (ROOT / "templates" / role / "system-prompt.md").read_text()
        assert "scion start" in text, role
        assert "If the admin created your team" not in text, role


def test_only_the_po_closes_issues(mono):
    """kubelet-lead closed #1 itself while the other leads left theirs open: the kit must say who accepts."""
    _, out = mono
    shared = (out / "templates" / "_shared" / "agents.md").read_text()
    assert "The PO accepts work by closing the issue" in shared
    assert "never close an issue" in shared
