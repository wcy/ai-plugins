---
name: mship
description: Use when a plan should be delivered one slice at a time, deciding after each slice what the remaining slices should be — triggers on phrases like "deliver this slice by slice", "run the loop", "ship it incrementally", "deliver the plan a slice at a time", "mship", or whenever a plan under context/project/plans/ carries slices and the next one should run. It is the loop the other skills run inside: every step is an invocation of mspec, mplan, mexecute or mverify, and it writes no spec, plan, code or conformance finding of its own.
---

# Ship: Deliver a Plan One Slice at a Time

`mship` delivers a plan **one slice at a time**, and decides after each slice what the remaining slices should be. It writes nothing itself beyond its own run record, the slice dimension of plan state, and — on a stop — the deferred-work entry recording it; every step it performs is an invocation of `mspec`, `mplan`, `mexecute` or `mverify` at that skill's normal entry point.

The loop is the point. Without it the pipeline can *detect* a wrong approach and cannot *respond* to one, so the cost of being wrong stays independent of when it was noticed. With it, what the first slice teaches is spent on the slices that have not been built yet.

**What it owns:** the order slices run in · the **gate policy** (which boundaries a human is asked about, a parameter and not a fixed behaviour) · the **budget breaker** · **re-planning** what is still outstanding, including an `mspec` cascade and the remediation a cascade schedules.

**What it never does:** write spec, plan, code or conformance findings — those belong to `mspec`, `mplan`, `mexecute` and `mverify`, each invoked rather than reimplemented · rewrite a slice already delivered · decide a plan's slices in the first place. That cut is `mspec`'s, made behind `mspec`'s approval gate and recorded in the change document's `## Slices` table; this skill consumes it.

## Invocation

```
/mship [<plan-id>] [--gate unverifiable-only|every-slice|never] [--from-slice <NN>]
```

| Argument | Default | Effect |
|---|---|---|
| `<plan-id>` | whatever `mc.py plan resolve` names | Which plan to deliver |
| `--gate` | `unverifiable-only` | The `GatePolicy` applied at every slice boundary |
| `--from-slice <NN>` | the first slice that is not `applied` | Where to resume |

## Invoking the Tool

Every mechanical step below runs `python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py`. Add `--json` when you want the `Result` envelope rather than the human-readable form. **Invoke it; never restate it** — a prose re-derivation of a step the tool owns is a second implementation of it.

Read the exit code:

- **`0`** — succeeded, no diagnostics.
- **`1`** — the command ran and reported diagnostics: a schema violation, a check finding, an unresolvable resource, or a refusal. This is a **result to read at the step that asked for it**, not a failure.
- **`2`** — usage error, bad identifier, path escape, a missing runtime prerequisite (`PyYAML`/`jsonschema`), or unparseable input. This is a **failure**: halt the loop and report it.

Yours alone: the `SliceOutcome` at each boundary, what a re-plan should contain, and whether a contract is wrong enough to cascade. The tool computes facts; those three are judgments and nothing below delegates them.

## The Types

```typescript
// How much of a slice boundary the developer is asked about.
type GatePolicy =
  | "unverifiable-only"   // default, and mquick's setting: ask only where
                          // acceptance cannot be obtained by running something
  | "every-slice"         // ask at every boundary, even a mechanically-confirmed one
  | "never";              // ask at none; an unverifiable acceptance is recorded
                          // unconfirmed and named in the run report

// What mship decides at a slice boundary. Exactly one is taken.
type SliceOutcome =
  | { kind: "continue" }                                   // acceptance passed, nothing outstanding
  | { kind: "ask"; question: string; artifacts: string[] } // acceptance needs a human eye
  | { kind: "replan"; reason: string; draft: SliceDraft[] }// outstanding slices are rewritten
  | { kind: "cascade"; iface: string; reason: string }     // a shared agreement must change
  | { kind: "stop"; reason: StopReason };                  // the run ends and reports

type StopReason =
  | "budget"              // this slice's cost departed sharply from earlier slices'
  | "halt"                // mexecute hit one of its three halt conditions
  | "acceptance-failed";  // the slice ran but did not demonstrate its behaviour

// A slice as mspec declared it and mplan recorded it. mship rewrites only
// entries whose status is still pending.
interface SliceDraft {
  id: string;             // ^[0-9]{2}$ — position in the plan, 00 being the walking skeleton
  name: string;
  behavior: string;       // what completing it makes work, end to end
  acceptance: AcceptanceStep[];
  modules: string[];      // TAGs it touches, across every repo in the plan
}

interface AcceptanceStep {
  kind: "exit-code" | "prose";
  description: string;
  command?: string;       // required when kind is exit-code
}

// Recorded per slice, and what the budget breaker compares against.
interface SliceTelemetry {
  slice: string;
  cost_usd: number | null;
  tokens: number | null;
  wall_clock_s: number | null;
}
```

