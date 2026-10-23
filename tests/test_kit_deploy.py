"""A compiled factory kit deploys with the same scripts as the reference factory (README end-to-end quickstart).

The kit's config/factory.env.example must use the variable names that run-on-hub.sh, render-templates.py and
push-templates.py read, and push-templates.py must take the kit's templates directory.
"""
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import scion_factory  # noqa: E402

SCRIPT_VARS = ("HUB_PROJECT HUB_ZONE HUB_VM HUB_OS_USER HUB_LOCAL_URL SANDBOX_PROJECT SANDBOX_REGION FIRESTORE_PROJECT "
               "FIRESTORE_DATABASE DEPLOYER_SA_NAME RUNTIME_SA_NAME GITHUB_REPO BUILD_PROJECT EVAL_PROJECT BUILD_BRANCH "
               "EVAL_BRANCH PO_EMAIL FACTORY_PROJECTS SERVING_SERVICE DASHBOARD_SERVICE").split()


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def kit(tmp_path_factory):
    out = tmp_path_factory.mktemp("kit")
    scion_factory.compile_factory_boilerplate(scion_factory.load_spec(ROOT / "presets" / "starter-service.factory.yaml"),
                                              out)
    return out


def _env_keys(text):
    keys = {}
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if "=" in line:
            key, value = line.split("=", 1)
            keys[key.strip()] = value.strip().strip('"')
    return keys


def test_kit_env_uses_the_script_variable_names(kit):
    keys = _env_keys((kit / "config" / "factory.env.example").read_text())
    assert set(SCRIPT_VARS) <= set(keys), sorted(set(SCRIPT_VARS) - set(keys))
    assert keys["BUILD_PROJECT"] == "pulse-ledger"
    assert keys["EVAL_PROJECT"] == "pulse-ledger-eval"
    assert keys["FACTORY_PROJECTS"] == "pulse-ledger,pulse-ledger-eval"


def test_kit_env_has_no_export_prefix(kit):
    """render-templates.py reads KEY=value lines; `export KEY=value` would silently drop every value."""
    lines = (kit / "config" / "factory.env.example").read_text().splitlines()
    assert [line for line in lines if line.startswith("export ")] == []


def test_kit_verify_script_reads_build_project(kit):
    text = (kit / "scripts" / "verify-factory.sh").read_text()
    assert "${BUILD_PROJECT:-pulse-ledger}" in text


def test_kit_templates_render_with_the_kit_env(kit, tmp_path):
    """The path push-templates.py takes: render-templates.py --src <kit>/templates with the kit's env file."""
    res = subprocess.run([sys.executable, str(ROOT / "scripts" / "render-templates.py"),
                          "--env", str(kit / "config" / "factory.env.example"), "--src", str(kit / "templates"),
                          "--out", str(tmp_path / "out")], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    assert (tmp_path / "out" / "domain-evaluator" / "system-prompt.md").exists()
    assert (tmp_path / "out" / "lead" / "agents.md").read_text() == (kit / "templates" / "lead" / "agents.md").read_text()


def test_push_templates_renders_from_a_kit_directory():
    push = _load("push_templates_kit", ROOT / "scripts" / "push-templates.py")
    cmd = push.render_command("/tmp/out", ["lead", "backend"], "/kits/ledger/templates")
    assert cmd[1:] == [str(ROOT / "scripts" / "render-templates.py"), "--out", "/tmp/out", "--roles", "lead,backend",
                       "--src", "/kits/ledger/templates"]
    assert "--src" not in push.render_command("/tmp/out", ["lead"], "")


def test_scion_admin_runs_the_scion_cli_as_the_admin(monkeypatch):
    """`scion-admin.py scion <args>` runs the scion CLI on the VM with the PO's admin token (never printed)."""
    admin = _load("scion_admin_kit", ROOT / "scripts" / "scion-admin.py")
    monkeypatch.setattr(admin, "_token", lambda: "minted")
    ran = {}
    monkeypatch.setattr(admin.subprocess, "run", lambda cmd, env: ran.update(cmd=cmd, env=env) or
                        type("R", (), {"returncode": 0})())
    with pytest.raises(SystemExit) as exit_info:
        admin.cmd_scion(type("A", (), {"args": ["start", "lead", "-t", "lead", "--project=ledger", "do it"]})())
    assert exit_info.value.code == 0
    assert ran["cmd"] == ["scion", "start", "lead", "-t", "lead", "--project=ledger", "do it"]
    assert ran["env"]["SCION_HUB_TOKEN"] == "minted"
    assert ran["env"]["SCION_HUB_ENDPOINT"] == admin.HUB_URL
