# Spec Change Standards

Standards for documenting changes to spec files in a multi-repo workspace so that an agentic coder can plan and execute corresponding code updates. Referenced by prompt files — this is the single source of truth for change tracking.

---

## Purpose

When spec files change, code must follow. A **change document** bridges the gap: it tells an implementing agent *exactly what changed* in the spec, *why*, and *what code is affected* — without forcing the agent to diff hundreds of lines of markdown.

---

## Change Document Location & Naming

Change tracking uses a **two-tier structure** that separates per-repo detail from cross-repo coordination.

### Repo-level change documents

Each repository's spec changes are documented in that repo's own change directory:

```
context/<repo>/changes/CHANGE-<NNN>-<SHORT-SLUG>.md
```

- `<NNN>` — Zero-padded sequence number (001, 002, …). Monotonically increasing **per repo** (each repo has its own independent sequence).
- `<SHORT-SLUG>` — 2–4 word kebab-case summary (e.g., `adapter-retry-logic`, `cli-run-command`, `verifier-output-format`).

Example: `context/repo-a/changes/CHANGE-003-adapter-retry-logic.md`

A repo-level change document contains the full detail: affected spec files, breaking changes, affected code paths, implementation order, and validation checklist.

### Project-level change index

Every `mspec` run also produces or updates a **project-level change index** in:

```
context/project/changes/PROJECT-CHANGE-<NNN>-<SHORT-SLUG>.md
```

- `<NNN>` — Zero-padded sequence number (001, 002, …). Monotonically increasing across the **whole workspace** (one shared sequence regardless of which repos changed).
- `<SHORT-SLUG>` — 2–4 word kebab-case summary of the overall change.

Example: `context/project/changes/PROJECT-CHANGE-005-retry-policy.md`

The project-level index references the repo-level change files produced in that mspec run. It is the entry point `mplan` reads to scope an incremental plan — `mplan` follows the repo-file references to get per-repo detail.

**Key rule:** `mplan` reads `context/project/changes/` to determine what to plan. Repo-level change files in `context/<repo>/changes/` contain the implementation detail agents need.

### Sequence resolution and create-vs-continue

A run either **continues** an existing change file or **creates** a new one at the next free number. Both the decision and the allocation are performed by the tool and must not be re-derived by the reader:

- `mc.py change resolve <repo> [--slug <slug>]` — repo-level, for `context/<repo>/changes/`.
- `mc.py change index-resolve [--slug <slug>]` — project-level, for `context/project/changes/`.

The rule they apply, keyed on `status` first:

- A change file is **continuable** only when its `status` is `pending` or `in-progress` **and** no plan directory in `context/project/plans/` corresponds to it. Both conditions must hold.
- Every other status is **terminal**: `applied`, `superseded`, and `complete` are never re-opened, and neither is any initial-spec baseline record. A terminal file is left untouched and a new file is created instead.
- Plan existence alone never decides this. A terminal file with no plan is still terminal.

Allocation takes the next number **above the highest `<NNN>` already present** in that sequence — repo-level per repo, project-level workspace-global — never a count of files and never a gap-filling reuse.

A failed invocation is a hard error that halts the phase. There is no prose fallback, because a fallback is a second implementation of the same step.

---

## Initial-Spec Baseline Records

The first change document written when a spec is first created is a **baseline record**, not a normal change. It documents the starting state of a spec rather than a modification to one, so it uses a distinct naming, status, and layout.

Baseline records live at the **repo level** (in `context/<repo>/changes/`) only — they are not represented in the project-level change index because they have no code to implement.

- **Naming:** `context/<repo>/changes/CHANGE-000-initial-spec.md` for the first spec in a fresh repo, or `CHANGE-<NNN>-initial-spec.md` for repos added later (using the next free per-repo `<NNN>`).
- **Status:** `complete` — a terminal status unique to baseline records (see Status Lifecycle). A baseline record is a historical snapshot and is **never** edited by UPDATE-mode runs.
- **Layout:** baseline records do **not** follow the full change schema below. They carry only a `## Summary`, a `## Modules` list (each module with its layer and a one-line description), and a `## Spec Files` list enumerating the files written. They have no Breaking Changes / Affected Code Paths / Implementation Order sections, because no code follows from them.

Front-matter for a baseline record:

```markdown
<!-- change: <NNN> -->
<!-- scope: repo | shared -->
<!-- repo: <target repo> -->
<!-- status: complete -->
<!-- date: YYYY-MM-DD -->
```

Tooling that scans `context/<repo>/changes/` treats `CHANGE-*-initial-spec.md` records as baseline state and skips them when looking for the latest actionable change. `mplan` reads `context/project/changes/` and only sees project-level indexes, so baseline records never interfere with plan scoping.

---

## Shared-Interface Change Cascade