**On the wire, a draft is written in the plan graph's own slice shape**, which is what `mc.py plan reslice` reads on stdin and what `plan-graph.schema.json` admits: `slice` (the `id` above), `name`, `behavior`, `acceptance`, and `stories` — the story ids covering the slice's modules. The graph's slice object allows **no other key**: a draft naming the slice `id` is refused for having no slice id at all, and one carrying `modules` is refused by schema validation. Either way nothing is written. Translate once, on the way out.

## The Per-Slice Sequence

For each slice from the starting one, in order, perform exactly these five steps. Each is an invocation of another module's documented entry point; none is reimplemented here.

| Step | Invokes | Purpose |
|---|---|---|
| 1. Deepen | `mspec` deepen entry | Raise this slice's modules from `contract` to `full` depth |
| 2. Plan | `mplan --slice <NN>` | Emit or refresh the stories of this slice alone |
| 3. Execute | `mexecute --slice <NN>` | Ship them as worktree-isolated waves, ending in slice acceptance |
| 4. Verify | `mverify --slice <NN>` | Conformance over what this slice shipped |
| 5. Decide | — | Produce one `SliceOutcome` |

### Step 0: Resolve the plan, the gate and the starting slice

```
python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py plan resolve [<plan-id>]
python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py plan slices <plan-id>
```

`plan resolve` names the plan, its status, the run counter, the resume wave and the **resume slice**; `plan slices` returns the slices in order, each with its `acceptance` steps, its `stories` and its current `status`. Start at `--from-slice` when one was given, at `resume_slice` otherwise. Never read the slice list off `plan.yaml` yourself.

The gate policy comes from `--gate`, defaulting to `unverifiable-only`. It applies at **every** boundary of the run; there is no per-slice override, because a gate that varies by slice is one nobody can predict.

### Step 1: Deepen — ask the catalog, do not infer

```
python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py spec depth <target> <MODULE>
```

For each module this slice touches, ask the tool for its depth. An unwritten IMPLEMENTATION file is indistinguishable from an absent one by looking, which is why the catalog is the authority. Invoke `mspec`'s deepen entry (`/mspec deepen <target> <MODULE> …`) with the modules the call reported `contract`, then check afterwards that every module of the slice reports `full`.

**`mship` writes no facet and never flips the depth field.** Both writes are `mspec`'s, under `mspec`'s own conformance rules; this skill supplies the module list and checks the result.

**A slice whose modules are all already `full` skips the step silently.** That is the whole of the backward-compatibility story for depth: a catalog written before `depth` existed reports `full` for every module, so a plan carried over from before spec depth runs with step 1 never firing.

### Steps 2–4: Plan, execute, verify this slice

- **`/mplan --slice <NN>`** refreshes that slice's stories against the graph that already exists, now that its modules carry detail they did not have when the graph was emitted. It does not re-emit the graph and does not disturb another slice.
- **`/mexecute --slice <NN>`** ships the slice's stories and no others: waves, barrier, bounded retry, then the slice's **Slice Acceptance** — the demonstration that the behaviour works, recorded through `mc.py state set-slice --acceptance`. It returns the acceptance result, every `spec_defect` its story agents reported, any halt, and the run's telemetry.
- **`/mverify --slice <NN>`** runs the conformance sweep over what the slice shipped, in `sweep` mode, and returns `{status, report, findings, deferred}`. Drift never halts the loop; it is an input to the decision.

## Step 5: The Decision

Evaluated **in this order; the first rule that applies wins.** A slice matching several rules yields the earliest, never a combination and never a question about which to prefer.

