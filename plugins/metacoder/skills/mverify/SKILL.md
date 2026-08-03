---
name: mverify
description: Use when you need to confirm that shipped code actually conforms to what a change/plan/spec said — triggers on phrases like "verify conformance", "check the code matches the spec", "did we implement the change", "conformance check", "verify the plan", "check cross-repo contracts", "audit coupling", or as the automatic post-ship sweep after mexecute. Read-only — it reports drift as a change-shaped diff, it never rewrites code or spec.
---

# Verify: Conformance From Change + Plan + Spec

`mverify` answers one question: **did the shipped code actually implement what a unit of executed work said it would?** It starts from the **change + plan + spec** files (the change docs, the plan graph, and the spec sections they reference) and checks the code in `repos/` against them. It is **read-only** — it produces a change-shaped conformance report and records results in state; it **never** rewrites code or spec (findings feed a follow-up `/mfix` run, or `mspec`/`mreverse` when the gap is a missing contract or a missing spec tree).

This is `mexecute`'s **post-ship sweep** (its Step 3), and it can be re-run standalone against any prior change/plan.

Every invocation carries an explicit **mode discriminator** — `sweep` (invoked by `mexecute`) or `standalone` — passed in, never inferred. The mode selects Step 4's behaviour: the sweep **returns** its result to the invoker, which persists it; standalone **writes** the plan's `conformance` block itself.

## What it detects

Three kinds of drift, all **detection only** (reported, never gated or rewritten):

1. **Change conformance** (`missing` / `extra` / `mismatch`) — for every module the change/plan touched, does the code implement the INTERFACE signatures and DATAMODEL types the change specifies? Flag surface that is missing, extra (present in code but not the contract), or mismatched (wrong signature/type/shape).
2. **Cross-repo conformance** (`cross-repo-drift`) — for a shared-interface change, do **all** producer/consumer repos the cascade covers actually match the frozen contract in `context/shared/spec/`? This is the "consistent across codebases" guarantee.
3. **Coupling detection** (`coupling`) — coupling violations that a contract comparison can't catch. `mc.py check coupling` and `mc.py check depends-on` detect them; **STANDARD-SPEC.md §"Dependency Rules" is the definition**, and this file does not restate it or describe how the detection works.

Detection is delegated. **Findings and their severity are not.** Whether a reported violation belongs in the report, what `type` it carries, and whether it is `blocking`, `warning`, or `info` is this skill's judgment on every one of the three kinds — the tool supplies the shard list and the mechanical checks, never the verdict.

## mverify vs mreverse

Both are read-only detectors; they differ by **starting point** (see also the plugin README):

- **`mverify` starts from the change + plan + spec files.** Given a unit of *executed work*, it confirms the code implements it. Runs as `mexecute`'s post-ship sweep; re-runnable against any prior change/plan.
- **`mreverse` starts from the code + spec.** It takes code as ground truth, reconciles the spec to match, and documents inconsistencies within and between repos. No change/plan is involved.

If the user wants "make the spec match the code," that's `mreverse`. If they want "confirm the code matches what we planned," that's `mverify`.

## Invoking the tool

Every mechanical step below runs `python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py`. Add `--json` when you need the `Result` envelope rather than the human-readable form.

**A failed invocation is a hard error: halt the phase and report it. There is no prose fallback** — re-deriving a step the tool owns would be a second implementation of it. Read the exit code:

- **`0`** — succeeded, no diagnostics.
- **`1`** — the command ran and reported diagnostics: a schema violation, a check finding, or an unresolvable resource. This is a **result**, not a failure. Read the diagnostics and continue at the step that asked for them.
- **`2`** — usage error, bad identifier, path escape, missing runtime prerequisite (`PyYAML`/`jsonschema`), or unparseable input. This is a **failure**. Halt.

## Step 0: Determine the Verification Target

The mode discriminator (`sweep` or `standalone`) is **passed in** and is never inferred from the plan id's provenance — a standalone run against the same plan id passes the same id, so provenance alone can't distinguish the two; only the explicit discriminator can.

