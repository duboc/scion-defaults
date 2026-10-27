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


def test_check_po_inbox_ignores_state_change_and_escalates_only_instructions(watchdog):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE users (id TEXT, email TEXT, role TEXT, created TEXT)")
    conn.execute(
        "CREATE TABLE messages (id TEXT, project_id TEXT, sender TEXT, sender_id TEXT, "
        "recipient TEXT, recipient_id TEXT, msg TEXT, type TEXT, created TEXT)"
    )
    conn.execute("INSERT INTO users VALUES ('u1', 'po@example.com', 'admin', '2026-10-26T00:00:00Z')")
    conn.execute(
        "INSERT INTO messages VALUES ('m1', 'p1', 'agent:lead', 'a1', 'user:u1', 'u1', "
        "'lead: PO decision needed: #327', 'instruction', '2026-10-26T20:57:28Z')"
    )
    conn.execute(
        "INSERT INTO messages VALUES ('m2', 'p1', 'agent:lead', 'a1', 'user:u1', 'u1', "
        "'lead has reached a state of COMPLETED: Wake-up done', 'state-change', '2026-10-26T20:57:42Z')"
    )
    conn.execute(
        "INSERT INTO messages VALUES ('m3', 'p2', 'agent:eval-lead', 'a2', 'user:u1', 'u1', "
        "'eval-lead has STALLED (was working): Harness started', 'state-change', '2026-10-26T21:04:47Z')"
    )
    conn.commit()

    alerts = []
    watchdog.log_alert = lambda event_type, payload, severity="ERROR": alerts.append((event_type, payload, severity))

    state = {"last_user_msg_ts": "2026-10-26T20:00:00Z"}
    watchdog.check_po_inbox(conn, state, apply=True)

    assert len(alerts) == 1
    assert alerts[0][0] == "PO_ESCALATION"
    assert alerts[0][1]["message_id"] == "m1"
    assert alerts[0][1]["type"] == "instruction"
    # Bookmark advances past the trailing state-change rows too so they aren't re-scanned forever.
    assert state["last_user_msg_ts"] == "2026-10-26T21:04:47Z"


def test_find_hung_subprocesses_identifies_old_non_harness_child_commands(watchdog):
    sample_ps = """  PID ELAPSED COMMAND         COMMAND
    1    1800 sciontool       sciontool init -- sh -c tmux new-session -d -s scion
   39    1799 sh              sh -c tmux new-session -d -s scion
   40    1799 tmux: client    tmux new-session -d -s scion
   42    1799 tmux: server    tmux new-session -d -s scion
   43    1799 sh              sh -c '/home/scion/.scion/harness/agy-wrapper.sh' '--model' 'gemini 4 argon (high)'
   44    1799 zsh             -zsh
   45    1798 agy             agy --dangerously-skip-permissions --model gemini 4 argon (high) -prompt-interactive Read
  506     912 python3         /tmp/venv/bin/python3 -c import asyncio, socket, sys; uvicorn.Server(config)
  590      45 pytest          /tmp/venv/bin/pytest -q
  655       0 ps              ps -eo pid,etimes,comm,args
"""
    hung = watchdog.find_hung_subprocesses(sample_ps, max_age_sec=600)
    assert len(hung) == 1
    assert hung[0]["pid"] == 506
    assert hung[0]["etimes"] == 912
    assert "uvicorn.Server" in hung[0]["args"]


