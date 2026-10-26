# Specialist Agent Fabric Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every compiled factory gets a lead-led team with three default specialists (researcher, doc writer,
investigator), more specialists proposed by repository discovery for both teams, a human-on-the-loop contract, and
first-class support for repositories with nothing to deploy.

**Architecture:** Two new pure modules hold the knowledge:
- `scripts/role_library.py`: the role catalog with each role's protocol text, default harness and model, and
  defaults;
- `scripts/discovery.py`: bounded, deterministic repository signals, mapped to proposed roles.

`scripts/scion_factory.py` (the interview compiler) consumes both: the draft roster, readiness, a new `add-role`
subcommand, prompt rendering, the deploy-free branch and the human-on-the-loop text. The watchdog gets one narrow
change. The skill and the README document the new interview topics.

**Tech Stack:** Python 3.10+ standard library, PyYAML, jsonschema (tests), pytest, bash (generated scripts).

**Spec:** [docs/specs/2026-10-26-specialist-agent-fabric-design.md](../specs/2026-10-26-specialist-agent-fabric-design.md)

## Global Constraints

- TDD for every change: write the failing test, see it fail, implement, see it pass
  (`uv run --with pytest --with pyyaml --with jsonschema pytest tests -q`).
- Every generated or edited script supports `DRY_RUN=1` with no side effects (AGENTS.md).
- No secrets, real project IDs or emails in tracked files. Run the AGENTS.md secret scan before each commit.
- Default harnesses: `claude`/`opus` for `lead`; `antigravity`/`Gemini 4 Argon (High)` for every other role,
  including `eval-lead` (L28).
- Build always: `lead`, `backend`, `reviewer`, `researcher`, `doc-writer`, `investigator`. Eval always:
  `eval-lead`, `domain-evaluator`.
- Specialists get no heartbeat; only the two leads do.
- Only the PO closes issues. Every factory's issues carry `factory:<slug>` (monorepo: `agent_prefix`).
- `infrastructure.deploy` is `service` or `none`. A missing key means `service`, so existing specs are unchanged.
- `team.autonomy` is `supervised`, `standard` or `autonomous`. A missing key means `standard`.
- The factory control plane stays Firestore. Don't change the backlog model, the shared checkout or the orphan
  evaluation branch.
- Commits are small, with no AI trailers.

## Review Focus

1. **Existing specs and the three presets** (no `focus`, `evidence`, `default`, `autonomy` or `deploy`) still pass
   `check` and compile. `compile` adds only the missing defaults, never duplicates `acme-airlines`' existing
   `researcher`, and treats a renamed implementer (`default_kind: implementer`, for example `go-dev`) as the
   backend. Tests in Task 3.
2. **A role the library doesn't know** (a custom role from the interview) renders a generic prompt with its
   responsibility and Chain of command, with no crash and no empty protocol header. Test in Task 4.
3. **`deploy: none` with a `deployer` or `eval-deployer` still in the roster**, or with a role that has
   `gcp_identity`, fails readiness with a message naming the role. Test in Task 6.
4. **Discovery on a huge or odd repository** (Kubernetes-sized, no Git, empty, unreadable files) finishes quickly
   and never raises. The walk is capped and skips `vendor/`, `node_modules/`, `.git/` and `third_party/`. Tests in
   Task 2.
5. **A monorepo factory** reads signals from its product folder, not the whole repository. A root `CHANGELOG.md`
   alone doesn't propose a `release-manager` for `pkg/kubelet`. Test in Task 2.

---

### Task 1: Role library

**Files:**
- Create: `scripts/role_library.py`
- Test: `tests/test_role_library.py`

**Interfaces:**
- Produces:
  - `ROLES: dict[str, RoleDef]`, where `RoleDef` is a `TypedDict` with keys `team: "build"|"eval"`,
    `kind: "always"|"catalog"`, `default_kind: str` (`"lead"`, `"implementer"`, `"reviewer"`, or the role name),
    `harness: str`, `model: str`, `responsibility: str` (one sentence the interview can edit), `protocol: str` (a
    markdown section body), `needs_service: bool`.
  - `ALWAYS_BUILD = ("lead", "backend", "reviewer", "researcher", "doc-writer", "investigator")` and
    `ALWAYS_EVAL = ("eval-lead", "domain-evaluator")`.
  - `role_protocol(role: str) -> str | None`.
  - `library_role_entry(role: str) -> dict`: a roster entry `{role, harness, model, responsibility}`; raises
    `KeyError` for an unknown role.
  - `CODE_PYRAMID_TOOLS: dict[str, dict[str, str]]`, keyed by language: `{"l2": ..., "l4": ...}` for `go`
    (`apidiff`, `govulncheck ./...`), `python` (`griffe check`, `pip-audit`), `typescript` (`api-extractor run`,
    `npm audit --omit=dev`) and `rust` (`cargo semver-checks`, `cargo audit`).

