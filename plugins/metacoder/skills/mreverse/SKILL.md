---
name: mreverse
description: Use when generating a spec from an existing codebase, reconciling an out-of-date spec against the actual code, or auditing consistency within and between repos — triggers on phrases like "reverse the spec", "reverse-engineer the spec", "regenerate the spec from code", "generate a spec from this repo", "the spec is stale", "spec doesn't match the code", "sync the spec with the codebase", "audit the spec against the code", "find inconsistencies between repos", "do the repos agree", "check cross-repo consistency", or when the user wants the spec to reflect what's actually implemented rather than what was designed
---

# Spec: Reverse-Engineer From Code

Takes existing source code as ground truth and produces or reconciles the spec in
`context/<repo>/spec/`, using a team of agents to read the code in parallel. There is no brainstorm
and no described change — every claim in the output spec must be traceable to code an agent
actually read.

**Spec reconciliation is per-repo** — the spec is split per repo, so the reconcile phases run once
per repo (`mreverse` never authors `context/shared/spec/` — see "What mreverse does NOT do"). It also
**documents inconsistencies within and between repos**: run against one repo to reconcile that
repo's spec and report its **intra-repo** inconsistencies (self-contradiction, drift between
modules, dead / duplicated-but-divergent code); run against **two or more repos** (or "the whole
workspace") to additionally run a **cross-repo inconsistency pass** (Phase 5) comparing the repos'
*actual code* across shared boundaries. It documents inconsistencies and reconciles each repo's
spec; it does **not** rewrite one repo's code to match another.

## Mechanical Steps Are Invoked, Not Re-Derived

Every mechanical step of this skill — CREATE/UPDATE detection, `CATALOG.yaml` emission, Phase 4's
rule checks, and every schema validation — has exactly one implementation, in
`${CLAUDE_PLUGIN_ROOT}/tools/mc.py`. Invoke it at the phase that needs it and use what it returns.
A failed invocation is a **hard error**: report its diagnostics and stop that phase. There is no
prose fallback anywhere in this skill, because a fallback is a second implementation of the step.

What the tool does not decide stays here: the module decomposition, what each module *is*, what the
Phase 2 readers report, and every inconsistency finding and its severity.

## Step 0: Determine the Target

Candidate targets are the subdirectories of `repos/`. Resolve in order:

1. Explicit repo name(s) in the user's message ("/mreverse repo-a", "regenerate the spec for repo-b").
2. The repo containing the file/directory the user is currently pointing at.
3. If ambiguous, list every subdirectory of `repos/` and ask which one(s) to target. If the user
   says "everything" / "the whole workspace", the scope is **all** repos.

**Multi-repo scope.** Run **Phases 1–4b once per in-scope repo**, independently — each repo's spec
is self-contained. With **two or more** repos in scope, also run **Phase 5** once at the end (the
cross-repo inconsistency pass). A single-repo run stops after Phase 4b.

**Path convention for the rest of this skill:** `context/spec/...` below is shorthand for
`context/<repo>/spec/...` for the repo whose Phase 1–4b pass you are currently running.

## Step 1: Detect the Mode

```
python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py spec mode <repo>
```

It returns **CREATE** or **UPDATE** for the target. Take that answer as given — the mode follows
from the tree on disk, and this skill does not second-guess it.

**UPDATE** means the job is to make the existing spec match the code as it exists today, not to
interpret a described change.

The judgment half of this step is still yours: whether the tree the tool found is the one the user
means to reverse. A spec tree left behind by a repo that has since been split, renamed, or vendored
elsewhere is a question to ask before Phase 1, not a mode to work around.

---

## Phase 1: Recon (agent team)

**Do not write any files yet.**

1. Dispatch one `Explore` agent (quick-to-medium depth) to map `repos/<repo>/`: top-level
   directory structure, entry points (main files, CLI commands, HTTP routers, package manifest),
   and existing test directories. This is reconnaissance, not a deep read.
2. From that map, propose a candidate module decomposition — inferred from the code's actual
   package/directory boundaries, not a designed architecture. Present it as:

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

Have each subagent name what it imports from other modules by TAG, so the spec files you write in
Phase 3 carry accurate `depends-on` front-matter. Phase 4 checks it.

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
- `CATALOG.yaml`: **not assembled by hand.** Once every spec file for the repo is on disk, emit it:

  ```
  python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py spec catalog-emit <repo>
  ```

  The emission walks the spec tree you just wrote and writes the file. Facets come from each file's
  name, so name files per STANDARD-SPEC.md and there is nothing further to do about them.

  Six fields are preserved across emissions rather than re-derived, and those six remain your
  judgment: each module's layer assignment, each module's `requirements`, each INTERFACE file's
  `exports`, `shared_interfaces`, each module's `depth`, and a shared interface's `revision`. Record
  them in `CATALOG.yaml` and re-emit — the emission keeps them. A module with no layer is refused
  rather than placed, so in CREATE mode seed `repo:` and `layers:` before the first emission. Give
  `shared_interfaces:` a TAG only if a Phase 2 report found an actual outbound call into an interface
  already declared in `context/shared/spec/CATALOG.yaml` — never invent a shared interface that
  doesn't exist yet there.

  `depth` and `revision` are on that list for the same reason as the other four: neither is readable
  from code. A module with no IMPLEMENTATION facet might be at `contract` depth deliberately or might
  simply be undocumented, so deriving the field would convert an omission into a declaration; a
  `revision` is a fact about what consumers were told, which no source file records. Carry whatever
  an existing catalog already records for both forward untouched. Write a module you create from code
  at `full` depth — reverse-engineering describes all six facets from source that already exists, so
  such a module is fully described by construction rather than by intent.

### Phase 3b: UPDATE mode — reconcile, don't append

For each module already in the existing `CATALOG.yaml`:

- If its source still exists: diff the Phase 2 report against the current spec files and edit only
  what's stale (renamed export, removed function, changed signature, new/dropped dependency).
  **Only update the sections that actually changed** — do not rewrite unaffected content.
- If Phase 1 found no remaining source directory for it: delete that module's spec directory, and
  remove its TAG from `CATALOG.yaml`'s `layers:` block — that block is preserved across emissions,
  so a TAG left there outlives the module. Call this out explicitly in the completion report — spec
  deletion is unusual and the user should know what disappeared and why.

For any source directory Phase 1 found with no matching existing TAG, write it as a brand-new
module (full facet set), placed in the write order by its layer.

## Phase 4: Validate

**Run the mechanical items** — the `depends-on`, coupling, requirements and handoff checks:

```
python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py check all <repo>
```

Every `depends-on` and coupling finding is a defect to fix in the spec you just wrote, then re-run.
The requirements and handoff findings are outside this checklist — report them to the user; their
fix lies in artifacts mreverse does not write.

**Then judge what no checker can**, against Phase 1 and Phase 2:

1. Every module the decomposition confirmed has a spec directory — the emission can only list the
   tree it walked, so a module you never wrote is a module the catalog silently lacks.
2. Each module's layer assignment matches its actual position in the dependency graph found in
   Phase 2 (not guessed).
3. `COMMON-OVERVIEW.md`'s lifecycles and feature index cover everything found in recon.
4. Every public export/endpoint/command found in Phase 2 appears in some INTERFACE file — the spec
   is not missing surface area that exists in code.
5. **UPDATE mode only:** every module whose source Phase 1 found deleted has been removed from
   both the spec tree and `CATALOG.yaml`.

**Then the schema passes.** The catalog validates — fix on any error:

```
python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py validate catalog context/<repo>/spec/CATALOG.yaml
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
python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py validate inconsistency-report <aggregate>.json
```

If a repo is fully self-consistent, say so explicitly rather than omitting the report. Intra-repo
inconsistencies are **documented, not fixed** — mreverse reconciles the *spec* to the code; a code
fix is a separate `mspec` → `mexecute` cycle.

## Phase 5: Cross-Repo Inconsistency Pass (two or more repos only)

**Skip this phase for a single-repo run.** With two or more repos in scope, after each repo's
Phases 1–4b complete, compare the repos' *actual code* across the boundaries they share. Inter-repo
findings belong to **no single repo**, so they are recorded at the **workspace level**.

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
   python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py validate inconsistency-report <cross-repo-aggregate>.json
   ```

   Because these findings cross repo boundaries, they live under `context/project/` (the workspace
   level), not in any one repo's spec tree.

4. **Report** the cross-repo findings to the user by boundary. mreverse **documents** them — it
   does not rewrite either repo's code to force agreement (a deliberate `mspec` cascade →
   `mexecute` decision).

5. **File them**, per the section below. Documenting a finding and leaving it there is only half of
   "documented, not fixed".

---

## Deferrals Are Filed

An inconsistency this skill **documents but does not fix** is a deferral, and every one is written to
`context/project/TODO.md` through `mc.py todo add` — never composed by hand. Both passes are covered:
the intra-repo findings collated at Phase 4b and the cross-repo findings aggregated at Phase 5. File
once the aggregate has validated, so the entry names a finding already checked into shape.

```
python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py todo add \
    --title "<the inconsistency, named>" --run <owner> --kind <kind> --origin <change-or-plan-ref> \
    --priority <r> --risk-if-unfixed <r> --regression-risk <r> --cost <r> \
    --context "<the boundary or module, what disagrees with what, and what closing it needs>"
```

**"`mreverse` reconciles the spec to the code; a code fix is a separate `mspec` → `mexecute` cycle"
is a routing statement**, and until it was filed it routed findings nowhere. The reports under
`context/project/out/mreverse/` stay reports and no check walks them — a second durable channel for
the same finding would drift from the list. **The report says what this run saw; the list says what
is still owed.**

| Finding | `--run` | `--kind` |
|---|---|---|
| An intra-repo inconsistency in code the spec now describes correctly | `/mspec` | `logic` |
| `type-mismatch`, `signature-mismatch` across a repo boundary | `/mspec` | `architecture` |
| `emitted-but-unread`, `read-but-unemitted` | `/mspec` | `architecture` |
| `version-skew` between a producer and a consumer | `/mfix` | `spec-drift` |
| A boundary with no shared contract to reconcile against | `/mspec` | `architecture` |
| Anything whose close needs an action no agent can take | `human` | as the cause fits |

Filing changes nothing about the read-only-toward-code contract: the entry **names** the
inconsistency and proposes no fix, and `repos/<repo>/` is exactly as this run found it.

**A fully-consistent repo files nothing** — and says so in its report, exactly as it already must, so
an absence of entries is never mistaken for a pass that was never run.

---

## What mreverse does NOT do

- **No change documents.** Never write to `context/<repo>/changes/` or `context/project/changes/`
  — this is code-derived documentation, not a proposed change, and must not trigger `mplan`.
- **No plans.** Never write to `context/project/plans/`, and never invoke `mplan`.
- **No code changes.** `repos/<repo>/` is read-only input — including in the Phase 5 cross-repo pass,
  which reads both sides of a boundary but rewrites neither.
- **No authoring the `shared` spec.** mreverse reconciles each **repo's** spec and, in Phase 5,
  *reads* across repos to detect inconsistencies, but never writes `context/shared/spec/`. The
  shared contract layer is a designed agreement, not something to reverse-engineer from code; use
  `mspec` to author or change it.
- **No forcing agreement.** It documents cross-repo inconsistencies; it does not edit one repo's code
  to match another. Resolving a real inconsistency is a deliberate `mspec` cascade → `mexecute` cycle.
- **No invented lifecycles or features.** Every claim must trace back to code an agent actually
  read in Phase 1, Phase 2, or Phase 5 — do not fill gaps with plausible-sounding design intent.

## Asking Questions

Ask as plain markdown prose and proceed only after the user answers. If
`${CLAUDE_PLUGIN_ROOT}/shared/CHATFORM.md` is loaded into context (opt-in via `@import`), follow it
for fixed-option questions; otherwise plain prose is expected.
