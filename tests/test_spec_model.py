"""Tests for the FactorySpec team model: default specialists, explicit drops, autonomy and deploy fields, add-role
(docs/specs/2026-10-26-specialist-agent-fabric-design.md §5.3, §7.4, §8, §9)."""

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import role_library  # noqa: E402
import scion_factory  # noqa: E402
from role_library import ALWAYS_BUILD, ALWAYS_EVAL  # noqa: E402

PRESETS = sorted((ROOT / "presets").glob("*.factory.yaml"))


def _preset(name: str) -> dict:
    return scion_factory.load_spec(ROOT / "presets" / name)


def _starter() -> dict:
    return _preset("starter-service.factory.yaml")


def _roles(roster: list[dict]) -> list[str]:
    return [r["role"] for r in roster]


def test_draft_roster_has_the_always_roles_marked_default():
    draft = scion_factory.draft_spec_from_repo(None)
    team = draft["team"]
    assert _roles(team["build_roles"]) == list(ALWAYS_BUILD)
    assert _roles(team["eval_roles"]) == list(ALWAYS_EVAL)
    assert all(r["default"] is True for r in team["build_roles"] + team["eval_roles"])
    assert "frontend" not in _roles(team["build_roles"])
    assert "deployer" not in _roles(team["build_roles"])
    assert team["autonomy"] == "standard"
    # An intent field the interview must answer.
    assert draft["infrastructure"]["deploy"] == ""
    assert any("infrastructure.deploy" in m for m in scion_factory.evaluate_spec_readiness(draft)["missing_or_weak"])


def test_presets_still_ready_and_compile(tmp_path):
    assert len(PRESETS) >= 3
    for preset in PRESETS:
        spec = scion_factory.load_spec(preset)
        report = scion_factory.evaluate_spec_readiness(spec)
        assert report["ready"] is True, (preset.name, report["missing_or_weak"])
        assert isinstance(report["warnings"], list)
        res = scion_factory.compile_factory_boilerplate(spec, tmp_path / preset.stem)
        assert res["files_written"] > 0


def test_old_spec_gets_missing_defaults_without_duplicates(tmp_path):
    spec = _preset("acme-airlines.factory.yaml")
    report = scion_factory.evaluate_spec_readiness(spec)
    assert report["ready"] is True
    assert any("doc-writer" in w for w in report["warnings"])
    assert any("investigator" in w for w in report["warnings"])
    assert not any("researcher" in w for w in report["warnings"])

    out = tmp_path / "kit"
    scion_factory.compile_factory_boilerplate(spec, out)
    for role in ("doc-writer", "investigator", "researcher"):
        assert (out / "templates" / role / "system-prompt.md").exists(), role
    compiled = scion_factory.load_spec(out / "factory.yaml")
    assert _roles(compiled["team"]["build_roles"]).count("researcher") == 1
    agents_md = (out / "AGENTS.md").read_text()
    assert agents_md.count("| `researcher` |") == 1
    assert "Roster notes" in agents_md
    assert "added default role doc-writer" in agents_md
    # The preset keeps its own researcher definition.
    original = next(r for r in spec["team"]["build_roles"] if r["role"] == "researcher")
    kept = next(r for r in compiled["team"]["build_roles"] if r["role"] == "researcher")
    assert kept == original


def test_renamed_implementer_counts_as_backend():
    spec = _starter()
    spec["team"]["build_roles"] = [r for r in spec["team"]["build_roles"] if r["role"] != "backend"]
    spec["team"]["build_roles"].append({"role": "go-dev", "default_kind": "implementer", "harness": "antigravity",
                                        "model": "Gemini 4 Argon (High)", "responsibility": "Implements Go changes."})
    normalized, notes = scion_factory.normalize_team(spec)
    names = _roles(normalized["team"]["build_roles"])
    assert "backend" not in names
    assert names.count("go-dev") == 1
    assert not any("backend" in n for n in notes)
    assert "added default role doc-writer" in notes
    # normalize_team returns a copy and leaves the input alone.
    assert "doc-writer" not in _roles(spec["team"]["build_roles"])


