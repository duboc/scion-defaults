"""Monorepo multi-factory support: agent names prefixed with the factory slug and a per-factory start folder.

Spec: docs/plans/2026-10-22-monorepo-factories.md
"""
import importlib.util
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "templates"
ROLES = sorted(d.name for d in TEMPLATES.iterdir() if d.is_dir() and not d.name.startswith("_"))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


render_templates = _load("render_templates", ROOT / "scripts" / "render-templates.py")

EXAMPLE_ENV = (ROOT / "config" / "factory.env.example").read_text()


def _env_file(tmp_path, **extra):
    """factory.env.example plus the optional values the reference templates need, plus `extra`."""
    lines = [EXAMPLE_ENV, "SPANNER_INSTANCE=psearch-instance", "SPANNER_DATABASE=psearch-db"]
    lines += [f"{k}={v}" for k, v in extra.items()]
    path = tmp_path / "factory.env"
    path.write_text("\n".join(lines) + "\n")
    return path


def _run_render(tmp_path, *args, **env_values):
    env_path = _env_file(tmp_path, **env_values)
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "render-templates.py"), "--env", str(env_path), *args],
        capture_output=True, text=True,
    )


# --- Task 1: naming and sub-folder variables -------------------------------------------------------------------

def test_agent_name_prefixes_role_with_factory_slug():
    assert render_templates.agent_name("project1", "eval-lead") == "project1-eval-lead"


def test_agent_name_without_prefix_is_the_bare_role():
    assert render_templates.agent_name("", "lead") == "lead"


def test_role_agent_vars_derive_one_variable_per_role():
    assert render_templates.role_agent_vars("project1", ["lead", "eval-lead"]) == {
        "LEAD_AGENT": "project1-lead",
        "EVAL_LEAD_AGENT": "project1-eval-lead",
    }


@pytest.mark.parametrize("prefix", ["Project1", "project_1", "project1-", "-project1", "project--1"])
def test_validate_prefix_rejects_non_slugs(prefix):
    with pytest.raises(ValueError, match="AGENT_PREFIX"):
        render_templates.validate_prefix(prefix, ["lead"])


def test_validate_prefix_rejects_names_longer_than_scion_allows():
    with pytest.raises(ValueError, match="63"):
        render_templates.validate_prefix("p" * 50, ["search-evaluator"])


def test_validate_prefix_accepts_a_full_slug():
    render_templates.validate_prefix("project1", ROLES)


@pytest.mark.parametrize("value,expected", [("src/project1/", "src/project1"), ("", "."), (".", "."),
                                            ("./src/project1", "src/project1")])
def test_normalize_subdir(value, expected):
    assert render_templates.normalize_subdir(value) == expected


@pytest.mark.parametrize("value", ["../x", "/abs", "src/../x"])
def test_normalize_subdir_rejects_paths_outside_the_repo(value):
    with pytest.raises(ValueError, match="FACTORY_SUBDIR"):
        render_templates.normalize_subdir(value)


def test_cli_agent_names_lists_prefixed_names(tmp_path):
    res = _run_render(tmp_path, "--agent-names", AGENT_PREFIX="project1")
    assert res.returncode == 0, res.stderr
    lines = res.stdout.splitlines()
    assert "lead\tproject1-lead" in lines
    assert "eval-lead\tproject1-eval-lead" in lines
    assert len(lines) == len(ROLES)


def test_cli_rejects_an_invalid_prefix(tmp_path):
    res = _run_render(tmp_path, "--agent-names", AGENT_PREFIX="Project_1")
    assert res.returncode != 0
    assert "AGENT_PREFIX" in res.stderr


def test_cli_rejects_a_subdir_outside_the_repo(tmp_path):
    res = _run_render(tmp_path, "--out", str(tmp_path / "out"), FACTORY_SUBDIR="../elsewhere")
    assert res.returncode != 0
    assert "FACTORY_SUBDIR" in res.stderr


# --- Task 2: templates use the role variables and carry FACTORY_SUBDIR -------------------------------------------

