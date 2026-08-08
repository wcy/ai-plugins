# Deferred Work Standards

Standards for `context/project/TODO.md` — the list of work a run identified but could not resolve.
Single source of truth for where that list lives, what one entry holds, and how an entry leaves it.
Entries are written through `mc.py`'s `todo add` verb and checked by its `check todo`; `check
handoff` reports the open ones. This document owns the field set and both closed enums, and the tool
reads them from here rather than restating them, so the standard and the checker cannot disagree.

---

## Purpose

A run that finds work it cannot do has two options: report it into a session that then ends, or
record it somewhere the next run will look. Only the second survives. The list exists so a deferral
outlives the context that produced it, and each entry carries enough to be started from a **cleared**
context — the reader is not assumed to have been present when the item was raised.

---

## Location & Tier

**One list, one tier:** `context/project/TODO.md`.

There is no per-repo TODO. A deferral often spans repos, and filing it under one would demand a
routing decision before the item is even readable; an entry names the target it concerns in its own
`Context` instead. Exactly one file, at the project tier, like `context/project/requirements/`.

---

## Front-Matter

Exactly two lines, mirroring `REQUIREMENTS.md`:

```markdown
<!-- todo: project -->
<!-- updated: YYYY-MM-DD -->
```

- `todo` — the literal `project`; the file has no other tier.
- `updated` — the date of the most recent write, `YYYY-MM-DD`.

---

## Entry schema

**Body:** `# Deferred Work` followed by a sequence of entries. Each is a `## <Title>` heading, then a
fixed field block, then `**Context:**`:

```markdown
## <Title>

**Run:** /mfix
**Kind:** spec-drift
**Origin:** CHANGE-029
**Raised:** 2026-08-08
**Priority:** medium
**Risk-if-unfixed:** low
**Regression-risk:** low
**Cost:** low
**Context:** <what the problem is, where it lives, what was already tried, what closing it requires>
```

| Field | Value |
|---|---|
| `Run` | who should pick this up — `/mquick`, `/mreq`, `/mspec`, `/mfix`, `/mreverse`, or `human` |
| `Kind` | `logic`, `edge-case`, `error-handling`, `build`, `compile`, `packaging`, `deployment`, `test-coverage`, `spec-drift`, `architecture`, `performance`, `security` |
| `Origin` | the `CHANGE-<NNN>`, `PROJECT-CHANGE-<NNN>` or plan id the deferral came from |
| `Raised` | `YYYY-MM-DD` |
| `Priority` | `high` \| `medium` \| `low` |
| `Risk-if-unfixed` | `high` \| `medium` \| `low` |
| `Regression-risk` | `high` \| `medium` \| `low` — the risk that fixing it breaks something |
| `Cost` | `high` \| `medium` \| `low` — estimated agentic AI coding cost |
| `Context` | enough to begin from a cleared context: what the problem is, where it lives, what was already tried, and what closing it requires |

**All nine fields are required.** There is no optional field and no default: an entry missing one is
malformed, not partially filled.

**`Run` and `Kind` are closed enums**, as are the four ratings — six members and twelve members
respectively, exactly as tabled above, and `high|medium|low` for each rating. A value outside the set
fails rather than inventing a category or routing an item nowhere: an unrecognised `Kind` would
silently create a thirteenth category nothing queries, and an unrecognised `Run` would file the item
against a skill that never reads for it.

**`human` is the one `Run` value that is not a skill**, and it carries no slash for exactly that
reason. The other five are commands an agent invokes; `human` names work no agent can perform — a
plugin install, a credential rotation, an approval only a person can give — and its own grammar says
so, so a reader cannot mistake it for something to run. Before it existed, such an item carried
whichever of the five was least wrong, which routed it to a skill that would pick it up and fail to
close it on every pass: the routing was not untidy but wrong.

Ruling human-only work off this list instead was considered and rejected. Any destination would have
to survive a cleared context, which is the whole premise of this list, so a second home would either
duplicate this schema or be a worse version of it.

**A prose deferral is a deferral.** The obligation to file attaches to the **act** of deferring, not
to the shape the run happened to record it in. A skill that defers through a named result field — a
`spec_defect`, a `deferred_break`, a stop reason — and one that defers in a sentence of its own prose
incur the same obligation, and neither is exempt for lacking a field to key on.

This is stated because the opposite was assumed once, and the assumption was invisible. Enumerating
the skills that defer by looking for their named fields returned five, and five looked like all of
them: `mmigrate` deferred at five separate points and `mreverse` at one, each in prose only, and each
called `todo add` zero times. The coverage read as complete precisely because the method that built
it could not see what it was missing. A rule stated without its cause reads as a preference, so the
cause is recorded here with it.

**`Origin` must resolve.** The named change document or plan has to exist on disk — the same
obligation `req change-close` places on the `CHANGE-<NNN>` it writes. A dangling origin is a finding,
not a plausible-looking string, because an entry whose provenance cannot be opened is an entry
nobody can judge.

---

## Resolution Is Removal

A fixed item is **deleted** from the file. It is not marked closed, moved to a closed section, or
struck through — so there is no `Status` field and no closed section anywhere in this schema. The
list holds outstanding work and nothing else, which is what makes it cheap enough to read in full
every time.

The record of what was fixed lives in the `CHANGE-<NNN>` that fixed it and in git history. That is
what makes deletion safe, and it is conditional: an **unversioned** list may not be pruned this way,
because deleting from one destroys the only copy. Under version control the deleted entry is still
recoverable, and the diff that removed it names the commit that closed it.

---

## The Four Ratings Are Annotations, Never Inputs

`Priority`, `Risk-if-unfixed`, `Regression-risk` and `Cost` describe an item for a human or a skill
reading the list. Nothing in the system may be **gated**, deferred, ordered or reordered on them.

`REQ-024-stop-runaway-cost` forbids gating on a cost predicted before the work begins, and `Cost`
here is exactly such a prediction; the other three are the same kind of guess about work not yet
started. Treating any of them as an input would make a guess act like a measurement.

What a checker can confirm, and what it cannot:

| Checkable | Not checkable |
|---|---|
| The rating is present | The rating is *right* |
| Its value is in the enum | That two entries rated `high` are comparably urgent |
| It is spelled as tabled | That a `low` cost estimate will hold once work starts |

The standard says so plainly rather than letting the fields read as guarantees. A rating the checker
accepts has been confirmed well-formed and **never** confirmed accurate.

---

## Example

```markdown
<!-- todo: project -->
<!-- updated: 2026-08-08 -->

# Deferred Work

## check handoff reports no per-repo counts

**Run:** /mfix
**Kind:** spec-drift
**Origin:** CHANGE-029
**Raised:** 2026-08-08
**Priority:** low
**Risk-if-unfixed:** low
**Regression-risk:** low
**Cost:** low
**Context:** `check handoff` in plugins/metacoder/tools/check.py aggregates across repos, but
TOOLS-INTERFACE.md documents a per-repo breakdown. Tried scoping by the ledger's repo list; the
handoff frame does not carry one. Closing it needs either the frame extended or the spec corrected.
```
