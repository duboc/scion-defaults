"""Human on the loop (spec §7): decision rights per autonomy level, one PO inbox, a daily digest on GitHub, eval findings
as issues.

Every decision that waits for the PO is a GitHub issue labeled `status:po-decision` and `factory:<slug>`; digests and
"tell" notices are GitHub comments, because the watchdog turns every `scion message user:<PO>` into an ERROR alert.
A factory without Git keeps today's behaviour (spec §3): the lead escalates by Scion message.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import scion_factory  # noqa: E402

PRESET = ROOT / "presets" / "starter-service.factory.yaml"
SLUG = "pulse-ledger"


def _prompt(out, role):
    return (out / "templates" / role / "system-prompt.md").read_text()


def _section(text, header):
    return text.split(f"## {header}\n", 1)[1].split("\n## ", 1)[0]


def _compile(tmp_path, spec):
    out = tmp_path / "kit"
    scion_factory.compile_factory_boilerplate(spec, out)
    return out


def _spec(**team):
    spec = scion_factory.load_spec(PRESET)
    spec["team"].update(team)
    return spec


@pytest.fixture
def kit(tmp_path):
    return _compile(tmp_path, _spec())


def _columns(table):
    """The decision-rights table's one body row as {header: cell}."""
    rows = [line for line in table.splitlines() if line.startswith("|")]
    headers = [c.strip() for c in rows[0].strip("|").split("|")]
    cells = [c.strip() for c in rows[2].strip("|").split("|")]
    return dict(zip(headers, cells))


def test_standard_lead_waits_for_po_on_scope_cost_iam_adr(kit):
    lead = _prompt(kit, "lead")
    for needle in ("status:po-decision", "Scope changes", "IAM", "ADR", "only the PO closes"):
        assert needle in lead
    waits = _columns(_section(lead, "Decision rights"))["Waits for the PO"]
    for needle in ("Scope changes", "IAM", "ADR", "only the PO closes"):
        assert needle in waits


def test_render_decision_rights_matches_the_spec_table():
    table = scion_factory.render_decision_rights(_spec())
    cols = _columns(table)
    assert list(cols) == ["Agents decide alone", "Agents decide and tell the PO", "Waits for the PO"]
    assert "How to implement" in cols["Agents decide alone"]
    assert "evidence comment" in cols["Agents decide and tell the PO"]


def test_po_inbox_is_one_label_query_and_messages_link_the_issue(kit):
    lead = _prompt(kit, "lead")
    inbox = _section(lead, "PO inbox")
    assert f"gh issue create -R example-org/{SLUG}" in inbox
    assert f"--label status:po-decision --label factory:{SLUG}" in inbox
    assert "recommendation" in inbox
    assert "scion message user:<PO_USER>" in inbox and "<issue URL>" in inbox
    assert "24 hours" in _section(lead, "Operating Loop")          # the reminder is a wake-up step


def test_digest_and_tell_go_to_github_not_messages(kit):
    lead = _prompt(kit, "lead")
    assert "digest" in lead and "gh issue comment" in lead
    assert "ready for acceptance" not in lead           # no per-issue PO message any more
    assert f"{SLUG} digest" in _section(lead, "Daily digest")
    shared = (kit / "templates" / "_shared" / "agents.md").read_text()
    assert "and messages the PO" not in shared
    assert "status:po-decision" in shared


def test_supervised_waits_for_status_approved(tmp_path):
    lead = _prompt(_compile(tmp_path, _spec(autonomy="supervised")), "lead")
    assert "status:approved" in lead and "status:po-review" in lead


def test_standard_has_no_plan_approval_step(kit):
    assert "status:po-review" not in _prompt(kit, "lead")


def test_autonomous_tells_scope_instead_of_asking(tmp_path):
    lead = _prompt(_compile(tmp_path, _spec(autonomy="autonomous")), "lead")
    cols = _columns(_section(lead, "Decision rights"))
    assert "Scope changes" in cols["Agents decide and tell the PO"]
    assert "Scope changes" not in cols["Waits for the PO"]
    assert "ADR" not in cols["Waits for the PO"]
    assert "IAM" in cols["Waits for the PO"] and "only the PO closes" in cols["Waits for the PO"]


def test_eval_lead_files_findings_as_issues_and_never_messages_build_agents(kit):
    ev = _prompt(kit, "eval-lead")
    assert "eval-finding" in ev and "factory:" in ev
    findings = _section(ev, "Evaluation findings")
    assert f"--label eval-finding --label factory:{SLUG}" in findings
    assert "Never message build agents" in findings
    assert "factory:<slug>" not in ev                  # the protocol's placeholder is filled in


def test_seed_script_creates_inbox_labels_and_digest_issue_in_dry_run_only_prints(kit, tmp_path):
    seed_path = kit / "scripts" / "seed-github-waves.sh"
    seed = seed_path.read_text()
    for label in ("status:po-decision", "status:po-review", "status:approved", "digest", "eval-finding",
                  f"factory:{SLUG}"):
        assert f'"{label}"' in seed, label
    assert "gh issue list" in seed and "--search" in seed and "gh issue pin" in seed
    assert subprocess.run(["bash", "-n", str(seed_path)], capture_output=True).returncode == 0

    bindir = tmp_path / "bin"
    bindir.mkdir()
    calls = tmp_path / "gh-calls"
    (bindir / "gh").write_text(f'#!/bin/sh\necho "$@" >> "{calls}"\n')
    (bindir / "gh").chmod(0o755)
    env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", "DRY_RUN": "1"}
    res = subprocess.run(["bash", str(seed_path)], env=env, capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    assert "status:po-decision" in res.stdout
    assert f"{SLUG} digest" in res.stdout
    assert not calls.exists(), calls.read_text()


def test_seed_script_creates_the_digest_only_when_absent(kit, tmp_path):
    """With DRY_RUN=0 and an existing digest, the script creates no second digest issue."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    calls = tmp_path / "gh-calls"
    (bindir / "gh").write_text(
        f'#!/bin/sh\necho "$@" >> "{calls}"\n'
        'if [ "$1 $2" = "issue list" ]; then echo 7; fi\n'
    )
    (bindir / "gh").chmod(0o755)
    env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", "DRY_RUN": "0"}
    res = subprocess.run(["bash", str(kit / "scripts" / "seed-github-waves.sh")], env=env,
                         capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    log = calls.read_text()
    assert f"{SLUG} digest" in log                     # it searched for the digest
    assert f"--title {SLUG} digest" not in log         # and created none
    assert "issue pin" not in log


def test_kit_env_records_the_autonomy_level(tmp_path):
    env = (_compile(tmp_path, _spec(autonomy="supervised")) / "config" / "factory.env.example").read_text()
    assert "AUTONOMY=supervised" in env


def test_no_git_kit_keeps_message_escalation(tmp_path):
    spec = _spec()
    spec["vcs"] = {"enabled": False}
    out = _compile(tmp_path, spec)
    lead = _prompt(out, "lead")
    assert "status:po-decision" not in lead
    assert "gh issue" not in lead
    assert "scion message user:<PO_USER>" in lead
    ev = _prompt(out, "eval-lead")
    assert "eval-finding" not in ev and "gh issue" not in ev
    assert "scion message user:<PO_USER>" in ev
    shared = (out / "templates" / "_shared" / "agents.md").read_text()
    assert "status:po-decision" not in shared
    assert not (out / "scripts" / "seed-github-waves.sh").exists()
