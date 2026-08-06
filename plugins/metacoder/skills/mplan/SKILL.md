---
name: mplan
description: Use when you need to generate modular implementation plans from specifications in a multi-repo workspace — triggers on phrases like "create plan", "generate plan", "make a plan", "plan the implementation", "break into stories", or when the user wants to turn specs under `context/<repo>/spec/` into executable agent work plans
---

# Create Modular Implementation Plan from Spec

Take a specification and produce modular, context-efficient implementation plan files a team of Claude Code agents can execute, cut into the slices delivery advances along.

**This is a documentation project. Do not write application code. The deliverables are plan files under `context/project/plans/` and the project ledger `context/project/state.yaml`.**

## The Tool

Every mechanical step of this skill — scope, wave assignment, story ids, and the emission of `plan.yaml`, `state.yaml`, the ledger entry and every story file — is performed by invoking:

```
python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py <group> <verb> [arguments]
```

**A failed invocation is a hard error.** Any `mc.py` call that exits non-zero halts the phase: report its diagnostics and stop. Never re-derive the step by hand. This rule holds for every invocation below; stated once here, not repeated.

What remains yours is **judgment**: how the work decomposes into stories, which context files each one loads, what its acceptance criteria and validation steps are, and where a consumer must sit relative to its producer.

## Step 0: Understand the Layout & Scope

A workspace splits the **spec per repository** (`context/<repo>/spec/`, plus a contract-only `context/shared/spec/`). Changes are tracked at two levels: **repo-level** files in `context/<repo>/changes/` and a **project-level index** in `context/project/changes/` that references them. Plans live in `context/project/plans/`. A single plan may contain stories for **more than one repo**, each tagged with the repo it implements. The `shared` layer is contract-only and never planned directly; its contracts are implemented by the repos that consume them.

Determine scope:

```
python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py plan scope
```

It returns `type` (`full` or `incremental`), the `plan_id` and `plan_dir` this run writes into, the driving `project_change` (`null` for a full plan), and the repo-level `change_files` that project change references. Take those values as given:

- **Incremental plan** — follow the returned `change_files` to the per-repo change files, which name the affected modules and code paths; plan exactly those.
- **Full plan** — plan the whole workspace by default: every module in every repo's `CATALOG.yaml`. If the user names a subset (e.g. "plan only repo-a", "plan only AUTH and USERS"), scope to that.

**A different entry point:** `/mplan --slice <NN>` does not generate a plan at all — it refreshes one slice's stories against a graph that already exists. If the invocation names a slice, skip to [Slice-Scoped Re-Plan](#slice-scoped-re-plan---slice) and do only what it says.

**Path convention for the rest of this skill:**
- `context/spec/...` is shorthand for `context/<repo>/spec/...` for the repo a given story belongs to. Shared interface files are referenced by their full path under `context/shared/spec/`.
- `context/project/plans/...` and `context/<repo>/changes/...` are **literal paths** — never mixed up.

## Prerequisites

### Precondition: contract depth, not completeness

Every module this plan touches must be at **at least `contract` depth** — OVERVIEW, DATAMODEL and INTERFACE written, which is enough to build other modules against it. Check it, per module, with the tool:

```
python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py spec depth <target> <module>
```

A module below `contract` depth **halts the plan**: report it and tell the user to run `/mspec <target>` to bring it up to contract depth. Do not plan around a module whose interface nobody has written — the stories would be guesses.

A module need **not** be `full`, and this skill never asks whether a spec is finished. DEPENDENCIES, IMPLEMENTATION and TESTING are authored per slice by `mspec`'s deepen entry, which `mship` invokes immediately before that slice runs, so internal detail is never written for work an earlier slice invalidated. Waiting for the whole spec before planning is exactly the batching slices exist to remove: require the contract, plan against it, and let the depth arrive slice by slice.

### Step 1: Determine Plan Type

`plan scope` has already classified the run and named the output directory. What is left is the judgment each type needs:

