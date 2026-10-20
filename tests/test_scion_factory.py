import json
import os
import subprocess
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parent.parent


def test_import_scion_factory():
    """Verify scion_factory module can be imported from scripts/."""
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    import scion_factory  # noqa: F401


def test_inspect_repository_detects_stack_and_gaps(tmp_path):
    """Static introspector detects Go/Docker stack and identifies remaining intent gaps."""
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    import scion_factory

    (tmp_path / "go.mod").write_text("module github.com/example/order-api\n\ngo 1.24\nrequire cloud.google.com/go/spanner v1.60.0\n")
    (tmp_path / "Dockerfile").write_text("FROM golang:1.24\nEXPOSE 8080\n")
    (tmp_path / "Makefile").write_text("test:\n\tgo test ./...\n")

    inspection = scion_factory.inspect_repository(tmp_path)
    assert inspection["detected"]["primary_language"] == "go"
    assert "go test ./..." in inspection["detected"]["test_command"]
    assert "spanner" in inspection["detected"]["cloud_stores"]
    assert inspection["detected"]["port"] == 8080
    # Must still require human intent for evaluation oracle & waves
    unresolved_ids = {q["id"] for q in inspection["unresolved_questions"]}
    assert "eval_l3_metric" in unresolved_ids
    assert "eval_l5_slo" in unresolved_ids
    assert "wave_1_backlog" in unresolved_ids


def test_grill_questions_cover_five_pillars_and_lessons():
    """Verify the Socratic grill-me bank covers all 5 factory pillars and cites lessons L1-L27."""
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    import scion_factory

    questions = scion_factory.get_grill_questions()
    assert len(questions) >= 8
    pillars = {q["pillar"] for q in questions}
    assert pillars == {
        "1_mission_and_boundaries",
        "2_runtime_data_and_iam",
        "3_roster_models_and_cost",
        "4_evaluation_pyramid_oracle",
        "5_backlog_waves_and_autonomy",
    }
    for q in questions:
        assert q.get("lessons"), f"Question {q['id']} must reference lessons learned"
        assert q.get("why_we_ask"), f"Question {q['id']} must explain why we ask"
        assert q.get("challenge_prompt"), f"Question {q['id']} must include a Socratic follow-up probe"


def test_readiness_scorer_rejects_vague_spec_and_accepts_complete_spec():
    """Readiness evaluator blocks boilerplate generation when L1-L5 oracle or waves are missing."""
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    import scion_factory

    incomplete_spec = {
        "project": {"name": "foo", "slug": "foo"},
    }
    report = scion_factory.evaluate_spec_readiness(incomplete_spec)
    assert report["ready"] is False
    assert len(report["missing_or_weak"]) >= 4

    preset_path = ROOT / "presets" / "starter-service.factory.yaml"
    complete_spec = scion_factory.load_spec(preset_path)
    report_ok = scion_factory.evaluate_spec_readiness(complete_spec)
    assert report_ok["ready"] is True, f"Unexpected issues: {report_ok['missing_or_weak']}"


def test_compile_non_search_project_has_zero_psearch_domain_leaks(tmp_path):
    """Compiling a non-search project produces complete boilerplate with 0 psearch/acme leaks."""
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    import scion_factory

    spec = scion_factory.load_spec(ROOT / "presets" / "starter-service.factory.yaml")
    out_dir = tmp_path / "ledger-factory"
    result = scion_factory.compile_factory_boilerplate(spec, out_dir)

    assert result["files_written"] >= 20
    assert (out_dir / "AGENTS.md").exists()
    assert (out_dir / "factory.yaml").exists()
    assert (out_dir / "config" / "factory.env.example").exists()
    assert (out_dir / "config" / "spanner-factory-ddl.sql").exists()
    assert (out_dir / "waves" / "issues.json").exists()
    assert (out_dir / "scripts" / "verify-factory.sh").exists()
    assert (out_dir / "templates" / "_shared" / "agents.md").exists()
    assert (out_dir / "templates" / "lead" / "system-prompt.md").exists()
    assert (out_dir / "templates" / "domain-evaluator" / "system-prompt.md").exists()

    # Forbidden domain tokens from psearch or acme-airlines
    forbidden = ["psearch", "acme-airlines", "ndcg_at_10", "mrr_at_10", "alphaCurve", "CosineDistance", "IRROPS"]
    leaks = []
    for path in out_dir.rglob("*"):
        if path.is_file():
            text = path.read_text(errors="ignore")
            for token in forbidden:
                if token.lower() in text.lower():
                    leaks.append((str(path.relative_to(out_dir)), token))

    assert leaks == [], f"Found domain leaks in generated boilerplate: {leaks}"


def test_retrodiction_presets_compile_cleanly(tmp_path):
    """Both psearch and acme-airlines presets compile cleanly from the same engine."""
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    import scion_factory

    for preset_name in ("psearch.factory.yaml", "acme-airlines.factory.yaml"):
        spec = scion_factory.load_spec(ROOT / "presets" / preset_name)
        report = scion_factory.evaluate_spec_readiness(spec)
        assert report["ready"] is True, f"{preset_name} failed readiness: {report}"
        out_dir = tmp_path / preset_name.replace(".factory.yaml", "")
        res = scion_factory.compile_factory_boilerplate(spec, out_dir)
        assert res["files_written"] >= 20
        # Verify generated shell script syntax with bash -n
        verify_sh = out_dir / "scripts" / "verify-factory.sh"
        proc = subprocess.run(["bash", "-n", str(verify_sh)], capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr
