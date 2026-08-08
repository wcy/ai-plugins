---
name: mfix
description: Use when a conformance report has findings that need resolving — triggers on phrases like "fix the drift", "fix the mverify findings", "reconcile code and spec", "close the conformance report", "fix the warnings from the sweep", or straight after /mverify reports drift. For each finding it decides which artefact is authoritative — the code or the spec — and fixes the one that is wrong, at the root cause.
---

# Fix: Resolve Conformance Findings at the Root Cause

For every finding, answer one question — **which artefact is authoritative, the code or the
spec?** — then repair the one that is wrong. Not always the code, not always the spec; sometimes
both, sometimes neither (missing architecture — hand to `mspec`, see Step 2.4).

**`mfix` writes remediation only — code and spec — never feature work.** Its scope is fenced: it
may only change what a **finding** points at. It does not add features, does not restructure
beyond the finding's blast radius, and does not invent work the report did not raise. A fix that
would grow past that fence is escalated, not taken.

## Invoking the Tool

Every mechanical step — resolving the plan a report belongs to, sequencing the remediation change
document, validating an artefact, checking coupling or `depends-on` correctness — is a single
invocation of `python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py`, never a procedure re-enacted from
prose. **A non-zero exit is a hard error: report the diagnostic and halt the phase.** No prose
fallback exists anywhere in this skill — a fallback would be a second implementation of the step,
and the two would drift.

Not delegated: the judgement. Which artefact is authoritative, the root cause, and the fix are
yours to decide from the evidence the tool returns.

## Step 0: Resolve the Findings

Take findings from, in order of preference: an explicit report path; the `conformance.report`
recorded in a plan's `state.yaml`; or the newest report under `context/project/out/*/`.

Resolve which plan a finding belongs to — and, absent an explicit path, whose `state.yaml`
carries the `conformance.report` — with the tool, then validate the report before trusting any
field:

```
python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py plan resolve [<plan-id>]
python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py validate conformance <report-path>
```

If the user names a subset ("just the blocking ones", "the warnings"), honour it and say which
findings you are leaving.

## Step 1: Verify Each Finding Before Acting

**A conformance report is evidence, not truth.** It was written by an agent reading the same
files you are about to change, and can be stale, wrong, or right for the wrong reason.

For each finding, independently confirm: the `spec_ref` still says what the finding claims, the
`code_path` still does what it claims, and the two genuinely disagree. Findings that fail this
are marked `not-reproduced` with the evidence, and **no edit is made**. Report them — an
over-claiming report is itself worth knowing about.

## Step 2: Decide Code or Spec — The Judgement

This is the skill. Work through it per finding, in this order:

1. **Is the spec describing intended architecture the code drifted from?** → **fix the code.**
   Signals: the spec names a module/layer/boundary and the code is elsewhere; the checker still
   reports the dependency-rule or `depends-on` violation the finding names; `CATALOG.yaml`
   declares an export the code hides. A catalog and a module disagreeing about what is public is
   the code's bug, not the catalog's. Get the first two signals from the tool — the rules are its
   to apply, not yours to re-derive:

   ```
   python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py check coupling <repo>
   python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py check depends-on <repo>
   ```

2. **Is the code a deliberate decision the spec over-promised past?** → **fix the spec.**
   Signals: the behaviour is documented in a docstring or change file with a stated reason; it
   preserves an invariant the change was built on; unifying or tightening it would change
   behaviour that already shipped. A spec claiming a uniformity the implementations never had is
   the spec's bug.

3. **Are both wrong?** → fix both, and say so in the change file.

4. **Is neither authoritative because the contract doesn't exist yet?** → **do not invent one.**
   "No registered interface covers this surface" findings are missing architecture. Defer them:
   make no edit, and record each one on the TODO list at Step 6 with `todo add`, routed to `mspec`
   (`mreverse` for a repo with no spec tree at all). The list is where a deferral is written; no
   spec file is edited to note it.

**Tie-breaker, when both readings look defensible:** ask which choice makes the *next* drift
impossible rather than merely describing this one. Documenting a private module as public API
"resolves" the finding and ratifies the defect; moving the symbol to a public module ends it.
Prefer the fix that removes the failure mode.

## Step 3: Fix at the Root Cause, Not the Symptom

The finding names a symptom. Fix what produced it:

- A stale literal (host, path, version, table name) → the fix is that **one place** reads it
  from the environment or a shared helper, and every duplicate is repointed there. Updating the
  literal just reproduces the finding next time.
- A check that cannot pass (a lint gate that is always red, a type-check that never terminates, a
  test pinned to dead infrastructure) → fix the **configuration or the fixture**. A gate nobody
  can satisfy trains everyone to ignore it — that's how the drift accumulated.
- A guard that stopped guarding (a deny-list keyed on a value that no longer exists) → invert it
  so it **fails closed**. Deny-lists go stale open; allow-lists go stale shut.
