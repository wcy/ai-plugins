---
name: mmigrate
description: Use when tracked project artifacts (plan/state/catalog YAML, change and requirements front-matter, project ledger) need to conform to their JSON Schema, or when REQ/CHANGE identifiers, spec module slugs, or the links between them have drifted — triggers on phrases like "migrate the artifacts", "check every catalog", "fix the schema", "conform to schema", "audit the requirement ids", "the slugs are wrong", "the change numbers don't line up", "fix broken depends-on links", "sweep the project files for drift", or after a schema version bump. Read-only detection folds into mechanical, root-cause repair of an artifact's own shape, identifiers, and cross-references — never spec content, code, or architecture.
---

# Migrate: Bring Every Tracked Artifact Into Schema and Identifier Conformance

`mverify` asks *does the code match what the spec says?* `mfix` decides, per finding, which of
the two is right and repairs it. Neither ever asks the more basic question this skill answers:
**is the artifact itself well-formed** — does it parse and validate against its own JSON Schema,
does its filename and id follow `STANDARD-REQ.md`/`STANDARD-CHANGE.md`, and do its cross-references
actually resolve? A `plan.yaml` with a typo'd `run:` field, a `CHANGE-004` front-matter block that
says `change: 4`, a `REQ-001` heading duplicated by a copy-paste, a `depends-on` line still pointing
at a file that was renamed a month ago — none of these are spec-vs-code drift, and none of them
need new architecture. They are bookkeeping defects in artifacts everyone already agreed the shape
of. `mmigrate` finds every one of these across the whole tracked tree — not just the file another
skill happened to be touching — and repairs it at the root cause.

**`mmigrate` never writes spec, requirement, or code content.** It makes existing, already-intended
content conform to its declared shape and cross-reference it correctly. A fix that would require
inventing a `Need`, a `Rationale`, a coupling decision, or which of two duplicate ids is the real
one is not this skill's to make — it is recorded as deferred, never guessed.

## Invoking the Tool

Every mechanical step — resolving the target list, validating an artifact against its schema,
sweeping depends-on/catalog/requirements links — is a single invocation of
`python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py`, never a procedure re-derived from prose. A schema
`FAIL` or a `check` finding is not a hard error here — it is the defect this skill exists to
close, and the run continues past it to the next artifact. An invocation that exits `2` (a bad
kind, an escaped path, an unparseable file) *is* a hard error: report the diagnostic and move on to
the next artifact rather than guessing at what it meant.

Discovering *which files exist* has no dedicated verb — that part is plain shell (`find`, `ls`).
Deciding *whether a file is well-formed, or what "well-formed" even means* is never re-derived by
hand; it is always the schema, `req`/`change`'s own parsers, or `check`'s rules.

## Step 0: Build the Artifact Inventory

Get the canonical target list from the tool, not a hand-rolled directory listing — it is exactly
the set `check`/`spec` themselves walk:

```
python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py --json check all
```

Read `data.targets` off the result (every `context/<name>/` directory except `project` — a repo,
or `shared` if this workspace has one). Then enumerate every schema-bearing instance:

| Kind | Instances |
|------|-----------|
| `project-state` | `context/project/state.yaml` |
| `plan-graph` | `context/project/plans/*/plan.yaml` |
| `plan-state` | `context/project/plans/*/state.yaml` |
| `catalog` | `context/<target>/spec/CATALOG.yaml` for every target |
| `requirements-frontmatter` | `context/<target>/requirements/REQUIREMENTS.md` for every target, plus `context/project/requirements/REQUIREMENTS.md` |
| `change-frontmatter` | `context/<repo>/changes/CHANGE-*.md` for every repo target, plus `context/project/changes/PROJECT-CHANGE-*.md` |
| `req-change-frontmatter` | `context/<tier>/requirements/changes/REQ-CHANGE-*.md` for every tier, **including the `project` tier** |

A tier with no directory (no `context/project/requirements/`, no `shared` target, no
`requirements/changes/` subdirectory) is absent, not defective — skip it without a finding, the same
rule `TOOLS-IMPLEMENTATION.md` states for a workspace with no `context/shared/` tree.

**Scope stays `context/**`.** The inventory above is everything tracked under `context/**`. The
plugin's own source tree — `SKILL.md` files, `shared/*.md`, `schemas/`, `tools/` — is out of scope
for this sweep, even where those files (`STANDARD-REQ.md` included) describe the very identifier
formats this skill polices. Migrating the plugin's own copies is a spec change shipped through
`mplan`/`mexecute` like any other source edit, never something a conformance sweep reaches into.

