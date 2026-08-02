---
name: mreverse
description: Use when generating a spec from an existing codebase, reconciling an out-of-date spec against the actual code, or auditing consistency within and between repos — triggers on phrases like "reverse the spec", "reverse-engineer the spec", "regenerate the spec from code", "generate a spec from this repo", "the spec is stale", "spec doesn't match the code", "sync the spec with the codebase", "audit the spec against the code", "find inconsistencies between repos", "do the repos agree", "check cross-repo consistency", or when the user wants the spec to reflect what's actually implemented rather than what was designed
---

# Spec: Reverse-Engineer From Code

Takes existing source code as ground truth and produces or reconciles the spec in
`context/<repo>/spec/`, using a team of agents to read the code in parallel. Unlike `mspec`, there
is no brainstorm and no described change — every claim in the output spec must be traceable to
code an agent actually read.

**Spec reconciliation is per-repo** — the spec is split per repo, so the reconcile phases run once
per repo (and `mreverse` never authors `context/shared/spec/` — see "What mreverse does NOT do").
But `mreverse` now also **documents inconsistencies within and between repos**: run it against one
repo to reconcile that repo's spec and report its **intra-repo** inconsistencies (self-contradiction,
drift between modules, dead / duplicated-but-divergent code); run it against **two or more repos**
(or "the whole workspace") to additionally run a **cross-repo inconsistency pass** (Phase 5) that
compares the repos' *actual code* across shared boundaries and reports where they disagree. It
documents inconsistencies and reconciles each repo's spec; it does **not** auto-rewrite one repo's
code to match another.

## Step 0: Determine the Target

Candidate targets are the subdirectories of `repos/`. Resolve in order:

1. Explicit repo name(s) in the user's message ("/mreverse repo-a", "regenerate the spec for repo-b").
2. The repo containing the file/directory the user is currently pointing at.
3. If ambiguous, list every subdirectory of `repos/` and ask which one(s) to target. If the user
   says "everything" / "the whole workspace", the scope is **all** repos.

