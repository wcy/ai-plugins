---
name: mquick
description: Use when you want the whole spec-to-ship loop run autonomously from one request — triggers on phrases like "just build it", "spec and ship this", "do the whole thing", "mquick", "take this to shipped code", "one-shot this feature", or any time the user wants a change taken from description to validated, conformed code with a single clarification round. Autonomous by name — it clarifies once, then runs mspec → mplan → mexecute to completion.
---

# mquick: Autonomous Spec-to-Ship

`mquick` takes a request from description to **shipped, validated, conformed code** with exactly **one** clarification gate. It is a **thin sequencer over the real skills** — it adds no lightweight forks and no orchestration of its own. It invokes `mspec`, `mplan`, and `mexecute` in order and lets `mexecute` (the one dynamic Workflow) own all the parallelism, worktrees, and retry. This **is** the small-task fast path: a small task simply has little to clarify.

**Autonomy is inherent in the name.** After the Phase A gate, `mquick` runs to completion at **full concurrency with no budget cap and no further gates** — the only legitimate mid-run break is one of `mexecute`'s three halt-and-report conditions.

## Phase A — Clarify (the only gate)

1. **Resolve the target(s) and mode.** Determine which repo(s) or `shared` the request touches and whether it's CREATE or UPDATE — exactly as `/mspec` Step 0 does.
2. **Run `mspec`'s Diagnostic / Clarify stage (Stage 1).** This is the *same* stage a gatekept `/mspec` runs — diagnose the change/product and run the **Risk & Ambiguity Scan** (the ranked list of decisions a human should make: breaking changes, data migration, security, ambiguous requirements, dependency choices). Do **not** write any files (Stage 1 writes nothing).
3. **Surface every question here and iterate.** Present the risk-scan decisions and any ambiguities, and **keep clarifying within this phase until they're resolved.** Further clarification rounds are still Phase A — this stays the *only* gate.
4. **Show the plan preview** — the resolved targets and, for a `shared` change, the cascade (which consuming repos will be touched). Do **not** promise a story/wave/parallelism breakdown or any cost estimate here — those are *actuals* reported in Phase E. **There is no cost gate.**
5. **Get approval to proceed.** Once the user approves, the gate is closed for the rest of the run.

## Phases B–D — Autonomous (no gates)

Run straight through, reusing the real skills exactly as a gatekept operator would, but without stopping:

- **Phase B — `mspec` Write (Stage 2).** Invoke `mspec`'s write stage: spec files → change documents → validation → (for `shared`) the cascade. Produces the `PROJECT-CHANGE-<NNN>` that drives planning.
- **Phase C — `mplan`.** Generate the story files, the `plan.yaml` graph, the initial plan-level `state.yaml`, and the project-ledger entry.
- **Phase D — `mexecute`.** Ship the plan: worktree-isolated waves, barrier merge (agent discretion), bounded retry (N=3), Post-Story/Final validation, two-level state, and the post-ship `/mverify` conformance sweep.

`mquick` itself is a **plain sequential pipeline** over these — it is **not** a dynamic Workflow (that is `mexecute` alone). It has no parallelism or isolation of its own; all of that lives inside `mexecute`.

## Phase E — Report & Escalate

Summarize the whole run: **specced → planned → shipped → validated → conformed**, with the actual story/wave/parallelism breakdown, retries used, the conformance-sweep result, and the run's **actual cost/telemetry** (never a pre-run estimate — there is no cost gate).

Handle the mid-run breaks per `mexecute`'s halt conditions:

- **Significant breaking contract change** (a shared-interface break that cascades beyond the affected story) — `mexecute` **escalates to the user for review mid-run**; relay it, get the decision, and resume.
- **Contained breaking change** (would only break code in the affected story) — **does not** halt; it's deferred here to the report.
- **Retry exhausted** or **unmergeable wave** — `mexecute` halts and reports; surface which story/wave and what's needed.
- **Conformance drift** found by the post-ship sweep — does **not** halt the run; `mquick` **folds it into this report** as an escalation (a gatekept manual workflow would instead leave such drift for the human to act on next via `mspec`/`mreverse`).

## Relationship to the Gatekept Path

`mquick` and the gatekept path (`/mspec` → `/mplan` → `/mexecute` → `/mverify`, reviewing at each hand-off) produce the same artifacts and run the same skills. The only difference is **where the human reviews**: `mquick` collapses all the review into the single Phase A gate and then runs unattended; the gatekept path reviews at every skill boundary. Use `mquick` when you trust the clarified plan enough to let it run; use the gatekept path when you want to inspect each stage.

## What mquick does / does NOT do

- **Does:** clarify once (with the risk scan), then drive `mspec → mplan → mexecute` to shipped, conformed code; report actuals; escalate only the three `mexecute` halt conditions.
- **Does NOT:** add any new spec/plan/execute logic of its own; introduce lightweight forks of the skills; run its own parallelism (that's `mexecute`); gate between waves; gate on cost; halt on conformance drift.