## Step 1: Schema Conformance

Validate every instance from the inventory against its kind. Batch same-kind files into one call;
a missing file fails that file alone and does not abort the batch:

```
python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py validate project-state context/project/state.yaml
python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py validate plan-graph context/project/plans/*/plan.yaml
python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py validate plan-state context/project/plans/*/state.yaml
python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py validate catalog context/<target>/spec/CATALOG.yaml ...
python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py validate requirements context/<target>/requirements/REQUIREMENTS.md ...
python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py validate change context/<repo>/changes/CHANGE-*.md ...
python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py validate req-change context/<tier>/requirements/changes/REQ-CHANGE-*.md ...
```

(`plan-state` has no shorthand alias — the canonical kind name is required. `requirements`,
`change`, and `req-change` are aliases for `requirements-frontmatter`, `change-frontmatter`, and
`req-change-frontmatter`.)

Every `FAIL` line names the JSON-Schema location and message — that is the fix target, taken
verbatim, not re-derived by reading the schema again.

## Step 2: Repair Schema Violations — Shape Only

For each `FAIL`, make the **minimal, mechanically-derivable** correction and re-validate:

- A required field missing where the correct value is unambiguous from the artifact's own other
  fields or the filename (e.g. a `change:` number that must equal the one already in the filename,
  an `updated:` date already visible in a sibling field) — fill it in.
- A value of the wrong shape carrying the right meaning (an unquoted sequence number where the
  schema wants a zero-padded string, a bare word where an enum member was intended and only one
  member is a plausible reading) — reshape it in place.
- An unrecognized key that is an obvious typo of a real one (`stauts` for `status`) — rename it.

**Never** invent substantive content — a `Need`, a `Rationale`, a `Title`, which `status` a change
document should carry — to make a required-field check pass. That is content, not shape, and
belongs to `mreq`/`mspec`/a human. Mark it deferred with the exact schema error, and move on.

## Step 3: REQ Identifiers

`req gate` already parses every entry and warns on a missing `Need`/`Rationale`/`Status` or an
unrecognized `Status` value — run it per tier before hand-checking anything the tool already
covers:

```
python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py req gate <tier>
```

Beyond what the tool reports, `STANDARD-REQ.md` makes two claims about `REQ-<NNN>` ids that
nothing mechanically checks today — read the file directly for these:

1. **Ascending, never-reused order.** Ids must appear in increasing numeric order, top to bottom,
   within the tier's file. Two ids swapped is a **layout-only** defect — safe to reorder the
   `### REQ-...` blocks directly, since no content changes and the standard mandates the order.
   A genuinely **duplicate** id (the same `REQ-<NNN>` heading twice) is not: which occurrence is
   the real entry is a content judgment. Defer it, quoting both occurrences in full so a human or
   `mreq` can decide.
2. **Every id parses.** The tool's own heading pattern is `REQ-(\d{3,})` — three or more digits.
   An id with fewer (`REQ-1`, `REQ-42`) is invisible to `req next`'s allocator entirely, which
   means the *next* id it hands out can collide with one nobody can see. Zero-pad any such id to
   three digits — this is pure formatting, the number itself does not change.

### Migrating a Tier onto the Mnemonic Id Format

Beyond the two hand-checks above, apply the identifier-convention change itself: a heading still on
the bare `### REQ-<NNN>: <Title>` form is migrated to `### REQ-<NNN>-<mnemonic>: <Title>`, taking the
mnemonic from the tool rather than deriving it yourself:

```
python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py req mnemonic "<Title>"
```

**Append the returned candidate verbatim.** The tool permits its caller to substitute a different
word choice; this skill does not exercise that permission. Picking words other than the ones the
mechanical rule chose is authoring, and a sweep does not author — that judgement belongs to `mreq`.
What remains here is a transform of content the artifact already contains, the same class as
zero-padding an id above, and deterministic besides: the candidate is a pure function of the title,
asserted in `tests/test_determinism.py` rather than promised here. `<NNN>` is preserved unchanged
throughout: nothing here is a renumber, which is exactly what makes the migration legal under
`STANDARD-REQ.md`'s stability rule — that rule prohibits reassigning an id to a different
requirement, not editing a heading's mnemonic.

