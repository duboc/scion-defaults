# Overnight Autonomous Factory Learnings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Codify the six operational, coordination, safety, and self-healing learnings from the 20-hour, 30-issue autonomous specialist-fabric run into `scion-defaults` scripts, shared protocols, role library, preflight checks, and documentation.

**Architecture:** Four self-contained tasks harden (1) the Hub watchdog's schedule guard, env-file precedence, and knowledge-plane sync reporting (`scripts/factory-watchdog.py`, `scripts/run-on-hub.sh`); (2) shared-worktree concurrent commit-slot safety and graceful partial delivery on GitHub token-scope rejections (`templates/_shared/agents.md`, `scripts/scion_factory.py`); (3) specialist protocols for IaC/Terraform + CI workflows, cross-app optional-dependency test imports, and coupled-file co-commits (`scripts/role_library.py`); and (4) GitHub PAT scope documentation, Hub token preflight warnings, and Lesson L31 (`scripts/hub-preflight.py`, `README.md`, `docs/setup-runbook.md`, `docs/lessons-learned.md`).

**Tech Stack:** Python 3.11+ (`sqlite3`, `subprocess`, `argparse`, `pathlib`), Bash (`set -euo pipefail`), Markdown templates, `pytest` via `uv run --with pytest --with pyyaml pytest tests -q`.

**Spec:** [`docs/specs/2026-10-26-specialist-agent-fabric-design.md`](../specs/2026-10-26-specialist-agent-fabric-design.md) and [`AGENTS.md`](../../AGENTS.md)

## Global Constraints

- Zero secrets, PATs (`ghp_*`, `github_pat_*`, `scion_pat_*`), emails, real GCP project IDs/numbers, or VM names in any tracked file.
- Every template in `templates/` must render cleanly via `scripts/render-templates.py` with zero leftover `${VAR}` placeholders (`tests/test_shared_dirs.py::test_rendered_prompts_and_configs_have_no_placeholders_left`).
- Run the full suite with `uv run --with pytest --with pyyaml pytest tests -q` after every task (baseline: `310 passed, 3 skipped`).
- Never add `Co-Authored-By` or AI session trailers to commits.

## Review Focus

1. **Shared Hub multi-factory schedule isolation:** `check_heartbeat_schedules` on a Hub hosting unrelated projects outside `FACTORY_PROJECTS` must not resume schedules belonging to non-factory projects, and must ignore soft-deleted schedules (`status = 'deleted'`).
2. **Env-file precedence in `factory-watchdog.py`:** Explicit environment variables (`os.environ`) must override `watchdog.env`, and `watchdog.env` must override any legacy `~/.scion/sync.env`.
3. **Template placeholder hygiene in `templates/_shared/agents.md`:** Any new shell snippet in `templates/_shared/agents.md` must avoid `${...}` syntax for local shell variables unless listed in `scripts/render-templates.py` (`VARS`), using `$var` or `<placeholder>` instead so `test_rendered_prompts_and_configs_have_no_placeholders_left` stays green.
4. **Cross-team branch neutrality in `_render_shared_agents_md`:** Because `compile_factory_boilerplate` writes the same `shared_md` to both `build_roles` and `eval_roles`, Section 4 (`Pre-Commit Verification & Commit Slot`) must reference the team's assigned trunk (`build_branch` for build, `eval_branch` for eval) rather than telling eval agents to rebase onto `build_branch`.
5. **Read-only secret safety in `hub-preflight.py`:** Checking `~/.factory-github-token` must inspect only file existence and non-zero size (`os.path.isfile` / `os.path.getsize > 0`), never reading or echoing token contents into JSON or stdout.

---

### Task 1: Watchdog Schedule Guard, Cron Env Precedence, and Quiet Knowledge-Plane Sync

**Files:**
- Modify: `scripts/factory-watchdog.py:60-95,395-446,790-860`
- Modify: `scripts/run-on-hub.sh:24-55`
- Test: `tests/test_watchdog_wake.py`
- Test: `tests/test_shared_dirs.py`

**Interfaces:**
- Produces:
  - `load_runtime_env(watchdog_env: str, sync_env: str, environ: MutableMapping[str, str]) -> None` in `scripts/factory-watchdog.py` (loads `watchdog_env` first, then `sync_env`, preserving pre-existing keys in `environ`).
  - `check_heartbeat_schedules(conn, apply: bool) -> bool` guarding `p.slug IN (FACTORY_PROJECTS)` where `s.status <> 'deleted'` and `(s.name LIKE '%heartbeat%' OR s.name LIKE 'eval-sweep%')`.
  - `watchdog_env_content(path: str) -> str` persisting `WATCHDOG_SUBPROCESS_TIMEOUT_SEC` when set in `os.environ`.
  - `WATCHDOG_SUBPROCESS_TIMEOUT_SEC` forwarded in `scripts/run-on-hub.sh`.

