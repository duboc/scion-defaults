"""Shared directories (scratchpad, knowledge-plane) per factory project, and a knowledge plane per monorepo factory.

Spec: docs/plans/2026-10-22-monorepo-factories.md, "Part 2: shared directories".
"""
import importlib.util
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


admin = _load("scion_admin", ROOT / "scripts" / "scion-admin.py")


# --- scion-admin.py project-create -------------------------------------------------------------------------------

def test_project_body_for_a_shared_git_project():
    body = admin.project_create_body("project1", "https://github.com/o/r.git", "main", "shared", "tok")
    assert body == {"name": "project1", "slug": "project1", "gitRemote": "https://github.com/o/r.git",
                    "defaultBranch": "main", "workspaceMode": "shared", "githubToken": "tok"}


def test_project_body_without_a_token_leaves_it_out():
    body = admin.project_create_body("project1-eval", "https://github.com/o/r.git", "eval-dashboard", "per-agent", "")
    assert "githubToken" not in body
    assert body["workspaceMode"] == "per-agent"


@pytest.mark.parametrize("mode", ["shared-plain", "clone", ""])
def test_project_body_rejects_unknown_modes(mode):
    with pytest.raises(ValueError, match="workspace mode"):
        admin.project_create_body("p", "https://github.com/o/r.git", "main", mode, "")


def test_project_body_requires_a_token_for_shared_mode():
    """The Hub clones a shared project at creation; without a token a private repo fails with clone_failed."""
    with pytest.raises(ValueError, match="token"):
        admin.project_create_body("p", "https://github.com/o/r.git", "main", "shared", "")


def test_read_token_file(tmp_path):
    f = tmp_path / "tok"
    f.write_text("gho_x\n")
    assert admin.read_token_file(str(f)) == "gho_x"
    assert admin.read_token_file("") == ""


# --- scion-admin.py shared-dirs ----------------------------------------------------------------------------------

def test_parse_dir_specs_reads_modes():
    assert admin.parse_dir_specs("scratchpad,knowledge-plane:ro") == [
        {"name": "scratchpad", "read_only": False},
        {"name": "knowledge-plane", "read_only": True},
    ]


@pytest.mark.parametrize("spec", ["Scratch_Pad", "kp:rw:x", "kp:readonly", ""])
def test_parse_dir_specs_rejects_bad_specs(spec):
    with pytest.raises(ValueError):
        admin.parse_dir_specs(spec)


WANTED = [{"name": "scratchpad", "read_only": False}, {"name": "knowledge-plane", "read_only": True}]


def test_plan_creates_only_missing_dirs():
    create, conflicts = admin.plan_shared_dirs([{"name": "scratchpad"}], WANTED)
    assert create == [{"name": "knowledge-plane", "read_only": True}]
    assert conflicts == []


def test_plan_is_a_no_op_when_everything_exists():
    existing = [{"name": "scratchpad"}, {"name": "knowledge-plane", "read_only": True}]
    assert admin.plan_shared_dirs(existing, WANTED) == ([], [])


def test_plan_reports_a_mode_mismatch_instead_of_changing_it():
    """Changing a mode means delete + create, which deletes the contents: never automatic."""
    create, conflicts = admin.plan_shared_dirs([{"name": "knowledge-plane"}], WANTED)
    assert create == [{"name": "scratchpad", "read_only": False}]
    assert conflicts == ["knowledge-plane is read-write, wanted read-only (fix it by hand: removing it deletes its files)"]


def test_default_projects_come_from_factory_env():
    assert admin.default_projects({"FACTORY_PROJECTS": "project1, project1-eval,"}) == ["project1", "project1-eval"]
    assert admin.default_projects({"BUILD_PROJECT": "project1", "EVAL_PROJECT": "project1-eval"}) == [
        "project1", "project1-eval"]
    assert admin.default_projects({}) == []


# --- factory-watchdog.py knowledge plane per factory -------------------------------------------------------------

@pytest.fixture(scope="module")
def watchdog(tmp_path_factory):
    os.environ.setdefault("SCION_HOME", str(tmp_path_factory.mktemp("scion-home")))
    return _load("factory_watchdog_kp", ROOT / "scripts" / "factory-watchdog.py")