A change to a shared interface in `context/shared/spec/` ripples into every repo that consumes that interface. The cascade is recorded with **one change file per affected repo** plus one project-level index that ties them together:

1. **Find the consumers.** Scan every `context/*/spec/CATALOG.yaml` and collect the repos whose `shared_interfaces` field lists the changed interface TAG.

2. **Write a repo-level change for the shared layer.** Create `context/shared/changes/CHANGE-<NNN>-<slug>.md` listing the `*-INTERFACE.md`/`*-DATAMODEL.md` edits.

3. **Write a repo-level change for each consuming repo.** For each consuming repo, run the UPDATE-mode spec edits and write `context/<repo>/changes/CHANGE-<NNN>-<slug>.md` covering only that repo's spec updates. If a listed consumer turns out to be genuinely unaffected, write a single-sentence note file rather than omitting it — the project-level index must account for every consumer.

4. **Write one project-level index.** Create `context/project/changes/PROJECT-CHANGE-<NNN>-<slug>.md` that:
   - Lists the shared interface(s) changed
   - References every repo-level change file produced (shared + each consumer)
   - Records `consumers:` so mplan knows which repos to plan across

Front-matter for a shared-interface cascade project-level index:

```markdown
<!-- project-change: <NNN> -->
<!-- scope: shared -->
<!-- repos: shared, repo-a, repo-b -->
<!-- status: pending -->
<!-- date: YYYY-MM-DD -->
<!-- consumers: repo-a, repo-b -->
```

A shared-interface cascade is not complete until every consuming repo has either a change file or a documented "unaffected" note, and the project-level index references all of them.

---

## Change Document Schemas

### Repo-level change document

Every repo-level change document in `context/<repo>/changes/` must follow this structure:

```markdown
<!-- change: <NNN> -->
<!-- scope: repo | shared -->
<!-- repo: <repo> -->                  # the repo this change file belongs to
<!-- status: pending | in-progress | applied | superseded -->
<!-- date: YYYY-MM-DD -->

# CHANGE-<NNN>: <Title>

## Summary

One to three sentences describing the change at a high level.
What was the motivation? What user-visible or system-visible effect does it have?

## Spec Files Modified

List every spec file that was added, modified, or removed. For each file, state
the nature of the change concisely.

| File | Action | What Changed |
|------|--------|--------------|
| `context/repo-a/spec/ADAPTERS/ADAPTERS-INTERFACE.md` | modified | Added `retryPolicy` parameter to `spawnAgent()` signature |
| `context/repo-a/spec/ADAPTERS/ADAPTERS-DATAMODEL.md` | modified | Added `RetryPolicy` type definition |
| `context/repo-a/spec/AGENT-EXECUTOR/AGENT-EXECUTOR-IMPLEMENTATION.md` | modified | Updated execution flow to respect retry policy from adapter |

## Breaking Changes

List any changes that break existing contracts. If none, write "None."

- [ ] Signature changes to exported functions in INTERFACE files
- [ ] Removed or renamed types in DATAMODEL files
- [ ] Changed signal/output formats in IMPLEMENTATION files
- [ ] Modified checkpoint protocols

## Detailed Changes

One H3 subsection per logical change. Each subsection must include:

### <Change Title>

**Spec reference:** `context/<repo>/spec/<TAG>/<TAG>-<FACET>.md` (or `context/shared/spec/<IFACE>/<IFACE>-<FACET>.md` for shared changes) — section name or line range

**Before:** Brief description or code block of the previous state. Use "N/A" for additions.

**After:** Brief description or code block of the new state. Use "N/A" for removals.

**Rationale:** Why this change was made.

## Affected Code Paths

Map each spec change to the source files and functions that must be updated.
This is the primary section the implementing agent uses to plan work.

Source paths are relative to `repos/<repo>/`. For a single-repo change, plain repo-relative paths are fine.

| Spec Change | Source File(s) | What to Update |
|-------------|---------------|----------------|
| Added `RetryPolicy` type | `src/adapters/interface.ts` | Add type export |
| Added `retryPolicy` param to `spawnAgent()` | `src/adapters/claude.ts`, `src/adapters/factory.ts` | Update function signature and implementation |
| Executor respects retry policy | `src/agents/executor.ts` | Wire retry policy into subprocess spawn call |

## Affected Tests

List test files that need to be created or updated.

| Test File | Action | What to Test |
|-----------|--------|-------------|
| `tests/unit/adapters/claude.test.ts` | update | Test `spawnAgent()` with and without retry policy |
| `tests/unit/agents/executor.test.ts` | update | Test retry behavior on transient failures |

## Implementation Order

Numbered list of steps the implementing agent should follow.
Respect layer and facet ordering from STANDARD-SPEC.md.

1. Add `RetryPolicy` type to `src/adapters/interface.ts`
2. Update `spawnAgent()` in `src/adapters/claude.ts`
3. Update factory in `src/adapters/factory.ts`
4. Wire retry into `src/agents/executor.ts`
5. Update unit tests
6. Run full test suite

## Validation Checklist

The implementing agent must confirm each item before marking the change as applied.

- [ ] All listed source files have been updated
- [ ] The project builds / type-checks with no new errors (use the project's build or type-check command)
- [ ] All existing tests still pass (use the project's test command)
- [ ] New/updated tests pass and cover the change
- [ ] No cross-module IMPLEMENTATION coupling introduced (only INTERFACE imports across modules)
- [ ] `depends-on` front-matter in modified spec files is accurate
```