- [ ] **Step 1: Write the failing tests in `tests/test_watchdog_wake.py` and `tests/test_shared_dirs.py`**

Add tests pinning:
1. `test_load_runtime_env_prefers_cli_then_watchdog_then_sync(watchdog, tmp_path)` — CLI `os.environ` beats `watchdog.env`, and `watchdog.env` beats `sync.env`.
2. `test_check_heartbeat_schedules_guards_lead_and_eval_sweep_in_factory_projects_only(watchdog, monkeypatch, capsys)` — in-memory SQLite `projects` + `schedules` with:
   - `proj1` (`bananacast-lead-heartbeat`, `active`)
   - `proj1-eval` (`eval-sweep-factory-main`, `paused`) -> resumed via `api("POST", "/api/v1/projects/p2/schedules/s2/resume")`
   - `proj1-eval` (`old-heartbeat`, `deleted`) -> ignored
   - `other-proj` (`other-lead-heartbeat`, `paused`) -> ignored because `other-proj` is not in `FACTORY_PROJECTS`.
3. Extend `test_watchdog_env_keeps_the_knowledge_plane_settings_for_cron` and `test_run_on_hub_forwards_the_knowledge_plane_settings` in `tests/test_shared_dirs.py` to assert `WATCHDOG_SUBPROCESS_TIMEOUT_SEC` is written into `watchdog.env` and forwarded by `scripts/run-on-hub.sh`.
4. `test_sync_reports_up_to_date_when_rsync_makes_no_changes(watchdog, tmp_path, monkeypatch, capsys)` — running `check_shared_knowledge_plane(conn, apply=True)` twice prints `synced` on the first run and `up to date` on the second unchanged run.

- [ ] **Step 2: Run targeted tests to verify they fail**

Run: `uv run --with pytest --with pyyaml pytest tests/test_watchdog_wake.py tests/test_shared_dirs.py -q`
Expected: FAIL (`AttributeError: module ... has no attribute 'load_runtime_env'`, `eval-sweep-factory-main` not resumed, `WATCHDOG_SUBPROCESS_TIMEOUT_SEC` missing).

- [ ] **Step 3: Implement in `scripts/factory-watchdog.py` and `scripts/run-on-hub.sh`**

1. In `scripts/factory-watchdog.py`:
   - Add `load_runtime_env(watchdog_env=WATCHDOG_ENV, sync_env=SYNC_ENV, environ=os.environ)` that loads `watchdog_env` before `sync_env` without overwriting existing keys in `environ`.
   - Update `check_heartbeat_schedules(conn, apply)` to return `True` early if `not FACTORY_PROJECTS`, otherwise query `projects` + `schedules` filtered by `p.slug IN (...) AND s.status <> 'deleted' AND (s.name LIKE '%heartbeat%' OR s.name LIKE 'eval-sweep%')`.
   - Update `check_shared_knowledge_plane(conn, apply=False)` to run `rsync -ai --delete` with `capture_output=True, text=True` when `apply=True`, reporting `synced (<N> changes)` when itemized lines exist and `up to date` when zero files changed.
   - Add `"WATCHDOG_SUBPROCESS_TIMEOUT_SEC"` to the optional keys loop in `watchdog_env_content(path)`.
2. In `scripts/run-on-hub.sh`:
   - Add `WATCHDOG_SUBPROCESS_TIMEOUT_SEC` to both `overrides` and `forward` variable lists (and header comment).

- [ ] **Step 4: Run targeted and full tests to verify they pass**

Run: `uv run --with pytest --with pyyaml pytest tests -q`
Expected: PASS (`313+ passed, 3 skipped`).

- [ ] **Step 5: Commit**

```bash
git add scripts/factory-watchdog.py scripts/run-on-hub.sh tests/test_watchdog_wake.py tests/test_shared_dirs.py
git commit -m "fix(watchdog): guard eval-sweep schedules, fix env precedence, and quiet unchanged KP syncs"
```

---

### Task 2: Shared-Worktree Commit-Slot Safety & Token-Scope Partial Delivery

**Files:**
- Modify: `templates/_shared/agents.md:46-68`
- Modify: `scripts/scion_factory.py:1017-1025`
- Test: `tests/test_shared_dirs.py`
- Test: `tests/test_human_on_the_loop.py`

