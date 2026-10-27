# Universal Auto-Factory & Full GKE Scion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable any user and coding agent to automatically investigate a repository, optionally install a full GKE-backed Scion Hub, and deterministically provision and operate a self-healing multi-agent software factory running on GKE pods (without requiring a GCE VM, Docker daemon, SSH, or local `hub.db`).

**Architecture:** Extract a stdlib-only transport-agnostic `HubClient` (`scripts/hub_client.py`) supporting remote HTTPS + IAP/Serverless OIDC + `credentials.json` alongside legacy local `hub.db` fallback, and migrate all control, template, preflight, sync, and watchdog scripts to use Hub REST APIs (`/health`, `/api/v1/projects`, `/templates`, `/harness-configs`, `/schedules`, `/messages`, `/shared-dirs/.../files`, `/agents/.../exec`) plus Firestore-backed watchdog state and Kubernetes `CronJob`s. Pair this with GKE Workload Identity provisioning (`setup-sandbox.sh`, `setup-gke-hub.sh`), `./scripts/scion-factory provision` (`scripts/provision_factory.py`), and an end-to-end GKE-aware `scion-factory-bootstrap` skill.

**Tech Stack:** Python 3.10+ (stdlib `urllib.request`, `http.server`, `json`, `hashlib`, `tarfile`, `subprocess`), Bash (`set -euo pipefail`), Google Cloud (`gcloud`, GKE Autopilot/Standard, Workload Identity, Firestore REST, Artifact Registry, Cloud Build), Kubernetes (`kubectl`, `CronJob`), Pytest (`uv run --with pytest --with pyyaml pytest`).

**Spec:** `docs/specs/2026-10-27-gke-auto-factory-design.md`

## Global Constraints

- Zero external runtime Python dependencies in `scripts/` (stdlib only; `pyyaml` remains optional in `scion_factory.py` with its existing JSON/YAML fallback, so K8s `CronJob` containers run with zero `pip install`).
- Zero secrets, PATs (`ghp_*`, `github_pat_*`, `scion_pat_*`), emails, real GCP project IDs/numbers, cluster names, or VM names in any tracked file; run `rg -i '(ghp_|github_pat_|scion_pat_|AIza|ya29\.|BEGIN PRIVATE KEY)' . --glob '!config/factory.env'` before committing.
- Every shell and provisioning script in `scripts/` must be idempotent, support `DRY_RUN=1` or `--dry-run` (default dry-run for Python mutating tools unless `--apply` is passed), and set `set -euo pipefail` + `CLOUDSDK_CORE_DISABLE_PROMPTS=1`.
- Serverless Firestore remains the sole factory control plane (`factory_agent_status`, `eval_runs`, `factory_control_state` via `scripts/factory_store.py`); never add Spanner or Cloud SQL to the factory control plane itself.
- Preserve 100% backward compatibility with existing single-VM Docker `hub.db` setups when `HUB_URL` is unset and `AGENT_RUNTIME` is unset/`docker`; all 320 existing tests must continue passing.
- Never add AI co-author or session trailers to Git commits.

## Review Focus

1. **Cloud Run `/healthz` platform 404 vs `/health` 200:** On Cloud Run HA Scion Hubs, `/healthz` returns HTTP 404 from Google's frontend while `/health` returns `{"status": "healthy", "scionVersion": "..."}` — `HubClient.health()` must succeed via `/health` without reporting the Hub unhealthy (pinned in Task 1 `test_health_falls_back_between_health_and_healthz`).
2. **Cold GKE Autopilot `503` on agent create/start (`ptone/scion#1956`):** When `POST .../agents` or `POST .../start` returns HTTP `503` or times out while Autopilot scales a node and pulls a ~1 GB image, `ensure_agent_started` must poll `GET /api/v1/projects/{pid}/agents/{slug}` rather than blindly re-POSTing a duplicate create (pinned in Task 1 `test_ensure_agent_started_polls_after_cold_autopilot_503_without_duplicate_create`).
3. **IAP `Proxy-Authorization` vs Hub `Authorization` header collision:** When `HUB_IAP_AUDIENCE` is configured, IAP OIDC tokens must travel in `Proxy-Authorization: Bearer <oidc>` (or `X-Serverless-Authorization` in `serverless` mode) while the Scion user token remains in `Authorization: Bearer <hub_token>`, and cross-origin GCS signed upload URLs in `push-templates.py` must NOT leak either auth header (pinned in Task 1 `test_iap_proxy_authorization_keeps_hub_bearer_and_strips_on_cross_origin`).
4. **Stateless K8s CronJob watchdog circuit-breaker amnesia:** Without persistent state, a 5-minute K8s `CronJob` pod restarts broken agents infinitely across runs because local `/tmp` or `~/.scion/watchdog-state.json` vanishes on pod exit — Firestore state persistence in `factory_control_state` must preserve `restarts`, `alerted`, and `last_user_msg_ts` across invocations (pinned in Task 3 `test_watchdog_api_mode_persists_circuit_breaker_in_firestore`).
5. **Legacy `deploy: none` VM sandbox regressions:** Adding `WORKER_SA_NAME` for `AGENT_RUNTIME=kubernetes` must not create service accounts when `AGENT_RUNTIME` is unset/`docker` and `DEPLOYER_SA_NAME`/`RUNTIME_SA_NAME` are empty (pinned in Task 4 `test_setup_sandbox_kubernetes_mode_creates_worker_sa_and_keeps_docker_deploy_none_clean`).

---

## File Structure