def test_sources_map_each_factory_and_its_eval_project(watchdog):
    sources = watchdog.knowledge_plane_sources(
        ["project1", "project1-eval", "project2", "project2-eval"],
        "project1=src/project1/knowledge-plane, project2=src/project2/knowledge-plane", "")
    assert sources == {
        "project1": ("project1", "src/project1/knowledge-plane"),
        "project1-eval": ("project1", "src/project1/knowledge-plane"),
        "project2": ("project2", "src/project2/knowledge-plane"),
        "project2-eval": ("project2", "src/project2/knowledge-plane"),
    }


def test_sources_without_a_map_keep_the_single_source(watchdog):
    assert watchdog.knowledge_plane_sources(["psearch", "psearch-eval"], "", "") == {
        "psearch": ("psearch", "knowledge-plane"), "psearch-eval": ("psearch", "knowledge-plane")}
    assert watchdog.knowledge_plane_sources(["a", "b"], "", "b") == {"a": ("b", "knowledge-plane"),
                                                                     "b": ("b", "knowledge-plane")}


def test_sources_with_a_map_skip_unmapped_projects(watchdog):
    assert watchdog.knowledge_plane_sources(["project1", "project3"], "project1=src/project1/kp", "") == {
        "project1": ("project1", "src/project1/kp")}


@pytest.mark.parametrize("bad", ["project1", "project1=/abs/kp", "project1=../kp", "=src/kp"])
def test_sources_reject_bad_entries(watchdog, bad):
    with pytest.raises(ValueError, match="KNOWLEDGE_PLANE_SOURCES"):
        watchdog.knowledge_plane_sources(["project1"], bad, "")


def _hub(scion_home, projects):
    conn = sqlite3.connect(scion_home / "hub.db")
    conn.execute("CREATE TABLE projects (id TEXT, slug TEXT)")
    for pid, slug in projects:
        conn.execute("INSERT INTO projects VALUES (?, ?)", (pid, slug))
    conn.commit()
    conn.row_factory = sqlite3.Row
    return conn


@pytest.mark.skipif(not any(os.access(os.path.join(p, "rsync"), os.X_OK)
                            for p in os.environ["PATH"].split(os.pathsep)), reason="rsync not installed")
def test_sync_copies_each_factorys_knowledge_plane_into_its_projects(watchdog, tmp_path, monkeypatch, capsys):
    home = tmp_path / ".scion"
    ids = {"project1": "11111111-aaaa", "project1-eval": "22222222-bbbb", "project2": "33333333-cccc",
           "project2-eval": "44444444-dddd"}
    conn = _hub(home.mkdir() or home, list((v, k) for k, v in ids.items()))
    for p in ("project1", "project2"):
        kp = home / "projects" / p / "src" / p / "knowledge-plane"
        kp.mkdir(parents=True)
        (kp / "INDEX.md").write_text(f"KP-{p}\n")
    for slug, pid in ids.items():
        if slug != "project2-eval":  # no agent has started there yet: its shared-dirs base doesn't exist
            (home / "project-configs" / f"{slug}__{pid[:8]}" / "shared-dirs").mkdir(parents=True)
    stale = home / "project-configs" / "project1-eval__22222222" / "shared-dirs" / "knowledge-plane" / "old.md"
    stale.parent.mkdir()
    stale.write_text("stale")
    monkeypatch.setattr(watchdog, "SCION_HOME", str(home))
    monkeypatch.setattr(watchdog, "FACTORY_PROJECTS", list(ids))
    monkeypatch.setenv("KNOWLEDGE_PLANE_SOURCES", "project1=src/project1/knowledge-plane,"
                                                  "project2=src/project2/knowledge-plane")

    watchdog.check_shared_knowledge_plane(conn, apply=True)

    def kp(slug):
        return home / "project-configs" / f"{slug}__{ids[slug][:8]}" / "shared-dirs" / "knowledge-plane"
    assert (kp("project1") / "INDEX.md").read_text() == "KP-project1\n"
    assert (kp("project1-eval") / "INDEX.md").read_text() == "KP-project1\n"
    assert not (kp("project1-eval") / "old.md").exists()
    assert (kp("project2") / "INDEX.md").read_text() == "KP-project2\n"
    assert not kp("project2-eval").exists()
    out = capsys.readouterr().out
    assert "project2-eval: skipped" in out