A `<Title>` the tool reports `E_NO_MNEMONIC` for (absent, or with fewer than two significant words)
is **deferred to `mreq`** and reported `deferred` in the run report with `mreq` named as owner of the
next step. Never invent a slug for it — choosing words the artifact does not itself contain would be
authoring content, which is outside this skill's fence.

Only a **bare** `REQ-<NNN>` heading is a format defect. A heading that already carries a mnemonic is
never re-derived, which is what makes a second pass over a migrated tier report nothing.

Every reference resolving to a migrated heading is rewritten in the same pass to carry that
heading's mnemonic.

**The standing rule, once a tier is migrated.** After migration this step degenerates to a standing
check: `mc.py check requirements <target>` reports `W_STALE_REQ_MNEMONIC` when a reference's
mnemonic disagrees with the heading it resolves to. Resolve it in **one direction only** — rewrite
the disagreeing **reference** to match the heading. **Never** rewrite the heading to match the
reference. The heading is where the requirement is defined; a reference is only a pointer to it, and
letting a stale reference rename a live requirement's mnemonic is exactly what keeping the number as
the identity was meant to prevent.

## Step 4: CHANGE / PROJECT-CHANGE / REQ-CHANGE Identifiers

For every `CHANGE-<NNN>-<slug>.md`, `PROJECT-CHANGE-<NNN>-<slug>.md`, and `REQ-CHANGE-<NNN>-<slug>.md`
found in Step 0, check what Step 1's schema pass cannot (the schema validates the front-matter
block's shape only, never the filename). All four checks below apply to `REQ-CHANGE-<NNN>-<slug>.md`
**unchanged**, evaluated against the tier's own per-tier sequence rather than a repo's:

1. **Filename agrees with front matter.** The filename's `<NNN>` must equal the front-matter
   `change:`/`project-change:`/`req-change:` value, same zero-padding. On a mismatch, the
   front-matter is authoritative — it is what `change resolve`/`change emit`/`req change-resolve`/
   `req change-emit` read and write — so rename the file to match it, **unless** another file in the
   same directory already holds that number, in which case this is a numbering collision, not a
   typo: defer it.
2. **Slug is well-formed.** 2–4 words, kebab-case (`^[a-z0-9]+(-[a-z0-9]+){1,3}$`), or the reserved
   `initial-spec` baseline slug. A malformed slug is cosmetic — normalize it (lowercase, hyphenate)
   and rename the file, unless the new name would collide with an existing one.
3. **No duplicate `<NNN>` in one directory.** Two files claiming the same sequence number is
   corruption. If a byte-for-byte `diff` shows them identical, the extra copy is a duplicate — say
   so and remove it. Otherwise, which one is "real" is not yours to decide: defer it with both
   paths and their content named, for a human or `mfix` to resolve.
4. **`repo:`/`tier:` matches placement.** A repo-level change's front-matter `repo:` field should
   equal the directory it lives under; a `REQ-CHANGE` record's `tier:` field must likewise equal the
   tier directory (`context/<tier>/requirements/changes/`) it lives under. A mismatch may mean the
   file is genuinely misfiled — that is a bigger move than a rename, so defer it rather than
   relocating a document something else might reference by path.

Two more checks are specific to `REQ-CHANGE` records:

5. **Dangling back-link.** A record whose `status` is `closed` must name a `spec-change` that
   resolves to a `CHANGE-<NNN>` that actually exists. One that doesn't is **deferred, never
   rewritten** — which of the two artifacts is wrong (the record's back-link, or the change file it
   claims) is `mfix`'s call, not this sweep's.
6. **Never edit the lifecycle.** `status` and `spec-change` are never edited by this skill, in
   either direction — not even to "fix" a dangling back-link. That transition belongs solely to
   `mc.py req change-close`; an open record surfaces here only as an inventoried artifact and in
   Step 5's `check handoff` note, never as something this skill writes.

## Step 5: Reference Integrity Sweep

Run the full per-target sweep once. It already covers both directions of catalog↔requirements
linkage, dangling and missing `depends-on`, and the three catalog-shape rules (file-set agreement,
facet-matches-filename, layer-matches-`layers:`) — none of these rules are re-derived here, only
consumed:

```
python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py check depends-on <target>
python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py check requirements <target>
python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py check catalog <target>
```

