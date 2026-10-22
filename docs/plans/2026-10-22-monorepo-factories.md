# Spec and plan: several factories in one monorepo

- **Status:** Proposed (2026-10-22)
- **Related:** [setup-runbook.md](../setup-runbook.md), [templates/README.md](../../templates/README.md),
  [Scion agent config reference](https://googlecloudplatform.github.io/scion/reference/agent-config/)

## Goal

One repository holds several products (`src/project1`, `src/project2`, `src/project3`). Each product gets its own
factory: a build team and an eval team. Every agent:

1. clones the **whole** monorepo,
2. starts its harness in its own product folder (`src/<product>`),
3. has a name that starts with its factory slug (`project1-lead`, `project1-eval-lead`), so names, containers and
   `scion/<agent>` branches never clash across factories on one Hub.

## Facts this design rests on (verified in the Scion source, `GoogleCloudPlatform/scion@2159e93`)

| Fact | Where |
|---|---|
| `scion-agent.yaml` has no working-directory key; a template describes the job, not the environment. | agent-config reference |
| The Docker runtime always sets `--workdir /workspace` (git clone, shared) or the worktree path; there is no sub-folder option. | `pkg/runtime/common.go:446-522` |
| A relative `--workspace <subdir>` mounts **only** that subtree and is rejected for clone-per-agent git projects. | `docs-site/.../local/workspace.md` |
| Agent names are slugified (`[a-z0-9-]`) and capped at 63 characters. | `pkg/api/slug.go:27-84` |
| The harness command is the harness-config's `command.base`; harness-config `home/` files are copied into every agent home at provisioning. | `harnesses/*/config.yaml`, harness-settings reference |
| Claude's provisioner pre-trusts only `/workspace` and `ctx.workspace`; Antigravity's only `trustedWorkspaces: [/workspace]`; Antigravity fires only project-local hooks (`<workspace>/.agents/hooks.json`). | `harnesses/claude/provision.py:208-261`, `harnesses/antigravity/provision.py:363-370,614-662` |
| Template `env:` values are literal fallbacks, applied at every start. | agent-config reference |
| The same git URL can back several Hub projects (project IDs are UUIDs). | workspace reference |

## Design

### 1. Agent names: `<AGENT_PREFIX>-<role>`

- New optional factory variable `AGENT_PREFIX`. For a monorepo factory it is the factory's full build slug
  (`AGENT_PREFIX=project1`, `BUILD_PROJECT=project1`, `EVAL_PROJECT=project1-eval`). Unset keeps today's bare role
  names, so single-factory setups don't change.
- The build and eval teams share the prefix: `project1-lead`, `project1-backend`, …, `project1-eval-lead`,
  `project1-factory-analyst`, `project1-eval-deployer`.
- Validation: `AGENT_PREFIX` matches `^[a-z0-9]+(-[a-z0-9]+)*$`, and `len(prefix) + 1 + len(longest role) <= 63`.
- Template names stay role names (`-t lead`); only the agent name carries the prefix.
- Prompts never hard-code peer names. `render-templates.py` derives one variable per role folder:
  `<ROLE>_AGENT` (`LEAD_AGENT`, `EVAL_LEAD_AGENT`, `FACTORY_ANALYST_AGENT`, …) = `<prefix>-<role>` or `<role>`.
  Templates use `${LEAD_AGENT}` wherever they name an agent (report lines, `scion message` targets, message
  signatures, team tables).
- `render-templates.py --agent-names` prints `<role>\t<agent name>` per role, for start commands and scripts.
- The watchdog no longer hard-codes `{"psearch": "lead", ...}`: it picks the lead of a project from that project's
  agents by convention (`<slug>-lead`, `<base>-eval-lead` for `<base>-eval`, then bare `lead`/`eval-lead`).

### 2. Start folder: `FACTORY_SUBDIR` + a harness launcher

- New optional factory variable `FACTORY_SUBDIR` (default `.`): the product folder relative to the repository root.
  Validation: relative, no `..` component, no leading `/`; a trailing `/` is dropped.
- Every role template gets `env: FACTORY_SUBDIR: "<rendered value>"` (literal after rendering; a project-scoped
  Hub env var of the same name overrides it).
- `config/harness/home/.local/bin/factory-cd.sh` runs before the harness:
  1. If `FACTORY_SUBDIR` is unset, empty or `.`: `exec "$@"` unchanged.
  2. Otherwise resolve the repository root (`git rev-parse --show-toplevel`, else `$PWD`), refuse absolute paths and
     `..` (exit 64) and a missing folder (exit 66, message on stderr), `cd` into it.
  3. Pre-trust the folder for Claude (`~/.claude.json` `projects[<dir>].hasTrustDialogAccepted = true`) and
     Antigravity (`~/.gemini/antigravity-cli/settings.json` `trustedWorkspaces += <dir>`) when those files exist.
  4. Copy `<root>/.agents/hooks.json` to `<dir>/.agents/hooks.json` when the root has one and the folder doesn't, and
     add `.agents/` to the clone's `info/exclude`, so Antigravity's project-local status hooks still fire.
  5. `exec "$@"`.
- `scripts/patch-harness-start-dir.py` (runs on the Hub VM, dry run by default, `--apply` to write) installs
  `factory-cd.sh` into the `home/.local/bin/` of the Hub's `claude`, `antigravity`, `opencode` and `hermes`
  harness-configs and prefixes their `command.base` with `["sh", "/home/scion/.local/bin/factory-cd.sh"]` once
  (idempotent). Because the launcher ships in the harness-config's own `home/`, every agent that uses the patched
  config has it, whatever its template.

### 3. Hub layout per factory

| Factory | Hub projects (same git URL, clone-per-agent) | `AGENT_PREFIX` | `FACTORY_SUBDIR` | Branches |
|---|---|---|---|---|
| project1 | `project1`, `project1-eval` | `project1` | `src/project1` | build `main`; eval `project1-eval-dashboard` (orphan) |
| project2 | `project2`, `project2-eval` | `project2` | `src/project2` | build `main`; eval `project2-eval-dashboard` (orphan) |

One `factory.env` per factory (`FACTORY_ENV=config/project1.env scripts/render-templates.py ...`).

## Non-goals

- Changing the `scion-factory` compiler (`scripts/scion_factory.py`, FactorySpec) to emit monorepo kits. That is a
  follow-up plan once this runtime path is proven end to end.
- Enforcing path boundaries (CODEOWNERS, CI path filters, commit guards). The start folder is a default, not a
  sandbox; the runbook names the options.
- Rewriting the `psearch`-specific paths in the reference prompts (`/workspace/src/psearch/serving`).

## Global constraints

- No secrets, real project IDs, emails or VM names in tracked files (AGENTS.md rule 1); run the secret scan before
  every commit.
- Hub-side scripts are idempotent and dry-run by default (AGENTS.md rule 2).
- Never `docker restart` agent containers (AGENTS.md rule 4).
- Behaviour without `AGENT_PREFIX` / `FACTORY_SUBDIR` is byte-for-byte today's rendering, except the new
  `FACTORY_SUBDIR: "."` template env line.

## Review focus

1. `AGENT_PREFIX` with upper case, underscores or a trailing dash → render fails with a clear message (no silent
   slugify that makes prompt names differ from Hub names).
2. `FACTORY_SUBDIR` set to `../x`, `/abs`, or a folder missing from the clone → harness does not start, stderr says why.
3. `factory-cd.sh` run twice on the same home (agent restart) → trust lists have no duplicates, exclude line not
   repeated.
4. Patch script run twice → `command.base` prefixed once.
5. Templates must not keep a bare `scion message lead` (or other role) once rendered with a prefix → a lint test.

## Tasks

### Task 1: naming and sub-folder variables in `render-templates.py`

**Files:** modify `scripts/render-templates.py`; create `tests/test_monorepo_factories.py`.

**Interfaces (produces):** `agent_name(prefix: str, role: str) -> str`, `role_agent_vars(prefix: str, roles: list[str])
-> dict[str, str]`, `validate_prefix(prefix: str, roles: list[str]) -> None` (raises `ValueError`),
`normalize_subdir(value: str) -> str` (raises `ValueError`); CLI `--agent-names`.

- [ ] Tests: `agent_name("project1", "eval-lead") == "project1-eval-lead"`; `agent_name("", "lead") == "lead"`;
  `role_agent_vars("project1", ["lead", "eval-lead"]) == {"LEAD_AGENT": "project1-lead", "EVAL_LEAD_AGENT":
  "project1-eval-lead"}`; `validate_prefix` rejects `"Project1"`, `"project_1"`, `"project1-"`, and a 50-character
  prefix with role `search-evaluator`; `normalize_subdir("src/project1/") == "src/project1"`, `normalize_subdir("")
  == "."`, rejects `"../x"`, `"/abs"`, `"src/../x"`; CLI on a temp env with `AGENT_PREFIX=project1` prints
  `lead\tproject1-lead`.
- [ ] Run, watch fail; implement; run, watch pass; full suite green; commit.

### Task 2: templates use the role variables and carry `FACTORY_SUBDIR`

**Files:** modify every `templates/<role>/system-prompt.md`, `templates/<role>/scion-agent.yaml`,
`templates/_shared/agents.md`; extend `tests/test_monorepo_factories.py`.

- [ ] Tests: rendering all roles with `AGENT_PREFIX=project1`, `FACTORY_SUBDIR=src/project1` (plus the example env
  values) → no rendered file matches `scion message (lead|backend|...|eval-deployer)\b` or `` report to `<role>` ``;
  the lead prompt contains `scion message project1-reviewer`-style names via `${REVIEWER_AGENT}`; every rendered
  `scion-agent.yaml` has `FACTORY_SUBDIR: "src/project1"` under `env:`; rendering without the two variables keeps
  `scion message lead` and renders `FACTORY_SUBDIR: "."`.
- [ ] Run, watch fail; replace peer names with `${<ROLE>_AGENT}`; add the env line and one sentence in
  `_shared/agents.md` ("Your product folder is `${FACTORY_SUBDIR}` relative to the repository root; the rest of the
  repository is context: change it only when your lead asks"); pass; commit.

### Task 3: `factory-cd.sh` launcher

**Files:** create `config/harness/home/.local/bin/factory-cd.sh`; extend tests.

- [ ] Tests (run the script with `sh` in a temp git repo with `src/project1`, a fake `HOME`): prints
  `<repo>/src/project1` for `sh factory-cd.sh sh -c pwd`; unset/`.` leaves the cwd; `/abs` → exit 64; `../x` → exit
  64; missing folder → exit 66 with the path on stderr; `~/.claude.json` gains the trusted entry and keeps existing
  keys; Antigravity `trustedWorkspaces` gains the folder once after two runs; `.agents/hooks.json` copied and
  `.agents/` appears once in `info/exclude` after two runs; outside a git repo it resolves against `$PWD`.
- [ ] Run, watch fail; implement (POSIX sh + `python3` for the JSON edits, both present in every Scion image); pass;
  commit.

### Task 4: `patch-harness-start-dir.py`

**Files:** create `scripts/patch-harness-start-dir.py`; extend tests.

**Interfaces:** `patch_command_base(config: dict) -> tuple[dict, bool]`; CLI `--root <harness-configs dir>`
(default: the Hub's global harness-config storage), `--harness claude,antigravity,opencode,hermes`, `--apply`.

- [ ] Tests: claude base `["claude", "--no-chrome", ...]` → prefixed; second call unchanged and `changed is False`;
  opencode `["sh", "/home/scion/.local/bin/opencode-launch.sh"]` → prefixed; config without `command.base` →
  unchanged; CLI dry run on a temp tree writes nothing and prints the plan; `--apply` writes `config.yaml` and
  `home/.local/bin/factory-cd.sh` (mode 0755) for each harness.
- [ ] Run, watch fail; implement; pass; commit.

### Task 5: watchdog lead discovery

**Files:** modify `scripts/factory-watchdog.py:375,452`; extend tests.

**Interfaces:** `lead_agent_for(project_slug: str, agent_slugs: list[str]) -> str`.

- [ ] Tests: `("project1", ["project1-lead", "project1-backend"]) -> "project1-lead"`; `("project1-eval",
  ["project1-eval-lead"]) -> "project1-eval-lead"`; `("psearch", ["lead", "backend"]) -> "lead"`; `("psearch-eval",
  ["eval-lead"]) -> "eval-lead"`; no match → `"lead"`.
- [ ] Run, watch fail; implement and use it in `check_agent_liveness`; pass; commit.

### Task 6: docs

**Files:** `config/factory.env.example` (two optional variables), `docs/setup-runbook.md` (new section "Several
factories in one monorepo": per-factory env files, project creation, patch script, start commands from
`--agent-names`, heartbeats with prefixed names), `templates/README.md`, `scripts/README.md`,
`docs/lessons-learned.md` (L28 with the e2e evidence), `AGENTS.md` repository table if a new path is added.

- [ ] Write; secret scan; commit.

### Task 7: end-to-end smoke on a throwaway Hub, then tear down

Throwaway resources only: a new GCP project with a single-node Hub VM (Scion `scripts/single-node-vm/deploy.sh`), a
new private GitHub repo with `src/project1..3`. Two factories (`project1`, `project2`), each with `lead` (Claude Code)
in the build project and `eval-lead` (Antigravity) in the eval project. No deploys.

Checks (each recorded with the command and its output):

| # | Check | Pass when |
|---|---|---|
| E1 | Names | `scion list` per project shows `project1-lead`, `project1-eval-lead`, `project2-lead`, `project2-eval-lead`; containers `<project>--<agent>` |
| E2 | Start folder | `readlink /proc/<harness pid>/cwd` in each container is `/workspace/src/<product>` |
| E3 | Whole monorepo | `ls /workspace/src` in each container lists `project1 project2 project3` |
| E4 | Branches | `git branch --show-current` is `scion/<agent>`; pushing all four branches succeeds with four distinct remote refs |
| E5 | Harness healthy | no trust prompt in the tmux pane; agent activity leaves `starting` (status hooks fire, incl. Antigravity) |
| E6 | Messaging | admin → `project1-lead` delivered; `project1-lead` → `project1-eval-lead` result recorded (cross-project delivery is exploratory) |
| E7 | Prompts | rendered `project1-lead` instructions name `project1-eval-lead`/`project1-reviewer`, never bare roles |

Teardown, verified empty afterwards: agents and Hub projects, the GCP project (VM, disks, SAs, firewall go with it),
the GitHub repo, local gcloud configuration and SSH known-hosts entries for the VM.