**Interfaces:**
- Consumes: none.
- Produces: updated `templates/_shared/agents.md` and `scion_factory._render_shared_agents_md(spec)` with path-scoped staging, remote-ahead check before rebasing, ban on `git stash -u` / `.scion/` / `.agents/`, branch-neutral Build vs Eval trunk guidance, co-commit allowance for coupled `PASS` issues, and `/scion-volumes/scratchpad/pending-<N>/` patch fallback on token-scope push rejections.

- [ ] **Step 1: Write the failing tests in `tests/test_shared_dirs.py` and `tests/test_human_on_the_loop.py`**

Add tests pinning:
1. `test_shared_agents_template_forbids_stash_untracked_and_scopes_staging(tmp_path)` in `tests/test_shared_dirs.py`:
   - Rendered `lead/agents.md` does **not** contain `git stash push -u`.
   - Contains `git add --` (explicit file paths), `git diff --cached --name-only`, `.scion/`, `.agents/`, `rev-list --count`, and `/scion-volumes/scratchpad/pending-`.
2. `test_compiled_shared_agents_md_is_branch_neutral_and_documents_pending_patch_fallback(kit)` in `tests/test_human_on_the_loop.py`:
   - `(kit / "templates" / "_shared" / "agents.md").read_text()` mentions both `build_branch` and `eval_branch` (never instructing eval agents to rebase onto `build_branch`), requires explicit `git add --` + `git diff --cached --name-only` verification, forbids `git stash -u`, and documents saving unpushable files (such as `.github/workflows/*` without `workflow` scope) to `/scion-volumes/scratchpad/pending-` paired with a `status:po-decision` issue while keeping other backlog items moving.

- [ ] **Step 2: Run targeted tests to verify they fail**

Run: `uv run --with pytest --with pyyaml pytest tests/test_shared_dirs.py tests/test_human_on_the_loop.py -q`
Expected: FAIL (`git stash push -u` still present in `templates/_shared/agents.md`; `pending-` and `git diff --cached --name-only` missing).

- [ ] **Step 3: Update `templates/_shared/agents.md` and `scripts/scion_factory.py`**

1. In `templates/_shared/agents.md` (`## Git in the shared workspace`):
   - Replace the `git stash push -u` instruction with safe shared-worktree commit-slot rules (using `$ahead` or `<branch>`, never `${UNDECLARED}`):
     - Stage only your assigned paths (`git add -- <files>`), verify `git diff --cached --name-only` lists only those paths, and never stage or stash `.scion/` or `.agents/`.
     - Check `git fetch origin <branch> && git rev-list --count HEAD..origin/<branch>` first: when `0`, commit and push directly without `git pull --rebase` or `git stash`. When remote is ahead, stash only your own paths (`git stash push -m "pre-rebase" -- <files>`, never `git stash -u`), rebase onto `origin/<branch>`, pop, re-verify, and push (or ask the lead if shared files conflict).
     - Never run `git add -A`, `git add .`, `git add -u`, `git stash -u`, `git reset --hard`, `git checkout -- .`, `git clean`, or a force push.
     - If `git push` is rejected due to token scope restrictions (for example `.github/workflows/*` without `workflow` scope), unstage the restricted paths, save them as a patch in `/scion-volumes/scratchpad/pending-<N>/`, push the permitted paths, and report the patch path to your lead so the lead opens a `status:po-decision` issue without stalling other work.
2. In `scripts/scion_factory.py` (`_render_shared_agents_md`, `integrate` section around line 1017):
   - Make Section 4 branch-aware for both teams (`{vcs['build_branch']}` in the build project, `{vcs['eval_branch']}` in the eval project) and mirror the same safe commit-slot steps (`git fetch` + `rev-list --count` check before rebase, path-scoped `git add -- <files>` + `git diff --cached --name-only`, ban on `git stash -u` / `.scion/` / `.agents/`, coupled-file co-commits authorized by the lead, and `/scion-volumes/scratchpad/pending-<issue>/` + `status:po-decision` on token-scope push rejection).

- [ ] **Step 4: Run targeted and full tests to verify they pass**

Run: `uv run --with pytest --with pyyaml pytest tests -q`
Expected: PASS (including `test_rendered_prompts_and_configs_have_no_placeholders_left`).

- [ ] **Step 5: Commit**

```bash
git add templates/_shared/agents.md scripts/scion_factory.py tests/test_shared_dirs.py tests/test_human_on_the_loop.py
git commit -m "fix(protocol): harden shared-worktree commit slots and token-scope partial delivery"
```

