import re
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


def test_docs_cover_github_workflow_scope_and_lesson_l31():
    readme = (ROOT / "README.md").read_text()
    runbook = (ROOT / "docs/setup-runbook.md").read_text()
    lessons = (ROOT / "docs/lessons-learned.md").read_text()

    assert "workflow" in readme and ".github/workflows" in readme
    assert "workflow" in runbook and ".github/workflows" in runbook
    assert "## L31." in lessons
    for needle in (
        "eval-sweep",
        "pytest.importorskip",
        "migration-specialist",
        "/scion-volumes/scratchpad/pending-",
        "status:po-decision",
    ):
        assert needle in lessons, f"missing {needle!r} in docs/lessons-learned.md"



def test_docs_and_skill_cover_gke_runtime_and_auto_provision():
    skill = (ROOT / "skills/scion-factory-bootstrap/SKILL.md").read_text()
    for needle in ("setup-gke-hub.sh", "provision", "infrastructure.runtime", "kubernetes"):
        assert needle in skill, f"missing {needle!r} in SKILL.md"

    gke_runbook = (ROOT / "docs/gke-setup-runbook.md").read_text()
    for needle in ("Workload Identity", "scion-worker", "scion-deployer", "install-k8s-control-plane.py", "/health", "setup-gke-hub.sh"):
        assert needle in gke_runbook, f"missing {needle!r} in docs/gke-setup-runbook.md"

    readme = (ROOT / "README.md").read_text()
    assert "GKE" in readme and "scion-factory provision" in readme

    lessons = (ROOT / "docs/lessons-learned.md").read_text()
    assert "## L32." in lessons


def _strip_fenced_code_blocks(markdown: str) -> str:
    lines = []
    in_fence = False
    for line in markdown.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence:
            lines.append(line)
    return "\n".join(lines)


def _github_heading_slug(heading_text: str) -> str:
    cleaned = re.sub(r"`([^`]*)`", r"\1", heading_text.strip().lower())
    cleaned = re.sub(r"[^\w\s-]", "", cleaned)
    cleaned = re.sub(r"\s+", "-", cleaned)
    return cleaned


def test_readme_follows_google_dev_style_and_ste100_without_content_loss():
    readme = (ROOT / "README.md").read_text()
    prose = _strip_fenced_code_blocks(readme)

    # 1. Google Developer Style + ASD-STE100: no first-person plural or contractions in prose
    assert not re.search(r"\b(we|our|ourselves)\b", prose, flags=re.IGNORECASE), (
        "README prose must use second-person ('you'), not first-person plural"
    )
    contractions = re.findall(
        r"\b(?:don't|doesn't|isn't|aren't|can't|won't|wouldn't|shouldn't|couldn't|didn't|wasn't|weren't|haven't|hasn't|it's|that's|what's|there's|you're|they're|we're)\b",
        prose,
        flags=re.IGNORECASE,
    )
    assert not contractions, f"ASD-STE100 forbids contractions in prose; found: {sorted(set(contractions))}"

    # 2. ASD-STE100 sentence length: descriptive/procedural prose sentences stay <= 25 words
    # (joining wrapped paragraph lines and treating inline `code` spans and [label](url) links as single tokens)
    paragraphs = []
    buf = []
    for raw_line in prose.splitlines():
        s = raw_line.strip()
        if s.startswith(">"):
            s = re.sub(r"^>\s*", "", s)
            if re.match(r"^\[![A-Z]+\]$", s):
                if buf:
                    paragraphs.append(" ".join(buf))
                    buf = []
                continue
        if not s or s.startswith(("#", "|", "├──", "└──", "│")):
            if buf:
                paragraphs.append(" ".join(buf))
                buf = []
            continue
        if re.match(r"^(?:[-*]|\d+\.)\s+", s):
            if buf:
                paragraphs.append(" ".join(buf))
            buf = [re.sub(r"^(?:[-*]|\d+\.)\s+", "", s)]
        else:
            buf.append(s)
    if buf:
        paragraphs.append(" ".join(buf))

    overlong = []
    for para in paragraphs:
        normalized = re.sub(r"`[^`]+`", "CODE_TOKEN", para)
        normalized = re.sub(r"\[([^\]]+)\]\([^)]+\)", "LINK_TOKEN", normalized)
        for sent in re.split(r"(?<=[.!?])\s+", normalized):
            words = re.findall(r"[A-Za-z0-9_'-]+", sent)
            if len(words) > 25:
                overlong.append((len(words), sent))
    assert not overlong, f"ASD-STE100 sentence length (>25 words) exceeded: {overlong[:5]}"

    # 3. Zero content loss: all core capabilities, topologies, presets, artifacts, and runbooks remain documented
    required_items = (
        "docs/gke-setup-runbook.md",
        "docs/setup-runbook.md",
        "docs/operations-runbook.md",
        "docs/grill-me-playbook.md",
        "docs/lessons-learned.md",
        "docs/verification-and-eval.md",
        "docs/telemetry/README.md",
        "skills/scion-factory-bootstrap/SKILL.md",
        "scripts/hub_client.py",
        "scripts/provision_factory.py",
        "scripts/setup-gke-hub.sh",
        "scripts/install-k8s-control-plane.py",
        "starter-service.factory.yaml",
        "psearch.factory.yaml",
        "acme-airlines.factory.yaml",
        "knowledge-plane/INDEX.md",
        "kp-check.py",
        "state/LIVE.md",
        "backlog/issues.json",
        "enable-git",
        "add-role",
        "L32",
        "## Next steps",
    )
    for item in required_items:
        assert item in readme, f"README lost or omitted required reference: {item!r}"

    # 4. Link and anchor integrity: every local path and #anchor in README resolves
    headings = {
        _github_heading_slug(m.group(1))
        for m in re.finditer(r"^#{1,6}\s+(.+)$", prose, flags=re.MULTILINE)
    }
    for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", prose):
        if target.startswith(("http://", "https://", "mailto:")):
            continue
        if target.startswith("#"):
            anchor = target[1:]
            assert anchor in headings, f"Broken internal anchor #{anchor} in README.md (available: {sorted(headings)})"
        else:
            rel_path = target.split("#", 1)[0]
            assert (ROOT / rel_path).exists(), f"Broken relative path {rel_path!r} in README.md"

