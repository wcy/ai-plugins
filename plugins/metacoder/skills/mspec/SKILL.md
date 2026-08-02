---
name: mspec
description: Use when creating a new specification OR updating an existing one for a repo in a multi-repo workspace, or for the shared interface contracts in `context/shared/spec/` — triggers on phrases like "spec out", "write a spec", "design the system", "architect this", "update the spec", "change the spec", "modify the spec", "add a feature to the spec", "change the shared interface", or when the user describes what they want to build or a modification to an existing system
---

# Spec: Create or Update

A workspace holds **several related repositories** plus a **shared interface contract layer**. The workspace root is the directory Claude Code is run from. Before anything else, resolve **which target** you are speccing, then detect the **mode** (CREATE vs UPDATE), then follow the matching path.

**Where the standards live.** The spec/change standards ship with the plugin, not the project. Reference them at `${CLAUDE_PLUGIN_ROOT}/shared/STANDARD-SPEC.md` and `${CLAUDE_PLUGIN_ROOT}/shared/STANDARD-CHANGE.md` — `${CLAUDE_PLUGIN_ROOT}` is substituted at runtime to the plugin's marketplace-cache location, so it resolves wherever Claude Code is running without any files being copied into the project's `.claude/`. Every "Load now" instruction below names the full `${CLAUDE_PLUGIN_ROOT}/shared/…` path; load lazily, only when you reach that step.

## Two Separable Stages (Diagnostic → Write)

`mspec` runs in two cleanly separable stages. Keeping them separate is a **hard requirement**, because `/mquick` reuses each independently:

