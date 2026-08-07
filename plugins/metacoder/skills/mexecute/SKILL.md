---
name: mexecute
description: Use when you need to actually ship a plan — turn its stories into working, validated code — triggers on phrases like "execute the plan", "run the plan", "implement the plan", "ship it", "build the stories", "run mexecute", or after mplan has produced a plan under context/project/plans/. This is the ONE skill that writes feature code (mfix writes remediation only) — it ships one slice of the plan as worktree-isolated waves, validates and merges at each barrier, retries failures, runs the slice's acceptance, and finishes with a conformance sweep.
---

# Execute: Ship a Slice as Worktree-Isolated Waves

`mexecute` is the **only** skill in this workflow that writes application code. It ships **one slice** of a finished plan (from `mplan`): each wave's stories run **concurrently, each in its own git worktree**, are validated and merged at a barrier, failures are retried, the slice's **Slice Acceptance** demonstrates the behaviour actually works, and the run ends with a post-ship `/mverify` conformance sweep. It is realized as a **dynamic Workflow** (the `Workflow` tool) so the parallelism, barrier, retry, and merge are deterministic control flow — not model-improvised.

**Invariants it never breaks:** **one story → one repo → one worktree** · greens merge into a per-repo integration branch, never the live working branch · state is the source of truth for resume/retry (not prose).

## Scope: `--slice`, and What Its Absence Means

```
/mexecute [<plan-id>] [--slice <NN>]
```

With **`--slice <NN>`** the run ships that slice's stories and no others, then returns. This is how `mship` invokes it — once per slice.

**Without `--slice`, every slice runs in order.** For a version-1/2 graph — whose single synthetic slice `00` spans every wave — that is **byte-for-byte the behaviour this skill always had**: same waves, same order, same barrier, same sweep. A plan written before slices existed therefore runs unchanged, and a user who wants the whole plan put through in one invocation still gets it by naming no slice.

Ask the tool for the slices; never read them off the graph yourself:

```
python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py plan slices <plan-id>
```

It returns them in order, each with its `acceptance` steps, its `stories`, and its current `status` (and tells you, via `synthesized`, when the single slice was composed for a legacy graph rather than declared). Run the slice named by `--slice`; with no `--slice`, run each `slices[]` entry in turn, in the order returned.

**Narrowing the unit of a run to a slice is what gives something else a place to decide between slices.** `mexecute` itself decides nothing about what runs next: it has no between-wave gate, it does not choose the next slice, and it never re-plans. It ships the slice it was given and returns the result.

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
- `resume_slice` — the first slice that is not `applied`. With no `--slice`, that is where the sequence of slices picks up; with `--slice <NN>`, the named slice wins and this is context only.
- `pending_stories` — the stories to actually dispatch. Anything already `applied` is not in the list and is skipped.

**Scope the resume to the slice in hand.** The slice in scope is `--slice <NN>` if given, else `resume_slice` and then each later slice in turn. Intersect `pending_stories` with that slice's `stories` (from `plan slices`) and start at the first of its waves that still holds one — a wave of a *later* slice is not this run's business, and neither is a story that belongs to one.

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

