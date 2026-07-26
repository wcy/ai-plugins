---
name: mverify
description: Use when you need to confirm that shipped code actually conforms to what a change/plan/spec said — triggers on phrases like "verify conformance", "check the code matches the spec", "did we implement the change", "conformance check", "verify the plan", "check cross-repo contracts", "audit coupling", or as the automatic post-ship sweep after mexecute. Read-only — it reports drift as a change-shaped diff, it never rewrites code or spec.
---

# Verify: Conformance From Change + Plan + Spec

`mverify` answers one question: **did the shipped code actually implement what a unit of executed work said it would?** It starts from the **change + plan + spec** files (the change docs, the plan graph, and the spec sections they reference) and checks the code in `repos/` against them. It is **read-only** — it produces a change-shaped conformance report and records results in state; it **never** rewrites code or spec (gaps feed a follow-up `mspec`/`mreverse` run).

This is `mexecute`'s **post-ship sweep** (its step 6), and it can be re-run standalone against any prior change/plan.

## What it detects

Three kinds of drift, all **detection only** (reported, never gated or rewritten):

1. **Change conformance** (`missing` / `extra` / `mismatch`) — for every module the change/plan touched, does the code implement the INTERFACE signatures and DATAMODEL types the change specifies? Flag surface that is missing, extra (present in code but not the contract), or mismatched (wrong signature/type/shape).
2. **Cross-repo conformance** (`cross-repo-drift`) — for a shared-interface change, do **all** producer/consumer repos the cascade covers actually match the frozen contract in `context/shared/spec/`? This is the "consistent across codebases" guarantee.
3. **Coupling detection** (`coupling`) — code-level coupling violations that spec-level validation can't catch: a module's IMPLEMENTATION importing another module's IMPLEMENTATION (not its INTERFACE), or a cross-repo reference that bypasses `context/shared/spec/`. See STANDARD-SPEC.md §"Dependency Rules".

## mverify vs mreverse

Both are read-only detectors; they differ by **starting point** (see also the plugin README):

- **`mverify` starts from the change + plan + spec files.** Given a unit of *executed work*, it confirms the code implements it. Runs as `mexecute`'s post-ship sweep; re-runnable against any prior change/plan.
- **`mreverse` starts from the code + spec.** It takes code as ground truth, reconciles the spec to match, and documents inconsistencies within and between repos. No change/plan is involved.

If the user wants "make the spec match the code," that's `mreverse`. If they want "confirm the code matches what we planned," that's `mverify`.

## Step 0: Determine the Verification Target

Resolve which change/plan to verify, in order:

1. An explicit argument (a plan id, a `PROJECT-CHANGE-<NNN>`, or a repo change file) in the user's message.
2. **Invoked as `mexecute`'s post-ship sweep:** the plan `mexecute` just ran — its `<plan-id>` is passed in.
3. Otherwise, the **latest** plan: read `context/project/state.yaml` and take the most recent `applied`/`in-progress` plan; fall back to the highest `<NNN>` directory in `context/project/plans/`.

A plan maps to its driving change via `plan.yaml`'s `project_change` and the plan's `change_file` references; use both the plan graph and the change docs as the verification frame.

## Step 1: Build the Verification Frame

**Read-only. Load only what scopes the check** — do not load the standards or unrelated specs.

1. Read the plan graph `context/project/plans/<plan-id>/plan.yaml` — the set of stories, the repos touched, and each story's `module`/`repo`/`change_file`/`target_paths`.
2. Read the driving change docs: the project-level index `context/project/changes/PROJECT-CHANGE-<NNN>-*.md` and each repo-level `CHANGE-<NNN>-*.md` it references. The **Affected Code Paths** and **Spec Files Modified** tables define exactly what to check.
3. From the change docs, note whether `scope: shared` — if so, the `consumers:` list plus every repo whose `CATALOG.yaml` lists the changed interface TAG are the cross-repo conformance set.
4. Build the **shard list**:
   - One **change-conformance shard** per `(repo, module)` the change/plan touched.
   - One **cross-repo shard** per changed shared interface, covering all its producer/consumer repos.
   - One **coupling shard** per touched module (or per repo for small repos).

Validate the plan graph on read before trusting it:

```
python3 ${CLAUDE_PLUGIN_ROOT}/schemas/validate.py plan-graph context/project/plans/<plan-id>/plan.yaml
```

## Step 2: Fan Out Conformance Shards (subagents)

