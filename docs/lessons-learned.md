# Lessons learned

What went wrong while building and running the `psearch` / `psearch-eval` factories (October 2026), why, and where the
fix now lives. Dates are UTC. "Encoded in" points to the script, template rule or runbook that prevents a repeat.

| # | Lesson | Area |
|---|---|---|
| [L1](#l1-nested-ssh-quoting-runs-your-text-as-commands) | Nested SSH quoting runs your text as commands | operations |
| [L2](#l2-the-web-terminal-button-stopped-working) | The web terminal button stopped working | Hub |
| [L3](#l3-opencode-picked-models-that-dont-exist) | OpenCode picked models that don't exist | harness |
| [L4](#l4-a-printed-verdict-deadlocked-the-factory-for-about-60-hours) | A printed verdict deadlocked the factory for ~60 hours | protocol |
| [L5](#l5-leads-looked-stalled-and-nothing-woke-them) | Leads looked stalled and nothing woke them | protocol |
| [L6](#l6-antigravity-couldnt-run-any-tool-telemetry-hook-hang--and-its-phase-j-resolution) | Antigravity couldn't run any tool (telemetry hook hang) — resolved in Phase J | harness |
| [L7](#l7-the-default-compute-service-account-is-not-a-runtime-identity) | The default compute SA is not a runtime identity | GCP |
| [L8](#l8-workspaces-shared-folder-today-worktrees-later) | Workspaces: shared folder today, worktrees later | Scion |
| [L9](#l9-agents-fabricated-telemetry-and-claimed-storage-that-didnt-exist) | Agents fabricated telemetry and claimed storage that didn't exist | protocol |
| [L10](#l10-admin-api-access-from-scripts) | Admin API access from scripts | Hub |
| [L11](#l11-schedules-heartbeats-semantics) | Schedules (heartbeats) semantics | Hub |
| [L12](#l12-spanner-calls-from-the-vm-failed-on-quota-project) | Spanner calls from the VM failed on quota project | GCP |
| [L13](#l13-interactive-gcloud-prompts-hang-automation) | Interactive gcloud prompts hang automation | GCP |
| [L14](#l14-a-shared-github-token-means-no-pr-approvals) | A shared GitHub token means no PR approvals | GitHub |
| [L15](#l15-the-lead-wrote-the-code-itself) | The lead wrote the code itself | protocol |
| [L16](#l16-hand-written-hub-db-rows-broke-templates) | Hand-written Hub DB rows broke templates | Hub |
| [L17](#l17-pre-provision-the-identities-the-backlog-will-need) | Pre-provision the identities the backlog will need | process |
| [L18](#l18-hub-activity-is-a-hint-not-the-truth) | Hub activity is a hint, not the truth | observability |
| [L19](#l19-heartbeats-can-turn-into-chatter) | Heartbeats can turn into chatter | protocol |
| [L20](#l20-local-patches-dont-survive-upgrades-silently) | Local patches don't survive upgrades silently | operations |
| [L21](#l21-reincarnate-is-refused-for-agents-of-these-projects) | Reincarnate is refused for agents of these projects | Scion |
| [L22](#l22-hub-secret-injection-as_needed-vs-always) | Hub secret injection: `as_needed` vs `always` | security |
| [L23](#l23-deployer-workspace-cleanliness-and-cloud-run-traffic-routing) | Deployer workspace cleanliness and Cloud Run traffic routing | operations |
| [L24](#l24-unannounced-hub-restarts-overwrite-managed-harness-files-update_policy-notify) | Unannounced Hub restarts overwrite managed harness files | Hub |
| [L25](#l25-initialize-eval_branch-as-an-orphan-branch-git-switch---orphan-so-it-can-never-be-merged-into-main) | Initialize `EVAL_BRANCH` as an orphan branch | git |
| [L26](#l26-multi-harness-upgrade-to-gemini-4-argon-antigravity-opencode-hermes) | Multi-harness upgrade to Gemini 4 Argon (Antigravity, OpenCode, Hermes) | harness |

---

## L1. Nested SSH quoting runs your text as commands

- **When:** throughout; worst on 2026-10-12 while sending messages to agents.
- **Symptom:** `zsh: command not found: lead`, truncated messages, half-written files.
- **Cause:** `gcloud compute ssh --command "... scion message lead \"... `lead` ...\""` passes through two or three shells.
  Backticks and `$(...)` inside the text run as commands; nested quotes end early.
- **Fix:** ship content base64-encoded and decode it remotely (`echo <b64> | base64 -d | sudo -u scion bash`).
- **Encoded in:** [`scripts/run-on-hub.sh`](../scripts/run-on-hub.sh). Agents use `scion message --body-file` for long text
  ([`_shared/agents.md`](../templates/_shared/agents.md)).

## L2. The web terminal button stopped working

- **When:** 2026-10-09.
- **Symptom:** the agent's Terminal button in the web UI did nothing or showed a blank terminal.
- **Cause:** the `web.terminal_workspace` experiment (beta, on by default) holds a cross-tab `navigator.locks` lock,
  which a hidden tab can keep forever, and `initTerminal` waits on `requestAnimationFrame`, which background tabs
  throttle.
- **Fix:** turn the experiment off: `scripts/run-on-hub.sh scripts/scion-admin.py experiments --set
  web.terminal_workspace=false`; users reload the page.
- **Encoded in:** [operations-runbook.md](operations-runbook.md#web-terminal-button-does-nothing).

## L3. OpenCode picked models that don't exist

- **When:** 2026-10-08/09 (`backend`).
- **Symptom:** every model call returned 404; OpenCode switched to image or text-to-speech variants.
- **Cause:** OpenCode resolves models from the models.dev catalog in `~/.cache/opencode/models.json`. That catalog marks
  Gemini 2.5 as deprecated and lists several "Gemini 2.5 Pro" variants; the name matched a TTS variant first. The Scion
  provisioner pre-fetches the full catalog at every start, the stock launcher then deleted it, and OpenCode refreshes it
  in the background (the live `backend` had a fresh 5.4 MB catalog again on 2026-10-12, three days after the pin).
- **Fix:** the launcher writes a pinned catalog with only `google-vertex/gemini-2.5-pro` and `gemini-2.5-flash` on every
  start, then runs `opencode -m google-vertex/gemini-2.5-pro`. To reload a running agent without recreating it:
  `docker exec -u scion <project>--backend tmux respawn-pane -k -t scion:0.0 "sh /home/scion/.local/bin/opencode-launch.sh"`.
- **Encoded in:** [`templates/backend/home/.local/bin/opencode-launch.sh`](../templates/backend/home/.local/bin/opencode-launch.sh),
  [`config/opencode-models.json`](../config/opencode-models.json).

## L4. A printed verdict deadlocked the factory for about 60 hours

- **When:** 2026-10-10 00:05Z to 2026-10-12 12:03Z.
- **Symptom:** no commits or comments for about 60 hours; every agent idle.
- **Cause:** the lead told the deployer to hold issue #11 "until my GO" and waited for the reviewer. The reviewer
  (Hermes) wrote `lead <- reviewer: ... FAIL` in its own output but never ran `scion message`, so nobody received it.
  Lead, deployer and reviewer then waited on each other, and nothing woke them up. The FAIL was also wrong: the reviewer
  looked for guessed file names (`ADR-001.md`, `011-gemini-v2-embeddings.sql`) instead of the real
  `knowledge-plane/adr/ADR-001-hybrid-and-vector-1024.md` and `src/iac/migrations/011_add_embedding_v2_gemini_1024.sql`.
- **Fix:** the PO sent the lead the verdict with the correct paths; the reviewer re-checked and delivered PASS through
  `scion message`; heartbeats were added (L5).
- **Encoded in:** "check for `Message delivered`" and "use real paths" in [`_shared/agents.md`](../templates/_shared/agents.md);
  the reviewer's FAIL must cite a file and line or a failing command ([`reviewer`](../templates/reviewer/system-prompt.md));
  GitHub as the record of every handoff.

## L5. Leads looked stalled and nothing woke them

- **When:** found 2026-10-10, fixed 2026-10-12.
- **Symptom:** 8 of 10 agents showed `stalled` in the Hub; leads stopped after "waiting for backend..." and never came
  back on their own.
- **Cause:**
  - A Claude agent that ends its turn without `sciontool status blocked` is flagged STALLED after about 5 minutes. The
    first templates said "Do NOT run `sciontool status`". Scion's own workstation default template (`agents.md`) tells agents to
    use `sciontool status`; in hosted mode, the bundled default template's `agents.md` is empty (0 bytes), so our custom templates must supply this guidance.
  - Agents only react to input. No schedule existed, so a lost message meant a permanent wait.
  - Hub notifications about an agent go to its creator. The admin created all agents, so worker notifications went to
    the PO, not the leads. Repeated identical notifications are deduplicated.
- **Fix:** admin-owned heartbeat schedules (`*/15 * * * *`) that message each lead, and the rule to end every turn
  with a status. Since then the heartbeats have fired every 15 minutes without errors.
- **Encoded in:** [`config/heartbeat-message.txt`](../config/heartbeat-message.txt),
  [setup-runbook.md](setup-runbook.md#9-heartbeats), "End every turn with a status" in
  [`_shared/agents.md`](../templates/_shared/agents.md).

## L6. Antigravity couldn't run any tool (telemetry hook hang) — and its Phase J resolution

- **When:** 2026-10-12 12:17Z (initial workaround 12:31Z, durable workaround 12:38Z; definitively resolved in Phase J on 2026-10-14).
- **Symptom:** every tool call of the Antigravity agents failed; the eval team produced nothing.
- **Initial Diagnosis & Temporary Workaround:** with Hub telemetry and cloud export on, `sciontool hook --dialect=antigravity` printed `{"decision":"allow"}`
  but did not exit (it waited on cloud export to `monitoring.googleapis.com`). Antigravity kills hooks after 10 seconds and
  aborts the tool call. As a temporary workaround, telemetry was turned off in the hook command:
  `... | SCION_TELEMETRY_ENABLED=false sciontool hook --dialect=antigravity`.
- **Root Cause (diagnosed in Phase J):** The container entrypoint (`sciontool init`, PID 1) was failing to bind the local OpenTelemetry
  receiver on ports `127.0.0.1:4317` (gRPC) and `127.0.0.1:4318` (HTTP) because `SCION_GCP_PROJECT_ID` (and GCP credentials) were missing
  from the container runtime environment. Because no background OTLP collector was listening inside the container, short-lived `sciontool hook`
  processes had nowhere to flush locally, attempting synchronous cloud export directly on each hook invocation and hanging when network or credentials were unconfigured.
- **Definitive Fix (Phase J End-to-End Telemetry):**
  1. **Telemetry Infrastructure & Credentials:** [`scripts/setup-telemetry.sh`](../scripts/setup-telemetry.sh) enabled GCP Cloud Logging, Cloud Monitoring,
     and Cloud Trace APIs, provisioned the `scion-telemetry-writer` service account with required IAM roles, and deployed credentials to `~/.scion/telemetry-gcp-credentials.json`
     on the Hub VM and container homes.
  2. **Sciontool Wrapper & `ETXTBSY` Prevention:** [`scripts/patch-sciontool-telemetry.sh`](../scripts/patch-sciontool-telemetry.sh) installs the patched
     binary at `/usr/local/bin/sciontool.real` and unlinks `/usr/local/bin/sciontool` (`rm -f /usr/local/bin/sciontool` to avoid Linux `ETXTBSY` while PID 1
     is running) before installing the executable wrapper `/usr/local/bin/sciontool` across all 10 running containers and base container images
     (`scion-antigravity`, `scion-claude`, `scion-opencode`, `scion-hermes`). The wrapper exports `SCION_GCP_PROJECT_ID`, `SCION_OTEL_GCP_CREDENTIALS`,
     `SCION_TELEMETRY_ENABLED=true`, `SCION_TELEMETRY_CLOUD_PROVIDER=gcp`, and per-harness `SCION_USAGE_SOURCE` / `SCION_MODEL` (`native` + `opus` for
     `claude`; `hooks` + `Gemini 4 Argon (High)` for `antigravity`; `hooks` + `google-vertex/gemini-4-argon` for `opencode`; `hooks` + `google/gemini-4-argon`
     for `hermes`) before executing `sciontool.real`.
  3. **Fix for `collectorEpoch=0` in `flushMetricBuffer` (`pkg/sciontool/telemetry/pipeline.go:915`):** In `sciontool` `20261002`, `metricFlushLoop` ticks 1s
     after startup and calls `flushMetricBuffer`, which initialized `p.metricStreams = newMetricStreams()` with `gcp: false` before any metric arrived via
     `handleMetrics`. Subsequent hook/usage counters inherited `collectorEpoch = 0` (`StartTimeUnixNano: 0`), which Cloud Monitoring rejected with
     `InvalidArgument: Field points[0].interval.start_time had an invalid value ... The start time must be positive`. Setting `p.metricStreams.gcp = p.config.IsGCP()`
     and `p.metricStreams.now = p.now` in both `flushMetricBuffer` and `handleMetrics` resolves this completely.
  4. **Verified Listening Ports & Clean Hook Execution:** On restart, PID 1 initializes the GCP-native cloud exporter and starts the telemetry
     pipeline listening on `127.0.0.1:4317` (gRPC) and `127.0.0.1:4318` (HTTP). Short-lived `sciontool hook` invocations flush immediately to loopback and
     exit in <0.3s.
  5. **Clean Hooks Restored:** [`scripts/patch-antigravity-hooks.py`](../scripts/patch-antigravity-hooks.py) and [`scripts/factory-watchdog.py`](../scripts/factory-watchdog.py)
     restore clean `sciontool hook --dialect=antigravity` calls in all `provision.py`, `.agents/hooks.json`, pre-start scripts, and Hub DB project hooks,
     with telemetry enabled and Gemini 4 Argon preserved.
  6. **Verified End-to-End Flow:** All 10 agent containers report `scion.telemetry.pipeline.status=1`, `gen_ai.api.calls`, `scion.usage.tokens`, and
     `agent.tool.calls` in Google Cloud Monitoring (`Failed:0`), container and server logs flow to `projects/<id>/logs/scion-agents` and
     `projects/<id>/logs/scion-server` in Cloud Logging, traces flow to Cloud Trace, the Hub metrics dashboard API (`GET /api/v1/metrics/dashboard`) serves
     live multi-harness session/call/token metrics at HTTP 200, and [`scripts/sync-factory-agents.py`](../scripts/sync-factory-agents.py) syncs live Cloud Monitoring
     call/tool counts per `scion_agent_id` into Cloud Spanner `factory_agent_status`.
- **Encoded in:** [`scripts/setup-telemetry.sh`](../scripts/setup-telemetry.sh), [`scripts/patch-sciontool-telemetry.sh`](../scripts/patch-sciontool-telemetry.sh),
  [`scripts/patch-antigravity-hooks.py`](../scripts/patch-antigravity-hooks.py), and [`scripts/factory-watchdog.py`](../scripts/factory-watchdog.py).

## L7. The default compute service account is not a runtime identity

- **When:** 2026-10-12 12:30Z (rolled back), fixed 22:46Z.
- **Symptom:** switching `psearch-serving` to Gemini embeddings returned 403 from Vertex AI; the deployer rolled back.
- **Cause:** Cloud Run ran as the project's default compute service account, which lacked `roles/aiplatform.user`.
- **Fix:** a dedicated runtime SA with exactly what the code uses (`roles/aiplatform.user` on the project,
  `roles/spanner.databaseUser` on the database); the deployer has `roles/iam.serviceAccountUser` on it.
- **Also learned:** `gemini-embedding-2` is served at location `global` through `:embedContent` (`:predict` returns 404),
  with `outputDimensionality: 1024`. Spanner full-text search doesn't fold accents: "tenis" doesn't match "Tênis"
  (relevant to the multilanguage issue).
- **Encoded in:** [`scripts/setup-sandbox.sh`](../scripts/setup-sandbox.sh) creates the runtime SA up front; the deployer
  deploys with `--service-account` ([`deployer`](../templates/deployer/system-prompt.md)).

## L8. Workspaces: shared folder today, worktrees later

- **When:** researched 2026-10-10.
- **What we found:**
  - A Hub-managed project without a git remote mounts one shared folder (`~/.scion/projects/<slug>`) as `/workspace`
    in every agent. Parallel issues are impossible and commits must be serialized (the "commit slot").
  - A project's `git_remote` can't be changed later: switching to a git-backed mode means creating new projects.
  - Clone-per-agent works for git-backed projects today. Worktree-per-agent has gaps on a VM broker: it needs host git
    2.47 or newer (the VM had 2.34.1 and silently fell back), the repository's `.git` is not mounted for
    broker-provisioned worktrees, there are no push credentials or git identity in the container, the base clone is
    never fetched, and deleting an agent leaks its worktree (created by UUID, deleted by slug).
- **Decision:** keep the shared workspace with a commit slot for now; move to clone-per-agent with one branch and PR
  per issue in v2; fix worktree-per-agent in Scion as a separate project.
- **Encoded in:** "Git in the shared workspace" in [`_shared/agents.md`](../templates/_shared/agents.md);
  [v2 design](specs/factory-orchestration-v2-design.md).

## L9. Agents fabricated telemetry and claimed storage that didn't exist

- **When:** found 2026-10-12 22:46Z, fixed by 23:25Z.
- **Symptom:** the eval dashboard showed plausible agent counters and a "GitHub repository & branch telemetry" panel that
  stayed empty.
- **Cause:** the eval agents hard-coded counters in `ingest_telemetry.py` and `public/telemetry.json`, reported
  Firestore and bucket storage that had never been created, and the dashboard called `/api/backlog`, a route that
  didn't exist (the server returned `index.html`). Nobody checked the claims against the source.
- **Fix:** real sources only: a Hub-to-Spanner sync of agent state every minute; the GitHub API with the token from
  Secret Manager; probe results only from real runs in `eval_runs`; counters the Hub doesn't report shown as "n/a".
  Firestore and the bucket were then created for real.
- **Encoded in:** "Never invent numbers" in [`_shared/agents.md`](../templates/_shared/agents.md); the data-source table in
  [`eval-lead`](../templates/eval-lead/system-prompt.md); [`scripts/sync-factory-agents.py`](../scripts/sync-factory-agents.py).
  Verify any delivery with `curl` against the live service before you accept it.

## L10. Admin API access from scripts

- **What we found:**
  - Personal access tokens (`scion_pat_...`) are rejected on hub-admin routes (`/api/v1/admin/*`) and can't author
    schedules.
  - An admin user JWT can be minted on the VM from `SCION_SERVER_SESSION_SECRET` in `~/.scion/hub.env`; it is
    equivalent to an admin web session, so keep it short-lived and on the VM.
  - `scion list` on the VM fails outside a project context, and the project settings point at the public (IAP) URL.
    For reads, use the Hub DB read-only (`sqlite3 'file:...hub.db?mode=ro'`) or the localhost API.
  - Docker on the VM belongs to the Hub OS user (`sudo -u scion docker ...`). Read an agent's terminal with
    `docker exec -u scion <project>--<agent> tmux capture-pane -p -t scion:0.0 -S -50`.
- **Encoded in:** [`scripts/scion-admin.py`](../scripts/scion-admin.py), [`scripts/factory-status.sh`](../scripts/factory-status.sh).

## L11. Schedules (heartbeats) semantics

- Each fire is re-authorized against the schedule's creator. A schedule created by an agent stops working if that agent
  is deleted, so the admin creates them.
- Any agent belonging to a project has permission to pause, resume, or edit that project's schedules (M1).
  If heartbeats stall unexpectedly, check that a confused worker has not disabled or re-pointed the schedule.
- Cron expressions are in UTC; the minimum useful interval is about a minute.
- To change a schedule's message, PATCH its `payload` (a JSON string with `agentName` and `message`).
- A schedule can report a successful fire while the message didn't reach the harness, and scheduled messages and
  notifications are not stored as message rows. Judge a heartbeat by its effect (the lead acts, GitHub moves), not by
  the run count alone.
- **Encoded in:** `schedule` subcommands of [`scripts/scion-admin.py`](../scripts/scion-admin.py);
  [operations-runbook.md](operations-runbook.md#a-lead-doesnt-react-to-heartbeats).

## L12. Spanner calls from the VM failed on quota project

- **When:** 2026-10-12 22:55Z.
- **Symptom:** `gcloud spanner databases execute-sql` from the Hub VM was denied although the VM SA had database access.
- **Cause:** the VM SA's quota project defaults to the Hub project, where the call isn't allowed.
- **Fix:** `--billing-project=<sandbox>` on every call, plus `roles/serviceusage.serviceUsageConsumer` for the VM SA on
  the sandbox.
- **Encoded in:** [`scripts/sync-factory-agents.py`](../scripts/sync-factory-agents.py) (`BILLING_PROJECT`),
  [`scripts/setup-sandbox.sh`](../scripts/setup-sandbox.sh).

## L13. Interactive gcloud prompts hang automation

- **When:** first on 2026-10-09, and again on 2026-10-13 while testing `setup-sandbox.sh` in dry-run mode against a
  project that doesn't exist: `gcloud firestore databases describe` waited for an answer to "API not enabled, enable
  and retry?" forever.
- **Fix:** enable APIs explicitly before using them, and set `CLOUDSDK_CORE_DISABLE_PROMPTS=1` in every script and in
  the deploy agents' environment.
- **Encoded in:** [`scripts/setup-sandbox.sh`](../scripts/setup-sandbox.sh), the deployer templates' `env`.

## L14. A shared GitHub token means no PR approvals

- **What we found:** all agents push and comment with the PO's personal access token, so GitHub sees the PO as the
  author of every PR and doesn't let the "reviewer" approve or request changes on its own PR.
- **Decision:** the reviewer posts a signed verdict comment; no branch protection requires approvals. A GitHub App
  (bot identity) is the long-term fix.
- **Encoded in:** [v2 design](specs/factory-orchestration-v2-design.md), "Pull requests".

## L15. The lead wrote the code itself

- **When:** 2026-10-09/10 (issue #11).
- **Symptom:** after two failed backend attempts, the lead implemented #11 itself; nobody orchestrated meanwhile.
- **Fix:** the lead never writes product code; it re-briefs the worker with what went wrong, or escalates.
- **Encoded in:** [`lead`](../templates/lead/system-prompt.md).

## L16. Hand-written Hub DB rows broke templates

- **When:** found 2026-10-12.
- **Symptom:** the five eval templates (`eval-lead`, `eval-deployer`, `factory-analyst`, `search-evaluator`,
  `ui-craftsman`) all point at the `frontend` template's storage path in the Hub DB.
- **Cause:** the templates were registered by inserting rows straight into the Hub's SQLite DB with a copied storage
  path, so the eval agents were actually provisioned from the frontend template's files.
- **Trap:** don't fix it with `scion templates push`. A push to an existing template uploads into that row's storage
  path, which here belongs to `frontend`: it would overwrite the frontend template's files. Deleting the rows with
  their files (`deleteFiles=true`) would delete the frontend's files; deleting them without files leaves the running
  agents pointing at a template ID that no longer exists.
- **Fix (2026-10-13):** rename each broken row to `<role>-legacy` (a metadata PATCH; storage untouched, and running
  agents keep resolving it by ID), then create `<role>` again so it gets its own storage path. Result: five new rows
  with their own files, the legacy rows still resolving for the running agents, and the frontend template's content
  hash unchanged. A plain restart of an eval agent still uses its legacy row, as before; a recreated agent uses the
  new template (reincarnate is refused here, L21).
- **Encoded in:** [`scripts/push-templates.py`](../scripts/push-templates.py), which does this automatically and never
  uploads into a path a template doesn't own; [templates/README.md](../templates/README.md); scripts open the DB
  read-only and write only through the API ([scripts/README.md](../scripts/README.md)).

## L17. Pre-provision the identities the backlog will need

- **When:** 2026-10-12. The #11 rollout waited about 10 hours (12:30Z to 22:29Z) for an IAM decision; the lead
  correctly escalated and reminded the PO every 2 hours.
- **Lesson:** the PO's response time is the factory's slowest step. Decide the identities and grants the backlog needs
  during setup (runtime SA, data stores, secrets) so agents don't wait on them, and keep escalations to genuine
  decisions.
- **Encoded in:** [`scripts/setup-sandbox.sh`](../scripts/setup-sandbox.sh), [setup-runbook.md](setup-runbook.md).

## L18. Hub activity is a hint, not the truth

- **What we found:**
  - Activity is only as good as the harness's hooks. The Hermes reviewer shows `stalled` with its last activity three
    days old, although it reviewed and messaged the lead that morning: Hermes doesn't report activity events here.
  - Agents that finished a turn correctly still show `stalled` if they skipped `sciontool status` (L5).
  - The Hub doesn't populate turn and model-call counters (they stay 0); that's "unknown", not "zero".
- **Lesson:** decide liveness from evidence (GitHub comments and commits, delivered messages, the agent's terminal), and
  use Hub activity only as a pointer to where to look.
- **Encoded in:** [`scripts/factory-status.sh`](../scripts/factory-status.sh) shows all of these side by side;
  [operations-runbook.md](operations-runbook.md).

## L19. Heartbeats can turn into chatter

- **When:** 2026-10-12 23:45Z.
- **Symptom:** on each heartbeat the eval lead sent `HEARTBEAT_PING` to all three specialists, and each answered with a
  `PONG`/`ACK`: six messages and four model turns every 15 minutes with no work behind them.
- **Fix:** the heartbeat says to ping only agents you are waiting on, and to do nothing but set the status when nothing
  needs action.
- **Encoded in:** [`config/heartbeat-message.txt`](../config/heartbeat-message.txt), "Messaging" in
  [`_shared/agents.md`](../templates/_shared/agents.md).

## L20. Local patches don't survive upgrades silently

- **What we found:** In hosted Hub mode, the broker provisions agents from `~/.scion/workspace-storage/hubs/<hub-id>/harness-configs/global/<harness>/provision.py`
  rather than bare `~/.scion/harness-configs/`. Additionally, each agent container maintains its own local copy under
  `<agent-home>/.scion/harness/provision.py` which executes `20-harness-provision` on container startup. Simply patching
  bare `~/.scion/harness-configs/` does not affect hosted agent provisioning. Furthermore, automatic nightly channel updates
  or container rebuilds can wipe local harness file edits.
- **Lesson & Solution:** A multi-tier durable hook architecture is required:
  1. Patch both the Hub's workspace-storage global provisioner and existing agent-home provisioners.
  2. Install a container pre-start hook script (`30-project-custom`) in `<agent-home>/.scion/hooks/pre-start.d/` (mode 0700)
     to re-apply the hook patch inside `/workspace/.agents/hooks.json` on container start.
  3. Register and activate the `agy-telemetry-off` hook in the Hub SQLite database (`project_pre_start_hooks` table)
     for all active projects, ensuring new agent containers automatically inherit the hook.
- **Encoded in:** [`scripts/patch-antigravity-hooks.py`](../scripts/patch-antigravity-hooks.py) (supports check mode, `--apply`,
  and checks both filesystem and Hub DB), [operations-runbook.md](operations-runbook.md#after-a-scion-upgrade).

## L21. Reincarnate is refused for agents of these projects

- **When:** 2026-10-13, preparing to move `eval-lead` from Antigravity to Claude Code.
- **Symptom:** `POST /api/v1/projects/<id>/agents/<agent-id>/reincarnate` with `{"dryRun": true}` returns HTTP 400
  "reincarnate requires a clone-per-agent, shared-workspace or hub-managed workspace" for the agents of both
  Hub-managed projects (no git remote, broker on the Hub VM).
- **Cause (from the code):** the eligibility check in `pkg/hub/handlers_agent_reincarnate.go` uses the agent's
  "effective" workspace, and for a project linked on the broker the agent's absolute workspace path
  (`~/.scion/projects/<slug>`) is cleared first, so the agent looks like it has no workspace at all. Probably a Scion
  bug for this topology; worth reporting upstream.
- **Workaround:** recreate the agent instead: put the state on GitHub and write a short handoff, `scion delete
  <agent>`, then `scion start <agent> -t <template> "<handoff>"`. The new agent has a new ID: schedules address agents
  by name and keep working, but notification subscriptions and message history don't carry over.
- **Encoded in:** [operations-runbook.md](operations-runbook.md#switch-an-agents-harness).

## L22. Hub secret injection: `as_needed` vs `always`

- **What we found:** When secrets are stored in the Hub with `injection_mode: as_needed` (the default for some CLI paths),
  they are only injected into agent containers whose template explicitly lists that secret name in its `secrets` stanza.
  If an agent's template omits `GITHUB_TOKEN`, git pushes, issue comments, and PR management fail with authentication errors.
- **Decision:** Critical infrastructure secrets shared across all factory agents (like `GITHUB_TOKEN`) must have
  `injection_mode: always`. This guarantees every agent container receives the environment variable at boot time,
  preventing template misconfiguration from breaking agent operations.
- **Encoded in:** Hub database `secrets` table (`UPDATE secrets SET injection_mode='always' WHERE key='GITHUB_TOKEN'`),
  [setup-runbook.md](setup-runbook.md#5-github), [operations-runbook.md](operations-runbook.md#rotate-the-github-token).

## L23. Deployer workspace cleanliness and Cloud Run traffic routing

- **What we found:**
  - In a shared workspace, running `gcloud run deploy --source .` directly packages the entire workspace folder,
    including untracked files, scratch scripts, or concurrent edits from other agents.
  - When rolling back a Cloud Run service using `gcloud run services update-traffic --to-revisions <rev>=100`, traffic
    remains pinned to that revision. A subsequent `gcloud run deploy` builds and deploys a new revision, but Cloud Run
    continues routing 100% of traffic to the pinned rollback revision unless `--to-latest` is explicitly specified.
- **Fix:**
  - Deployers must export the target commit cleanly using `git archive "$REV" | tar -x -C "$TMP_DIR"` and deploy from `$TMP_DIR`.
  - The deployer system prompt specifies passing `--to-latest` on deployments (or running `gcloud run services update-traffic --to-latest`)
    to re-enable automatic traffic routing to newly deployed revisions after a rollback.
- **Encoded in:** [`templates/deployer/system-prompt.md`](../templates/deployer/system-prompt.md),
  [`templates/eval-deployer/system-prompt.md`](../templates/eval-deployer/system-prompt.md),
  [operations-runbook.md](operations-runbook.md#a-deploy-failed-or-needs-a-rollback).

## L24. Unannounced Hub restarts overwrite managed harness files (`update_policy: notify`)

- **What we found:** On startup, `scion server` executes `OverwriteBuiltinManaged`, which overwrites
  `workspace-storage/.../harness-configs/global/antigravity/provision.py` with default builtin templates.
  When `server.maintenance.update_policy` was set to `auto`, nightly Scion updates triggered unannounced service restarts,
  silently reverting the Antigravity telemetry hook patch and causing tool calls to time out (L6).
- **Fix:**
  1. Set `server.maintenance.update_policy: notify` in `~/.scion/settings.yaml`. Updates notify the operator without auto-restarting.
  2. The watchdog daemon ([`scripts/factory-watchdog.py`](../scripts/factory-watchdog.py)) runs every 5 minutes and immediately
     detects and re-patches any provisioner drift if the service is restarted.
- **Encoded in:** [`config/hub-settings.example.yaml`](../config/hub-settings.example.yaml),
  [`scripts/factory-watchdog.py`](../scripts/factory-watchdog.py),
  [operations-runbook.md](operations-runbook.md#after-a-scion-upgrade).

## L25. Initialize `EVAL_BRANCH` as an orphan branch (`git switch --orphan`) so it can never be merged into `main`

- **What we found:** When two parallel factories (`psearch` building the product and `psearch-eval` observing it) share a single GitHub repository, branching `eval-dashboard` (`EVAL_BRANCH`) off `main` (`BUILD_BRANCH`) creates shared Git ancestry. This introduces two serious failure modes:
  1. The evaluation workspace inherits product application code, build files, and Dockerfiles (`cmd/`, `internal/`, `go.mod`, etc.) that are irrelevant to the eval dashboard and cause confusing git conflicts or mistaken test runs.
  2. A confused agent or automated tool can attempt to merge, rebase, or open a pull request between the branches, risking product regressions or polluting the product history with eval tooling.
- **Fix:**
  - Initialize `EVAL_BRANCH` as an **orphan branch** using `git switch --orphan "$EVAL_BRANCH"` (or create an empty root commit and push).
  - An orphan branch has 0 shared ancestor commits with `main` (`git merge-base origin/main origin/eval-dashboard` exits 1). Git and GitHub will refuse unrelated history merges by default.
  - In existing repositories where `eval-dashboard` was branched off `main`, rewrite the branch root or strip stale application files from `eval-dashboard` history using `git filter-branch` while temporarily pausing branch protection rulesets.
- **Encoded in:** [setup-runbook.md](setup-runbook.md#6-hub-projects-and-the-deployer-identity),
  [architecture.md](architecture.md#components),
  [agent-protocol.md](agent-protocol.md#git-in-a-shared-workspace),
  [`templates/_shared/agents.md`](../templates/_shared/agents.md),
  [`templates/lead/system-prompt.md`](../templates/lead/system-prompt.md),
  [`templates/eval-lead/system-prompt.md`](../templates/eval-lead/system-prompt.md).

## L26. Multi-harness upgrade to Gemini 4 Argon (Antigravity, OpenCode, Hermes)

- **When:** 2026-10-13 / 2026-10-14.
- **Goal:** Upgrade all factory worker harnesses (6 Antigravity agents, 1 OpenCode backend agent, 1 Hermes reviewer agent) from legacy Gemini 2.5 / 3.x to Gemini 4 Argon on Vertex AI while leaving Claude Code agents (lead, deployer) undisturbed.
- **Symptom & Root Causes across harnesses:**
  1. **Vertex AI Global Endpoint (`locations/global`):**
     Gemini 4 Argon on Vertex AI is served from the global publisher location (`https://aiplatform.googleapis.com/v1/projects/.../locations/global/publishers/google/models/gemini-4-argon`). Regional endpoints (e.g. `us-central1`) return `404 Not Found` or model not recognized. All harnesses require explicit global location overrides (`GOOGLE_CLOUD_LOCATION=global`, `VERTEX_LOCATION=global`, `VERTEX_REGION=global`).
  2. **Hermes Configuration Override Precedence:**
     Hermes loads `~/.hermes/.env` with `override=True` (in `env_loader.py`). Even if `~/.hermes/config.yaml` specifies `vertex.region: global`, an existing or defaulted `VERTEX_REGION=us-central1` in `~/.hermes/.env` overrides it silently, causing 404 errors on Vertex AI. The launch script must ensure `VERTEX_REGION=global` is written to `~/.hermes/.env`.
  3. **Hermes CLI Flag Incompatibility (`--create-if-missing`):**
     Scion's default harness config for Hermes configured `command.base: ["hermes", "chat", "--yolo", "-c", "scion-session", "--create-if-missing"]`. However, the Hermes CLI does not recognize `--create-if-missing` (it only supports `-c / --continue`). Passing this unrecognized flag caused the container entrypoint to immediately exit with code 2. The solution is the wrapper pattern `base: ["sh", "/home/scion/.local/bin/hermes-launch.sh"]` with `task_flag: "--prompt"` and `task_position: "before_base_args"`.
  4. **Scion Hosted Harness Config Caching (`~/.scion/cache/harness-configs/`):**
     In Scion's hosted/broker architecture, the Runtime Broker's `HCResolver` resolves and caches harness configurations under `~/.scion/cache/harness-configs/<contentHash>/config.yaml`. Updating only `~/.scion/harness-configs/` does not update running or restarted containers if SQLite `hub.db` or cached hashes point to the bundled configs in `workspace-storage/hubs/<hub-id>/harness-configs/global/`. To update a harness persistently, the harness config must be updated in `hub.db`, `workspace-storage`, and the broker cache cleared or updated.
  5. **Antigravity Role Directory Detection:**
     In Scion project staging, agent configurations are organized by agent role name under `~/.scion/project-configs/<project>__<id>/.scion/agents/<role>/home/`. The previous provisioner hook patch script only checked agent names, missing custom role directories or misidentifying role folders during provisioner patching.
- **Fix:**
  - Automated patching via [`scripts/patch-antigravity-hooks.py`](../scripts/patch-antigravity-hooks.py) and [`scripts/patch-opencode-and-hermes.py`](../scripts/patch-opencode-and-hermes.py).
  - Configured `model: "Gemini 4 Argon (High)"` and `location: global` across all 6 Antigravity agent templates and container `settings.json`.
  - Configured OpenCode catalog `models.json` with `gemini-4-argon` and `gemini-3.8-flash`, setting `GOOGLE_CLOUD_LOCATION=global` and `model: google-vertex/gemini-4-argon`.
  - Added wrapper [`templates/reviewer/home/.local/bin/hermes-launch.sh`](../templates/reviewer/home/.local/bin/hermes-launch.sh) for Hermes, setting `VERTEX_REGION=global` and launching `hermes chat --yolo --accept-hooks --provider vertex -m google/gemini-4-argon`.
  - Pushed updated templates to Hub via [`scripts/push-templates.py`](../scripts/push-templates.py).
- **Encoded in:**
  - [`templates/backend/home/.local/bin/opencode-launch.sh`](../templates/backend/home/.local/bin/opencode-launch.sh)
  - [`templates/reviewer/home/.local/bin/hermes-launch.sh`](../templates/reviewer/home/.local/bin/hermes-launch.sh)
  - [`templates/frontend/scion-agent.yaml`](../templates/frontend/scion-agent.yaml)
  - [`templates/eval-deployer/scion-agent.yaml`](../templates/eval-deployer/scion-agent.yaml)
  - [`templates/eval-lead/scion-agent.yaml`](../templates/eval-lead/scion-agent.yaml)
  - [`templates/factory-analyst/scion-agent.yaml`](../templates/factory-analyst/scion-agent.yaml)
  - [`templates/search-evaluator/scion-agent.yaml`](../templates/search-evaluator/scion-agent.yaml)
  - [`templates/ui-craftsman/scion-agent.yaml`](../templates/ui-craftsman/scion-agent.yaml)
  - [`scripts/patch-antigravity-hooks.py`](../scripts/patch-antigravity-hooks.py)
  - [`scripts/patch-opencode-and-hermes.py`](../scripts/patch-opencode-and-hermes.py)
  - [`scripts/push-templates.py`](../scripts/push-templates.py)

## L27. Cross-process trace state (`tracestate.go`) and stacked parent-child conversation traces in Cloud Trace

- **When:** 2026-10-15.
- **Symptom:** Traces from agent containers appeared in Cloud Trace, but every hook invocation (`agent.turn.start`, `agent.tool.result`, `gen_ai.api.response`, `agent.turn.end`) showed up as an isolated ~0ms root span with `parentSpanId: null` and a random `traceId`. Operators could not expand a waterfall of a turn or follow a multi-turn conversation path.
- **Root Causes:**
  1. **Ephemeral Hook Processes + In-Memory `sync.Map`:** Every harness hook (`PreInvocation`, `PostInvocation`, `PreToolUse`, `PostToolUse`, `Stop`) executes as a separate short-lived `sciontool hook` process. `TelemetryHandler` stored in-flight spans in an in-memory `sync.Map` (`h.spanStore`). Because `startSpan` never called `span.End()`, start events (`PreInvocation`, `PreToolUse`) were discarded when their hook process exited; end events (`PostInvocation`, `PostToolUse`, `Stop`) ran in a fresh process where `h.spanStore` was empty and fell back to `singleSpan(context.Background())` — emitting a ~0ms root span with a brand-new `TraceID`.
  2. **Claude Code Native OTLP Logs Lacked Trace Context & Child Spans:** Claude Code emits `api_request` events as native OTLP logs to `127.0.0.1:4317` without a `TraceId`/`SpanId`, so Claude API calls were invisible inside the turn's Cloud Trace waterfall.
  3. **Antigravity `PostToolUse` Omitted `tool_name`:** Antigravity's `PostToolUse` hook payload only carries `response` (omitting `toolCall.name`), so without state persisted from `PreToolUse`, `agent.tool.result` spans had empty `tool_name`.
  4. **Sticky `/tmp` Ownership Between `root` and `scion`:** Container lifecycle hooks (`post-start`) run as `root` (`uid=0`), whereas harness hooks run as `scion` (`uid=1000`). Writing `/tmp/scion-trace-state.json` with `0600` or via `os.Rename` failed across `root`/`scion` in sticky `/tmp` (`1777`).
- **Fix:**
  1. **Cross-Process `TraceState` (`/tmp/scion-trace-state.json`):** Implemented `pkg/sciontool/telemetry/tracestate.go` with `syscall.Flock` locking and world-rw (`0666`) in-place truncation (`O_CREATE|O_RDWR|O_TRUNC`), plus `ExplicitIDGenerator` so `sdktrace.TracerProvider` honors persisted `TraceID`, `TurnSpanID`, `PendingModel`, and `PendingTools` across hook processes.
  2. **Hierarchical Turn Waterfall (`agent.turn/<agent_slug>`):**
     - Root span `agent.turn/<agent_slug>` (`SpanID = TurnSpanID`, `ParentSpanID = nil`) spans `[TurnStartTime, TurnEndTime]` with summary attributes (`turn.index`, `turn.model_calls`, `turn.tool_calls`, cumulative token usage, `scion.previous_trace_id`) and an OTel **Span Link** (`link.type = "previous_turn"`) to the previous turn in the conversation.
     - Child spans (`ParentSpanID = TurnSpanID`) share the turn's `TraceID` and real `[StartTime, EndTime]` durations: `agent.turn.prompt`, `gen_ai.api.response/<model>`, `agent.tool.result/<tool_name>`, and `agent.status/<status_type>` (which nests 3 levels deep under the active tool span when `sciontool status` runs inside a tool call).
  3. **Claude Code Log-to-Span Synthesis & Cloud Logging Correlation:** `CorrelateAndSynthesizeClaudeSpans` attaches the active turn's `TraceID`/`SpanID` to native OTLP logs and synthesizes `gen_ai.api.response/<model>` child spans for `api_request`/`api_error`. `protoLogToCloudEntry` sets top-level `entry.SpanID`, `entry.TraceSampled = true`, and `SCION_GCP_PROJECT_ID` fallback so logs appear inline inside Cloud Trace waterfalls.
  4. **Safe Live Deployment:** Update `/usr/local/bin/sciontool.real` in running containers via `docker cp` ([`scripts/patch-sciontool-telemetry.sh`](../scripts/patch-sciontool-telemetry.sh)) — never run `docker restart` on `--rm` Scion containers.
- **Encoded in:** [`scripts/patch-sciontool-telemetry.sh`](../scripts/patch-sciontool-telemetry.sh), [`docs/telemetry/reproduce-telemetry.md`](telemetry/reproduce-telemetry.md).

## L28. Several factories in one monorepo: names, start folders, and what the e2e showed

- **When:** 2026-10-22, on a throwaway single-node Hub (Scion `nightly-20261022`) with a private monorepo
  (`src/project1..3`), two factories × (`lead` on Claude Code, `eval-lead` on Antigravity), plus one shared-mode lead.
- **Problem:** several factories on one Hub need distinct agent names (bare `lead` in every project makes the CLI exit 2,
  "agent name found in several projects", and every clone-per-agent lead pushes `scion/lead`), and each agent should
  start in its product folder while keeping the whole repository. Scion has no working-directory setting: the Docker
  runtime always sets `--workdir /workspace` (`pkg/runtime/common.go`), and a relative `--workspace <subdir>` mounts only
  that subtree.
- **Fix:**
  1. `AGENT_PREFIX` renders every peer reference as `${<ROLE>_AGENT}` → `<prefix>-<role>`; start agents with
     `render-templates.py --agent-names`.
  2. `FACTORY_SUBDIR` + [`factory-cd.sh`](../config/harness/home/.local/bin/factory-cd.sh) in custom-named
     `<harness>-factory` harness-configs ([`make-factory-harness-configs.py`](../scripts/make-factory-harness-configs.py)).
     Bundled configs are refreshed from the binary on every Hub restart, so they are never edited.
- **Evidence (e2e):** `scion list` showed `project1-lead` / `claude-factory` and `project1-eval-lead` /
  `antigravity-factory`, containers `project1--project1-lead`; `readlink /proc/<pid>/cwd` was `/workspace/src/project1`
  for both the `claude` and the `agy` process (`/workspace/src/project3` in shared mode); `ls /workspace/src` listed all
  three products; four distinct `scion/<agent>` branches pushed; no trust prompt; Antigravity status hooks fired from the
  product folder (activity `completed`), `git status` stayed clean; Claude on Vertex answered an admin message.
- **Gotchas found:**
  - Messaging is per Hub project: `scion message project1-eval-lead` from `project1-lead` → `agent_not_found` (404).
  - `push-templates.py` fails on a fresh Hub (`default home directory not found`): the default template is stored as
    blobs. `scion templates sync <role> --project=<slug>` works.
  - `scion hub projects create` from the same URL a second time needs `--yes` when not on a TTY; shared mode clones at
    creation and needs `githubToken` through the API (the CLI has no token flag).
  - In clone-per-agent mode the leads flagged that `_shared/agents.md` describes a shared checkout on `main`.
  - `deploy.sh` in a new org project: enable `cloudresourcemanager.googleapis.com` first, create the `default`
    network if the org skips it, and re-run after a transient IAP SSH `502` or a "service account does not exist"
    right after creating it (the script is idempotent). Its `build` image source builds only Antigravity; build
    `scion-claude` on the VM for Claude Code agents, and enable the Claude model in Model Garden per project
    (otherwise the harness starts but answers "model … is not available on your vertex deployment").
- **Second e2e (shared directories and knowledge planes, runbook M1–M8 run as written on a fresh Hub):**
  - `POST /api/v1/projects` has no branch field: the branch goes in the `scion.dev/default-branch` label, else the
    Hub clones `main` (the eval projects first came up on `main`). `scion-admin.py project-create` sends the labels.
  - The Hub stores `githubToken` as the project's `GITHUB_TOKEN` secret with injection mode `always`.
  - New Hub projects already have a `scratchpad` shared directory; `shared-dirs --apply` added `knowledge-plane`.
  - `shared-dirs/` on the broker appears only when the first agent starts, so the watchdog now fills
    `knowledge-plane` as soon as the project's `project-configs/<slug>__<id8>/` exists; the agents' first turn
    already read `KP-project1` / `KP-project2`. Scion keeps the pre-filled folder when it mounts it.
  - Inside every agent: `/scion-volumes` = `knowledge-plane scratchpad`; each factory saw its own product's
    knowledge plane; writes to it were refused; the scratchpad is per Hub project and appears in the Hub's Files
    API (the tabs next to `workspace`); an edit to the source reached a running agent on the next watchdog run;
    the cron run kept `KNOWLEDGE_PLANE_SOURCES` from `watchdog.env`.
  - A runbook loop that relied on bash word splitting (`set -- $p`) broke in zsh, macOS's default shell: runbook
    commands are now written out one per project.
- **Encoded in:** [`scripts/render-templates.py`](../scripts/render-templates.py),
  [`scripts/make-factory-harness-configs.py`](../scripts/make-factory-harness-configs.py),
  [`config/harness/home/.local/bin/factory-cd.sh`](../config/harness/home/.local/bin/factory-cd.sh),
  [`scripts/factory-watchdog.py`](../scripts/factory-watchdog.py), [`scripts/scion-admin.py`](../scripts/scion-admin.py)
  (`project-create`, `shared-dirs`), [setup-runbook.md](setup-runbook.md#several-factories-in-one-monorepo).
