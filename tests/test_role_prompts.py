"""Role prompts: each role's protocol from the library, its repo focus, file briefs and issue sizing (spec §5.4, §6).

The lead sizes every issue against the roles present in its roster and briefs teammates through files in the
scratchpad (never inline: shell metacharacters and long prompts break). A team member reads the brief file named in
the message, and a task is done only when its result is pushed or sent in a message.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import scion_factory  # noqa: E402

PRESET = ROOT / "presets" / "starter-service.factory.yaml"


def _prompt(out, role):
    return (out / "templates" / role / "system-prompt.md").read_text()


@pytest.fixture
def spec():
    return scion_factory.load_spec(PRESET)


@pytest.fixture
def kit(tmp_path, spec):
    out = tmp_path / "kit"
    scion_factory.compile_factory_boilerplate(spec, out)
    return out


def test_specialist_prompts_carry_their_protocol(kit):
    assert "## Role protocol" in _prompt(kit, "investigator")
    assert "Reproduce first" in _prompt(kit, "investigator")
    assert "Docs follow code" in _prompt(kit, "doc-writer")


def test_focus_lists_commands_and_docs(kit, spec):
    investigator = _prompt(kit, "investigator")
    assert "## Focus" in investigator
    assert spec["stack"]["test_command"] in investigator.split("## Focus", 1)[1]
    assert "knowledge-plane/INDEX.md" in _prompt(kit, "doc-writer").split("## Focus", 1)[1]


def test_lead_sizes_with_only_the_roles_present(kit):
    lead = _prompt(kit, "lead")
    assert "## Sizing an issue" in lead and "investigator" in lead
    assert "security-auditor" not in lead          # not in this roster


def test_briefs_are_files_in_the_scratchpad(kit):
    lead = _prompt(kit, "lead")
    assert "## Briefs" in lead
    assert "/scion-volumes/scratchpad/briefs/" in lead
    assert "Read the brief file named in the message" in _prompt(kit, "backend")


def test_every_team_member_has_the_durability_rule(kit, spec):
    for r in spec["team"]["build_roles"] + spec["team"]["eval_roles"]:
        if r["role"] in ("lead", "eval-lead"):
            continue
        assert "done only when its result is pushed or sent in a message" in _prompt(kit, r["role"]), r["role"]
    assert "done only when its result is pushed or sent in a message" in _prompt(kit, "researcher")


def test_unknown_role_gets_a_generic_prompt(tmp_path, spec):
    spec["team"]["build_roles"].append(
        {"role": "data-wrangler", "harness": "antigravity", "model": "m", "responsibility": "Cleans CSVs"})
    out = tmp_path / "kit"
    scion_factory.compile_factory_boilerplate(spec, out)
    text = _prompt(out, "data-wrangler")
    assert "Cleans CSVs" in text
    assert "## Chain of command" in text
    assert "## Role protocol" not in text


def test_role_focus_defaults_and_explicit_focus_wins(spec):
    stack = spec["stack"]
    backend = scion_factory.role_focus(spec, {"role": "backend"})
    assert backend["paths"] == ["."]
    assert backend["commands"][:2] == [stack["build_command"], stack["test_command"]]
    researcher = scion_factory.role_focus(spec, {"role": "researcher"})
    assert researcher["docs"][-1] == "/workspace/knowledge-plane/INDEX.md"
    assert researcher["commands"] == []
    assert scion_factory.role_focus(spec, {"role": "reviewer"}) == {"paths": ["."], "commands": [], "docs": []}

    explicit = scion_factory.role_focus(spec, {"role": "backend", "focus": {"paths": ["api/"]}})
    assert explicit == {"paths": ["api/"], "commands": [], "docs": []}


def test_role_focus_in_a_monorepo_uses_the_product_folder(spec):
    spec["monorepo"] = {"subdir": "pkg/kubelet", "agent_prefix": "kubelet"}
    spec["knowledge_plane"]["sources"]["design"] = ["ARCHITECTURE.md"]
    doc = scion_factory.role_focus(spec, {"role": "doc-writer"})
    assert doc["paths"] == ["pkg/kubelet"]
    assert doc["docs"] == ["/workspace/ARCHITECTURE.md", "/workspace/pkg/kubelet/knowledge-plane/INDEX.md"]


def test_monorepo_lead_sizes_with_prefixed_agent_names(tmp_path, spec):
    spec["monorepo"] = {"subdir": "pkg/kubelet", "agent_prefix": "kubelet"}
    out = tmp_path / "kit"
    scion_factory.compile_factory_boilerplate(spec, out)
    sizing = _prompt(out, "lead").split("## Sizing an issue", 1)[1].split("\n## ", 1)[0]
    assert "bug → `kubelet-investigator` first" in sizing
    assert "UI → `kubelet-frontend`" in sizing
    assert "release-manager" not in sizing


def test_eval_lead_briefs_by_file_and_sizes_only_with_its_own_team(kit):
    eval_lead = _prompt(kit, "eval-lead")
    assert "/scion-volumes/scratchpad/briefs/" in eval_lead
    assert "investigator" not in eval_lead