**Multi-repo scope.** Spec reconciliation is per-repo, so run **Phases 1–4 once per in-scope repo**
(independently — each repo's spec is self-contained). When **two or more** repos are in scope, then
run **Phase 5** once at the end: the cross-repo inconsistency pass over all of them. A single-repo
run stops after Phase 4 (its intra-repo inconsistencies are recorded there).

**Path convention for the rest of this skill:** `context/spec/...` below is shorthand for
`context/<repo>/spec/...` for the repo whose Phase 1–4 pass you are currently running.

## Step 1: Detect the Mode

**CREATE mode** — `context/<repo>/spec/` does not exist or is empty.
**UPDATE mode** — a spec already exists. The job is to make it match the code as it exists today,
not to interpret a described change.

---

## Phase 1: Recon (agent team)

**Do not write any files yet.**

1. Dispatch one `Explore` agent (quick-to-medium depth) to map `repos/<repo>/`: top-level
   directory structure, entry points (main files, CLI commands, HTTP routers, package manifest),
   and existing test directories. This is reconnaissance, not a deep read.
2. From that map, propose a candidate module decomposition — inferred from the code's actual
   package/directory boundaries, not a designed architecture. Present it the same way mspec does:

   ```
   | Module (TAG) | Layer | Source Dirs | Depends On | Purpose (inferred) |
   |-------------|-------|-------------|------------|---------------------|
   | AUTH        | L1    | src/auth/   | —          | Session/token handling |
   | ...         | ...   | ...         | ...        | ... |
   ```

3. Ask the user to confirm or adjust the decomposition before the deep-dive fan-out in Phase 2,
   since that fan-out is the expensive step. Skip this checkpoint only if the user has explicitly
   asked for a fully autonomous run.

**If UPDATE mode:** also read `context/<repo>/spec/CATALOG.yaml` now, and flag in the table above
any existing module TAG whose source directory no longer exists in the code (candidate deletion —
see Phase 3b) and any source directory with no matching existing TAG (candidate new module).

## Phase 2: Deep-Dive (agent team, one per module)

For every confirmed module, dispatch one subagent **in parallel** (single message, multiple Agent
tool calls) scoped to only that module's source directory and its own tests. Each subagent is
blind to the others and to Phase 1 — name its target directory/files explicitly in the prompt.
Use `general-purpose` for modules large enough to need synthesis, `Explore` for small/simple ones.

Ask each subagent to report back structured under headings that map onto the target facets:

- **Overview** — one-paragraph purpose and responsibilities, as evidenced by the code, not inferred intent.
- **Datamodel** — exported types/schemas/constants/config shapes, with the defaults and validation actually enforced in code.
- **Interface** — every publicly exported function/class/endpoint/CLI command/event, with exact signatures as they exist today.
- **Dependencies** — internal imports (which other modules/directories it calls, by TAG if known) and external packages (name + version pin from the manifest) with a one-line inferred "why" from usage.
- **Implementation** — the real control flow/algorithm/state machine, described narratively. Flag anything surprising: undocumented side effects, TODOs, dead code, behavior that contradicts the module's apparent name.
- **Testing** — what test scenarios already exist (file + what each asserts) and any public surface that looks untested.
- **Inconsistencies** — any intra-repo inconsistencies this module reveals (self-contradiction, module-drift, dead-code, duplicated-divergent), returned as `inconsistency-report.schema.json` findings (`scope.kind: intra-repo`) so Phase 4b can collate them mechanically.

Have each subagent name what it imports from other modules by TAG so the orchestrator can populate
`depends_on` and check the INTERFACE-only coupling rule in Phase 4.

## Phase 3: Write Spec

**Load now:** `${CLAUDE_PLUGIN_ROOT}/shared/STANDARD-SPEC.md` — the single source of truth for
facets, layering, file naming, and the `CATALOG.yaml` schema. `${CLAUDE_PLUGIN_ROOT}` is
substituted at runtime to the plugin's marketplace-cache location (nothing is copied into the
project's `.claude/`). Do not restate its rules here; follow them.

**This is a documentation project. mreverse is read-only against `repos/` — never modify application code.**

Write in the order STANDARD-SPEC.md §"Process & Ordering Rules" mandates: COMMON first, then
modules by layer L1→L5, each module by facet order, `CATALOG.yaml` last.

- `COMMON-OVERVIEW.md`: derive its Primary Lifecycles from real entry points traced through Phase
  1/2 findings (e.g. "what happens when the `sync` CLI command runs," followed through actual
  calls) — not aspirational flows. Feature Index must map every module found.
- `CATALOG.yaml`: populate `repo:`. Only populate `shared_interfaces:` if a Phase 2 report found
  an actual outbound call into an interface already declared in `context/shared/spec/CATALOG.yaml`
  — never invent a shared interface that doesn't exist yet there.

### Phase 3b: UPDATE mode — reconcile, don't append

For each module already in the existing `CATALOG.yaml`:

- If its source still exists: diff the Phase 2 report against the current spec files and edit only
  what's stale (renamed export, removed function, changed signature, new/dropped dependency).
  **Only update the sections that actually changed** — do not rewrite unaffected content.
- If Phase 1 found no remaining source directory for it: delete that module's spec directory and
  remove it from `CATALOG.yaml`. Call this out explicitly in the completion report — spec deletion
  is unusual and the user should know what disappeared and why.

For any source directory Phase 1 found with no matching existing TAG, write it as a brand-new
module (full facet set), placed in the write order by its layer.

## Phase 4: Validate

Before reporting completion, verify:

1. Every `depends-on` path points to a file that exists.
2. No IMPLEMENTATION file depends on another module's IMPLEMENTATION.
3. `CATALOG.yaml` lists every module identified in Phase 1/2, and every file written.
4. Each module's layer assignment matches its actual position in the dependency graph found in
   Phase 2 (not guessed).
5. `COMMON-OVERVIEW.md`'s lifecycles and feature index cover everything found in recon.
6. Every public export/endpoint/command found in Phase 2 appears in some INTERFACE file — the spec
   is not missing surface area that exists in code.
7. **UPDATE mode only:** every module whose source Phase 1 found deleted has been removed from
   both the spec tree and `CATALOG.yaml`.
8. **Schema passes.** The catalog validates — fix on any error:

   ```
   python3 ${CLAUDE_PLUGIN_ROOT}/schemas/validate.py catalog context/<repo>/spec/CATALOG.yaml
   ```

Report a short summary: modules created, modules updated, modules deleted (if any), and any
surprising implementation findings flagged in Phase 2.

## Phase 4b: Intra-Repo Inconsistency Report

The Phase 2 deep-dive already flags per-module surprises (undocumented side effects, TODOs, dead
code, behavior that contradicts a module's name). Collate the ones that are genuine **inconsistencies
within this repo** — not mere notes — into a structured report:

- **self-contradiction** — a module whose own code disagrees with itself (e.g. two code paths
  enforcing different validation for the same input).
- **module-drift** — two modules that should agree but don't (a caller and callee with mismatched
  assumptions; a type defined one way and consumed another).
- **dead-code** — exported/branchable surface no live path reaches.
- **duplicated-divergent** — the same logic copied into two places that have since diverged.

Write the report to `context/project/out/mreverse/<repo>-inconsistencies.md` (a readable summary),
and have each Phase 2 subagent return its findings in the `inconsistency-report.schema.json` shape
(`scope.kind: intra-repo`) so the collation is mechanical. Validate the aggregate you assemble:

```
python3 ${CLAUDE_PLUGIN_ROOT}/schemas/validate.py inconsistency-report <aggregate>.json
```

If a repo is fully self-consistent, say so explicitly rather than omitting the report. Intra-repo
inconsistencies are **documented, not fixed** — mreverse reconciles the *spec* to the code; a code
fix is a separate `mspec` → `mexecute` cycle.

## Phase 5: Cross-Repo Inconsistency Pass (two or more repos only)

**Skip this phase for a single-repo run.** When two or more repos are in scope, after each repo's
Phases 1–4 are complete, compare the repos' *actual code* across the boundaries they share. This is
the pass that lifts mreverse's former single-repo limit — inter-repo findings belong to **no single
repo**, so they are recorded at the **workspace level**.

1. **Find the shared boundaries.** From each repo's `context/<repo>/spec/CATALOG.yaml`
   `shared_interfaces` (and any direct cross-repo calls a Phase 2 reader flagged), list the
   producer/consumer pairs that couple across a contract.

2. **Fan out cross-repo readers (subagents), in parallel** — one per shared boundary. Each reader
   loads only the two (or more) repos' code at that boundary (the producer's emit/serialize path and
   each consumer's read/deserialize path) plus the shared contract in `context/shared/spec/<IFACE>/`
   if it exists. It reports where the code disagrees:
   - **type-mismatch** — the same field typed/shaped differently on each side.
   - **emitted-but-unread** — a producer field no consumer reads.
   - **read-but-unemitted** — a consumer expecting a field no producer sends.
   - **version-skew** — the two sides coded against different versions of the contract.
   - **signature-mismatch** — an RPC/endpoint/event whose shape differs across the boundary.
   Each returns the `inconsistency-report.schema.json` shape (`scope.kind: cross-repo`, naming the
   `repos` and `boundary`).

3. **Aggregate + record at workspace level.** Merge the readers' findings into
   `context/project/out/mreverse/cross-repo-inconsistencies.md` (readable) and validate the machine
   aggregate:

   ```
   python3 ${CLAUDE_PLUGIN_ROOT}/schemas/validate.py inconsistency-report <cross-repo-aggregate>.json
   ```

   Because these findings cross repo boundaries, they live under `context/project/` (the workspace
   level), not in any one repo's spec tree.

4. **Report** the cross-repo findings to the user by boundary, and state plainly that mreverse
   **documents** them — it does not rewrite either repo's code to force agreement (that's a
   deliberate `mspec` cascade → `mexecute` decision).