- **Stage 1 — Diagnostic / Clarify.** Everything up to and including "Confirm Before Writing" (each mode's **Phase 1**). It **writes no files**: it resolves the target, diagnoses the change/product, runs the **Risk & Ambiguity Scan** (below), surfaces every question the user should answer, and iterates until they're resolved. This is the stage `/mquick` invokes at its single clarification gate.
- **Stage 2 — Write.** Everything after approval (each mode's **Phase 2** onward): the spec files, the change documents, validation, and (for `shared`) the cascade. `/mquick` invokes this after the gate, unattended.

The stage boundary is the "**Get explicit user approval before moving to Phase 2**" line in each mode. Do not write any file before it; do not re-ask for approval after it.

### Risk & Ambiguity Scan (runs in Stage 1, both modes)

As part of the diagnostic — after you've synthesized what's changing but **before** "Confirm Before Writing" — dispatch a **single risk-scan subagent** (context-efficient: it reads the relevant specs/change description, not the whole workspace) that returns a **ranked list of decisions a human should make** before code gets written. It looks specifically for:

- **Breaking changes** — signatures/types/endpoints/events that would break existing contracts (and, for `shared`, cascade to consumers).
- **Data migration** — schema/format changes that need a migration path for existing data.
- **Security** — auth, secrets, input-validation, or exposure implications.
- **Ambiguous requirements** — places the description underspecifies behavior.
- **Dependency choices** — new libraries/services where a preferred option should be picked.
- **Requirements drift** — evaluated only when a requirements tier exists for the target: a `REQ-<NNN>` with no corresponding spec coverage, spec content with no traceable requirement, or a `CATALOG.yaml` `requirements:` reference to a `REQ-<NNN>` no longer present in the requirements file.

Fold the scan's ranked list into the questions you surface in this stage (dedupe against what you already asked). The scan **informs** the clarification; it never writes anything. In a gatekept `/mspec` run you present these and iterate as normal; `/mquick` surfaces the exact same list at its Phase A gate. When a **Requirements drift** item surfaces, the Phase 1e "Confirm Before Writing" summary must present the user a per-item choice — widen this change's scope to cover the drifted requirement, or leave it flagged for a later `/mreq` pass to reconcile — since `mspec` never writes to `requirements/` itself.

## Step 0: Determine the Target

**Spec is split per target; changes are two-tier (repo-level + project index).**

| Target | Spec lives in | What it holds |
|--------|---------------|---------------|
| A repo `<repo>` | `context/<repo>/spec/` | That repo's modules (full facet set) |
| `shared` | `context/shared/spec/` | Cross-repo interface contracts (OVERVIEW/DATAMODEL/INTERFACE only) |

Change documents are written at **two levels** on every mspec run (see STANDARD-CHANGE.md):
- **Repo-level:** `context/<repo>/changes/CHANGE-<NNN>-<slug>.md` — one per affected repo, full detail
- **Project index:** `context/project/changes/PROJECT-CHANGE-<NNN>-<slug>.md` — one per mspec run, references repo files, drives mplan

Resolve the target from context, in this order:

1. An explicit argument or repo/interface name in the user's message (e.g. "/mspec repo-a …", "update the EVENT-BUS interface").
2. The file the user is editing or referencing (its path under `context/<target>/spec/`).
3. Whether the user is describing a cross-repo contract (→ `shared`) or behavior inside one codebase (→ that repo).

If the request plausibly spans multiple repos, or you cannot tell which repo, **list the candidate targets from subdirectories of `context/` and ask the user** before proceeding. A single invocation may legitimately target the shared layer and then cascade into several repos (see the Cascade phase in UPDATE MODE).

**Path convention for the rest of this skill:**
- Every path written below as `context/spec/...` is shorthand for `context/<target>/spec/...`, where `<target>` is the repo directory you resolved, or `shared`. When the target is `shared`, only the OVERVIEW, DATAMODEL, and INTERFACE facets exist — skip every instruction about IMPLEMENTATION and TESTING facets.
- Every path written below as `context/<repo>/changes/...` is the **repo-level change path** for that repo.
- `context/project/changes/` is the **project-level index** — written once per mspec run.

## Step 1: Detect the Mode

**CREATE mode** — no spec exists yet for the target, or the user is describing a new product/project/contract from scratch.
**UPDATE mode** — a spec already exists for the target in `context/<target>/spec/` and the user is describing a change, new feature, or modification.

---

## CREATE MODE

Take a requirements description or product idea and produce a complete, implementation-ready specification in `context/<target>/spec/` through structured brainstorming with the user.

### Prerequisites

Phase 1 is pure conversation — it writes no files, so do **not** load the standards yet. Defer loading `${CLAUDE_PLUGIN_ROOT}/shared/STANDARD-SPEC.md` until Phase 2 (Write Spec), where the output must conform to it. This keeps the standard out of context during the (often lengthy) brainstorm.

**Requirements Gate.** Before Phase 1a, check `context/<repo>/requirements/REQUIREMENTS.md` (or `context/shared/requirements/REQUIREMENTS.md` for the `shared` target) for at least one valid `REQ-<NNN>` entry — file existence alone does not satisfy this; the file must contain a real entry. If the check fails, halt here — do not proceed to Phase 1a — and tell the user to trigger `/mreq <target>` manually first; `mspec` never calls `/mreq` on its own. If the check passes, also read `context/project/requirements/REQUIREMENTS.md`, if present, as supplementary cross-cutting context, then proceed to Phase 1.

### Phase 1: Brainstorm

**Do NOT write spec files yet.** First, explore the design space with the user.

#### 1a. Clarify the Product

From the user's description, synthesize and present back:

- **What It Is** — 2-3 sentence product summary
- **The Problem** — What pain point does it solve?
- **Target Users** — Who uses this?
- **Key Differentiators** — What makes this different from alternatives?

Ask the user to confirm or correct before proceeding.

#### 1b. Identify Modules

Propose a module decomposition:

1. List every major functional area as a candidate module with a short ALL-CAPS `<TAG>`
2. Assign each to a layer (L0-L5) based on dependency direction
3. Show the dependency graph — which modules depend on which

Present as a table:

```
| Module (TAG) | Layer | Depends On | Purpose |
|-------------|-------|------------|---------|
| CONFIG      | L1    | —          | App configuration and env vars |
| AUTH        | L1    | —          | Authentication and authorization |
| ...         | ...   | ...        | ... |
```

Ask the user: "Does this decomposition look right? Any modules to add, remove, or reorganize?"

**If the target is a repo:** also identify which **shared interfaces** (from `context/shared/spec/`) this repo produces or consumes. List each as a candidate `shared_interfaces` entry. If a needed contract does not exist in `context/shared/spec/` yet, flag it — it may warrant speccing the `shared` target first (a separate CREATE run), since shared interfaces are a higher altitude and repos depend on them.

**If the target is `shared`:** the "modules" are interface contracts. Each becomes an `<IFACE>` module with only OVERVIEW/DATAMODEL/INTERFACE facets. For each, note which repos are expected to produce vs. consume it (informational — the authoritative consumer list lives in each repo's `shared_interfaces` in their `context/<repo>/spec/CATALOG.yaml`).

#### 1c. Define Primary Lifecycles

For each major user journey:

1. Name it (e.g., "User Registration", "Process Payment")
2. Walk through step-by-step: user action -> system components -> outcome
3. Identify which modules participate at each step

Present as numbered flows. Ask the user to confirm the workflows are complete.

#### 1d. Surface Decisions

Identify architectural decisions that need user input:

- Technology stack choices
- Data storage strategy
- Authentication model
- API style (REST, GraphQL, RPC)
- Deployment model
- Any either-or design tradeoffs

Present each as 2-3 concrete options with trade-offs. Let the user decide. Record all decisions for `COMMON-DECISIONS.md`.

#### 1e. Confirm Before Writing

Run the **Risk & Ambiguity Scan** (see "Two Separable Stages") now and fold its ranked decisions into your questions; iterate until they're resolved. Then present a final summary:

```
Product: {name}
Modules: {count} across {layer count} layers
Lifecycles: {list}
Decisions: {list of key choices made}
Risks resolved: {the risk-scan items and how each was decided}
```

**Get explicit user approval before moving to Phase 2.** This is the Stage 1 → Stage 2 boundary — do not write any file before approval.

### Phase 2: Write Spec

**Load now:** `${CLAUDE_PLUGIN_ROOT}/shared/STANDARD-SPEC.md` — the single source of truth for file naming, module/facet layout, dependency rules, ordering, and the CATALOG.yaml schema. Do not restate its rules here; follow them.

**This is a documentation project. Do not write application code. Every deliverable is a Markdown file.**

#### Write Order

Write in the order STANDARD-SPEC.md §"Process & Ordering Rules" mandates (shared interfaces → COMMON → modules by layer L1→L5 → each module by facet order → `CATALOG.yaml` last), using its §"Module File Structure" for per-facet content. mspec-specific notes:

- **COMMON files** map Phase 1 outputs to disk: `COMMON-OVERVIEW.md` (the 200-500 line narrative), `COMMON-STACK.md` (stack decisions), and `COMMON-DECISIONS.md` (every Phase 1 decision with rationale), plus any others the project needs. Keep each under ~150 lines.
- **`CATALOG.yaml`:** for a repo target, populate `repo:` and `shared_interfaces:`; for the `shared` target, use the shared catalog schema (`scope: shared`, `interfaces:`). Also populate each touched module's optional `requirements:` list from the Phase 1e-resolved traceability (which `REQ-<NNN>` entries it satisfies), the same way you populate `shared_interfaces`.

Coupling, `depends-on` front-matter, and the INTERFACE-only cross-module/cross-repo rules are all defined in STANDARD-SPEC.md §"Dependency Rules" — obey them rather than re-deriving them. Keep OVERVIEW/COMMON files concise (they are context-injected) and describe logic language-agnostically. All paths in `depends-on` front-matter are workspace-relative (e.g. `context/<repo>/spec/<TAG>/<TAG>-INTERFACE.md`).

### Phase 3: Write the Initial Change Document

After all spec files are written, create a **repo-level baseline record**. Because sequence numbers are per-repo, `000` is always available for the first spec of any repo.

Write `context/<repo>/changes/CHANGE-000-initial-spec.md` (or `context/shared/changes/CHANGE-000-initial-spec.md` for the shared target):

```markdown
<!-- change: 000 -->
<!-- scope: repo | shared -->
<!-- repo: <target repo> -->
<!-- status: complete -->
<!-- date: YYYY-MM-DD -->

# CHANGE-000: Initial Spec Creation — {target}

## Summary

Initial specification created for {product/contract name} ({target}). This document records the
baseline state of that spec and is not associated with any implementation plan.

## Modules

{List all modules with their layer and a one-line description}

## Spec Files

{List all files written under context/<target>/spec/}
```

This file is a historical record only. It is never updated by UPDATE mode. **Do not** create a project-level index for a baseline record — baseline records have no code to implement and must not trigger mplan.

#### Validation

Before reporting completion, verify:

1. Every `depends-on` path points to a file that exists
2. No IMPLEMENTATION file depends on another module's IMPLEMENTATION
3. CATALOG.yaml includes entries for all modules and all files
4. Every module in CATALOG.yaml has correct layer and facet assignments
5. COMMON-OVERVIEW.md covers all primary lifecycles identified in brainstorm
6. `context/<repo>/changes/CHANGE-000-initial-spec.md` exists
7. **Schemas pass.** The catalog and the baseline change doc's front-matter validate — fix on any error before reporting completion:

   ```
   python3 ${CLAUDE_PLUGIN_ROOT}/schemas/validate.py catalog context/<target>/spec/CATALOG.yaml
   python3 ${CLAUDE_PLUGIN_ROOT}/schemas/validate.py change  context/<repo>/changes/CHANGE-000-initial-spec.md
   ```

---

## UPDATE MODE

Take a description of a change to an existing system and produce updated spec files in `context/<target>/spec/` plus repo-level change files in `context/<repo>/changes/` and a project-level index in `context/project/changes/`.

### Prerequisites

Phase 1 only diagnoses the change — it modifies nothing. **Defer** loading the standards until you write:
- Load `${CLAUDE_PLUGIN_ROOT}/shared/STANDARD-SPEC.md` at the start of Phase 2 (Update Spec Files).
- Load `${CLAUDE_PLUGIN_ROOT}/shared/STANDARD-CHANGE.md` at the start of Phase 3 (Write Change Document).

Loading them only when you reach the writing step keeps ~600 lines of standards out of context during the diagnostic phase.

To understand current state in Phase 1, read only what you need:
- `context/<repo>/spec/COMMON/COMMON-OVERVIEW.md` — understand the product and current feature set (skip for the `shared` target, which has no COMMON-OVERVIEW requirement)
- `context/<repo>/spec/CATALOG.yaml` — understand current modules and their layers

**If the target is `shared`:** this change will cascade. Before editing, also read every repo catalog `context/*/spec/CATALOG.yaml` and record which repos list the interface(s) you are about to change under `shared_interfaces` — these are the consumers you must cascade into (Phase 5).

**Requirements read (advisory).** Also read `context/<repo>/requirements/REQUIREMENTS.md` (or `context/shared/requirements/REQUIREMENTS.md` for the `shared` target) and, if present, `context/project/requirements/REQUIREMENTS.md` as supplementary cross-cutting context. This is advisory only — UPDATE mode never halts on this check, and proceeds with whatever exists (including none, for a spec that predates this feature).

### Phase 1: Understand the Change

**Do NOT modify any spec files yet.** First, fully understand what needs to change and why.

#### 1a. Clarify the Change

From the user's description, synthesize and present back:

- **What Is Changing** — 2-3 sentence summary of the proposed change
- **Why** — What problem or need motivates this change?
- **Scope** — Which parts of the system are affected?
- **Type** — Is this a new feature, a modification, a removal, or a refactor?

Ask the user to confirm or correct before proceeding.

#### 1b. Identify Affected Modules

Scan the existing spec to determine which modules are touched:

1. List every module whose spec files will need to change
2. For each module, list which facets are affected (OVERVIEW, DATAMODEL, INTERFACE, IMPLEMENTATION, TESTING)
3. Note whether any new modules need to be added or existing modules removed

Present as a table:

```
| Module (TAG) | Layer | Affected Facets | Nature of Change |
|-------------|-------|-----------------|-----------------|
| AUTH        | L1    | INTERFACE, IMPLEMENTATION | Add new endpoint |
| ...         | ...   | ...             | ...             |
```

Ask the user: "Does this capture all the affected areas? Anything missing or out of scope?"

#### 1c. Surface Breaking Changes

Identify whether the change breaks existing contracts:

- Does any exported function signature change?
- Are any types renamed, removed, or have fields removed?
- Do any API endpoints change URL, method, or required parameters?
- Does any event name or payload format change?
- Are any CLI commands or flags removed or renamed?

Present each breaking change explicitly. Ask the user to confirm whether these are acceptable or if the change should be made backwards-compatible.

#### 1d. Clarify Decisions

For any ambiguities in the change description, ask targeted questions:

- If new types are needed: what fields and validation rules?
- If behavior changes: what are the old and new behaviors exactly?
- If new dependencies are needed: is there a preferred library?
- If multiple implementation approaches exist: present 2-3 options with trade-offs and let the user decide.

Keep questions focused — only ask what's needed to write the spec accurately.

#### 1e. Confirm Before Writing

Run the **Risk & Ambiguity Scan** (see "Two Separable Stages") now and fold its ranked decisions into your questions; iterate until they're resolved. Then present a final summary:

```
Change: {short title}
Target: {repo name or "shared"}
Affected Modules: {list with facets}
Breaking Changes: {yes/no — list if yes}
New Modules: {list or "none"}
Cascade: {for shared target — list of consuming repos whose spec updates this one change doc will record; otherwise "n/a"}
Decisions Made: {list of key choices}
Risks resolved: {the risk-scan items — breaking/migration/security/ambiguity/dependency — and how each was decided}
```

**Get explicit user approval before moving to Phase 2.** This is the Stage 1 → Stage 2 boundary — do not write any file before approval.

### Phase 2: Update Spec Files

**Load now:** `${CLAUDE_PLUGIN_ROOT}/shared/STANDARD-SPEC.md` — the single source of truth for layout, facets, dependency rules, ordering, and the CATALOG.yaml schema. Follow it rather than re-deriving its rules.

**This is a documentation project. Do not write application code. Every deliverable is a Markdown file.**

#### Update Order

Update in the dependency order STANDARD-SPEC.md §"Process & Ordering Rules" mandates (COMMON if affected → any new modules' full sets → existing modules by layer L1→L5 → each module by facet order → `CATALOG.yaml` last), so no file references something not yet updated. Update-specific notes:

- Touch a COMMON file only when the change reaches it: `COMMON-OVERVIEW.md` (lifecycle/feature-index changes), `COMMON-STACK.md` (new tech), `COMMON-DECISIONS.md` (new decisions).
- **Only update the sections that actually change — do not rewrite unaffected content.**

Per-facet content and all coupling/`depends-on` rules live in STANDARD-SPEC.md §"Module File Structure" and §"Dependency Rules". Keep OVERVIEW/COMMON files concise (context-injected).

### Phase 3: Write Change Documents

**Load now:** `${CLAUDE_PLUGIN_ROOT}/shared/STANDARD-CHANGE.md` — defines the change-document schemas, naming, status lifecycle, and ordering rules. The documents below must conform to it; do not restate its schema from memory.

After all spec files are updated, write changes at **both levels**: first the repo-level file(s), then the project-level index.

#### Step 3a: Repo-level change file

Write or continue a change file in `context/<repo>/changes/` (one per affected repo).

**Determine whether to create or continue:**

1. List all files in `context/<repo>/changes/` matching `CHANGE-<NNN>-*.md`, **excluding baseline records** (`CHANGE-000-initial-spec.md` or `*-initial-spec.md` with `status: complete`)
2. For each, check whether a corresponding project-level index exists in `context/project/changes/` referencing this change file AND whether a plan directory exists in `context/project/plans/` for that project change
3. If a change file exists with **no** matching plan, **continue updating that file** rather than creating a new one
4. If all existing change files have plans (or only baseline records exist), create a new file

**Determine the next sequence number** *(new files only):* read every file in `context/<repo>/changes/` to find the highest `<NNN>` used by that repo, then use `<NNN+1>`.

**File naming:** `context/<repo>/changes/CHANGE-<NNN>-<short-slug>.md`

**Content:** use the repo-level schema from STANDARD-CHANGE.md §"Repo-level change document". Set `scope: repo`, `repo: <this repo>`, `status: pending`. A shared-interface cascade overrides these in Phase 5.

#### Step 3b: Project-level change index

After writing all repo-level change files, create or update the project-level index in `context/project/changes/`.

**Determine whether to create or continue:**

1. List all files in `context/project/changes/` matching `PROJECT-CHANGE-<NNN>-*.md`
2. If the most recent one has `status: pending` and no matching plan in `context/project/plans/`, **continue updating that file** — add the new repo change file(s) to its "Repo Change Files" table
3. Otherwise, create a new `PROJECT-CHANGE-<NNN>-<slug>.md` using the next free project-level sequence number

**Content:** use the project-level index schema from STANDARD-CHANGE.md §"Project-level change index". List every repo-level change file produced in this mspec run in the "Repo Change Files" table.

### Phase 4: Validate

Before reporting completion, verify:

1. Every `depends-on` path in modified spec files points to a file that actually exists
2. No IMPLEMENTATION file depends on another module's IMPLEMENTATION
3. CATALOG.yaml is updated for all new or modified modules
4. A repo-level change file exists in `context/<repo>/changes/` with the correct naming
5. The repo-level change file's "Affected Code Paths" table covers every spec change made
6. A project-level index exists in `context/project/changes/` referencing the repo change file(s)
7. COMMON-OVERVIEW.md is updated if any lifecycle or feature index entry changed
8. No unintended spec content was removed or rewritten outside the change scope
9. **Cross-repo coupling intact:** if a shared interface path was renamed or removed, no repo's `depends-on` still points at the old path (this is a cascade trigger — proceed to Phase 5)
10. **Schemas pass.** Every catalog you modified and every change document you wrote validate — fix on any error before reporting completion:

    ```
    python3 ${CLAUDE_PLUGIN_ROOT}/schemas/validate.py catalog context/<repo>/spec/CATALOG.yaml
    python3 ${CLAUDE_PLUGIN_ROOT}/schemas/validate.py change  context/<repo>/changes/CHANGE-<NNN>-<slug>.md
    python3 ${CLAUDE_PLUGIN_ROOT}/schemas/validate.py change  context/project/changes/PROJECT-CHANGE-<NNN>-<slug>.md
    ```

    Run the `change` check against every repo-level change file produced (and the shared one, for a cascade) plus the project-level index.

### Phase 5: Cascade (shared target only)

**Skip this phase entirely when the target is a repo.** It applies only when the target was `shared` and you changed one or more interface contracts.

Per STANDARD-CHANGE.md → "Shared-Interface Change Cascade", write **separate repo-level change files per repo** and one project-level index that references them all. Run this as an **agent team**: you are the **lead**, the **frozen shared-interface contract is the sync point**, and each consuming repo gets **one teammate subagent** that updates only that repo — the consumers are independent (each codes against the frozen contract, not each other), so they fan out in parallel.

1. **Freeze the shared contract first (lead).** Finish the shared `*-INTERFACE.md`/`*-DATAMODEL.md` edits and write `context/shared/changes/CHANGE-<NNN>-<slug>.md` recording them. This frozen contract is what every teammate codes against — do not let it drift once teammates are dispatched.

2. **Dispatch one teammate per consuming repo, in parallel** (single message, multiple Agent tool calls). Each teammate is scoped to exactly one repo and blind to the others; its prompt names the changed shared files, the frozen contract, and that repo only. Each teammate performs a scoped UPDATE-mode pass on its repo's spec:
   - Re-read the repo's spec files whose `depends-on` names a changed shared file.
   - Update only what the contract change forces (a renamed type, a new field, a changed signature, a removed endpoint). Do not redesign the repo.
   - Write `context/<repo>/changes/CHANGE-<NNN>-<slug>.md` covering only that repo's spec updates.
   - If the consumer turns out to be genuinely unaffected, write a one-sentence note file rather than omitting it.
   - Return which files it changed so the lead can assemble the index.

   Because every teammate targets a different repo, their edits never collide — the shared contract, not any teammate, is the coordination point.

3. **Assemble the project-level index (lead).** Collect the teammates' results and add all repo change files produced (shared + each consumer) to the "Repo Change Files" table in `context/project/changes/PROJECT-CHANGE-<NNN>-<slug>.md`. Set `scope: shared`, `repos:` and `consumers:` listing every repo with a change file.

4. **Confirm coverage.** Before reporting completion, verify that every repo in `consumers:` is represented by either a change file or a documented "unaffected" note, and that the project-level index references all of them.

5. **Report** the cascade to the user: the project change number, the shared interface(s) changed, and the consuming repos covered.

---

## Asking Questions

Throughout both modes, ask clarifying/brainstorm questions as plain markdown prose and proceed
only after the user has answered. If the optional chat-form convention
(`${CLAUDE_PLUGIN_ROOT}/shared/CHATFORM.md`, opt-in via `@import` — see the README) is loaded into
context, follow it to render fixed-option questions as `<chat-form>` blocks; if it is not loaded,
plain prose is the expected behavior.
