"""Clean-room reproducibility and operator preflight (`scion-factory doctor`) tests.

Proves that an external operator on a clean machine with isolated HOME/TMPDIR/PATH
can run `doctor`, `inspect -> grill -> add-role -> check -> compile -> verify-factory.sh -> kp-check.py`
against unseeded repositories without hidden author state.
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import factory_doctor  # noqa: E402


def _make_fake_bin(bin_dir: Path, name: str, script_body: str) -> Path:
    path = bin_dir / name
    path.write_text("#!/bin/sh\n" + script_body + "\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def _seed_clean_gke_toolchain(bin_dir: Path, *, terraform_ver: str = "1.9.8", include_gke_plugin: bool = True,
                              gcloud_authed: bool = True, gh_authed: bool = True) -> None:
    _make_fake_bin(bin_dir, "python3", 'echo "Python 3.12.3"')
    _make_fake_bin(bin_dir, "git", 'echo "git version 2.45.1"')
    _make_fake_bin(bin_dir, "uv", 'echo "uv 0.5.14"')
    _make_fake_bin(bin_dir, "jq", 'echo "jq-1.7.1"')
    _make_fake_bin(bin_dir, "openssl", 'echo "OpenSSL 3.0.13 30 Jan 2024"')
    _make_fake_bin(bin_dir, "terraform", f'echo "Terraform v{terraform_ver}"')
    _make_fake_bin(bin_dir, "kubectl", 'echo "Client Version: v1.31.2"')
    if include_gke_plugin:
        _make_fake_bin(bin_dir, "gke-gcloud-auth-plugin", 'echo "Kubernetes v1.31.0-gke"')
    if gcloud_authed:
        _make_fake_bin(
            bin_dir,
            "gcloud",
            'if [ "$1" = "version" ]; then echo "Google Cloud SDK 502.0.0"; exit 0; fi\n'
            'echo "operator@example.com"\n',
        )
    else:
        _make_fake_bin(
            bin_dir,
            "gcloud",
            'if [ "$1" = "version" ]; then echo "Google Cloud SDK 502.0.0"; exit 0; fi\n'
            'exit 0\n',
        )
    if gh_authed:
        _make_fake_bin(
            bin_dir,
            "gh",
            'if [ "$1" = "--version" ]; then echo "gh version 2.63.0"; exit 0; fi\n'
            'echo "Logged in to github.com"\n'
            'echo "  - Token scopes: repo, workflow"\n',
        )
    else:
        _make_fake_bin(
            bin_dir,
            "gh",
            'if [ "$1" = "--version" ]; then echo "gh version 2.63.0"; exit 0; fi\n'
            'echo "You are not logged into any GitHub hosts." >&2; exit 1\n',
        )


def test_doctor_passes_fast_on_valid_gke_toolchain(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _seed_clean_gke_toolchain(bin_dir)

    t0 = time.monotonic()
    report = factory_doctor.run_doctor(mode="gke", env={"PATH": str(bin_dir), "HOME": str(tmp_path)})
    elapsed = time.monotonic() - t0

    assert report["ok"] is True, report
    assert report["fail_count"] == 0
    assert elapsed < 5.0
    assert "ok=True" in factory_doctor.format_report(report)


def test_doctor_isolates_kubectl_kubeconfig_to_devnull(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _seed_clean_gke_toolchain(bin_dir)
    # Fail loudly if KUBECONFIG is not isolated to os.devnull (prevents kubectl-dispatcher hangs).
    _make_fake_bin(
        bin_dir,
        "kubectl",
        'if [ "${KUBECONFIG:-}" != "/dev/null" ]; then echo "unexpected KUBECONFIG=${KUBECONFIG:-}" >&2; exit 1; fi\n'
        'echo "Client Version: v1.31.2"\n',
    )

    report = factory_doctor.run_doctor(
        mode="gke",
        skip_auth=True,
        env={"PATH": str(bin_dir), "HOME": str(tmp_path), "KUBECONFIG": "/tmp/stale-dead-cluster.yaml"},
    )
    assert report["ok"] is True, report
    kubectl_check = next(c for c in report["checks"] if c["name"] == "kubectl")
    assert kubectl_check["status"] == "ok" and kubectl_check["version"] == "1.31.2"


def test_doctor_flags_missing_gke_plugin_outdated_terraform_and_missing_auth(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _seed_clean_gke_toolchain(
        bin_dir,
        terraform_ver="1.6.4",
        include_gke_plugin=False,
        gcloud_authed=False,
        gh_authed=False,
    )

    t0 = time.monotonic()
    report = factory_doctor.run_doctor(mode="gke", env={"PATH": str(bin_dir), "HOME": str(tmp_path)})
    elapsed = time.monotonic() - t0

    assert elapsed < 5.0
    assert report["ok"] is False
    by_name = {c["name"]: c for c in report["checks"]}

    assert by_name["gke-gcloud-auth-plugin"]["status"] == "fail"
    assert "gcloud components install gke-gcloud-auth-plugin" in by_name["gke-gcloud-auth-plugin"]["fix"]

    assert by_name["terraform"]["status"] == "fail"
    assert "1.6.4" in by_name["terraform"]["detail"]
    assert "1.9" in by_name["terraform"]["fix"]

    assert by_name["gcloud-auth"]["status"] == "fail"
    assert "gcloud auth login" in by_name["gcloud-auth"]["fix"]

    assert by_name["gh-auth"]["status"] == "fail"
    assert "gh auth login" in by_name["gh-auth"]["fix"]


def test_doctor_never_leaks_github_token_in_output(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _seed_clean_gke_toolchain(bin_dir, gh_authed=False)
    synthetic_secret = "token_" + "NeverPrint123456"

    report = factory_doctor.run_doctor(
        mode="gke",
        env={"PATH": str(bin_dir), "HOME": str(tmp_path), "GITHUB_TOKEN": synthetic_secret},
    )
    assert report["ok"] is True
    rendered = json.dumps(report) + "\n" + factory_doctor.format_report(report)
    assert synthetic_secret not in rendered


def test_doctor_cli_subcommand_local_mode_works():
    res = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "scion_factory.py"), "doctor", "--mode", "local", "--skip-auth", "--json"],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, res.stderr
    payload = json.loads(res.stdout)
    assert payload["mode"] == "local"
    assert payload["ok"] is True


def test_hermetic_clean_room_bootstrap_without_presets(tmp_path):
    """Run inspect -> grill -> add-role -> check -> compile -> verify-factory.sh -> kp-check.py
    under an isolated HOME/TMPDIR environment against an unseeded synthetic Python repo."""
    home = tmp_path / "clean-home"
    home.mkdir()
    tmpdir = tmp_path / "clean-tmp"
    tmpdir.mkdir()
    repo = tmp_path / "sample-service"
    repo.mkdir()
    (repo / "requirements.txt").write_text("fastapi==0.115.0\npytest==8.3.0\n")
    (repo / "app.py").write_text("def health():\n    return {'status': 'ok'}\n")

    hermetic_env = {
        "HOME": str(home),
        "TMPDIR": str(tmpdir),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LANG": "C.UTF-8",
    }

    r_inspect = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "scion_factory.py"), "inspect", str(repo), "--json"],
        env=hermetic_env,
        capture_output=True,
        text=True,
    )
    assert r_inspect.returncode == 0, r_inspect.stderr
    inspected = json.loads(r_inspect.stdout)
    assert inspected["detected"]["primary_language"] == "python"

    spec_path = tmp_path / "factory.yaml"
    r_grill = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "scion_factory.py"),
            "grill",
            "--repo",
            str(repo),
            "--out",
            str(spec_path),
            "--json",
        ],
        env=hermetic_env,
        capture_output=True,
        text=True,
    )
    assert r_grill.returncode == 0, r_grill.stderr
    assert spec_path.is_file()

    # Fill in testable product-intent answers (as the coding agent does during the interview)
    spec = yaml.safe_load(spec_path.read_text())
    spec["project"]["name"] = "Clean Room Sample Service"
    spec["project"]["slug"] = "sample-svc"
    spec["project"]["eval_slug"] = "sample-svc-eval"
    spec["project"]["mission"] = "Serve deterministic health and status endpoints for clean-room verification."
    spec["project"]["non_goals"] = ["Billing", "User authentication"]
    spec["vcs"]["enabled"] = False
    spec["infrastructure"]["deploy"] = "none"
    spec["infrastructure"]["cloud_stores"] = ["firestore"]
    spec["infrastructure"]["budget_usd"] = 50
    spec["team"]["verification_policy"] = "single-harness"
    spec["team"]["single_harness_reason"] = "Clean-room smoke test uses default harness mix."
    spec["team"]["build_roles"] = [r for r in spec["team"]["build_roles"] if r["role"] != "deployer"]
    spec["team"]["eval_roles"] = [r for r in spec["team"]["eval_roles"] if r["role"] != "eval-deployer"]
    for r in spec["team"]["build_roles"] + spec["team"]["eval_roles"]:
        if not str(r.get("responsibility") or "").strip() or "TODO" in str(r.get("responsibility") or ""):
            r["responsibility"] = f"Concrete responsibility for {r['role']} in clean-room verification."
    spec["knowledge_plane"]["requirements"] = [
        {
            "id": "REQ-001",
            "title": "Health endpoint",
            "acceptance": ["health() returns status ok with HTTP 200"],
        }
    ]
    spec["knowledge_plane"]["invariants"] = ["All public handlers return JSON dictionaries."]
    spec["evaluation_pyramid"]["l2_contract"] = {
        "endpoints": ["GET /health"],
        "invariants": ["Returns JSON object containing status=ok"],
    }
    spec["evaluation_pyramid"]["l3_domain_quality"] = {
        "primary_metric": "pass_rate",
        "target_threshold": 1.0,
        "probe_description": "Run pytest suite against app.py and assert 100% pass rate.",
    }
    spec["evaluation_pyramid"]["l4_safety_grounding"] = {
        "invariants": ["No secrets printed in error responses"],
    }
    spec["waves"] = [
        {
            "wave": 1,
            "title": "Core health API",
            "issues": [
                {
                    "title": "Add unit tests for health endpoint",
                    "owner_role": "backend",
                    "acceptance_criteria": ["pytest -q passes with 100% coverage of health()"],
                    "requirements": ["REQ-001"],
                }
            ],
        }
    ]
    spec_path.write_text(yaml.safe_dump(spec, sort_keys=False))

    r_add = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "scion_factory.py"),
            "add-role",
            str(spec_path),
            "--role",
            "test-engineer",
            "--evidence",
            "sample repo lacks tests",
        ],
        env=hermetic_env,
        capture_output=True,
        text=True,
    )
    assert r_add.returncode == 0, r_add.stderr

    r_check = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "scion_factory.py"), "check", str(spec_path), "--json"],
        env=hermetic_env,
        capture_output=True,
        text=True,
    )
    assert r_check.returncode == 0, r_check.stdout + r_check.stderr
    assert json.loads(r_check.stdout)["ready"] is True

    kit_dir = tmp_path / "kit"
    r_compile = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "scion_factory.py"), "compile", str(spec_path), "--out", str(kit_dir)],
        env=hermetic_env,
        capture_output=True,
        text=True,
    )
    assert r_compile.returncode == 0, r_compile.stderr

    r_verify = subprocess.run(
        ["bash", str(kit_dir / "scripts" / "verify-factory.sh")],
        env=hermetic_env,
        capture_output=True,
        text=True,
    )
    assert r_verify.returncode == 0, r_verify.stdout + r_verify.stderr

    r_kp = subprocess.run(
        [sys.executable, str(kit_dir / "knowledge-plane" / "kp-check.py")],
        env=hermetic_env,
        capture_output=True,
        text=True,
    )
    assert r_kp.returncode == 0, r_kp.stdout + r_kp.stderr


@pytest.mark.parametrize(
    "preset_name",
    [p.name for p in sorted((ROOT / "presets").glob("*.factory.yaml"))],
)
def test_every_preset_passes_check_compile_and_verify(preset_name, tmp_path):
    preset = ROOT / "presets" / preset_name
    out = tmp_path / preset.stem

    r_check = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "scion_factory.py"), "check", str(preset)],
        capture_output=True,
        text=True,
    )
    assert r_check.returncode == 0, r_check.stdout + r_check.stderr

    r_compile = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "scion_factory.py"), "compile", str(preset), "--out", str(out)],
        capture_output=True,
        text=True,
    )
    assert r_compile.returncode == 0, r_compile.stderr

    r_verify = subprocess.run(
        ["bash", str(out / "scripts" / "verify-factory.sh")],
        capture_output=True,
        text=True,
    )
    assert r_verify.returncode == 0, r_verify.stdout + r_verify.stderr