ROLE_ALT = "|".join(re.escape(r) for r in sorted(ROLES, key=len, reverse=True))
BARE_MESSAGE = re.compile(rf"scion message ({ROLE_ALT})\b(?!-)")
BARE_REPORT = re.compile(rf"report to `({ROLE_ALT})`", re.IGNORECASE)


def _render_all(tmp_path, **env_values):
    out = tmp_path / "out"
    res = _run_render(tmp_path, "--out", str(out), **env_values)
    assert res.returncode == 0, res.stderr
    return {p.relative_to(out).as_posix(): p.read_text() for p in out.rglob("*") if p.is_file()}


def test_prefixed_render_leaves_no_bare_peer_names(tmp_path):
    files = _render_all(tmp_path, AGENT_PREFIX="project1", FACTORY_SUBDIR="src/project1")
    offenders = [f"{path}: {m.group(0)}" for path, text in files.items()
                 for pattern in (BARE_MESSAGE, BARE_REPORT) for m in pattern.finditer(text)]
    assert offenders == []


def test_prefixed_render_names_peers_with_the_factory_slug(tmp_path):
    files = _render_all(tmp_path, AGENT_PREFIX="project1", FACTORY_SUBDIR="src/project1")
    lead = files["lead/system-prompt.md"]
    assert "`project1-reviewer`" in lead
    assert "`project1-deployer`" in lead
    assert "scion message project1-eval-lead" in files["search-evaluator/system-prompt.md"]
    assert "You report to `project1-lead`" in files["backend/system-prompt.md"]


def test_every_rendered_template_carries_the_start_folder(tmp_path):
    files = _render_all(tmp_path, AGENT_PREFIX="project1", FACTORY_SUBDIR="src/project1/")
    for role in ROLES:
        assert 'FACTORY_SUBDIR: "src/project1"' in files[f"{role}/scion-agent.yaml"], role
    assert "`src/project1`" in files["lead/agents.md"]


def test_unprefixed_render_keeps_bare_role_names(tmp_path):
    files = _render_all(tmp_path)
    assert "scion message lead" in files["backend/system-prompt.md"]
    assert "You report to `lead`" in files["backend/system-prompt.md"]
    for role in ROLES:
        assert 'FACTORY_SUBDIR: "."' in files[f"{role}/scion-agent.yaml"], role


def test_rendered_scion_agent_yaml_stays_valid_yaml(tmp_path):
    yaml = pytest.importorskip("yaml")
    files = _render_all(tmp_path, AGENT_PREFIX="project1", FACTORY_SUBDIR="src/project1")
    for role in ROLES:
        doc = yaml.safe_load(files[f"{role}/scion-agent.yaml"])
        assert doc["env"]["FACTORY_SUBDIR"] == "src/project1", role


# --- Task 3: factory-cd.sh launcher ------------------------------------------------------------------------------

FACTORY_CD = ROOT / "config" / "harness" / "home" / ".local" / "bin" / "factory-cd.sh"


@pytest.fixture
def monorepo(tmp_path):
    repo = tmp_path / "repo"
    for product in ("project1", "project2", "project3"):
        (repo / "src" / product).mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    home = tmp_path / "home"
    home.mkdir()
    return repo, home


def _factory_cd(cwd, home, subdir, *cmd):
    env = {"PATH": os.environ["PATH"], "HOME": str(home)}
    if subdir is not None:
        env["FACTORY_SUBDIR"] = subdir
    return subprocess.run(["sh", str(FACTORY_CD), *(cmd or ("sh", "-c", "pwd -P"))],
                          cwd=cwd, env=env, capture_output=True, text=True)


def test_factory_cd_starts_the_harness_in_the_product_folder(monorepo):
    repo, home = monorepo
    res = _factory_cd(repo, home, "src/project1")
    assert res.returncode == 0, res.stderr
    assert res.stdout.strip() == str((repo / "src" / "project1").resolve())


def test_factory_cd_resolves_against_the_repo_root_from_a_nested_cwd(monorepo):
    repo, home = monorepo
    res = _factory_cd(repo / "src" / "project3", home, "src/project2")
    assert res.stdout.strip() == str((repo / "src" / "project2").resolve()), res.stderr


