"""Factories with nothing to deploy (spec §8): `infrastructure.deploy: none` for a library, CLI or monorepo package.

No deployer, no service, no live checks: the evaluation team grades the code itself (L1 build and tests, L2 API
compatibility, L3 quality metric, L4 dependency and vulnerability checks, L5 benchmarks).
"""
import copy
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import scion_factory  # noqa: E402

LIBRARY = ROOT / "presets" / "go-library.factory.yaml"
STARTER = ROOT / "presets" / "starter-service.factory.yaml"


def _prompt(out, role):
    return (out / "templates" / role / "system-prompt.md").read_text()


def _compile(spec, out):
    scion_factory.compile_factory_boilerplate(spec, out)
    return out


@pytest.fixture
def library():
    return scion_factory.load_spec(LIBRARY)


@pytest.fixture
def kit(tmp_path, library):
    return _compile(library, tmp_path / "kit")


def test_library_preset_is_ready_and_has_no_deployers(kit, library):
    readiness = scion_factory.evaluate_spec_readiness(library)
    assert readiness["ready"], readiness["missing_or_weak"]
    assert scion_factory.deploy_mode(library) == "none"
    assert not (kit / "templates" / "deployer").exists()
    assert not (kit / "templates" / "eval-deployer").exists()
    env = (kit / "config" / "factory.env.example").read_text()
    for var in ("SERVING_SERVICE", "DASHBOARD_SERVICE", "RUNTIME_SA_NAME", "DEPLOYER_SA_NAME", "DEPLOYER_IAM_ROLES"):
        assert var not in env, var
    assert "BUILD_PROJECT=verso" in env


def test_code_pyramid_replaces_live_checks(kit):
    ev = _prompt(kit, "domain-evaluator")
    assert "govulncheck" in ev and "apidiff" in ev and "Health" not in ev
    assert "Target Cloud Run Service" not in ev
    assert "test_coverage_pct >= 90" in ev
    verify = (kit / "scripts" / "verify-factory.sh").read_text()
    assert "curl" not in verify and "go test" in verify
    assert "go build ./..." in verify and "govulncheck ./..." in verify
    assert "set -euo pipefail" in verify
    subprocess.run(["bash", "-n", str(kit / "scripts" / "verify-factory.sh")], check=True)


def test_deploy_none_kit_leaves_out_deploy_instructions(kit):
    shared = (kit / "templates" / "_shared" / "agents.md").read_text()
    assert "gcloud run deploy" not in shared and "Cloud Run" not in shared
    assert "Health" not in shared and "Latency SLO" not in shared
    assert "govulncheck ./..." in shared
    root = (kit / "AGENTS.md").read_text()
    assert "Cloud Run" not in root and "Health" not in root and "govulncheck ./..." in root
    assert "none (nothing to deploy)" in root
    live = (kit / "knowledge-plane" / "state" / "LIVE.md").read_text()
    assert "| Service | none (nothing to deploy) |" in live
    for role in ("lead", "backend", "reviewer", "eval-lead", "domain-evaluator"):
        prompt = _prompt(kit, role)
        assert "Target Cloud Run Service" not in prompt and "L5 Latency SLO" not in prompt, role
        assert "nothing to deploy" in prompt, role
    # Evaluation runs record tool output and benchmark deltas, not service latency.
    assert "p95_ms" not in _prompt(kit, "domain-evaluator") and "layer_results" in _prompt(kit, "eval-lead")


def test_deploy_none_verify_runs_in_the_monorepo_product_folder(tmp_path, library):
    library["monorepo"] = {"subdir": "pkg/verso", "agent_prefix": "verso"}
    out = _compile(library, tmp_path / "kit")
    verify = (out / "scripts" / "verify-factory.sh").read_text()
    assert 'cd "$WORKSPACE_DIR/pkg/verso"' in verify
    subprocess.run(["bash", "-n", str(out / "scripts" / "verify-factory.sh")], check=True)


def test_deploy_none_verify_runs_the_code_checks(tmp_path, kit):
    """With WORKSPACE_DIR set, verify-factory.sh runs build, tests and the L4 tool there, and fails when they fail."""
    ws = tmp_path / "ws"
    ws.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    for tool in ("go", "govulncheck"):
        stub = bin_dir / tool
        stub.write_text(f'#!/usr/bin/env bash\necho "{tool} $*" >> "{log}"\n[[ "$*" != *fail* ]]\n')
        stub.chmod(0o755)
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "WORKSPACE_DIR": str(ws)}
    script = kit / "scripts" / "verify-factory.sh"
    res = subprocess.run(["bash", str(script)], env=env, capture_output=True, text=True)
    assert res.returncode == 0, res.stdout + res.stderr
    calls = log.read_text().splitlines()
    assert calls == ["go build ./...", "go test -race ./...", "govulncheck ./..."]
    # Without WORKSPACE_DIR the code checks are skipped, like the service script's live checks.
    res = subprocess.run(["bash", str(script)], env={"PATH": "/usr/bin:/bin"}, capture_output=True, text=True)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "SKIP" in res.stdout
    # A missing L4 tool fails the check with a clear message.
    (bin_dir / "govulncheck").unlink()
    res = subprocess.run(["bash", str(script)], env=env, capture_output=True, text=True)
    assert res.returncode != 0 and "govulncheck" in res.stderr