---

### Task 3: Specialist Role Protocols — IaC/Terraform `migration-specialist`, Cross-App `importorskip`, and Lead Co-Commits

**Files:**
- Modify: `scripts/role_library.py:54-105,208-289`
- Test: `tests/test_role_library.py`

**Interfaces:**
- Consumes: `role_library.ROLES`, `role_library.role_protocol(role, deploy="service")`.
- Produces: updated responsibilities and protocols for `migration-specialist`, `backend`, `test-engineer`, `reviewer`, and `lead`.

- [ ] **Step 1: Write the failing tests in `tests/test_role_library.py`**

Add tests pinning:
1. `test_migration_specialist_covers_database_and_terraform_iac_and_ci_workflows()`:
   - `role_library.ROLES["migration-specialist"]["responsibility"]` mentions both schema/database migrations and Infrastructure-as-Code / Terraform (or CI workflows).
   - `role_library.role_protocol("migration-specialist")` includes `terraform validate` (or `terraform init -backend=false`), `terraform fmt`, Secret Manager / state secret hygiene (`tfstate`), opt-in variables (`default = false`) when `deploy` is not live, and pinned CI workflow action SHAs — and forbids running `terraform apply` directly without authorization.
2. `test_python_and_multi_app_test_protocols_guard_optional_cross_app_imports()`:
   - `role_protocol("backend")`, `role_protocol("backend", deploy="none")`, `role_protocol("test-engineer")`, and `role_protocol("reviewer")` require guarding cross-app or optional-dependency imports in tests (e.g., `pytest.importorskip`) so test collection in minimal per-app environments never fails with `ModuleNotFoundError`.
3. `test_lead_protocol_covers_coupled_co_commits_and_pending_patch_escalation()`:
   - `role_protocol("lead")` covers co-committing coupled `PASS` issues that touch the same file in a single commit slot and escalating token-scope push rejections via `/scion-volumes/scratchpad/pending-<issue>/` + `status:po-decision` without stalling remaining WIP.

- [ ] **Step 2: Run targeted tests to verify they fail**

Run: `uv run --with pytest --with pyyaml pytest tests/test_role_library.py -q`
Expected: FAIL (`terraform` missing from `migration-specialist`, `importorskip` missing from `backend`/`test-engineer`/`reviewer`, `pending-` missing from `lead`).

- [ ] **Step 3: Update role definitions in `scripts/role_library.py`**

1. `lead`: add guidance on co-committing coupled reviewed (`PASS`) issues when they touch overlapping files, and handling token-scope push rejections by saving `/scion-volumes/scratchpad/pending-<issue>/` patches, opening `status:po-decision`, and keeping remaining WIP moving.
2. `backend` (`protocol` and `protocol_none`), `test-engineer`, and `reviewer`: require guarding cross-app or optional-dependency imports in tests (e.g. `pytest.importorskip(...)`) when a repository has multiple apps/manifests so primary test collection never breaks in minimal environments.
3. `migration-specialist`: update `responsibility` and `protocol` to cover database schema/DDL migrations, Infrastructure-as-Code (`terraform fmt -check`, `terraform init -backend=false && terraform validate`, gating new edge/cloud resources behind opt-in variables when `deploy` is not live, storing sensitive values in Secret Manager rather than plaintext `tfstate`, never running `terraform apply` directly), and CI/CD workflow definitions (pinned action SHAs, `workflow_dispatch` safety).

- [ ] **Step 4: Run targeted and full tests to verify they pass**

Run: `uv run --with pytest --with pyyaml pytest tests -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/role_library.py tests/test_role_library.py
git commit -m "feat(roles): add IaC/CI guardrails to migration-specialist, importorskip rule, and lead co-commits"
```

---

### Task 4: Hub Token Preflight Warning, GitHub PAT Scope Docs, and Lesson L31

**Files:**
- Modify: `scripts/hub-preflight.py:60-147`
- Modify: `README.md:365-415`
- Modify: `docs/setup-runbook.md:120-140`
- Modify: `docs/lessons-learned.md:1-15,645-650`
- Modify: `AGENTS.md:13-17`
- Test: `tests/test_hub_preflight.py`
- Test: `tests/test_docs_contract.py`

