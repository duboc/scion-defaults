# GKE Setup Runbook

This guide covers setting up Scion Hub on a Full GKE HA architecture or a Hybrid VM+GKE target.

## Full GKE HA Scion Hub

For a completely serverless/managed infrastructure, use the Terraform configurations in `deploy/terraform/`. This setups up the GKE cluster, Artifact Registry for harness images, and necessary service accounts.

1. Configure Google Cloud Workload Identity.
2. Ensure you have built and pushed the harness images to Artifact Registry.

## Hybrid VM+GKE

If you are using a single-VM Hub but targeting GKE for agent workloads (`gke_target`), you'll connect the VM to the cluster.

## Workload Identity and Service Accounts

When setting up GKE, we map Google Service Accounts to Kubernetes Service Accounts (KSA).
The default identity mapping includes:
- `scion-worker` KSA for running agent tasks.
- `scion-deployer` KSA for deploying the target service.

These rely on Workload Identity to securely provide credentials to the agent pods without mounting static keys.

## Control Plane Installation

To install the factory control plane on the Kubernetes cluster, use the provided script:

```bash
python3 scripts/install-k8s-control-plane.py --namespace scion-hub --apply
```

This sets up the 1m `CronJob` `scion-factory-sync` and 5m `CronJob` `scion-factory-watchdog` for monitoring, backed by the Firestore `factory_control_state`.

## Health Checks

Note that when running on Cloud Run, the health check endpoint is `/health`.
On GKE, Autopilot may return a cold 503 response; the Scion Hub client automatically polls on `ensure_agent_started` to handle this cold start gracefully.