@pytest.mark.parametrize("subdir", [None, "", "."])
def test_factory_cd_without_a_subdir_keeps_the_cwd(monorepo, subdir):
    repo, home = monorepo
    res = _factory_cd(repo, home, subdir)
    assert res.returncode == 0, res.stderr
    assert res.stdout.strip() == str(repo.resolve())


def test_factory_cd_passes_arguments_through_unchanged(monorepo):
    repo, home = monorepo
    res = _factory_cd(repo, home, "src/project1", "sh", "-c", 'printf "%s|" "$@"', "sh", "a b", "--flag")
    assert res.stdout == "a b|--flag|"


@pytest.mark.parametrize("subdir", ["/etc", "../outside", "src/../../x"])
def test_factory_cd_refuses_paths_outside_the_repo(monorepo, subdir):
    repo, home = monorepo
    res = _factory_cd(repo, home, subdir)
    assert res.returncode == 64
    assert "FACTORY_SUBDIR" in res.stderr


def test_factory_cd_refuses_a_missing_folder(monorepo):
    repo, home = monorepo
    res = _factory_cd(repo, home, "src/project9")
    assert res.returncode == 66
    assert "src/project9" in res.stderr


def test_factory_cd_outside_git_resolves_against_the_cwd(tmp_path):
    (tmp_path / "work" / "src" / "project1").mkdir(parents=True)
    (tmp_path / "home").mkdir()
    res = _factory_cd(tmp_path / "work", tmp_path / "home", "src/project1")
    assert res.stdout.strip() == str((tmp_path / "work" / "src" / "project1").resolve()), res.stderr


def test_factory_cd_pre_trusts_the_folder_for_claude(monorepo):
    repo, home = monorepo
    (home / ".claude.json").write_text(json.dumps({"projects": {"/workspace": {"hasTrustDialogAccepted": True}},
                                                   "keep": 1}))
    for _ in range(2):
        assert _factory_cd(repo, home, "src/project1").returncode == 0
    cfg = json.loads((home / ".claude.json").read_text())
    target = str((repo / "src" / "project1").resolve())
    assert cfg["projects"][target]["hasTrustDialogAccepted"] is True
    assert cfg["projects"]["/workspace"] == {"hasTrustDialogAccepted": True}
    assert cfg["keep"] == 1


def test_factory_cd_pre_trusts_the_folder_for_antigravity_once(monorepo):
    repo, home = monorepo
    settings = home / ".gemini" / "antigravity-cli" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({"trustedWorkspaces": ["/workspace"], "onboardingComplete": True}))
    for _ in range(2):
        assert _factory_cd(repo, home, "src/project1").returncode == 0
    data = json.loads(settings.read_text())
    assert data["trustedWorkspaces"] == ["/workspace", str((repo / "src" / "project1").resolve())]
    assert data["onboardingComplete"] is True


def test_factory_cd_leaves_absent_harness_configs_absent(monorepo):
    repo, home = monorepo
    assert _factory_cd(repo, home, "src/project1").returncode == 0
    assert not (home / ".claude.json").exists()
    assert not (home / ".gemini").exists()


def test_factory_cd_carries_antigravity_hooks_into_the_folder(monorepo):
    repo, home = monorepo
    (repo / ".agents").mkdir()
    (repo / ".agents" / "hooks.json").write_text('{"hooks": {}}')
    for _ in range(2):
        assert _factory_cd(repo, home, "src/project1").returncode == 0
    assert (repo / "src" / "project1" / ".agents" / "hooks.json").read_text() == '{"hooks": {}}'
    exclude = (repo / ".git" / "info" / "exclude").read_text().splitlines()
    assert exclude.count(".agents/") == 1


def test_factory_cd_refreshes_stale_antigravity_hooks(monorepo):
    repo, home = monorepo
    (repo / ".agents").mkdir()
    (repo / ".agents" / "hooks.json").write_text('{"v": 1}')
    assert _factory_cd(repo, home, "src/project1").returncode == 0
    (repo / ".agents" / "hooks.json").write_text('{"v": 2}')
    assert _factory_cd(repo, home, "src/project1").returncode == 0
    assert (repo / "src" / "project1" / ".agents" / "hooks.json").read_text() == '{"v": 2}'
