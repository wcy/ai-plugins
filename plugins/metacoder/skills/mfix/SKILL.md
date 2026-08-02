---
name: mfix
description: Use when a conformance report has findings that need resolving — triggers on phrases like "fix the drift", "fix the mverify findings", "reconcile code and spec", "close the conformance report", "fix the warnings from the sweep", or straight after /mverify reports drift. For each finding it decides which artefact is authoritative — the code or the spec — and fixes the one that is wrong, at the root cause.
---

# Fix: Resolve Conformance Findings at the Root Cause

`mverify` detects drift and stops. `mfix` **closes** it. For every finding it answers one
question — **which artefact is authoritative here, the code or the spec?** — and then repairs
the one that is wrong. Not always the code. Not always the spec. Sometimes both, sometimes
neither (the finding is a symptom of missing architecture, which belongs to `mspec`).

**`mfix` writes code and spec.** That makes it the second writing skill alongside `mexecute`,
so its scope is deliberately fenced: it may only change what a **finding** points at. It does
not add features, does not restructure beyond the finding's blast radius, and does not invent
work the report did not raise. If a fix would grow past that fence, it is escalated, not taken.

## Step 0: Resolve the Findings

Take findings from, in order of preference: an explicit report path; the `conformance.report`
recorded in a plan's `state.yaml`; or the newest report under `context/project/out/*/`. Parse
against `${CLAUDE_PLUGIN_ROOT}/schemas/conformance-report.schema.json`.

If the user names a subset ("just the blocking ones", "the warnings"), honour it and say which
findings you are leaving.

## Step 1: Verify Each Finding Before Acting

**A conformance report is evidence, not truth.** It was written by an agent reading the same
files you are about to change, and it can be stale, wrong, or right for the wrong reason.

For each finding, independently confirm: the `spec_ref` still says what the finding claims, the
`code_path` still does what it claims, and the two genuinely disagree. Findings that do not
survive this are marked `not-reproduced` with the evidence, and **no edit is made**. Report
them — a report that over-claims is itself worth knowing about.

## Step 2: Decide Code or Spec — The Judgement

This is the skill. Work through it per finding, in this order:

1. **Is the spec describing intended architecture the code drifted from?** → **fix the code.**
   Signals: the spec names a module/layer/boundary and the code is somewhere else; the code
   violates STANDARD-SPEC's dependency rules (an IMPLEMENTATION importing another module's
   IMPLEMENTATION, a cross-repo reference bypassing `context/shared/spec/`); `CATALOG.yaml`
   declares an export the code hides. A catalog and a module disagreeing about what is public
   is the code's bug, not the catalog's.

2. **Is the code a deliberate decision the spec over-promised past?** → **fix the spec.**
   Signals: the behaviour is documented in a docstring or change file with a stated reason; it
   preserves an invariant the change was built on; unifying or tightening it would change
   behaviour that already shipped. A spec that claims a uniformity the implementations never
   had is the spec's bug.

3. **Are both wrong?** → fix both, and say so plainly in the change file.

4. **Is neither authoritative because the contract does not exist yet?** → **do not invent one.**
   Findings of the form "no registered interface covers this surface" are missing architecture.
   Record them as tracked debt in the relevant spec file and hand them to `mspec`. Writing a
   shared interface is `mspec`'s job and needs its cascade.

**The tie-breaker, when both readings look defensible:** ask which choice makes the *next*
drift impossible rather than merely describing this one. Documenting a private module as public
API "resolves" the finding and ratifies the defect. Moving the symbol to a public module ends
it. Prefer the fix that removes the failure mode.

## Step 3: Fix at the Root Cause, Not the Symptom

The finding names a symptom. Fix what produced it:

- A stale literal (host, path, version, table name) → the fix is that **one place** reads it
  from the environment or a shared helper, and every duplicate is repointed there. Updating the
  literal reproduces the finding on the next change.
- A check that cannot pass (a lint gate that is always red, a type-check that never terminates,
  a test pinned to dead infrastructure) → fix the **configuration or the fixture**. A gate
  nobody can satisfy trains everyone to ignore it, which is how the drift accumulated.
- A guard that stopped guarding (a deny-list keyed on a value that no longer exists) → invert it
  so it **fails closed**. Deny-lists go stale open; allow-lists go stale shut.
- An untested implementation → the fix includes the test, not just the doc.

**Scope discipline.** Repo-wide auto-fixers (`ruff --fix .`, codemods, formatters over the whole
tree) will sweep up unrelated pre-existing violations and bury your change. Fix the files the
finding names; if the gate is red for other reasons, that is its own finding.

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
`CATALOG.yaml` updated when an export or module path moves. Do not pad — these files are
context-injected.

**Every run writes a change file** per `STANDARD-CHANGE.md` in `context/<repo>/changes/`, even
when only the spec moved. It records, per finding: the decision, the reason, and — for a spec
fix — why the code was left alone. Validate what you write:

```
python3 ${CLAUDE_PLUGIN_ROOT}/schemas/validate.py change-frontmatter context/<repo>/changes/CHANGE-<NNN>-*.md
python3 ${CLAUDE_PLUGIN_ROOT}/schemas/validate.py catalog context/<repo>/spec/CATALOG.yaml
```

A fix that lands **after** the code shipped — a retroactive record — says so in the change file.
Silent reconciliation of a released symbol is how the gap got there.

## Step 6: Re-verify and Report

Re-run `/mverify` over the same scope and confirm each finding is closed. Findings that survive
are reported with why; do not edit twice hoping the report changes.

The run report lists, per finding: `code` | `spec` | `both` | `deferred` | `not-reproduced`, the
one-line reason, the files touched, and any release/propagation the fix forced. Deferred items
name the skill that owns them (`mspec` for missing contracts, `mreverse` for a repo with no spec
tree). **Anything not fixed is written down** — in the spec file it affects, so it reads as known
debt rather than an unnoticed violation.

## What mfix does / does NOT do

- **Does:** verify findings; decide code-vs-spec per finding; fix at root cause; write a change
  file; keep gates green; re-verify; record deferrals.
- **Does NOT:** add features or refactor beyond a finding's fence; author `context/shared/spec/`
  contracts (that is `mspec`); generate a spec tree for a repo that has none (that is
  `mreverse`); re-plan (`mplan`); publish or push without explicit authorisation.

## Asking Questions

`mfix` decides code-vs-spec **itself** — that judgement is the skill, and handing the user a
menu of findings defeats it. Escalate only when:

- the fix requires **new architecture** (a contract that does not exist), or
- two defensible readings imply **materially different blast radius** (e.g. one is a spec edit,
  the other forces a release and a change across consuming repos), or
- an outward-facing action is required — **publishing, pushing, or deleting** — which is
  confirmed unless already authorised.
