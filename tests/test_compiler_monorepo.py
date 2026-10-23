"""Monorepo factories from the interview compiler: `monorepo: {subdir, agent_prefix}` in factory.yaml.

Spec: docs/plans/2026-10-22-monorepo-factories.md (follow-up "compiler monorepo kits").
"""
import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import scion_factory  # noqa: E402

PRESET = ROOT / "presets" / "starter-service.factory.yaml"


def _spec(**monorepo):
    spec = scion_factory.load_spec(PRESET)
    if monorepo:
        spec["monorepo"] = monorepo
    return spec


def _compile(tmp_path, spec):
    out = tmp_path / "kit"
    scion_factory.compile_factory_boilerplate(spec, out)
    return out


def _env(out):
    keys = {}
    for line in (out / "config" / "factory.env.example").read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if "=" in line:
            k, v = line.split("=", 1)
            keys[k.strip()] = v.strip().strip('"')
    return keys


@pytest.fixture
def mono(tmp_path):
    return _compile(tmp_path, _spec(subdir="pkg/kubelet", agent_prefix="pulse-ledger"))


def test_monorepo_kit_starts_agents_in_the_product_folder(mono):
    lead = (mono / "templates" / "lead" / "scion-agent.yaml").read_text()
    assert "default_harness_config: claude-factory" in lead
    assert 'FACTORY_SUBDIR: "pkg/kubelet"' in lead
    evaluator = (mono / "templates" / "domain-evaluator" / "scion-agent.yaml").read_text()
    assert "default_harness_config: antigravity-factory" in evaluator


def test_monorepo_kit_reads_the_product_knowledge_plane(mono):
    shared = (mono / "templates" / "_shared" / "agents.md").read_text()
    assert "/workspace/pkg/kubelet/knowledge-plane/INDEX.md" in shared
    assert "/workspace/knowledge-plane" not in shared
    lead = (mono / "templates" / "lead" / "system-prompt.md").read_text()
    assert "/workspace/pkg/kubelet/knowledge-plane/INDEX.md" in lead
    evaluator = (mono / "templates" / "domain-evaluator" / "system-prompt.md").read_text()
    assert "/workspace/pkg/kubelet/tools/factory_store.py" in evaluator
    assert "/workspace/tools/" not in evaluator


def test_monorepo_kit_names_agents_with_the_prefix(mono):
    lead = (mono / "templates" / "lead" / "system-prompt.md").read_text()
    assert "Your agent name is `pulse-ledger-lead`" in lead
    assert "`pulse-ledger-backend`" in lead
    shared = (mono / "templates" / "_shared" / "agents.md").read_text()
    assert "`pulse-ledger-deployer`" in shared


def test_monorepo_kit_env_carries_prefix_subdir_and_knowledge_plane_source(mono):
    env = _env(mono)
    assert env["AGENT_PREFIX"] == "pulse-ledger"
    assert env["FACTORY_SUBDIR"] == "pkg/kubelet"
    assert env["KNOWLEDGE_PLANE_SOURCES"] == "pulse-ledger=pkg/kubelet/knowledge-plane"


def test_kit_without_monorepo_is_unchanged(tmp_path):
    out = _compile(tmp_path, _spec())
    assert "default_harness_config: claude\n" in (out / "templates" / "lead" / "scion-agent.yaml").read_text()
    assert "FACTORY_SUBDIR" not in (out / "templates" / "lead" / "scion-agent.yaml").read_text()
    assert "/workspace/knowledge-plane/INDEX.md" in (out / "templates" / "_shared" / "agents.md").read_text()
    assert "AGENT_PREFIX" not in _env(out)


@pytest.mark.parametrize("monorepo,needle", [
    ({"subdir": "../x", "agent_prefix": "k"}, "monorepo.subdir"),
    ({"subdir": "/abs", "agent_prefix": "k"}, "monorepo.subdir"),
    ({"subdir": "pkg/kubelet", "agent_prefix": "Kube_let"}, "monorepo.agent_prefix"),
    ({"subdir": "", "agent_prefix": "k"}, "monorepo.subdir"),
])
def test_readiness_rejects_a_bad_monorepo_block(monorepo, needle):
    report = scion_factory.evaluate_spec_readiness(_spec(**monorepo))
    assert report["ready"] is False
    assert any(needle in m for m in report["missing_or_weak"])


def test_readiness_accepts_a_monorepo_block():
    assert scion_factory.evaluate_spec_readiness(_spec(subdir="pkg/kubelet", agent_prefix="kubelet"))["ready"] is True


def test_schema_allows_the_monorepo_block():
    jsonschema = pytest.importorskip("jsonschema")
    import yaml
    schema = json.loads((ROOT / "config" / "factory.schema.json").read_text())
    spec = copy.deepcopy(yaml.safe_load(PRESET.read_text()))
    spec["monorepo"] = {"subdir": "pkg/kubelet", "agent_prefix": "kubelet"}
    jsonschema.validate(spec, schema)
    assert "monorepo" in schema["properties"]


@pytest.mark.parametrize("subdir", ["pkg/kubelet", "staging/src/k8s.io/kubectl"])
def test_monorepo_knowledge_plane_links_reach_the_repo_root(tmp_path, subdir):
    """Learned sources are repo-root paths; the knowledge plane sits at <subdir>/knowledge-plane (L28 k8s run)."""
    import subprocess

    spec = _spec(subdir=subdir, agent_prefix="kubelet")
    spec["knowledge_plane"]["sources"]["guides"] = ["AGENTS.md"]
    spec["knowledge_plane"]["sources"]["contracts"] = ["api/openapi.yaml"]
    out = _compile(tmp_path, spec)

    repo = tmp_path / "repo"
    (repo / "api").mkdir(parents=True)
    (repo / "AGENTS.md").write_text("# root\n")
    (repo / "api" / "openapi.yaml").write_text("openapi: 3.0.0\n")
    kp = repo / subdir / "knowledge-plane"
    kp.parent.mkdir(parents=True)
    import shutil
    shutil.copytree(out / "knowledge-plane", kp)

    res = subprocess.run([sys.executable, str(kp / "kp-check.py"), str(kp)], capture_output=True, text=True)
    assert res.returncode == 0, res.stdout + res.stderr
