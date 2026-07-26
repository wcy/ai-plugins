---
name: mplan
description: Use when you need to generate modular implementation plans from completed specifications in a multi-repo workspace — triggers on phrases like "create plan", "generate plan", "make a plan", "plan the implementation", "break into stories", or when the user wants to turn specs under `context/<repo>/spec/` into executable agent work plans
---

# Create Modular Implementation Plan from Spec

Take completed specifications and produce a set of modular, context-efficient implementation plan files that can be executed by a team of Claude Code agents.

**This is a documentation project. Do not write application code. The deliverables are plan files under `context/project/plans/`.**

## Step 0: Understand the Layout & Scope

A workspace splits the **spec per repository** (`context/<repo>/spec/`, plus a contract-only `context/shared/spec/`). Changes are tracked at two levels: **repo-level** files in `context/<repo>/changes/` and a **project-level index** in `context/project/changes/` that references them. Plans live in `context/project/plans/`. A single plan may contain stories for **more than one repo** — each story is tagged with the repo it implements. The `shared` layer is contract-only and is never planned directly; its contracts are implemented by the repos that consume them.

Determine scope:

- **Incremental plan** (a project-level index exists): the latest `PROJECT-CHANGE-<NNN>-*.md` in `context/project/changes/` drives the plan. Follow its "Repo Change Files" table to the per-repo change files, which name the affected modules and code paths — plan exactly those.
- **Full plan** (no project-level indexes exist): plan the whole workspace by default — every module in every repo's `CATALOG.yaml`. If the user names a subset (e.g. "plan only repo-a", "plan only AUTH and USERS"), scope to that.

**Path convention for the rest of this skill:**
- `context/spec/...` is shorthand for `context/<repo>/spec/...` for the repo a given story belongs to. Shared interface files are referenced by their full path under `context/shared/spec/`.
- `context/project/plans/...` and `context/<repo>/changes/...` are **literal paths** — never mixed up.

## Prerequisites

### Step 1: Determine Plan Type

Check `context/project/changes/` for project-level index documents (`PROJECT-CHANGE-<NNN>-*.md`):

