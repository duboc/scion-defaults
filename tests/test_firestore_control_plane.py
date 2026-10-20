"""Tests for the serverless Firestore control plane (docs/plans/2026-10-20-firestore-control-plane.md)."""

import datetime
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import factory_store  # noqa: E402
import scion_factory  # noqa: E402


# --- factory_store: value encoding -----------------------------------------


def test_firestore_value_encoding_round_trip():
    when = datetime.datetime(2026, 10, 20, 12, 0, 0, tzinfo=datetime.timezone.utc)
    doc = {"s": "x", "i": 3, "f": 1.5, "b": True, "n": None, "t": when,
           "a": [1, "y"], "m": {"k": 2}}
    fields = factory_store.to_fields(doc)
    assert fields["s"] == {"stringValue": "x"}
    assert fields["i"] == {"integerValue": "3"}
    assert fields["f"] == {"doubleValue": 1.5}
    assert fields["b"] == {"booleanValue": True}
    assert fields["n"] == {"nullValue": None}
    assert fields["t"] == {"timestampValue": "2026-10-20T12:00:00Z"}
    assert fields["a"] == {"arrayValue": {"values": [{"integerValue": "1"}, {"stringValue": "y"}]}}
    assert fields["m"] == {"mapValue": {"fields": {"k": {"integerValue": "2"}}}}

    back = factory_store.from_fields(fields)
    assert back["i"] == 3 and back["a"] == [1, "y"] and back["m"] == {"k": 2} and back["n"] is None
    assert back["t"] == "2026-10-20T12:00:00Z"


def test_agent_status_writes_use_stable_ids_and_server_time():
    store = factory_store.FirestoreStore(project="demo-proj", database="(default)")
    rows = [{"project_slug": "demo", "agent_name": "lead", "agent_id": "a1", "current_turns": None}]
    writes = factory_store.agent_status_writes(store, rows)
    assert writes[0]["update"]["name"] == (
        "projects/demo-proj/databases/(default)/documents/factory_agent_status/demo__lead")
    assert writes[0]["update"]["fields"]["current_turns"] == {"nullValue": None}
    assert writes[0]["updateTransforms"] == [{"fieldPath": "synced_at", "setToServerValue": "REQUEST_TIME"}]


def test_eval_run_validation():
    good = {"run_id": "r-1", "project_slug": "demo", "overall_status": "pass",
            "primary_metric_name": "accuracy", "primary_metric_value": 0.99}
    assert factory_store.validate_eval_run(good) == []
    bad = dict(good)
    del bad["primary_metric_name"]
    bad["run_id"] = "has/slash"
    problems = " ".join(factory_store.validate_eval_run(bad))
    assert "primary_metric_name" in problems and "run_id" in problems


def test_put_eval_run_dry_run_prints_commit(tmp_path):
    run = tmp_path / "run.json"
    run.write_text(json.dumps({"run_id": "r-2", "project_slug": "demo", "overall_status": "fail",
                               "primary_metric_name": "accuracy", "primary_metric_value": 0.5,
                               "requirements": [{"id": "REQ-001", "status": "fail"}]}))
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "factory_store.py"), "--project", "demo-proj", "--dry-run",
         "put-eval-run", "--file", str(run)],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    req = json.loads(proc.stdout)
    assert req["url"].endswith("/databases/(default)/documents:commit")
    write = req["body"]["writes"][0]
    assert write["update"]["name"].endswith("/eval_runs/r-2")
    assert write["updateTransforms"][0]["fieldPath"] == "evaluated_at"


# --- Hub sync --------------------------------------------------------------


def _fake_hub(home: Path):
    home.mkdir()
    db = sqlite3.connect(home / "hub.db")
    db.executescript("""
        CREATE TABLE projects (id TEXT, slug TEXT);
        CREATE TABLE agents (id TEXT, name TEXT, project_id TEXT, template TEXT, phase TEXT, activity TEXT,
            created_by TEXT, current_turns INT, current_model_calls INT, task_summary TEXT, message TEXT,
            last_activity_event TEXT, applied_config TEXT, harness_config TEXT, deleted_at TEXT);
        INSERT INTO projects VALUES ('p1', 'demo'), ('p2', 'other');
        INSERT INTO agents VALUES ('a1', 'lead', 'p1', 'lead', 'running', 'idle', 'u1', 0, 0, 'done', NULL,
            '2026-10-20 12:00:00.1 +0000 UTC', '{"harnessConfig": "claude"}', NULL, NULL);
        INSERT INTO agents VALUES ('a2', 'backend', 'p1', 'backend', 'running', 'working', 'a1', 4, 9, NULL, 'hi',
            NULL, NULL, 'opencode', NULL);
        INSERT INTO agents VALUES ('a3', 'gone', 'p1', 'x', 'stopped', NULL, NULL, 0, 0, NULL, NULL, NULL, NULL, NULL,
            '2026-10-19');
        INSERT INTO agents VALUES ('a4', 'elsewhere', 'p2', 'x', 'running', NULL, NULL, 0, 0, NULL, NULL, NULL, NULL,
            NULL, NULL);
    """)
    db.commit()
    db.close()


