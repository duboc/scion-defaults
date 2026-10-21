# Evidence: the real setup journey, step by step

- **Status:** Input for the guided-journey design (2026-10-21). Not yet a design.
- **Sources (raw, local, not committed):** two from-scratch setup transcripts (the `acme` factory: 1 h 44 min from
  an empty project to a working lead; the `psearch` factory: ~13 calendar days, ~30–35 h hands-on), one ops-worker
  transcript, and the Scion source at the live Hub's commit (`69c0be79`) and local HEAD (`4e43e988`). The auth code
  is identical in both. Evidence labels: `A:<step>` (acme transcript), `P:<step>` (psearch transcript), and
  `file:line` in the Scion repo.
- **Rule for this page:** no real project IDs, VM names, emails, or credential values.

## 1. What the evidence says about the current runbook

`docs/setup-runbook.md` is accurate for what it covers. It assumes a lot already exists, though, and it hides
the steps that cost the most time:

| Gap | Evidence | Cost when hit |
|---|---|---|
| No preflight: org policy `skipDefaultNetworkCreation`, gcloud beta prompt, CPU quota or zone stockout, `testIamPermissions` | P:58–83, A:96–152 | 8–30 min each, plus reruns |
| No project, billing, API (batches of ≤15), or VPC + IAP-SSH firewall creation | A:83–120 | — (done by hand) |
| No model-access step: Model Garden EULA (human), probes for the **exact** models each harness uses, including background defaults | A:204–253, A:235 | 404 at the first agent |
| Upstream `deploy.sh` builds only the antigravity image; Claude, OpenCode and Hermes images must be built | P:119–549, A:20 | ~15 min |
| `server.hub.agent_endpoint` must be the internal IP, or agents can't reach the Hub | P:241–266 | ~10 min |
| **The first IAP sign-in is a hard prerequisite**: no `users` row means no admin credential | P:4053, `handlers_auth.go:1391` | Blocks every on-VM tool |
| No CLI or API credential for steps 5, 6 and 8 (project create, Hub secret, SA registration, `scion start`) | auth audit, Part B | ~40 min plus an 80 min wait (P:709–745) |
| Backups are installed at step 10, after the riskiest DB-touching steps | P:2781 | — |
| The step 7 check calls an authenticated route without a token and returns 401 | auth audit | — |
| `patch-opencode-and-hermes.py` is never referenced | P, L26 | ~2 h (P:3078–3090) |
| No smoke agent per harness before the real team | A:319–355 | Failures surface in the full team |
| No message-delivery check (`scion conversation send` exits 0 without sending) | A:3771–4388, A:4991–5225 | ~6 h of silent stalls |
| **Step 9 sets `*/15` heartbeats directly on Opus leads** | A:4440–4882 | ~$261 of $272/day wasted |
| Stale path `cd docs/scion` (copied from the source repo) | README, runbook §1 | Confusion |

## 2. Ordered steps, merged from both setups

Legend for **Auto**: ✅ script exists and works, ◐ partial, ❌ missing. **Gate** is the check the coding agent must run
and show before it continues. **OK?** marks steps that need the user's explicit approval.

