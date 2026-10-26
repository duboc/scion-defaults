import importlib.util
import os
import sqlite3
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def watchdog(tmp_path):
    os.environ.setdefault("SCION_HOME", str(tmp_path / "scion-home"))
    return _load("factory_watchdog_wake", ROOT / "scripts" / "factory-watchdog.py")


def test_watchdog_wakes_suspended_leads_only(watchdog):
    assert watchdog.should_wake("kubelet-lead", "lead", {"lead", "eval-lead"})
    assert not watchdog.should_wake("kubelet-researcher", "researcher", {"lead", "eval-lead"})


def test_should_wake_slug_fallback_when_template_empty(watchdog):
    wake_roles = {"lead", "eval-lead"}
    # empty template + slug "kubelet-lead" → woken
    assert watchdog.should_wake("kubelet-lead", "", wake_roles)
    assert watchdog.should_wake("lead", "", wake_roles)
    assert watchdog.should_wake("kubelet-eval-lead", None, wake_roles)
    # empty template + slug "kubelet-researcher" → not
    assert not watchdog.should_wake("kubelet-researcher", "", wake_roles)
    # non-empty template still decides (template "researcher" with slug "x-lead" → not woken)
    assert not watchdog.should_wake("x-lead", "researcher", wake_roles)


def test_wake_roles_come_from_env(watchdog, monkeypatch):
    assert watchdog.default_wake_roles({}) == {"lead", "eval-lead"}
    monkeypatch.setenv("WATCHDOG_WAKE_ROLES", "lead,eval-lead,backend")
    roles = watchdog.default_wake_roles(os.environ)
    assert roles == {"lead", "eval-lead", "backend"}
    assert watchdog.should_wake("kubelet-backend", "backend", roles)
    assert not watchdog.should_wake("kubelet-researcher", "researcher", roles)


def test_check_agent_liveness_leaves_idle_specialist_asleep(watchdog, monkeypatch, capsys):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE projects (id TEXT, slug TEXT)")
    conn.execute("CREATE TABLE agents (id TEXT, project_id TEXT, slug TEXT, name TEXT, phase TEXT, status TEXT, template TEXT, deleted_at TEXT)")
    conn.execute("INSERT INTO projects VALUES ('p1', 'proj1')")
    conn.execute("INSERT INTO agents VALUES ('a1', 'p1', 'kubelet-lead', 'Lead', 'suspended', '', 'lead', NULL)")
    conn.execute("INSERT INTO agents VALUES ('a2', 'p1', 'kubelet-backend', 'Backend', 'suspended', '', 'backend', NULL)")
    conn.commit()

    monkeypatch.setattr(watchdog, "FACTORY_PROJECTS", ["proj1"])
    monkeypatch.setattr(watchdog, "get_docker_containers", lambda: {})

    watchdog.check_agent_liveness(conn, {}, apply=False)
    out = capsys.readouterr().out

    assert "Agent 'proj1/kubelet-lead' is SUSPENDED" in out
    assert "(dry-run: would wake agent via API)" in out
    assert "Agent 'proj1/kubelet-backend' is SUSPENDED" in out
    assert "left asleep (lead wakes it with a brief)" in out