**Role table (from the spec, §5):**
- Build catalog: `security-auditor`, `test-engineer`, `frontend`, `performance-analyst`, `release-manager`,
  `migration-specialist`, `deployer` (`needs_service=True`, `gcp_identity: deployer-sa`).
- Eval catalog: `contract-tester`, `latency-prober`, `ui-auditor` (each `needs_service=True`), `safety-evaluator`,
  `factory-analyst`, `benchmark-runner`, `api-compat-checker`, `eval-deployer` (`needs_service=True`).

**Steps:**

- [ ] **Step 1: Write the failing tests**

```python
def test_every_always_role_and_catalog_role_has_a_protocol():
    for role in role_library.ALWAYS_BUILD + role_library.ALWAYS_EVAL:
        assert role_library.ROLES[role]["kind"] == "always"
    for role, d in role_library.ROLES.items():
        assert d["protocol"].strip(), role
        assert d["team"] in ("build", "eval")

def test_specialist_protocols_carry_their_guardrails():
    assert "never invent a citation" in role_library.role_protocol("researcher").lower()
    inv = role_library.role_protocol("investigator").lower()
    assert "reproduce first" in inv and "file:line" in inv and "don't fix" in inv
    assert "docs follow code" in role_library.role_protocol("doc-writer").lower()
    assert "kp-check.py" in role_library.role_protocol("doc-writer")

def test_default_harnesses():
    assert role_library.library_role_entry("lead")["harness"] == "claude"
    assert all(role_library.library_role_entry(r)["harness"] == "antigravity"
               for r in role_library.ROLES if r != "lead")

def test_service_roles_are_marked():
    assert {r for r, d in role_library.ROLES.items() if d["needs_service"]} == {
        "deployer", "eval-deployer", "contract-tester", "latency-prober", "ui-auditor"}

def test_unknown_role():
    assert role_library.role_protocol("chef") is None
    with pytest.raises(KeyError):
        role_library.library_role_entry("chef")
```

- [ ] **Step 2: Run them and see them fail.** Run `uv run --with pytest --with pyyaml pytest tests/test_role_library.py -q`.
  Expected: `ModuleNotFoundError: role_library`.
- [ ] **Step 3: Implement `scripts/role_library.py`.**
  - Each `protocol` is 6–15 lines adapted from the spec §5 tables and the research note: what the role produces,
    where (`/scion-volumes/scratchpad/reports/<issue>-<kind>.md` for researcher and investigator), and what it never
    does.
  - Researcher: summary, findings with sources rated High/Medium/Low, gaps, references, "never invent a citation".
  - Investigator: "Reproduce first", root cause at `file:line`, scope tier (`small`, `medium`, `large`), recommended
    approach, "Don't fix; a proof-of-concept patch only shows the cause".
  - Doc writer: "Docs follow code", update README, docs and knowledge plane, run
    `python3 <knowledge plane>/kp-check.py`, list items of concern.
  - Parallel reviewers (`reviewer`, `security-auditor`, `performance-analyst`): "Never start or message other
    agents; send your verdict only to your lead" (spec §5.4). Add the assertion
    `"never start or message other agents" in role_protocol(r).lower()` for these three to
    `test_specialist_protocols_carry_their_guardrails`.
- [ ] **Step 4: Run them and see them pass.**
- [ ] **Step 5: Commit:** `feat(kit): role library with specialist protocols and the discovery catalog`.

### Task 2: Discovery signals

**Files:**
- Create: `scripts/discovery.py`
- Modify: `scripts/scion_factory.py`, so that `inspect_repository` adds `detected["proposed_roles"]`
- Test: `tests/test_discovery.py`

