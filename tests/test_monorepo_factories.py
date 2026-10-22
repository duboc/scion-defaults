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
