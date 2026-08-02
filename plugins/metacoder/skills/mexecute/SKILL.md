---
name: mexecute
description: Use when you need to actually ship a plan — turn its stories into working, validated code — triggers on phrases like "execute the plan", "run the plan", "implement the plan", "ship it", "build the stories", "run mexecute", or after mplan has produced a plan under context/project/plans/. This is the ONE code-writing skill — it runs the plan as worktree-isolated waves, validates and merges at each barrier, retries failures, and finishes with a conformance sweep.
---

# Execute: Ship a Plan as Worktree-Isolated Waves

`mexecute` is the **only** skill in this workflow that writes application code. It takes a finished plan (from `mplan`) and ships it: each wave's stories run **concurrently, each in its own git worktree**, are validated and merged at a barrier, failures are retried, and the run ends with a post-ship `/mverify` conformance sweep. It is realized as a **dynamic Workflow** (the `Workflow` tool) so the parallelism, barrier, retry, and merge are deterministic control flow — not model-improvised.

**Invariants it never breaks:** doc-only skills vs. this one code-writer · **one story → one repo → one worktree** · greens merge into a per-repo integration branch, never the live working branch · state is the source of truth for resume/retry (not prose).

## Step 0: Resolve the Plan + Resume Point

1. **Which plan.** Use an explicit plan id/dir if given; otherwise read the project ledger `context/project/state.yaml` and take the first plan whose `status` is `pending` or `in-progress`; fall back to the highest `<NNN>` directory under `context/project/plans/`.
2. **Resume point.** Read the plan's `state.yaml`. Stories already `applied` are **skipped**; the run resumes at the **first wave with any unfinished story**. A story still `failed` after prior retry exhaustion is retried **afresh** on this run — the **per-run** retry budget resets, and its prior attempts stay recorded in history under their own `run` number.

## Step 1: Pre-flight

Do this before starting the Workflow — it is cheap, sequential, and sets up the ground the wave agents stand on.

1. **Read + validate the graph** (fail fast on a corrupt plan):

   ```
   python3 ${CLAUDE_PLUGIN_ROOT}/schemas/validate.py plan-graph context/project/plans/<plan-id>/plan.yaml
   python3 ${CLAUDE_PLUGIN_ROOT}/schemas/validate.py plan-state context/project/plans/<plan-id>/state.yaml
   ```

2. **Per-repo integration branches.** For each repo in `plan.yaml`'s `repos`, ensure an integration branch exists (create from the repo's current tip if absent), named deterministically, e.g. `mexec/<plan-id>/integration`. **Greens merge here, never into the repo's live working branch.** Record each in `state.yaml`'s `integration_branches`.
3. **Reconcile leftover worktrees** (resume safety). List existing `mexec/<plan-id>/…` worktrees (`git worktree list`) — names parse as `<plan-id>/<story-id>/r<run>/<attempt>`. For each: if its story is now `applied` in `state.yaml`, remove it; if it is a worktree **not present in `state.yaml` at all** (an orphan), **report it — do not silently delete it**. Failed/unmerged worktrees whose story is still `pending`/`failed` are kept for inspection/salvage.
4. Set the plan `status: in-progress` in both `state.yaml` and the ledger, and in that same write **increment `state.yaml`'s top-level `run`** — unconditionally, once per invocation, for the whole plan, with **no first-invocation special case**: `mplan` seeds `run: 0`, so the first invocation yields `1` and every later one previous + 1. Every branch, worktree path, and `attempts[]` entry this run produces carries that `run` number.

## Step 2: Run the Dynamic Workflow

Drive the waves with the `Workflow` tool. The script loops waves **in order** (a wave is a hard barrier — never pipeline across waves), and within each wave fans out one agent per story. Use this shape as the skeleton (fill in from `plan.yaml`):