- **Full plan** (`type: full`) → read every in-scope repo's `context/<repo>/spec/CATALOG.yaml` and all spec files in those catalogs.
  - **Greenfield** (no source code exists yet in `repos/`): generate stories for every module in every in-scope repo.
  - **Update sub-mode** (source code already exists — check each `repos/<repo>/` for `src/`, `lib/`, or equivalent): diff existing code against the spec. Only create stories for gaps or changes; mark already-compliant modules as "Verify". See [Update Sub-Mode](#update-sub-mode) in the Generation Process.
- **Incremental plan** (`type: incremental`) → read the driving `PROJECT-CHANGE-<NNN>-*.md` and then each repo-level change file `scope` returned, for the affected modules and code paths.

**Scope filtering (full plan only):** If the user specifies a subset of repos or module TAGs (e.g., "plan only repo-a", "plan only AUTH and USERS"), generate stories only for those. Still include their lower-layer dependencies, but mark those as "Verify existing" rather than "Implement".

### Step 2: Load Context

**Full plan:** For each in-scope repo, read `context/<repo>/spec/CATALOG.yaml` to get its layer/module structure and `shared_interfaces` list, then read all spec files referenced in that catalog. For every shared interface a repo consumes, also read its `*-INTERFACE.md` (and `*-DATAMODEL.md`) from `context/shared/spec/<IFACE>/` — this is context for any module that depends on them.

**Incremental plan:** Read the driving `PROJECT-CHANGE-<NNN>-*.md` in full, then for each repo-level change file `scope` named:
1. Extract each spec file listed in **Spec Files Modified** (noting which repo each belongs to)
2. Read their `depends_on` entries from the owning repo's `CATALOG.yaml` (one level deep) — including any `context/shared/spec/<IFACE>/<IFACE>-INTERFACE.md` paths
3. Read each owning repo's `CATALOG.yaml` (for wave/layer assignments)

**Note on shared-interface cascades:** if the project-level index carries `<!-- scope: shared -->`, it changed a cross-repo contract. Plan stories for each consuming repo's affected modules, and load the changed `context/shared/spec/<IFACE>/` files as context for those stories.

## Slices: Consumed, Never Derived

A **slice** is the unit of delivery: the set of stories that together make one behaviour work end to end. A slice cuts *vertically* through the layers rather than along one of them, so completing one yields something that runs. Slices are the axis delivery advances along — `mship` ships one at a time — and every plan this skill emits carries them.

**The cut is not yours to make.** The driving `PROJECT-CHANGE-<NNN>-*.md` carries a `## Slices` table, and this skill consumes it **verbatim**: each row's `Slice` id, `Name`, `Behavior`, `Acceptance` and `Modules` are taken exactly as written, in the order written. Assign each story to the slice whose `Modules` column names its module. Do not recut, reorder, renumber, merge or split what the table says, and do not invent a cut of your own where it already gave one.

Deriving the cut here was rejected deliberately. Cutting a change into deliverable increments is a design judgement, and `mspec` makes it behind an approval gate. This skill has **no** interactive gate, so a slicing invented here would reach execution without anyone having seen it — and a wrong cut is the single most expensive planning error there is, because every slice after it is paid for before the mistake shows.

**Two fallbacks, both to lifecycles.** Fall back to the primary lifecycles named in the target's `context/<repo>/spec/COMMON/COMMON-OVERVIEW.md` — one slice per lifecycle, in the order that overview lists them — when either holds:

- the driving change document has **no `## Slices` section**. It was written before the section existed; its absence is not a defect and is never reported as one.
- there is **no driving change document at all** — a **full or greenfield plan**, scoped from the whole spec rather than from a change. There is nothing to consume, so the same fallback applies.

Those lifecycles are themselves authored behind `mspec`'s gate, which is why falling back to them is still consuming a reviewed cut rather than inventing one.

**Two rules bind the cut**, whichever of the three sources it came from:

1. **Slice `00` is a walking skeleton** — it touches every layer the plan touches. Its acceptance proves the shape of the whole system runs before any depth is built on it. A first slice confined to one layer is the old bottom-up order wearing slice vocabulary.
2. **Every slice carries at least one runnable acceptance** — one `kind: exit-code` step. A slice whose acceptance is prose alone can only be settled by a person, and a plan made entirely of those turns delivery back into supervision.

`plan emit` refuses a draft that breaks either, so both are enforced mechanically rather than by a careful reading.

**Membership and ordering.** Every story belongs to **exactly one** slice — none to zero, none to two — and a story's **prerequisites never cross into a later slice**. A slice whose work depends on stories scheduled after it is not deliverable on its own, which is the entire property slicing buys.

## Role

You are a technical project manager creating implementation plans for Claude Code agent teams.

## Output File Structure

Each plan directory contains one **story file per module** (human-readable, self-contained) plus two machine-readable files that make the plan executable and resumable by `mexecute`:

- **`plan.yaml`** — the immutable **plan graph**: slices and their acceptance, waves, stories, dependencies, and per-story validation. `mexecute` reads this instead of re-deriving structure from story prose. Conforms to `${CLAUDE_PLUGIN_ROOT}/schemas/plan-graph.schema.json`.
- **`state.yaml`** — the initial **plan-level state**: every story `pending`, no attempts yet. `mexecute` mutates this as it runs. Conforms to `${CLAUDE_PLUGIN_ROOT}/schemas/plan-state.schema.json`.

The plan is also recorded in the **project-level ledger** `context/project/state.yaml`, the entry point resume/orchestration reads to find the unfinished plan. Conforms to `${CLAUDE_PLUGIN_ROOT}/schemas/project-state.schema.json`.

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

**Waves order stories *within* a slice.** They are no longer the axis delivery advances along — slices are — and wave assignment is applied per slice: the stories of one slice are ordered by layer among themselves, then the waves of the next slice follow, so wave numbers keep rising across the plan and **no wave ever mixes stories from two slices**. What a wave has always meant — a set of stories that can run concurrently once the layer beneath them is in — is unchanged; only its scope of application is.

```
python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py plan waves <target>
```

is likewise unchanged: it returns the layer-ordered assignment for one target repo — one wave per layer, in layer order, with that layer's modules as its parallel siblings. Run it once per in-scope repo and merge the results by altitude — same-index layers across repos share a wave, so modules in different repos run in parallel. The `shared` layer is contract-only: it is pure context and gets no stories.

**The judgment that stays yours:** where a story depends, via a shared interface, on another repo's module being implemented first, move the **consumer into a later wave than its producer**, even though the layer assignment placed them in the same one. `plan waves` assigns; it never reorders a consumer past its producer. Each story's **Prerequisites** and **Parallel group** then make the resulting order explicit.

**Prerequisites point backwards on both axes.** A prerequisite never names a later wave, and never names a story in a later **slice** (see *Slices*).

## Story File Template

Story files are **rendered, not written**: `mc.py plan story-emit` produces each one from `${CLAUDE_PLUGIN_ROOT}/shared/PLAN-STORY-TEMPLATE.md`. This skill never loads that file, and nothing is ever copied out of it by hand.

The renderer substitutes everything the plan graph answers — repo, module, layer, wave numbers, prerequisites, parallel group, change file, target-path rows — and gates the conditional sections (`## Change Scope` for incremental plans only, `**Compliance Status:**` for update sub-mode, `## Final Validation` for last-wave stories only). It leaves in place only the placeholders judgment must fill: the Context Files lists, the change-scope narrative, and the module's implementation tasks and acceptance criteria.

**E2E Hard Rules.** `STANDARD-SPEC.md` §"E2E Testing Hard Rules" **owns** the four rules. They are **injected at render time**, generated from that owning section on every emit and never hand-copied, into **both** placements in the story file — `## Post-Story Validation` **and** `## Final Validation (last wave only)` — each block gated on the repo's `CATALOG.yaml` naming a module whose TESTING facet covers E2E, and each annotated with `STANDARD-SPEC.md` as their owner. Both placements are required: the owning rule covers module-level and project-level E2E, while Final Validation reaches last-wave stories only. This is the sanctioned exception to the no-duplication rule, and only because the copy is generated: a story agent's context is its own story, its Context Files, and the repo `CATALOG.yaml`, so the rules must physically reach the story file even though there is exactly one authored source.

Each story file must stay **small and focused** — as small as the work allows, with no hard line limit (see *Context Size Budget*).

## Generation Process

### Full Plan (`000-initial`)

1. Read every in-scope repo's `context/<repo>/spec/CATALOG.yaml` to get its exact layer/module structure
2. Read all spec files referenced in those catalogs, plus the shared interface files any of them consume
3. Establish the slices. A full plan has no driving change document, so fall back to the lifecycles in `COMMON-OVERVIEW.md` — one slice per lifecycle, in the order listed (see *Slices*)
4. Get each in-scope repo's wave assignment from `plan waves`, merge them by altitude, and apply the consumer-after-producer judgment (see *Wave assignment*) — ordering the stories **within** each slice
5. For each module in each in-scope repo's catalog, decide what its story carries:
   - The owning repo, the slice it belongs to, and its wave within that slice
   - Exact context files derived from each spec file's `depends_on` fields in that repo's CATALOG.yaml
   - Cross-module dependencies listed explicitly (only INTERFACE files from other modules; cross-repo only via `context/shared/spec/`)
   - Acceptance criteria derived from `*-TESTING.md` specs (if the module has a testing facet)
   - Prerequisite stories from earlier waves — and never from a later slice
   - Implementation tasks adapted to the facets that actually exist for this module
   - The source paths the story writes, and its Post-Story Validation steps (and, for last-wave stories, its Final Validation steps)
6. Emit the graph and render the story files (see *Emit the Plan Graph, Stories, State & Ledger*)
7. Verify: every spec file in every in-scope catalog is referenced in exactly one story; every story belongs to exactly one slice; only INTERFACE cross-references

### Update Sub-Mode

Applies when doing a full plan (000-initial) but source code already exists. After steps 1–4 of the full plan above:

5. **Diff each module** against its INTERFACE and DATAMODEL spec. For each module determine:
   - **Implement** — no existing code; generate a standard story
   - **Update** — code exists but has gaps or spec drift; generate a story listing only the specific gaps
   - **Verify** — code appears compliant; generate a minimal story that only runs tests to confirm

6. **Migration stories** — If a spec type, function signature, or API endpoint was renamed or removed compared to what exists in code, add a migration story before the implementation story, named `PLAN-{WW}-{SS}m-{REPO}-{MODULE}-MIGRATION.md`. It must describe the old contract, the new contract, and any data migration steps. A migration story belongs to the **same slice** as the implementation story it precedes — a slice that ships a contract without its migration is not deliverable on its own.

7. **Preserve working code** — Stories for "Update" and "Verify" modules must explicitly state which files to leave untouched. Do not re-implement modules that already match their spec.

8. Set **Compliance Status** in each rendered story's header to match the module's classification.

---

### Incremental Plan (`<NNN>-<slug>`)

1. Read the driving `PROJECT-CHANGE-<NNN>-*.md` that `plan scope` named
2. Read each repo-level change file it references
3. Extract the list of affected modules — and the repo each belongs to — from each repo change file's **Spec Files Modified** and **Affected Code Paths** tables
4. Take the index's `## Slices` table **verbatim** as the cut, in the order it lists (see *Slices*). An index written before the section existed has none: fall back to the lifecycles in `COMMON-OVERVIEW.md`
5. Read each affected module's owning repo `context/<repo>/spec/CATALOG.yaml`, and get its wave assignment from `plan waves` — ordering the stories **within** each slice
6. Read only the spec files for affected modules (plus their one-level `depends_on` deps, including any shared `*-INTERFACE.md`)
7. For each affected module, decide what its story carries:
   - The slice it belongs to — the row whose **Modules** column names it — and its wave within that slice
   - A **Change Scope** narrative naming exactly which source files to touch and what to change (from the repo-level change file's **Affected Code Paths** table — only the rows for this story's repo)
   - The path to the repo-level change file (e.g. `context/repo-a/changes/CHANGE-003-retry-logic.md`) so the agent can read it
   - Context files (same derivation as full plan, but scoped to affected modules)
   - Implementation tasks limited to the facets mentioned in the change file
   - Acceptance criteria from the repo change file's **Validation Checklist** plus relevant TESTING spec criteria
   - The source paths the story writes, and its Post-Story Validation steps (and, for last-wave stories, its Final Validation steps, referencing the repo change file's **Validation Checklist**)
8. Emit the graph and render the story files (see *Emit the Plan Graph, Stories, State & Ledger*)
9. Verify: every entry in every referenced repo change file's **Affected Code Paths** is covered by a story; no story touches modules not listed in those change files; every module the `## Slices` table names has a story, and every story sits in exactly one slice

## Emit the Plan Graph, Stories, State & Ledger

The graph is emitted **first**: `plan story-emit` reads `plan.yaml` for each story's repo, module, slice, wave, prerequisites, parallel group, change file, and target paths.

### 1. `plan.yaml`, `state.yaml`, and the ledger entry

```
python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py plan emit <plan-id> < draft.json
```

One invocation writes all three artifacts together — `<plan-dir>/plan.yaml`, the initial `<plan-dir>/state.yaml`, and the `context/project/state.yaml` ledger entry (`version: 1`, `status: pending`, other plans' entries never overwritten) — and **validates each against its schema before persisting it**, so an invalid graph or state cannot reach disk. A refusal persists nothing at all: fix the draft and re-run.

The draft graph, supplied as JSON on stdin, carries only what judgment produced. Everything derived — `version: 3`, `run: 0`, `status: pending`, `waves[]`, each story's `slice` back-reference, `parallel_group` and `file`, the `repos` list, the whole of `state.yaml`, and the ledger entry — belongs to the tool:

```json
{
  "project_change": "003",
  "slices": [
    {
      "slice": "00",
      "name": "retry end to end",
      "behavior": "An adapter call that fails once succeeds on retry, and the consumer sees the retried result",
      "acceptance": [
        {"kind": "exit-code", "command": "pytest tests/e2e/test_retry.py -q", "description": "A failing call succeeds on retry, end to end."}
      ],
      "stories": ["01-01-repo-a-AUTH", "02-01-repo-a-USERS"]
    }
  ],
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

- **`slices[]` carries the cut**, in delivery order, each entry taken from the `## Slices` table (or the lifecycle fallback) with its `slice` id, `name`, `behavior`, `acceptance` steps and the `stories` it delivers. Every story id appears in **exactly one** slice's `stories` list; the tool writes each story's own `slice` field from that listing, so a story entry never sets `slice` itself. A draft carrying `slices` is emitted as a **version-3** graph — `version: 3` is supplied by the tool, not by the draft — and every plan generated now carries the cut. (A version-1/2 graph is a plan written before slices existed; it is read as one implicit slice spanning every wave, and nothing needs migrating.)
- **`plan emit` refuses**, and persists nothing, when slice `00` does not touch every layer the plan touches, when any slice has no `kind: exit-code` acceptance step, or when a story belongs to no slice or to two. These are mechanical consequences of the draft, so they are the tool's to enforce rather than yours to eyeball — but a refusal means the cut or the story-to-slice assignment is wrong, and the fix is in the draft, never in the tool.
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

It writes `<plan-dir>/PLAN-{WW}-{SS}-{REPO}-{MODULE}.md` from the shared template, gates the conditional sections from the graph, and injects the E2E Hard Rules into both `## Post-Story Validation` and `## Final Validation (last wave only)` (see *E2E Hard Rules* above).

Then edit each rendered file to fill in only what the graph could not answer: the four **Context Files** sub-lists, the **Change Scope** narrative (incremental plans), the **Implementation Tasks** and **Acceptance Criteria** for this module, and — update sub-mode only — the `**Compliance Status:**` header field. Leave everything the renderer produced as it stands — never edit, reformat, or re-copy the injected E2E rules.

## Slice-Scoped Re-Plan (`--slice`)

`/mplan --slice <NN>` refreshes the stories of **one** slice against a plan graph that already exists. It is the entry `mship` calls at step 2 of each loop iteration, once `mspec` has deepened that slice's modules from `contract` to `full`: the stories are then written against detail that did not exist when the graph was first emitted.

Do this, and only this:

1. Resolve the plan and read the slice:

   ```
   python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py plan resolve [<plan-id>]
   python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py plan slices <plan-id>
   ```

   `resolve` names the plan, its run, and the wave and slice to resume at; `slices` returns the plan's slices in order, each with its acceptance, its stories and its current status.
2. Load the now-`full` spec files for that slice's modules only — nothing from another slice.
3. Re-render **that slice's** stories with `plan story-emit`, one invocation per story, then fill their judgment sections in again (Context Files, Change Scope narrative, Implementation Tasks, Acceptance Criteria).

**Do not re-emit the graph.** `plan emit` rewrites `plan.yaml`, `state.yaml` and the ledger entry wholesale, which would disturb slices this run has no business touching — including ones already delivered.

**This is not reslicing.** Refreshing one slice's *stories* is this operation. Rewriting the slice **set** — recutting, reordering, splitting or dropping the slices still outstanding — is `mc.py plan reslice`, a different operation that `mship` owns and invokes, and that refuses any draft altering a slice already `applied`. Never call `plan reslice` from here: two skills with a claim on the same write is how a delivered slice gets rewritten by accident.

## Context Size Budget

Keep each story file **small and focused** — as small as the work allows, with no hard line limit. If a story is growing too large to stay self-contained:
- Split into sub-stories with focused scope (e.g., `PLAN-02-01a-FOO-TYPES.md`, `PLAN-02-01b-FOO-CLIENT.md`)
- Move detailed implementation notes to the spec files (they belong there anyway)
- Keep the story file focused on: context files, task order, and acceptance criteria

## Post-Generation Story Validation (subagents)

After the stories and machine-readable artifacts are written, **fan out validators**: one subagent **per story** (batch small plans, one per wave, to keep the fan-out reasonable), dispatched in parallel (single message, multiple Agent tool calls). Each validator is scoped to one story and its declared context, blind to the others, and checks:

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
8. **Slice acceptance is per slice** — every slice carries its own end-to-end acceptance, and the Final Validation section lands on the last wave *of that slice*, not once per plan. Delivery advances a slice at a time, so a plan with a single terminal validation proves nothing until the last one lands
9. **Graph agrees with the story files** — every `PLAN-*.md` has exactly one `plan.yaml` story entry and vice versa; each story's `wave`/`prerequisites`/`parallel_group` in `plan.yaml` matches its story-file header; every id in `waves[]`, `prerequisites`, and `parallel_group` resolves to a real story; no prerequisite points to a later wave
10. **Single-repo stories** — every `plan.yaml` story names exactly one `repo`; same-wave siblings' `target_paths` are disjoint (module-disjoint by construction), tested pairwise on non-empty lists. A story with `target_paths: []` — a Verify story that writes no source files — is an explicit **Verify-story exemption** from this disjointness test, not a trivial pass via an empty intersection.
11. **Schemas pass** — re-check the three emitted artifacts explicitly, since step 10's fixes may have touched them:

    ```
    python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py validate plan-graph    context/project/plans/<plan-id>/plan.yaml
    python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py validate plan-state    context/project/plans/<plan-id>/state.yaml
    python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py validate project-state context/project/state.yaml
    ```

    All three must exit 0, and `state.yaml` must have one story entry per `plan.yaml` story, all `pending`.

12. **Slice `00` is a walking skeleton** — it touches every layer the plan touches. A first slice confined to one layer is the old bottom-up order wearing slice vocabulary
13. **Every slice has a runnable acceptance** — at least one `kind: exit-code` step. Prose-only acceptance forces a human stop under every gate policy but `never`, so a plan of them is a plan that cannot run unattended
14. **Every story belongs to exactly one slice**, and no prerequisite crosses into a later slice

Checks 12–14 are enforced mechanically by `plan emit`, which refuses a draft that breaks any of them: they are stated here so the plan can be read against them, not performed here. A refusal is where they actually bite.

The former single-wave Final Validation check is retired. End-to-end validation is now per slice, so exactly one wave *per slice* carries it — which check 13 subsumes.