3. **Per-repo integration branches.** Get the name from the tool — the `integration` field of `worktree names` (below), which is per-plan (**not** per-slice, which is what lets slice N+1 start from slice N's merged tip), so any story id in the plan yields it. Then, in each repo listed in `plan.yaml`, use git yourself to create that branch from the repo's current tip if it does not already exist. **Greens merge there, never into the repo's live working branch.** Record the repo→branch map through the tool, never by hand:

   ```
   python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py state set-plan <plan-id> --status in-progress \
     --integration-branches '{"<repo>": "<branch>"}'
   ```

   That is the same verb step 2 used, now carrying the map — one write for the status and the branches together. `integration_branches` is **never** hand-written into `state.yaml`.

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

Drive the waves with the `Workflow` tool. The script loops waves **in order** (a wave is a hard barrier — never pipeline across waves), and within each wave fans out one agent per story. Use this shape as the skeleton (fill in from `plan.yaml`, `mc.py plan slices`, and Step 0's resolution):

```javascript
export const meta = {
  name: 'mexecute-<plan-id>',
  description: 'Ship slice <NN> of <plan-id>: worktree-isolated waves, barrier merge, retry 3 per run, slice acceptance, conformance sweep',
  phases: [ { title: 'Wave 1' }, /* one per wave */ { title: 'Sweep' } ],
}

const SLICE = /* the slice in scope: `--slice <NN>`, or each `plan slices` entry in turn */
const WAVES = /* the slice's waves, in order, only those at/after `resume_wave` */
const RETRY_MAX = 3   // per run
const RUN = /* the value `mc.py state run-increment` returned in Step 1 */
const PENDING_STORIES = /* `pending_stories` ∩ SLICE.stories */
let acceptance = null

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

  // BARRIER — a merge agent applies the discretion merge, then runs this wave's own
  // `validation` steps against the merged integration branch (after the merge, before the
  // next wave is cut from it), persists two-level state, and returns the wave outcome
  // (merged / halt / deferred breaks). A failed check is an unmergeable wave: no advance.
  const barrier = await agent(barrierPrompt(wave, results), {
    label: `barrier:w${wave.wave}`, phase: `Wave ${wave.wave}`, schema: BARRIER_SCHEMA,
  })
  if (barrier.halt) return barrier   // one of the three halt-and-report conditions

  // SLICE ACCEPTANCE — on the slice's last wave, not the plan's. Merging is not
  // the completion criterion; this is.
  const isLast = wave === WAVES[WAVES.length - 1]
  if (isLast) {
    acceptance = await agent(sliceAcceptancePrompt(SLICE), {
      label: `slice-acceptance:${SLICE.slice}`, phase: `Wave ${wave.wave}`, schema: ACCEPTANCE_SCHEMA,
    })
    // recorded via `mc.py state set-slice --acceptance` (below)
  }
}

phase('Sweep')
const sweep = await agent(mverifySweepPrompt(SWEEP_DISCRIMINATOR, SLICE), { label: 'mverify-sweep', phase: 'Sweep' })
return { done: true, slice: SLICE.slice, acceptance, sweep }   // the session persists both after this returns
```

In the skeleton, `STORY_REPORT_SCHEMA` is the parsed `${CLAUDE_PLUGIN_ROOT}/schemas/story-report.schema.json`; `BARRIER_SCHEMA` is a small inline shape you define for the barrier agent's return (`{ halt: bool, reason, merged: [...], deferred_breaks: [...], spec_defects: [...] }`); `ACCEPTANCE_SCHEMA` is likewise a small inline shape (`{ result: 'pass'|'fail'|'unconfirmed'|'not-run', steps: [...] }`); `SWEEP_DISCRIMINATOR` is the explicit sweep-mode value `/mverify` is invoked with; and `storyPrompt`/`barrierPrompt`/`sliceAcceptancePrompt`/`storyOf`/`mergeAttempts` are illustrative helpers you build from `plan.yaml` + `plan slices` + Step 0's resolution. Adapt freely — the **contract** is: waves in order, one agent per story at full concurrency (each creating its own worktree), a barrier that merges, then checks the merged branch before the next wave is cut, then persists, bounded retry 3 per run, Slice Acceptance on the slice's last wave, then the sweep. `return { … }` ends the `Workflow`; the acceptance and sweep results are persisted **after** that return, by the `mexecute` session, not by the script (see Slice Acceptance and Step 3).

**With no `--slice`, wrap the whole loop above in an outer loop over the slices in order** — each slice's waves, its barrier, its acceptance, then the next slice's. There is still no gate between them; the sweep runs once at the end of the run.

**Names for every dispatch.** Before building a story prompt, ask the tool for that (story, run, attempt)'s names:

```
python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py worktree names <plan-id> <story-id> --run <n> --attempt <n>
```

It returns `integration` (the branch to cut from and merge back into), `story` (the branch this attempt works on), and `worktree_path` (where the work tree goes). Pass all three into the prompt verbatim. Never assemble, guess at, or pattern-match a branch or worktree name yourself — this call is the only source of them, for the first attempt and every retry alike.

The compute-heavy parts are **agents** because a Workflow script itself has no filesystem/git access — it only orchestrates. Concretely:

- **Story agent** (worktree-isolated). Its prompt names exactly one story and carries the three names above. It: (a) **creates its own worktree** with `git worktree add` inside `repos/<repo>/`, on the `story` branch cut from that repo's `integration` branch (the merged tip of the previous wave), at `worktree_path`; (b) loads **only** its `PLAN-*.md`, that story's Context Files, and the repo `CATALOG.yaml` — nothing else; (c) implements the story in `repos/<repo>/` (its single target repo); (d) runs the story's **Post-Story Validation** from `plan.yaml` (`kind: exit-code` commands must exit 0; `prose` steps it interprets); (e) returns the `story-report.schema.json` shape — status, branch, worktree path, files changed, validation result, any `breaking_contract_change`, any `contract_revisions` it built against, and any `spec_defect`. `agent()`'s `isolation: 'worktree'` option is **unparameterized** — it selects neither repo nor branch — so it is **not** the mechanism of story isolation; real `git worktree` commands, run by the story agent itself, are.
- **Barrier/merge agent** (one per wave, at the barrier). It applies the **merge/salvage discretion** (below), merges greens into the integration branch with git, then **runs that wave's own `validation` steps — the barrier check — against the merged integration branch**, records each story through `mc.py state set-story` (Step 4) — including each merged story's `contract_revisions` — removes merged worktrees with `git worktree remove`, keeps failed ones, and returns whether to `halt`. The check runs **after the merge and before the next wave is cut** from that branch; a wave whose barrier check fails does not advance and is reported as an **unmergeable wave** (see below). `barrierPrompt` therefore carries the wave's `validation` steps from `plan.yaml` verbatim, alongside its story results.
- **Slice-acceptance agent** (the slice's last wave only): runs the slice's `acceptance` steps from the plan graph. See **Slice Acceptance** below.
- **mverify sweep agent** (Step 3): invokes `/mverify` across every repo the slice touched, with the explicit sweep discriminator.

**If the `Workflow` tool is unavailable** in the environment, emulate the same structure by hand: for each wave in order, dispatch the story agents concurrently (`Agent` calls, one message — each story agent creates its own worktree with `git worktree add`), then run the **retry loop to exhaustion**, then the barrier/merge yourself, then run that wave's `validation` steps against the merged integration branch — the same ordering the Workflow path uses: after the merge, before you cut the next wave — then state persistence, then advance; on the slice's last wave, run Slice Acceptance and record it. What matters is the model — waves in order, worktree-per-story, barrier discretion and the merged-branch check that follows it, retry 3 per run, slice acceptance, sweep — not the tool. The `mc.py` calls are identical either way.

## The Barrier: Merge / Salvage Discretion

The barrier is two steps, and their order is fixed: **the per-story merge discretion below, then the check against the merged result.**

At each wave barrier, wait for all stories (including retries), then decide **per story** — this is **agent discretion, not a strict green-only gate**, and it is yours, not the tool's:

- **Good** — passed Post-Story Validation → merge its branch into the repo integration branch.
- **Salvageable** — mostly right, small fixable gap → salvage (a targeted fix on its branch) then merge, or fold into a retry.
- **Reusable** — wrong overall but has usable parts → keep the worktree, note what to reuse.
- **Mergeable** — independently, conflict-free.

Same-wave stories target **disjoint modules by construction** (mplan guarantees module-disjoint `target_paths`), so merges should be **conflict-free**. An unexpected merge conflict signals a **planning error** — the agent **reports it and halts the wave** rather than blindly auto-resolving; it may salvage if it can do so sensibly, but it never guesses at a resolution.

### The Barrier Checks the Merged Result

Merging the greens is not the end of the barrier. Once they have landed on the integration branch, run **the wave's own `validation` steps, taken from `plan.yaml`'s `waves[]` entry, against that merged integration branch**: every `kind: exit-code` step must exit 0, and a `kind: prose` step you cannot honestly confirm is a fail, not a pass.

**The ordering is fixed: after the merge, and before the next wave is cut** from that branch. Both halves carry weight — before the merge there is nothing to check, and once the next wave has been cut its worktrees are already built on the branch the check was meant to clear.

This is the only check in the whole run that sees more than one story's output at once, and that is exactly why it exists. `mplan` guarantees same-wave stories write **disjoint paths**, which is what makes the merge conflict-free — and says nothing at all about whether the merged *behaviour* holds. **Disjoint files are not disjoint behaviour.** A story that passes its own Post-Story Validation alone and breaks against its wave-mates was previously invisible until the slice's acceptance ran, a whole slice later.

**A failing barrier check is an unmergeable wave** — halt condition 2, which already exists. It is **not a fourth halt condition**: the list below stays at three, and an unmergeable wave is simply now reached two ways, by a same-wave merge conflict you cannot sensibly resolve *or* by the merged branch failing the wave's own check. Either way the wave **does not advance**, and the report names the failing step.

A graph below version 4 carries no `waves[].validation` at all, so there is nothing for the barrier to run and it behaves exactly as it always did.

### The Barrier Records What Each Merged Story Was Built Against

At merge time — and only at merge time, because that is when the fact becomes true of shipped code — the barrier records, **per merged story**, the `revision` of every shared interface that story was built against. Read each revision from the **shared catalog** (`context/shared/spec/CATALOG.yaml`, whose `revision` is preserved rather than derived) for the TAGs the story consumed, and write the map through the tool:

```
python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py state set-story <plan-id> <story-id> --status applied \
  --contract-revisions '{"<IFACE-TAG>": <revision>}'
```

A story that consumed no shared interface records nothing; the option is simply omitted. The story agent's own `contract_revisions` (on its `story-report`) is the input where it supplied one — the barrier's job is to persist it against the merged story, not to re-derive it after the fact.

This is what makes the cross-repo sync point **versioned rather than permanent** (see Cross-Repo Change Flow): a later cascade bumps the interface's revision, and `mc.py spec consumers <IFACE> --stale` then names exactly the delivered stories built below it.

## Slice Acceptance

**Merging is not the completion criterion.** On the **slice's last wave** — after that wave's barrier — run the slice's `acceptance` steps, exactly as `mc.py plan slices` returned them: every `kind: exit-code` step must exit 0; a `kind: prose` step is interpreted honestly, and one that cannot be confirmed is a `fail`, not a pass.

Record the result through the tool, in the same call that writes the slice's status:

```
python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py state set-slice <plan-id> <NN> --status applied --acceptance pass
python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py state set-slice <plan-id> <NN> --status failed  --acceptance fail
```

`--acceptance` takes `pass | fail | unconfirmed | not-run`, and `set-slice` keeps the ledger's `slices_total`/`slices_applied` in step in the same write. **A slice whose stories all merged green but whose acceptance failed is `failed`** — and that is why a slice's status is *written* rather than derived: no function of story statuses can express that outcome. (`waves[].status` is the opposite case: it *is* derived, by `set-story`, in the same write, and must stay so.)

Slice Acceptance is the difference between a slice's stories having merged and the slice having **worked**. It is what `mship` reads at the boundary, and it is what makes a slice a demonstrable increment rather than a bookkeeping milestone.

## `spec_defect`: a Finding Returned Upward, **Not** a Fourth Halt

A story agent may report on its `story-report` that its **spec** — not its difficulty — is the problem, in one of exactly two ways:

- **`contradicts-contract`** — the story's spec and a contract it must honour cannot both be satisfied.
- **`inexpressible`** — the spec describes something the code cannot be made to do.

**The run continues.** A `spec_defect` does not halt the wave, does not halt the run, and is **not** a fourth halt condition — the halt conditions remain the three below, unchanged in number and in meaning. Carry the defect through the barrier, return it with the slice result, and let **`mship`** act on it at the slice boundary by re-planning what remains or cascading a contract change. `mexecute` never rewrites a spec, including one it just reported as defective.

**The enum is closed, and deliberately narrow.** A story agent able to declare its own spec wrong has an escape hatch from difficult work, so these have **no representation** and are never a `spec_defect`:

- "harder than expected"
- "the approach seems poor"
- "I would have designed this differently"

Each of those belongs in `notes` on a **failed attempt**, where the retry budget is the correct response to it.

## Bounded Auto-Retry

Up to **3 attempts per failed story, per run** (`RETRY_MAX = 3`). `retries` resets at the start of each run; exhaustion halts the run. Each attempt gets a **fresh worktree and branch** — a new `worktree names` call with the next attempt number — recorded in `state.yaml`. Total attempts across resumes are **unbounded by design** — each re-invocation of `mexecute` is the human's deliberate consent to grant a fresh budget. On exhaustion the story is left `failed` and the run **halts and reports** — it never advances a wave on red.

## Halt-and-Report Conditions

These are the **only** reasons a run stops early, and calling one is your judgment. Everything else — including conformance drift — defers to the run report. **All three are terminating**: the run halts, reports, and ends. There is no mid-run pause and no between-wave gate — the user acts on the report and resumes with a **fresh `mexecute` invocation**, which takes a new `run` number and a fresh per-run retry budget.

1. **Retry exhausted** — a story fails all 3 attempts (this run).
2. **Unmergeable wave** — reached two ways, and they count as one condition: a same-wave conflict the agent judges it cannot sensibly resolve or salvage (it reports rather than auto-resolving), **or the wave's barrier check failing against the merged integration branch**. Both say the same thing about the merged result — it is not fit to cut the next wave from — so the wave does not advance and the report names the conflict or the failing step.
3. **Significant breaking contract change** — a shared-interface break surfaced during implementation whose blast radius **cascades beyond the current story** (forces a shared-spec revision or changes across consuming repos). The run **halts, reports the break, and ends**; it does not wait in-place for a decision.

A breaking contract change whose blast radius is **contained** — it would only break code in the affected story — is **not** a halt: that story fails Post-Story Validation (so its worktree never merges), the failure is recorded as a `deferred_break` through `mc.py state set-story --deferred-break` (Step 4), and the **run continues**, folding it into the final report.

**Three, and only three.** A `spec_defect` is not a fourth (see above), and neither is conformance drift, a failed Slice Acceptance, or a contained break: those are results the run reports, not reasons it stops early. A failed **barrier check** is not a fourth either — it *is* condition 2, an unmergeable wave, and it adds nothing to this list.

**No human review between waves.** `mexecute` always runs every wave of the slice through; there is no between-wave gate and no mid-run prompt. Gating between **slices** belongs to `mship`, not here: a slice-scoped run simply ends at its slice boundary and returns, and that boundary is what gives `mship` somewhere to decide.

## Worktree Lifecycle

- A story targets **exactly one repo** → **one worktree**. Wave N worktrees branch from the run's per-repo **integration branch** (the merged tip of wave N−1).
- Names are never composed here: the branch and the worktree path come from `mc.py worktree names`, per story **and** per attempt, so they are unique per (run, attempt) by construction.
- **Every** worktree ref — per story **and** per retry attempt — is recorded in `state.yaml` under the story's `attempts[]`, via `mc.py state set-story --attempt <n> --branch <b> --worktree <w> --validation <r>`.
- **Merged (green)** worktrees are **removed** with `git worktree remove`, and the removal is recorded with `mc.py state set-story --attempt <n> --worktree-removed`; **failed/unmerged** ones are **kept and recorded** so the agent or a later run can inspect, reuse, or salvage them.
- **On resume**, reconcile with `mc.py worktree reconcile` and act on its verdicts (Pre-flight step 4): `remove` a worktree whose story is now `applied`, `keep` an unfinished one, and **report — never remove — an orphan**.

## Cross-Repo Change Flow

A story is single-repo, so a cross-repo change is *separate per-repo stories* joined by the frozen shared contract — never one story spanning repos:

1. `mspec` cascade freezes the shared-interface contract in `context/shared/spec/` and updates each consumer's spec to reference it.
2. `mplan` decomposes it into a producer story + one consumer story per repo. Every repo codes against the *frozen spec*, not the producer's code, so consumer stories needn't wait on the producer's implementation — same-altitude modules across repos may share a wave.
3. `mexecute` runs each story in its own single-repo worktree — no cross-repo worktree juggling.
4. `mverify` (the sweep) confirms every producer/consumer repo matches the frozen contract and flags any that bypassed the shared spec.

The **contract**, not any worktree, is the cross-repo sync point — code-level atomicity across repos is neither needed nor attempted.

**That sync point is versioned, not permanent.** The barrier records each merged story's `contract_revisions` (above), so a cascade run later in the same delivery can bump an interface's `revision` and `mc.py spec consumers <IFACE> --stale` will name exactly the delivered stories built below it. A contract that moves mid-delivery therefore produces **scheduled remediation** — `mship` appends it to the next slice — instead of code that silently conforms to a contract nobody holds any more. Freezing still means no consumer diverges *within* a revision; it no longer has to mean the contract cannot move.

## Step 3: Post-Ship Conformance Sweep

Once per run, after the wave loop (not per wave) — and with a slice-scoped run, that is **once per slice, over what the slice shipped**: invoke **`/mverify`** across every repo the run touched, **with the explicit sweep discriminator**. It is **detection-only** — it never rewrites code, spec, or change files — and it **writes no state itself**: it returns `{status, report, findings}`, whose `status` uses `plan-state`'s `clean|drift|not-run` vocabulary.

The **`mexecute` session** persists that returned result **once the `Workflow` returns** — not a barrier agent (the wave loop has ended by then, so none remains) and not the `Workflow` script (which has no filesystem access):

```
python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py state conformance <plan-id> --status <status> --report <report-path> --findings <n>
```

**The sweep is explicitly non-blocking — drift never halts the run.**

## Step 4: State, Telemetry & Report

- **Two-level state, written only through `mc.py state`.** Per-story/per-wave/per-attempt status and refs, conformance, and telemetry live in the plan-level `state.yaml`; whole-plan completion (`pending → in-progress → applied | failed`) is reflected in the project ledger `context/project/state.yaml`. One verb per transition, at every transition:

  ```
  python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py state run-increment <plan-id>
  python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py state set-plan     <plan-id> --status <status> [--integration-branches <json>]
  python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py state set-story    <plan-id> <story-id> --status <status> \
      [--attempt <n> --branch <b> --worktree <w>] [--validation <r>] [--worktree-removed] \
      [--deferred-break <json>] [--contract-revisions <json>]
  python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py state set-slice    <plan-id> <NN> --status <status> [--acceptance <r>] [--outcome <o>]
  python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py state conformance  <plan-id> --status <status> --report <path> --findings <n>
  python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py state telemetry    <plan-id> --cost <usd> --tokens <n> --wall-clock <s>
  ```

  **Each verb validates before it persists** — the equivalent of `mc.py validate plan-state` over the plan `state.yaml` and `mc.py validate project-state` over the ledger — and **refuses a write that would not validate, so an invalid state file cannot reach disk.** Validation is therefore no longer a step you run separately, and no longer one you can omit. `set-plan` writes both files in one call, so `state.yaml` and the ledger cannot end up disagreeing; `set-story` with `--attempt` appends or updates that attempt's `attempts[]` entry, derives the containing wave's status from its stories in the same write, and keeps `retries` consistent for the current run; `set-slice` is the only writer of the ledger's `slices_total`/`slices_applied`.

  **Nothing is hand-written into `state.yaml`, without exception.** The four fields that once had no verb — plan-level `integration_branches`, a story's `deferred_break`, an attempt's `validation` and `worktree_removed` — now have options above (`--integration-branches`, `--deferred-break`, `--validation`, `--worktree-removed`) and **must** go through them. Hand-writing was only ever safe against *shape* errors: the next verb's validation caught a malformed file, never a plausible-and-wrong value.
- **Telemetry, not estimates.** Record actual `cost_usd` / `tokens` / `wall_clock_s` with `state telemetry`. **There is no cost gate** — never gate on a pre-run estimate.
- **Logs/artifacts** under `context/project/out/<plan-id>/`.
- **Final report:** the slice shipped and its **acceptance result**, stories shipped/failed/deferred, waves run, parallelism, retries used, the conformance-sweep result, any deferred contained breaks, **any `spec_defect` a story agent reported** (returned upward for `mship`), and telemetry. If the run halted, name which of the three conditions and what the user must decide. Close the plan with `state set-plan <plan-id> --status applied|failed` once its last slice is done.

## What mexecute does / does NOT do

- **Does:** write code (the only skill that does), in `repos/<repo>/`; run the Workflow; run every git command the run needs — branch, `git worktree add`, merge, `git worktree remove`; validate, merge, check each wave's merged result at its barrier, retry, run the slice's acceptance, sweep; own both state files during the run.
- **Does NOT:** brainstorm or design (that's `mspec`); decide plan structure (that's `mplan`, read from `plan.yaml`); rewrite specs — **including a `spec_defect` it reported**, which it hands upward rather than acting on; decide which slice runs next, or whether to re-plan (that's `mship`); touch more than one repo per story; merge into a repo's live working branch; advance a wave on red; halt on conformance drift; ask `mc.py` to run git, or compose a name or a verdict `mc.py` owns.

## Asking Questions

None in-run: no between-wave gates and no mid-run prompt. Halt condition 3 (a **significant breaking contract change**) **terminates the run** with a report naming the break, its blast radius, and the options (revise the shared spec / change consuming repos / abort). The user acts on that report and **re-invokes `mexecute`** — there is no in-place wait for a decision.

Gating between **slices** is `mship`'s, not this skill's. A slice-scoped run ends at its slice boundary and returns its result — acceptance, defects, drift and cost — which is exactly what `mship` needs in order to decide, and exactly what `mexecute` declines to decide for it.
