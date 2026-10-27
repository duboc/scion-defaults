import os
import sys
import subprocess
import time
import importlib.util

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from unittest import mock
import hub_client
import factory_store

def load_script(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


import pytest

class FakeFirestore:
    def __init__(self):
        self._docs = {}
    def get_document(self, collection, doc_id):
        return self._docs.get((collection, doc_id))
    def upsert_document(self, collection, doc_id, data):
        self._docs[(collection, doc_id)] = data

@pytest.fixture
def fake_firestore():
    return FakeFirestore()

watchdog_mod = load_script("watchdog_mod", os.path.join(ROOT, "scripts", "factory-watchdog.py"))
install_k8s = load_script("install_k8s", os.path.join(ROOT, "scripts", "install-k8s-control-plane.py"))

def test_sync_factory_agents_reads_from_hub_api_without_sqlite(fake_hub, monkeypatch):
    fake_hub.seed_agent("proj-1", slug="lead", phase="running", activity="working", harness="claude", task_summary="Wave 1")
    monkeypatch.setenv("HUB_URL", fake_hub.url)
    monkeypatch.setenv("SCION_HUB_TOKEN", "tok-admin")
    monkeypatch.setenv("FACTORY_PROJECTS", "demo")
    monkeypatch.setenv("FIRESTORE_PROJECT", "sandbox-proj")
    # Run sync-factory-agents.py --dry-run with SCION_HOME pointing at an empty dir (no hub.db!)
    res = subprocess.run(
        [sys.executable, "scripts/sync-factory-agents.py", "--dry-run"],
        cwd=ROOT, env={**os.environ, "SCION_HOME": "/nonexistent-scion-home", "HUB_URL": fake_hub.url, "SCION_HUB_TOKEN": "tok-admin", "FACTORY_PROJECTS": "demo"},
        capture_output=True, text=True,
    )
    assert res.returncode == 0, res.stderr
    assert "demo__lead" in res.stdout


def test_watchdog_api_mode_reaps_hung_subprocess_via_exec_and_persists_circuit_breaker_in_firestore(fake_hub, fake_firestore):
    fake_hub.seed_agent("proj-1", slug="backend", phase="running", runtime="kubernetes", pod_name="scion-backend")
    fake_hub.exec_outputs[("proj-1", "backend")] = (
        "  PID ELAPSED COMMAND         COMMAND\n"
        "    1    1200 sciontool       sciontool init\n"
        "  442     905 uvicorn         uvicorn app.main:app --port 8080\n"
    )
    client = hub_client.HubClient(base_url=fake_hub.url, token="tok-admin")
    state = factory_store.load_control_state(fake_firestore, "watchdog__default")
    watchdog_mod.run_api_watchdog(client, state, apply=True, runtime="kubernetes")
    factory_store.save_control_state(fake_firestore, "watchdog__default", state)

    print(f'EXEC COMMANDS: {fake_hub.exec_commands}'); assert any(cmd == ["kill", "-9", "442"] for cmd in fake_hub.exec_commands[("proj-1", "backend")])

    # Simulate 3 crash restarts -> 4th run in a fresh process loads state from Firestore and trips circuit breaker
    state["restarts"] = {"proj-1/backend": [time.time() - 10, time.time() - 5, time.time() - 1]}
    factory_store.save_control_state(fake_firestore, "watchdog__default", state)
    fake_hub.set_agent_phase("proj-1", "backend", "error")
    reloaded = factory_store.load_control_state(fake_firestore, "watchdog__default")
    watchdog_mod.run_api_watchdog(client, reloaded, apply=True, runtime="kubernetes")
    assert not fake_hub.started_agents  # circuit breaker blocked the 4th restart!


def test_install_k8s_control_plane_renders_cronjobs_and_workload_identity_ksa():
    manifests = install_k8s.render_manifests(
        namespace="scion-hub-prod",
        hub_url="https://hub.example.com",
        factory_projects="demo,demo-eval",
        sandbox_project="my-sandbox",
        firestore_project="my-sandbox",
        gsa_email="agent-worker@my-sandbox.iam.gserviceaccount.com",
    )
    kinds = {(m["kind"], m["metadata"]["name"]) for m in manifests}
    assert ("ServiceAccount", "scion-factory-control") in kinds
    assert ("ConfigMap", "scion-factory-control-scripts") in kinds
    assert ("CronJob", "scion-factory-sync") in kinds
    assert ("CronJob", "scion-factory-watchdog") in kinds
    sa = next(m for m in manifests if m["kind"] == "ServiceAccount")
    assert sa["metadata"]["annotations"]["iam.gke.io/gcp-service-account"] == "agent-worker@my-sandbox.iam.gserviceaccount.com"
