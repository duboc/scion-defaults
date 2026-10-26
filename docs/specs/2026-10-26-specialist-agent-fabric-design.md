# Specialist agent fabric: design

- **Status:** Draft for PO approval (2026-10-26). Nothing is built until the PO approves this document.
- **Inputs:** [L29](../lessons-learned.md#l29-a-real-monorepo-kubernetes-as-three-factories-and-leads-that-start-their-own-teams)
  (Kubernetes run, chain of command), [specialized-agents research](../plans/2026-10-26-specialized-agents-research.md)
  (`scion-frontiers/agent-team`), and the PO conversation of 2026-10-26.

## 1. Intent

Point this repository's skill at any repository, and get a deep, integrated team of agents for it, led by a lead,
without hand-writing templates. Today every compiled factory has the same thin build team (lead, backend, reviewer)
and one evaluator. Real repositories need more: research before building in unfamiliar ground, root-cause work on
bugs before fixes, and docs that keep up with the code. A coding agent sets the factory up end to end from this
repository's skills: the inspection, the interview, the compilation, and the Hub setup.

**Why now:** the Kubernetes run proved the base. The interview compiler handles any repository, including a monorepo
subfolder. Leads start their teams as Scion children, so the hierarchy is real in the Hub. Six leads ran two waves
of real issues. The team is the weak part, not the plumbing.

## 2. Goals

1. **Three default specialists in every build team:** researcher, doc writer, investigator, next to the lead,
   backend and reviewer.
2. **Discovery adds more,** for both teams, from a catalog (section 5). Each addition is proposed with its
   evidence, and the PO confirms it.
3. **Always-on teammates.** The lead starts every specialist at its first wake-up as its Scion child. Specialists
   wait for briefs and report only to their lead.
4. **Repo-specific prompts.** Each role's prompt names the paths it owns, the commands it runs, and the documents it
   reads, all taken from the inspection and the interview.
5. **A balance between the human and the agents** (section 7): explicit decision rights, one inbox for the PO, a daily
   digest, and an autonomy level per factory.
6. **Agent-driven setup.** The `scion-factory-bootstrap` skill drives the whole flow; the human only answers the
   interview and approves.

## 3. Non-goals

- On-demand, short-lived specialists. The PO chose always-on teammates; per-task agents can come later.
- Changing the backlog (GitHub issues, or `backlog/issues.json` without Git), the shared checkout per Hub project,
  the orphan evaluation branch, or the Firestore control plane.
- A web dashboard for the PO inbox. GitHub issues and Scion messages carry it.
- Importing third-party skills at agent run time. Section 9 decides how skills ship.
- Replacing the reference templates in `templates/` (the psearch reference factory). They keep working.
- The PO inbox, digest issue and eval findings issues in factories without Git. These features need Git. A factory
  without Git keeps today's behaviour: the lead escalates by Scion message, and the eval-lead reports findings to the
  PO by message.

## 4. Anti-goal (what makes this a failure even if it runs)

**The balance between the human on the loop and the agents' agency is lost.** Either agents pull the PO into
routine calls and flood the PO with messages, or agents decide what belongs to the PO (scope, architecture, cost,
acceptance) and the PO finds out after the fact or can't see what is happening. Section 7 exists to prevent both.

## 5. The team

### 5.1 Build team

**Always:**

| Role | Owns | Never |
|---|---|---|
| `lead` | Plans issues, sizes them, briefs, accepts results, gives the commit slot, escalates to the PO | Writes product code |
| `backend` | Implements, renamed per stack when needed (for example `go-dev`) | Reviews its own work |
| `reviewer` | Verdicts against invariants and ADRs (PASS/FAIL with evidence) | Fixes code |
| `researcher` | Cited reports before planning unfamiliar work: the code, upstream docs and issues, papers | Writes product code, invents citations |
| `doc-writer` | README, docs and knowledge plane kept in step with merged code; lists items of concern | Changes code to match docs |
| `investigator` | Reproduces bugs, pins the root cause at `file:line`, assigns a scope tier, proposes the approach | Fixes (a proof-of-concept patch is allowed only to show the cause) |

**Discovery catalog:**

| Role | Signals in the repository | Owns |
|---|---|---|
| `security-auditor` | auth or crypto code, secrets handling, a public API, `SECURITY.md`, `security` labels | Security review of every change, in parallel with the reviewer |
| `test-engineer` | low test-to-code ratio, `flaky` labels, no detected test command | Tests and missing edge cases |
| `frontend` | a UI folder (`package.json` with React, Vue or Svelte; templates) | UI changes; the backend keeps the server |
| `performance-analyst` | benchmarks, interview latency targets, `performance` labels | Before/after measurements; blocks regressions |
| `release-manager` | `CHANGELOG*`, release tags, a release workflow | Changelog and release notes from merged work |
| `migration-specialist` | schema migrations, DDL, infrastructure as code | Migrations and their rollback plans |
| `deployer` | `infrastructure.deploy: service` | Deploys and live checks (as today) |

### 5.2 Evaluation team

**Always:** `eval-lead` and `domain-evaluator` (as today).

**Discovery catalog:**

| Role | Signals | Measures |
|---|---|---|
| `contract-tester` | an OpenAPI, protobuf or GraphQL spec | The live service against the contract (L2) |
| `latency-prober` | interview latency targets, a deployed service | p50/p95/p99 against targets (L5) |
| `ui-auditor` | a UI folder, a deployed web page | Screenshots, accessibility, key-flow regressions |
| `safety-evaluator` | LLM or Gen AI client calls in the code | Prompt injection, grounding, PII leaks (L4) |
| `factory-analyst` | several factories on one Hub, or a monorepo | Agent activity, issue flow, bottlenecks |
| `eval-deployer` | `deploy: service` and an evaluation dashboard in the interview | Deploys the dashboard (as today) |

The evaluation team never messages build agents. Its findings become `factory:<prefix>` issues for the build lead.

### 5.3 How defaults enter a spec (builder's call, recorded here)

- `grill` pre-fills the three default specialists in every new draft roster with `default: true`.
- The PO can drop one only explicitly: `team.dropped_defaults: [{role, reason}]`.
- Older specs and the presets have no `focus`, `evidence`, `default` or `autonomy` fields. They keep passing
  `check`, which only warns about the missing defaults. `compile` adds the missing defaults with a note in the kit's
  `AGENTS.md`. A preset that already has a `researcher` keeps its own definition.
- A renamed implementer (for example `go-dev` instead of `backend`) is recognised by `default_kind: implementer`, so
  the defaults aren't added twice.

### 5.4 Rules for every specialist

- **Started by its lead,** listed in the lead's Chain of command table (the mechanism that exists since L29).
- **Reports only to its lead,** with the artifact path and a one-line result.
- **The author of a change is never its reviewer.** Parallel reviewers (reviewer, security auditor, performance
  analyst) don't start or message each other.
- **No heartbeats for specialists.** Only the two leads get heartbeats, so an idle specialist costs a container but no
  model tokens (L19).
- **One writer per file set.** The lead gives the commit slot. The doc writer commits on the build branch like any
  other writer.

## 6. How an issue flows

1. **Size.** The lead reads the issue and the knowledge plane, then decides who is needed:
   - a small change goes to the backend and reviewer;
   - a bug goes to the investigator first;
   - unfamiliar ground goes to the researcher first;
   - a change users can see adds the doc writer;
   - a matching catalog specialist joins when its signal applies (for example, the security auditor for auth code).
2. **Brief by file.** The lead writes `/scion-volumes/scratchpad/briefs/<issue>-<agent>.md` and sends
   `scion message <agent> "<lead> -> <agent>: #N: read /scion-volumes/scratchpad/briefs/<file>"`. A brief has the
   key locations, the deliverables (exact paths), the acceptance checks, and a termination line ("You MUST … then
   report to `<lead>`"). Briefs are never inlined, because shell metacharacters and long prompts break
   (agent-team).
3. **Pre-work.** The investigator writes `scratchpad/reports/<issue>-investigation.md` (reproduction, root cause at
   `file:line`, scope tier, recommended approach). The researcher writes `scratchpad/reports/<issue>-research.md`
   (summary, findings with sources rated by credibility, gaps, references).
4. **Build and review.** The backend implements from the reports. The reviewer and any parallel reviewers give
   verdicts.
5. **Commit.** On PASS, the lead gives the commit slot. The commit message ends with `(#N)`.
6. **Docs.** The lead briefs the doc writer with the commit. The doc writer updates the docs and the knowledge plane
   in a follow-up commit on the build branch, and runs `kp-check.py`.
7. **Evidence.** The lead comments the evidence on the issue and sets `status:verified`. Only the PO closes issues
   (L29).
8. **Durability.** A task is done only when its result is pushed or sent in a message. The scratchpad is durable for
   the life of the Hub project, but anything the next issue needs goes to the knowledge plane or the repository.

## 7. Human on the loop

### 7.1 Decision rights (default level `standard`)

| Agents decide alone | Agents decide and tell the PO | Waits for the PO |
|---|---|---|
| How to implement; which specialist to brief; test strategy; doc updates; retries and re-briefs | Each verified issue (evidence comment); new issues filed from findings; specialists restarted | Scope changes; new cost or IAM; decisions that create an ADR; accepting work (closing the issue) |

### 7.2 One inbox

Every decision that waits for the PO becomes a GitHub issue with the label `status:po-decision` and
`factory:<slug>`. Every factory gets a `factory:<slug>` label, not only monorepo factories, so one query covers all
factories on a repository. A monorepo factory uses its `agent_prefix` as the slug. The issue holds the question, the options, and the lead's recommendation. The PO answers on the
issue, and the lead keeps working on other issues meanwhile. A reminder goes out every 24 hours without an answer.
`gh issue list --label status:po-decision` across the repository is the PO's whole inbox.

### 7.3 Daily digest and "tell" notices go to GitHub, not to Scion messages

The watchdog turns every `scion message user:<PO>` into a `PO_ESCALATION` event at severity ERROR, and the alert
policy fires on it (`factory-watchdog.py` check 4, `setup-alerts-and-budgets.sh`). A daily digest sent as a message
would therefore page the PO every day, which is the anti-goal. So:

- **Digest:** each lead adds one comment a day to a pinned issue per factory, "`<slug>` digest" (labels `digest` and
  `factory:<slug>`). The comment covers what shipped, what is blocked and on whom, and what waits in the inbox.
- **"Tell" notices:** comments on the issue concerned (the evidence comment at `status:verified`, a link from a new
  issue to the finding that caused it). The lead's current per-issue "ready for acceptance" message to the PO is
  replaced by a single line in the digest.
- **Scion messages to the PO** are only for `status:po-decision` items. Each message links to its issue, and the
  ERROR alert is the right signal for those.
- The watchdog and alert policy stay unchanged.

The builder must also verify one thing: the watchdog wakes `suspended` agents. In the Kubernetes run, idle teammates
sat at `completed`, not `suspended`. Confirm that an idle specialist isn't woken every 5 minutes; if it is, exclude
specialists from that wake.

### 7.4 Autonomy level

The interview records `autonomy: supervised | standard | autonomous` per factory (default `standard`). The kit puts
it in the env file and the prompts:

- `supervised`: before work starts on an issue, the lead comments its plan and adds `status:po-review`. It waits
  until the PO replaces that label with `status:approved`, working on other approved issues meanwhile. There is no
  per-commit approval, because it would hold the commit slot for hours. Acceptance stays the PO closing the issue.
- `standard`: section 7.1.
- `autonomous`: only cost, IAM and acceptance wait for the PO. Scope and ADR decisions are told, not asked.

Changing the level means recompiling and re-pushing the templates. The heartbeat and watchdog don't change.

## 8. Factories with nothing to deploy (PO decision, 2026-10-26)

A repository with nothing to deploy is first-class: a library, a CLI, or a package of a monorepo, such as the
Kubernetes factories that used placeholder `none` services.

- **Interview:** a new answer, `infrastructure.deploy: service | none`. With `none`, the interview doesn't ask for a
  serving service, an eval service, deployer IAM roles, a health endpoint or latency targets, and readiness doesn't
  require them.
- **Roster:** no `deployer` and no `eval-deployer`. The shared protocol's deploy section, the env file's service
  variables and `verify-factory.sh`'s live checks are left out.
- **Evaluation pyramid for code:** the evaluation team grades the code itself, on the evaluation branch, against
  the build branch's commits:
  - **L1 builds and tests:** the build and test commands pass at the head.
  - **L2 API compatibility:** for example `apidiff` or `go vet` for Go, `cargo semver-checks` for Rust, a
    public-API snapshot for Python or TypeScript. The interview picks the tool from the detected stack.
  - **L3 quality metric:** the interview's numeric metric, such as coverage, a benchmark score or lint findings.
  - **L4 safety:** dependency and vulnerability checks, such as `govulncheck`, `pip-audit` or `npm audit`.
  - **L5 performance:** benchmarks against a recorded baseline, when the repository has benchmarks.
- **Eval catalog:** a new `benchmark-runner` (signal: benchmark files) and an `api-compat-checker` (signal: a public
  library API) join section 5.2. The `contract-tester`, `latency-prober` and `ui-auditor` need a deployed service, so
  discovery doesn't propose them with `deploy: none`.
- **Compatibility:** existing specs mean `deploy: service`, and nothing changes for them.

## 9. Discovery and setup

1. **Inspection.** `inspect_repository` grows a signals pass that returns, for each catalog role, the evidence it
   found: paths, counts, file names, and issue labels when `gh` is available. It stays deterministic and offline-safe
   (issue labels are optional).
2. **Interview.** A new topic, "Team", presents the default roster and each proposed specialist with its evidence.
   The PO confirms or drops each one, and the topic records the autonomy level. This follows the existing rule: the
   tool proposes, and the human confirms.
3. **Spec.** `factory.yaml` records each role with `role`, `harness`, `model`, `responsibility`, plus new fields:
   `focus` (owned paths, commands, documents), `evidence` (why discovery proposed it), and `default: true|false`.
   The schema and the readiness check validate them.
4. **Compile.** One template per role from a role library in the compiler. Each template has the common parts
   (identity, Chain of command, durability, briefs) plus the role's protocol and its repo-specific `focus`. The
   lead's Chain of command lists every confirmed role.
5. **Hub setup.** Unchanged: README steps 4 to 8, with the PO starting only the two leads.
6. **Skill.** `skills/scion-factory-bootstrap/SKILL.md` gains the Team topic and the autonomy question, and keeps
   "never invent intent".

## 10. Constraints and the builder's choices

**Constraints (decided):**
- TDD for every compiler change, and `DRY_RUN=1` for every script (AGENTS.md).
- Serverless only, with Firestore as the factory control plane.
- No secrets or real identifiers in tracked files.
- Default harnesses are Claude Code for the leads and Antigravity for the other roles (L28).
- `AGENTS.md` is the project instruction file.

**Left to the builder (with these defaults):**
- **Researcher harness:** Antigravity, like the others. Gemini CLI with Google Search is attractive (agent-team pins
  it), but it isn't verified on this Hub. Verify it in one factory before making it the default.
- **Skills:** vendor the few protocols we need (research, investigation, doc sync) as prompt sections compiled into
  each template, not as runtime skill imports by URI. This keeps one source of truth in the compiler. Revisit URI
  imports when Scion's skill support is verified on the Hub.
- **Signal thresholds** (for example, the test-to-code ratio for `test-engineer`): start simple, and record the
  evidence so the PO decides.
- **VM size:** a full roster is about 6 to 13 agents per factory. Recommend `medium` above 15 agents on one Hub; the
  Kubernetes run had 15 agents on `medium` with room to spare.

## 11. Verification

- **Unit:** the compiler emits the three default specialists for every preset; discovery proposes each catalog role
  from a fixture repository with its signal and none without it; the lead's Chain of command lists exactly the
  confirmed roles; the autonomy level changes the prompts as section 7.4 says; the PO decision label and the digest
  appear in the leads' prompts.
- **End to end:** one fresh factory on an existing Hub against a real repository. The PO starts only the two leads.
  - The leads start the whole roster, shown by `scion list --lineage`.
  - A bug issue goes investigator, then backend, then reviewer.
  - An unfamiliar issue goes researcher first.
  - A merged change is followed by a doc-writer commit that passes `kp-check`.
  - One scope question lands as a `status:po-decision` issue.
  - One daily digest arrives.
  - Record the evidence in a new lesson.
  - Repeat the end-to-end run with one `deploy: none` library factory (for example a Go package), whose evaluation
    team grades L1 to L5 on the code.