| # | Condition | Holds when | Outcome |
|---|---|---|---|
| 1 | `halted` | `mexecute` halted — retry exhausted, an unmergeable wave, or a significant breaking contract change | `{stop, "halt"}` |
| 2 | `acceptance-failed` | the slice ran but its acceptance did not demonstrate the behaviour — an `exit-code` step exited non-zero | `{stop, "acceptance-failed"}` |
| 3 | `budget-fired` | the budget breaker fired on this slice's cost | `{stop, "budget"}` |
| 4 | `contract-defect` | a story reported a `spec_defect` naming a contract that must change | `{cascade, iface, reason}` |
| 5 | `replannable` | a story reported a `spec_defect` re-planning can absorb, or `mverify` findings imply the remaining cut is wrong | `{replan, reason, draft}` |
| 6 | `gate-asks` | the gate policy asks at this boundary — `every-slice` at all of them, `unverifiable-only` where any acceptance step is `kind: prose`, `never` at none | `{ask, question, artifacts}` |
| 7 | `otherwise` | none of the above held | `{continue}` |

**A halt outranks a concurrent prose acceptance.** A slice that both halted and carries a prose acceptance step is `{stop, "halt"}` — rule 1 — and the developer is told the run stopped, not asked to judge the output of a slice that never finished. The same precedence settles every other overlap: a failed acceptance in a slice that also reported a `spec_defect` stops rather than re-plans, because re-planning around work that did not run is planning against nothing.

**Rules 1–3 are outcomes reported, not questions asked.** Each ends the run with a report naming the `StopReason`; none of them waits on anybody, which is what keeps them outside `REQ-008`'s promise of a single request path.

**Rule 6 is the only hand-back in this skill, and the whole of `REQ-008`'s exception.** A slice whose acceptance is entirely `kind: exit-code`, run under the default gate, never reaches it — `mplan` requires at least one runnable acceptance step per slice precisely so the common path is unattended. When it does fire, the question **names the artifacts and what needs judging, and nothing else**: an open-ended "does this look right?" makes the developer re-derive the acceptance criterion the slice already carries. Present it, wait, and then continue or stop on the answer.

### Every stop writes an entry to the TODO list

A stop ends the run, and the session that produced it ends with it. **Each of the three stop reasons writes an entry to `context/project/TODO.md` before returning** — a stop is the clearest case of work the run could not resolve and the most expensive to rediscover, and the run has already paid the cost of finding it. Record the outcome first (`state set-slice --outcome`), then write the entry, then report:

```
python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py todo add \
    --title "Slice <NN> <slice-name>: <the stop in one line>" \
    --run <skill> \
    --kind <kind> \
    --origin <plan-id> \
    --priority high --risk-if-unfixed <r> --regression-risk <r> --cost <c> \
    --context "<which slice stopped, what it was measured against, and what closing it requires>"
```

| Stop | `--run` | `--kind` | What `--context` carries beyond the slice |
|---|---|---|---|
| `{stop, "halt"}` | `/mspec` when the halt was a significant breaking contract change, `/mquick` for retry exhaustion or an unmergeable wave | `architecture` for the contract case, `logic` otherwise | which of `mexecute`'s three halt conditions fired, the story or wave it fired on, and the path to `mexecute`'s own report |
| `{stop, "acceptance-failed"}` | `/mfix` | `logic` | the acceptance step that failed — its `description`, and for an `exit-code` step the `command` and the code it exited — and what would have to be true for it to pass |
| `{stop, "budget"}` | `/mspec` | `architecture` | this slice's `cost_usd`, the median it was compared against, how many completed slices formed the sample, and that what needs settling is the size of the remaining cut |

**A stop whose resolution no agent can perform routes `human`, whichever row it came from.** An unmergeable wave needing a person to reconcile it by hand, a halt waiting on a credential or an install — the row above names the skill that would act *if a skill could*, and `human` overrides it when none can. Routing such a stop to a skill files an item that skill will pick up and fail to close on every pass, which is the defect `human` exists to remove.

**The closing report names human-routed items separately from the rest.** Outstanding work a later run will pick up and outstanding work waiting on a person are different states, and presenting them in one list reads as queued when the second is not — nothing will move it until somebody acts.

**`--run` routes to a skill that can actually act on the stop**, which is why a halt splits by its cause: only `mspec` can change a contract, and re-delivering work that merely failed to land is the loop's own job re-entered. **`--kind` follows the cause too** — a stop is not a category of its own. **`--origin` is the plan id**, so a reader can open the run that raised the item. **`--priority` is `high` on every stop**: the run ended on it and nothing downstream proceeds until it is settled. The remaining three ratings are judgements about the work the entry names, and the standard's warning applies — they annotate an item, and nothing in the loop is ordered, deferred or gated on them.

