---
name: mreq
description: Use when creating a new requirements document OR updating an existing one for a repo, the shared contract layer, or the workspace-global project tier — triggers on phrases like "flesh out requirements", "write requirements", "what are we building this for", "derive requirements from the spec", "update requirements from the spec", or generally when the user wants to capture or reconcile *why* before *how*
---

# Requirements: Brainstorm or Derive

`mreq` is the requirements-authoring skill. It creates or updates the *requirements layer* — user
needs and business goals (the *what*) — for one target: a repo, the shared contract layer
(`shared`), or the workspace-global `project` tier for cross-cutting goals tied to no single repo
or contract.

It is a documentation-only skill — it never produces application code.
Its only deliverable is one Markdown file per tier: `context/<tier>/requirements/REQUIREMENTS.md`.
It never touches `context/<target>/spec/`, `CATALOG.yaml`, or any `CHANGE-*.md` — that whole tree
belongs to `mspec`.

**Where the standard lives.** The file format ships with the plugin, not the project. Reference it
at `${CLAUDE_PLUGIN_ROOT}/shared/STANDARD-REQ.md` — `${CLAUDE_PLUGIN_ROOT}` resolves at runtime to
the plugin's marketplace-cache location. Load it lazily, at Phase 2 of whichever mode you're
running, and follow it rather than restating its schema here.

**Every mechanical step is a tool call.** Id allocation, the tier gate, the drift check, and the
closing validation each have exactly one implementation, in `${CLAUDE_PLUGIN_ROOT}/tools/mc.py`.
Run it at the call sites below and use the answer it returns; never re-derive one in prose, and
never assume one.

**A failed invocation is a hard error.** If a command below does not run — a usage error, a missing
runtime prerequisite, a path it cannot resolve — report its diagnostics to the user and stop the
phase. Do not perform the step by hand instead: a prose fallback is a second implementation of the
step, which is precisely what this design forbids. A command that runs and *reports findings* has
not failed; each call site says what its findings mean there.

What a requirement *says* is not mechanical and stays entirely your work.

## Step 0: Determine the Tier

Resolve which of three tiers the request maps to, in this order — the same resolution mspec's
Step 0 uses for its own target, generalized to three destinations:

1. An explicit argument or target name in the user's message (e.g. "/mreq repo-a …", "/mreq
   shared …", "/mreq project …").
2. The file the user is editing or referencing, by its path under `context/<tier>/requirements/`.
3. Inference from what's described: a need scoped to one codebase → that repo's own tier; a
   cross-repo contract-level need → `shared`; a product-wide or cross-cutting goal tied to no
   single repo or contract → `project`.

If the request plausibly spans multiple targets, or you cannot tell which one, list the candidate
tiers (subdirectories of `context/`, plus `shared` and `project`) and ask before proceeding.

The tier resolved here is the `<tier>` argument every invocation below takes; `STANDARD-REQ.md`
§"Location & Tiers" owns where that name puts the file, which is the path written throughout this
skill as `context/<tier>/requirements/REQUIREMENTS.md`.

## Step 1: Select the Mode

**DERIVE** only when the user explicitly asks to create or update requirements *from* the spec
(e.g. "derive requirements from the spec", "update requirements from the spec"). **BRAINSTORM**
otherwise — the default for every other trigger phrase.

Then ask the tier itself what this run is starting from:

```
python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py req gate <tier>
```

It answers whether the tier already holds at least one valid `REQ-<NNN>` entry, and lists the ids
present. Both modes key their Phase 2 off that answer rather than off file existence: **gate does
not pass** → this run seeds the tier from scratch; **gate passes** → this run amends or appends to
what is already there. Within BRAINSTORM that is mspec's own CREATE/UPDATE split, mirrored rather
than renamed as a third mode.

---

## BRAINSTORM Mode

An interactive conversation at product/business altitude only — no technical decisions, no module
decomposition. Those belong to `mspec`.

### Phase 1: Clarify

**Do not create or amend the file yet.** Ask only:

- The need — what user need or business goal is this?
- Who has it — which user or stakeholder?
- Why now — why does this matter at this point in time?
- Success — what observable outcome would tell you the need is met?

Never ask about technology, storage, API style, or any other spec-level decision — if a question
would only make sense to answer with a technical choice, it belongs to `mspec`, not here.

If Step 1's gate reported entries present, read
`context/<tier>/requirements/REQUIREMENTS.md` first so you can tell whether the new description is
a genuinely new need (a new entry) or a refinement of one already there (amend in place, or mark
superseded and add a replacement — a human decision, never automatic).

Confirm the drafted need(s) with the user before proceeding.

### Phase 2: Write

**Load now:** `${CLAUDE_PLUGIN_ROOT}/shared/STANDARD-REQ.md` — the single source of truth for the
tier locations, front-matter, entry schema, REQ-ID scoping, and the append-only stability rule.
Follow it rather than re-deriving it here.