- **Create `scripts/hub_client.py`** — Single transport-agnostic Scion Hub HTTP client (remote `HUB_URL` + `SCION_HUB_TOKEN` / `~/.scion/credentials.json` + IAP/Serverless OIDC headers + local `hub.db` HS256 fallback, `/health`+`/healthz`, cold-start `503` polling, Shared-Dirs Files sync, Agent Exec).
- **Modify `scripts/scion-admin.py`**, **`scripts/push-templates.py`**, **`scripts/make-factory-harness-configs.py`**, **`scripts/hub-preflight.py`** — Use `hub_client.HubClient` directly when `HUB_URL` is set (zero `hub.db` or `run-on-hub.sh` required), adding `project-settings` and `service-accounts` subcommands to `scion-admin.py`, `--via-api` to `make-factory-harness-configs.py`, and Artifact Registry / K8s namespace checks to `hub-preflight.py`.
- **Modify `scripts/factory_store.py`**, **`scripts/sync-factory-agents.py`**, **`scripts/factory-watchdog.py`** & **Create `scripts/install-k8s-control-plane.py`** — Add Firestore `factory_control_state` state persistence, Hub-API mode for agent sync + watchdog (using `/exec`, `/messages`, `/schedules`, and `/shared-dirs/knowledge-plane/files`), and Kubernetes `CronJob` manifest generation/apply.
- **Modify `scripts/setup-sandbox.sh`**, **`config/factory.env.example`** & **Create `scripts/setup-gke-hub.sh`** — GKE `WORKER_SA_NAME` + Workload Identity (`scion-worker` / `scion-deployer` KSAs) in `setup-sandbox.sh`, plus idempotent `setup-gke-hub.sh` for Full Terraform HA / Hybrid GKE Scion Hubs.
- **Modify `scripts/scion_factory.py`**, **`templates/_shared/agents.md`** & **Create `scripts/provision_factory.py`** — Add `infrastructure.runtime` (`kubernetes` | `docker`), `runtime_profile`, `worker_sa_name` to `FactorySpec`, and `./scripts/scion-factory provision` 9-phase idempotent orchestrator.
- **Modify `skills/scion-factory-bootstrap/SKILL.md`**, **`README.md`**, **`AGENTS.md`**, **`docs/lessons-learned.md`** & **Create `docs/gke-setup-runbook.md`** — End-to-end GKE & auto-provision agent skill workflow, runbook, starting prompts, and Lesson `L32`.

---

### Task 1: Transport-Agnostic Scion Hub API Client (`scripts/hub_client.py`)

**Files:**
- Create: `scripts/hub_client.py`
- Test: `tests/test_hub_client.py`

**Interfaces:**
- Consumes: Environment variables (`HUB_URL`, `HUB_LOCAL_URL`, `SCION_HUB_TOKEN`, `HUB_IAP_AUDIENCE`, `SCION_TRANSPORT_AUDIENCE`, `HUB_TRANSPORT_MODE`, `HUB_TRANSPORT_TOKEN`, `PO_EMAIL`, `ADMIN_EMAIL`, `SCION_HOME`) and optional `~/.scion/credentials.json` or `~/.scion/hub.db` + `hub.env`.
- Produces:
  - `normalize_endpoint(url: str) -> str`
  - `load_credentials_token(scion_home: str, hub_url: str) -> str`
  - `mint_local_admin_jwt(scion_home: str, email: str = "", ttl: int = 300) -> str`
  - `class HubAPIError(RuntimeError)` with `.status: int`, `.method: str`, `.path: str`, `.body: str`
  - `class HubClient`:
    - `from_env(cls, env: Mapping[str, str] | None = None, scion_home: str | None = None) -> HubClient`
    - `.is_remote: bool`, `.base_url: str`
    - `.request(method: str, path_or_url: str, body: Any = None, raw: bytes | None = None, headers: dict[str, str] | None = None, timeout: int = 60) -> tuple[int, Any]`
    - `.health() -> dict | None` (probes `/health`, falls back to `/healthz`)
    - `.current_user() -> dict | None`, `.find_user(email: str) -> dict | None`
    - `.list_projects() -> list[dict]`, `.get_project_by_slug(slug: str) -> dict | None`, `.project_id(slug: str) -> str`
    - `.create_project(body: dict, wait_sec: int = 1800, poll_sec: float = 5.0) -> dict`
    - `.get_project_settings(project_id: str) -> dict`, `.put_project_settings(project_id: str, patch: dict) -> dict`
    - `.list_project_service_accounts(project_id: str) -> list[dict]`, `.register_project_service_account(project_id: str, email: str, gcp_project: str, name: str = "") -> dict`
    - `.list_agents(project_id: str) -> list[dict]`, `.get_agent(project_id: str, slug: str) -> dict | None`
    - `.ensure_agent_started(project_id: str, slug: str, create_body: dict | None = None, force_resume: bool = False, wait_sec: int = 180, poll_sec: float = 2.0) -> dict`
    - `.exec_agent(project_id: str, slug: str, command: list[str], timeout: int = 10) -> tuple[int, str]`
    - `.list_shared_dir_files(project_id: str, dir_name: str) -> list[dict]`, `.sync_shared_dir(project_id: str, dir_name: str, src_dir: str, apply: bool = False) -> dict`

- [ ] **Step 1: Write the failing tests in `tests/test_hub_client.py`**