**`--context` is written for a cleared context.** The reader was not here, has not seen the run, and cannot be sent to a transcript: name the slice, what it was measured against, and what closing it requires, in that order.

**`{ask, ...}` writes no entry.** Rule 6 is a question put to a developer who is present and the run continues on their answer — it is a hand-back, not unresolved work, and an entry for it would file an item that is closed before anybody reads the list. `{replan}`, `{cascade}` and `{continue}` write none either: each is work the loop is about to do itself.

**Do not compose an entry by hand and do not restate its enums here.** `STANDARD-TODO.md` owns the field set and `todo add` applies it, `check todo` checks it, and `check handoff` reports the open ones. A refusal is exit `1`, writes nothing, and is surfaced in the closing report alongside the stop; it never converts the stop into a different outcome, because the run stopped whether or not the entry landed.

### The gate policy

| `--gate` | Asks at | An acceptance no command can settle is recorded |
|---|---|---|
| `unverifiable-only` | boundaries where an acceptance step is `kind: prose` | `pass` or `fail`, on the developer's answer |
| `every-slice` | every boundary, even a mechanically-confirmed one | `pass` or `fail`, on the developer's answer |
| `never` | no boundary | **`unconfirmed`** |

**Under `--gate never` an unverifiable acceptance is recorded `unconfirmed` and named in the closing report. It is never silently treated as passed.** `unconfirmed` is a value `plan-state.schema.json` and `slice-report.schema.json` both carry, deliberately distinct from both `pass` and `fail`, so no report can present an unlooked-at slice as demonstrated. The exit-code steps of that slice still run and are still reported; `unconfirmed` covers the prose steps alone.

### The budget breaker

The comparison is against the **observed cost of earlier slices in this same plan**, never an estimate made before the run: `REQ-024` asks for evidence rather than prediction, and a figure quoted for unfamiliar work is prediction. The sample is each completed slice's `SliceTelemetry`, recorded in the run record as that slice finished — `mc.py state`'s slice verb writes status, acceptance and outcome, so the per-slice cost series lives in the run record and is read back from there.

| Parameter | Value | Why |
|---|---|---|
| Baseline statistic | `median` of completed slices' `cost_usd` | A single expensive slice cannot drag it upward the way a mean can |
| Minimum sample | `3` completed slices | Below three there is no baseline, so the breaker cannot fire |
| Threshold | `4`× the baseline, at or above | Crude on purpose: it catches an order-of-magnitude departure, not ordinary variance |
| Excluded from the sample | a `cost_usd` of `null` | Telemetry unavailable is not a slice that cost nothing |

A plan whose *first* slice is enormous is not stopped by this — there is no baseline yet, and stopping a runaway first slice is `mexecute`'s halt conditions' job. Firing produces `{stop, "budget"}`: an outcome reported, never a question asked.

The threshold and the minimum sample are deliberately crude. A tighter rule would fire on the ordinary difference between a thin slice and a thick one and would train the developer to ignore it — which is the failure mode `REQ-024`'s "stops and reports" exists to avoid.

### State is written before the decision, never after

```
python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py state set-slice <plan-id> <NN> --status <s> --acceptance <r>
   … decide …
python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py state set-slice <plan-id> <NN> --status <s> --outcome <o>
```

Record the slice's `status` and `acceptance` **first**, with no `--outcome` — the field is `null` until the slice is decided — then decide, then record the outcome. `set-slice` also keeps the ledger's `slices_total`/`slices_applied` in step in the same write, so the two files cannot disagree about how far the plan got.

A run interrupted at the boundary therefore resumes at a slice whose status and acceptance are on record rather than one whose status is ambiguous: `plan resolve` names it as `resume_slice`, the loop re-enters at step 5 with what was recorded, and the slice is decided at most once more. Deciding first and recording afterwards inverts that — the interruption loses the acceptance result and the slice is re-run to recover it.

Each story's `contract_revisions` are recorded by `mexecute`'s barrier at merge time, through `mc.py state set-story --contract-revisions`. `mship` does not write them; it depends on them being there, because they are what `spec consumers --stale` reads.

## Re-Planning

`{replan}` and `{cascade}` both rewrite what is **outstanding**, and neither touches what is **delivered**.

```
python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py plan reslice <plan-id> < draft.json
```