| # | Phase | What happens | Gate (evidence of done) | Failure modes seen | OK? | Auto |
|---|---|---|---|---|---|---|
| 0 | **Intake and decisions** | Grill-me interview (existing) **plus deploy decisions**: one or two GCP projects, harness mix and model map (Vertex `global`), Scion release pin, `update_policy: notify`, git now or later, data stores (serverless), runtime SA per service, budget and alert recipient, **heartbeat strategy**, Hub-access method | `scion-factory check` ready; decisions sheet signed | Implicit decisions nobody asked about (DB writes, hand-set OTel endpoint, SA keys) P:§4; `update_policy auto` P:50 | ✔ | ◐ |
| 1 | **Operator preflight** | Dedicated gcloud configuration; `CLOUDSDK_CORE_DISABLE_PROMPTS=1`; beta component; `testIamPermissions`; org policies (default network, SA-key creation, domain restriction); quota; billing account; `gh auth status` | Preflight table all green | P:58–83, A:96–105, A:131–152, IAM-session S349 | — | ❌ |
| 2 | **Cloud foundation** | Create or select projects; link billing; enable APIs (≤15 per call, including `orgpolicy`, `cloudresourcemanager`); VPC + IAP-SSH (35.235.240.0/20:22) + proxy firewall | `billingEnabled`, APIs enabled, network present | A:83–120 (batch-size limit, prompt hang) | ✔ cost | ❌ |
| 3 | **Model access (human)** | User accepts Model Garden terms; agent probes every model in every harness config from the VM SA | HTTP 200 per model | A:235 (background Haiku default 404) | human | ❌ |
| 4 | **Hub VM** | Upstream `scripts/single-node-vm/deploy.sh` with a generated config (size, disk, zone fallback, `release_channel`, `update_policy: notify`, `container_images.source: build`, `admin_email`); then build every chosen harness image; set `agent_endpoint` | VM RUNNING; IAP proxy redirects; `localhost:8080/healthz` over IAP SSH; `docker images` lists all harness images | P:F1–F5, A:F12–F13 | ✔ cost | ◐ (upstream) |
| 5 | **First sign-in (human)** | PO opens the IAP URL once | Read-only `users` has the PO as `admin` | P:4053 | human | ❌ |
| 6 | **Hub access, backups, hardening** | Access probe with an on-VM minted JWT (`GET /api/v1/auth/me`); backups **now** (`backup-hub.sh --install-cron`, disk snapshot schedule); remove `roles/iam.serviceAccountAdmin` from the VM SA; `default_user_role: viewer`; experiments (`web.terminal_workspace=false`) | Probe 200 with role admin; first backup archive; IAM shows no SA-admin role; experiment off | P:F11, P:F20, A:F11 | — | ◐ |
| 7 | **Web access for other humans** *(optional)* | Custom OAuth client (human, console, External); `gcloud iap settings set` on **every** IAP'd service; Hub `authorized_domains` | Each domain signs in; IAP bindings match the allowlist | A:F4 (~33 h human wait), A:F5 | ✔ | ❌ |
| 8 | **Sandbox identities and data** | `setup-sandbox.sh`: deployer SA, **runtime SA per Cloud Run service**, build SA when org blocks default grants, Firestore + indexes, bucket, `serviceUsageConsumer`; IAM retry for eventual consistency | Rerun reports only "exists" | P:F16, P:F23 (~10 h wait), P:F25, A:F9, A:F15 | ✔ IAM | ✅ (needs retry) |
| 9 | **Telemetry before agents** | `setup-telemetry.sh` (prefer keyless VM SA; key only if required); `telemetry.cloud.gcp_project_id`; never hand-set `SCION_OTEL_ENDPOINT`; sciontool patch until upstream fixes | Canary container: no "destination is not configured", OTLP :4317 listening, hook < 1 s | P:F4 (latent 12 days), P:F9, P:F21 (~2.5 days of eval output), P:F32–F34 | ✔ IAM | ◐ |
| 10 | **Harness configs and templates** | Pin model catalogs (OpenCode, Hermes, Antigravity) **through the API**; push the **compiled kit's** templates; template lint (unique storage paths, no nested `gh://` skills, no reserved `SCION_*` env); recreate containers after template changes | Hub templates and harness configs match the kit; no shared storage paths | P:F14, P:F19/F27 (L16), A:F16, A:F20, A:F7 | — | ◐ (`push-templates.py` can't take a kit) |
| 11 | **Git and Hub secrets** *(optional, can come later)* | Repo, labels, rulesets, orphan eval branch; PAT typed on the VM (stdin) into the Hub secret with `injection_mode: always` | `gh api …/rulesets`; `git merge-base` exits 1; `git ls-remote` works inside an agent | PAT pasted in chat and inlined in commands (P:955, A:229/308/1046); P:F28; A:F8 (Actions minutes) | ✔ | ◐ (`enable-git.sh`) |
| 12 | **Hub projects** | Create build and eval projects **through the API** (minted JWT, never SQL); register the deployer SA; workspace checkout | Projects listed; SA verified | P:F18 (SQL-inserted project, origin of L16), A:F18 | ✔ (`git_remote` is irreversible) | ❌ |
| 13 | **Smoke agent per harness** | One throwaway agent per harness writes a file or opens a PR | Container exit 0; `agent.log` writable; `docker exec … curl $SCION_HUB_ENDPOINT/healthz`; Vertex `global`; hook < 1 s | A:F7 (uid mismatch), P:F5, P:F13 (stale `agent_identity_keys`) | — | ❌ |
| 14 | **Standing team** | Lead first, workers under it (delegation through the API, not `delegation_edges` SQL); size or remove `max_duration`/`max_turns`; `scion message` convention | Every container up; a lead → worker → lead message is **delivered** | P:F8, A:F2 (~6 h) | ✔ roster | ◐ |
| 15 | **Heartbeat and cost guardrails** | Default: cheap dedicated heartbeat agent on a low cadence that wakes the lead only when work is actionable; cost projection (wakes/day × lead context × cache-write price); budget with alerts; schedules created as the PO (minted JWT) | `schedule list` runs ≥ 1, errors = 0; projection under the agreed $/day | A:F1 (~$261/day), P:F22 (~60 h stall) | ✔ budget, cadence | ◐ (`schedule create` not idempotent) |
| 16 | **Ops** | Firestore status sync (never exercised live), watchdog (remove its DB-write fallbacks and "any admin/any user" impersonation), alerts | Cron entries; sync log; alert policy | A:S3581, P:F21 | ✔ alert recipient | ◐ |
| 17 | **Verification gate, then GO** | End-to-end checklist; dashboard data traced to real sources; first loop observed | Within one heartbeat: plan comment, worker briefed, message delivered, PR opened and reviewed | P:F24 (fabricated data), A:F2 | ✔ GO | ◐ (`verify-factory.sh`) |

