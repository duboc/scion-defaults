import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_teardown_env_dry_run_plans_all_targets(tmp_path):
    res = subprocess.run(
        [
            "bash",
            str(ROOT / "scripts/teardown-env.sh"),
            "--project", "demo-scion-x",
            "--prefix", "tfhx",
            "--hub-name", "main",
            "--gke-project", "shared-gke-host",
            "--gke-cluster", "shared-autopilot",
            "--gke-region", "us-central1",
            "--alloydb", "alloy-proj:us-central1:alloy-cluster:alloy-primary",
            "--walgit-url", "https://walgit.example.run.app",
            "--walgit-repos", "org/demo-repo",
            "--farmtable-ids", "11111111-1111-1111-1111-111111111111,22222222-2222-2222-2222-222222222222",
            "--spanner", "spanner-proj:spanner-inst:farmtable-db",
            "--firestore-project", "fs-proj",
            "--cross-iap-services", "wal-proj:us-central1:walgit,ft-proj:us-central1:farmtable",
            "--shared-gcs-bucket", "demo-scion-x-shared",
            "--delete-project",
            "--dry-run",
        ],
        cwd=ROOT,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
    )
    out = res.stdout + res.stderr
    assert res.returncode == 0, out
    assert "DELETE https://walgit.example.run.app/org/demo-repo/api" in out
    assert "DELETE FROM WatchEventsLog WHERE CollectionId IN ('11111111-1111-1111-1111-111111111111', '22222222-2222-2222-2222-222222222222')" in out
    assert "firestore delete collections=[11111111-1111-1111-1111-111111111111,22222222-2222-2222-2222-222222222222] project=fs-proj" in out
    assert "kubectl delete namespace tfhx-main --ignore-not-found" in out
    assert "gcloud alloydb instances update alloy-primary" in out
    assert "--allowed-psc-projects" in out
    assert "gcloud storage rm -r gs://demo-scion-x-shared --quiet" in out
    assert "gcloud projects delete demo-scion-x --quiet" in out


def test_teardown_env_apply_executes_with_mock_clis(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log_file = tmp_path / "calls.log"
    (fake_bin / "gcloud").write_text(
        '#!/usr/bin/env bash\n'
        f'echo "gcloud $*" >> "{log_file}"\n'
        'case "$*" in\n'
        '  *"projects describe"* ) echo "999888777" ;;\n'
        '  *"alloydb instances describe"* ) echo "111222;999888777;333444" ;;\n'
        'esac\n'
        'exit 0\n'
    )
    (fake_bin / "gcloud").chmod(0o755)
    (fake_bin / "kubectl").write_text(
        f'#!/usr/bin/env bash\necho "kubectl $*" >> "{log_file}"\nexit 0\n'
    )
    (fake_bin / "kubectl").chmod(0o755)

    res = subprocess.run(
        [
            "bash",
            str(ROOT / "scripts/teardown-env.sh"),
            "--project", "demo-scion-x",
            "--prefix", "tfhx",
            "--gke-project", "shared-gke-host",
            "--gke-cluster", "shared-autopilot",
            "--alloydb", "alloy-proj:us-central1:alloy-cluster:alloy-primary",
            "--cross-iap-services", "wal-proj:us-central1:walgit",
            "--delete-project",
            "--approve-apply",
        ],
        cwd=ROOT,
        env={**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}"},
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, res.stdout + res.stderr
    calls = log_file.read_text()
    assert "kubectl delete namespace tfhx-main --ignore-not-found" in calls
    assert "--allowed-psc-projects=111222,333444" in calls
    assert "iap web remove-iam-policy-binding" in calls
    assert "gcloud projects delete demo-scion-x --quiet" in calls
