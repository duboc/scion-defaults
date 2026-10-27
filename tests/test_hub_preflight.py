"""Preflight for an existing Scion Hub (README: "Use an existing Scion Hub").

scripts/hub-preflight.py runs read-only on the Hub VM and reports what the deploy steps need: Hub health, the PO's
admin user, harness images, existing projects, and the factory lists the watchdog and the status sync already cover.
"""
import importlib.util
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location("hub_preflight", ROOT / "scripts" / "hub-preflight.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pre = _load()


def test_merge_keeps_the_factories_already_covered():
    """Installing the watchdog or the sync rewrites FACTORY_PROJECTS: other factories on the Hub must stay in it."""
    assert pre.merge_projects(["shop", "shop-eval"], ["ledger", "ledger-eval", "shop"]) == [
        "shop", "shop-eval", "ledger", "ledger-eval"]
    assert pre.merge_projects([], ["ledger"]) == ["ledger"]


def test_parse_env_reads_key_values():
    assert pre.parse_env("# c\nFACTORY_PROJECTS='a,b'\nexport X=1\n\nPATH=/bin\n") == {
        "FACTORY_PROJECTS": "a,b", "X": "1", "PATH": "/bin"}


def test_missing_images():
    present = {"localhost/scion/scion-antigravity:latest", "scion-antigravity:latest", "scion-claude:latest"}
    assert pre.missing_images(present, ["claude", "antigravity", "opencode"]) == ["opencode"]


def _fake_scion_home(tmp_path, users=(("po@example.com", "admin"),), projects=("shop", "shop-eval")):
    home = tmp_path / ".scion"
    home.mkdir()
    conn = sqlite3.connect(home / "hub.db")
    conn.execute("CREATE TABLE users (id TEXT, email TEXT, role TEXT)")
    conn.execute("CREATE TABLE projects (id TEXT, slug TEXT)")
    for i, (email, role) in enumerate(users):
        conn.execute("INSERT INTO users VALUES (?, ?, ?)", (f"u{i}", email, role))
    for i, slug in enumerate(projects):
        conn.execute("INSERT INTO projects VALUES (?, ?)", (f"p{i}", slug))
    conn.commit()
    conn.close()
    (home / "watchdog.env").write_text("FACTORY_PROJECTS=shop,shop-eval\nPATH=/bin\n")
    (home / "harness-configs" / "claude").mkdir(parents=True)
    (home / "harness-configs" / "claude-factory").mkdir(parents=True)
    return home


def _report(home, **overrides):
    facts = {"scion_home": str(home), "health": {"status": "healthy", "scionVersion": "nightly-x"},
             "images": {"scion-claude:latest", "scion-antigravity:latest"}, "sync_env": "",
             "po_email": "po@example.com", "new_projects": ["ledger", "ledger-eval"]}
    facts.update(overrides)
    return pre.build_report(**facts)


def test_report_for_a_healthy_hub(tmp_path):
    report = _report(_fake_scion_home(tmp_path))
    assert report["ok"] is True
    assert report["factory_projects"] == "shop,shop-eval,ledger,ledger-eval"
    assert report["existing_projects"] == ["shop", "shop-eval"]
    assert report["harness_configs"] == ["claude", "claude-factory"]
    assert any("opencode" in w for w in report["warnings"])


def test_report_fails_when_the_po_never_signed_in(tmp_path):
    report = _report(_fake_scion_home(tmp_path, users=(("someone@example.com", "admin"),)))
    assert report["ok"] is False
    assert any("po@example.com" in e and "sign in" in e for e in report["errors"])


def test_report_fails_when_the_po_is_not_an_admin(tmp_path):
    report = _report(_fake_scion_home(tmp_path, users=(("po@example.com", "member"),)))
    assert report["ok"] is False
    assert any("admin" in e for e in report["errors"])


def test_report_fails_on_an_unhealthy_hub(tmp_path):
    report = _report(_fake_scion_home(tmp_path), health=None)
    assert report["ok"] is False


def test_report_warns_when_a_factory_slug_is_taken(tmp_path):
    report = _report(_fake_scion_home(tmp_path), new_projects=["shop", "shop-eval"])
    assert any("shop" in w and "exists" in w for w in report["warnings"])


def test_report_warns_when_github_token_file_is_missing(tmp_path):
    missing = tmp_path / "missing-token"
    empty = tmp_path / "empty-token"
    empty.write_text("")
    populated = tmp_path / "token"
    populated.write_text("fake-token-bytes-not-a-pat\n")

    assert pre._has_github_token(str(missing)) is False
    assert pre._has_github_token(str(empty)) is False
    assert pre._has_github_token(str(populated)) is True

    report = _report(_fake_scion_home(tmp_path), github_token_present=False)
    assert report["ok"] is True
    assert report["github_token_present"] is False
    assert any(".factory-github-token" in w and "workflow" in w for w in report["warnings"])
    assert "fake-token-bytes-not-a-pat" not in json.dumps(report)


def test_cli_prints_json_and_never_writes(tmp_path):
    home = _fake_scion_home(tmp_path)
    before = sorted(p.as_posix() for p in home.rglob("*"))
    res = subprocess.run([sys.executable, str(ROOT / "scripts" / "hub-preflight.py"), "--json", "--scion-home",
                          str(home), "--skip-health", "--skip-docker"],
                         env={"PATH": "/usr/bin:/bin", "PO_EMAIL": "po@example.com",
                              "BUILD_PROJECT": "ledger", "EVAL_PROJECT": "ledger-eval"},
                         capture_output=True, text=True)
    report = json.loads(res.stdout)
    assert report["factory_projects"] == "shop,shop-eval,ledger,ledger-eval"
    assert sorted(p.as_posix() for p in home.rglob("*")) == before


def test_run_on_hub_lets_the_command_line_override_factory_env(tmp_path):
    """Full e2e: `FACTORY_PROJECTS=... scripts/run-on-hub.sh scripts/factory-watchdog.py --install-cron` kept the
    env file's FACTORY_PROJECTS, because run-on-hub.sh sourced the file after the caller set the variable."""
    import os
    env_file = tmp_path / "factory.env"
    env_file.write_text("HUB_PROJECT=p\nHUB_ZONE=z\nHUB_VM=vm\nFACTORY_PROJECTS=a,a-eval\n"
                        "KNOWLEDGE_PLANE_SOURCE_PROJECT=a\n")
    fake = tmp_path / "bin" / "gcloud"
    fake.parent.mkdir()
    fake.write_text('#!/bin/sh\nfor a in "$@"; do echo "$a"; done\ncat >/dev/null\n')
    fake.chmod(0o755)
    script = tmp_path / "noop.py"
    script.write_text("print('hi')\n")
    res = subprocess.run(["bash", str(ROOT / "scripts" / "run-on-hub.sh"), str(script)],
                         env={**os.environ, "PATH": f"{fake.parent}:{os.environ['PATH']}", "FACTORY_ENV": str(env_file),
                              "FACTORY_PROJECTS": "a,a-eval,b,b-eval", "KNOWLEDGE_PLANE_SOURCES": "b=src/b/kp"},
                         capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    assert "FACTORY_PROJECTS=a\\,a-eval\\,b\\,b-eval" in res.stdout or "FACTORY_PROJECTS=a,a-eval,b,b-eval" in res.stdout
    assert "KNOWLEDGE_PLANE_SOURCES=b=src/b/kp" in res.stdout
    assert "KNOWLEDGE_PLANE_SOURCE_PROJECT=a" in res.stdout
