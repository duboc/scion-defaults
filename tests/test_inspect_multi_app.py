"""inspect_repository() finds the stack in multi-app repos that have no manifest at the root.

Found running the interview compiler against a real repo shaped like src/backend (Flask + pytest),
src/frontend (Vite/React) and src/management (Next.js): it reported language `unknown` and no commands.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import scion_factory  # noqa: E402


def _write(path: Path, text: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _bananacast_like(root: Path) -> Path:
    _write(root / "src" / "backend" / "requirements.txt", "Flask==3.0.0\ngoogle-cloud-firestore==2.19.0\n")
    _write(root / "src" / "backend" / "app.py", "from flask import Flask\n")
    _write(root / "src" / "backend" / "tests" / "test_x.py", "def test_x():\n    assert True\n")
    pkg = {"name": "frontend", "scripts": {"build": "vite build"}, "dependencies": {"react": "^18.0.0"}}
    _write(root / "src" / "frontend" / "package.json", json.dumps(pkg))
    for i in range(3):
        _write(root / "src" / "frontend" / "src" / f"c{i}.tsx", "export const C = () => null\n")
    _write(root / "src" / "infrastructure" / "terraform" / "main.tf", "")
    return root


def test_multi_app_repo_without_root_manifest_detects_the_tested_app(tmp_path):
    det = scion_factory.inspect_repository(_bananacast_like(tmp_path))["detected"]
    assert det["primary_language"] == "python"
    assert det["framework"] == "Flask"
    assert det["test_command"] == "cd src/backend && pytest -q"
    assert det["build_command"] == "cd src/backend && python3 -m compileall ."
    assert det["lint_command"] == "cd src/backend && ruff check ."
    assert "firestore" in det["cloud_stores"]
    apps = {a["path"]: a for a in det["apps"]}
    assert set(apps) == {"src/backend", "src/frontend"}
    assert apps["src/backend"] == {
        "path": "src/backend", "language": "python", "framework": "Flask",
        "build_command": "cd src/backend && python3 -m compileall .",
        "test_command": "cd src/backend && pytest -q",
        "lint_command": "cd src/backend && ruff check .",
    }
    assert apps["src/frontend"]["language"] == "typescript"
    assert apps["src/frontend"]["build_command"] == "cd src/frontend && npm run build"
    assert apps["src/frontend"]["test_command"] == "cd src/frontend && npm test"
    # A stack was found, so the verification-commands question is answered.
    unresolved = {q["id"] for q in scion_factory.inspect_repository(tmp_path)["unresolved_questions"]}
    assert "stack_verification_commands" not in unresolved


def test_without_tests_the_largest_app_wins_then_path_order(tmp_path):
    _write(tmp_path / "apps" / "api" / "go.mod", "module example.com/api\n\ngo 1.24\n")
    _write(tmp_path / "apps" / "api" / "main.go", "package main\n")
    _write(tmp_path / "apps" / "web" / "package.json", "{}")
    for i in range(4):
        _write(tmp_path / "apps" / "web" / f"p{i}.ts", "export {}\n")
    det = scion_factory.inspect_repository(tmp_path)["detected"]
    assert det["primary_language"] == "typescript"
    assert det["test_command"] == "cd apps/web && npm test"
    assert [a["path"] for a in det["apps"]] == ["apps/api", "apps/web"]

    # Equal size and no tests: path order decides.
    _write(tmp_path / "apps" / "api" / "util.go", "package main\n")
    _write(tmp_path / "apps" / "api" / "x.go", "package main\n")
    _write(tmp_path / "apps" / "api" / "y.go", "package main\n")
    det = scion_factory.inspect_repository(tmp_path)["detected"]
    assert det["primary_language"] == "go"
    assert det["test_command"] == "cd apps/api && go test ./..."


def test_top_level_conventional_dirs_and_tested_typescript_app(tmp_path):
    _write(tmp_path / "server" / "pyproject.toml", "[project]\nname='s'\ndependencies=['fastapi']\n")
    for i in range(5):
        _write(tmp_path / "server" / f"m{i}.py", "x = 1\n")
    _write(tmp_path / "frontend" / "package.json", "{}")
    _write(tmp_path / "frontend" / "src" / "a.tsx", "export {}\n")
    _write(tmp_path / "frontend" / "src" / "a.test.tsx", "test('a', () => {})\n")
    det = scion_factory.inspect_repository(tmp_path)["detected"]
    assert det["primary_language"] == "typescript"
    assert det["test_command"] == "cd frontend && npm test"
    assert {a["path"] for a in det["apps"]} == {"frontend", "server"}
    assert {a["path"]: a["framework"] for a in det["apps"]}["server"] == "FastAPI"


def test_root_flask_requirements_detects_flask(tmp_path):
    (tmp_path / "requirements.txt").write_text("flask==3.0.0\n")
    det = scion_factory.inspect_repository(tmp_path)["detected"]
    assert det["primary_language"] == "python"
    assert det["framework"] == "Flask"
    assert det["test_command"] == "pytest -q"


def test_root_go_mod_is_unchanged_and_ignores_subapps(tmp_path):
    (tmp_path / "go.mod").write_text("module example.com/svc\n\ngo 1.24\n")
    _write(tmp_path / "src" / "frontend" / "package.json", "{}")
    det = scion_factory.inspect_repository(tmp_path)["detected"]
    assert det["primary_language"] == "go"
    assert det["build_command"] == "go build -buildvcs=false ./..."
    assert det["test_command"] == "go test ./..."
    assert det["lint_command"] == "go vet ./..."
    assert det["apps"] == []


def test_no_manifests_anywhere_stays_unknown(tmp_path):
    _write(tmp_path / "src" / "notes" / "README.md", "x")
    det = scion_factory.inspect_repository(tmp_path)["detected"]
    assert det["primary_language"] == "unknown"
    assert det["test_command"] == ""
    assert det["apps"] == []
