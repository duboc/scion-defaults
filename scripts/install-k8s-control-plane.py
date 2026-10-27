import argparse
import json
import os
import subprocess
import sys

def render_manifests(
    namespace: str,
    hub_url: str,
    factory_projects: str,
    sandbox_project: str,
    firestore_project: str,
    firestore_database: str = "(default)",
    ksa_name: str = "scion-factory-control",
    gsa_email: str = "",
    image: str = "python:3.12-slim",
    iap_audience: str = ""
) -> list[dict]:
    
    script_dir = os.path.dirname(os.path.abspath(__file__))
    scripts_to_include = ["hub_client.py", "factory_store.py", "sync-factory-agents.py", "factory-watchdog.py"]
    
    configmap_data = {}
    for script in scripts_to_include:
        path = os.path.join(script_dir, script)
        if os.path.exists(path):
            with open(path, "r") as f:
                configmap_data[script] = f.read()
        else:
            configmap_data[script] = "# not found during render"
            
    manifests = []
    
    sa = {
        "apiVersion": "v1",
        "kind": "ServiceAccount",
        "metadata": {
            "name": ksa_name,
            "namespace": namespace,
            "annotations": {}
        }
    }
    if gsa_email:
        sa["metadata"]["annotations"]["iam.gke.io/gcp-service-account"] = gsa_email
    manifests.append(sa)

    cm = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": "scion-factory-control-scripts",
            "namespace": namespace
        },
        "data": configmap_data
    }
    manifests.append(cm)

    def make_cronjob(name, schedule, command, extra_env=None):
        env = [
            {"name": "HUB_URL", "value": hub_url},
            {"name": "FACTORY_PROJECTS", "value": factory_projects},
            {"name": "SANDBOX_PROJECT", "value": sandbox_project},
            {"name": "FIRESTORE_PROJECT", "value": firestore_project},
            {"name": "FIRESTORE_DATABASE", "value": firestore_database},
            {"name": "AGENT_RUNTIME", "value": "kubernetes"},
            {"name": "SCION_HUB_TOKEN", "valueFrom": {"secretKeyRef": {"name": "scion-factory-hub-auth", "key": "token"}}}
        ]
        if iap_audience:
            env.append({"name": "HUB_IAP_AUDIENCE", "value": iap_audience})
        if extra_env:
            env.extend(extra_env)
            
        return {
            "apiVersion": "batch/v1",
            "kind": "CronJob",
            "metadata": {
                "name": name,
                "namespace": namespace
            },
            "spec": {
                "schedule": schedule,
                "concurrencyPolicy": "Forbid",
                "jobTemplate": {
                    "spec": {
                        "template": {
                            "spec": {
                                "serviceAccountName": ksa_name,
                                "containers": [{
                                    "name": "worker",
                                    "image": image,
                                    "command": command,
                                    "env": env,
                                    "volumeMounts": [{
                                        "name": "scripts",
                                        "mountPath": "/opt/scion-factory"
                                    }]
                                }],
                                "volumes": [{
                                    "name": "scripts",
                                    "configMap": {
                                        "name": "scion-factory-control-scripts"
                                    }
                                }],
                                "restartPolicy": "Never"
                            }
                        }
                    }
                }
            }
        }

    sync_cj = make_cronjob(
        "scion-factory-sync", 
        "*/1 * * * *", 
        ["python3", "/opt/scion-factory/sync-factory-agents.py"]
    )
    manifests.append(sync_cj)
    
    watchdog_cj = make_cronjob(
        "scion-factory-watchdog", 
        "*/5 * * * *", 
        ["python3", "/opt/scion-factory/factory-watchdog.py", "--apply"],
        extra_env=[{"name": "WATCHDOG_STATE_BACKEND", "value": "firestore"}]
    )
    manifests.append(watchdog_cj)

    return manifests

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--namespace", default="default")
    parser.add_argument("--hub-url", default="")
    parser.add_argument("--factory-projects", default="")
    parser.add_argument("--sandbox-project", default="")
    parser.add_argument("--firestore-project", default="")
    parser.add_argument("--gsa-email", default="")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    manifests = render_manifests(
        namespace=args.namespace,
        hub_url=args.hub_url or os.environ.get("HUB_URL", ""),
        factory_projects=args.factory_projects or os.environ.get("FACTORY_PROJECTS", ""),
        sandbox_project=args.sandbox_project or os.environ.get("SANDBOX_PROJECT", ""),
        firestore_project=args.firestore_project or os.environ.get("FIRESTORE_PROJECT", ""),
        gsa_email=args.gsa_email or os.environ.get("GSA_EMAIL", "")
    )
    
    json_docs = "\n---\n".join(json.dumps(m) for m in manifests)
    if args.out:
        with open(args.out, "w") as f:
            f.write(json_docs)
            
    is_dry_run = args.dry_run or not args.apply
    
    if is_dry_run:
        print(json_docs)
    else:
        # Apply token secret first
        token = os.environ.get("SCION_HUB_TOKEN")
        if token:
            secret_cmd = [
                "kubectl", "create", "secret", "generic", "scion-factory-hub-auth",
                "--namespace", args.namespace,
                f"--from-literal=token={token}",
                "--dry-run=client", "-o", "json"
            ]
            secret_json = subprocess.check_output(secret_cmd, text=True)
            subprocess.run(["kubectl", "apply", "-f", "-"], input=secret_json, text=True, check=True)
            
        # Apply manifests
        subprocess.run(["kubectl", "apply", "-f", "-"], input=json_docs, text=True, check=True)

if __name__ == "__main__":
    main()
