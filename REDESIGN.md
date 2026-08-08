# metacoder v2 — Design Specification

Replaces the current ten-skill workflow. This document is the thing to build from; rationale lives
here so it does not live in a `SKILL.md`.

## 1. Purpose and success criteria

Build applications **correctly the first time**, **token- and time-efficiently**, across **one or
several repositories with interdependencies**.

A change to this design is justified only if it improves one of these four, measured:

| Criterion | Measure |
|---|---|
| Correct first time | slices whose acceptance passes on the first `mship` attempt |
| Token efficient | instruction tokens loaded before the first line of user code is read |
| Time efficient | wall-clock from request to first demonstrably-working increment |
| Multi-repo | a cross-repo contract change delivered without code-level atomicity |

Nothing in v2 exists to make the *process* auditable. Process auditability is what produced 39,585
lines of bookkeeping for one repo; it is not a goal.

## 2. The spec is an execution artifact

The decision that shapes everything below. A spec's only jobs are:

1. be sufficient for a story agent to build a module against, and
2. be sufficient for `mverify` to check shipped code against.

It is **not** a design record, not a requirements trace, and not a description of internals. So:

- **Three facets become two.** `<TAG>-OVERVIEW.md` (what the module is, ~30 lines) and
  `<TAG>-INTERFACE.md` (the contract: types, signatures, endpoints, events, CLI surface). Types fold
  into INTERFACE — a type that never crosses the module boundary is an implementation detail and does
  not belong in a spec at all.
- **`IMPLEMENTATION`, `TESTING`, `DEPENDENCIES` are deleted.** `IMPLEMENTATION` was prose describing
  code the agent was about to write. `DEPENDENCIES` duplicated the package manifest and the
  `depends-on` front-matter. `TESTING` is superseded by the slice acceptance command (§4).
- **`depth` is deleted**, and with it `spec depth`, `/mspec deepen`, `mship`'s deepen step,
  `mplan --slice`, and `mverify`'s contract-depth false-positive carve-out. A spec is contract-only,
  permanently. There is no second state to be in.

Cross-module agreement is settled by the walking-skeleton slice *running*, which is stronger evidence
than two prose documents agreeing.

## 3. Artifact model

```
<workspace>/
├── repos/<repo>/                     # source
└── context/
    ├── <repo>/spec/                  # CATALOG.yaml + <TAG>/{OVERVIEW,INTERFACE}.md
    ├── shared/spec/                  # <IFACE>/{OVERVIEW,INTERFACE}.md — cross-repo contracts
    └── project/
        ├── changes/CHANGE-<NNN>-<slug>.md    # ONE tier, spans repos
        ├── plans/<NNN>-<slug>/               # plan.yaml + state.yaml + PLAN-*.md
        ├── state.yaml                        # ledger
        └── out/<plan-id>/                    # reports
```

**Change documents collapse to one tier.** A change to a multi-repo system is one change; sequencing
it per repo and then writing an index to re-join the pieces was bookkeeping invented to undo a split
that had no reason to exist. One `CHANGE-<NNN>` at the project tier, with a repo column in its
Affected table. This deletes `change index-resolve`, the continue-vs-create reconciliation across two
tiers, `check handoff`'s stranded-index rule, and half of `STANDARD-CHANGE.md`.

**Deleted outright:** the requirements tier (`REQUIREMENTS.md`, `REQ-CHANGE` records, `req` verb
group, `check requirements`, two schemas, mnemonic derivation) · `context/project/TODO.md` and the
whole deferral apparatus (`todo` verb group, `check todo`, `state sweep`, `--filed 0`, and routing
tables in six skills) · `CHATFORM.md` · `inconsistency-report` as a separate kind.

Why: **why** belongs in the change document's Summary. A separate tier with its own id grammar,
change-record lifecycle, closure verb, and audit branch is a documentation product. And a deferral
list is the workflow's own unfinished business — a conformance report is a file, it persists, and it
is already the record of what is outstanding.

## 4. Acceptance-first, and it must go red before it goes green

The highest-leverage correctness change in v2, and it costs one field and one refusal.

Today a slice's acceptance carries `surface: delivered`, and `plan emit` "can count the annotation and
cannot judge whether it is honest." So a unit test beneath the delivered surface, mislabelled, ships a
slice that reports itself demonstrated and is not.

**v2 makes the annotation checkable by executing it twice:**

1. **Arm, before the slice runs.** `mship` runs the slice's `surface: delivered` acceptance command
   and records the exit code:

   ```
   mc.py state set-slice <plan-id> <NN> --armed <exit-code>
   ```

   **The verb refuses exit code `0`.** An acceptance that already passes before any work is done
   either does not reach the behaviour the slice delivers, or the slice delivers nothing. Both are
   defects in the cut, caught before a single token is spent building it.

2. **Confirm, after the slice's last barrier.** Same command:

   ```
   mc.py state set-slice <plan-id> <NN> --status applied --acceptance pass --confirmed <exit-code>
   ```

   **The verb refuses a non-zero code paired with `pass`.**

Red → green is the demonstration. `surface: delivered` stops being a claim and becomes a measurement.
Authoring the acceptance command is therefore part of cutting the slice, at `mspec`'s gate, and a
slice whose command cannot be named is not yet understood well enough to build.

The tool records; `mship` executes. `mc.py` still runs no user command and no git command.

## 5. Five skills

| Skill | Owns | Budget |
|---|---|---:|
| `/mspec` | target, clarify gate, contract spec, change doc + slice cut + acceptance commands, shared cascade | 3,500 |
| `/mplan` | change → `plan.yaml`, `state.yaml`, ledger entry, story files | 3,000 |
| `/mship` | the delivery loop: arm → execute → barrier → confirm → verify → decide | 6,000 |
| `/mverify` | conformance detection **and** repair | 4,000 |
| `/mreverse` | code → spec on-ramp for existing repos | 2,500 |

**`mexecute` merges into `mship`.** The split existed so something could decide between slices; with
one loop there is one place. `mship` gains the waves, worktrees, barrier, retry, and merge.

**`mfix` merges into `mverify`.** Detect-then-repair is one procedure with one judgment in the middle
(which artefact is authoritative). Two skills meant two change-document disciplines, two deferral
protocols, and a re-verify handshake between them.

**`mquick` becomes `--gate never`.** It was, by its own text, "a thin sequencer adding two pieces of
decision logic." A flag is a flag.

**`mplan` runs once per change, not once per slice.** Per-slice re-planning existed to rewrite stories
against newly-deepened specs; with no depth there is nothing to rewrite against. Re-cutting the
*outstanding* slices when a delivered slice teaches something is still real, and stays `plan reslice`,
`mship`'s call.

### `/mship` — the loop, in full

```
resolve plan → pre-flight (validate, run-increment, integration branches, reconcile worktrees)
for each slice, in order:
    arm            mc.py state set-slice --armed <code>     # refuses 0
    execute        Workflow(ship-slice.js, args)            # waves → worktrees → barrier → merge
    confirm        mc.py state set-slice --confirmed <code> # refuses non-zero with pass
    verify         /mverify --slice <NN>
    decide         one SliceOutcome
```

Decision table, first match wins: `halt` → stop · `acceptance-failed` → stop · `budget` → stop ·
`contract-defect` → cascade · `replannable` → reslice · `gate-asks` → ask · else continue.

### Retry escalates instead of restarting

`RETRY_MAX = 3`, and today each attempt gets a fresh worktree and a clean slate — three full
re-implementations that learn nothing. v2:

| Attempt | Branches from | Prompt additionally carries |
|---|---|---|
| 1 | integration | — |
| 2 | attempt 1's tip | the failing check's command, its captured output, attempt 1's diff |
| 3 | integration (fresh) | both prior diffs and both failure records |

Fix → fix → rewrite-with-knowledge. `worktree names` gains `--from` so the branch point is the tool's
to name, not the skill's to compose.

### The Workflow script ships; it is not authored per run

`mexecute` claims its parallelism, barrier, and retry are "deterministic control flow — not
model-improvised," then hands the model a JS skeleton and says "adapt freely." v2 ships
`plugins/metacoder/workflows/ship-slice.js` as a fixed script:

```
Workflow({ scriptPath: "${CLAUDE_PLUGIN_ROOT}/workflows/ship-slice.js", args: { … } })
```

`mship`'s pre-flight computes every name and structure the script needs (`plan slices`,
`worktree names` per story/attempt) and passes them in `args`, since a Workflow script has no
filesystem access. Removes ~60 lines of JS from the skill and makes the claim true.

## 6. Multi-repo — unchanged, because it is already right

Keep exactly as designed; this is the strongest part of v1 and it is half the stated purpose.

- A repo consumes a contract by declaring it **twice** — `shared_interfaces` in its catalog *and* a
  `depends-on` on the shared `*-INTERFACE.md`. `check coupling` enforces both directions.
- A shared change **cascades**: freeze the contract → `spec revision <IFACE> --bump` → one teammate
  subagent per consuming repo, in parallel, each blind to the others → one change document.
- The barrier records each merged story's `contract_revisions` at merge time.
- `spec consumers <IFACE> --stale` names delivered stories built below the current revision;
  `mship` folds their remediation into the **next** slice. The cascade-then-sweep ordering is
  load-bearing (asking before the bump returns an empty list, silently).
- Cross-repo atomicity is neither needed nor attempted. The contract is the sync point.
- Single-repo: `context/shared/` is simply absent, with no diagnostic.

## 7. Tool surface

~45 verbs → ~31. Deleted groups: `req` (7), `todo` (4), `status` (folded into `plan resolve`).

| Group | Verbs |
|---|---|
| `validate` | catalog, change, plan-graph, plan-state, project-state |
| `spec` | mode, catalog-emit, consumers, revision |
| `change` | resolve, emit, close |
| `plan` | scope, resolve, story-id, waves, slices, reslice, emit, story-emit, shards |
| `state` | run-increment, set-plan, set-story, set-slice, conformance, telemetry |
| `worktree` | names, reconcile |
| `check` | depends-on, coupling, catalog, all |