Resolve the plan with:

```
python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py plan resolve [<plan-id>]
```

Pass `<plan-id>` when one was supplied — an explicit argument in the user's message (a plan id, a `PROJECT-CHANGE-<NNN>`, or a repo change file), or, in `sweep` mode, the just-run plan's id passed in alongside the discriminator. Omit it otherwise and let `plan resolve` name the target plan. It returns the plan id, its directory, status, run counter, resume wave, and pending stories.

An unresolvable plan (exit `1`, `E_NOT_FOUND`) means an ad-hoc change with no plan directory: keep the change ref as the verification target and see **Output File Path Patterns** for where its reports go.

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
python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py validate plan-graph context/project/plans/<plan-id>/plan.yaml
```

## Step 2: Fan Out Conformance Shards (subagents)

Dispatch the shards **in parallel** (single message, multiple Agent tool calls). Each shard is **blind to the others** — name its exact scope (the repo, the module's source dir, the spec/contract files, the change-doc rows) in the prompt, and load only those. This keeps each agent's context minimal and lets conformance scale across a large plan.

Each shard reads code as it exists in `repos/<repo>/` and compares it to its contract:

- **Change-conformance shard** — compare the module's implemented surface against its `*-INTERFACE.md` / `*-DATAMODEL.md` spec **and** the change doc's Affected Code Paths rows for that module. Report each `missing` / `extra` / `mismatch` with the spec surface, the code path, and the exact signature/type affected.
- **Cross-repo shard** — for the changed shared interface, read the frozen contract in `context/shared/spec/<IFACE>/` and check each producer/consumer repo's code against it. Report `cross-repo-drift` for any repo that diverges. The bypass half of this shard is mechanical, so it runs `mc.py check coupling <repo>` (below) for each repo in the conformance set rather than looking for bypasses by reading.
- **Coupling shard** — run the two checkers over the shard's target and judge what comes back:

  ```
  python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py check coupling <repo>
  python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py check depends-on <repo>
  ```

  Each returns a `CheckReport`; exit `1` means it found something. **STANDARD-SPEC.md §"Dependency Rules" is the definition of a violation** — the checkers implement it, this shard does not re-derive it and this file does not restate it. What the shard does is the judgment the checkers cannot make: decide which reported violations are real findings for *this* change, give each a `type` and a `severity`, and write the `detail` that explains it.

**Each shard must return the `conformance-report.schema.json` shape**, carrying **both**:

- **`shard`** — `change-conformance|cross-repo|coupling` — the *kind* axis (which sweep produced the report).
- **`scope.kind`** — `module | repo | cross-repo` — the *granularity* axis (what breadth the report covers).

The two axes are distinct even though `cross-repo` appears on both, with different meanings: as `scope.kind` it means the report spans repos rather than covering one; as `shard` it names the shared-interface conformance sweep. Shards **write nothing** — instruct each to return its JSON object as its final message; the orchestrator is what persists it. A clean shard returns `findings: []` and `clean: true`.

## Step 3: Aggregate + Write the Change-Shaped Report

1. **Write the shard files.** The **orchestrator** — not the shard — writes each returned JSON object to `context/project/out/<plan-id>/shards/<shard-id>.json`. Shards themselves write nothing; they only return the object (Step 2). `<shard-id>` is formed **per shard kind**, because no single template fits all three — a cross-repo shard spans every repo of one interface and has neither a single repo nor a module, and a coupling shard's per-repo variant has no module:

   | Shard kind | `<shard-id>` |
   |---|---|
   | change-conformance | `change-conformance-<repo>-<module>` |
   | cross-repo | `cross-repo-<IFACE>` |
   | coupling | `coupling-<repo>` (per-repo) or `coupling-<repo>-<module>` (per-module) |

   Every id is constrained to the charset `[A-Za-z0-9._-]+`, which confines every shard file to `context/project/out/<plan-id>/shards/`. Validate each written file against the schema (drop/re-request any malformed one):

   ```
   python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py validate conformance-report context/project/out/<plan-id>/shards/<shard-id>.json
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

   Severity is yours to assign — the checkers and the schema admit `blocking|warning|info`, and nothing upstream decides which one a finding carries. When there are no findings, still write the report with an explicit "clean" line — a clean sweep is a real result.