def test_sync_dry_run_upserts_live_agents_and_prunes(tmp_path):
    home = tmp_path / "scion"
    _fake_hub(home)
    env = {k: v for k, v in os.environ.items()
           if k not in ("HUB_PROJECT_ID", "SCION_GCP_PROJECT_ID", "HUB_PROJECT", "BILLING_PROJECT")}
    env.update({"SCION_HOME": str(home), "FACTORY_PROJECTS": "demo", "SANDBOX_PROJECT": "demo-proj",
                "HOME": str(tmp_path)})
    proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "sync-factory-agents.py"), "--dry-run"],
                          capture_output=True, text=True, env=env, timeout=30)
    assert proc.returncode == 0, proc.stderr
    requests = [json.loads(line) for line in proc.stdout.splitlines() if line.startswith("{")]
    commit = next(r for r in requests if r["url"].endswith(":commit"))
    names = sorted(w["update"]["name"].rsplit("/", 1)[1] for w in commit["body"]["writes"])
    assert names == ["demo__backend", "demo__lead"]
    lead = next(w for w in commit["body"]["writes"] if w["update"]["name"].endswith("demo__lead"))
    assert lead["update"]["fields"]["harness"] == {"stringValue": "claude"}
    assert lead["update"]["fields"]["current_turns"] == {"nullValue": None}  # unknown is not zero (L9)
    assert lead["update"]["fields"]["last_activity_at"] == {"timestampValue": "2026-10-20T12:00:00Z"}
    backend = next(w for w in commit["body"]["writes"] if w["update"]["name"].endswith("demo__backend"))
    assert backend["update"]["fields"]["parent_agent"] == {"stringValue": "lead"}
    prune = next(r for r in requests if r["url"].endswith(":runQuery"))
    where = prune["body"]["structuredQuery"]["where"]["fieldFilter"]
    assert where["field"]["fieldPath"] == "synced_at" and where["op"] == "LESS_THAN"
    assert "spanner" not in proc.stdout.lower()


# --- Compiled kit ----------------------------------------------------------


def test_compiled_starter_kit_is_serverless_firestore(tmp_path):
    spec = scion_factory.load_spec(ROOT / "presets" / "starter-service.factory.yaml")
    out = tmp_path / "kit"
    scion_factory.compile_factory_boilerplate(spec, out)

    assert not (out / "config" / "spanner-factory-ddl.sql").exists()
    contract = out / "config" / "firestore" / "README.md"
    assert contract.exists() and "factory_agent_status" in contract.read_text() and "eval_runs" in contract.read_text()
    indexes = json.loads((out / "config" / "firestore" / "firestore.indexes.json").read_text())
    groups = {i["collectionGroup"] for i in indexes["indexes"]}
    assert groups == {"factory_agent_status", "eval_runs"}
    assert "if false" in (out / "config" / "firestore" / "firestore.rules").read_text()

    tool = out / "eval-workspace" / "tools" / "factory_store.py"
    assert tool.exists()
    assert "put-eval-run" in (out / "templates" / "domain-evaluator" / "system-prompt.md").read_text()

    offenders = [str(p.relative_to(out)) for p in out.rglob("*") if p.is_file()
                 and "spanner" in p.read_text(errors="ignore").lower()]
    assert offenders == [], f"serverless starter kit must not mention Spanner: {offenders}"


def test_cloud_store_question_is_serverless_first():
    q = next(q for q in scion_factory.get_grill_questions() if q["id"] == "cloud_stores_and_deployer_iam")
    text = (q["question"] + q["challenge_prompt"]).lower()
    assert "serverless" in text and "firestore" in text


def test_repo_firestore_config_matches_compiler_and_spanner_ddl_is_gone():
    indexes = json.loads((ROOT / "config" / "firestore" / "firestore.indexes.json").read_text())
    assert indexes == scion_factory.FIRESTORE_INDEXES
    assert (ROOT / "config" / "firestore" / "firestore.rules").read_text() == scion_factory.FIRESTORE_RULES
    assert not (ROOT / "config" / "spanner-factory-ddl.sql").exists()
    # setup-sandbox.sh creates the same composite indexes.
    setup = (ROOT / "scripts" / "setup-sandbox.sh").read_text()
    for index in indexes["indexes"]:
        assert f"create_index {index['collectionGroup']}" in setup
        for field in index["fields"]:
            assert f"field-path={field['fieldPath']},order={field['order'].lower()}" in setup


def test_control_plane_scripts_do_not_use_spanner():
    for rel in ("scripts/sync-factory-agents.py", "scripts/install-sync-cron.sh", "scripts/factory_store.py"):
        assert "spanner" not in (ROOT / rel).read_text().lower(), rel


def test_emulator_host_redirects_requests(monkeypatch, capsys):
    monkeypatch.setenv("FIRESTORE_EMULATOR_HOST", "localhost:8085")
    store = factory_store.FirestoreStore(project="demo-proj", dry_run=True)
    store.commit([{"delete": store.doc_name("eval_runs", "x")}])
    req = json.loads(capsys.readouterr().out)
    assert req["url"] == "http://localhost:8085/v1/projects/demo-proj/databases/(default)/documents:commit"