def test_normalize_team_adds_library_entries_marked_default():
    normalized, notes = scion_factory.normalize_team(_starter())
    added = {r["role"]: r for r in normalized["team"]["build_roles"] if r["role"] in ("researcher", "doc-writer",
                                                                                         "investigator")}
    assert set(added) == {"researcher", "doc-writer", "investigator"}
    for role, entry in added.items():
        assert entry["default"] is True
        assert entry["responsibility"] == role_library.ROLES[role]["responsibility"]
        assert entry["harness"] == role_library.ROLES[role]["harness"]
    assert "added default role investigator" in notes


def test_dropping_a_default_needs_a_reason():
    spec = _starter()
    spec["team"]["dropped_defaults"] = [{"role": "researcher"}]
    report = scion_factory.evaluate_spec_readiness(spec)
    assert report["ready"] is False
    assert "dropped_defaults: researcher needs a reason" in report["missing_or_weak"]

    spec["team"]["dropped_defaults"] = [{"role": "researcher", "reason": "   "}]
    assert "dropped_defaults: researcher needs a reason" in scion_factory.evaluate_spec_readiness(spec)["missing_or_weak"]

    spec["team"]["dropped_defaults"] = [{"role": "researcher", "reason": "The PO writes the research notes."}]
    report = scion_factory.evaluate_spec_readiness(spec)
    assert report["ready"] is True, report["missing_or_weak"]
    assert not any("researcher" in w for w in report["warnings"])
    normalized, notes = scion_factory.normalize_team(spec)
    assert "researcher" not in _roles(normalized["team"]["build_roles"])
    assert not any("researcher" in n for n in notes)


def test_bad_enum_values_fail_readiness():
    spec = _starter()
    spec["infrastructure"]["deploy"] = "maybe"
    report = scion_factory.evaluate_spec_readiness(spec)
    assert report["ready"] is False
    assert any("infrastructure.deploy" in m for m in report["missing_or_weak"])

    spec = _starter()
    spec["team"]["autonomy"] = "yolo"
    report = scion_factory.evaluate_spec_readiness(spec)
    assert report["ready"] is False
    assert any("team.autonomy" in m for m in report["missing_or_weak"])

    for deploy in ("service", "none"):
        spec = _starter()
        spec["infrastructure"]["deploy"] = deploy
        assert not any("infrastructure.deploy" in m for m in scion_factory.evaluate_spec_readiness(spec)["missing_or_weak"])
    for level in ("supervised", "standard", "autonomous"):
        spec = _starter()
        spec["team"]["autonomy"] = level
        assert scion_factory.evaluate_spec_readiness(spec)["ready"] is True


def test_deploy_mode_and_autonomy_defaults():
    spec = _starter()
    assert scion_factory.deploy_mode(spec) == "service"
    assert scion_factory.autonomy(spec) == "standard"
    spec["infrastructure"]["deploy"] = ""
    assert scion_factory.deploy_mode(spec) == ""
    spec["infrastructure"]["deploy"] = "none"
    spec["team"]["autonomy"] = "supervised"
    assert scion_factory.deploy_mode(spec) == "none"
    assert scion_factory.autonomy(spec) == "supervised"


def test_add_role_inserts_library_entry_with_evidence(tmp_path):
    path = tmp_path / "factory.yaml"
    scion_factory.save_spec(_starter(), path)
    scion_factory.add_role(path, "security-auditor", evidence=["pkg/auth"])
    spec = scion_factory.load_spec(path)
    entries = [r for r in spec["team"]["build_roles"] if r["role"] == "security-auditor"]
    assert len(entries) == 1
    assert entries[0]["harness"] == "antigravity"
    assert entries[0]["evidence"] == ["pkg/auth"]
    assert entries[0]["responsibility"] == role_library.ROLES["security-auditor"]["responsibility"]

    scion_factory.add_role(path, "security-auditor", evidence=["SECURITY.md"])
    spec = scion_factory.load_spec(path)
    entries = [r for r in spec["team"]["build_roles"] if r["role"] == "security-auditor"]
    assert len(entries) == 1
    assert entries[0]["evidence"] == ["pkg/auth"]

    # Eval roles go to the eval roster by default.
    scion_factory.add_role(path, "safety-evaluator")
    spec = scion_factory.load_spec(path)
    assert "safety-evaluator" in _roles(spec["team"]["eval_roles"])
    assert "safety-evaluator" not in _roles(spec["team"]["build_roles"])
    assert scion_factory.evaluate_spec_readiness(spec)["ready"] is True