def test_check_agent_liveness_reaps_hung_subprocess_and_notifies_worker_and_lead(watchdog, monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE projects (id TEXT, slug TEXT)")
    conn.execute("CREATE TABLE agents (id TEXT, project_id TEXT, slug TEXT, name TEXT, phase TEXT, status TEXT, template TEXT, deleted_at TEXT)")
    conn.execute("INSERT INTO projects VALUES ('p1', 'bananacast')")
    conn.execute("INSERT INTO agents VALUES ('a1', 'p1', 'lead', 'lead', 'running', '', 'lead', NULL)")
    conn.execute("INSERT INTO agents VALUES ('a2', 'p1', 'investigator', 'investigator', 'running', '', 'investigator', NULL)")
    conn.commit()

    monkeypatch.setattr(watchdog, "FACTORY_PROJECTS", ["bananacast"])
    monkeypatch.setattr(
        watchdog,
        "get_docker_containers",
        lambda: {"bananacast--lead": "Up 20 minutes", "bananacast--investigator": "Up 20 minutes"},
    )
    monkeypatch.setattr(
        watchdog,
        "list_container_hung_subprocesses",
        lambda cname, max_age_sec: (
            [{"pid": 506, "etimes": 912, "comm": "python3", "args": "/tmp/venv/bin/python3 -c import asyncio, socket"}]
            if cname == "bananacast--investigator"
            else []
        ),
    )
    killed = []
    monkeypatch.setattr(watchdog, "kill_container_subprocess", lambda cname, pid: killed.append((cname, pid)) or True)
    messages = []
    monkeypatch.setattr(watchdog, "api", lambda method, path, body=None: (messages.append((method, path, body)) or (200, {})))
    monkeypatch.setattr(watchdog, "log_alert", lambda *a, **kw: None)

    watchdog.check_agent_liveness(conn, {}, apply=True)

    assert killed == [("bananacast--investigator", 506)]
    paths = [m[1] for m in messages]
    assert "/api/v1/projects/p1/agents/investigator/message" in paths
    assert "/api/v1/projects/p1/agents/lead/message" in paths


def test_load_runtime_env_prefers_cli_then_watchdog_then_sync(watchdog, tmp_path):
    w_env = tmp_path / "watchdog.env"
    s_env = tmp_path / "sync.env"
    w_env.write_text("FACTORY_PROJECTS=from-watchdog\nHUB_PROJECT_ID=watchdog-proj\n")
    s_env.write_text("FACTORY_PROJECTS=from-sync\nHUB_PROJECT_ID=sync-proj\nADMIN_EMAIL=po@example.com\n")

    env = {"FACTORY_PROJECTS": "from-cli"}
    watchdog.load_runtime_env(str(w_env), str(s_env), env)

    assert env["FACTORY_PROJECTS"] == "from-cli"
    assert env["HUB_PROJECT_ID"] == "watchdog-proj"
    assert env["ADMIN_EMAIL"] == "po@example.com"


def test_check_heartbeat_schedules_guards_lead_and_eval_sweep_in_factory_projects_only(watchdog, monkeypatch, capsys):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE projects (id TEXT, slug TEXT)")
    conn.execute(
        "CREATE TABLE schedules (id TEXT, project_id TEXT, name TEXT, cron_expr TEXT, status TEXT, "
        "last_run_at TEXT, last_run_status TEXT, run_count INTEGER, error_count INTEGER)"
    )
    conn.execute("INSERT INTO projects VALUES ('p1', 'proj1')")
    conn.execute("INSERT INTO projects VALUES ('p2', 'proj1-eval')")
    conn.execute("INSERT INTO projects VALUES ('p3', 'other-proj')")
    conn.execute("INSERT INTO schedules VALUES ('s1', 'p1', 'proj1-lead-heartbeat', '*/15 * * * *', 'active', NULL, NULL, 4, 0)")
    conn.execute("INSERT INTO schedules VALUES ('s2', 'p2', 'eval-sweep-factory-main', '*/5 * * * *', 'paused', NULL, NULL, 2, 0)")
    conn.execute("INSERT INTO schedules VALUES ('s3', 'p2', 'old-heartbeat', '*/15 * * * *', 'deleted', NULL, NULL, 1, 0)")
    conn.execute("INSERT INTO schedules VALUES ('s4', 'p3', 'other-lead-heartbeat', '*/15 * * * *', 'paused', NULL, NULL, 1, 0)")
    conn.commit()

    monkeypatch.setattr(watchdog, "FACTORY_PROJECTS", ["proj1", "proj1-eval"])
    resumed = []
    monkeypatch.setattr(watchdog, "api", lambda method, path, body=None: (resumed.append((method, path)) or (200, {})))
    monkeypatch.setattr(watchdog, "log_alert", lambda *a, **kw: None)

    ok = watchdog.check_heartbeat_schedules(conn, apply=True)
    out = capsys.readouterr().out

    assert ok is False
    assert resumed == [("POST", "/api/v1/projects/p2/schedules/s2/resume")]
    assert "eval-sweep-factory-main" in out
    assert "other-lead-heartbeat" not in out
    assert "old-heartbeat" not in out


