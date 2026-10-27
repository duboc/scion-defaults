import os
import subprocess
import sys
from pathlib import Path
import json
import importlib.util

from hub_client import HubClient

def _load_env(env_file) -> dict:
    env_file = Path(env_file)
    env = {}
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                env[k] = v
    for k, v in os.environ.items():
        if v:
            env[k] = v
    return env

def _load_hub_preflight():
    spec = importlib.util.spec_from_file_location("hub_preflight", Path(__file__).parent / "hub-preflight.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

ROOT_DIR = Path(__file__).resolve().parent.parent

def provision_factory(spec: dict, kit_dir, env_file, seed_repo = None, apply: bool = False, send_go: bool = False) -> dict:
    try:
        kit_dir = Path(kit_dir)
        env_file = Path(env_file)
        if seed_repo: seed_repo = Path(seed_repo)
        eff_env = _load_env(env_file)
        hub_url = eff_env.get("HUB_URL") or "http://127.0.0.1:8000"
        token = eff_env.get("HUB_TOKEN", "")
        client = HubClient(hub_url, token) if token else HubClient.from_env(eff_env)
        
        phases = []
        build_pid = spec.get("project", {}).get("slug", "")
        eval_pid = spec.get("project", {}).get("eval_slug", "")
        
        # 1. preflight
        factory_projects = eff_env.get("FACTORY_PROJECTS", "").split(",") if eff_env.get("FACTORY_PROJECTS") else []
        if eff_env.get("HUB_URL"):
            hub_preflight = _load_hub_preflight()
            report = hub_preflight.build_api_report(
                client,
                eff_env.get("PO_EMAIL", ""),
                [build_pid, eval_pid],
                [],
                [],
                []
            )
            if report.get("projects"):
                factory_projects = list(set(factory_projects + [p["slug"] for p in report["projects"]]))
            phases.append({"phase": "preflight", "ok": True})
        else:
            phases.append({"phase": "preflight", "ok": True, "local": True})

        # 2. sandbox
        r_sb = subprocess.run(["bash", "scripts/setup-sandbox.sh"], cwd=ROOT_DIR, env={**os.environ, "FACTORY_ENV": str(env_file), "DRY_RUN": "0" if apply else "1"}, capture_output=True, text=True)
        if r_sb.returncode == 0:
            phases.append({"phase": "sandbox", "ok": True})
        else:
            phases.append({"phase": "sandbox", "ok": False, "stderr": r_sb.stderr})
            return {"ok": False, "apply": apply, "phases": phases}

        # 3. seed_repo
        if seed_repo:
            r_seed = subprocess.run(["bash", str(ROOT_DIR / "scripts" / "seed-github-waves.sh")], cwd=seed_repo, env={**os.environ, "DRY_RUN": "0" if apply else "1"}, capture_output=True, text=True)
            if r_seed.returncode == 0:
                phases.append({"phase": "seed_repo", "ok": True})
            else:
                phases.append({"phase": "seed_repo", "ok": False, "stderr": r_seed.stderr})
                return {"ok": False, "apply": apply, "phases": phases}
        else:
            phases.append({"phase": "seed_repo", "ok": True, "skipped": True})

        build_pid_id = build_pid
        eval_pid_id = eval_pid
        # 4. projects
        if apply:
            build_pid_id = client.create_project(build_pid).get("id", build_pid)
            eval_pid_id = client.create_project(eval_pid).get("id", eval_pid)
        phases.append({"phase": "projects", "ok": True})

        # 5. identity_and_profile
        import scion_factory
        runtime = scion_factory.runtime_mode(spec)
        sandbox_project = eff_env.get("SANDBOX_PROJECT", "")
        worker_sa_name = eff_env.get("WORKER_SA_NAME", spec.get("infrastructure", {}).get("worker_sa_name", ""))
        deployer_sa_name = eff_env.get("DEPLOYER_SA_NAME", "")
        runtime_profile = eff_env.get("RUNTIME_PROFILE") or spec.get("infrastructure", {}).get("runtime_profile", "")
        print("DEBUG:", eff_env.get("RUNTIME_PROFILE"), spec.get("infrastructure", {}).get("runtime_profile", ""), file=sys.stderr)

        worker_sa_email = f"{worker_sa_name}@{sandbox_project}.iam.gserviceaccount.com"
        deployer_sa_email = f"{deployer_sa_name}@{sandbox_project}.iam.gserviceaccount.com"

        if runtime == "kubernetes" and apply:
            for pid in (build_pid_id, eval_pid_id):
                client.register_project_service_account(pid, worker_sa_email, sandbox_project)
                if scion_factory.deploy_mode(spec) != "none" and deployer_sa_name:
                    client.register_project_service_account(pid, deployer_sa_email, sandbox_project)
                
                patch = {
                    "defaultGCPIdentityMode": eff_env.get("GCP_IDENTITY_DEFAULT", "assign"),
                    "defaultGCPIdentityServiceAccountID": worker_sa_email,
                }
                if runtime_profile:
                    patch["activeProfile"] = runtime_profile
                    if patch["defaultGCPIdentityMode"] == "assign":
                        patch["defaultGCPIdentityServiceAccountIDByProfile"] = {runtime_profile: worker_sa_email}
                client.put_project_settings(pid, patch)
        phases.append({"phase": "identity_and_profile", "ok": True})

        # 6. shared_dirs_and_kp
        if apply:
            for pid in (build_pid_id, eval_pid_id):
                try:
                    client.request("PUT", f"/api/v1/projects/{pid}/shared-dirs/scratchpad", body={})
                except:
                    client.request("POST", f"/api/v1/projects/{pid}/shared-dirs", body={"name": "scratchpad"})
                try:
                    client.request("PUT", f"/api/v1/projects/{pid}/shared-dirs/knowledge-plane", body={"readOnly": True})
                except:
                    client.request("POST", f"/api/v1/projects/{pid}/shared-dirs", body={"name": "knowledge-plane", "readOnly": True})
            
            kp_src_dir = (seed_repo / spec.get("monorepo", {}).get("subdir", "") / "knowledge-plane") if seed_repo and spec.get("monorepo", {}).get("subdir") else (kit_dir / "knowledge-plane")
            if not kp_src_dir.exists() and seed_repo:
                kp_src_dir = seed_repo / "knowledge-plane"
            client.sync_shared_dir(build_pid_id, "knowledge-plane", str(kp_src_dir), apply=True)
            client.sync_shared_dir(eval_pid_id, "knowledge-plane", str(kp_src_dir), apply=True)
        phases.append({"phase": "shared_dirs_and_kp", "ok": True})

        # 7. harness_and_templates
        if spec.get("monorepo", {}).get("subdir"):
            cmd_harness = [sys.executable, str(ROOT_DIR / "scripts" / "make-factory-harness-configs.py")]
            if eff_env.get("HUB_URL"):
                cmd_harness.append("--via-api")
            if apply:
                cmd_harness.append("--apply")
            harness_env = {**os.environ, "FACTORY_ENV": str(env_file)}
            subprocess.run(cmd_harness, cwd=ROOT_DIR, env=harness_env, check=True, stdout=sys.stderr)

        b_roles = ",".join([r.get("role", "") for r in spec.get("team", {}).get("build_roles", [])])
        e_roles = ",".join([r.get("role", "") for r in spec.get("team", {}).get("eval_roles", [])])
        
        for pid, roles_str in [(build_pid, b_roles), (eval_pid, e_roles)]:
            if not roles_str: continue
            if not apply and pid not in factory_projects:
                continue
            cmd_push = [sys.executable, str(ROOT_DIR / "scripts" / "push-templates.py"), "--project", pid, "--roles", roles_str, "--templates", str(kit_dir / "templates")]
            if eff_env.get("HUB_URL"):
                cmd_push.append("--direct")
            if apply:
                cmd_push.append("--apply")
            # pass HUB_URL in env so push-templates can find it since we don't pass --env
            push_env = {**os.environ, "HUB_URL": eff_env.get("HUB_URL", ""), "HUB_TOKEN": eff_env.get("HUB_TOKEN", ""), "FACTORY_ENV": str(env_file)}
            subprocess.run(cmd_push, cwd=ROOT_DIR, env=push_env, check=True, stdout=sys.stderr)
        phases.append({"phase": "harness_and_templates", "ok": True})

        # 8. control_plane
        if runtime == "kubernetes":
            cmd_cp = [sys.executable, str(ROOT_DIR / "scripts" / "install-k8s-control-plane.py")]
            if apply:
                cmd_cp.append("--apply")
            else:
                cmd_cp.append("--dry-run")
            if eff_env.get("HUB_K8S_NAMESPACE"):
                cmd_cp.extend(["--gsa-email", worker_sa_email])
            r_cp = subprocess.run(cmd_cp, cwd=ROOT_DIR, env={**os.environ, "FACTORY_PROJECTS": eff_env.get("FACTORY_PROJECTS", f"{build_pid},{eval_pid}")}, capture_output=True, text=True)
            if r_cp.returncode == 0:
                phases.append({"phase": "control_plane", "ok": True})
            else:
                if "kubectl: command not found" in r_cp.stderr or not eff_env.get("HUB_K8S_NAMESPACE"):
                     phases.append({"phase": "control_plane", "ok": True, "rendered_only": True})
                else:
                     phases.append({"phase": "control_plane", "ok": False, "stderr": r_cp.stderr})
                     return {"ok": False, "apply": apply, "phases": phases}
        else:
            phases.append({"phase": "control_plane", "ok": True})

        # 9. schedules_and_leads
        if apply:
            scheds = client.request("GET", f"/api/v1/projects/{build_pid_id}/schedules")[1]
            sched_name = f"{build_pid}-lead-heartbeat"
            if not any(s.get("name") == sched_name for s in scheds):
                client.request("POST", f"/api/v1/projects/{build_pid_id}/schedules", body={"name": sched_name, "interval": "*/5 * * * *", "task": "heartbeat"})
            
            c_body = {"name": "lead", "template": "lead", "task": "Wake up."}
            if runtime_profile:
                c_body["profile"] = runtime_profile
            client.ensure_agent_started(build_pid_id, "lead", create_body=c_body, wait_sec=5, poll_sec=0.05)

            e_body = {"name": "eval-lead", "template": "eval-lead", "task": "Wake up."}
            if runtime_profile:
                e_body["profile"] = runtime_profile
            client.ensure_agent_started(eval_pid_id, "eval-lead", create_body=e_body, wait_sec=5, poll_sec=0.05)

            if send_go:
                client.request("POST", f"/api/v1/projects/{build_pid_id}/agents/lead/message", body={"message": "GO: start Wave 1 execution per knowledge-plane/index.md."})
                
        phases.append({"phase": "schedules_and_leads", "ok": True})

        return {"ok": True, "apply": apply, "runtime": runtime, "factory_projects": [build_pid_id, eval_pid_id], "phases": phases}
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"ok": False, "error": str(e), "phases": []}