**Interfaces:**
- Consumes: `role_library.ROLES` (Task 1).
- Produces:
  - `discover_roles(root: Path, subdir: str = "", deploy: str = "service", labels: list[str] | None = None, max_files: int = 50000) -> list[dict]`.
    Each item is `{"role": str, "team": "build"|"eval", "evidence": list[str]}`. Items are sorted by
    (team, role). The result never includes an always-role, and with `deploy="none"` it never includes a
    `needs_service` role.
  - `walk_files(root: Path, max_files: int) -> list[str]`: repository-relative POSIX paths. It skips `.git`,
    `vendor`, `node_modules` and `third_party`, and stops at `max_files`.

**Signals (evidence strings name the matched paths or labels, at most 3 per role):**

| Role | Signal |
|---|---|
| `security-auditor` | a dir or file whose name contains `auth`, `crypto`, `oauth` or `secret`; `SECURITY.md`; a `security` label |
| `test-engineer` | test files / source files < 0.2 for the detected language, or a `flaky` label |
| `frontend` | `package.json` mentioning `react`, `vue`, `svelte` or `next`, or `*.tsx` / `*.vue` files |
| `performance-analyst` | `func Benchmark` in `*_test.go`, a `bench`/`benchmarks` dir, or a `performance` label |
| `release-manager` | `CHANGELOG*`, `RELEASE*`, `.goreleaser*`, `.github/workflows/*release*` |
| `migration-specialist` | a `migrations` dir, `*.sql`, `*.tf`, or `alembic.ini` |
| `contract-tester` | `*.proto`, `openapi*.y*ml` / `swagger*.json`, `*.graphql` (needs a service) |
| `ui-auditor` | the same signal as `frontend` (needs a service) |
| `safety-evaluator` | dependency or import text mentioning `openai`, `anthropic`, `google.genai`, `vertexai` or `langchain` |
| `factory-analyst` | `subdir` is set |
| `benchmark-runner` | the `performance-analyst` signal and `deploy="none"` |
| `api-compat-checker` | `deploy="none"` and a library layout (`go.mod` with no `package main` under `subdir`, `pyproject.toml` with `[project]`, `Cargo.toml` with `[lib]`, `package.json` with `main` or `exports`) |

- [ ] **Step 1: Write the failing tests** (fixture repositories built under `tmp_path`):

```python
def test_auth_code_proposes_a_security_auditor(tmp_path):
    (tmp_path / "pkg" / "auth").mkdir(parents=True); (tmp_path / "pkg" / "auth" / "token.go").write_text("package auth\n")
    got = {r["role"]: r for r in discovery.discover_roles(tmp_path)}
    assert "security-auditor" in got and any("pkg/auth" in e for e in got["security-auditor"]["evidence"])

def test_plain_repo_proposes_nothing_needing_a_service_when_deploy_is_none(tmp_path):
    (tmp_path / "api.proto").write_text("syntax = \"proto3\";\n")
    roles = {r["role"] for r in discovery.discover_roles(tmp_path, deploy="none")}
    assert "contract-tester" not in roles

def test_service_repo_with_a_proto_gets_a_contract_tester(tmp_path):
    (tmp_path / "api.proto").write_text("syntax = \"proto3\";\n")
    assert "contract-tester" in {r["role"] for r in discovery.discover_roles(tmp_path)}

def test_go_library_without_service_gets_api_compat_and_benchmarks(tmp_path):
    (tmp_path / "go.mod").write_text("module example.com/lib\n")
    (tmp_path / "lib_test.go").write_text("package lib\nfunc BenchmarkX(b *testing.B) {}\n")
    roles = {r["role"] for r in discovery.discover_roles(tmp_path, deploy="none")}
    assert {"api-compat-checker", "benchmark-runner", "performance-analyst"} <= roles

def test_monorepo_reads_only_the_product_folder(tmp_path):
    (tmp_path / "CHANGELOG.md").write_text("x"); (tmp_path / "pkg" / "kubelet").mkdir(parents=True)
    roles = {r["role"] for r in discovery.discover_roles(tmp_path, subdir="pkg/kubelet")}
    assert "release-manager" not in roles and "factory-analyst" in roles

def test_walk_is_capped_and_skips_vendor(tmp_path):
    (tmp_path / "vendor" / "x").mkdir(parents=True); (tmp_path / "vendor" / "x" / "auth.go").write_text("")
    for i in range(30): (tmp_path / f"f{i}.txt").write_text("")
    files = discovery.walk_files(tmp_path, max_files=10)
    assert len(files) == 10 and not any(f.startswith("vendor/") for f in files)

def test_empty_and_missing_dirs_never_raise(tmp_path):
    assert discovery.discover_roles(tmp_path) == []
    assert discovery.discover_roles(tmp_path / "nope") == []

def test_labels_add_evidence(tmp_path):
    got = {r["role"]: r for r in discovery.discover_roles(tmp_path, labels=["security", "flaky"])}
    assert "label: security" in got["security-auditor"]["evidence"]
    assert "test-engineer" in got

def test_inspect_repository_returns_proposed_roles(tmp_path):
    (tmp_path / "SECURITY.md").write_text("x")
    assert any(r["role"] == "security-auditor"
               for r in scion_factory.inspect_repository(tmp_path)["detected"]["proposed_roles"])
```

- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement `discovery.py`.** Read at most 256 KB of any one file for text signals, ignore
  decode errors, and catch `OSError` per file. In `inspect_repository`, call `discover_roles(root)`; labels stay
  `None` there.
- [ ] **Step 4: Run them and see them pass.** Then run the full suite: still green.
- [ ] **Step 5: Commit:** `feat(kit): repository discovery proposes specialists with evidence`.

### Task 3: Spec model: defaults, drops, autonomy, deploy, and `add-role`

**Files:**
- Modify: `scripts/scion_factory.py`:
  - `DEFAULT_BUILD_ROLES` / `DEFAULT_EVAL_ROLES` are built from the library (always-roles only);
  - `draft_spec_from_repo`;
  - `evaluate_spec_readiness`;
  - new `normalize_team`;
  - `compile_factory_boilerplate` calls `normalize_team`;
  - new `add_role`;
  - CLI subcommand `add-role`;
  - `prepare_grill_session` returns `proposed_roles`.
- Modify: `config/factory.schema.json`: `infrastructure.deploy` enum; `team.autonomy` enum; `team.dropped_defaults`
  array of `{role, reason}`; role items may carry `focus` (`{paths, commands, docs}`, arrays of strings),
  `evidence` (array of strings), `default` (bool) and `default_kind` (string). `serving_service`, `eval_service`
  and `deployer_iam_roles` are required only when `deploy` isn't `none` (`if`/`then`).
- Test: `tests/test_spec_model.py`

**Interfaces:**
- Consumes: `role_library.ALWAYS_BUILD`, `ALWAYS_EVAL`, `library_role_entry` (Task 1); `discovery.discover_roles`
  (Task 2).
