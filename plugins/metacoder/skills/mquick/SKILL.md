---
name: mquick
description: Use when you want the whole spec-to-ship loop run autonomously from one request — triggers on phrases like "just build it", "spec and ship this", "do the whole thing", "mquick", "take this to shipped code", "one-shot this feature", or any time the user wants a change taken from description to validated, conformed code with a single clarification round. Autonomous by name — it clarifies once, then runs mspec → mplan → mexecute to completion.
---

# mquick: Autonomous Spec-to-Ship

`mquick` takes a request from description to **shipped, validated, conformed code** with exactly **one** clarification gate. It is a **thin sequencer over the real skills** — it adds no lightweight forks and no orchestration of its own. It invokes `mspec`, `mplan`, and `mexecute` in order and lets `mexecute` (the one dynamic Workflow) own all the parallelism, worktrees, and retry. This **is** the small-task fast path: a small task simply has little to clarify.

**Autonomy is inherent in the name.** After the Phase A gate, `mquick` runs to completion at **full concurrency with no budget cap and no further gates** — the only legitimate mid-run break is one of `mexecute`'s three halt-and-report conditions.

## Phase A — Clarify (the only gate)

1. **Resolve the target(s) and mode.** Determine which repo(s) or `shared` the request touches and whether it's CREATE or UPDATE — exactly as `/mspec` Step 0 does.
2. **If CREATE mode and the target's own requirements tier has no valid `REQ-<NNN>` entry, run `mreq`'s BRAINSTORM Phase 1 first.** Check `context/<repo>/requirements/REQUIREMENTS.md` (or `context/shared/requirements/REQUIREMENTS.md` for a `shared` target) for at least one valid `REQ-<NNN>` entry — file existence alone doesn't satisfy this. If it's missing, run `mreq`'s BRAINSTORM Phase 1 (product/business-altitude clarify only — the need, who has it, why now, what success looks like; never a technical decision) folded into this same gate, not a second one. This is the one presence check `mquick` performs itself beyond the blast-radius test at Phase E; every other decision stays delegated to the skill it's sequencing.
3. **Run `mspec`'s Diagnostic / Clarify stage (Stage 1).** This is the *same* stage a gatekept `/mspec` runs — diagnose the change/product and run the **Risk & Ambiguity Scan** (the ranked list of decisions a human should make: breaking changes, data migration, security, ambiguous requirements, dependency choices). Do **not** write any files (Stage 1 writes nothing).
4. **Surface every question from both steps 2 and 3 here and iterate.** Present `mreq`'s clarify questions (if step 2 ran), the risk-scan decisions, and any ambiguities, and **keep clarifying within this phase until they're resolved.** Further clarification rounds are still Phase A — this stays the *only* gate.
5. **Show the plan preview** — the resolved targets and, for a `shared` change, the cascade (which consuming repos will be touched). Do **not** promise a story/wave/parallelism breakdown or any cost estimate here — those are *actuals* reported in Phase E. **There is no cost gate.**
6. **Get approval to proceed.** Once the user approves, the gate is closed for the rest of the run.

## Phases B–D — Autonomous (no gates)

Run straight through, reusing the real skills exactly as a gatekept operator would, but without stopping:

- **Phase B — `mreq`'s BRAINSTORM Phase 2 write (only if step A2 ran), then `mspec` Write (Stage 2).** If Phase A step 2 ran `mreq`'s BRAINSTORM Phase 1, invoke `mreq`'s Phase 2 write first — it appends the confirmed `### REQ-<NNN>` entries to `context/<tier>/requirements/REQUIREMENTS.md` — before invoking `mspec`'s write stage, so the requirements tier is populated before `mspec`'s CREATE-mode Prerequisites gate runs (otherwise the gate `mquick` just satisfied in Phase A would immediately re-fail in Phase B). Then invoke `mspec`'s write stage: spec files → change documents → validation → (for `shared`) the cascade. Produces the `PROJECT-CHANGE-<NNN>` that drives planning.
- **Phase C — `mplan`.** Generate the story files, the `plan.yaml` graph, the initial plan-level `state.yaml`, and the project-ledger entry.
- **Phase D — `mexecute`.** Ship the plan: worktree-isolated waves, barrier merge (agent discretion), bounded retry (N=3), Post-Story/Final validation, two-level state, and the post-ship `/mverify` conformance sweep.

`mquick` itself is a **plain sequential pipeline** over these — it is **not** a dynamic Workflow (that is `mexecute` alone). It has no parallelism or isolation of its own; all of that lives inside `mexecute`.

## Phase E — Report & Escalate

Summarize the whole run: **specced → planned → shipped → validated → conformed**, with the actual story/wave/parallelism breakdown, retries used, the conformance-sweep result, and the run's **actual cost/telemetry** (never a pre-run estimate — there is no cost gate).

Handle the mid-run breaks per `mexecute`'s halt conditions:

- **Significant breaking contract change** (a shared-interface break that cascades beyond the affected story) — `mexecute` **halts and reports**; `mquick` relays the break, its blast radius, and the options, and **stops** — the user acts on it and re-invokes.
- **Contained breaking change** (would only break code in the affected story) — **does not** halt; it's deferred here to the report.
- **Retry exhausted** or **unmergeable wave** — `mexecute` halts and reports; surface which story/wave and what's needed.
- **Conformance drift** found by the post-ship sweep — does **not** halt the run; `mquick` **folds it into this report** as an escalation (a gatekept manual workflow would instead leave such drift for the human to act on next via `mspec`/`mreverse`).

## Relationship to the Gatekept Path

`mquick` and the gatekept path (`/mspec` → `/mplan` → `/mexecute` → `/mverify`, reviewing at each hand-off) produce the same artifacts and run the same skills. The only difference is **where the human reviews**: `mquick` collapses all the review into the single Phase A gate and then runs unattended; the gatekept path reviews at every skill boundary. Use `mquick` when you trust the clarified plan enough to let it run; use the gatekept path when you want to inspect each stage.

## What mquick does / does NOT do

- **Does:** clarify once (with the risk scan, and `mreq`'s brainstorm when a CREATE target has no requirements yet), then drive `mreq → mspec → mplan → mexecute` to shipped, conformed code; report actuals; escalate only the three `mexecute` halt conditions.
- **Does NOT:** add spec/plan/execute/requirements logic of its own beyond the one presence check — the requirements-presence check deciding whether `mreq` runs at Phase A/B is the only other piece of decision logic `mquick` performs, alongside the existing blast-radius test separating a significant from a contained breaking change at Phase E; introduce lightweight forks of the skills; run its own parallelism (that's `mexecute`); gate between waves; gate on cost; halt on conformance drift.
