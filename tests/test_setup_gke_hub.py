import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

def make_fake_scion_upstream(tmp_path):
    scion_repo = tmp_path / "scion-upstream"
    scion_repo.mkdir()
    (scion_repo / "image-build" / "scripts").mkdir(parents=True)
    (scion_repo / "deploy" / "terraform" / "configurations" / "shared-infra").mkdir(parents=True)
    (scion_repo / "deploy" / "terraform" / "configurations" / "hub").mkdir(parents=True)
    (scion_repo / "scripts" / "single-node-vm").mkdir(parents=True)
    return scion_repo

def test_setup_gke_hub_dry_run_plans_cloud_build_and_terraform_without_mutating(tmp_path):
    scion_repo = make_fake_scion_upstream(tmp_path)
    env = {
        **os.environ,
        "DRY_RUN": "1",
        "HUB_PROJECT": "demo-hub-proj",
        "HUB_REGION": "us-central1",
        "HUB_NAME": "prod",
        "PO_EMAIL": "admin@example.com",
    }
    res = subprocess.run(
        ["bash", str(ROOT / "scripts/setup-gke-hub.sh"), "--tier", "terraform-ha",
         "--scion-repo", str(scion_repo), "--harnesses", "claude,antigravity"],
        cwd=ROOT, env=env, capture_output=True, text=True,
    )
    out = res.stdout + res.stderr
    assert res.returncode == 0, out
    assert "build-images.sh --builder cloud-build" in out
    assert "configurations/shared-infra" in out
    assert "configurations/hub" in out
    assert "GOOGLE_CLOUD_PROJECT=demo-hub-proj" in out
    assert "terraform apply" not in out  # never applies without --approve-apply
