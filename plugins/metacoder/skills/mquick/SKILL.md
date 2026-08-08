---
name: mquick
description: Use when you want the whole spec-to-ship loop run autonomously from one request — triggers on phrases like "just build it", "spec and ship this", "do the whole thing", "mquick", "take this to shipped code", "one-shot this feature", or any time the user wants a change taken from description to validated, conformed code with a single clarification round. Autonomous by name — it clarifies once, then runs mspec → mplan → mship to completion.
---

# mquick: Autonomous Spec-to-Ship

`mquick` takes a request from description to **shipped, validated, conformed code** with exactly **one** clarification gate. It's a **thin sequencer over the real skills** — no lightweight forks, no orchestration of its own. It invokes `mspec`, `mplan` and `mship` in order, and `mship` owns the delivery loop: slice iteration, re-planning, the budget breaker, and the `mexecute` run that ships each slice. This **is** the small-task fast path: a small task simply has little to clarify.

`mquick` adds **two** pieces of decision logic and no more: the requirements-presence check at Phase A that decides whether `mreq` runs, and the **gate policy** it hands `mship`. Everything else is delegation — including the delivery loop itself.

After the Phase A gate the run proceeds **without further design questions**, at full concurrency and with no budget cap. It is not, however, uninterruptible, and the promise in `REQ-008-one-request-path` is *scoped* rather than absolute: **nothing stops that the run can settle itself.** `mship` hands back at exactly one point — a slice whose acceptance can be judged only by a person — and it stops outright if a slice's cost has run away, if a slice's acceptance failed, or if `mexecute` halted. Those stops are the run **reporting an outcome, not asking a question**, and are outside the promise entirely. An interruption the agent could have resolved itself would regress this path to step-by-step supervision, which is the exact cost it exists to remove.

## Phase A — Clarify (the only gate)

1. **Resolve the target(s).** Determine which repo(s) or `shared` the request touches — exactly as `/mspec` Step 0 does. The CREATE-vs-UPDATE mode is not derived here; it comes from step 2's call.
2. **Ask for the mode and the requirements gate in one call.** Run `python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py spec mode <target>` — it reports the CREATE/UPDATE mode and the target's requirements-gate result in one call. A failed invocation is a **hard error**: halt the phase and report it; there is no prose fallback and neither answer is ever re-derived by hand. **If the mode is CREATE and the requirements gate fails, run `mreq`'s BRAINSTORM Phase 1 first** (product/business-altitude clarify only — the need, who has it, why now, what success looks like; never a technical decision), folded into this same gate, not a second one; otherwise go straight to step 3. This is the one check `mquick` performs itself and the only tool call it makes directly — every other decision and mechanical step is delegated to the skill that owns it (see "What mquick does NOT do" below).
3. **Run `mspec`'s Diagnostic / Clarify stage (Stage 1)** — the same stage a gatekept `/mspec` runs: diagnose the change/product and run the **Risk & Ambiguity Scan** (the ranked list of decisions a human should make: breaking changes, data migration, security, ambiguous requirements, dependency choices, requirements drift). Do **not** write any files (Stage 1 writes nothing).
4. **Surface every question from steps 2 and 3 and iterate.** Present `mreq`'s clarify questions (if step 2 ran), the risk-scan decisions, and any ambiguities; **keep clarifying within this phase until resolved.** Further rounds are still Phase A — this stays the *only* gate.
5. **Show the plan preview** — resolved targets and, for a `shared` change, the cascade (which consuming repos will be touched). Do **not** promise a story/wave/parallelism breakdown or cost estimate here — those are *actuals* reported in Phase E. **There is no cost gate.**
6. **Get approval to proceed.** Once approved, the gate is closed for the rest of the run.

## Phases B–D — Autonomous (no gates)

Run straight through, reusing the real skills, without stopping:

- **Phase B — `mreq`'s BRAINSTORM Phase 2 write (only if step A2 ran), then `mspec` Write (Stage 2).** If Phase A step 2 ran `mreq`'s BRAINSTORM Phase 1, invoke `mreq`'s Phase 2 write first: it appends the confirmed `### REQ-<NNN>-<mnemonic>` entries to `context/<tier>/requirements/REQUIREMENTS.md`, before `mspec`'s write stage, so the requirements tier is populated before `mspec`'s CREATE-mode Prerequisites gate runs — otherwise the gate `mquick` just satisfied in Phase A would immediately re-fail in Phase B. Then invoke `mspec`'s write stage: spec files → change documents → validation → (for `shared`) the cascade. Produces the `PROJECT-CHANGE-<NNN>`, **including its `## Slices` table**, that drives planning and delivery. `mreq`'s inline contradiction check runs inside this write and introduces no gate: with no one to ask, it flags the contradiction into the run's `REQ-CHANGE` record and continues — the same treatment DERIVE gets. This keeps the no-further-gates guarantee true without resolving a contradiction silently or halting the run mid-pipeline.
- **Phase C — `mplan`.** Generate the story files, the `plan.yaml` graph, the initial plan-level `state.yaml`, and the project-ledger entry.
- **Phase D — `mship --gate unverifiable-only`.** Hand the whole plan to `mship` and let it deliver **slice by slice**: for each slice it deepens that slice's modules to `full`, plans that slice's stories, ships them with `mexecute --slice`, verifies them with `mverify --slice`, and then decides what the remaining slices should be — re-planning, cascading or stopping on what the slice taught. `mquick` passes the gate policy and nothing else; it does not iterate slices itself and **never invokes `mexecute` directly**.

### The gate policy is a parameter, not a fork

`--gate unverifiable-only` is the whole of `mquick`'s contribution to Phase D. `mship` runs **identically** however it was invoked — same five steps per slice, same decision table, same budget breaker — and only the boundaries it asks at differ. That is deliberate:

- **Rejected — a `mquick`-specific delivery path.** Driving `mexecute` slice by slice from here would be a second implementation of `mship`. Two implementations of the loop diverge: slice iteration, re-planning and the budget breaker would each exist twice and drift apart, and the bug would show up as one path behaving unlike the other under load.
- **Rejected — folding the loop into `mquick`.** That leaves the gatekept `/mship` path with no loop at all.
- **Taken — one loop, two policies.** Slice iteration, re-planning and the budget breaker exist exactly once, and the gatekept path gets the same loop with a different `--gate`.

Before `mship` existed, `mquick` drove `mexecute` over the whole plan at once, which is precisely why there was nowhere for a decision to happen between increments. Nothing in this skill drives `mexecute` any more; it is named here only as the thing `mship` invokes per slice and as the source of the halt conditions Phase E relays.

Under `unverifiable-only`, `mship` does not hand back for anything it can settle itself — a slice whose acceptance is a runnable command is confirmed and the next slice begins, and because `mplan` requires at least one `exit-code` acceptance step per slice, that is the common path. It hands back only where the acceptance can be judged only by a person, naming what needs judging.

`mquick` itself is a **plain sequential pipeline** over Phases A–E, not a dynamic Workflow: it has **no per-slice iteration, no parallelism and no isolation of its own**. The slice loop lives in `mship`; the parallelism, worktrees, barrier merges and bounded retry (`N=3`) live in `mexecute`, which is still the plugin's one dynamic Workflow.

## Phase E — Report & Escalate

Summarize the run: **specced → planned → shipped → validated → conformed**, with *actuals* — **slices delivered of slices planned**, the story/wave/parallelism breakdown, retries used, every re-plan and cascade `mship` performed and why, any open `REQ-CHANGE` records Phase B flagged, the conformance result, and **cost per slice** (never a pre-run estimate — there is no cost gate).

### Reported, never asked

Each of these ends the run. Relay what `mship` or `mexecute` reported and stop. **None is a question**, so none is an exception to the one-gate promise — a run that ends by failing is not a question put to the developer.

