# Specialized agents: research input (2026-10-26)

Status: **research only, no design yet.** This note records what we learned before designing specialized roles
(researcher, investigator, doc writer, and more) that a lead runs. The goal is a repo-agnostic team that an agent
sets up from this repository's skills after it inspects a target repository. The design conversation hasn't started:
the open question is what such a team must do on the next repository that today's lead, backend and reviewer can't.

## What the Kubernetes run proved (L29)

- **Chain of command:** a lead that starts its own team with `scion start` is its Scion parent. The project's tree
  view shows the lead on top, and the lead gets its team's notifications. Six leads started their teams within
  5 minutes, and wave 2 ran through that hierarchy.
- **Close rule:** the protocol must say who closes an issue. Only the PO does.
- **Repo-agnostic compile:** the interview compiler already produces kits for any repository: a monorepo subfolder
  with `monorepo.subdir` and `agent_prefix`, a backlog scoped by `factory:<prefix>`, and a knowledge plane per product.

## Prior art: `scion-frontiers/agent-team`

Source: <https://github.com/scion-frontiers/agent-team/tree/main/templates>.

**What it is:**
- 13 generic templates. Each has `scion-agent.yaml` (description, `agent_instructions`, `system_prompt`, and
  `skills:` imported by URI, such as `gh://<org>/<repo>/<skill>`, cross-org allowed), `agents.md` and
  `system-prompt.md`.
- The roles:
  - coordinator, eng-manager, architect (writes `design.md`);
  - investigator (reproduces, finds the root cause at `file:line`, assigns a scope tier, doesn't fix);
  - developer, code-reviewer, test-engineer, security-auditor;
  - qa-tester (runs E2E, verdicts PASS / PASS-WITH-NITS / FAIL, files findings and doesn't fix);
  - doc-writer ("docs follow code", finds doc-site rules in nested `AGENTS.md`, lists items of concern);
  - release-notes;
  - researcher (5-phase protocol with credibility ratings and gaps, never invents citations, pinned to
    `gemini-cli` for Google Search);
  - web-builder.
- Only the researcher pins a harness.

**Patterns worth copying:**
- **Ownership tree = notification topology.** The creator of an agent is subscribed to its notifications. That
  matches our chain of command.
- **Sizing by tree depth.** Small work is coordinator → developer + reviewer. Large work is coordinator → issue
  owner → investigator, architect, manager → developers + reviewers. They allow at most 2 ownership layers, and
  every orchestrating role must collapse itself when the work is small.
- **File-based briefs.** The start prompt says "Read and implement `<scratchpad>/projects/<slug>/briefs/<agent>.md`".
  Briefs are never inlined, because backticks and `$` break `sh -c`, and long briefs abort silently. A brief has Key
  Locations, Deliverables (exact paths), Direct Contact, and a termination line ("You MUST … and then mark the task
  complete"). Any deviation from the worker's own template must be stated in the brief.
- **Durability.** A result is durable only when it is reachable after the container is deleted: a pushed ref, a
  mounted volume, or a sent message. Signal completion only after the push. Check the content of an artifact, not just
  that it exists.
- **Waiting.** After starting a worker: `scion list`, `sciontool status blocked "<reason>"`, then stop (no polling).
  Verify a completion notification with `scion look`. Send `scion message <agent> "continue"` before recreating an
  agent.
- **Reviews.**
  - The author is never the reviewer.
  - Rotate the reviewer and keep the author.
  - Cap at 6 rounds, then escalate.
  - Non-blocking findings are resolved or explicitly declined.
  - Specialist reviewers run in parallel and never start other agents.
- **Single source of truth.** Rules live in one skill, and the templates point to it. YAML comments explain why a
  skill is included or excluded.
- **Lifecycle.** A fresh agent per task, with numbered names (`-dev-2`). Delete finished agents only after their work
  is on the remote.

**Where it differs from this repository** (the design must reconcile these):
- **Backlog:** work enters by message to the coordinator, and tracking lives in scratchpad state files. Here, GitHub
  issues are the backlog.
- **Checkouts:** they use one work branch per agent plus PRs, merged by the manager. Here, each Hub project has one
  shared checkout with a commit slot. Their closest fit is code ownership: each agent owns its own folders.
- **Evaluation:** their QA, test and security agents are children of the same tree. Here, evaluation is an independent
  team on an orphan branch.
- **Layers:** they allow at most 2 ownership layers, under one top coordinator. Here, build and eval leads run in
  parallel per factory.
- **Self-starting:** "Don't self-start before user direction" conflicts with our always-on heartbeats. In the
  Kubernetes run, the leads started on their wake-up prompt alone.
- **Instruction file:** they assume `CLAUDE.md` as the project instruction file. Here it is `AGENTS.md`.
- **Repository discovery:** they have none; their templates rely on conventions in the target repository. Here, the
  interview inspects the repository and compiles the kit.

## Open questions for the design

1. **The why:** what must the specialists do on the next repository that the current team can't? For example:
   investigate production bugs, research before building, keep docs in sync, or audit security.
2. **Lead or library:** does the lead size each issue (the agent-team model), or does the interview fix the roster
   per repository (the current model)?
3. **Two layers:** do specialists live under the build lead only, or also under the eval lead (a researcher for
   benchmarks, for example)?
4. **Skills:** import external skills by URI, as agent-team does, or vendor them into this repository?
