---
name: mplan
description: Use when you need to generate modular implementation plans from completed specifications in a multi-repo workspace — triggers on phrases like "create plan", "generate plan", "make a plan", "plan the implementation", "break into stories", or when the user wants to turn specs under `context/<repo>/spec/` into executable agent work plans
---

# Create Modular Implementation Plan from Spec

Take completed specifications and produce a set of modular, context-efficient implementation plan files that can be executed by a team of Claude Code agents.

**This is a documentation project. Do not write application code. The deliverables are plan files under `context/project/plans/` and the project ledger `context/project/state.yaml`.**

## The Tool

Every mechanical step of this skill — scope, wave assignment, story ids, and the emission of `plan.yaml`, `state.yaml`, the ledger entry and every story file — is performed by invoking:

```
python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py <group> <verb> [arguments]
```

**A failed invocation is a hard error.** Any `mc.py` call that exits non-zero halts the phase: report its diagnostics and stop. Never re-derive the step by hand — a prose fallback is a second implementation of the step, and two implementations diverge. This rule holds for every invocation in every phase below; it is stated once here and never repeated.

What remains yours is **judgment**: how the work decomposes into stories, which context files each one loads, what its acceptance criteria and validation steps are, and where a consumer must sit relative to its producer.

## Step 0: Understand the Layout & Scope

A workspace splits the **spec per repository** (`context/<repo>/spec/`, plus a contract-only `context/shared/spec/`). Changes are tracked at two levels: **repo-level** files in `context/<repo>/changes/` and a **project-level index** in `context/project/changes/` that references them. Plans live in `context/project/plans/`. A single plan may contain stories for **more than one repo** — each story is tagged with the repo it implements. The `shared` layer is contract-only and is never planned directly; its contracts are implemented by the repos that consume them.

Determine scope:

```
python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py plan scope
```

It returns `type` (`full` or `incremental`), the `plan_id` and `plan_dir` this run writes into, the driving `project_change` (`null` for a full plan), and the repo-level `change_files` that project change references. Take those values as given:

- **Incremental plan** — follow the returned `change_files` to the per-repo change files, which name the affected modules and code paths; plan exactly those.
- **Full plan** — plan the whole workspace by default: every module in every repo's `CATALOG.yaml`. If the user names a subset (e.g. "plan only repo-a", "plan only AUTH and USERS"), scope to that.

**Path convention for the rest of this skill:**
- `context/spec/...` is shorthand for `context/<repo>/spec/...` for the repo a given story belongs to. Shared interface files are referenced by their full path under `context/shared/spec/`.
- `context/project/plans/...` and `context/<repo>/changes/...` are **literal paths** — never mixed up.

## Prerequisites

### Step 1: Determine Plan Type

`plan scope` has already classified the run and named the output directory. What is left is the judgment each type needs:

- **Full plan** (`type: full`) → read every in-scope repo's `context/<repo>/spec/CATALOG.yaml` and all spec files in those catalogs.
  - **Greenfield** (no source code exists yet in `repos/`): generate stories for every module in every in-scope repo.
  - **Update sub-mode** (source code already exists — check each `repos/<repo>/` for `src/`, `lib/`, or equivalent): diff existing code against the spec. Only create stories for gaps or changes; mark already-compliant modules as "Verify". See [Update Sub-Mode](#update-sub-mode) in the Generation Process.
- **Incremental plan** (`type: incremental`) → read the driving `PROJECT-CHANGE-<NNN>-*.md` and then each repo-level change file `scope` returned, for the affected modules and code paths.

**Scope filtering (full plan only):** If the user specifies a subset of repos or module TAGs (e.g., "plan only repo-a", "plan only AUTH and USERS"), generate stories only for those. Still include their lower-layer dependencies, but mark those as "Verify existing" rather than "Implement".

### Step 2: Load Context

**Full plan:** For each in-scope repo, read `context/<repo>/spec/CATALOG.yaml` to get its layer/module structure and `shared_interfaces` list, then read all spec files referenced in that catalog. For every shared interface a repo consumes, also read its `*-INTERFACE.md` (and `*-DATAMODEL.md`) from `context/shared/spec/<IFACE>/` — these are part of the context any module that depends on them needs.

**Incremental plan:** Read the driving `PROJECT-CHANGE-<NNN>-*.md` in full, then for each repo-level change file `scope` named:
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

The plan is also recorded in the **project-level ledger** `context/project/state.yaml` — the entry point that resume/orchestration reads to find the unfinished plan. Conforms to `${CLAUDE_PLUGIN_ROOT}/schemas/project-state.schema.json`.

All three files, and every story file, are written by `mc.py` (see *Emit the Plan Graph, Stories, State & Ledger*) — never assembled by hand.

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
- `{WW}` = zero-padded wave number
- `{SS}` = zero-padded story number within the wave (stories in the same wave get sequential numbers)
- `{REPO}` = the repository directory name this story implements (so two repos may each have a module with the same TAG without colliding)
- `{MODULE}` = module name exactly as it appears in that repo's CATALOG.yaml

### Wave assignment

```
python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py plan waves <target>
```

returns the layer-ordered assignment for one target repo: one wave per layer, in layer order, with that layer's modules as its parallel siblings. Run it once per in-scope repo and merge the results by altitude — same-index layers across repos share a wave, so modules in different repos run in parallel. The `shared` layer is contract-only: it is pure context and gets no stories.

**The judgment that stays yours:** where a story depends, via a shared interface, on another repo's module being implemented first, move the **consumer into a later wave than its producer**, even though the layer assignment placed them in the same one. `plan waves` assigns; it never reorders a consumer past its producer, and that call is `mplan`'s alone. Each story's **Prerequisites** and **Parallel group** then make the resulting order explicit.

## Story File Template

Story files are **rendered, not written**: `mc.py plan story-emit` produces each one from `${CLAUDE_PLUGIN_ROOT}/shared/PLAN-STORY-TEMPLATE.md`, which belongs to `shared/`. This skill never loads that file and nothing is ever copied out of it by hand.

The renderer substitutes everything the plan graph answers — repo, module, layer, wave numbers, prerequisites, parallel group, change file, target-path rows — and gates the conditional sections (`## Change Scope` for incremental plans only, `**Compliance Status:**` for update sub-mode, `## Final Validation` for last-wave stories only). It leaves in place only the placeholders judgment must fill: the Context Files lists, the change-scope narrative, and the module's implementation tasks and acceptance criteria.

**E2E Hard Rules.** `STANDARD-SPEC.md` §"E2E Testing Hard Rules" **owns** the four rules. They are **injected at render time**, generated from that owning section on every emit and never hand-copied, into **both** placements in the story file — `## Post-Story Validation` **and** `## Final Validation (last wave only)` — each block gated on the repo's `CATALOG.yaml` naming a module whose TESTING facet covers E2E, and each annotated with `STANDARD-SPEC.md` as their owner. Both placements are required: the owning rule covers module-level and project-level E2E, while Final Validation reaches last-wave stories only. This is the sanctioned exception to the no-duplication rule, and only because the copy is generated — a story agent's context is its own story, its Context Files, and the repo `CATALOG.yaml`, so the rules must physically reach the story file, yet there is still exactly one authored source.

Each story file must stay **small and focused** — as small as the work allows, with no hard line limit (see *Context Size Budget*).

## Generation Process

### Full Plan (`000-initial`)

1. Read every in-scope repo's `context/<repo>/spec/CATALOG.yaml` to get its exact layer/module structure
2. Read all spec files referenced in those catalogs, plus the shared interface files any of them consume
3. Get each in-scope repo's wave assignment from `plan waves`, merge them by altitude, and apply the consumer-after-producer judgment (see *Wave assignment*)
4. For each module in each in-scope repo's catalog, decide what its story carries:
   - The owning repo and its wave
   - Exact context files derived from each spec file's `depends_on` fields in that repo's CATALOG.yaml
   - Cross-module dependencies listed explicitly (only INTERFACE files from other modules; cross-repo only via `context/shared/spec/`)
   - Acceptance criteria derived from `*-TESTING.md` specs (if the module has a testing facet)
   - Prerequisite stories from earlier waves
   - Implementation tasks adapted to the facets that actually exist for this module
   - The source paths the story writes, and its Post-Story Validation steps (and, for last-wave stories, its Final Validation steps)
5. Emit the graph and render the story files (see *Emit the Plan Graph, Stories, State & Ledger*)
6. Verify: every spec file in every in-scope catalog is referenced in exactly one story; only INTERFACE cross-references

### Update Sub-Mode

Applies when doing a full plan (000-initial) but source code already exists. After steps 1–3 of the full plan above:

4. **Diff each module** against its INTERFACE and DATAMODEL spec. For each module determine:
   - **Implement** — no existing code; generate a standard story
   - **Update** — code exists but has gaps or spec drift; generate a story listing only the specific gaps
   - **Verify** — code appears compliant; generate a minimal story that only runs tests to confirm

5. **Migration stories** — If a spec type, function signature, or API endpoint was renamed or removed compared to what exists in code, add a migration story before the implementation story, named `PLAN-{WW}-{SS}m-{REPO}-{MODULE}-MIGRATION.md`. It must describe the old contract, the new contract, and any data migration steps.

6. **Preserve working code** — Stories for "Update" and "Verify" modules must explicitly state which files to leave untouched. Do not re-implement modules that already match their spec.

7. Set **Compliance Status** in each rendered story's header to match the module's classification.

---

### Incremental Plan (`<NNN>-<slug>`)

1. Read the driving `PROJECT-CHANGE-<NNN>-*.md` that `plan scope` named
2. Read each repo-level change file it references
3. Extract the list of affected modules — and the repo each belongs to — from each repo change file's **Spec Files Modified** and **Affected Code Paths** tables
4. Read each affected module's owning repo `context/<repo>/spec/CATALOG.yaml`, and get its wave assignment from `plan waves`
5. Read only the spec files for affected modules (plus their one-level `depends_on` deps, including any shared `*-INTERFACE.md`)
6. For each affected module, decide what its story carries:
   - A **Change Scope** narrative naming exactly which source files to touch and what to change (from the repo-level change file's **Affected Code Paths** table — only the rows for this story's repo)
   - The path to the repo-level change file (e.g. `context/repo-a/changes/CHANGE-003-retry-logic.md`) so the agent can read it
   - Context files (same derivation as full plan, but scoped to affected modules)
   - Implementation tasks limited to the facets mentioned in the change file
   - Acceptance criteria from the repo change file's **Validation Checklist** plus relevant TESTING spec criteria
   - The source paths the story writes, and its Post-Story Validation steps (and, for last-wave stories, its Final Validation steps, referencing the repo change file's **Validation Checklist**)
7. Emit the graph and render the story files (see *Emit the Plan Graph, Stories, State & Ledger*)
8. Verify: every entry in every referenced repo change file's **Affected Code Paths** is covered by a story; no story touches modules not listed in those change files

## Emit the Plan Graph, Stories, State & Ledger

The graph is emitted **first**: `plan story-emit` reads `plan.yaml` for each story's repo, module, wave, prerequisites, parallel group, change file, and target paths.

### 1. `plan.yaml`, `state.yaml`, and the ledger entry

```
python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py plan emit <plan-id> < draft.json
```

One invocation writes all three artifacts together — `<plan-dir>/plan.yaml`, the initial `<plan-dir>/state.yaml`, and the `context/project/state.yaml` ledger entry (`version: 1`, `status: pending`, other plans' entries never overwritten) — and **validates each against its schema before persisting it**, so an invalid graph or state cannot reach disk. A refusal persists nothing at all: fix the draft and re-run.

The draft graph, supplied as JSON on stdin, carries only what judgment produced. Everything derived — `waves[]`, each story's `parallel_group` and `file`, the `repos` list, the whole of `state.yaml`, and the ledger entry — belongs to the tool:

```json
{
  "project_change": "003",
  "stories": {
    "01-01-repo-a-AUTH": {
      "repo": "repo-a",
      "module": "AUTH",
      "wave": 1,
      "prerequisites": [],
      "change_file": "context/repo-a/changes/CHANGE-003-retry-logic.md",
      "target_paths": ["src/auth/retry.ts"],
      "validation": {
        "post_story": [
          {"kind": "exit-code", "command": "npm test -- auth", "description": "Unit tests for the AUTH module pass."}
        ]
      }
    },
    "02-01-repo-a-USERS": {
      "repo": "repo-a",
      "module": "USERS",
      "wave": 2,
      "prerequisites": ["01-01-repo-a-AUTH"],
      "change_file": "context/repo-a/changes/CHANGE-003-retry-logic.md",
      "target_paths": ["src/users/service.ts"],
      "validation": {
        "post_story": [
          {"kind": "exit-code", "command": "npm test -- users", "description": "Unit tests for the USERS module pass."}
        ],
        "final": [
          {"kind": "prose", "description": "All acceptance criteria in every story file are checked off."}
        ]
      }
    }
  }
}
```

What the draft must get right:

- **Story keys** come from `python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py plan story-id <file>` — pass the story's intended `PLAN-…-….md` filename and use the id it returns as the key here and in every `prerequisites` reference. Do not compose the key yourself.
- `project_change` is the value `plan scope` returned — `null` for a full or greenfield plan; `change_file` is `null` there too.
- `target_paths` and `validation.post_story` are **mandatory** on every story; `validation.final` appears only on last-wave stories. A Verify story that writes no source files emits `target_paths: []`.
- **Lift validation into the graph.** Translate each story's Post-Story / Final Validation into `validation.post_story[]` / `validation.final[]` steps. Prefer **`kind: exit-code`** with a runnable `command` (build/type-check/test) so `mexecute` can gate mechanically; use `kind: prose` only where a step is genuinely agent-interpreted.
- All three schemas are `additionalProperties: false` at **every** level. A story entry may carry no field beyond the ones `plan-graph.schema.json` defines, so anything else an agent needs to know belongs in the story file, never in the graph.

### 2. Story files

One invocation per story, once the graph is on disk:

```
python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py plan story-emit <plan-id> <story-id>
```

It writes `<plan-dir>/PLAN-{WW}-{SS}-{REPO}-{MODULE}.md` from the shared template, gates the conditional sections from the graph, and injects `STANDARD-SPEC.md`'s four E2E Hard Rules — generated at render time from that owning section, annotated with it as owner — into both `## Post-Story Validation` and `## Final Validation (last wave only)`.

Then edit each rendered file to fill in only what the graph could not answer: the four **Context Files** sub-lists, the **Change Scope** narrative (incremental plans), the **Implementation Tasks** and **Acceptance Criteria** for this module, and — update sub-mode only — the `**Compliance Status:**` header field. Leave everything the renderer produced as it stands; in particular never edit, reformat, or re-copy the injected E2E rules.

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

Have each validator return a small structured verdict (`story`, `ok: true|false`, `issues: []`). **Fix every flagged story.** If a fix changes structure, correct the draft and re-run `plan emit` (which rewrites the graph, `state.yaml`, and the ledger entry, re-validating each), then re-render every affected story with `plan story-emit` and fill its judgment sections in again. Only proceed to Final Validation once the fan-out is clean.

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
10. **Single-repo stories** — every `plan.yaml` story names exactly one `repo`; same-wave siblings' `target_paths` are disjoint (module-disjoint by construction), tested pairwise on non-empty lists. A story with `target_paths: []` — a Verify story that writes no source files — is an explicit **Verify-story exemption** from this disjointness test, not a trivial pass via an empty intersection.
11. **Schemas pass** — re-check the three emitted artifacts explicitly, since step 10's fixes may have touched them:

    ```
    python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py validate plan-graph    context/project/plans/<plan-id>/plan.yaml
    python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py validate plan-state    context/project/plans/<plan-id>/state.yaml
    python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py validate project-state context/project/state.yaml
    ```

    All three must exit 0, and `state.yaml` must have one story entry per `plan.yaml` story, all `pending`.