Dispatch the shards **in parallel** (single message, multiple Agent tool calls). Each shard is **blind to the others** — name its exact scope (the repo, the module's source dir, the spec/contract files, the change-doc rows) in the prompt, and load only those. This keeps each agent's context minimal and lets conformance scale across a large plan.

Each shard reads code as it exists in `repos/<repo>/` and compares it to its contract:

- **Change-conformance shard** — compare the module's implemented surface against its `*-INTERFACE.md` / `*-DATAMODEL.md` spec **and** the change doc's Affected Code Paths rows for that module. Report each `missing` / `extra` / `mismatch` with the spec surface, the code path, and the exact signature/type affected.
- **Cross-repo shard** — for the changed shared interface, read the frozen contract in `context/shared/spec/<IFACE>/` and check each producer/consumer repo's code against it. Report `cross-repo-drift` for any repo that diverges, and flag any repo that reaches the contract's data **without going through the shared spec** (a bypass).
- **Coupling shard** — scan the module's IMPLEMENTATION-level code for imports of other modules' IMPLEMENTATION (only INTERFACE is allowed) and for cross-repo references that don't route through `context/shared/spec/`. Report each as `coupling`.

**Each shard must return the `conformance-report.schema.json` shape** (its `scope` + a `findings[]` array). Instruct shards to return that JSON object as their final message. A clean shard returns `findings: []` and `clean: true`.

## Step 3: Aggregate + Write the Change-Shaped Report

1. Collect the shard reports. Validate each against the schema (drop/re-request any malformed one):

   ```
   python3 ${CLAUDE_PLUGIN_ROOT}/schemas/validate.py conformance-report <shard>.json
   ```

2. Merge into one **change-shaped** report at `context/project/out/<plan-id>/mverify-report.md`. Use the **same table shape as a change doc's Affected Code Paths** so a follow-up `mspec`/`mreverse` can consume it directly:

   ```markdown
   # mverify Report — <plan-id>

   Verified: <PROJECT-CHANGE-NNN> across repos: <list>. Result: <clean | N findings>.

   ## Findings

   | Type | Severity | Repo | Spec / Contract | Code Path | Detail |
   |------|----------|------|-----------------|-----------|--------|
   | mismatch | blocking | repo-a | AUTH-INTERFACE.md `login()` | src/auth.ts | code adds a 3rd arg not in the contract |
   | cross-repo-drift | blocking | repo-b | EVENT-BUS-DATAMODEL.md `Event.ts` | src/consume.ts | reads `ts` as string; contract is number |
   | coupling | warning | repo-a | — | src/orders/impl.ts | imports users/impl.ts (INTERFACE only allowed) |

   ## Suggested Follow-up

   - <which findings are an mspec change vs an mreverse reconciliation vs a code fix>
   ```

   When there are no findings, still write the report with an explicit "clean" line — a clean sweep is a real result.

## Step 4: Record + Report

1. **Record in plan-level state.** Update `context/project/plans/<plan-id>/state.yaml`'s `conformance` block: `status: clean | drift`, `report:` = the report path, `findings:` = the count. Validate on write:

   ```
   python3 ${CLAUDE_PLUGIN_ROOT}/schemas/validate.py plan-state context/project/plans/<plan-id>/state.yaml
   ```

   If verifying something with no plan directory (an ad-hoc change), skip the state update and just write the report, noting there is no plan to record into.

2. **Report to the user** the counts by type (change / cross-repo / coupling) and severity, and point at the report file.

3. **Do not halt or rewrite.** `mverify` only detects. When run as `mexecute`'s sweep, the run does **not** stop on drift — `mexecute` folds the result into its own report (an autonomous `/mquick` run escalates it; a gatekept run leaves it for the human to act on next via `mspec`/`mreverse`).

## What mverify does NOT do

- **No code changes.** `repos/<repo>/` is read-only input.
- **No spec/change/plan rewrites.** It reports drift; closing it is a separate `mspec` (respec) or `mreverse` (reconcile) run, or a code fix.
- **No gate.** It never blocks a run; coupling and conformance drift are surfaced, not enforced (the only spec-level gate is `mspec`'s authoring-time coupling check).

## Asking Questions

`mverify` is usually non-interactive (it runs as a sweep). If the verification target is genuinely ambiguous and no argument disambiguates it, ask which change/plan to verify as plain markdown prose before fanning out.