`check coupling` and `check handoff` findings are architecture and process debt outside this
skill's fence — note them in the report (Step 7) but leave them for `mfix`/`mreverse`; acting on
them would be re-deciding code-vs-spec authority, which is `mfix`'s call alone.

## Step 6: Fix Reference-Integrity Findings

Each `depends-on`/`catalog`/`requirements` finding has a correct value derivable from the artifact
itself, or it does not — there is no third case:

- **Dangling `depends-on`** naming a moved or renamed file → grep the tree for the file's real
  location and repoint the reference; if it is genuinely gone, remove the reference and say why.
- **Missing `depends-on` block** (mandatory on every spec file except `COMMON-OVERVIEW.md`) → add
  it, deriving the edge from what the file actually references in prose — read it, do not guess.
- **Wrong `facet`/`layer` in a catalog entry** → correct the one field that disagrees with reality
  (the filename for facet, the `layers:` block for layer). If both readings look equally plausible,
  defer rather than picking one.
- **Catalog `requirements:` entry naming an absent `REQ-<NNN>`** → if it is a one-digit or
  zero-padding typo of a real id, correct it; if it names nothing real, remove it and say so.
- **Orphan `REQ-<NNN>`** (exists, referenced by no module) → **do not** delete it or invent a
  module reference for it. Whether the requirement still applies, and to what, is content — record
  it as tracked debt, the same way `mfix` defers missing architecture to `mspec`.

Any of these that edits `context/<repo>/spec/` content (a catalog field, a `depends-on` line) is a
**spec fix** in `mfix`'s sense, even though a mechanical sweep triggered it rather than a drift
finding. Follow `mfix`'s Step 5 discipline for it: write or continue a change document recording
the fix, then re-run the check to confirm closure:

```
python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py change resolve <repo> --slug <slug>
python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py change emit context/<repo>/changes/CHANGE-<NNN>-<slug>.md \
    --scope repo --repo <repo> --status pending --title "<title>" --plan not-required
python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py validate change context/<repo>/changes/CHANGE-<NNN>-<slug>.md
```

Pass `--plan not-required`, same as `mfix`: a mechanical-sweep fix is already applied by the time
this record is written, so it has no further code phase for a plan to reach (`STANDARD-CHANGE.md`
§"No-Plan-Needed Records").

Fixes confined to bookkeeping artifacts — a change file's own filename, a `REQ` heading's
zero-padding, `plan.yaml`/`state.yaml` schema shape, the project ledger — are not spec changes and
need no change document. Record them in this run's own report instead (Step 7).

## Step 7: Re-validate and Report

Re-run every `validate` call from Step 1 and every `check` call from Step 5. Every artifact this
run touched must now come back clean; one that does not is a fix that did not hold, not a report to
paper over.

Write a run report to `context/project/out/mmigrate/<date>/report.md` covering every artifact this
run inventoried:

- **clean** — validated and linked correctly on the first pass, no action taken.
- **fixed** — the defect and the correction made, with the `CHANGE-<NNN>` reference if the fix
  touched spec content.
- **deferred** — the defect, why it needs content or a judgment call this skill will not make, and
  which skill or person owns the next step (`mreq`, `mspec`, `mfix`, or a human).

## What mmigrate does / does NOT do

- **Does:** validate every schema-bearing artifact in the tree against its canonical kind, not just
  the ones another skill happens to be writing; repair id/slug format, filename-vs-front-matter
  agreement, and reference integrity at the root cause; write or continue a change document for any
  fix that touches spec content; defer anything that would require inventing content or judging
  between two equally plausible artifacts.
- **Does NOT:** decide code-vs-spec authority (`mfix`'s call); write new spec or requirement
  content (`mspec`/`mreq`); generate a spec tree for a repo that has none (`mreverse`); plan or ship
  code (`mplan`/`mexecute`); fabricate a `Need`, `Rationale`, `Title`, `Status`, or `depends-on`
  edge it cannot derive from what already exists; silently pick a winner between two duplicate ids
  or files; publish or push without explicit authorisation.

## Asking Questions

Escalate only when: a fix would require choosing between two artifacts that both plausibly own the
same id or number (a genuine duplicate, not a mismatch with one clear answer); a schema's required
field demands content no source states; or an outward-facing action is needed — publishing,
pushing, or deleting — which is confirmed unless already authorised. Every id-format,
filename-agreement, and reference-integrity repair this skill exists for is mechanical and made
without asking, the same way `mfix` makes its code-vs-spec calls without a menu.
