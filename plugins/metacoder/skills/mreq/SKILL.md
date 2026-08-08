---
name: mreq
description: Use when creating a new requirements document OR updating an existing one for a repo, the shared contract layer, or the workspace-global project tier — triggers on phrases like "flesh out requirements", "write requirements", "what are we building this for", "derive requirements from the spec", "update requirements from the spec", or generally when the user wants to capture or reconcile *why* before *how*
---

# Requirements: Brainstorm, Derive, or Audit

`mreq` is the requirements-authoring skill. It creates or updates the *requirements layer* — user
needs and business goals (the *what*) — for one target: a repo, the shared contract layer
(`shared`), or the workspace-global `project` tier for cross-cutting goals tied to no single repo
or contract.

Documentation-only: it never produces application code. Its deliverables are
`context/<tier>/requirements/REQUIREMENTS.md` and, for every amending run, that tier's
`context/<tier>/requirements/changes/REQ-CHANGE-<NNN>-<slug>.md` record. It never touches
`context/<target>/spec/`, `CATALOG.yaml`, or a spec-layer change document —
`CHANGE-<NNN>-*.md` or `PROJECT-CHANGE-<NNN>-*.md` — that tree belongs to `mspec`. The
`REQ-CHANGE-<NNN>-<slug>.md` records this skill authors carry a different prefix and are not part
of that exclusion.