3. **Write the aggregate JSON.** Alongside the Markdown report, write `context/project/out/<plan-id>/mverify-report.json` — the machine-readable counterpart, an aggregate `conformance-report`: `scope.kind: aggregate`, **no** `shard` field, and `clean: true` **iff no shard reported a finding**. Schema-validate it on write exactly like a shard file:

   ```
   python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py validate conformance-report context/project/out/<plan-id>/mverify-report.json
   ```

   The aggregate is a call site of its own, which is why this file invokes `mc.py validate` at **four** sites across **three** kinds, not three sites: `plan-graph` (Step 1), `conformance-report` twice (each shard file and this aggregate, Step 3), and `plan-state` (Step 4, standalone only).

## Step 4: Record + Report

1. **Persist the result, keyed on the mode discriminator.** Three cases:

   - **`sweep`** — write **no** state. Return `{status, report, findings}` to the invoker (`mexecute`), which persists it. `status` uses `plan-state`'s `clean | drift | not-run` vocabulary — `mverify` carries no separate pass/fail vocabulary of its own; the triple is written into the `conformance` block verbatim by the invoker.
   - **`standalone`, plan directory exists** — write the `conformance` block yourself:

     ```
     python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py state conformance <plan-id> --status <clean|drift> --report <report-path> --findings <count>
     ```

     `state conformance` runs `mc.py validate plan-state` over the rendered `context/project/plans/<plan-id>/state.yaml` **before** it persists anything: a block that would not validate is refused and the file is left byte-identical. No separate validation call follows the write.

   - **`standalone`, no plan directory (ad-hoc change)** — skip the state update entirely; just write the reports, noting there is no plan to record into.

2. **Report to the user** the counts by type (change / cross-repo / coupling) and severity, and point at the report file.

3. **Do not halt or rewrite.** `mverify` only detects. When run as `mexecute`'s sweep, the run does **not** stop on drift — `mexecute` folds the returned result into its own report (an autonomous `/mquick` run escalates it; a gatekept run leaves it for the human to act on next via `mspec`/`mreverse`).

## Output File Path Patterns

Every artifact this skill writes lives under `context/project/out/<plan-id>/`:

- `context/project/out/<plan-id>/mverify-report.md`
- `context/project/out/<plan-id>/mverify-report.json`
- `context/project/out/<plan-id>/shards/<shard-id>.json`

**No plan directory (ad-hoc change):** `<plan-id>` is unresolvable, so `adhoc-<change-ref>` replaces it as the output **directory name** in all three patterns above. `<change-ref>` is taken verbatim from the user's message, and a change-file path is a legal form of it — so before substitution it is **basename-normalized** (every path component stripped) and constrained to the charset `[A-Za-z0-9._-]+`. This keeps a user-supplied path from escaping `context/project/out/` when it's substituted into a directory name.

Worked example: `context/project/changes/CHANGE-003-retry.md` → basename `CHANGE-003-retry.md` → normalized `CHANGE-003-retry` → output directory `adhoc-CHANGE-003-retry`, e.g. `context/project/out/adhoc-CHANGE-003-retry/mverify-report.md`.

## What mverify does NOT do

- **No code changes.** `repos/<repo>/` is read-only input.
- **No spec/change/plan rewrites.** It reports drift; closing it is a separate `mspec` (respec) or `mreverse` (reconcile) run, or a code fix.
- **No gate.** It never blocks a run; coupling and conformance drift are surfaced, not enforced (the only spec-level gate is `mspec`'s authoring-time coupling check).
- **No delegated verdict.** `mc.py` supplies the shard list and the mechanical checks; which findings the report carries and how severe each one is stays here.

## Asking Questions

`mverify` is usually non-interactive (it runs as a sweep). If the verification target is genuinely ambiguous and no argument disambiguates it, ask which change/plan to verify as plain markdown prose before fanning out.