```javascript
export const meta = {
  name: 'mexecute-<plan-id>',
  description: 'Ship plan <plan-id>: worktree-isolated waves, barrier merge, retry 3 per run, conformance sweep',
  phases: [ { title: 'Wave 1' }, /* one per wave */ { title: 'Sweep' } ],
}

const WAVES = /* from plan.yaml, only waves at/after the resume point */
const RETRY_MAX = 3   // per run
const RUN = /* state.yaml's top-level run, already incremented in Step 1 pre-flight */

for (const wave of WAVES) {
  phase(`Wave ${wave.wave}`)
  const pending = wave.stories.filter(s => stateSays(s) !== 'applied')

  // Fan out: one agent per story, full wave concurrency. Each story agent creates its
  // own worktree with `git worktree add` on branch mexec/<plan-id>/<story-id>/r<run>/<attempt>.
  let results = await parallel(pending.map(story => () =>
    agent(storyPrompt(story, RUN, /*attempt*/ 1), {
      label: `${story}/r${RUN}/1`, phase: `Wave ${wave.wave}`,
      schema: STORY_REPORT_SCHEMA,
    })))

  // Bounded auto-retry: up to RETRY_MAX attempts per failed story, per run.
  for (let attempt = 2; attempt <= RETRY_MAX; attempt++) {
    const failed = results.filter(r => !r || r.status !== 'applied')
    if (!failed.length) break
    const retried = await parallel(failed.map(r => () =>
      agent(storyPrompt(storyOf(r), RUN, attempt), {
        label: `${storyOf(r)}/r${RUN}/${attempt}`, phase: `Wave ${wave.wave}`,
        schema: STORY_REPORT_SCHEMA,
      })))
    results = mergeAttempts(results, retried)
  }

  // BARRIER — a merge agent applies the discretion merge + validation-gated integration,
  // persists two-level state, and returns the wave outcome (merged / halt / deferred breaks).
  const barrier = await agent(barrierPrompt(wave, results), {
    label: `barrier:w${wave.wave}`, phase: `Wave ${wave.wave}`, schema: BARRIER_SCHEMA,
  })
  if (barrier.halt) return barrier   // one of the three halt-and-report conditions

  const isLast = wave === WAVES[WAVES.length - 1]
  if (isLast) await agent(finalValidationPrompt(wave), { label: 'final-validation', phase: `Wave ${wave.wave}` })
}

phase('Sweep')
const sweep = await agent(mverifySweepPrompt(SWEEP_DISCRIMINATOR), { label: 'mverify-sweep', phase: 'Sweep' })
return { done: true, sweep }   // the session persists `sweep` into state.yaml's conformance block after this returns
```

In the skeleton, `STORY_REPORT_SCHEMA` is the parsed `${CLAUDE_PLUGIN_ROOT}/schemas/story-report.schema.json`; `BARRIER_SCHEMA` is a small inline shape you define for the barrier agent's return (`{ halt: bool, reason, merged: [...], deferred_breaks: [...] }`); `SWEEP_DISCRIMINATOR` is the explicit sweep-mode value `/mverify` is invoked with; and `storyPrompt`/`barrierPrompt`/`stateSays`/`storyOf`/`mergeAttempts` are illustrative helpers you build from `plan.yaml` + `state.yaml`. Adapt freely — the **contract** is: waves in order, one agent per story at full concurrency (each creating its own worktree), a barrier that validates/merges/persists, bounded retry 3 per run, then the sweep. Note that `return { done: true, sweep }` ends the `Workflow`; the sweep result is persisted **after** that return, by the `mexecute` session, not by the script (see Step 3).

The compute-heavy parts are **agents** because a Workflow script itself has no filesystem/git access — it only orchestrates. Concretely:

- **Story agent** (worktree-isolated). Its prompt names exactly one story. It: (a) **creates its own worktree** with `git worktree add` inside `repos/<repo>/`, on branch `mexec/<plan-id>/<story-id>/r<run>/<attempt>` cut from that repo's **integration branch** (the merged tip of the previous wave); (b) loads **only** its `PLAN-*.md`, that story's Context Files, and the repo `CATALOG.yaml` — nothing else; (c) implements the story in `repos/<repo>/` (its single target repo); (d) runs the story's **Post-Story Validation** from `plan.yaml` (`kind: exit-code` commands must exit 0; `prose` steps it interprets); (e) returns the `story-report.schema.json` shape — status, branch, worktree path, files changed, validation result, and any `breaking_contract_change`. `agent()`'s `isolation: 'worktree'` option is **unparameterized** — it selects neither repo nor branch — so it is **not** the mechanism of story isolation; real `git worktree` commands, run by the story agent itself, are.
- **Barrier/merge agent** (one per wave, at the barrier). It applies the **merge/salvage discretion** (below), merges greens into the integration branch, updates `state.yaml` (per-story status, retries, **per-attempt worktree/branch refs**, validation) and the ledger, removes merged worktrees, keeps failed ones, and returns whether to `halt`.
- **Final-validation agent** (last wave only): runs the last wave's **Final Validation** steps from `plan.yaml`.
- **mverify sweep agent** (Step 3): invokes `/mverify` across every repo the plan touched, with the explicit sweep discriminator.

**If the `Workflow` tool is unavailable** in the environment, emulate the same structure by hand: for each wave in order, dispatch the story agents concurrently (`Agent` calls, one message — each story agent creates its own worktree with `git worktree add`), then run the **retry loop to exhaustion**, then the barrier/merge + state persistence yourself, then advance. The model (waves in order, worktree-per-story, barrier discretion, retry 3 per run, sweep) is what matters — the `Workflow` tool is just the cleanest way to express it.

## The Barrier: Merge / Salvage Discretion

At each wave barrier, wait for all stories (including retries), then decide **per story** — this is **agent discretion, not a strict green-only gate**:

- **Good** — passed Post-Story Validation → merge its branch into the repo integration branch.
- **Salvageable** — mostly right, small fixable gap → salvage (a targeted fix on its branch) then merge, or fold into a retry.
- **Reusable** — wrong overall but has usable parts → keep the worktree, note what to reuse.
- **Mergeable** — independently, conflict-free.

Same-wave stories target **disjoint modules by construction** (mplan guarantees module-disjoint `target_paths`), so merges should be **conflict-free**. An unexpected merge conflict signals a **planning error** — the agent **reports it and halts the wave** rather than blindly auto-resolving; it may salvage if it can do so sensibly, but it never guesses at a resolution.

## Bounded Auto-Retry

Up to **3 attempts per failed story, per run** (`RETRY_MAX = 3`). `retries` resets at the start of each run; exhaustion halts the run. Each attempt is a fresh worktree/branch (`mexec/<plan-id>/<story-id>/r<run>/<attempt>`), recorded in `state.yaml`. Total attempts across resumes are **unbounded by design** — each re-invocation of `mexecute` is the human's deliberate consent to grant a fresh budget. On exhaustion the story is left `failed` and the run **halts and reports** — it never advances a wave on red.

## Halt-and-Report Conditions

These are the **only** reasons a run stops early. Everything else — including conformance drift — defers to the run report. **All three are terminating**: the run halts, reports, and ends. There is no mid-run pause and no between-wave gate — the user acts on the report and resumes with a **fresh `mexecute` invocation**, which takes a new `run` number and a fresh per-run retry budget.

1. **Retry exhausted** — a story fails all 3 attempts (this run).
2. **Unmergeable wave** — a same-wave conflict the agent judges it cannot sensibly resolve or salvage (it reports rather than auto-resolving).
3. **Significant breaking contract change** — a shared-interface break surfaced during implementation whose blast radius **cascades beyond the current story** (forces a shared-spec revision or changes across consuming repos). The run **halts, reports the break, and ends**; it does not wait in-place for a decision.

A breaking contract change whose blast radius is **contained** — it would only break code in the affected story — is **not** a halt: that story fails Post-Story Validation (so its worktree never merges), the failure is recorded as a `deferred_break` in `state.yaml`, and the **run continues**, folding it into the final report.

**No human review between waves.** `mexecute` always runs every wave through; gatekeeping (§ modes) lives at the skill hand-offs *around* `mexecute`, not inside it.

## Worktree Lifecycle