The draft is a JSON array covering **only** slices that are not yet `applied`, in the wire shape given under *The Types*. Compose it from **what the slice just delivered actually taught** — a `spec_defect`'s detail, an `mverify` finding, the remediation list from a stale sweep. Never compose it from a general re-reading of the spec: that is `mplan`'s job, and doing it here discards the reason for re-planning in the first place.

**`plan reslice` refuses a draft that would alter, drop, reorder or renumber a slice whose status is `applied`, and one that would leave a story belonging to no slice or to two.** A refusal persists nothing — both files stay byte-identical. `plan.yaml` is immutable in the sense that mattered: the record of what was delivered cannot be rewritten, while what is merely predicted stays revisable.

**Surface a refusal; never retry it and never work around it.** A refusal means the draft would rewrite delivered work, which is a defect in the draft — report which slice it collided with and re-compose the draft over outstanding slices only.

### Cascade — the ordering is load-bearing

When a slice reveals the shared contract itself is wrong, run the cascade in exactly this order:

1. **Cascade.** Invoke `mspec`'s shared-interface cascade (its Phase 5): it freezes the contract, bumps the interface's `revision` — `mc.py spec revision <IFACE> --bump`, `mspec`'s call and never yours — and writes a change file per consuming repo.
2. **Sweep for stale work.** `python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py spec consumers <IFACE> --stale` names the **delivered stories** whose recorded `contract_revisions[<IFACE>]` is now below the interface's current `revision`.
3. **Reslice.** `python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py plan reslice <plan-id>` with those stories folded into the **next** slice's draft as remediation stories.

**Reversing the first two yields an empty stale list and silently drops the remediation.** `--stale` compares recorded revisions against the *current* one, so asked before the bump it finds every delivered story exactly in step and returns nothing — no error, no diagnostic, no missing exit code. The remediation simply never reaches the draft, and the run continues over code conforming to an agreement nobody holds any more. That is the failure this ordering exists to prevent, and it is invisible if you get it wrong.

Remediation stories are appended to the **next** slice, never edited into the slice that shipped them. The slice that shipped stays as delivered; the work the cascade left out of step is scheduled, not rewritten.

## Outputs

- **`context/project/out/<plan-id>/mship-run.md`** — per slice: what it delivered, what its acceptance proved, its `SliceTelemetry`, and the `SliceOutcome` taken. This file is the per-slice cost series the budget breaker samples.
- **Plan state** — slice status, acceptance and outcome via `mc.py state set-slice`; the run's actuals via `mc.py state telemetry <plan-id>`. Nothing under `context/project/plans/` is ever hand-written.
- **`context/project/TODO.md`** — one entry per stop, via `mc.py todo add`, written before the run returns. This is the only output that outlives the session, which is why a stop writes one and a hand-back does not.
- **A closing report** naming: slices delivered vs. planned · every re-plan performed and why · every cascade run and the revision it moved · the developer questions asked and their answers · **cost per slice** · every acceptance recorded `unconfirmed` · and, if the run stopped, which `StopReason` fired.

## Failure Handling

Every `mc.py` invocation follows the exit-code trichotomy above: `2` halts the loop and reports, `1` is a result to read at the step that asked for it.

**A skill invocation that halts is `{stop, "halt"}`, relayed with the halting skill's own report rather than re-summarised.** `mexecute`'s three halt conditions — retry exhausted, an unmergeable wave, a significant breaking contract change — terminate the loop the same way they terminate a direct `mexecute` run: the user acts and re-invokes. Re-summarising the halt would lose the detail the user needs to act on and would make this skill a second author of a report it did not produce.

## What mship Does / Does NOT Do

- **Does:** iterate a plan's slices in order, running deepen → plan → execute → verify → decide for each; own the gate policy, the budget breaker and re-planning; record slice status, acceptance and outcome before deciding; write every stop to `context/project/TODO.md` through `mc.py todo add` before returning; schedule remediation a cascade created; report per-slice cost and every outcome taken.
- **Does NOT:** write a spec, a plan, code or a conformance finding · rewrite, re-run or edit a slice already `applied` · decide a plan's slices in the first place (`mspec`'s cut, behind `mspec`'s gate) · flip a module's `depth` or bump an interface's `revision` (both `mspec`'s writes) · re-derive by hand any step `mc.py` owns · gate between waves — that is inside `mexecute` and there is no gate there · halt on conformance drift.
