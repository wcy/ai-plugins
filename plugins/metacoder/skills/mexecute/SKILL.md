---
name: mexecute
description: Use when you need to actually ship a plan — turn its stories into working, validated code — triggers on phrases like "execute the plan", "run the plan", "implement the plan", "ship it", "build the stories", "run mexecute", or after mplan has produced a plan under context/project/plans/. This is the ONE skill that writes feature code (mfix writes remediation only) — it runs the plan as worktree-isolated waves, validates and merges at each barrier, retries failures, and finishes with a conformance sweep.
---

# Execute: Ship a Plan as Worktree-Isolated Waves

`mexecute` is the **only** skill in this workflow that writes application code. It ships a finished plan (from `mplan`): each wave's stories run **concurrently, each in its own git worktree**, are validated and merged at a barrier, failures are retried, and the run ends with a post-ship `/mverify` conformance sweep. It is realized as a **dynamic Workflow** (the `Workflow` tool) so the parallelism, barrier, retry, and merge are deterministic control flow — not model-improvised.

**Invariants it never breaks:** **one story → one repo → one worktree** · greens merge into a per-repo integration branch, never the live working branch · state is the source of truth for resume/retry (not prose).

## The Tool Computes, `mexecute` Runs Git

Every mechanical step of this run — which plan and where to resume it, the run counter, branch and worktree names, worktree reconciliation verdicts, and every two-level state write — is computed by `python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py`. **Invoke it; never restate it.** The split runs both ways: `mc.py` invokes no git command, and this skill composes no name and derives no verdict of its own. Every `git` command below is run by `mexecute` (or by a story agent it dispatches), on the names and verdicts the tool returned.

**A failed `mc.py` invocation — any non-zero exit — is a hard error: report it and halt the phase. No prose fallback, ever.** A fallback would be a second implementation of the step — exactly what the tool exists to prevent.

Yours alone: merge and salvage discretion at the barrier, whether a retry is worth spending, whether a halt condition has fired. The tool computes facts; those are judgments, and nothing below delegates them.

## Step 0: Resolve the Plan + Resume Point

```
python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py plan resolve [<plan-id>]
```

One call answers all of it — pass an explicit plan id only if the user named one:

- `plan_id` — the plan to run.
- `status` — its current status.
- `run` — the run counter **as currently recorded** (Step 1 increments it).
- `resume_wave` — the first wave holding an unfinished story; the run starts there.
- `pending_stories` — the stories to actually dispatch. Anything already `applied` is not in the list and is skipped.

Do not re-derive any of these by reading the ledger or the plans directory. A story still `failed` after prior retry exhaustion is simply pending again: it is retried **afresh** on this run, the **per-run** retry budget resets, and its prior attempts stay recorded in history under their own `run` number.

## Step 1: Pre-flight

Do this before starting the Workflow: cheap, sequential, and sets up the ground the wave agents stand on. The Workflow script itself has no filesystem or git access.

1. **Validate the graph and the plan state** (fail fast on a corrupt plan):

   ```
   python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py validate plan-graph  context/project/plans/<plan-id>/plan.yaml
   python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py validate plan-state  context/project/plans/<plan-id>/state.yaml
   ```

2. **Take this run's number and mark the plan in progress:**

   ```
   python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py state run-increment <plan-id>
   python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py state set-plan <plan-id> --status in-progress
   ```

   `run-increment` is **unconditional and identical on a fresh plan and on a resume** — there is no first-invocation special case. `mplan` seeds `run: 0`, so the **first invocation runs as `1`** and every later one as previous + 1. It returns the new value: that is `RUN`, and every branch, worktree path, and `attempts[]` entry this run produces carries it. `set-plan` writes the plan `state.yaml` and the project ledger **in the same call**, so the two cannot be left disagreeing.

3. **Per-repo integration branches.** Get the name from the tool — the `integration` field of `worktree names` (below), which is per-plan, so any story id in the plan yields it. Then, in each repo listed in `plan.yaml`, use git yourself to create that branch from the repo's current tip if it does not already exist. **Greens merge there, never into the repo's live working branch.** Record the branch for each repo in `state.yaml`'s `integration_branches`.