- Produces:
  - `deploy_mode(spec) -> str`: `"service"` when `infrastructure.deploy` is absent, otherwise the raw value (the
    draft's `""` included). Readiness rejects anything other than `service` or `none`.
  - `autonomy(spec) -> str`: `team.autonomy`, defaulting to `"standard"`.
  - `normalize_team(spec) -> tuple[dict, list[str]]`: returns a deep copy with each missing always-role appended
    (`default: true`, library entry, responsibility from the library), skipping roles in
    `team.dropped_defaults` and treating any role whose `default_kind` equals the missing role's `default_kind` as
    present. Also returns notes such as `"added default role doc-writer"`.
  - `add_role(spec_path, role: str, team: str | None = None, evidence: list[str] = ()) -> dict`: inserts the library
    entry, with `evidence`, into the right roster. It's idempotent: an existing role is left as is. It raises
    `ValueError` for a `needs_service` role when `deploy_mode` is `none`.
  - `evaluate_spec_readiness(spec)` gains a `"warnings": list[str]` key. A missing always-role is a warning, not an
    error. A dropped default without a non-empty `reason` is an error. A bad `deploy` or `autonomy` value is an error.

**Steps:**

- [ ] **Step 1: Write the failing tests:**

```python
def test_draft_roster_has_the_always_roles_marked_default(): ...
    # draft_spec_from_repo(None)["team"]["build_roles"] roles == list(ALWAYS_BUILD), each default is True;
    # eval == list(ALWAYS_EVAL); "frontend" and "deployer" are NOT in the draft; team.autonomy == "standard";
    # infrastructure.deploy == ""  (an intent field the interview must answer)

def test_presets_still_ready_and_compile(tmp_path): ...
    # for each presets/*.factory.yaml: evaluate_spec_readiness(spec)["ready"] is True; compile succeeds

def test_old_spec_gets_missing_defaults_without_duplicates(tmp_path): ...
    # acme-airlines preset: compiled templates include doc-writer and investigator; researcher appears once;
    # AGENTS.md mentions "added default role doc-writer"

def test_renamed_implementer_counts_as_backend(): ...
    # a roster with {"role": "go-dev", "default_kind": "implementer", ...} and no backend → normalize_team adds no backend

def test_dropping_a_default_needs_a_reason(): ...
    # team.dropped_defaults=[{"role": "researcher"}] → readiness error "dropped_defaults: researcher needs a reason";
    # with reason → ready, and normalize_team doesn't add researcher

def test_bad_enum_values_fail_readiness(): ...
    # infrastructure.deploy="maybe" → error; team.autonomy="yolo" → error

def test_add_role_inserts_library_entry_with_evidence(tmp_path): ...
    # add_role(path, "security-auditor", evidence=["pkg/auth"]) → build_roles has it with harness antigravity and evidence;
    # second call leaves one entry

def test_add_role_refuses_service_roles_without_a_service(tmp_path): ...
    # deploy none + add_role("deployer") → ValueError mentioning "deploy: none"

def test_grill_payload_lists_proposed_roles(tmp_path): ...
    # prepare_grill_session(repo_with_SECURITY_md, out)["proposed_roles"] contains security-auditor

def test_schema_accepts_new_fields_and_deploy_none_without_services(): ...
    # jsonschema.validate on a preset with deploy none, serving_service/eval_service/deployer_iam_roles removed
```

  Write these out in full with the values in the comments.
- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement.** In the AGENTS.md renderer (`_render_root_agents_md`), list the `normalize_team` notes
  under a "Roster notes" heading. The `check` CLI prints the warnings after `missing_or_weak`.
- [ ] **Step 4: Run them, then the full suite, and see both pass.**
- [ ] **Step 5: Commit:** `feat(kit): default specialists, explicit drops, autonomy and deploy fields, add-role`.

### Task 4: Role prompts: protocol, focus, briefs, sizing

**Files:**
- Modify: `scripts/scion_factory.py`:
  - `_render_role_system_prompt_base` / `_render_role_system_prompt` insert `## Role protocol` (from the library)
    and `## Focus`;
  - `_render_chain_of_command`: the lead adds `## Sizing an issue` and `## Briefs`; a team member adds
    "Read the brief file named in the message".
- Test: `tests/test_role_prompts.py`

**Interfaces:**
- Consumes: `role_library.role_protocol`, `normalize_team` (Task 3).
- Produces: `role_focus(spec, role_spec) -> dict[str, list[str]]`. An explicit `focus` wins. Otherwise:
  - backend, test-engineer and investigator get `commands=[build, test, lint]`;
  - doc-writer and researcher get `docs` from the knowledge-plane sources plus `<ws>/knowledge-plane/INDEX.md`;
  - every role gets `paths=[subdir or "."]`.

**Steps:**

- [ ] **Step 1: Write the failing tests:**

```python
def test_specialist_prompts_carry_their_protocol(kit):      # kit = compiled starter preset
    assert "Reproduce first" in _prompt(kit, "investigator")
    assert "Docs follow code" in _prompt(kit, "doc-writer")

def test_focus_lists_commands_and_docs(kit, spec):
    assert spec["stack"]["test_command"] in _prompt(kit, "investigator")
    assert "knowledge-plane/INDEX.md" in _prompt(kit, "doc-writer")

def test_lead_sizes_with_only_the_roles_present(kit):
    lead = _prompt(kit, "lead")
    assert "## Sizing an issue" in lead and "investigator" in lead
    assert "security-auditor" not in lead          # not in this roster

def test_briefs_are_files_in_the_scratchpad(kit):
    assert "/scion-volumes/scratchpad/briefs/" in _prompt(kit, "lead")
    assert "Read the brief file named in the message" in _prompt(kit, "backend")

def test_every_team_member_has_the_durability_rule(kit):
    assert "done only when its result is pushed or sent in a message" in _prompt(kit, "researcher")

def test_unknown_role_gets_a_generic_prompt(tmp_path):
    # roster plus {"role": "data-wrangler", "harness": "antigravity", "model": "m", "responsibility": "Cleans CSVs"}
    # → its prompt contains "Cleans CSVs" and "## Chain of command", and has no "## Role protocol" header
```

- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement.** Every team member's Chain of command block adds the durability rule from spec §6.8:
  "A task is done only when its result is pushed or sent in a message." The sizing lines come from a fixed map,
  filtered to the roster:
  - `bug → investigator first`;
  - `unfamiliar ground → researcher first`;
  - `user-visible change → doc-writer after the commit`;
  - `auth/crypto/secrets → security-auditor in parallel with reviewer`;
  - `UI → frontend`;
  - `schema/IaC → migration-specialist`;
  - `perf-sensitive → performance-analyst`;
  - `release → release-manager`.
- [ ] **Step 4: Run them and see them pass.** Then run the full suite. Existing assertions in
  `tests/test_team_hierarchy.py` must still pass unchanged.
- [ ] **Step 5: Commit:** `feat(kit): role protocols, repo focus, file briefs and issue sizing in prompts`.

### Task 5: Human on the loop

**Files:**
- Modify: `scripts/scion_factory.py`:
  - `_factory_label` returns `factory:<agent_prefix or slug>` for every factory;
  - move the `gh issue list` label injection out of `_monorepo_paths` so it applies to all factories;
  - the shared backlog section;
  - lead and eval-lead prompts: decision rights per autonomy level, the PO inbox, the digest, "tell" notices;
  - the eval-lead files findings as issues;
  - `_render_seed_waves_sh` creates the labels `status:po-decision`, `status:po-review`, `status:approved`,
    `digest`, `eval-finding` and `factory:<slug>`, plus the digest issue `"<slug> digest"`, created only if absent
    (`gh issue list --search`) and pinned (`gh issue pin`); with `DRY_RUN` it prints only.
- Modify: `tests/test_compiler_monorepo.py`. Replace `test_single_repo_factory_has_no_factory_label` with
  `test_single_repo_factory_labels_issues_with_its_slug`.
  > [!NOTE]
  > This replaces a test because the spec changed the behaviour (§7.2), not to make a test pass. Flag it in the
  > review.
- Test: `tests/test_human_on_the_loop.py`

**Interfaces:**
- Consumes: `autonomy(spec)` (Task 3), `_render_chain_of_command` (Task 4).
- Produces: `render_decision_rights(spec) -> str`, the markdown table from spec §7.1, adjusted per §7.4.

**Steps:**

- [ ] **Step 1: Write the failing tests:**

```python
def test_standard_lead_waits_for_po_on_scope_cost_iam_adr(kit):
    lead = _prompt(kit, "lead")
    for needle in ("status:po-decision", "Scope changes", "IAM", "ADR", "only the PO closes"):
        assert needle in lead

def test_digest_and_tell_go_to_github_not_messages(kit):
    lead = _prompt(kit, "lead")
    assert "digest" in lead and "gh issue comment" in lead
    assert "ready for acceptance" not in lead           # no per-issue PO message any more

def test_supervised_waits_for_status_approved(tmp_path):     # autonomy=supervised
    assert "status:approved" in lead_prompt and "status:po-review" in lead_prompt

def test_autonomous_tells_scope_instead_of_asking(tmp_path): # autonomy=autonomous
    # "Scope changes" appears in the told column, not in the "Waits for the PO" column

def test_eval_lead_files_findings_as_issues_and_never_messages_build_agents(kit):
    ev = _prompt(kit, "eval-lead")
    assert "eval-finding" in ev and "factory:" in ev

def test_seed_script_creates_inbox_labels_and_digest_issue_in_dry_run_only_prints(kit, tmp_path):
    # reuse the fake-gh pattern from tests/test_compiler_monorepo.py::test_seed_script_dry_run_never_calls_github
    # DRY_RUN=1 → no gh calls; stdout lists status:po-decision and "<slug> digest"

def test_single_repo_factory_labels_issues_with_its_slug(tmp_path):   # replaces the old test
    # starter preset: seed script contains '"factory:<slug>"'; lead prompt has "--label factory:<slug>"
```

- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement.** The 24-hour reminder for an unanswered `status:po-decision` is part of the lead's
  wake-up steps. Scion messages to the PO stay only for `status:po-decision` items, and each one links its issue.
- [ ] **Step 4: Run them, then the full suite, and see both pass.**
- [ ] **Step 5: Commit:** `feat(kit): decision rights, one PO inbox, daily digest on GitHub, eval findings as issues`.

### Task 6: Factories with nothing to deploy

**Files:**
- Modify: `scripts/scion_factory.py`:
  - readiness skips `serving_service`, `eval_service`, `deployer_iam_roles`, `l1_health.endpoint` and `l5.p95_ms`
    when `deploy_mode == "none"`, and errors on any `needs_service` role or `gcp_identity` in the roster;
  - `_render_shared_agents_md_base` (topology and deploy sections);
  - `_render_role_system_prompt_base` (target service line and pyramid);
  - `_render_factory_env_example` (leave out `SERVING_SERVICE`, `DASHBOARD_SERVICE`, `RUNTIME_SA_NAME` and
    `DEPLOYER_*`);
  - `_render_verify_factory_sh` (L1 = build plus test, L4 = `CODE_PYRAMID_TOOLS[lang]["l4"]`, no curl);
  - `_render_root_agents_md`;
  - GRILL_QUESTIONS gets a `deploy_target` question.
- Modify: `scripts/knowledge_plane.py:373`: the LIVE.md service row reads `none (nothing to deploy)`.
- Create: `presets/go-library.factory.yaml`, a `deploy: none` Go library preset used by the tests.
- Test: `tests/test_deploy_none.py`

**Interfaces:**
- Consumes: `deploy_mode` (Task 3), `role_library.CODE_PYRAMID_TOOLS` (Task 1).
- Produces: `render_code_pyramid(spec) -> str`, the five lines from spec §8 with the language's tools. An unknown
  language says `ask the PO for the tool`.

**Steps:**

- [ ] **Step 1: Write the failing tests:**

```python
def test_library_preset_is_ready_and_has_no_deployers(tmp_path):
    spec = load_spec("presets/go-library.factory.yaml"); assert evaluate_spec_readiness(spec)["ready"]
    out = compile(spec); assert not (out / "templates" / "deployer").exists()
    env = (out / "config" / "factory.env.example").read_text()
    assert "SERVING_SERVICE" not in env and "DEPLOYER_SA_NAME" not in env

def test_code_pyramid_replaces_live_checks(tmp_path):
    ev = _prompt(out, "domain-evaluator")
    assert "govulncheck" in ev and "apidiff" in ev and "Health" not in ev
    verify = (out / "scripts" / "verify-factory.sh").read_text()
    assert "curl" not in verify and "go test" in verify

def test_deploy_none_rejects_service_roles():
    # library preset + {"role": "deployer", ...} → missing_or_weak names "deployer" and "deploy: none"

def test_unknown_language_code_pyramid_asks_the_po():
    # primary_language "elixir" → render_code_pyramid contains "ask the PO for the tool"

def test_service_specs_unchanged(tmp_path):
    # starter preset compiled before/after: env still has SERVING_SERVICE; verify still curls the health endpoint
```

- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement.** In the `deploy_target` question:
  - `field_path`: `infrastructure.deploy`;
  - `challenge_prompt`: "Is there a Cloud Run service to deploy? A library, CLI or monorepo package is `none`";
  - lessons: `["L29"]`.
- [ ] **Step 4: Run them, then the full suite, and see both pass.**
- [ ] **Step 5: Commit:** `feat(kit): first-class factories with nothing to deploy (code evaluation pyramid)`.

### Task 7: Watchdog leaves idle specialists asleep

**Files:**
- Modify: `scripts/factory-watchdog.py`, check 3 (the `phase == "suspended"` branch, around line 411).
- Test: `tests/test_watchdog_wake.py` (load the module the way `tests/test_shared_dirs.py` does).

**Interfaces:**
- Produces: `should_wake(agent_slug: str, template: str, wake_roles: set[str]) -> bool`, and the env
  `WATCHDOG_WAKE_ROLES` (default `lead,eval-lead`), forwarded by `run-on-hub.sh` and written to `watchdog.env` like
  the other watchdog settings.

**Steps:**

- [ ] **Step 1: Write the failing tests:**

```python
def test_watchdog_wakes_suspended_leads_only(watchdog):
    assert watchdog.should_wake("kubelet-lead", "lead", {"lead", "eval-lead"})
    assert not watchdog.should_wake("kubelet-researcher", "researcher", {"lead", "eval-lead"})

def test_wake_roles_come_from_env(watchdog, monkeypatch):
    # WATCHDOG_WAKE_ROLES="lead,eval-lead,backend" → backend is woken
```

  Also extend the existing env-forwarding test in `tests/test_shared_dirs.py` to include `WATCHDOG_WAKE_ROLES`.
- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement.** A suspended non-lead isn't woken by the watchdog, which logs
  `left asleep (lead wakes it with a brief)`. The lead's prompt already uses `scion message --wake` for suspended
  teammates. Dry-run behaviour is unchanged.
- [ ] **Step 4: Run them, then the full suite, and see both pass.**
- [ ] **Step 5: Commit:** `fix(watchdog): wake only suspended leads; leads wake their specialists with a brief`.

### Task 8: Skill and docs

**Files:**
- Modify: `skills/scion-factory-bootstrap/SKILL.md`. Add a Team step:
  - present the default roster and each `proposed_roles` item with its evidence;
  - run `./scripts/scion-factory add-role factory.yaml --role R --evidence "..."` for each confirmed one;
  - record drops with a reason under `team.dropped_defaults`;
  - ask for `team.autonomy` and `infrastructure.deploy`;
  - keep "never invent intent".
- Modify: `README.md`, "The interview" table: a Team row (roster, specialists, autonomy) and a Deploy row
  (`service`/`none`). In "Review the generated factory kit", note the new prompt sections.
- Modify: `docs/grill-me-playbook.md`, with the two new topics.
- Modify: `templates/README.md`, a pointer to `scripts/role_library.py` as the source of compiled role protocols.
- Test: `tests/test_docs_contract.py`

**Steps:**

- [ ] **Step 1: Write the failing test.** The docs name the real interfaces, so they can't drift:

```python
def test_skill_and_readme_document_the_team_step():
    skill = (ROOT / "skills/scion-factory-bootstrap/SKILL.md").read_text()
    for needle in ("add-role", "proposed_roles", "dropped_defaults", "team.autonomy", "infrastructure.deploy"):
        assert needle in skill
    readme = (ROOT / "README.md").read_text()
    assert "autonomy" in readme and "deploy: none" in readme
    assert subprocess.run([sys.executable, "scripts/scion_factory.py", "add-role", "--help"], cwd=ROOT).returncode == 0
```

- [ ] **Step 2: Run it and see it fail.**
- [ ] **Step 3: Edit the docs.** Follow the repository's style: ASD-STE100 short sentences, second person.
- [ ] **Step 4: Run it, then the full suite, and see both pass.** Then run the secret scan.
- [ ] **Step 5: Commit:** `docs: team step, specialists, autonomy and deploy: none in the skill and README`.

### Task 9: End-to-end on a Hub (needs the PO)

This task has no code; its deliverable is evidence. It runs only after Tasks 1–8 are green and pushed.

- [ ] **Step 1:** Ask the PO for a Hub (the kept `scion-k8s-e2e` Hub works) and two target repositories:
  - one service repository (`deploy: service`);
  - one Go library (`deploy: none`).
- [ ] **Step 2:** Run the skill end to end for each: `grill`, the interview including the Team step, `add-role`,
  `check`, `compile`, then README steps 4–8. The PO starts only the two leads per factory.
- [ ] **Step 3: Verify:**
  - `scion list --lineage` under each lead shows the whole roster;
  - a bug issue goes investigator, then backend, then reviewer (scratchpad reports exist);
  - an unfamiliar issue goes researcher first;
  - a doc-writer commit follows a merged change, and `kp-check` passes;
  - one `status:po-decision` issue appears for a planted scope question;
  - a digest comment appears on the pinned digest issue;
  - the library factory's evaluation team reports L1–L5 code results;
  - no idle specialist is woken by the watchdog (`watchdog.log`).
- [ ] **Step 4:** Record the evidence as lesson L30 in `docs/lessons-learned.md`. Fix any gap with TDD. Run the
  secret scan, commit, and push `main`.
- [ ] **Step 5:** Ask the PO before teardown, and follow the L29 teardown notes.
