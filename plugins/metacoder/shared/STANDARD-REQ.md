# Requirements Document Standards

Standards for the requirements layer — the *what* (user needs and business goals) that sits
beneath a spec's *how* (technical design). Referenced by prompt files — this is the single source
of truth for `REQUIREMENTS.md` layout, IDs, and stability rules. Consumed by `mreq` (author) and
read by `mspec` (Phase 1 context, never written).

---

## Purpose

A spec answers *how* something works. A requirements document answers *why it should exist at
all* — the user need or business goal that justifies building it. Keeping this a separate,
lighter-weight layer means "why" survives design churn: a spec can be rewritten from scratch
without losing the business rationale that motivated it.

---

## Location & Tiers

**Three tiers**, mirroring `STANDARD-CHANGE.md`'s repo/shared/project split exactly:

```
context/<repo>/requirements/REQUIREMENTS.md
context/shared/requirements/REQUIREMENTS.md
context/project/requirements/REQUIREMENTS.md
```

- **`context/<repo>/requirements/REQUIREMENTS.md`** — needs and goals scoped to one repo.
- **`context/shared/requirements/REQUIREMENTS.md`** — needs and goals scoped to a cross-repo
  contract in `context/shared/spec/`.
- **`context/project/requirements/REQUIREMENTS.md`** — cross-cutting needs and goals tied to no
  single repo or contract.

Exactly one `REQUIREMENTS.md` file per tier — there is no per-module split the way spec has
`<TAG>-<FACET>.md` files; requirements are lighter-weight by design.

---

## Front-Matter

`REQUIREMENTS.md` sits outside `context/<target>/spec/` and never participates in the
facet/layer dependency graph `CATALOG.yaml` governs, so it is **exempt** from the general spec
`depends-on` convention. Its front-matter is exactly two lines:

```markdown
<!-- requirements: <target-name>|shared|project -->
<!-- updated: YYYY-MM-DD -->
```

- `requirements` — the target/tier name: a repo name, `shared`, or `project`.
- `updated` — the date of the most recent write to this file, `YYYY-MM-DD`.

---

## Entry Schema

**Body:** `# Requirements — <Target Name>` followed by a sequence of entries, in ascending,
never-reused ID order:

```markdown
### REQ-<NNN>: <Title>

**Need:** <the user need or business goal, in plain language, no technical detail>
**Rationale:** <why this matters — business justification>
**Status:** active | stale | superseded
**Acceptance:** <optional bullet list of observable outcomes>
**Source:** brainstormed | derived: <TAG>[, <TAG>...]
```

- `<NNN>` — zero-padded, ascending, unique **within this tier's file** (see REQ-ID Scoping below).
- **`Need`** — required. Plain language, no technology, storage, API-style, or other spec-level
  decision. If it reads like a design choice, it belongs in a spec, not here.
- **`Rationale`** — required. The business justification for the need. A DERIVE-drafted entry
  carries the fixed placeholder `derived from spec — business rationale not yet captured` rather
  than a fabricated one — a spec states *how*, never *why*.
- **`Status`** — required, one of exactly three values:
  - `active` — still wanted.
  - `stale` — no longer covered by the spec; set only by a DERIVE re-run when the entry's matching
    module disappears. Never set by deleting the entry.
  - `superseded` — explicitly replaced by a newer requirement; set only by a human during a
    BRAINSTORM amend.

  There is no `applied` value — a requirement isn't executed the way a change document is; it is
  either still wanted, no longer covered, or replaced.
- **`Acceptance`** — optional bullet list of observable outcomes that would tell you the need is
  met.
- **`Source`** — optional. `brainstormed` for a human-authored entry, or `derived: <TAG>[,
  <TAG>...]` naming the spec module(s) a DERIVE pass drafted it from.

---

## REQ-ID Scoping

Each of the three tiers keeps its **own independent** `REQ-<NNN>` sequence — a repo's
`REQUIREMENTS.md` numbers from `REQ-001` regardless of what numbers `shared` or `project` are
using.

A bare `REQ-<NNN>` inside a `CATALOG.yaml` module's `requirements:` list always means "this
target's own tier." A reference to the workspace-global project tier from within a repo or shared
catalog is written `project:REQ-<NNN>` to disambiguate it from the target's own numbering — the
same bare-tag-vs-qualified pattern `shared_interfaces` already uses for cross-repo references.

---

## Stability Rule

IDs are **append-only** and stable across every DERIVE re-run:

- A drafted requirement that matches an existing entry updates that entry's `Need` **in place** —
  its ID never changes.
- A drafted requirement with no match **appends** as a new `REQ-<NNN>`, tagged `Source: derived:
  <TAG>[, <TAG>...]`.
- An existing entry whose spec coverage has disappeared is marked `Status: stale` and **left in
  the file** — never deleted, never renumbered.

Nothing is ever renumbered or deleted, by any mode, for any reason. This is what keeps a
`CATALOG.yaml` `requirements:` back-reference from being silently orphaned by a later `mreq` run.

Mechanically checked: this file must always state the rule in a form
`grep -qi 'never.{0,15}renumber' plugins/metacoder/shared/STANDARD-REQ.md` can find.

---

## Example

```markdown
<!-- requirements: repo-a -->
<!-- updated: 2026-08-02 -->

# Requirements — repo-a

### REQ-001: Faster checkout for repeat customers

**Need:** Returning customers want to complete a purchase without re-entering payment details.
**Rationale:** Cart abandonment rises sharply on longer checkout flows; repeat customers are our
highest-margin segment.
**Status:** active
**Acceptance:**
- A returning customer completes checkout in one confirmation step.
- Saved payment methods are never displayed in full.
**Source:** brainstormed

### REQ-002: Session persistence across devices

**Need:** derived from spec — business rationale not yet captured
**Rationale:** derived from spec — business rationale not yet captured
**Status:** stale
**Source:** derived: SESSION
```

A minimal single-entry file is just the front-matter, the `# Requirements — <Target Name>`
heading, and one `### REQ-001` block with `Need`, `Rationale`, and `Status` filled in —
`Acceptance` and `Source` are optional and may be omitted rather than padded.
