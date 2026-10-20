# Spec: Knowledge plane and optional Git in the factory bootstrap

- **Status:** Accepted for implementation (2026-10-20)
- **Owner:** scion-defaults maintainers
- **Related:** [`skills/scion-factory-bootstrap/SKILL.md`](../../skills/scion-factory-bootstrap/SKILL.md),
  [`scripts/scion_factory.py`](../../scripts/scion_factory.py), [`docs/grill-me-playbook.md`](../grill-me-playbook.md)

## Goal

The bootstrap configures a new Scion factory from the human's answers plus the lessons L1–L27, driven by a coding
agent. It must also:

1. Produce a **knowledge plane** equivalent to the one that made `psearch` work: an index, requirements with stable
   IDs, ADRs, contracts, invariants, project-specific playbooks, and a separate live-state file.
2. **Learn** the knowledge plane from the target repository when sources exist, and **elicit** it with grill-me
   questions when they don't.
3. Make **Git optional**. A factory can start without a Git remote and add Git later without being rebuilt.

## Non-goals

- The tool doesn't call an LLM. Extracting requirements or invariants from prose is the agent's job; the tool only
  detects sources, validates, and renders.
- The tool doesn't change a live Hub. Scripts that touch GitHub or a workspace are generated with a dry-run default.

## Design

### Spec changes (`factory.yaml`)

- New `vcs` section replaces `project.github_repo`, `project.build_branch`, and `project.eval_branch`:

  ```yaml
  vcs:
    enabled: false          # true = GitHub issues + branches; false = file backlog + snapshot deploys
    provider: github
    repo: ""                # required only when enabled
    build_branch: main
    eval_branch: eval-dashboard
  ```

- New `knowledge_plane` section:

  ```yaml
  knowledge_plane:
    root: knowledge-plane
    sources:                # existing files the agent learned from, indexed in place (never moved)
      requirements: []      # e.g. PRODUCT.md, docs/prd.md
      adrs: []              # e.g. docs/adr
      contracts: []         # e.g. api/openapi.yaml
      design: []            # e.g. DESIGN.md, ARCHITECTURE.md
      guides: []            # e.g. CONTRIBUTING.md, AGENTS.md
    requirements:           # stable IDs cited by issues, ADRs, and eval runs
      - {id: REQ-001, title: "", acceptance: [""]}
    invariants: [""]        # testable MUST rules; the reviewer's checklist
    decisions:              # seed ADRs elicited in the interview
      - {id: ADR-001, title: "", context: "", decision: "", consequences: ""}
    glossary: []            # {term, definition}
  ```

- Wave issues may cite `requirements: [REQ-001]`.

### Readiness rules

- `vcs.enabled: true` requires `vcs.repo`, `vcs.build_branch`, and `vcs.eval_branch`, and the two branches differ (L25).
  `vcs.enabled: false` requires none of them.
- The knowledge plane needs at least one requirement with acceptance criteria and at least one invariant.
- Every `REQ-` ID cited by a wave issue must exist in `knowledge_plane.requirements`.

### Learning from the repository

`inspect` reports a `knowledge_sources` map by scanning well-known paths (ADR folders, PRD and requirements files,
OpenAPI, protobuf, and GraphQL contracts, design docs, and agent guides) and a `vcs` map (`is_git_repo`, GitHub
remote). The draft spec pre-fills `knowledge_plane.sources`, and `vcs.enabled`/`vcs.repo` when a GitHub remote exists.
The agent reads the sources and proposes requirements, invariants, and decisions; the human confirms each one.

A sixth interview pillar, `6_knowledge_plane`, has two questions: `knowledge_sources` (confirm or supply sources) and
`knowledge_requirements_invariants` (requirements, invariants, and decisions).

### Generated knowledge plane

```none
knowledge-plane/
├── INDEX.md                 # router: task -> file; links learned sources in place; no live state
├── product/requirements.md  # REQ table + acceptance criteria
├── adr/_template.md         # header: status, requirements, issues, migrations, supersedes
├── adr/ADR-NNN-slug.md      # one per seeded decision
├── contracts/README.md      # points at learned contracts; L2 source of truth
├── invariants.md            # numbered MUST rules
├── glossary.md
├── playbooks/README.md      # project-specific how-tos only; factory process stays in templates/_shared
└── state/LIVE.md            # live revision, rollback target, snapshot/commit; the deployer is the only writer
```

Rules:

1. **Seed once.** `compile` never overwrites an existing file under `knowledge-plane/` or `backlog/`, because agents
   edit them after bootstrap. `--force-knowledge` overrides.
2. **Check references.** The kit ships `knowledge-plane/kp-check.py`, which travels with the knowledge plane into the
   workspace. It fails if a Markdown link, an ADR, or a cited `REQ-` ID doesn't resolve (prevents L4).
3. **Golden data stays with the eval team.** The kit writes `eval-workspace/golden/README.md` for the eval project;
   the build knowledge plane never contains L3 golden datasets.
4. The watchdog's knowledge-plane sync reads its source project from `KNOWLEDGE_PLANE_SOURCE_PROJECT` (default: the
   first entry in `FACTORY_PROJECTS`) instead of hardcoding `psearch`.

### Without Git (`vcs.enabled: false`)

| Concern | With Git | Without Git |
|---|---|---|
| Backlog | GitHub issues and `status:*` labels | `backlog/issues.json`; the lead is the only writer, workers report through `scion message` |
| Integration | Commit slot, push to `build_branch` | Lead-granted edit slot in the shared `/workspace` |
| Deploy source | `git archive <sha>` to a temp dir | `tar` snapshot of `/workspace` after reviewer PASS; SHA-256 recorded in `state/LIVE.md` |
| Eval isolation | Orphan `eval_branch` | Separate Hub project workspace (`eval_slug`) |
| Seeding | `scripts/seed-github-waves.sh` | `backlog/issues.json` written by `compile` |

### Adding Git later

- `scion-factory enable-git factory.yaml --repo ORG/NAME [--build-branch main] [--eval-branch eval-dashboard]`
  updates the spec in place and validates it. Recompiling the kit regenerates templates in Git mode; seed-once keeps
  the knowledge plane and backlog.
- No-Git kits include `scripts/enable-git.sh` (dry run unless `DRY_RUN=0`). Run in the build workspace, it initializes
  the repo, pushes `build_branch`, initializes the orphan `eval_branch` from the eval workspace, and migrates
  `backlog/issues.json` items to GitHub issues with their current `status:*` label.

## Verification

- New tests in `tests/test_scion_factory.py`: no-Git compile (backlog, snapshot deploy text, no `gh issue`, enable-git
  script), knowledge sources detection, knowledge plane rendering, seed-once, unknown `REQ-` rejection, `enable-git`
  round trip, and `kp-check` passing on a compiled kit.
- Existing tests stay green; presets migrate to the `vcs` section.
- `bash -n` on every generated shell script.