**Interfaces:**
- Consumes: `hub_preflight.build_report(...)`.
- Produces:
  - `build_report(scion_home, health, images, sync_env, po_email, new_projects, github_token_present=True)` in `scripts/hub-preflight.py` — emits a warning when `github_token_present` is `False` (`~/.factory-github-token` missing or empty before `project-create`) and includes `"github_token_present": bool(github_token_present)` in the JSON report, never reading token bytes.
  - PAT scope guidance (`workflow` scope vs human-gated `.github/workflows/*` patches) in `README.md` and `docs/setup-runbook.md`.
  - Lesson `L31` in `docs/lessons-learned.md` (and updated lesson count in `AGENTS.md`).

- [ ] **Step 1: Write the failing tests in `tests/test_hub_preflight.py` and `tests/test_docs_contract.py`**

Add tests pinning:
1. `test_report_warns_when_github_token_file_is_missing(tmp_path)` in `tests/test_hub_preflight.py`:
   - `build_report(..., github_token_present=False)` stays `ok is True` (read-only warning, since non-Git factories are allowed) and adds a warning naming `~/.factory-github-token` and `workflow` scope; `report["github_token_present"] is False`.
   - Also verify `_has_github_token(path)` returns `False` for missing or 0-byte files and `True` for non-empty files without returning the file's contents.
2. `test_docs_cover_github_workflow_scope_and_lesson_l31()` in `tests/test_docs_contract.py`:
   - `README.md` and `docs/setup-runbook.md` document GitHub token `workflow` scope (and what happens when `.github/workflows/*` pushes are rejected).
   - `docs/lessons-learned.md` contains `## L31.` covering the 30-issue overnight autonomous run, `eval-sweep`, `pytest.importorskip`, `migration-specialist`, and `pending-` patch fallback, with zero forbidden real GCP project IDs or VM names.

- [ ] **Step 2: Run targeted tests to verify they fail**

Run: `uv run --with pytest --with pyyaml pytest tests/test_hub_preflight.py tests/test_docs_contract.py -q`
Expected: FAIL (`unexpected keyword argument 'github_token_present'`, `## L31.` missing).

- [ ] **Step 3: Implement preflight check and documentation updates**

1. In `scripts/hub-preflight.py`:
   - Add `_has_github_token(path: str) -> bool` checking `os.path.isfile(path) and os.path.getsize(path) > 0`.
   - Add `github_token_present=True` to `build_report(...)`; when `False`, append warning `"~/.factory-github-token is missing or empty: Git-backed project-create needs a PAT (include 'workflow' scope if agents edit .github/workflows/*)"` and include `"github_token_present": bool(github_token_present)` in returned dict.
   - Pass `github_token_present=_has_github_token(os.path.expanduser(os.environ.get("GITHUB_TOKEN_FILE", "~/.factory-github-token")))` from `main()`.
2. In `README.md` (Step 6) and `docs/setup-runbook.md` (Step 5):
   - Document required GitHub token permissions: `repo` + `workflow` (classic PAT) or `Contents`, `Issues`, `Pull requests`, and `Workflows` read/write (fine-grained PAT) when issues modify `.github/workflows/*`, noting that omitting `workflow` intentionally gates CI/CD workflow changes behind `/scion-volumes/scratchpad/pending-<N>/` patches + `status:po-decision`.
3. In `docs/lessons-learned.md` and `AGENTS.md`:
   - Add `## L31. 20-hour autonomous 30-issue sweep: shared-worktree commit slots, token-scope graceful degradation, IaC specialists, and eval-sweep guarding` with concrete anonymized metrics (`30` issues across security/backend/frontend/IaC/tests/docs, `29/30` verified & closed autonomously, `511` Scion messages, `31` commits, `250 -> 476` tests + `328` subtests, `59 -> 0` `pip-audit` advisories, `#234` `.github/workflows` partial delivery via `pending-<N>/*.patch` + `status:po-decision`) and update lesson count references.

- [ ] **Step 4: Run full test suite and hygiene scan**

Run:
```bash
uv run --with pytest --with pyyaml pytest tests -q
rg -i '(ghp_[A-Za-z0-9]|github_pat_[A-Za-z0-9]|scion_pat_[A-Za-z0-9]|AIza|ya29\.|BEGIN PRIVATE KEY)' . --glob '!config/*.env'
```
Expected: All tests PASS; `rg` exits 1 (no matches).

- [ ] **Step 5: Commit**

```bash
git add scripts/hub-preflight.py README.md docs/setup-runbook.md docs/lessons-learned.md AGENTS.md tests/test_hub_preflight.py tests/test_docs_contract.py
git commit -m "docs(lessons): add L31 overnight factory learnings, PAT workflow scope docs, and token preflight check"
```