def test_add_role_refuses_service_roles_without_a_service(tmp_path):
    path = tmp_path / "factory.yaml"
    spec = _starter()
    spec["team"]["build_roles"] = [r for r in spec["team"]["build_roles"] if r["role"] != "deployer"]
    spec["infrastructure"]["deploy"] = "none"
    scion_factory.save_spec(spec, path)
    with pytest.raises(ValueError, match="deploy: none"):
        scion_factory.add_role(path, "deployer")
    assert "deployer" not in _roles(scion_factory.load_spec(path)["team"]["build_roles"])


def test_add_role_rejects_unknown_roles(tmp_path):
    path = tmp_path / "factory.yaml"
    scion_factory.save_spec(_starter(), path)
    with pytest.raises(ValueError, match="unknown role"):
        scion_factory.add_role(path, "astrologer")


def test_add_role_cli_prints_json(tmp_path):
    path = tmp_path / "factory.yaml"
    scion_factory.save_spec(_starter(), path)
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "scion_factory.py"), "add-role", str(path),
         "--role", "test-engineer", "--evidence", "no test command", "--evidence", "label: flaky"],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["role"] == "test-engineer"
    assert payload["team"] == "build"
    assert payload["added"] is True
    entry = next(r for r in scion_factory.load_spec(path)["team"]["build_roles"] if r["role"] == "test-engineer")
    assert entry["evidence"] == ["no test command", "label: flaky"]

    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "scion_factory.py"), "add-role", str(path), "--role", "astrologer"],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 2
    assert "unknown role" in proc.stderr


def test_check_cli_prints_warnings(tmp_path):
    path = tmp_path / "factory.yaml"
    scion_factory.save_spec(_starter(), path)
    proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "scion_factory.py"), "check", str(path)],
                          capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "WARNING" in proc.stdout and "doc-writer" in proc.stdout


def test_grill_payload_lists_proposed_roles(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "SECURITY.md").write_text("# Security policy\n")
    (repo / "go.mod").write_text("module example.com/svc\n\ngo 1.24\n")
    payload = scion_factory.prepare_grill_session(repo, tmp_path / "factory.yaml")
    roles = {r["role"]: r for r in payload["proposed_roles"]}
    assert "security-auditor" in roles
    assert roles["security-auditor"]["evidence"]

    no_repo = scion_factory.prepare_grill_session(None, tmp_path / "other.yaml")
    assert no_repo["proposed_roles"] == []


def test_schema_accepts_new_fields_and_deploy_none_without_services():
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads((ROOT / "config" / "factory.schema.json").read_text())
    spec = _starter()
    spec["infrastructure"]["deploy"] = "none"
    for key in ("serving_service", "eval_service", "deployer_iam_roles"):
        spec["infrastructure"].pop(key)
    spec["team"]["autonomy"] = "supervised"
    spec["team"]["dropped_defaults"] = [{"role": "investigator", "reason": "Bugs are rare in this library."}]
    spec["team"]["build_roles"][1].update({
        "focus": {"paths": ["pkg/"], "commands": ["go test ./..."], "docs": ["README.md"]},
        "evidence": ["go.mod"],
        "default": True,
        "default_kind": "implementer",
    })
    jsonschema.validate(spec, schema)

    # With a service (or no deploy field), the service fields stay required.
    service = copy.deepcopy(spec)
    service["infrastructure"]["deploy"] = "service"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(service, schema)
    service["infrastructure"].pop("deploy")
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(service, schema)

    for path, bad in ((("infrastructure", "deploy"), "maybe"), (("team", "autonomy"), "yolo")):
        broken = copy.deepcopy(spec)
        broken[path[0]][path[1]] = bad
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(broken, schema)

    broken = copy.deepcopy(spec)
    broken["team"]["build_roles"][1]["focus"]["paths"] = "pkg/"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(broken, schema)