- A story targets **exactly one repo** → **one worktree**. Wave N worktrees branch from the run's per-repo **integration branch** (the merged tip of wave N−1).
- Deterministic names: branch `mexec/<plan-id>/<story-id>/r<run>/<attempt>`; worktree path likewise unique per (run, attempt).
- **Every** worktree ref — per story **and** per retry attempt — is recorded in `state.yaml` under the story's `attempts[]`.
- **Merged (green)** worktrees are **removed** (`worktree_removed: true`); **failed/unmerged** ones are **kept and recorded** so the agent or a later run can inspect, reuse, or salvage them.
- **On resume**, reconcile against `state.yaml`: remove worktrees whose story is now `applied`; **report** (don't silently delete) any orphan not in state (done in Pre-flight step 3).

## Cross-Repo Change Flow

A story is single-repo, so a cross-repo change is *separate per-repo stories* joined by the frozen shared contract — never one story spanning repos:

1. `mspec` cascade freezes the shared-interface contract in `context/shared/spec/` and updates each consumer's spec to reference it.
2. `mplan` decomposes it into a producer story + one consumer story per repo. Because every repo codes against the *frozen spec* (not the producer's code), consumer stories needn't wait on the producer's implementation — same-altitude modules across repos may share a wave.
3. `mexecute` runs each story in its own single-repo worktree — no cross-repo worktree juggling.
4. `mverify` (the sweep) confirms every producer/consumer repo matches the frozen contract and flags any that bypassed the shared spec.

The **contract**, not any worktree, is the cross-repo sync point; code-level atomicity across repos is neither needed nor attempted.

## Step 3: Post-Ship Conformance Sweep

Once per run, after the wave loop (not per wave): invoke **`/mverify`** across every repo the plan touched, **with the explicit sweep discriminator**. `/mverify` returns `{status, report, findings}` — `status` uses `plan-state`'s `clean|drift|not-run` vocabulary — and **writes no state itself**. The **`mexecute` session** persists that result verbatim into `state.yaml`'s `conformance` block **once the `Workflow` returns** — not a barrier agent (the wave loop has ended by then, so none remains) and not the `Workflow` script (which has no filesystem access). **Drift does not halt the run** — a gatekept manual run leaves the drift for the human to act on next (`mspec`/`mreverse`); an autonomous `/mquick` run folds it into its escalation report.

## Step 4: State, Telemetry & Report

- **Two-level state, updated at every transition.** Per-story/per-wave/per-attempt status, retries, worktree refs, validation, and conformance go in the plan-level `state.yaml`; whole-plan completion (`pending → in-progress → applied | failed`) is reflected in the project ledger `context/project/state.yaml`. Validate both on write:

  ```
  python3 ${CLAUDE_PLUGIN_ROOT}/schemas/validate.py plan-state    context/project/plans/<plan-id>/state.yaml
  python3 ${CLAUDE_PLUGIN_ROOT}/schemas/validate.py project-state context/project/state.yaml
  ```

- **Telemetry, not estimates.** Record actual `cost_usd` / `tokens` / `wall_clock_s` in `state.yaml`'s `telemetry`. **There is no cost gate** — never gate on a pre-run estimate.
- **Logs/artifacts** under `context/project/out/<plan-id>/`.
- **Final report:** stories shipped/failed/deferred, waves run, parallelism, retries used, the conformance-sweep result, any deferred contained breaks, and telemetry. If the run halted, name which of the three conditions and what the user must decide.

## What mexecute does / does NOT do

- **Does:** write code (the only skill that does), in `repos/<repo>/`; run the Workflow; manage worktrees/integration branches; validate, merge, retry, sweep; own both state files during the run.
- **Does NOT:** brainstorm or design (that's `mspec`); decide plan structure (that's `mplan`, read from `plan.yaml`); rewrite specs; touch more than one repo per story; merge into a repo's live working branch; advance a wave on red; halt on conformance drift.

## Asking Questions

None in-run: no between-wave gates and no mid-run prompt. Halt condition 3 (a **significant breaking contract change**) **terminates the run** with a report naming the break, its blast radius, and the options (revise the shared spec / change consuming repos / abort). The user acts on that report and **re-invokes `mexecute`** — there is no in-place wait for a decision.