- An untested implementation → the fix includes the test, not just the doc.

**Scope discipline.** Repo-wide auto-fixers (`ruff --fix .`, codemods, formatters over the whole
tree) sweep up unrelated pre-existing violations and bury your change. Fix the files the finding
names; a gate that's red for other reasons is its own finding.

## Step 4: Blast Radius Before You Commit

Before applying any fix, work out what else it touches — this is where remediation does damage:

- **Does it change a published package's surface?** Then it needs a version bump, a
  **Breaking Changes** entry per `${CLAUDE_PLUGIN_ROOT}/shared/STANDARD-CHANGE.md`, and
  propagation to dependents. Check whether the bump tool updates *every* dependent, not just
  the one you had in mind.
- **Does it break out-of-tree subclasses or importers?** Grep every repo and worktree, not just
  the obvious consumer. An added `@abstractmethod` breaks any subclass; a moved symbol breaks
  any importer.
- **Is another repo mid-flight on the same files?** Do not fix into a plan's integration branch
  while it is executing.

Land remediation on its own branch off the repo's default branch, never on an in-flight
integration branch.

## Step 5: Apply, Record, Validate

**Code fixes** must leave the repo's own gates green — formatter, linter, type-checker, tests —
and add the test when the finding was "shipped untested".

**Spec fixes** follow `${CLAUDE_PLUGIN_ROOT}/shared/STANDARD-SPEC.md`: concise, correct facet
(contract statements in INTERFACE, algorithms in IMPLEMENTATION, scenarios in TESTING), and
`CATALOG.yaml` updated when an export or module path moves. Don't pad — these files are
context-injected.

**Every run writes a change file** per `STANDARD-CHANGE.md` in `context/<repo>/changes/`, even
when only the spec moved. It records, per finding: the decision, the reason, and — for a spec
fix — why the code was left alone. The tool picks the number and decides create-vs-continue,
continuing an open document rather than allocating a new one exactly when that document's
`status` is still `pending` or `in-progress` and no plan directory corresponds to it:

```
python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py change resolve <repo> --slug <slug>
python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py change emit context/<repo>/changes/CHANGE-<NNN>-<slug>.md \
    --scope repo --repo <repo> --status pending --title "<title>" --plan not-required
```

Pass `--plan not-required`: an `mfix` record documents fixes already applied in this same run, so
it has no further code phase for a plan to reach — see `STANDARD-CHANGE.md` §"No-Plan-Needed
Records". This also means it never needs a project-level index to avoid `check handoff` stranding
**the record itself**: the stranded-change and unplanned-index rules both exempt a
`plan: not-required` document. Write one anyway if the fix is worth surfacing to a human scanning
`context/project/changes/`.

Be clear about what that does **not** settle. `check handoff` raises a *separate* `mverify → mfix`
finding for a plan whose `conformance` block still records findings, and that rule has no
`plan: not-required` exemption — writing this record does not clear it. Step 6 does.

`change emit` writes the front-matter and section skeleton; the per-finding prose is yours. Then
validate what you wrote, and re-run the mechanical checks over any spec you edited:

```
python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py validate change context/<repo>/changes/CHANGE-<NNN>-<slug>.md
python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py validate catalog context/<repo>/spec/CATALOG.yaml
python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py check depends-on <repo>
```

A fix landing **after** the code shipped — a retroactive record — says so in the change file.
Silent reconciliation of a released symbol is how the gap got there.

## Step 6: Re-verify and Report

Re-run `/mverify` over the same scope and confirm each finding is closed. **Invoke it in
`standalone` mode**, passing the plan id — `mverify` takes its mode discriminator explicitly and
never infers it, and the mode decides whether state is written: `standalone` against an existing
plan directory rewrites the plan's `conformance` block itself, while `sweep` returns its result to
an invoker for persistence, and there is no `mexecute` run here to persist it.

That rewrite is what closes the `mverify → mfix` handoff finding — the block's *undeferred*
findings reaching zero, not the change document Step 5 wrote. Confirm it landed before reporting
completion.

For findings the mechanical checks raised, confirm closure with the checker rather than by
re-reading the rules:

```
python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py check all <repo>
```

Findings that survive are reported with why; don't edit twice hoping the report changes.

**A deferred finding is closed by recording it, not by fixing it.** Re-verification re-detects a
deferral — the finding is still true — so the `conformance` block never returns to zero on its own.
Record how many findings this run **deliberately deferred**, and `check handoff` scores
`findings - deferred` instead of `findings`:

```
python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py state conformance <plan-id> \
    --status <clean|drift> --report <report-path> --findings <n> --deferred <n>
```

Run this **after** the standalone re-verify, and pass `--status`, `--report` and `--findings` the
values that run just recorded — the verb writes the `conformance` block whole, so omitting them
drops them. A run that deferred nothing skips the call and leaves the block as `mverify` wrote it.