def test_sync_reports_a_missing_source(watchdog, tmp_path, monkeypatch, capsys):
    home = tmp_path / ".scion"
    conn = _hub(home.mkdir() or home, [("11111111-aaaa", "project1")])
    monkeypatch.setattr(watchdog, "SCION_HOME", str(home))
    monkeypatch.setattr(watchdog, "FACTORY_PROJECTS", ["project1"])
    monkeypatch.setenv("KNOWLEDGE_PLANE_SOURCES", "project1=src/project1/knowledge-plane")
    watchdog.check_shared_knowledge_plane(conn, apply=True)
    assert "source missing" in capsys.readouterr().out


def test_watchdog_env_keeps_the_knowledge_plane_settings_for_cron(watchdog, monkeypatch):
    """install_cron writes watchdog.env for the cron runs; settings it leaves out silently fall back to defaults."""
    monkeypatch.setenv("KNOWLEDGE_PLANE_SOURCES", "project1=src/project1/knowledge-plane")
    monkeypatch.setenv("KNOWLEDGE_PLANE_SOURCE_PROJECT", "project1")
    content = watchdog.watchdog_env_content("/usr/bin:/bin")
    assert "KNOWLEDGE_PLANE_SOURCES=project1=src/project1/knowledge-plane\n" in content
    assert "KNOWLEDGE_PLANE_SOURCE_PROJECT=project1\n" in content
    assert "PATH=/usr/bin:/bin\n" in content


def test_run_on_hub_forwards_the_knowledge_plane_settings():
    text = (ROOT / "scripts" / "run-on-hub.sh").read_text()
    assert "KNOWLEDGE_PLANE_SOURCES" in text and "KNOWLEDGE_PLANE_SOURCE_PROJECT" in text


# --- templates: the knowledge plane path follows FACTORY_SUBDIR --------------------------------------------------

def _render(tmp_path, **extra):
    env = tmp_path / "factory.env"
    env.write_text((ROOT / "config" / "factory.env.example").read_text()
                   + "\nSPANNER_INSTANCE=i\nSPANNER_DATABASE=d\n" + "".join(f"{k}={v}\n" for k, v in extra.items()))
    out = tmp_path / "out"
    res = subprocess.run([sys.executable, str(ROOT / "scripts" / "render-templates.py"), "--env", str(env),
                          "--out", str(out)], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    return {p.relative_to(out).as_posix(): p.read_text() for p in out.rglob("*") if p.is_file()}


def test_knowledge_plane_path_follows_the_product_folder(tmp_path):
    files = _render(tmp_path, AGENT_PREFIX="project1", FACTORY_SUBDIR="src/project1")
    assert "`/workspace/src/project1/knowledge-plane/INDEX.md`" in files["lead/system-prompt.md"]
    assert not [p for p, t in files.items() if "/workspace/knowledge-plane" in t]


def test_knowledge_plane_path_is_unchanged_for_a_single_factory(tmp_path):
    files = _render(tmp_path)
    assert "`/workspace/knowledge-plane/INDEX.md`" in files["lead/system-prompt.md"]


def test_every_agent_learns_about_the_shared_directories(tmp_path):
    files = _render(tmp_path, AGENT_PREFIX="project1", FACTORY_SUBDIR="src/project1")
    agents = files["lead/agents.md"]
    assert "/scion-volumes/scratchpad" in agents
    assert "/scion-volumes/knowledge-plane" in agents


def test_rendered_prompts_and_configs_have_no_placeholders_left(tmp_path):
    """A variable missing from render-templates.py VARS is left as ${NAME}: an agent would read it literally."""
    import re
    files = _render(tmp_path, AGENT_PREFIX="project1", FACTORY_SUBDIR="src/project1")
    leftovers = {f"{p}: {m}" for p, t in files.items() if p.endswith((".md", ".yaml"))
                 for m in re.findall(r"\$\{[A-Z][A-Z0-9_]*\}", t)}
    assert leftovers == set()
