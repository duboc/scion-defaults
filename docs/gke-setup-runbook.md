# GKE Setup Runbook

This guide covers setting up Scion Hub on a Full GKE Multi-Hub HA architecture or a Hybrid VM+GKE target.

## Prerequisites & Environment

Before starting, configure your `config/factory.env` with the following GKE keys:
- `HUB_URL`: URL of the Scion Hub API.
- `HUB_IAP_AUDIENCE`: IAP Client ID if using IAP mode.
- `HUB_TRANSPORT_MODE`: Either `iap` or `serverless`.
- `AGENT_RUNTIME=kubernetes`: Instructs tools to use the K8s execution mode.
- `RUNTIME_PROFILE`: Leave empty `""` for Full GKE, or set to `"gke"` for Hybrid.
- `HUB_GKE_CLUSTER` / `HUB_GKE_LOCATION`: Your GKE cluster name and location.
- `HUB_K8S_NAMESPACE`: Kubernetes namespace for workloads (e.g. `scion-hub`).
- `IMAGE_REGISTRY`: Artifact Registry path for harness images.
- `WORKER_SA_NAME`: GCP Service Account for agents (default `agent-worker`).
- `HUB_SA`: Hub service account.

## Option A: Full GKE Multi-Hub HA Scion

For a completely serverless/managed infrastructure, use the Terraform configurations (`deploy/terraform/` — `configurations/shared-infra` + `configurations/hub`).

1. Plan the installation:
   ```bash
   DRY_RUN=1 scripts/setup-gke-hub.sh --tier terraform-ha --scion-repo <dir> --harnesses claude,antigravity --dry-run
   ```
2. Review the generated `.tfvars` and Terraform plan output.
3. Apply the installation:
   ```bash
   scripts/setup-gke-hub.sh --tier terraform-ha --scion-repo <dir> --harnesses claude,antigravity --approve-apply
   ```
4. Verify connectivity using IAP `Proxy-Authorization` and the `/health` endpoint.

## Option B: Hybrid VM + GKE Tier

If you are using a single-VM Hub but targeting GKE for agent workloads (`gke_target`), you'll connect the VM to the cluster. Set `RUNTIME_PROFILE=gke` in your environment.

1. Plan the installation:
   ```bash
   DRY_RUN=1 scripts/setup-gke-hub.sh --tier hybrid-gke --scion-repo <dir> --harnesses claude,antigravity --dry-run
   ```
2. Apply the installation:
   ```bash
   scripts/setup-gke-hub.sh --tier hybrid-gke --scion-repo <dir> --harnesses claude,antigravity --approve-apply
   ```

## Multi-arch Artifact Registry Image Builds

Both topology options build multi-arch container images (`core-base`, `scion-base`, `claude`, `antigravity`, `opencode`) using Cloud Build and push them to your `IMAGE_REGISTRY` with immutable tags.

## Sandbox IAM & Workload Identity KSA Mapping

When setting up the environment, we map Google Service Accounts to Kubernetes Service Accounts (KSA). Use the sandbox setup script to configure this:

```bash
AGENT_RUNTIME=kubernetes scripts/setup-sandbox.sh
```

This binds Workload Identity to:
- `scion-worker` KSA for running agent tasks (default `agent-worker` GCP SA).
- `scion-factory-control` KSA for factory coordination.
- `scion-deployer` KSA for deploying the target service.

You can configure identity in `assign` vs `passthrough` modes depending on your security perimeter.

## Deterministic Factory Provisioning

Once the cluster and toolkit are ready, perform automated provisioning.

Review the plan:
```bash
./scripts/scion-factory provision factory.yaml --kit <dir> --env config/factory.env
```

Apply and start agents:
```bash
./scripts/scion-factory provision factory.yaml --kit <dir> --env config/factory.env --apply --go
```

## Control Plane Installation

To install the K8s CronJob control plane on the Kubernetes cluster, use the provided script:

1. Render and review manifests:
   ```bash
   python3 scripts/install-k8s-control-plane.py --namespace <ns> --dry-run --out manifests.yaml
   ```
2. Apply manifests:
   ```bash
   python3 scripts/install-k8s-control-plane.py --namespace <ns> --apply
   ```

This sets up `scion-factory-sync` and `scion-factory-watchdog` CronJobs. Monitor them via:
```bash
kubectl get cronjobs,jobs,pods -n <ns>
```
Their state is stored persistently in the Firestore `factory_control_state` bookmark.
