"""Tests for scripts/discovery.py."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import discovery  # noqa: E402
import scion_factory  # noqa: E402


def test_auth_code_proposes_a_security_auditor(tmp_path):
    (tmp_path / "pkg" / "auth").mkdir(parents=True)
    (tmp_path / "pkg" / "auth" / "token.go").write_text("package auth\n")
    got = {r["role"]: r for r in discovery.discover_roles(tmp_path)}
    assert "security-auditor" in got and any("pkg/auth" in e for e in got["security-auditor"]["evidence"])


def test_plain_repo_proposes_nothing_needing_a_service_when_deploy_is_none(tmp_path):
    (tmp_path / "api.proto").write_text("syntax = \"proto3\";\n")
    roles = {r["role"] for r in discovery.discover_roles(tmp_path, deploy="none")}
    assert "contract-tester" not in roles


def test_service_repo_with_a_proto_gets_a_contract_tester(tmp_path):
    (tmp_path / "api.proto").write_text("syntax = \"proto3\";\n")
    assert "contract-tester" in {r["role"] for r in discovery.discover_roles(tmp_path)}


def test_go_library_without_service_gets_api_compat_and_benchmarks(tmp_path):
    (tmp_path / "go.mod").write_text("module example.com/lib\n")
    (tmp_path / "lib_test.go").write_text("package lib\nfunc BenchmarkX(b *testing.B) {}\n")
    roles = {r["role"] for r in discovery.discover_roles(tmp_path, deploy="none")}
    assert {"api-compat-checker", "benchmark-runner", "performance-analyst"} <= roles


def test_monorepo_reads_only_the_product_folder(tmp_path):
    (tmp_path / "CHANGELOG.md").write_text("x")
    (tmp_path / "pkg" / "kubelet").mkdir(parents=True)
    roles = {r["role"] for r in discovery.discover_roles(tmp_path, subdir="pkg/kubelet")}
    assert "release-manager" not in roles and "factory-analyst" in roles


def test_walk_is_capped_and_skips_vendor(tmp_path):
    (tmp_path / "vendor" / "x").mkdir(parents=True)
    (tmp_path / "vendor" / "x" / "auth.go").write_text("")
    for i in range(30):
        (tmp_path / f"f{i}.txt").write_text("")
    files = discovery.walk_files(tmp_path, max_files=10)
    assert len(files) == 10 and not any(f.startswith("vendor/") for f in files)


def test_empty_and_missing_dirs_never_raise(tmp_path):
    assert discovery.discover_roles(tmp_path) == []
    assert discovery.discover_roles(tmp_path / "nope") == []


def test_labels_add_evidence(tmp_path):
    got = {r["role"]: r for r in discovery.discover_roles(tmp_path, labels=["security", "flaky"])}
    assert "label: security" in got["security-auditor"]["evidence"]
    assert "test-engineer" in got


def test_inspect_repository_returns_proposed_roles(tmp_path):
    (tmp_path / "SECURITY.md").write_text("x")
    assert any(
        r["role"] == "security-auditor"
        for r in scion_factory.inspect_repository(tmp_path)["detected"]["proposed_roles"]
    )


def test_frontend_and_ui_auditor_signals(tmp_path):
    (tmp_path / "package.json").write_text('{"dependencies": {"react": "^18.0.0"}}')
    roles = {r["role"] for r in discovery.discover_roles(tmp_path, deploy="service")}
    assert "frontend" in roles
    assert "ui-auditor" in roles

    roles_none = {r["role"] for r in discovery.discover_roles(tmp_path, deploy="none")}
    assert "frontend" in roles_none
    assert "ui-auditor" not in roles_none


def test_migration_and_safety_evaluator_signals(tmp_path):
    (tmp_path / "migrations").mkdir()
    (tmp_path / "migrations" / "001_init.sql").write_text("CREATE TABLE users (id INT);")
    (tmp_path / "app.py").write_text("import openai\nclient = openai.OpenAI()\n")
    roles = {r["role"] for r in discovery.discover_roles(tmp_path)}
    assert "migration-specialist" in roles
    assert "safety-evaluator" in roles


def test_discovered_roles_are_sorted_and_capped(tmp_path):
    (tmp_path / "SECURITY.md").write_text("x")
    (tmp_path / "auth1.go").write_text("")
    (tmp_path / "auth2.go").write_text("")
    (tmp_path / "auth3.go").write_text("")
    (tmp_path / "api.proto").write_text("syntax = 'proto3';")
    discovered = discovery.discover_roles(tmp_path)
    teams = [r["team"] for r in discovered]
    assert teams == sorted(teams)
    for r in discovered:
        assert len(r["evidence"]) <= 3