---

## What mreverse does NOT do

- **No change documents.** Never write to `context/<repo>/changes/` or `context/project/changes/`
  — this is code-derived documentation, not a proposed change, and must not trigger `mplan`.
- **No plans.** Never write to `context/project/plans/`, and never invoke `mplan`.
- **No code changes.** `repos/<repo>/` is read-only input — including in the Phase 5 cross-repo pass,
  which reads both sides of a boundary but rewrites neither.
- **No authoring the `shared` spec.** mreverse reconciles each **repo's** spec and, in Phase 5,
  *reads* across repos to detect inconsistencies — but it never writes `context/shared/spec/`. The
  shared contract layer is a designed agreement, not something to reverse-engineer from one repo's
  code; use `mspec` to author or change it.
- **No forcing agreement.** It documents cross-repo inconsistencies; it does not edit one repo's code
  to match another. Resolving a real inconsistency is a deliberate `mspec` cascade → `mexecute` cycle.
- **No invented lifecycles or features.** Every claim must trace back to code an agent actually
  read in Phase 1, Phase 2, or Phase 5 — do not fill gaps with plausible-sounding design intent.

## Asking Questions

Same convention as `mspec`: ask as plain markdown prose and proceed only after the user answers.
If `${CLAUDE_PLUGIN_ROOT}/shared/CHATFORM.md` is loaded into context (opt-in via `@import`), follow
it for fixed-option questions; otherwise plain prose is expected.
