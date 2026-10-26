"""Tests for scripts/discovery.py."""
import json
import os
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import discovery  # noqa: E402
import role_library  # noqa: E402
import scion_factory  # noqa: E402


def test_auth_code_proposes_a_security_auditor(tmp_path):
    (tmp_path / "pkg" / "auth").mkdir(parents=True)
    (tmp_path / "pkg" / "auth" / "token.go").write_text("package auth\n")
    got = {r["role"]: r for r in discovery.discover_roles(tmp_path)}
    assert "security-auditor" in got and any("pkg/auth" in e for e in got["security-auditor"]["evidence"])


def test_authors_and_subdir_prefix_do_not_propose_security_auditor(tmp_path):
    # AUTHORS file and authors.go must not trigger security-auditor
    (tmp_path / "AUTHORS").write_text("Alice <alice@example.com>\n")
    (tmp_path / "authors.go").write_text("package authors\n")
    roles = {r["role"] for r in discovery.discover_roles(tmp_path)}
    assert "security-auditor" not in roles

    # Subdir prefix like "pkg/auth-free" must not flag plain files below it
    (tmp_path / "pkg" / "auth-free").mkdir(parents=True)
    (tmp_path / "pkg" / "auth-free" / "worker.go").write_text("package worker\n")
    roles_sub = {r["role"] for r in discovery.discover_roles(tmp_path, subdir="pkg/auth-free")}
    assert "security-auditor" not in roles_sub


def test_package_json_with_next_tick_does_not_propose_frontend(tmp_path):
    pkg_json = {
        "name": "util-tool",
        "description": "A utility for next actions",
        "dependencies": {
            "next-tick": "^1.1.0"
        }
    }
    (tmp_path / "package.json").write_text(json.dumps(pkg_json))
    roles = {r["role"] for r in discovery.discover_roles(tmp_path)}
    assert "frontend" not in roles
    assert "ui-auditor" not in roles


def test_source_files_named_release_do_not_propose_release_manager(tmp_path):
    (tmp_path / "pkg" / "x").mkdir(parents=True)
    (tmp_path / "pkg" / "x" / "release.go").write_text("package x\n")
    roles = {r["role"] for r in discovery.discover_roles(tmp_path)}
    assert "release-manager" not in roles


def test_changelog_proposes_release_manager(tmp_path):
    (tmp_path / "CHANGELOG.md").write_text("# Changelog\n")
    roles = {r["role"] for r in discovery.discover_roles(tmp_path)}
    assert "release-manager" in roles


def test_low_test_ratio_proposes_test_engineer(tmp_path):
    for i in range(10):
        (tmp_path / f"source_{i}.go").write_text("package main\n")
    roles = {r["role"] for r in discovery.discover_roles(tmp_path)}
    assert "test-engineer" in roles


def test_performance_label_and_bench_dir_propose_performance_analyst(tmp_path):
    # Label test
    roles_lbl = {r["role"] for r in discovery.discover_roles(tmp_path, labels=["performance"])}
    assert "performance-analyst" in roles_lbl

    # Bench directory test
    (tmp_path / "bench").mkdir()
    (tmp_path / "bench" / "runner.go").write_text("package bench\n")
    roles_dir = {r["role"] for r in discovery.discover_roles(tmp_path)}
    assert "performance-analyst" in roles_dir


def test_library_layouts_propose_api_compat_checker(tmp_path):
    # pyproject.toml with [project]
    p1 = tmp_path / "py_lib"
    p1.mkdir()
    (p1 / "pyproject.toml").write_text("[project]\nname = 'mylib'\nversion = '0.1.0'\n")
    assert "api-compat-checker" in {r["role"] for r in discovery.discover_roles(p1, deploy="none")}

    # Cargo.toml with [lib]
    p2 = tmp_path / "rust_lib"
    p2.mkdir()
    (p2 / "Cargo.toml").write_text("[package]\nname = 'rlib'\n[lib]\nname = 'rlib'\n")
    assert "api-compat-checker" in {r["role"] for r in discovery.discover_roles(p2, deploy="none")}

    # package.json with "main" or "exports"
    p3 = tmp_path / "node_lib"
    p3.mkdir()
    (p3 / "package.json").write_text(json.dumps({"name": "nlib", "main": "index.js"}))
    assert "api-compat-checker" in {r["role"] for r in discovery.discover_roles(p3, deploy="none")}


def test_go_module_with_package_main_does_not_get_api_compat_checker(tmp_path):
    (tmp_path / "go.mod").write_text("module example.com/app\n")
    (tmp_path / "main.go").write_text("package main\n\nfunc main() {}\n")
    roles = {r["role"] for r in discovery.discover_roles(tmp_path, deploy="none")}
    assert "api-compat-checker" not in roles


def test_monorepo_parent_go_mod_proposes_api_compat_for_subdir(tmp_path):
    (tmp_path / "go.mod").write_text("module example.com/mono\n")
    (tmp_path / "pkg" / "client").mkdir(parents=True)
    (tmp_path / "pkg" / "client" / "client.go").write_text("package client\n")
    roles = {r["role"] for r in discovery.discover_roles(tmp_path, subdir="pkg/client", deploy="none")}
    assert "api-compat-checker" in roles


def test_always_roles_are_never_proposed_even_with_all_labels(tmp_path):
    (tmp_path / "SECURITY.md").write_text("x")
    (tmp_path / "api.proto").write_text("syntax = 'proto3';")
    (tmp_path / "go.mod").write_text("module example.com/lib\n")
    (tmp_path / "lib.go").write_text("package lib\n")

    always_roles = set(role_library.ALWAYS_BUILD) | set(role_library.ALWAYS_EVAL)
    discovered = discovery.discover_roles(
        tmp_path,
        subdir="pkg/client",
        deploy="none",
        labels=["security", "flaky", "performance"],
    )
    proposed_names = {r["role"] for r in discovered}
    assert proposed_names.isdisjoint(always_roles)


def test_escaping_or_absolute_subdir_returns_empty(tmp_path):
    assert discovery.discover_roles(tmp_path, subdir="/etc") == []
    assert discovery.discover_roles(tmp_path, subdir="../outside") == []


def test_non_regular_fifo_returns_promptly(tmp_path):
    fifo_path = tmp_path / "package.json"
    try:
        os.mkfifo(fifo_path)
    except (AttributeError, OSError):
        pytest.skip("mkfifo not supported on this platform/filesystem")
    # Must not hang waiting on FIFO read
    roles = discovery.discover_roles(tmp_path)
    assert isinstance(roles, list)


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
    (tmp_path / "auth1.go").write_text("package auth1\n")
    (tmp_path / "auth2.go").write_text("package auth2\n")
    (tmp_path / "auth3.go").write_text("package auth3\n")
    (tmp_path / "api.proto").write_text("syntax = 'proto3';")
    discovered = discovery.discover_roles(tmp_path)
    assert len(discovered) >= 2
    keys = [(r["team"], r["role"]) for r in discovered]
    assert keys == sorted(keys)
    for r in discovered:
        assert len(r["evidence"]) <= 3
