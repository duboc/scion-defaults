import json
import os
import subprocess
import sys
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'scripts')))
import scion_factory
from test_hub_client import fake_hub, CrossOriginHandler, get_free_port

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

def _starter():
    import yaml
    with open(os.path.join(ROOT, "presets", "starter-service.factory.yaml"), "r") as f:
        return yaml.safe_load(f)

def write_gke_factory_env(tmp_path, hub_url):
    env_file = tmp_path / "factory.env"
    env_file.write_text(f"""
HUB_URL={hub_url}
SANDBOX_PROJECT=my-sandbox
SANDBOX_REGION=us-central1
FIRESTORE_PROJECT=my-sandbox
WORKER_SA_NAME=agent-worker
DEPLOYER_SA_NAME=deploy-sa
RUNTIME_SA_NAME=runtime-sa
HUB_PROJECT=my-hub
HUB_SA=my-hub-sa
DATA_BUCKET=my-bucket
""")
    return env_file

def fake_cli_env(tmp_path, hub_url):
    env = os.environ.copy()
    env["HUB_URL"] = hub_url
    
    # Fake binaries so scripts don't hit GCP or k8s
    bin_dir = tmp_path / "fake_bin"
    bin_dir.mkdir(exist_ok=True)
    for b in ("gcloud", "kubectl", "make", "git"):
        f = bin_dir / b
        f.write_text("#!/bin/bash\nexit 0\n")
        f.chmod(0o755)
    env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
    return env

def test_bad_infrastructure_runtime_fails_readiness_while_kubernetes_compiles_gke_env(tmp_path):
    spec = _starter()
    spec["infrastructure"]["runtime"] = "ecs"
    report = scion_factory.evaluate_spec_readiness(spec)
    assert report["ready"] is False
    assert any("infrastructure.runtime" in m for m in report["missing_or_weak"])

    spec["infrastructure"]["runtime"] = "kubernetes"
    spec["infrastructure"]["runtime_profile"] = "gke"
    assert scion_factory.evaluate_spec_readiness(spec)["ready"] is True
    scion_factory.compile_factory_boilerplate(spec, tmp_path / "kit")
    env_example = (tmp_path / "kit" / "config" / "factory.env.example").read_text()
    assert "AGENT_RUNTIME=kubernetes" in env_example
    assert "RUNTIME_PROFILE=gke" in env_example
    assert "WORKER_SA_NAME=agent-worker" in env_example

def test_scion_factory_provision_dry_run_and_apply_against_gke_fake_hub(fake_hub, tmp_path):
    spec = _starter()
    spec["project"]["slug"] = "starter-service"
    spec["project"]["eval_slug"] = "starter-service-eval"
    spec["infrastructure"]["runtime"] = "kubernetes"
    spec["infrastructure"]["runtime_profile"] = "gke"
    spec_path = tmp_path / "factory.yaml"
    kit_dir = tmp_path / "kit"
    scion_factory.save_spec(spec, spec_path)
    scion_factory.compile_factory_boilerplate(spec, kit_dir)

    env_file = write_gke_factory_env(tmp_path, fake_hub.url)
    
    r_dry = subprocess.run(
        [sys.executable, "scripts/scion_factory.py", "provision", str(spec_path),
         "--kit", str(kit_dir), "--env", str(env_file), "--json"],
        cwd=ROOT, env=fake_cli_env(tmp_path, fake_hub.url), capture_output=True, text=True,
    )
    assert r_dry.returncode == 0, r_dry.stderr
    assert json.loads(r_dry.stdout)["apply"] is False
    assert "starter-service" not in fake_hub.projects_by_slug

    r_apply = subprocess.run(
        [sys.executable, "scripts/scion_factory.py", "provision", str(spec_path),
         "--kit", str(kit_dir), "--env", str(env_file), "--apply", "--go", "--json"],
        cwd=ROOT, env=fake_cli_env(tmp_path, fake_hub.url), capture_output=True, text=True,
    )
    assert r_apply.returncode == 0, r_apply.stderr
    out = json.loads(r_apply.stdout)
    assert out["apply"] is True
    assert {"starter-service", "starter-service-eval"} <= set(fake_hub.projects_by_slug)
    build_pid = fake_hub.projects_by_slug["starter-service"]["id"]
    print("DEBUG PROJECT SETTINGS:", fake_hub.project_settings)
    assert fake_hub.project_settings[build_pid]["activeProfile"] == "gke"
    assert fake_hub.project_settings[build_pid]["defaultGCPIdentityMode"] == "assign"
    assert "INDEX.md" in fake_hub.shared_dir_files[(build_pid, "knowledge-plane")]
    assert "lead" in fake_hub.agents_by_project[build_pid]
    assert any("starter-service-lead-heartbeat" == s["name"] for s in fake_hub.schedules[build_pid])