`state set-slice` absorbs `--armed` / `--confirmed` (§4). `spec depth` is gone.

Schemas: 12 → 7 (`catalog`, `change`, `plan-graph`, `plan-state`, `project-state`, `story-report`,
`finding-report`). `conformance-report` and `inconsistency-report` unify as `finding-report` with a
`kind` discriminator.

## 8. Skill authoring rules — the constitution

The observed failure mode is prose accretion over 226 commits. These rules are what prevent it, and
**they are enforced by a test, not by discipline.**

1. **No rationale in a `SKILL.md`.** Why a design beat its alternatives goes in this file. A skill
   states what to do. Delete every "Rejected —", "was retired", "is not a fourth", "and only three",
   "the trade was taken because".
2. **State each rule exactly once.** If the tool enforces it, the skill gets one line naming the
   refusal and does not restate the rule. `mplan` currently states its two slice rules three times
   and then admits the third statement is not performed.
3. **No "does NOT do" section** beyond three bullets, and only where an agent would plausibly
   overstep.
4. **No back-compat prose.** Schemas carry versions; the tool migrates or refuses. No skill describes
   a state that no artifact is in.
5. **Each boundary is described once**, in the skill that owns the write — never from both sides.
6. **No worked examples longer than the rule they illustrate.**

### The budget is a test

`tests/test_budget.py` fails when any shipped file exceeds its budget:

```python
BUDGETS = {  # bytes; ~4 bytes/token
    "skills/mspec/SKILL.md": 14_000,   "skills/mplan/SKILL.md":    12_000,
    "skills/mship/SKILL.md": 24_000,   "skills/mverify/SKILL.md":  16_000,
    "skills/mreverse/SKILL.md": 10_000,
    "shared/STANDARD-SPEC.md":   8_000, "shared/STANDARD-CHANGE.md": 6_000,
    "shared/PLAN-STORY-TEMPLATE.md": 5_000,
}
TOTAL_RUNTIME_BUDGET = 96_000   # ~24k tokens, all skills + standards
```

A skill that needs more than its budget needs a smaller job, not a bigger budget.

## 9. What this costs, in tokens

| Path | v1 | v2 |
|---|---:|---:|
| `/mspec` update (skill + standards) | 21,085 | 7,000 |
| Full autonomous run, cumulative instruction | 66,840 | ~20,000 |
| All runtime-loadable prose | 87,783 | 23,700 |

~70% of the process description is deleted. None of the deleted text is a check that runs.

## 10. Migration

**None.** `context/` is archived to a dated directory, `/mreverse` regenerates each repo's spec at two
facets from code, and history stays in git. The existing tree holds 15 plans, 118 story files, a
requirements tier, and a TODO list, for one workspace with one user — writing a migration tool for
that is the `mmigrate` mistake a second time.

## 11. Build order

Each step ends somewhere runnable; the first three are the walking skeleton.

1. ~~**Schemas + `mc.py` reduction.**~~ **Done (v0.14.0).** `req`/`todo`/`status` deleted, along with
   `spec depth`, `spec layers`, `state sweep`, `check requirements|todo|handoff` and the catalog's
   `depth`/`requirements` fields: ~45 verbs → 31. Twelve schemas → seven (`conformance-report` +
   `inconsistency-report` → `finding-report`, discriminated by `pass`). `state set-slice` gained
   `--armed`/`--confirmed` and the three refusals of §4, each covered by a test asserting **nothing
   is persisted** on refusal. `mreq`/`mmigrate`/`mquick` and `STANDARD-REQ`/`STANDARD-TODO`/`CHATFORM`
   removed, since their tooling no longer exists. `tests/test_budget.py` added as a **ratchet** on the
   runtime surface — it may be lowered and never raised — with every not-yet-rewritten file listed
   against the build step that closes it. Suite: 986 passed, 2 skipped, 1 `xfail`.

   The `xfail` is `test_every_documented_invocation_names_a_registered_verb`, and it is honest rather
   than convenient: the surviving skills still document verbs this step deleted. It is `strict=True`,
   so it fails the moment step 3 makes it pass and cannot linger.
2. **`STANDARD-SPEC` at two facets + `STANDARD-CHANGE` at one tier**, and `spec catalog-emit` /
   `check catalog` following them.
3. **`/mspec` + `/mplan` rewritten to budget**, producing a plan whose slices carry armable
   acceptance commands. Demonstrated by: a plan emitted for a two-repo toy workspace where
   `set-slice --armed 0` is refused.
4. **`ship-slice.js` + `/mship`** — waves, worktrees, barrier, escalating retry, arm/confirm.
5. **`/mverify`** with the repair phase folded in.
6. **`/mreverse`** trimmed to two facets.
7. **`tests/test_budget.py`** wired into CI, and this file becomes `DESIGN-NOTES.md`.

**Validate on a real application, not on this repo.** Every design decision in v1 was tested against
markdown and Python with no runtime, no UI, no database, and no deploy. The acceptance-first mechanism
in §4 is worth nothing until a slice's acceptance command has had to drive a real delivered surface.
Pick a two-repo application with a service and a client, and build it with v2 before v2 is trusted.