4. **Reconcile leftover worktrees** (resume safety). You run git; the tool judges:

   ```
   git -C repos/<repo> worktree list --porcelain \
     | python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py worktree reconcile <plan-id> --list-from -
   ```

   You get one verdict per work tree in the listing, and you act on it:

   - `remove` — its story is now `applied`; run `git worktree remove` yourself.
   - `keep` — its story is unfinished (pending or failed); leave it for inspection or salvage.
   - `orphan` — it is not in `state.yaml` at all, or its name does not parse. **Report it and leave it in place. An orphan is never removed** — the tool emits no removal instruction for one, and neither do you.

## Step 2: Run the Dynamic Workflow

Drive the waves with the `Workflow` tool. The script loops waves **in order** (a wave is a hard barrier — never pipeline across waves), and within each wave fans out one agent per story. Use this shape as the skeleton (fill in from `plan.yaml` and from Step 0's resolution):

```javascript
export const meta = {
  name: 'mexecute-<plan-id>',
  description: 'Ship plan <plan-id>: worktree-isolated waves, barrier merge, retry 3 per run, conformance sweep',
  phases: [ { title: 'Wave 1' }, /* one per wave */ { title: 'Sweep' } ],
}

const WAVES = /* from plan.yaml, only waves at/after `resume_wave` */
const RETRY_MAX = 3   // per run
const RUN = /* the value `mc.py state run-increment` returned in Step 1 */
const PENDING_STORIES = /* `pending_stories` from Step 0's `mc.py plan resolve` */

for (const wave of WAVES) {
  phase(`Wave ${wave.wave}`)
  const pending = wave.stories.filter(s => PENDING_STORIES.includes(s))

  // Fan out: one agent per story, full wave concurrency. Each story agent creates its
  // own worktree with `git worktree add`, on the names `mc.py worktree names` returned.
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

In the skeleton, `STORY_REPORT_SCHEMA` is the parsed `${CLAUDE_PLUGIN_ROOT}/schemas/story-report.schema.json`; `BARRIER_SCHEMA` is a small inline shape you define for the barrier agent's return (`{ halt: bool, reason, merged: [...], deferred_breaks: [...] }`); `SWEEP_DISCRIMINATOR` is the explicit sweep-mode value `/mverify` is invoked with; and `storyPrompt`/`barrierPrompt`/`storyOf`/`mergeAttempts` are illustrative helpers you build from `plan.yaml` + Step 0's resolution. Adapt freely — the **contract** is: waves in order, one agent per story at full concurrency (each creating its own worktree), a barrier that validates/merges/persists, bounded retry 3 per run, then the sweep. `return { done: true, sweep }` ends the `Workflow`; the sweep result is persisted **after** that return, by the `mexecute` session, not by the script (see Step 3).

**Names for every dispatch.** Before building a story prompt, ask the tool for that (story, run, attempt)'s names:

```
python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py worktree names <plan-id> <story-id> --run <n> --attempt <n>
```

It returns `integration` (the branch to cut from and merge back into), `story` (the branch this attempt works on), and `worktree_path` (where the work tree goes). Pass all three into the prompt verbatim. Never assemble, guess at, or pattern-match a branch or worktree name yourself — this call is the only source of them, for the first attempt and every retry alike.

The compute-heavy parts are **agents** because a Workflow script itself has no filesystem/git access — it only orchestrates. Concretely:

- **Story agent** (worktree-isolated). Its prompt names exactly one story and carries the three names above. It: (a) **creates its own worktree** with `git worktree add` inside `repos/<repo>/`, on the `story` branch cut from that repo's `integration` branch (the merged tip of the previous wave), at `worktree_path`; (b) loads **only** its `PLAN-*.md`, that story's Context Files, and the repo `CATALOG.yaml` — nothing else; (c) implements the story in `repos/<repo>/` (its single target repo); (d) runs the story's **Post-Story Validation** from `plan.yaml` (`kind: exit-code` commands must exit 0; `prose` steps it interprets); (e) returns the `story-report.schema.json` shape — status, branch, worktree path, files changed, validation result, and any `breaking_contract_change`. `agent()`'s `isolation: 'worktree'` option is **unparameterized** — it selects neither repo nor branch — so it is **not** the mechanism of story isolation; real `git worktree` commands, run by the story agent itself, are.
- **Barrier/merge agent** (one per wave, at the barrier). It applies the **merge/salvage discretion** (below), merges greens into the integration branch with git, records each story through `mc.py state set-story` (Step 4), removes merged worktrees with `git worktree remove`, keeps failed ones, and returns whether to `halt`.
- **Final-validation agent** (last wave only): runs the last wave's **Final Validation** steps from `plan.yaml`.
- **mverify sweep agent** (Step 3): invokes `/mverify` across every repo the plan touched, with the explicit sweep discriminator.

**If the `Workflow` tool is unavailable** in the environment, emulate the same structure by hand: for each wave in order, dispatch the story agents concurrently (`Agent` calls, one message — each story agent creates its own worktree with `git worktree add`), then run the **retry loop to exhaustion**, then the barrier/merge + state persistence yourself, then advance. What matters is the model — waves in order, worktree-per-story, barrier discretion, retry 3 per run, sweep — not the tool. The `mc.py` calls are identical either way.

## The Barrier: Merge / Salvage Discretion

At each wave barrier, wait for all stories (including retries), then decide **per story** — this is **agent discretion, not a strict green-only gate**, and it is yours, not the tool's:

- **Good** — passed Post-Story Validation → merge its branch into the repo integration branch.
- **Salvageable** — mostly right, small fixable gap → salvage (a targeted fix on its branch) then merge, or fold into a retry.
- **Reusable** — wrong overall but has usable parts → keep the worktree, note what to reuse.
- **Mergeable** — independently, conflict-free.

Same-wave stories target **disjoint modules by construction** (mplan guarantees module-disjoint `target_paths`), so merges should be **conflict-free**. An unexpected merge conflict signals a **planning error** — the agent **reports it and halts the wave** rather than blindly auto-resolving; it may salvage if it can do so sensibly, but it never guesses at a resolution.

## Bounded Auto-Retry

Up to **3 attempts per failed story, per run** (`RETRY_MAX = 3`). `retries` resets at the start of each run; exhaustion halts the run. Each attempt gets a **fresh worktree and branch** — a new `worktree names` call with the next attempt number — recorded in `state.yaml`. Total attempts across resumes are **unbounded by design** — each re-invocation of `mexecute` is the human's deliberate consent to grant a fresh budget. On exhaustion the story is left `failed` and the run **halts and reports** — it never advances a wave on red.

## Halt-and-Report Conditions

These are the **only** reasons a run stops early, and calling one is your judgment. Everything else — including conformance drift — defers to the run report. **All three are terminating**: the run halts, reports, and ends. There is no mid-run pause and no between-wave gate — the user acts on the report and resumes with a **fresh `mexecute` invocation**, which takes a new `run` number and a fresh per-run retry budget.

1. **Retry exhausted** — a story fails all 3 attempts (this run).
2. **Unmergeable wave** — a same-wave conflict the agent judges it cannot sensibly resolve or salvage (it reports rather than auto-resolving).
3. **Significant breaking contract change** — a shared-interface break surfaced during implementation whose blast radius **cascades beyond the current story** (forces a shared-spec revision or changes across consuming repos). The run **halts, reports the break, and ends**; it does not wait in-place for a decision.

A breaking contract change whose blast radius is **contained** — it would only break code in the affected story — is **not** a halt: that story fails Post-Story Validation (so its worktree never merges), the failure is recorded as a `deferred_break` in `state.yaml`, and the **run continues**, folding it into the final report.

**No human review between waves.** `mexecute` always runs every wave through; gatekeeping lives at the hand-offs around `mexecute`, not inside it.

## Worktree Lifecycle

- A story targets **exactly one repo** → **one worktree**. Wave N worktrees branch from the run's per-repo **integration branch** (the merged tip of wave N−1).
- Names are never composed here: the branch and the worktree path come from `mc.py worktree names`, per story **and** per attempt, so they are unique per (run, attempt) by construction.
- **Every** worktree ref — per story **and** per retry attempt — is recorded in `state.yaml` under the story's `attempts[]`, via `mc.py state set-story --attempt <n> --branch <b> --worktree <w>`.
- **Merged (green)** worktrees are **removed** with `git worktree remove` (`worktree_removed: true`); **failed/unmerged** ones are **kept and recorded** so the agent or a later run can inspect, reuse, or salvage them.
- **On resume**, reconcile with `mc.py worktree reconcile` and act on its verdicts (Pre-flight step 4): `remove` a worktree whose story is now `applied`, `keep` an unfinished one, and **report — never remove — an orphan**.

## Cross-Repo Change Flow

A story is single-repo, so a cross-repo change is *separate per-repo stories* joined by the frozen shared contract — never one story spanning repos:

1. `mspec` cascade freezes the shared-interface contract in `context/shared/spec/` and updates each consumer's spec to reference it.
2. `mplan` decomposes it into a producer story + one consumer story per repo. Every repo codes against the *frozen spec*, not the producer's code, so consumer stories needn't wait on the producer's implementation — same-altitude modules across repos may share a wave.
3. `mexecute` runs each story in its own single-repo worktree — no cross-repo worktree juggling.
4. `mverify` (the sweep) confirms every producer/consumer repo matches the frozen contract and flags any that bypassed the shared spec.

The **contract**, not any worktree, is the cross-repo sync point — code-level atomicity across repos is neither needed nor attempted.

## Step 3: Post-Ship Conformance Sweep

Once per run, after the wave loop (not per wave): invoke **`/mverify`** across every repo the plan touched, **with the explicit sweep discriminator**. It is **detection-only** — it never rewrites code, spec, or change files — and it **writes no state itself**: it returns `{status, report, findings}`, whose `status` uses `plan-state`'s `clean|drift|not-run` vocabulary.

The **`mexecute` session** persists that returned result **once the `Workflow` returns** — not a barrier agent (the wave loop has ended by then, so none remains) and not the `Workflow` script (which has no filesystem access):

```
python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py state conformance <plan-id> --status <status> --report <report-path> --findings <n>
```

**The sweep is explicitly non-blocking — drift never halts the run.**

## Step 4: State, Telemetry & Report

- **Two-level state, written only through `mc.py state`.** Per-story/per-wave/per-attempt status and refs, conformance, and telemetry live in the plan-level `state.yaml`; whole-plan completion (`pending → in-progress → applied | failed`) is reflected in the project ledger `context/project/state.yaml`. One verb per transition, at every transition:

  ```
  python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py state run-increment <plan-id>
  python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py state set-plan     <plan-id> --status <status>
  python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py state set-story    <plan-id> <story-id> --status <status> [--attempt <n> --branch <b> --worktree <w>]
  python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py state conformance  <plan-id> --status <status> --report <path> --findings <n>
  python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py state telemetry    <plan-id> --cost <usd> --tokens <n> --wall-clock <s>
  ```

  **Each verb validates before it persists** — the equivalent of `mc.py validate plan-state` over the plan `state.yaml` and `mc.py validate project-state` over the ledger — and **refuses a write that would not validate, so an invalid state file cannot reach disk.** Validation is therefore no longer a step you run separately, and no longer one you can omit. `set-plan` writes both files in one call, so `state.yaml` and the ledger cannot end up disagreeing; `set-story` with `--attempt` appends or updates that attempt's `attempts[]` entry and keeps `retries` consistent for the current run. The few schema fields no verb takes an option for — plan-level `integration_branches`, a story's `deferred_break`, an attempt's `validation` and `worktree_removed` — you write into `state.yaml` alongside these calls, and the next verb's validation refuses to persist over a file you got wrong.
- **Telemetry, not estimates.** Record actual `cost_usd` / `tokens` / `wall_clock_s` with `state telemetry`. **There is no cost gate** — never gate on a pre-run estimate.
- **Logs/artifacts** under `context/project/out/<plan-id>/`.
- **Final report:** stories shipped/failed/deferred, waves run, parallelism, retries used, the conformance-sweep result, any deferred contained breaks, and telemetry. If the run halted, name which of the three conditions and what the user must decide. Close the plan with `state set-plan <plan-id> --status applied|failed`.

## What mexecute does / does NOT do

- **Does:** write code (the only skill that does), in `repos/<repo>/`; run the Workflow; run every git command the run needs — branch, `git worktree add`, merge, `git worktree remove`; validate, merge, retry, sweep; own both state files during the run.
- **Does NOT:** brainstorm or design (that's `mspec`); decide plan structure (that's `mplan`, read from `plan.yaml`); rewrite specs; touch more than one repo per story; merge into a repo's live working branch; advance a wave on red; halt on conformance drift; ask `mc.py` to run git, or compose a name or a verdict `mc.py` owns.

## Asking Questions

None in-run: no between-wave gates and no mid-run prompt. Halt condition 3 (a **significant breaking contract change**) **terminates the run** with a report naming the break, its blast radius, and the options (revise the shared spec / change consuming repos / abort). The user acts on that report and **re-invokes `mexecute`** — there is no in-place wait for a decision.