## 3. Hub access: what the source actually allows

The operator learnings were checked against the source. Verdicts:

| Learning | Verdict | Correction that matters for the journey |
|---|---|---|
| UAT = `scion_pat_` + sha256 lookup | True | A lost token can't be recovered; mint a new one |
| Scopes (colon) + ceiling (dot, v1) | Partly | The ceiling applies to **every** UAT, not only v1; both gates only narrow the user's own bindings |
| UATs are project-bound; no admin routes or project create | True | They also can't manage **secrets, schedules, or GCP SA creation**; no such scope can be minted |
| Creating a UAT needs a session | True | The route is `POST /api/v1/auth/tokens`; a minted JWT qualifies; the issuer needs **project-scoped** bindings; `scion hub token create` **prints the token** |
| CLI credential precedence | True | A leftover `~/.scion/scion-token` makes the CLI prefer the dev token on localhost; a UAT in `SCION_AUTH_TOKEN` is a hard 401 |
| Session JWT derivation | Partly | Identity comes from `uid`, not `sub`. Only signature, `iss`, and `aud` are checked. **`exp` is optional and `type: refresh` is accepted** (verified with a dummy secret), and there is no revocation |
| UATs can't author recurring schedules | Partly, broader | UATs can't author **any** schedule (one-shot included); agent tokens can, in their own project |
| Hosted mode disables dev auth | Partly | An explicit `--dev-auth` still enables it and logs the token. The first admin is created by `admin_emails` + first sign-in. The supported headless login needs CLI OAuth clients, which an IAP-only Hub doesn't have |

**Chosen direction (to confirm in the design):** admin-grade steps use a **JWT minted on the VM** that never leaves
it. The JWT must use the exact `PO_EMAIL` with no fallback user, and `exp` must be at most 5 minutes and always
set. A `/auth/me` probe runs before each phase. Project-scoped day-2 CLI work may use an API-minted UAT, written
straight to a `0600` file on the VM. **Never write to SQLite.** The acme alternative (a temporary federation
issuer plus a gcloud ID token) needs a Hub restart on every toggle, and it was once left enabled after a crash
(A:741).

## 4. Rules for the coding agent, taken from the failures

1. **Secrets never enter the transcript.** Don't `cat hub.env`. Don't pass secrets as arguments. Don't run
   `scion hub token create` or `factory-status.sh --panes` on credentialed agents. Humans type secrets on the VM
   (stdin) or into Secret Manager. Both transcripts contain leaked values (a GitHub PAT, the Hub session secret,
   and a low-entropy UAT in about 50 command lines).
2. **Never write to `hub.db`.** Read it with `?mode=ro`; write only through the API. Two shipped scripts still
   write to it (`factory-watchdog.py` schedule fallback, `patch-opencode-and-hermes.py`). L16 came from a
   SQL-copied `storage_path` (P:1286).
3. **All remote work goes through `run-on-hub.sh`** (base64 over stdin, explicit `--tunnel-through-iap`); no nested
   quoting. All gcloud values come from config, never typed by hand (P:1524 SSH'd into the wrong project).
4. **Every step has a gate**, and the agent shows its output before continuing. Approvals carry a deadline and a
   safe default. Human latency was the largest single cost: 36.5 h (P:2034) and 10 h (P:2182) unanswered, and
   33 h waiting on an OAuth client (A:F4).
5. **Run a cost check before enabling any cadence.**

## 5. Corrections to existing lessons

- L6: the hook hang first occurred on 2026-10-09 (evidence from 10-02). It was **self-inflicted** by hand-setting
  `SCION_OTEL_ENDPOINT` (P:442).
- L13: first occurred on 10-02 (P:58–76), not 10-09.
- L26 #3: the Hermes flag was seen on 10-08 (P:577).
- L27: the deployed trace-state path is `~/.scion/trace-state.json`, not `/tmp/scion-trace-state.json`.
- New lessons to add: org-policy default network; images built only for antigravity; `agent_endpoint`; stale
  `agent_identity_keys`; IAM eventual consistency; Cloud Run reserves `/healthz`; first sign-in prerequisite;
  heartbeat cost (cadence × context); `scion conversation send` exits 0 without sending; human approval latency.

## 6. Open decisions for the design

1. **Heartbeat default:** a dedicated cheap heartbeat agent (acme's final state) versus `*/15` on the lead (current
   runbook and psearch).
2. **One or two GCP projects:** one (acme, simpler) versus separate Hub and sandbox projects (psearch; the audit
   favors separation).
3. **Telemetry credentials:** keyless through the VM SA (the VM already holds the writer roles) versus the current
   JSON key copied with mode 644.
4. **Hub projects:** git-backed through the API versus a shared folder plus `git init` (and the optional-Git path).