Allocate the id of every new entry, one call per entry:

```
python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py req next <tier>
```

Ids are allocated from the highest id present in the tier, never from a count of its entries —
that is what makes reuse structurally impossible rather than merely discouraged. Never choose,
guess, or adjust an id yourself, and never renumber one.

- Gate did not pass → create `context/<tier>/requirements/REQUIREMENTS.md` if it isn't there, with
  its two-line front-matter, then write the confirmed entries under the ids allocated above.
- Gate passed → append a new `### REQ-<NNN>` entry per genuinely new need, under the id allocated
  above; amend an existing entry's text in place if the user confirmed a refinement; set
  `Status: superseded` on an entry the user says is replaced (never delete it).
- Tag every entry written in this mode `Source: brainstormed`.

Then close with Validation (below).

---

## DERIVE Mode

A one-way, non-interactive sync from an existing `context/<target>/spec/` into the requirements
file. DERIVE never diffs requirements against spec for drift — that's mspec's job, run in the
opposite direction, at its own Phase 1.

### Phase 1: Read

Read-only; the requirements file is not touched in this phase. Read
`context/<target>/spec/COMMON/COMMON-OVERVIEW.md` and every module's `<TAG>-OVERVIEW.md`. For each
capability found, draft one candidate requirement:

- **Need** — inferred from what the module does, in plain language.
- **Rationale** — the fixed placeholder `derived from spec — business rationale not yet captured`,
  never fabricated. A spec states *how*, never *why*.

Do not ask a clarifying question about business rationale here — a spec doesn't encode it, so
there's nothing to ask. Show the full draft and confirm, adjust, or reject specific entries with
the user before anything is written.

### Phase 2: Write

**Load now:** `${CLAUDE_PLUGIN_ROOT}/shared/STANDARD-REQ.md` (if not already loaded this run).

Match each drafted requirement against the target's existing
`context/<tier>/requirements/REQUIREMENTS.md`, using the target's `CATALOG.yaml` per-module
`requirements:` back-reference as the match key:

- A matched entry — its `Need` is updated in place; its ID never changes.
- An unmatched draft — appends as a new `### REQ-<NNN>` entry, under an id allocated by `req next`
  exactly as BRAINSTORM's Phase 2 does, tagged `Source: derived: <TAG>[, <TAG>...]`.
- An existing entry whose matching module disappeared from the target's spec — set `Status: stale`
  and leave it in the file. Never delete it, never renumber it.

Gate did not pass for this tier → create the file the same way BRAINSTORM's Phase 2 does, seeded
entirely from this draft.

Then check that the re-run stranded no traceability reference:

```
python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py check requirements <target>
```

It reports every `CATALOG.yaml` reference to a `REQ-<NNN>` the tier no longer declares, and every
entry no module references. A dangling reference means this run violated the stability rule —
restore the id and re-run before reporting completion. An entry no module references yet is the
expected state for a need `mspec` has not covered; report it to the user and leave it alone, since
`CATALOG.yaml` is `mspec`'s file, never this skill's to edit.

Then close with Validation (below).

---

## Validation (both modes)

Before reporting completion:

```
python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py validate requirements context/<tier>/requirements/REQUIREMENTS.md
```

A failure blocks completion — fix the file and re-run, exactly as mspec gates its own
`catalog`/`change` checks.

---

## Reads / Touches

- **Reads:** `context/<tier>/requirements/REQUIREMENTS.md`, if present (both modes — BRAINSTORM's
  amend path and DERIVE's reconciliation path both need it). DERIVE mode additionally reads
  `context/<target>/spec/**` (every `<TAG>-OVERVIEW.md` plus `COMMON-OVERVIEW.md`) and the
  target's `CATALOG.yaml`, for its `requirements:` back-reference.
- **Only file produced:** `context/<tier>/requirements/REQUIREMENTS.md`.
- **Left alone, always:** `context/<target>/spec/**`, `CATALOG.yaml`, any `CHANGE-*.md` — those
  belong to `mspec`.

`mreq` and `mspec` never call each other directly. `mspec` reads
`context/<tier>/requirements/REQUIREMENTS.md` as data — its own Prerequisites/Risk-Scan step —
never as a call into this skill. The only skill that runs both, conditionally, at its own single
gate, is `mquick`.

---

## Asking Questions

Ask clarifying/brainstorm questions as plain Markdown prose and proceed only after the user has
answered. If the optional chat-form convention (`${CLAUDE_PLUGIN_ROOT}/shared/CHATFORM.md`,
opt-in via `@import`) is loaded into context, follow it to render fixed-option questions as
`<chat-form>` blocks; if it is not loaded, plain prose is the expected behavior.