| Reported by | Condition | What `mquick` relays |
|---|---|---|
| `mship` — `{stop, "budget"}` | a slice's cost departed sharply from what earlier slices of the same job cost | which slice, what it spent, and the earlier slices it was measured against |
| `mship` — `{stop, "acceptance-failed"}` | the slice ran but did not demonstrate its behaviour | which slice, and which acceptance step failed |
| `mship` — `{stop, "halt"}` | `mexecute` hit one of its three halt conditions | the underlying condition, per the two rows below |
| `mexecute` — significant breaking contract change | a shared-interface break that **cascades beyond the affected story** | the break, its **blast radius**, and the options — then stop; the user acts on it and re-invokes |
| `mexecute` — retry exhausted, or an unmergeable wave | a story exhausted `N=3` attempts, or a wave would not merge | which story and which wave, and what is needed to move it |

### The one hand-back

**A slice whose acceptance can be judged only by a person** is the sole case where Phase D returns control before finishing. Under `unverifiable-only`, `mship` produces `{ask, question, artifacts}` at that boundary; `mquick` relays **exactly what `mship` asked and which artifacts to look at** — not an open-ended "does this look right?", which would make the developer re-derive the acceptance criterion the slice already carries.

Then **resume the run from the answer**: `mship` continues the loop from that same slice boundary. The run is not restarted, Phase A is not re-opened, and no other gate is introduced. This case exists because no degree of autonomy can answer the question, and it is the scoped exception in `REQ-008-one-request-path` rather than a hole in it.

### An `unconfirmed` slice is never a demonstrated one

Report each slice's acceptance with the value `mship` recorded — `pass`, `fail`, or **`unconfirmed`**. `unconfirmed` is deliberately distinct from both, and it means a prose acceptance step nobody judged. Name every `unconfirmed` slice individually and never present one as demonstrated, delivered-and-proven, or folded into a passing count. The exit-code steps of such a slice still ran and are still reported; `unconfirmed` covers its prose steps alone.

### Outstanding work is relayed in two groups, never one

Phase E names items routed to a skill and items routed `human` **separately**. An item a later run
will pick up and an item waiting on a person are different states: no run can clear the second, so
listing it among work that drains reads as queued when nothing will move it until somebody acts.
`check handoff` draws the same line — `human` entries carry their own code and their own group — and
this report matches it rather than re-flattening what the check separated.

### Non-halting — folded into this report

- **Contained breaking change** (would only break code in the affected story) — **does not** halt; deferred here as a `deferred_break`.
- **A `spec_defect` `mship` absorbed by re-planning** — reported with the re-plan it caused and what the remaining cut became.
- **Conformance drift** from the per-slice `mverify` sweep — **does not** halt the run; folded in here as an escalation. Contrast this with the gatekept path, where the same drift is left for a human to act on via `mspec` or `mreverse`.

## What mquick does / does NOT do

- **Does:** clarify once (with the risk scan, and `mreq`'s brainstorm when a CREATE target has no requirements yet); drive `mreq → mspec → mplan → mship` to shipped, conformed code; report actuals per slice; relay `mship`'s stop reasons (`budget`, `acceptance-failed`, `halt`) and `mexecute`'s halt conditions as outcomes; present `mship`'s one hand-back and resume from the answer.
- **Does NOT:**
  - **own the delivery loop** — that is `mship`'s, and `mquick` neither re-implements it nor bypasses it. **No per-slice iteration lives in this skill**: a loop here would be a second implementation of delivery, and two would diverge;
  - invoke `mexecute` directly;
  - add spec/plan/execute/requirements logic of its own beyond the one presence check at Phase A/B and the gate policy it passes to `mship`;
  - perform the blast-radius test separating a significant from a contained breaking change — that is `mexecute`'s, and `mquick` only relays the verdict;
  - decide a plan's slices — that cut is `mspec`'s, made behind `mspec`'s approval gate;
  - introduce lightweight forks of the skills;
  - run its own parallelism (that's `mexecute`'s);
  - gate between waves, or between slices;
  - gate on a *predicted* cost;
  - halt on conformance drift.