- **No `context/project/changes/` directory or no files** → **Full plan.** Output goes to `context/project/plans/000-initial/`. Read every in-scope repo's `context/<repo>/spec/CATALOG.yaml` and all spec files in those catalogs.
  - **Greenfield** (no source code exists yet in `repos/`): generate stories for every module in every in-scope repo.
  - **Update sub-mode** (source code already exists — check each `repos/<repo>/` for `src/`, `lib/`, or equivalent): diff existing code against the spec. Only create stories for gaps or changes; mark already-compliant modules as "Verify". See [Update Sub-Mode](#update-sub-mode) in the Generation Process.
- **One or more project-level indexes exist** → **Incremental plan.** Find the `PROJECT-CHANGE-<NNN>-*.md` with the highest `<NNN>` and `status: pending`. Read it. Output goes to `context/project/plans/<NNN>-<slug>/` where `<NNN>-<slug>` is taken directly from the project change file name (e.g., `PROJECT-CHANGE-003-adapter-retry-logic.md` → `context/project/plans/003-adapter-retry-logic/`). Then follow its "Repo Change Files" table to read each referenced repo-level change file for affected modules and code paths.

**Scope filtering (full plan only):** If the user specifies a subset of repos or module TAGs (e.g., "plan only repo-a", "plan only AUTH and USERS"), generate stories only for those. Still include their lower-layer dependencies, but mark those as "Verify existing" rather than "Implement".

### Step 2: Load Context

**Full plan:** For each in-scope repo, read `context/<repo>/spec/CATALOG.yaml` to get its layer/module structure and `shared_interfaces` list, then read all spec files referenced in that catalog. For every shared interface a repo consumes, also read its `*-INTERFACE.md` (and `*-DATAMODEL.md`) from `context/shared/spec/<IFACE>/` — these are part of the context any module that depends on them needs.

**Incremental plan:** Read the latest `PROJECT-CHANGE-<NNN>-*.md` in full, then for each entry in its "Repo Change Files" table, read the referenced repo-level change file and:
1. Extract each spec file listed in **Spec Files Modified** (noting which repo each belongs to)
2. Read their `depends_on` entries from the owning repo's `CATALOG.yaml` (one level deep) — including any `context/shared/spec/<IFACE>/<IFACE>-INTERFACE.md` paths
3. Read each owning repo's `CATALOG.yaml` (for wave/layer assignments)

**Note on shared-interface cascades:** if the project-level index carries `<!-- scope: shared -->`, it changed a cross-repo contract. Plan stories for each consuming repo's affected modules, and load the changed `context/shared/spec/<IFACE>/` files as context for those stories.

## Role

You are a technical project manager creating modular, context-efficient implementation plans for Claude Code agent teams.

## Output File Structure

Each plan directory contains one **story file per module** (human-readable, self-contained) plus two machine-readable files that make the plan executable and resumable by `mexecute`:

- **`plan.yaml`** — the immutable **plan graph**: waves, stories, dependencies, and per-story validation. `mexecute` reads this instead of re-deriving structure from the story prose. Conforms to `${CLAUDE_PLUGIN_ROOT}/schemas/plan-graph.schema.json`.
- **`state.yaml`** — the initial **plan-level state**: every story `pending`, no attempts yet. `mexecute` mutates this as it runs. Conforms to `${CLAUDE_PLUGIN_ROOT}/schemas/plan-state.schema.json`.

`mplan` also records this plan in the **project-level ledger** `context/project/state.yaml` (create it if absent) with `status: pending` — the entry point that resume/orchestration reads to find the unfinished plan. Conforms to `${CLAUDE_PLUGIN_ROOT}/schemas/project-state.schema.json`.

### Full Plan (`000-initial`)

```
context/project/plans/000-initial/
├── PLAN-{WW}-{SS}-{REPO}-{MODULE}.md    # One per module, across all in-scope repos
├── plan.yaml                            # plan graph (immutable structure)
└── state.yaml                           # initial plan-level state (all stories pending)
```

### Incremental Plan (`<NNN>-<slug>`)

```
context/project/plans/<NNN>-<slug>/
├── PLAN-{WW}-{SS}-{REPO}-{MODULE}.md    # One per affected module, across all repos in the change
├── plan.yaml
└── state.yaml
```

Only modules that appear in the referenced repo-level change file(s)' **Spec Files Modified** or **Affected Code Paths** tables get a story file. Unchanged modules are skipped.

### Naming Convention (both plan types)

`PLAN-{WW}-{SS}-{REPO}-{MODULE}.md` where:
- `{WW}` = zero-padded wave number (derived from layer/altitude order — see below)
- `{SS}` = zero-padded story number within the wave (stories in the same wave get sequential numbers)
- `{REPO}` = the repository directory name this story implements (so two repos may each have a module with the same TAG without colliding)
- `{MODULE}` = module name exactly as it appears in that repo's CATALOG.yaml

**Wave assignment (multi-repo):** Waves order work by altitude across the whole plan. Shared interface contracts are the highest altitude but are never implemented here (no stories) — they are pure context. Within each repo, the first layer is the earliest wave, the next layer the next wave, etc. Modules at the same altitude — **including modules in different repos** — share a wave and can run in parallel, unless a story depends (via a shared interface) on another repo's module being implemented first; in that case place the consumer in a later wave than the producer. Each story's **Prerequisites** and **Parallel group** make the actual ordering explicit.

## Story File Template (PLAN-{WW}-{SS}-{REPO}-{MODULE}.md)

Each story file is **fully self-contained** — an agent can implement and validate it without reading any other plan file.

The full template lives alongside this skill at `${CLAUDE_SKILL_DIR}/PLAN-STORY-TEMPLATE.md` (`${CLAUDE_SKILL_DIR}` is substituted at runtime to this skill's directory in the plugin's marketplace cache — nothing is copied into the project). **Read it once when you reach the story-generation step** (the Generation Process below), then copy the block between its `BEGIN STORY TEMPLATE`/`END STORY TEMPLATE` markers into each story and fill in the `{placeholders}`. It is kept out of this skill body so it is not in context during planning/scoping.

Conditional sections in that template are gated by inline comments:
- **Change Scope** (`<!-- INCREMENTAL PLANS ONLY -->`) — include only in incremental (`<NNN>-<slug>`) plans, immediately after the header; omit for full `000-initial` plans.
- **Compliance Status** header field — update sub-mode only.
- **Final Validation (last wave only)** — only in stories belonging to the last wave.

Each story file must stay **small and focused** — as small as the work allows, with no hard line limit (see *Context Size Budget*).

## Generation Process

### Full Plan (`000-initial`)

1. Read every in-scope repo's `context/<repo>/spec/CATALOG.yaml` to get its exact layer/module structure
2. Read all spec files referenced in those catalogs, plus the shared interface files any of them consume
3. Map layers to waves across the whole plan (see "Wave assignment (multi-repo)"): same-altitude modules — including those in different repos — share a wave
4. For each module in each in-scope repo's catalog, generate `PLAN-{WW}-{SS}-{REPO}-{MODULE}.md` containing:
   - The owning repo in the **Repo:** header
   - Exact context files derived from each spec file's `depends_on` fields in that repo's CATALOG.yaml
   - Cross-module dependencies listed explicitly (only INTERFACE files from other modules; cross-repo only via `context/shared/spec/`)
   - Acceptance criteria derived from `*-TESTING.md` specs (if the module has a testing facet)
   - Wave number (with total wave count), parallelization group, and prerequisite story files
   - Implementation tasks adapted to the facets that actually exist for this module
   - Post-Story Validation checklist
   - Final Validation section (last wave stories only)
5. Verify: every spec file in every in-scope catalog is referenced in exactly one story; only INTERFACE cross-references

### Update Sub-Mode

Applies when doing a full plan (000-initial) but source code already exists. After steps 1–3 of the full plan above:

4. **Diff each module** against its INTERFACE and DATAMODEL spec. For each module determine:
   - **Implement** — no existing code; generate a standard story
   - **Update** — code exists but has gaps or spec drift; generate a story listing only the specific gaps
   - **Verify** — code appears compliant; generate a minimal story that only runs tests to confirm

5. **Migration stories** — If a spec type, function signature, or API endpoint was renamed or removed compared to what exists in code, add a migration story before the implementation story, named `PLAN-{WW}-{SS}m-{REPO}-{MODULE}-MIGRATION.md`. It must describe the old contract, the new contract, and any data migration steps.

6. **Preserve working code** — Stories for "Update" and "Verify" modules must explicitly state which files to leave untouched. Do not re-implement modules that already match their spec.

7. Set **Compliance Status** in each story's header to match the module's classification.

---

### Incremental Plan (`<NNN>-<slug>`)

1. Read the latest `PROJECT-CHANGE-<NNN>-*.md` (highest `<NNN>` in `context/project/changes/` with `status: pending`)
2. For each entry in its "Repo Change Files" table, read the referenced repo-level change file
3. Extract the list of affected modules — and the repo each belongs to — from each repo change file's **Spec Files Modified** and **Affected Code Paths** tables
4. Read each affected module's owning repo `context/<repo>/spec/CATALOG.yaml` to determine its layer (wave assignment)
5. Read only the spec files for affected modules (plus their one-level `depends_on` deps, including any shared `*-INTERFACE.md`)
6. For each affected module, generate `PLAN-{WW}-{SS}-{REPO}-{MODULE}.md` containing:
   - A **Change Scope** section listing exactly which source files to touch and what to change (from the repo-level change file's **Affected Code Paths** table — only the rows for this story's repo)
   - The path to the repo-level change file (e.g. `context/repo-a/changes/CHANGE-003-retry-logic.md`) so the agent can read it
   - Context files (same derivation as full plan, but scoped to affected modules)
   - Implementation tasks limited to the facets mentioned in the change file
   - Acceptance criteria from the repo change file's **Validation Checklist** plus relevant TESTING spec criteria
   - Post-Story Validation checklist
   - Final Validation section (last wave stories only), referencing the repo change file's **Validation Checklist**
7. Verify: every entry in every referenced repo change file's **Affected Code Paths** is covered by a story; no story touches modules not listed in those change files

## Emit the Plan Graph, State & Ledger

After all story files are written, emit the machine-readable artifacts that make the plan executable and resumable. Do this **from the same story set** you just wrote — the graph must agree with the story files exactly.

**Story id convention.** A story's id is its filename minus the `PLAN-` prefix and `.md` suffix: `PLAN-01-02-repo-a-AUTH.md` → `01-02-repo-a-AUTH` (migration stories keep their `m`, e.g. `01-02m-repo-a-AUTH-MIGRATION`). Use this id as the key in `plan.yaml`, `state.yaml`, and every prerequisite/parallel-group reference.

### 1. `plan.yaml` (the plan graph)

Write `context/project/plans/<plan-id>/plan.yaml` per `${CLAUDE_PLUGIN_ROOT}/schemas/plan-graph.schema.json`:

- `plan_id` = the plan directory name (`<NNN>-<slug>` or `000-initial`); `type` = `full` or `incremental`; `project_change` = the PROJECT-CHANGE `<NNN>` for incremental plans, else `null`; `repos` = every repo any story targets.
- One `waves[]` entry per wave, listing its story ids in order.
- One `stories{}` entry per story, carrying `file`, `repo`, `module`, `wave`, `prerequisites` (story ids from earlier waves), `parallel_group` (same-wave siblings), `change_file` (incremental only, else `null`), `target_paths` (the source paths this story writes — used later to confirm module-disjointness), and `validation`.
- **Lift validation into the graph.** Translate each story's Post-Story / Final Validation into `validation.post_story[]` / `validation.final[]` steps. Prefer **`kind: exit-code`** with a runnable `command` (build/type-check/test) so `mexecute` can gate mechanically; use `kind: prose` only where a step is genuinely agent-interpreted. `validation.final` appears only on last-wave stories.

### 2. `state.yaml` (initial plan-level state)

Write `context/project/plans/<plan-id>/state.yaml` per `${CLAUDE_PLUGIN_ROOT}/schemas/plan-state.schema.json`: `status: pending`, one `waves[]` entry per wave (`status: pending`), and one `stories{}` entry per story with `repo`, `wave`, `status: pending`, `retries: 0`. Leave `attempts`, `integration_branches`, `conformance`, and `telemetry` for `mexecute` to fill.

### 3. Project ledger entry

Update `context/project/state.yaml` (create it, `version: 1`, if absent) per `${CLAUDE_PLUGIN_ROOT}/schemas/project-state.schema.json`: add/update the `plans.<plan-id>` entry with `status: pending`, `project_change`, and `plan_dir`. Never overwrite other plans' entries.

### 4. Validate on write

Before reporting completion, validate every emitted file with the runnable checker — reject and fix on any error:

```
python3 ${CLAUDE_PLUGIN_ROOT}/schemas/validate.py plan-graph    context/project/plans/<plan-id>/plan.yaml
python3 ${CLAUDE_PLUGIN_ROOT}/schemas/validate.py plan-state    context/project/plans/<plan-id>/state.yaml
python3 ${CLAUDE_PLUGIN_ROOT}/schemas/validate.py project-state context/project/state.yaml
```

## Context Size Budget

Keep each story file **small and focused** — as small as the work allows, with no hard line limit. If a story is growing too large to stay self-contained:
- Split into sub-stories with focused scope (e.g., `PLAN-02-01a-FOO-TYPES.md`, `PLAN-02-01b-FOO-CLIENT.md`)
- Move detailed implementation notes to the spec files (they belong there anyway)
- Keep the story file focused on: context files, task order, and acceptance criteria

## Post-Generation Story Validation (subagents)

After the stories and machine-readable artifacts are written, **fan out validators** to catch problems a single author misses — the story files are independent, so validation parallelizes cleanly. Dispatch one validator subagent **per story** (batch small plans, one per wave, to keep the fan-out reasonable), in parallel (single message, multiple Agent tool calls). Each validator is scoped to one story and its declared context, blind to the others, and checks:

- **Self-containment** — could an agent implement this story loading **only** the files in its Context Files list? Flag anything the tasks require that isn't listed.
- **Context minimality & correctness** — every Context File traces to a `depends_on` in the owning repo's `CATALOG.yaml`; no wildcards; no unrelated files.
- **Coupling** — cross-module references are **INTERFACE-only**; cross-repo context points **only** into `context/shared/spec/` (never another repo).
- **Single-repo** — the story names exactly one repo and touches only `repos/<that-repo>/`.
- **Testable acceptance criteria** — each criterion passes/fails unambiguously.
- **Graph agreement** — the story's wave/prerequisites/parallel-group match its `plan.yaml` entry.

Have each validator return a small structured verdict (`story`, `ok: true|false`, `issues: []`). **Fix every flagged story** (and re-emit `plan.yaml`/`state.yaml` if a fix changes structure), then re-run the schema checks. Only proceed to Final Validation once the fan-out is clean.

## Final Validation (of the plan itself)

Before finalizing the plan, verify:

1. **Every spec file is referenced** — No orphan specs in any in-scope repo's CATALOG.yaml
2. **Every story names its repo** — Each story has a **Repo:** header and a `PLAN-{WW}-{SS}-{REPO}-{MODULE}.md` filename
3. **Layer order is respected** — No story's prerequisites include a later wave
4. **Only INTERFACE cross-references** — No IMPLEMENTATION files from other modules in context lists; cross-repo context entries point only into `context/shared/spec/` (a shared `*-INTERFACE.md`/`*-DATAMODEL.md`), never into another repo
5. **Acceptance criteria are testable** — Each criterion can pass/fail unambiguously
6. **Context lists are minimal** — Only files from `depends_on` fields, not wildcards
7. **Files are self-contained** — An agent can implement a story by loading only the listed context files
8. **Final Validation section present** — Exactly one wave's stories contain the Final Validation section (the last wave)
9. **Graph agrees with the story files** — every `PLAN-*.md` has exactly one `plan.yaml` story entry and vice versa; each story's `wave`/`prerequisites`/`parallel_group` in `plan.yaml` matches its story-file header; every id in `waves[]`, `prerequisites`, and `parallel_group` resolves to a real story; no prerequisite points to a later wave
10. **Single-repo stories** — every `plan.yaml` story names exactly one `repo`; same-wave siblings' `target_paths` are disjoint (module-disjoint by construction)
11. **Schemas pass** — `plan.yaml`, `state.yaml`, and the `context/project/state.yaml` ledger entry all validate (the three `validate.py` commands above exit 0); `state.yaml` has one story entry per `plan.yaml` story, all `pending`