```python
def test_health_falls_back_between_health_and_healthz(fake_hub):
    # Cloud Run returns 404 on /healthz and 200 {"status": "healthy", "scionVersion": "0.9.0"} on /health
    fake_hub.fail_healthz_404 = True
    client = hub_client.HubClient(base_url=fake_hub.url, token="tok-123")
    assert client.health() == {"status": "healthy", "scionVersion": "0.9.0"}


def test_load_credentials_token_normalizes_endpoint(tmp_path):
    creds = {
        "version": 1,
        "hubs": {
            "https://hub.example.com": {
                "accessToken": "scion_oauth_tok",
                "expiresAt": "2099-01-01T00:00:00Z",
            }
        },
    }
    (tmp_path / "credentials.json").write_text(json.dumps(creds))
    assert hub_client.load_credentials_token(str(tmp_path), "https://HUB.example.com/") == "scion_oauth_tok"


def test_iap_proxy_authorization_keeps_hub_bearer_and_strips_on_cross_origin(fake_hub):
    client = hub_client.HubClient(
        base_url=fake_hub.url,
        token="hub-jwt",
        transport_token="iap-oidc-jwt",
        transport_mode="iap",
    )
    status, data = client.request("GET", "/api/v1/projects")
    assert status == 200
    last = fake_hub.requests[-1]["headers"]
    assert last["Authorization"] == "Bearer hub-jwt"
    assert last["Proxy-Authorization"] == "Bearer iap-oidc-jwt"

    # Uploading to a cross-origin signed GCS URL must strip both Authorization and Proxy-Authorization
    status, _ = client.request("PUT", fake_hub.cross_origin_upload_url, raw=b"hello")
    assert status == 200
    upload_hdrs = fake_hub.cross_origin_requests[-1]["headers"]
    assert "Authorization" not in upload_hdrs
    assert "Proxy-Authorization" not in upload_hdrs


def test_ensure_agent_started_polls_after_cold_autopilot_503_without_duplicate_create(fake_hub):
    # First POST /agents creates the agent server-side (phase="provisioning" -> "running") but returns 503
    fake_hub.cold_start_503_once = True
    client = hub_client.HubClient(base_url=fake_hub.url, token="tok-123")
    agent = client.ensure_agent_started(
        "proj-1",
        "lead",
        create_body={"name": "lead", "template": "lead", "task": "Wake up."},
        wait_sec=5,
        poll_sec=0.05,
    )
    assert agent["phase"] == "running"
    create_posts = [r for r in fake_hub.requests if r["method"] == "POST" and r["path"] == "/api/v1/projects/proj-1/agents"]
    assert len(create_posts) == 1


def test_sync_shared_dir_uploads_changed_and_deletes_stale_files(fake_hub, tmp_path):
    src = tmp_path / "knowledge-plane"
    (src / "adrs").mkdir(parents=True)
    (src / "index.md").write_text("# Index\n")
    (src / "adrs" / "ADR-001.md").write_text("# ADR 1\n")
    fake_hub.shared_dir_files[("proj-1", "knowledge-plane")] = {"stale.md": "old", "index.md": "# Index\n"}

    client = hub_client.HubClient(base_url=fake_hub.url, token="tok-123")
    plan = client.sync_shared_dir("proj-1", "knowledge-plane", str(src), apply=False)
    assert plan == {"uploaded": ["adrs/ADR-001.md"], "deleted": ["stale.md"], "unchanged": ["index.md"]}
    assert "stale.md" in fake_hub.shared_dir_files[("proj-1", "knowledge-plane")]

    applied = client.sync_shared_dir("proj-1", "knowledge-plane", str(src), apply=True)
    assert applied == plan
    assert fake_hub.shared_dir_files[("proj-1", "knowledge-plane")] == {
        "index.md": "# Index\n",
        "adrs/ADR-001.md": "# ADR 1\n",
    }
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --with pytest --with pyyaml pytest tests/test_hub_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'hub_client'`

- [ ] **Step 3: Implement `scripts/hub_client.py`**