Recording the count is what makes a deferral terminable. Before `--deferred` existed the handoff
finding stayed raised at `error` severity for as long as the deferral stood, with nothing anyone
could do about it — and a gate nobody can satisfy trains everyone to ignore it, the same failure
mode Step 3 exists to fix and how the drift accumulated in the first place.

**The count is a recorded acceptance, not a suppression.** `mc.py status` reports findings and
deferrals separately, so accepted debt stays visible as debt instead of vanishing from the ledger.
Only a finding Step 2.4 resolved as missing architecture is deferrable — one you merely did not get
to is not deferred, it is unfixed.

**Deferring is this skill's judgement and no other's.** Deciding that a finding may stand is a call
about which artefact is authoritative, which is exactly what `mfix` exists to make: `mverify`
reports findings and never adjudicates one, and `mmigrate` will not touch it either.

**Write every deferral to the TODO list**, one `todo add` per deferred finding, before you report:

```
python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py todo add \
    --title "<the finding in one line>" \
    --run /mspec \
    --kind architecture \
    --origin CHANGE-<NNN> \
    --priority <p> --risk-if-unfixed <r> --regression-risk <r> --cost <c> \
    --context "<what the finding is, the spec_ref and code_path it names, what Step 1 confirmed, and what closing it needs>"
```

`--run` is where a deferred item names the skill that owns it: `/mspec` for a missing contract,
`/mreverse` for a repo with no spec tree. `--kind` is `architecture` for the missing-contract case
Step 2.4 defers. `--origin` is the Step 5 change document, so a reader can open the run that raised
the item. `--context` is written for a **cleared** context — the reader was not here and has not
seen the report.

The verb validates every field before it appends and a refusal writes nothing, so a rejected entry
leaves no half-written list. Do not compose an entry in prose and do not restate its enums here;
`STANDARD-TODO.md` owns the field set and `todo add` applies it.

**The TODO entry is the only record of a deferral.** Do not also write the debt into the module's
own spec. That was the earlier rule and it is retired, not merely supplemented: two records of one
debt need two edits to close, and the one nobody is looking at rots into a claim that is no longer
true.

**Say plainly what that costs**, because it is not free. A module's spec no longer carries its own
known debt, so someone reading `<MODULE>-INTERFACE.md` sees a contract that looks whole while a
finding against it stands open. The only place that says otherwise is `context/project/TODO.md`, and
they only find it if they look there or run `mc.py check handoff`. The trade was taken because a
rotted second copy is worse than a single copy read in one place — but the reader who never opens
the list is the person this change made worse off, and the run report should not pretend otherwise.

The run report lists, per finding: `code` | `spec` | `both` | `deferred` | `not-reproduced`, the
one-line reason, the files touched, and any release/propagation the fix forced. A `deferred` finding
also names the `todo add` entry that now carries it and the skill its `Run` field routed it to.

**Close the change document the run settled.** When every finding the Step 5 record covers is
resolved, deferred, or marked `not-reproduced`, that record is finished — move it off `pending`:

```
python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py change close context/<repo>/changes/CHANGE-<NNN>-<slug>.md \
    --status applied
```

`change emit` writes `pending`, and until this verb existed nothing ever moved a change document
off it — so every shipped change read as outstanding forever and `check handoff`'s pending list
only ever grew. The requirements layer has had a terminator since the beginning; this is the change
layer's. It refuses `complete` (a baseline record's birth status, never a transition), a baseline
record, and a document already terminal — closing twice is a caller bug, not an idempotent no-op.
Leave the record open only when the run genuinely did not settle it, and name the finding still
outstanding when you do.

## What mfix does / does NOT do

- **Does:** verify findings; decide code-vs-spec per finding — that call is `mfix`'s, not the
  user's or the tool's; fix at root cause; write a change file and close it once the run settles
  it; keep gates green; re-verify in `standalone` mode; write each deferral to
  `context/project/TODO.md` through `todo add`; record deferrals as an accepted count.
- **Does NOT:** ship feature code — remediation of a raised finding only, never new functionality
  (that's `mexecute`); refactor beyond a finding's fence; author `context/shared/spec/` contracts
  (that's `mspec`); generate a spec tree for a repo that has none (that's `mreverse`); re-plan
  (`mplan`); note a deferral in the affected module's spec — the TODO entry is the whole record;
  re-derive a mechanical step `mc.py` owns or carry on past a failed invocation; publish or push
  without explicit authorisation.

## Asking Questions

`mfix` decides code-vs-spec **itself** — that judgement is the skill, and handing the user a menu
of findings defeats it. Escalate only when:

- the fix requires **new architecture** (a contract that doesn't exist), or
- two defensible readings imply **materially different blast radius** (e.g. one is a spec edit,
  the other forces a release and a change across consuming repos), or
- an outward-facing action is required — **publishing, pushing, or deleting** — confirmed unless
  already authorised.