**Where the standard lives.** The file format ships with the plugin, not the project, at
`${CLAUDE_PLUGIN_ROOT}/shared/STANDARD-REQ.md` (`${CLAUDE_PLUGIN_ROOT}` resolves at runtime to the
plugin's marketplace-cache location). Load it lazily, at Phase 2 of whichever mode is running, and
follow it rather than restating its schema here — it covers `REQUIREMENTS.md`'s schema and the
`REQ-CHANGE` record's.

**Every mechanical step is a tool call.** Id allocation, the tier gate, requirements-change
sequencing and emission, the drift check, and the closing validation each have exactly one
implementation, in `${CLAUDE_PLUGIN_ROOT}/tools/mc.py`. Run it at the call sites below and use the
answer it returns; never re-derive one in prose, and never assume one.

**A failed invocation is a hard error.** If a command below does not run — a usage error, a missing
runtime prerequisite, an unresolvable path — report its diagnostics and stop the phase. Do not
perform the step by hand instead: a prose fallback is a second implementation of the step. A command
that runs and *reports findings* has not failed — each call site says what its findings mean there.

What a requirement *says* is not mechanical and stays entirely your work.

## Step 0: Determine the Tier

Resolve which of three tiers the request maps to, in this order:

1. An explicit argument or target name in the user's message (e.g. "/mreq repo-a …", "/mreq
   shared …", "/mreq project …").
2. The file the user is editing or referencing, by its path under `context/<tier>/requirements/`.
3. Inference from what's described: a need scoped to one codebase → that repo's own tier; a
   cross-repo contract-level need → `shared`; a product-wide or cross-cutting goal tied to no
   single repo or contract → `project`.

If the request plausibly spans multiple targets, or you cannot tell which one, list the candidate
tiers (subdirectories of `context/`, plus `shared` and `project`) and ask before proceeding.

The tier resolved here is the `<tier>` argument every invocation below takes. `STANDARD-REQ.md`
§"Location & Tiers" owns where that name puts the file — written throughout this skill as
`context/<tier>/requirements/REQUIREMENTS.md`.

The literal token `audit` is never resolved as a target name by this step, whether it stands alone
or follows a tier that was resolved here — Step 1's AUDIT branch is what reads it, and it does its
own target handling: `/mreq audit` sweeps every tier, `/mreq <tier> audit` sweeps the tier resolved
above. A target genuinely named `audit` is written `/mreq audit audit` — the first token is read as
the branch keyword, the second as the tier name.

## Step 1: Branch on `audit`, Then Select the Mode

**AUDIT branch — checked first, before any mode is selected.** If the argument position holds the
literal token `audit` — alone (`/mreq audit`) or after the tier resolved in Step 0 (`/mreq <tier>
audit`) — take the AUDIT branch below instead of selecting a mode. AUDIT is a branch, not a third
mode: it captures nothing and derives nothing. Skip the rest of this step and go straight to [AUDIT
Branch](#audit-branch) — neither BRAINSTORM's nor DERIVE's Phase 1/Phase 2 applies.

**Mode selection**, only when the AUDIT branch above is not taken: **DERIVE** only when the user
explicitly asks to create or update requirements *from* the spec (e.g. "derive requirements from
the spec", "update requirements from the spec"). **BRAINSTORM** otherwise — the default for every
other trigger phrase.

Then ask the tier itself what this run is starting from:

```
python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py req gate <tier>
```

It answers whether the tier already holds at least one valid `REQ-<NNN>[-<mnemonic>]` entry, and
lists the ids present. Both modes key their Phase 2 off that answer rather than off file existence:
**gate does not pass** → this run seeds the tier from scratch; **gate passes** → this run amends or
appends to what is already there.

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

Never ask about technology, storage, API style, or any other spec-level decision — that belongs to
`mspec`.

If Step 1's gate reported entries present, read
`context/<tier>/requirements/REQUIREMENTS.md` first so you can tell whether the new description is
a genuinely new need (a new entry) or a refinement of one already there (amend in place, or mark
superseded and add a replacement — a human decision, never automatic).

**Inline contradiction check.** Compare the need(s) just drafted against the tier's own existing
entries (already read above, if any were present) and against
`context/project/requirements/REQUIREMENTS.md` as cross-cutting context — scoped only to the
entries in hand, never the whole tier's set (narrower than the [AUDIT branch](#audit-branch)'s full
pairwise sweep). Raise anything found for the user to resolve before Phase 2 writes anything — a
contradiction settled here, with the user present, is resolved work and nothing further is filed for
it. When this Phase 1 runs unattended inside `mquick`, where there is no one to ask, do not halt and
do not resolve it silently — flag it into the run's `REQ-CHANGE` record *and* file it on the TODO
list, routed to `/mreq` for a later AUDIT pass (see [Recording the Run](#recording-the-run)).

Confirm the drafted need(s) with the user before proceeding.

### Phase 2: Write

**Load now:** `${CLAUDE_PLUGIN_ROOT}/shared/STANDARD-REQ.md` — the single source of truth for the
tier locations, front-matter, entry schema, REQ-ID scoping, and the append-only stability rule.
Follow it rather than re-deriving it here.

Allocate the id of every new entry with two calls per entry — derive the mnemonic from the entry's
own title, then allocate the id under it:

```
python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py req mnemonic "<Title>"
python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py req next <tier> --mnemonic <slug>
```

`req mnemonic` returns a **candidate**, not a decision. Use it as-is unless the mechanical pick
misses words that carry the title's actual meaning — the rule keeps the first 2–4 significant
words, which sometimes keeps a throwaway one and drops a load-bearing one. Substituting a better
choice is yours to make; whatever you choose still goes to `req next --mnemonic`, which validates
the grammar and refuses a bad slug rather than sanitizing it. Never derive a slug from a title by
hand.

A title `req mnemonic` reports `E_NO_MNEMONIC` for — absent, or fewer than two significant words —
needs retitling with the user, or an explicit mnemonic. Never guess a slug for it: choosing words
the title doesn't contain is authoring, done in the open.

Ids are allocated from the highest id present in the tier, never from a count of entries — this
makes reuse structurally impossible, not merely discouraged. Never choose, guess, or adjust an id,
and never renumber one. Never invent a mnemonic for a reference whose heading you haven't read; a
bare `REQ-<NNN>` reference resolves without one.

- Gate did not pass → create `context/<tier>/requirements/REQUIREMENTS.md` if it isn't there, with
  its two-line front-matter, then write the confirmed entries as `### REQ-<NNN>-<mnemonic>: <Title>`
  under the ids allocated above.
- Gate passed → append a new `### REQ-<NNN>-<mnemonic>` entry per genuinely new need, under the id
  allocated above; amend an existing entry's text in place if the user confirmed a refinement
  (never its id or mnemonic); set `Status: superseded` on an entry the user says is replaced (never
  delete it).
- Tag every entry written in this mode `Source: brainstormed`.

Then record the run — see [Recording the Run](#recording-the-run) — and close with
[Validation](#validation-every-branch).

---

## DERIVE Mode

A one-way, non-interactive sync from an existing `context/<target>/spec/` into the requirements
file. DERIVE never diffs requirements against spec for drift — that's mspec's job.

### Phase 1: Read

Read-only; the requirements file is not touched in this phase. Read
`context/<target>/spec/COMMON/COMMON-OVERVIEW.md` and every module's `<TAG>-OVERVIEW.md`. For each
capability found, draft one candidate requirement:

- **Need** — inferred from what the module does, in plain language.
- **Rationale** — the fixed placeholder `derived from spec — business rationale not yet captured`,
  never fabricated. A spec states *how*, never *why*.

Do not ask a clarifying question about business rationale here — a spec doesn't encode it. Show the
full draft and confirm, adjust, or reject specific entries with the user before anything is
written.

**Contradiction check — cannot ask.** As part of this same read-only pass, compare the drafted
requirements against the tier's existing entries and against
`context/project/requirements/REQUIREMENTS.md`. DERIVE has no one to ask, so a contradiction found
here is never resolved and never halts the run — it is flagged into the run's `REQ-CHANGE` record
*and* filed on the TODO list, routed to `/mreq` (see [Recording the Run](#recording-the-run)), for a
human to resolve later at a BRAINSTORM amend or a later `/mreq audit`.

### Phase 2: Write

**Load now:** `${CLAUDE_PLUGIN_ROOT}/shared/STANDARD-REQ.md` (if not already loaded this run).

Match each drafted requirement against the target's existing
`context/<tier>/requirements/REQUIREMENTS.md`, using the target's `CATALOG.yaml` per-module
`requirements:` back-reference as the match key:

- A matched entry — its `Need` is updated in place; its id and mnemonic never change.
- An unmatched draft — appends as a new `### REQ-<NNN>-<mnemonic>` entry, under an id allocated
  exactly as BRAINSTORM's Phase 2 does — `req mnemonic "<Title>"` for the drafted requirement's own
  title, then `req next <tier> --mnemonic <slug>` — tagged `Source: derived: <TAG>[, <TAG>...]`.
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
expected state for a need `mspec` hasn't covered; report it and leave it alone — `CATALOG.yaml` is
`mspec`'s file, not this skill's to edit.

Then record the run — see [Recording the Run](#recording-the-run) — and close with
[Validation](#validation-every-branch).

---

## AUDIT Branch

A read-then-resolve sweep for contradictions within a tier's existing requirements. Not a mode: it
reads no spec, drafts nothing, and appends no entry.

### Phase 1: Sweep

Read the target tier's `context/<tier>/requirements/REQUIREMENTS.md` in full — every tier's, for a
bare `/mreq audit` — plus `context/project/requirements/REQUIREMENTS.md` as cross-cutting context,
and compare entries pairwise for contradiction: two needs that cannot both be satisfied, or one
that supersedes another without saying so. Read no spec file in this phase. This is the one path
whose cost scales with the size of the existing set rather than the request, which is why it's a
separate branch rather than something every run performs.

### Phase 2: Resolve In-Run

Present each contradiction found and resolve it with the user immediately, not in a later pass — a
contradiction between two requirements exists only in the comparison just made, so there's no
artifact a later pass could act on. Apply each resolution as an in-place amendment — an entry
reworded, or marked `Status: superseded`.

Then record the run — see [Recording the Run](#recording-the-run) — naming every contradiction
found and how each was resolved, and close with [Validation](#validation-every-branch).
Abandoning the run mid-resolution leaves the entries not yet resolved untouched and the
`REQ-CHANGE` open, which is what a later run resumes from.

---

## Recording the Run

BRAINSTORM's Phase 2, DERIVE's Phase 2, and the AUDIT branch's Phase 2 alike close by writing
exactly one `REQ-CHANGE` record for what this run changed — a run that amended nothing writes none:

```
python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py req change-resolve <tier> [--slug <slug>]
python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py req change-emit context/<tier>/requirements/changes/REQ-CHANGE-<NNN>-<slug>.md --tier <tier> --status open [--spec-change not-required]
```

`change-resolve` returns the allocated per-tier `REQ-CHANGE-<NNN>` and whether this is a fresh
record or a continuation of one already open for this run; `change-emit` writes it. Always pass
`--status open` — `mreq` never closes a record. The closing transition — `status: closed` together
with `spec-change: CHANGE-<NNN>` — is performed only by `mc.py req change-close`, invoked by
`mspec` once the spec change covering this record is written; it is the one write under
`context/<tier>/requirements/` that this skill never makes. Pass `--spec-change not-required` only
when the revision produced no spec delta — a rationale reworded, a mnemonic corrected — so the
record reads complete rather than as permanently outstanding.

In the record's body, name every entry touched, why, and what it replaced. In a BRAINSTORM run
inside `mquick` and in DERIVE, additionally name any contradiction flagged rather than resolved. In
AUDIT, name every contradiction found and how each was resolved.

### A contradiction flagged rather than resolved is also filed as deferred work

DERIVE, and a BRAINSTORM running unattended inside `mquick`, have no one to ask. Each contradiction
they flagged is therefore **also** written to `context/project/TODO.md` — one entry per
contradiction, after the record above and before the run reports:

```
python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py todo add \
    --title "<the two needs that cannot both hold, in one line>" \
    --run /mreq \
    --kind architecture \
    --origin <CHANGE-NNN|PROJECT-CHANGE-NNN|plan-id> \
    --priority <p> --risk-if-unfixed <r> --regression-risk <r> --cost <c> \
    --context "<the REQ ids and tiers on both sides, what each asks, which mode found it and why it could not ask, the REQ-CHANGE that records it, and that closing it is a /mreq <tier> audit pass>"
```

`--run` is `/mreq` on every one of these, because AUDIT is the branch that resolves a contradiction
— the item routes back to this skill, not onward to `mspec`. `--kind` is `architecture`: two needs
that cannot both be satisfied is a decision nobody has taken, not a defect in something built.
`--origin` is the `CHANGE-<NNN>`, `PROJECT-CHANGE-<NNN>` or plan id this run was requested under —
the `REQ-CHANGE` this run wrote is **not** an accepted origin, since `todo add` resolves only those
three. `--context` is written for a **cleared** context: the reader was not here, has not seen this
run, and cannot be sent to its transcript.

**The entry is additional, never a replacement.** The `REQ-CHANGE` is the durable record *that* the
contradiction was found; the TODO entry is what makes anyone look at it again. Drop the record and
the contradiction is untraceable; drop the entry and it is unscheduled — nothing revisits it.

**Neither path halts, and a refusal does not change that.** Halting would break `mquick`'s guarantee
of no gate after Phase A, and resolving silently would break the rule that a human decides. A
`todo add` refusal is exit `1` and writes nothing — including when this run has no change or plan
for `--origin` to name; surface it in the closing report next to the flag and finish the run. The
contradiction is still named in the `REQ-CHANGE` either way.

**Attended BRAINSTORM and the AUDIT branch file nothing.** Both settle the contradiction with the
user in the run that found it, so there is no unresolved work to defer, and an entry for one of them
would file an item that is already closed before anybody reads the list.

Do not compose an entry in prose and do not restate its enums here: `STANDARD-TODO.md` owns the
field set, `todo add` applies it, and `check todo` checks it.

---

## Validation (every branch)

Before reporting completion:

```
python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py validate requirements context/<tier>/requirements/REQUIREMENTS.md
python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py validate req-change context/<tier>/requirements/changes/REQ-CHANGE-<NNN>-<slug>.md
```

Run the first for `context/<tier>/requirements/REQUIREMENTS.md` whenever this run wrote to it, and
the second for every `REQ-CHANGE` record this run wrote via [Recording the
Run](#recording-the-run) — including an AUDIT run's, even though AUDIT never touches
`REQUIREMENTS.md` itself through this call. A failure blocks completion — fix the file and re-run.

---

## Reads / Touches

- **Reads:** `context/<tier>/requirements/REQUIREMENTS.md`, if present (every branch —
  BRAINSTORM's amend path, DERIVE's reconciliation path, and AUDIT's sweep all need it, AUDIT
  reading every tier's for a bare `/mreq audit`). `context/project/requirements/REQUIREMENTS.md` as
  cross-cutting context, for the inline contradiction check (BRAINSTORM Phase 1), DERIVE's
  contradiction check, and AUDIT's sweep. DERIVE mode additionally reads
  `context/<target>/spec/**` (every `<TAG>-OVERVIEW.md` plus `COMMON-OVERVIEW.md`) and the
  target's `CATALOG.yaml`, for its `requirements:` back-reference.
- **Writes:** `context/<tier>/requirements/REQUIREMENTS.md` and, for every amending run,
  `context/<tier>/requirements/changes/REQ-CHANGE-<NNN>-<slug>.md` — those two are the whole of
  what this skill *authors*. It appends to one file it does not own,
  `context/project/TODO.md`, and only through `mc.py todo add`: one entry per contradiction a mode
  that could not ask flagged rather than resolved.
- **Left alone, always:** `context/<target>/spec/**` and `CATALOG.yaml` — `mspec`'s exclusive
  domain — and a spec-layer change document, `CHANGE-<NNN>-*.md` or `PROJECT-CHANGE-<NNN>-*.md`.
  The `REQ-CHANGE-<NNN>-<slug>.md` records this skill authors carry a different prefix and are not
  part of that exclusion.

---

## Asking Questions

Ask clarifying/brainstorm questions as plain Markdown prose and proceed only after the user has
answered. If the optional chat-form convention (`${CLAUDE_PLUGIN_ROOT}/shared/CHATFORM.md`,
opt-in via `@import`) is loaded into context, follow it to render fixed-option questions as
`<chat-form>` blocks; if it is not loaded, plain prose is the expected behavior.