Implement `HubClient` using `urllib.request` with same-origin header scoping (matching upstream `pkg/apiclient/transport.go` so `Authorization`, `Proxy-Authorization`, and `X-Serverless-Authorization` are attached only when `scheme://host:port` matches `base_url`), OIDC transport token resolution (`HUB_TRANSPORT_TOKEN`, GCE/GKE metadata server `/computeMetadata/v1/instance/service-accounts/default/identity?audience=...&format=full`, or `gcloud auth print-identity-token --audiences=...`), `credentials.json` lookup + local `hub.db` HS256 fallback, `/health` -> `/healthz` probe, cold-start `503`/timeout polling on `ensure_agent_started`, `exec_agent` (`POST /api/v1/projects/{pid}/agents/{slug}/exec`), and `sync_shared_dir` (`GET/PUT/DELETE /api/v1/projects/{pid}/shared-dirs/{dir_name}/files/...` using `{"content": text}` on `PUT`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --with pytest --with pyyaml pytest tests/test_hub_client.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/hub_client.py tests/test_hub_client.py
git commit -m "feat(hub-client): add transport-agnostic Scion Hub API client with IAP and GKE cold-start support"
```

---

### Task 2: Remote Hub API Mode for Admin, Template, Harness-Config, and Preflight Scripts

**Files:**
- Modify: `scripts/scion-admin.py:1-453`
- Modify: `scripts/push-templates.py:1-287`
- Modify: `scripts/make-factory-harness-configs.py:1-193`
- Modify: `scripts/hub-preflight.py:1-166`
- Test: `tests/test_remote_hub_scripts.py`, `tests/test_hub_preflight.py`

**Interfaces:**
- Consumes: `hub_client.HubClient` from Task 1.
- Produces:
  - `scion-admin.py` subcommands working against `HUB_URL` with zero `hub.db`:
    - existing subcommands (`projects`, `message`, `schedule`, `agent-start`, `project-create`, `shared-dirs`, `scion`, `experiments`)
    - `project-settings --project <slug> [--profile <name>] [--gcp-identity block|passthrough|assign] [--service-account <id_or_email>]`
    - `service-accounts --project <slug> list | add --email <gsa> --gcp-project <proj> [--name <display>]`
  - `push-templates.py`: `push_templates_via_api(client: HubClient, project_slug: str, roles: list[str], rendered_dir: str, apply: bool = False, default_dir: str = "") -> dict` invoked automatically when `HUB_URL` is set or `--direct` is passed (preserving `L16` `storagePath` isolation check via `GET /api/v1/templates?scope=project&projectId={pid}`).
  - `make-factory-harness-configs.py`: `--via-api` mode reading global `<harness>` files via `/api/v1/harness-configs` + `/download-urls` (when local `~/.scion/harness-configs` is absent) and publishing `<harness>-factory` via `/api/v1/harness-configs` + `/upload` + `/finalize`.
  - `hub-preflight.py`: `build_api_report(client: HubClient, po_email: str, new_projects: list[str], images: set[str] | None, watchdog_projects: list[str], sync_projects: list[str], github_token_present: bool = True, runtime: str = "docker", k8s_ready: bool | None = None) -> dict` plus `--runtime kubernetes|docker`, Artifact Registry image listing (`IMAGE_REGISTRY`), and K8s CronJob `FACTORY_PROJECTS` discovery.

- [ ] **Step 1: Write failing tests in `tests/test_remote_hub_scripts.py` and `tests/test_hub_preflight.py`**

```python
def test_push_templates_direct_api_isolates_shared_storage_and_uploads(fake_hub, tmp_path):
    # Pre-seed a legacy template sharing another template's storagePath on fake_hub (no SQLite hub.db anywhere)
    fake_hub.seed_shared_storage_collision(project_id="proj-1", role="lead")
    env = {
        **os.environ,
        "HUB_URL": fake_hub.url,
        "SCION_HUB_TOKEN": "tok-admin",
        "FACTORY_ENV": str(write_minimal_env(tmp_path)),
    }
    res = subprocess.run(
        [sys.executable, "scripts/push-templates.py", "--project", "demo", "--roles", "lead", "--apply"],
        cwd=ROOT, env=env, capture_output=True, text=True,
    )
    assert res.returncode == 0, res.stderr
    assert "rename it to lead-legacy" in res.stdout
    assert "other templates unchanged: yes" in res.stdout


def test_scion_admin_project_settings_and_service_accounts_over_api(fake_hub, tmp_path):
    env = {**os.environ, "HUB_URL": fake_hub.url, "SCION_HUB_TOKEN": "tok-admin"}
    r1 = subprocess.run(
        [sys.executable, "scripts/scion-admin.py", "service-accounts", "--project", "demo",
         "add", "--email", "agent-worker@sandbox.iam.gserviceaccount.com", "--gcp-project", "sandbox"],
        cwd=ROOT, env=env, capture_output=True, text=True,
    )
    assert r1.returncode == 0, r1.stderr
    r2 = subprocess.run(
        [sys.executable, "scripts/scion-admin.py", "project-settings", "--project", "demo",
         "--profile", "gke", "--gcp-identity", "assign",
         "--service-account", "agent-worker@sandbox.iam.gserviceaccount.com"],
        cwd=ROOT, env=env, capture_output=True, text=True,
    )
    assert r2.returncode == 0, r2.stderr
    assert fake_hub.project_settings["proj-1"]["activeProfile"] == "gke"
    assert fake_hub.project_settings["proj-1"]["defaultGCPIdentityMode"] == "assign"


def test_make_factory_harness_configs_via_api_wraps_command_base(fake_hub):
    env = {**os.environ, "HUB_URL": fake_hub.url, "SCION_HUB_TOKEN": "tok-admin"}
    res = subprocess.run(
        [sys.executable, "scripts/make-factory-harness-configs.py", "--via-api", "--harness", "claude", "--apply"],
        cwd=ROOT, env=env, capture_output=True, text=True,
    )
    assert res.returncode == 0, res.stderr
    uploaded = fake_hub.harness_configs["claude-factory"]
    assert '["sh", "/home/scion/.local/bin/factory-cd.sh"' in uploaded["files"]["config.yaml"]
    assert "home/.local/bin/factory-cd.sh" in uploaded["files"]


def test_hub_preflight_api_mode_checks_registry_images_and_merges_k8s_projects(fake_hub):
    client = hub_client.HubClient(base_url=fake_hub.url, token="tok-admin")
    report = hub_preflight.build_api_report(
        client=client,
        po_email="po@example.com",
        new_projects=["alpha", "alpha-eval"],
        images={"us-central1-docker.pkg.dev/proj/scion/scion-claude:v1",
                "us-central1-docker.pkg.dev/proj/scion/scion-antigravity:v1"},
        watchdog_projects=["existing"],
        sync_projects=["existing"],
        github_token_present=True,
        runtime="kubernetes",
        k8s_ready=True,
    )
    assert report["ok"] is True
    assert report["factory_projects"] == "existing,alpha,alpha-eval"
    assert any("scion-opencode" in w for w in report["warnings"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --with pytest --with pyyaml pytest tests/test_remote_hub_scripts.py tests/test_hub_preflight.py -v`
Expected: FAIL (missing `project-settings` / `service-accounts` / `--via-api` / `build_api_report` and `hub.db` lookup errors when `HUB_URL` is set without SQLite).

- [ ] **Step 3: Implement remote Hub API support in the four scripts**

Update `scion-admin.py`, `push-templates.py`, `make-factory-harness-configs.py`, and `hub-preflight.py` to route through `hub_client.HubClient.from_env()` whenever `HUB_URL` is non-empty (or `--direct` / `--via-api` is passed), keeping existing `hub.db` functions intact when running locally on a VM — and preserving module-level `_token()` and `HUB_URL` attributes in `scion-admin.py` (`HUB_URL = os.environ.get("HUB_URL") or os.environ.get("HUB_LOCAL_URL", "http://127.0.0.1:8080")`) so `tests/test_kit_deploy.py::test_scion_admin_runs_the_scion_cli_as_the_admin` passes unchanged. Update `missing_images(present, harnesses)` in `hub-preflight.py` to match any `<registry>/scion-<harness>:<tag>` image in addition to `scion-<harness>:latest` and `localhost/scion/scion-<harness>:latest`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --with pytest --with pyyaml pytest tests/test_remote_hub_scripts.py tests/test_hub_preflight.py tests/test_monorepo_factories.py tests/test_shared_dirs.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/scion-admin.py scripts/push-templates.py scripts/make-factory-harness-configs.py scripts/hub-preflight.py tests/test_remote_hub_scripts.py tests/test_hub_preflight.py
git commit -m "feat(scripts): support direct Hub API and GKE registry preflight without hub.db or SSH"
```

---

### Task 3: Hub-API + GKE Watchdog, Firestore State Backend, Sync, and K8s CronJobs

**Files:**
- Modify: `scripts/factory_store.py:1-340`
- Modify: `scripts/sync-factory-agents.py:1-234`
- Modify: `scripts/factory-watchdog.py:1-937`
- Create: `scripts/install-k8s-control-plane.py`
- Test: `tests/test_gke_watchdog_and_sync.py`

**Interfaces:**
- Consumes: `hub_client.HubClient` (Task 1) and `factory_store.FirestoreStore`.
- Produces:
  - `factory_store.py`:
    - `CONTROL_STATE_COLLECTION = "factory_control_state"`
    - `load_control_state(store: FirestoreStore, state_id: str) -> dict`
    - `save_control_state(store: FirestoreStore, state_id: str, state: dict) -> None`
  - `sync-factory-agents.py`:
    - `collect_agent_docs_from_api(client: HubClient, projects: list[str], api_calls_by_agent: dict[str, int], tool_calls_by_agent: dict[str, int]) -> list[dict]` (runs automatically when `HUB_URL` is set; falls back to `hub.db` otherwise).
  - `factory-watchdog.py`:
    - `run_api_watchdog(client: HubClient, state: dict, apply: bool, runtime: str = "kubernetes") -> None` (Check 1 skipped on remote/immutable hubs; Check 2 via `/schedules`; Check 3 via `/agents` + `/exec` hung-subprocess reaping + `ensure_agent_started`; Check 4 via `GET /api/v1/messages`; Check 5 via `client.sync_shared_dir` from `KNOWLEDGE_PLANE_LOCAL_DIR` or workspace API/NFS mount), plus Firestore state persistence when `WATCHDOG_STATE_BACKEND=firestore` (default when `HUB_URL` + `FIRESTORE_PROJECT` are set and `WATCHDOG_STATE` file isn't overridden).
  - `scripts/install-k8s-control-plane.py`:
    - `render_manifests(namespace: str, hub_url: str, factory_projects: str, sandbox_project: str, firestore_project: str, firestore_database: str = "(default)", ksa_name: str = "scion-factory-control", gsa_email: str = "", image: str = "python:3.12-slim", iap_audience: str = "") -> list[dict]`
    - CLI: `install-k8s-control-plane.py [--namespace NS] [--apply] [--dry-run] [--out FILE]` (`--dry-run` default unless `--apply` is passed; writes `Secret` via stdin `kubectl create secret generic ... --dry-run=client -o yaml | kubectl apply -f -` so `SCION_HUB_TOKEN` never touches disk or `ps` argv).

- [ ] **Step 1: Write failing tests in `tests/test_gke_watchdog_and_sync.py`**

```python
def test_sync_factory_agents_reads_from_hub_api_without_sqlite(fake_hub, monkeypatch):
    fake_hub.seed_agent("proj-1", slug="lead", phase="running", activity="working", harness="claude", task_summary="Wave 1")
    recorded = []
    monkeypatch.setenv("HUB_URL", fake_hub.url)
    monkeypatch.setenv("SCION_HUB_TOKEN", "tok-admin")
    monkeypatch.setenv("FACTORY_PROJECTS", "demo")
    monkeypatch.setenv("FIRESTORE_PROJECT", "sandbox-proj")
    # Run sync-factory-agents.py --dry-run with SCION_HOME pointing at an empty dir (no hub.db!)
    res = subprocess.run(
        [sys.executable, "scripts/sync-factory-agents.py", "--dry-run"],
        cwd=ROOT, env={**os.environ, "SCION_HOME": "/nonexistent-scion-home"},
        capture_output=True, text=True,
    )
    assert res.returncode == 0, res.stderr
    assert "demo__lead" in res.stdout


def test_watchdog_api_mode_reaps_hung_subprocess_via_exec_and_persists_circuit_breaker_in_firestore(fake_hub, fake_firestore):
    fake_hub.seed_agent("proj-1", slug="backend", phase="running", runtime="kubernetes", pod_name="scion-backend")
    fake_hub.exec_outputs[("proj-1", "backend")] = (
        "  PID ELAPSED COMMAND         COMMAND\n"
        "    1    1200 sciontool       sciontool init\n"
        "  442     905 uvicorn         uvicorn app.main:app --port 8080\n"
    )
    client = hub_client.HubClient(base_url=fake_hub.url, token="tok-admin")
    state = factory_store.load_control_state(fake_firestore, "watchdog__default")
    watchdog.run_api_watchdog(client, state, apply=True, runtime="kubernetes")
    factory_store.save_control_state(fake_firestore, "watchdog__default", state)

    assert any(cmd == ["kill", "-9", "442"] for cmd in fake_hub.exec_commands[("proj-1", "backend")])

    # Simulate 3 crash restarts -> 4th run in a fresh process loads state from Firestore and trips circuit breaker
    state["restarts"] = {"demo/backend": [time.time() - 10, time.time() - 5, time.time() - 1]}
    factory_store.save_control_state(fake_firestore, "watchdog__default", state)
    fake_hub.set_agent_phase("proj-1", "backend", "error")
    reloaded = factory_store.load_control_state(fake_firestore, "watchdog__default")
    watchdog.run_api_watchdog(client, reloaded, apply=True, runtime="kubernetes")
    assert not fake_hub.started_agents  # circuit breaker blocked the 4th restart!


def test_install_k8s_control_plane_renders_cronjobs_and_workload_identity_ksa():
    manifests = install_k8s.render_manifests(
        namespace="scion-hub-prod",
        hub_url="https://hub.example.com",
        factory_projects="demo,demo-eval",
        sandbox_project="my-sandbox",
        firestore_project="my-sandbox",
        gsa_email="agent-worker@my-sandbox.iam.gserviceaccount.com",
    )
    kinds = {(m["kind"], m["metadata"]["name"]) for m in manifests}
    assert ("ServiceAccount", "scion-factory-control") in kinds
    assert ("ConfigMap", "scion-factory-control-scripts") in kinds
    assert ("CronJob", "scion-factory-sync") in kinds
    assert ("CronJob", "scion-factory-watchdog") in kinds
    sa = next(m for m in manifests if m["kind"] == "ServiceAccount")
    assert sa["metadata"]["annotations"]["iam.gke.io/gcp-service-account"] == "agent-worker@my-sandbox.iam.gserviceaccount.com"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --with pytest --with pyyaml pytest tests/test_gke_watchdog_and_sync.py -v`
Expected: FAIL

- [ ] **Step 3: Implement Firestore control state, API sync, API+GKE watchdog, and `install-k8s-control-plane.py`**

Implement `load_control_state` / `save_control_state` in `scripts/factory_store.py` (storing JSON-serialized `state_json` + `updated_at` in `factory_control_state/{state_id}`), add `collect_agent_docs_from_api` in `scripts/sync-factory-agents.py`, add `run_api_watchdog` + Firestore state load/save in `scripts/factory-watchdog.py` (preserving all existing SQLite/Docker checks when `HUB_URL` is unset), and create `scripts/install-k8s-control-plane.py`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --with pytest --with pyyaml pytest tests/test_gke_watchdog_and_sync.py tests/test_watchdog_wake.py tests/test_firestore_control_plane.py tests/test_shared_dirs.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/factory_store.py scripts/sync-factory-agents.py scripts/factory-watchdog.py scripts/install-k8s-control-plane.py tests/test_gke_watchdog_and_sync.py
git commit -m "feat(control-plane): add Hub-API watchdog/sync, Firestore state backend, and K8s CronJobs"
```

---

### Task 4: GKE Sandbox Workload Identity & Full GKE Scion Hub Bootstrapper

**Files:**
- Modify: `scripts/setup-sandbox.sh:1-271`
- Modify: `config/factory.env.example:1-57`
- Create: `scripts/setup-gke-hub.sh`
- Test: `tests/test_setup_sandbox.py`, `tests/test_setup_gke_hub.py`

**Interfaces:**
- Consumes: `FACTORY_ENV` (`AGENT_RUNTIME`, `WORKER_SA_NAME`, `DEPLOYER_SA_NAME`, `RUNTIME_SA_NAME`, `HUB_SA`, `HUB_PROJECT`, `HUB_K8S_NAMESPACE`, `HUB_GKE_CLUSTER`, `HUB_GKE_LOCATION`, `IMAGE_REGISTRY`).
- Produces:
  - `scripts/setup-sandbox.sh`:
    - When `AGENT_RUNTIME=kubernetes` (or `HUB_K8S_NAMESPACE` is set): does NOT require `HUB_VM` (uses `HUB_SA` if provided, optional on pure Workload Identity); creates `WORKER_SA_NAME` (default `agent-worker`) with `roles/aiplatform.user`, `roles/datastore.user`, `roles/serviceusage.serviceUsageConsumer`, and `roles/storage.objectAdmin` on `$DATA_BUCKET`; binds `roles/iam.workloadIdentityUser` for `serviceAccount:$HUB_PROJECT.svc.id.goog[$HUB_K8S_NAMESPACE/scion-worker]` and `serviceAccount:$HUB_PROJECT.svc.id.goog[$HUB_K8S_NAMESPACE/scion-factory-control]` on `$WORKER_SA`, plus `serviceAccount:$HUB_PROJECT.svc.id.goog[$HUB_K8S_NAMESPACE/scion-deployer]` on `$DEPLOYER_SA` when `deploys == 1`; and creates/annotates KSAs `scion-worker` / `scion-deployer` via `kubectl` when `kubectl` is available (honoring `DRY_RUN=1`).
    - When `AGENT_RUNTIME` is unset/`docker`: preserves exact existing behavior (including zero SA creation on `deploy: none`).
  - `scripts/setup-gke-hub.sh` (`DRY_RUN=1` / `--dry-run`, `--tier terraform-ha|hybrid-gke`, `--scion-repo DIR`, `--harnesses claude,antigravity`, `--approve-apply`):
    - Enables required GCP APIs, builds multi-arch harness images into `IMAGE_REGISTRY` via upstream `image-build/scripts/build-images.sh --builder cloud-build` with an immutable tag (never mutating `:latest` silently), generates Terraform `.tfvars` (or `deploy-config.json` for `hybrid-gke`) in a git-ignored work directory, runs `terraform init` + `terraform plan` (applying only when `--approve-apply` is explicitly passed), sets Hub env `GOOGLE_CLOUD_PROJECT` & `GOOGLE_CLOUD_REGION`, and verifies cluster/namespace readiness.

- [ ] **Step 1: Write failing tests in `tests/test_setup_sandbox.py` and `tests/test_setup_gke_hub.py`**

```python
def test_setup_sandbox_kubernetes_mode_creates_worker_sa_and_keeps_docker_deploy_none_clean(tmp_path):
    k8s_env = (
        "AGENT_RUNTIME=kubernetes\nSANDBOX_PROJECT=sandbox\nSANDBOX_REGION=us-central1\n"
        "HUB_PROJECT=hub-proj\nHUB_K8S_NAMESPACE=scion-prod\nDATA_BUCKET=sandbox-data\n"
        "DEPLOYER_SA_NAME=agent-deployer\nRUNTIME_SA_NAME=app-serving\n"
    )
    res, calls = _sandbox_with_kubectl(tmp_path, k8s_env, "0")
    assert res.returncode == 0, res.stdout + res.stderr
    assert "compute instances describe" not in calls
    assert "iam service-accounts create agent-worker" in calls
    assert "serviceAccount:hub-proj.svc.id.goog[scion-prod/scion-worker]" in calls
    assert "serviceAccount:hub-proj.svc.id.goog[scion-prod/scion-deployer]" in calls


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --with pytest --with pyyaml pytest tests/test_setup_sandbox.py tests/test_setup_gke_hub.py -v`
Expected: FAIL

- [ ] **Step 3: Update `scripts/setup-sandbox.sh`, `config/factory.env.example`, and create `scripts/setup-gke-hub.sh`**

Implement the `AGENT_RUNTIME=kubernetes` Workload Identity branch in `scripts/setup-sandbox.sh`, document the GKE / `HUB_URL` keys in `config/factory.env.example`, and write `scripts/setup-gke-hub.sh` (`chmod +x`, `set -euo pipefail`, `CLOUDSDK_CORE_DISABLE_PROMPTS=1`, `DRY_RUN=1` / `--dry-run`, plan-review gate requiring `--approve-apply`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --with pytest --with pyyaml pytest tests/test_setup_sandbox.py tests/test_setup_gke_hub.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/setup-sandbox.sh scripts/setup-gke-hub.sh config/factory.env.example tests/test_setup_sandbox.py tests/test_setup_gke_hub.py
git commit -m "feat(gke): add Workload Identity sandbox bindings and full GKE Scion Hub bootstrapper"
```

---

### Task 5: `FactorySpec` Runtime Schema & Deterministic End-to-End Provisioner (`./scripts/scion-factory provision`)

**Files:**
- Modify: `config/factory.schema.json`
- Modify: `scripts/scion_factory.py`
- Modify: `templates/_shared/agents.md`
- Create: `scripts/provision_factory.py`
- Test: `tests/test_provision_factory.py`, `tests/test_spec_model.py`

**Interfaces:**
- Consumes: `hub_client.HubClient` (Task 1), `push-templates.py` / `hub-preflight.py` / `make-factory-harness-configs.py` (Task 2), `install-k8s-control-plane.py` (Task 3), `setup-sandbox.sh` (Task 4).
- Produces:
  - `config/factory.schema.json`: adds optional `runtime` (`enum: ["kubernetes", "docker"]`), `runtime_profile` (`string`), and `worker_sa_name` (`string`) under `infrastructure.properties`.
  - `scion_factory.py`:
    - `runtime_mode(spec: dict) -> str` (`"kubernetes"` or `"docker"`, returning `"docker"` when `infrastructure.runtime` is omitted in legacy presets, and validating `infrastructure.runtime in ("", "kubernetes", "docker")` in `evaluate_spec_readiness`).
    - Compiled `config/factory.env.example` retains all legacy `SCRIPT_VARS` (`HUB_VM`, `HUB_ZONE`, `HUB_OS_USER`, `HUB_LOCAL_URL`, etc. for `tests/test_kit_deploy.py`) with `HUB_URL=` empty by default, while emitting `AGENT_RUNTIME`, `RUNTIME_PROFILE`, `HUB_K8S_NAMESPACE`, `IMAGE_REGISTRY`, and `WORKER_SA_NAME`.
    - Compiled & canonical `_shared/agents.md` explains GKE Workload Identity (`assign` default via `worker-sa` / `passthrough`, plus `--service-account` for `deployer` / `eval-deployer`).
    - Subcommand: `./scripts/scion-factory provision <spec_path> --kit <kit_dir> --env <env_file> [--seed-repo <dir>] [--apply] [--go] [--json]`
  - `scripts/provision_factory.py`:
    - `provision_factory(spec: dict, kit_dir: Path, env_file: Path, seed_repo: Path | None = None, apply: bool = False, send_go: bool = False) -> dict` executing the 9 idempotent phases (1. preflight + merged `FACTORY_PROJECTS`, 2. `setup-sandbox.sh`, 3. optional `--seed-repo` knowledge-plane + orphan eval branch + Wave 1 issues, 4. `BUILD_PROJECT` & `EVAL_PROJECT` create, 5. project GCP service accounts + `activeProfile`/`defaultGCPIdentityMode` settings, 6. `scratchpad` + `knowledge-plane:ro` shared dirs + initial `sync_shared_dir`, 7. monorepo `<harness>-factory` configs + `push-templates.py`, 8. K8s CronJobs or VM cron install, 9. heartbeat schedule + `ensure_agent_started` for `lead` and `eval-lead` + optional `GO` message).

- [ ] **Step 1: Write failing tests in `tests/test_provision_factory.py` and `tests/test_spec_model.py`**

```python
def test_bad_infrastructure_runtime_fails_readiness_while_kubernetes_compiles_gke_env(tmp_path):
    spec = _starter()
    spec["infrastructure"]["runtime"] = "ecs"
    report = scion_factory.evaluate_spec_readiness(spec)
    assert report["ready"] is False
    assert any("infrastructure.runtime" in m for m in report["missing_or_weak"])

    spec["infrastructure"]["runtime"] = "kubernetes"
    spec["infrastructure"]["runtime_profile"] = "gke"
    assert scion_factory.evaluate_spec_readiness(spec)["ready"] is True
    scion_factory.compile_factory_boilerplate(spec, tmp_path / "kit")
    env_example = (tmp_path / "kit" / "config" / "factory.env.example").read_text()
    assert "AGENT_RUNTIME=kubernetes" in env_example
    assert "RUNTIME_PROFILE=gke" in env_example
    assert "WORKER_SA_NAME=agent-worker" in env_example


def test_scion_factory_provision_dry_run_and_apply_against_gke_fake_hub(fake_hub, tmp_path):
    spec = _starter()
    spec["infrastructure"]["runtime"] = "kubernetes"
    spec["infrastructure"]["runtime_profile"] = "gke"
    spec_path = tmp_path / "factory.yaml"
    kit_dir = tmp_path / "kit"
    scion_factory.save_spec(spec, spec_path)
    scion_factory.compile_factory_boilerplate(spec, kit_dir)

    env_file = write_gke_factory_env(tmp_path, fake_hub.url)
    # 1. Dry-run changes nothing on fake_hub
    r_dry = subprocess.run(
        [sys.executable, "scripts/scion_factory.py", "provision", str(spec_path),
         "--kit", str(kit_dir), "--env", str(env_file), "--json"],
        cwd=ROOT, env=fake_cli_env(tmp_path, fake_hub.url), capture_output=True, text=True,
    )
    assert r_dry.returncode == 0, r_dry.stderr
    assert json.loads(r_dry.stdout)["apply"] is False
    assert "starter-service" not in fake_hub.projects_by_slug

    # 2. --apply --go provisions projects, identity, shared-dirs, KP files, templates, schedules, and starts leads
    r_apply = subprocess.run(
        [sys.executable, "scripts/scion_factory.py", "provision", str(spec_path),
         "--kit", str(kit_dir), "--env", str(env_file), "--apply", "--go", "--json"],
        cwd=ROOT, env=fake_cli_env(tmp_path, fake_hub.url), capture_output=True, text=True,
    )
    assert r_apply.returncode == 0, r_apply.stderr
    out = json.loads(r_apply.stdout)
    assert out["apply"] is True
    assert {"starter-service", "starter-service-eval"} <= set(fake_hub.projects_by_slug)
    build_pid = fake_hub.projects_by_slug["starter-service"]["id"]
    assert fake_hub.project_settings[build_pid]["activeProfile"] == "gke"
    assert fake_hub.project_settings[build_pid]["defaultGCPIdentityMode"] == "assign"
    assert "index.md" in fake_hub.shared_dir_files[(build_pid, "knowledge-plane")]
    assert "lead" in fake_hub.agents_by_project[build_pid]
    assert any("starter-service-lead-heartbeat" == s["name"] for s in fake_hub.schedules[build_pid])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --with pytest --with pyyaml pytest tests/test_provision_factory.py tests/test_spec_model.py -v`
Expected: FAIL

- [ ] **Step 3: Implement `FactorySpec` runtime fields, `scripts/provision_factory.py`, and `provision` CLI subcommand**

Add `runtime` / `runtime_profile` / `worker_sa_name` validation and rendering in `scripts/scion_factory.py` + `templates/_shared/agents.md`, implement `scripts/provision_factory.py` with clean per-phase reporting, and wire `provision` into `scripts/scion_factory.py`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --with pytest --with pyyaml pytest tests/test_provision_factory.py tests/test_spec_model.py tests/test_scion_factory.py tests/test_deploy_none.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/scion_factory.py scripts/provision_factory.py templates/_shared/agents.md tests/test_provision_factory.py tests/test_spec_model.py
git commit -m "feat(provision): add GKE FactorySpec runtime fields and deterministic scion-factory provision pipeline"
```

---

### Task 6: Universal Bootstrap Skill, GKE Setup Runbook, Lesson `L32`, and Docs Contract

**Files:**
- Modify: `skills/scion-factory-bootstrap/SKILL.md`
- Create: `docs/gke-setup-runbook.md`
- Modify: `README.md`, `AGENTS.md`, `docs/lessons-learned.md`, `scripts/README.md`
- Test: `tests/test_docs_contract.py`

**Interfaces:**
- Consumes: `setup-gke-hub.sh` (Task 4) and `./scripts/scion-factory provision` (Task 5).
- Produces:
  - Updated `skills/scion-factory-bootstrap/SKILL.md`:
    - Step 0: Detect/ask target Hub mode (**New Full GKE Scion Hub**, **Existing GKE / Cloud Run Scion Hub**, or **Existing Single-VM Hub**) and guide `scripts/setup-gke-hub.sh` when installing Scion from scratch.
    - Steps 1–4: Repository inspection, specialist discovery, Socratic interview (including `infrastructure.runtime: kubernetes`), readiness gate, and `compile`.
    - Step 5: Automated provisioning via `./scripts/scion-factory provision factory.yaml --kit OUTPUT_DIR --env config/<slug>.env` (dry-run review with human first, then `--apply --go`).
  - `docs/gke-setup-runbook.md`: Complete operator & agent guide for Full GKE HA Scion (`deploy/terraform/`), Hybrid VM+GKE (`gke_target`), Workload Identity (`scion-worker` / `scion-deployer`), Artifact Registry harness images, and K8s `CronJob` monitoring.
  - `README.md`: Updated architecture overview, starting prompts (including **Full GKE Scion installation + auto-factory** and **Existing GKE Scion Hub**), and `./scripts/scion-factory provision` quickstart (keeping all existing contract strings checked by `tests/test_docs_contract.py`).
  - `docs/lessons-learned.md`: Lesson `L32` documenting GKE `block` default identity + Workload Identity KSA mapping, Cloud Run `/health` vs `/healthz`, cold Autopilot `503` idempotency, and stateless K8s CronJob state in Firestore.

- [ ] **Step 1: Write failing tests in `tests/test_docs_contract.py`**

```python
def test_docs_and_skill_cover_gke_runtime_and_auto_provision():
    skill = (ROOT / "skills/scion-factory-bootstrap/SKILL.md").read_text()
    for needle in ("setup-gke-hub.sh", "provision", "infrastructure.runtime", "kubernetes"):
        assert needle in skill, f"missing {needle!r} in SKILL.md"

    gke_runbook = (ROOT / "docs/gke-setup-runbook.md").read_text()
    for needle in ("Workload Identity", "scion-worker", "scion-deployer", "install-k8s-control-plane.py", "/health"):
        assert needle in gke_runbook, f"missing {needle!r} in docs/gke-setup-runbook.md"

    readme = (ROOT / "README.md").read_text()
    assert "GKE" in readme and "scion-factory provision" in readme

    lessons = (ROOT / "docs/lessons-learned.md").read_text()
    assert "## L32." in lessons
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --with pytest --with pyyaml pytest tests/test_docs_contract.py -v`
Expected: FAIL

- [ ] **Step 3: Update `SKILL.md`, `README.md`, `AGENTS.md`, `scripts/README.md`, `docs/lessons-learned.md`, and write `docs/gke-setup-runbook.md`**

Write clear, concise GKE + auto-provision documentation and skill instructions while preserving every existing contract tested in `tests/test_docs_contract.py`.

- [ ] **Step 4: Run full test suite & secret hygiene scan**

Run:
```bash
uv run --with pytest --with pyyaml pytest tests -q
rg -i '(ghp_|github_pat_|scion_pat_|AIza|ya29\.|BEGIN PRIVATE KEY)' . --glob '!config/factory.env' --glob '!AGENTS.md'
```
Expected: All tests PASS (`0` failures) and zero secret matches.

- [ ] **Step 5: Commit**

```bash
git add skills/scion-factory-bootstrap/SKILL.md docs/gke-setup-runbook.md README.md AGENTS.md scripts/README.md docs/lessons-learned.md tests/test_docs_contract.py
git commit -m "docs(gke): add GKE setup runbook, L32 lessons, and end-to-end auto-provision skill workflow"
```