def test_deploy_none_rejects_service_roles(library):
    spec = copy.deepcopy(library)
    spec["team"]["build_roles"].append({"role": "deployer", "harness": "claude", "model": "opus",
                                        "responsibility": "Deploys the service to Cloud Run"})
    missing = scion_factory.evaluate_spec_readiness(spec)["missing_or_weak"]
    assert any("deployer" in m and "deploy: none" in m for m in missing), missing

    spec = copy.deepcopy(library)
    spec["team"]["eval_roles"].append({"role": "latency-prober", "harness": "claude", "model": "opus",
                                       "responsibility": "Measures p95 latency of the live service"})
    missing = scion_factory.evaluate_spec_readiness(spec)["missing_or_weak"]
    assert any("latency-prober" in m and "deploy: none" in m for m in missing), missing

    spec = copy.deepcopy(library)
    spec["team"]["build_roles"][1]["gcp_identity"] = "deployer-sa"
    missing = scion_factory.evaluate_spec_readiness(spec)["missing_or_weak"]
    assert any("backend" in m and "gcp_identity" in m and "deploy: none" in m for m in missing), missing


def test_deploy_none_needs_no_service_fields_but_service_still_does(library):
    assert not any(k in library["infrastructure"] for k in ("serving_service", "eval_service", "deployer_iam_roles"))
    assert "l1_health" not in library["evaluation_pyramid"] and "l5_latency_slo" not in library["evaluation_pyramid"]
    service = copy.deepcopy(library)
    service["infrastructure"]["deploy"] = "service"
    missing = " ".join(scion_factory.evaluate_spec_readiness(service)["missing_or_weak"])
    for field in ("serving_service", "eval_service", "deployer_iam_roles", "l1_health.endpoint", "p95_ms"):
        assert field in missing, field


def test_unknown_language_code_pyramid_asks_the_po(library):
    spec = copy.deepcopy(library)
    spec["stack"]["primary_language"] = "elixir"
    text = scion_factory.render_code_pyramid(spec)
    assert "ask the PO for the tool" in text
    assert len([line for line in text.splitlines() if line.strip()]) == 5


def test_code_pyramid_uses_the_language_tools(library):
    for lang, tools in scion_factory.role_library.CODE_PYRAMID_TOOLS.items():
        spec = copy.deepcopy(library)
        spec["stack"]["primary_language"] = lang
        text = scion_factory.render_code_pyramid(spec)
        assert tools["l2"] in text and tools["l4"] in text, lang
        assert "ask the PO for the tool" not in text, lang
        for layer in ("L1", "L2", "L3", "L4", "L5"):
            assert f"**{layer}" in text, (lang, layer)


def test_deploy_none_verify_unknown_language_skips_l4_with_a_note(tmp_path, library):
    library["stack"]["primary_language"] = "elixir"
    out = _compile(library, tmp_path / "kit")
    verify = (out / "scripts" / "verify-factory.sh").read_text()
    assert "ask the PO for the tool" in verify and "curl" not in verify
    subprocess.run(["bash", "-n", str(out / "scripts" / "verify-factory.sh")], check=True)


def test_deploy_target_grill_question():
    q = next(q for q in scion_factory.get_grill_questions() if q["id"] == "deploy_target")
    assert q["field_path"] == "infrastructure.deploy"
    assert q["challenge_prompt"] == (
        "Is there a Cloud Run service to deploy? A library, CLI or monorepo package is `none`")
    assert q["lessons"] == ["L29"]
    assert q["question"] and q["why_we_ask"]


def test_service_specs_unchanged(tmp_path):
    out = _compile(scion_factory.load_spec(STARTER), tmp_path / "kit")
    env = (out / "config" / "factory.env.example").read_text()
    for line in ("SERVING_SERVICE=pulse-ledger-serving", "DASHBOARD_SERVICE=pulse-ledger-eval-dashboard",
                 "DEPLOYER_SA_NAME=deployer-sa", "RUNTIME_SA_NAME=pulse-ledger-serving"):
        assert line in env, line
    verify = (out / "scripts" / "verify-factory.sh").read_text()
    assert 'curl -s -o /dev/null -w \'%{http_code}\' "$SERVING_URL/health"' in verify
    assert "WORKSPACE_DIR" not in verify
    shared = (out / "templates" / "_shared" / "agents.md").read_text()
    assert "gcloud run deploy" in shared and "**L1 Health:** `GET /health`" in shared
    assert "Target Cloud Run Service:** `pulse-ledger-serving`" in _prompt(out, "backend")
    assert "**L5 Latency SLO:**" in _prompt(out, "domain-evaluator")
    live = (out / "knowledge-plane" / "state" / "LIVE.md").read_text()
    assert "| Service | `pulse-ledger-serving` |" in live


@pytest.mark.parametrize("preset", ["psearch", "acme-airlines"])
def test_search_evaluator_counts_as_the_domain_evaluator(tmp_path, preset):
    spec = scion_factory.load_spec(ROOT / "presets" / f"{preset}.factory.yaml")
    out = _compile(spec, tmp_path / "kit")
    assert (out / "templates" / "search-evaluator").exists()
    assert not (out / "templates" / "domain-evaluator").exists()
