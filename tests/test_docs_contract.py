import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_skill_and_readme_document_the_team_step():
    skill = (ROOT / "skills/scion-factory-bootstrap/SKILL.md").read_text()
    for needle in ("add-role", "proposed_roles", "dropped_defaults", "team.autonomy", "infrastructure.deploy"):
        assert needle in skill
    readme = (ROOT / "README.md").read_text()
    assert "autonomy" in readme and "deploy: none" in readme
    assert subprocess.run([sys.executable, "scripts/scion_factory.py", "add-role", "--help"], cwd=ROOT).returncode == 0


def test_grill_question_team_roster_describes_specialists_and_deploy():
    sys.path.insert(0, str(ROOT / "scripts"))
    import scion_factory

    question = next(q for q in scion_factory.GRILL_QUESTIONS if q["id"] == "team_roster_and_harnesses")
    prompt = question["challenge_prompt"]
    q_text = question["question"]

    # Must mention the 3 default specialists, dropped_defaults/reason, and deployer conditional on deploy: service
    for needle in ("researcher", "doc-writer", "investigator"):
        assert needle in prompt or needle in q_text
    assert "deploy" in prompt or "deploy" in q_text
    assert "add-role" in prompt or "add-role" in q_text or "proposed" in prompt or "proposed" in q_text
    for needle in ("dropped_defaults", "reason"):
        assert needle in prompt or needle in q_text


def test_grill_me_playbook_covers_deploy_and_team_topics():
    playbook = (ROOT / "docs/grill-me-playbook.md").read_text()
    assert "deploy_target" in playbook or "infrastructure.deploy" in playbook
    assert "autonomy" in playbook


def test_templates_readme_references_role_library():
    templates_readme = (ROOT / "templates/README.md").read_text()
    assert "role_library.py" in templates_readme


def test_setup_runbook_documents_deploy_none_sandbox_skips():
    runbook = (ROOT / "docs/setup-runbook.md").read_text()
    assert "deploy: none" in runbook or "infrastructure.deploy: none" in runbook
    assert "DEPLOYER_SA_NAME" in runbook


def test_readme_includes_copy_paste_starting_prompts():
    readme = (ROOT / "README.md").read_text()
    assert "## Starting prompts" in readme
    for needle in (
        "scion-factory-bootstrap",
        "existing Scion Hub",
        "new Scion Hub",
        "monorepo",
        "factory/main",
        "factory/eval",
    ):
        assert needle in readme, f"missing {needle!r} in README starting prompts"