### Project-level change index

Every project-level index in `context/project/changes/` must follow this structure:

```markdown
<!-- project-change: <NNN> -->
<!-- scope: repo | shared -->
<!-- repos: <repo, ...> -->            # every repo whose change files this index references
<!-- status: pending | in-progress | applied | superseded -->
<!-- date: YYYY-MM-DD -->
<!-- consumers: <repo, ...> -->        # shared scope only: consuming repos that have change files

# PROJECT-CHANGE-<NNN>: <Title>

## Summary

One to three sentences describing the overall change across all affected repos.

## Repo Change Files

| Repo | Change File | Summary |
|------|-------------|---------|
| `repo-a` | `context/repo-a/changes/CHANGE-003-retry-logic.md` | Added retry policy to adapter |
| `repo-b` | `context/repo-b/changes/CHANGE-001-update-consumer.md` | Updated consumer to use retry policy |

## Cross-Repo Notes

Any coordination notes spanning more than one repo — ordering dependencies, migration steps,
or release sequencing. Omit this section for single-repo changes.
```

---

## Status Lifecycle

```
pending → in-progress → applied
                      → superseded (if a later change replaces this one)

complete (terminal; baseline records only — see Initial-Spec Baseline Records)
```

- **pending** — Spec changes are documented but code has not been touched.
- **in-progress** — An agent is actively implementing the code changes.
- **applied** — All code changes are complete and validated.
- **superseded** — A later change document replaces or invalidates this one. Reference the superseding change number.
- **complete** — Terminal status reserved for **initial-spec baseline records**. These document the starting state of a spec and have no code to implement, so they are born `complete` and never transition.

---

## Rules for Writing Change Documents

1. **One change document per logical change.** A "logical change" is a set of spec modifications that form a coherent unit — they'd go in one PR. Don't bundle unrelated changes.

2. **Be precise about signatures.** If a function signature changed, show the old and new signatures in full. An implementing agent should never have to guess parameter names, types, or return types.

3. **Always fill "Affected Code Paths."** This is the most important section. If you can't map a spec change to code, the change document is incomplete. Use `grep` or the codebase to identify affected files before writing the document.

4. **Respect the facet dependency chain.** Implementation order must follow: DATAMODEL changes → INTERFACE changes → IMPLEMENTATION changes → TESTING changes. Cross-module changes go INTERFACE-first.

5. **Reference spec sections, not just files.** Point to the specific section header or describe the location within the file (e.g., "the `## Execution Flow` section" or "the `SpawnOptions` type definition").

6. **Don't duplicate the spec.** The change document describes *what changed and what code to update* — it does not restate the full spec. The implementing agent will read the actual spec files for complete details.

7. **Include rollback notes for breaking changes.** If a change breaks an existing contract, note what the old contract was so an agent can revert if needed.

---

## Mapping Spec Facets to Code

Use this guide to determine which source files are affected by changes to each facet type:

| Spec Facet | Typical Code Impact |
|------------|-------------------|
| `DATAMODEL` | Type definitions in `src/<module>/` — interfaces, enums, constants, Zod schemas |
| `INTERFACE` | Exported functions/classes in `src/<module>/index.ts` or dedicated interface files |
| `IMPLEMENTATION` | Internal logic in `src/<module>/` — algorithms, state machines, orchestration flows |
| `DEPENDENCIES` | `package.json`, import statements, adapter configurations |
| `TESTING` | `tests/unit/<module>/`, `tests/integration/`, `tests/e2e/` |
| `OVERVIEW` | Rarely affects code directly — may affect README or architectural documentation |

---

## Example

The "Change Document Schemas" section above already shows every section filled with concrete
rows (the `RetryPolicy` example). A minimal single-repo change is just that schema with each
table holding one or two rows — e.g. a `CHANGE-004` adding an optional `timeoutMs` to
`SpawnOptions` would carry `scope: repo`, `repo: repo-a`, `status: pending`, "Breaking
Changes: None.", one **Spec Files Modified** row per touched facet (DATAMODEL/INTERFACE/
IMPLEMENTATION), one **Affected Code Paths** row (`src/subprocess/spawn.ts`), one **Affected
Tests** row, and a short **Implementation Order**. Don't pad empty sections — one honest row
beats a fabricated table.
