---
name: mquick
description: Use when you want the whole spec-to-ship loop run autonomously from one request — triggers on phrases like "just build it", "spec and ship this", "do the whole thing", "mquick", "take this to shipped code", "one-shot this feature", or any time the user wants a change taken from description to validated, conformed code with a single clarification round. Autonomous by name — it clarifies once, then runs mspec → mplan → mexecute to completion.
---

# mquick: Autonomous Spec-to-Ship

`mquick` takes a request from description to **shipped, validated, conformed code** with exactly **one** clarification gate. It's a **thin sequencer over the real skills** — no lightweight forks, no orchestration of its own. It invokes `mspec`, `mplan`, and `mexecute` in order and lets `mexecute` (the one dynamic Workflow) own all parallelism, worktrees, and retry. This **is** the small-task fast path: a small task simply has little to clarify.

After the Phase A gate, `mquick` runs to completion at **full concurrency with no budget cap and no further gates** — the only legitimate mid-run break is one of `mexecute`'s three halt-and-report conditions.

## Phase A — Clarify (the only gate)

1. **Resolve the target(s).** Determine which repo(s) or `shared` the request touches — exactly as `/mspec` Step 0 does. The CREATE-vs-UPDATE mode is not derived here; it comes from step 2's call.
2. **Ask for the mode and the requirements gate in one call.** Run `python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py spec mode <target>` — it reports the CREATE/UPDATE mode and the target's requirements-gate result in one call. A failed invocation is a **hard error**: halt the phase and report it; there is no prose fallback and neither answer is ever re-derived by hand. **If the mode is CREATE and the requirements gate fails, run `mreq`'s BRAINSTORM Phase 1 first** (product/business-altitude clarify only — the need, who has it, why now, what success looks like; never a technical decision), folded into this same gate, not a second one; otherwise go straight to step 3. This is the one check `mquick` performs itself and the only tool call it makes directly — every other decision and mechanical step is delegated to the skill that owns it (see "What mquick does NOT do" below).
3. **Run `mspec`'s Diagnostic / Clarify stage (Stage 1)** — the same stage a gatekept `/mspec` runs: diagnose the change/product and run the **Risk & Ambiguity Scan** (the ranked list of decisions a human should make: breaking changes, data migration, security, ambiguous requirements, dependency choices, requirements drift). Do **not** write any files (Stage 1 writes nothing).
4. **Surface every question from steps 2 and 3 and iterate.** Present `mreq`'s clarify questions (if step 2 ran), the risk-scan decisions, and any ambiguities; **keep clarifying within this phase until resolved.** Further rounds are still Phase A — this stays the *only* gate.
5. **Show the plan preview** — resolved targets and, for a `shared` change, the cascade (which consuming repos will be touched). Do **not** promise a story/wave/parallelism breakdown or cost estimate here — those are *actuals* reported in Phase E. **There is no cost gate.**
6. **Get approval to proceed.** Once approved, the gate is closed for the rest of the run.

## Phases B–D — Autonomous (no gates)

Run straight through, reusing the real skills, without stopping:

- **Phase B — `mreq`'s BRAINSTORM Phase 2 write (only if step A2 ran), then `mspec` Write (Stage 2).** If Phase A step 2 ran `mreq`'s BRAINSTORM Phase 1, invoke `mreq`'s Phase 2 write first: it appends the confirmed `### REQ-<NNN>-<mnemonic>` entries to `context/<tier>/requirements/REQUIREMENTS.md`, before `mspec`'s write stage, so the requirements tier is populated before `mspec`'s CREATE-mode Prerequisites gate runs — otherwise the gate `mquick` just satisfied in Phase A would immediately re-fail in Phase B. Then invoke `mspec`'s write stage: spec files → change documents → validation → (for `shared`) the cascade. Produces the `PROJECT-CHANGE-<NNN>` that drives planning. `mreq`'s inline contradiction check runs inside this write and introduces no gate: with no one to ask, it flags the contradiction into the run's `REQ-CHANGE` record and continues — the same treatment DERIVE gets. This keeps the no-further-gates guarantee true without resolving a contradiction silently or halting the run mid-pipeline.
- **Phase C — `mplan`.** Generate the story files, the `plan.yaml` graph, the initial plan-level `state.yaml`, and the project-ledger entry.
- **Phase D — `mexecute`.** Ship the plan: worktree-isolated waves, barrier merge (agent discretion), bounded retry (N=3), Post-Story/Final validation, two-level state, and the post-ship `/mverify` conformance sweep.

`mquick` itself is a **plain sequential pipeline** over these, not a dynamic Workflow — it has no parallelism or isolation of its own; that lives inside `mexecute`.

## Phase E — Report & Escalate

Summarize the run: **specced → planned → shipped → validated → conformed**, with the actual story/wave/parallelism breakdown, retries used, any open `REQ-CHANGE` records Phase B flagged, the conformance-sweep result, and **actual cost/telemetry** (never a pre-run estimate — there is no cost gate).

Handle mid-run breaks per `mexecute`'s halt conditions:

- **Significant breaking contract change** (a shared-interface break that cascades beyond the affected story) — `mexecute` **halts and reports**; `mquick` relays the break, blast radius, and options, then **stops** — the user acts and re-invokes.
- **Contained breaking change** (would only break code in the affected story) — **does not** halt; deferred to this report.
- **Retry exhausted** or **unmergeable wave** — `mexecute` halts and reports; surface which story/wave and what's needed.
- **Conformance drift** found by the post-ship sweep — does **not** halt the run; `mquick` **folds it into this report** as an escalation.

## What mquick does / does NOT do

- **Does:** clarify once (with the risk scan, and `mreq`'s brainstorm when a CREATE target has no requirements yet), then drive `mreq → mspec → mplan → mexecute` to shipped, conformed code; report actuals; escalate only the three `mexecute` halt conditions.
- **Does NOT:** add spec/plan/execute/requirements logic of its own beyond the one presence check — the requirements-presence check deciding whether `mreq` runs at Phase A/B is the only decision logic `mquick` performs; the blast-radius test separating a significant from a contained breaking change belongs to `mexecute` at Phase E, `mquick` only relays it; introduce lightweight forks of the skills; run its own parallelism (that's `mexecute`); gate between waves; gate on cost; halt on conformance drift.
