---
name: mverify
description: Use when you need to confirm that shipped code actually conforms to what a change/plan/spec said — triggers on phrases like "verify conformance", "check the code matches the spec", "did we implement the change", "conformance check", "verify the plan", "check cross-repo contracts", "audit coupling", or as the automatic post-ship sweep after mexecute. Read-only — it reports drift as a change-shaped diff, it never rewrites code or spec.
---

# Verify: Conformance From Change + Plan + Spec

`mverify` answers one question: **did the shipped code actually implement what a unit of executed work said it would?** It starts from the **change + plan + spec** files (change docs, plan graph, and the spec sections they reference) and checks the code in `repos/` against them. It is **read-only**: it produces a change-shaped conformance report and records results in state, and **never** rewrites code or spec. Findings feed a follow-up `/mfix` run, or `mspec`/`mreverse` when the gap is a missing contract or spec tree.

Runs as `mexecute`'s **post-ship sweep** (its Step 3), or standalone against any prior change/plan.

Every invocation carries an explicit **mode discriminator** — `sweep` (invoked by `mexecute`) or `standalone` — passed in, never inferred. The mode selects Step 4's behaviour: sweep **returns** its result to the invoker, which persists it; standalone **writes** the plan's `conformance` block itself.

**Slice scope — `--slice <NN>`.** An invocation may narrow the run to what one slice shipped; this is how `mship` verifies each slice as it lands rather than waiting for the whole plan. It passes straight through to `mc.py plan shards --slice <NN>` (Step 1.4) and does nothing else. **It is a scope narrowing only:** the mode discriminator, the shard kinds, which findings are reported and how severe each one is are all exactly what they are without it — the narrowed run is the same run over fewer shards. Omitted, the run covers the whole plan, as it always has.

## What it detects

Three kinds of drift, all **detection only** (reported, never gated or rewritten):

1. **Change conformance** (`missing` / `extra` / `mismatch`) — for every module the change/plan touched, does the code implement the INTERFACE signatures and DATAMODEL types the change specifies? Flag surface that is missing, extra (present in code but not the contract), or mismatched (wrong signature/type/shape).
2. **Cross-repo conformance** (`cross-repo-drift` / `stale-contract-revision`) — for a shared-interface change, do **all** producer/consumer repos the cascade covers actually match the frozen contract in `context/shared/spec/`, and is any delivered story still recorded against an older revision of that agreement?
3. **Coupling detection** (`coupling`) — coupling violations that a contract comparison can't catch. `mc.py check coupling` and `mc.py check depends-on` detect them; **STANDARD-SPEC.md §"Dependency Rules" is the definition**, and this file does not restate it.

Detection is delegated. **Findings and their severity are not.** Whether a reported violation belongs in the report, what `type` it carries, and whether it is `blocking`, `warning`, or `info` is this skill's judgment on every one of the three kinds — the tool supplies the shard list and the mechanical checks, never the verdict.

### A contract that moved is not a contract someone diverged from

`stale-contract-revision` is a **distinct finding type from `cross-repo-drift`**, and the difference is the remedy. Drift means somebody diverged from the contract: it wants investigating. A stale revision means the delivered code conforms to the agreement revision it was built against and to no other — the contract moved underneath conforming work — and it wants **scheduling**. Filing both under `cross-repo-drift` would put deliberate remediation and suspected mistakes in one bucket, and whoever read the report would have to re-derive the distinction the shard already knew.

The finding carries the gap rather than describing it: **`built_against`** (the revision the delivered story recorded) and **`current_revision`** (the revision the agreement now carries). Step 2's cross-repo shard produces it; see there for how the comparison is made.

### Spec depth is not drift

A module recorded at **`depth: contract`** in `CATALOG.yaml` has **no DEPENDENCIES, IMPLEMENTATION or TESTING facet by design** — those three are written by `mspec`'s deepen entry in the slice that builds the module. Their absence is **expected, and never a `missing` finding**. Verification of such a module covers its **contract only**: OVERVIEW, DATAMODEL and INTERFACE against the code.

This is not a leniency to be traded off against thoroughness. Without it the just-in-time deepen makes **every** not-yet-built module report as drift, and a report that is mostly false positives is one nobody reads — which is the failure this whole skill exists to prevent. A module at `full` depth (including one declaring no depth at all, which means `full`) is checked exactly as before.

## Invoking the tool

Every mechanical step below runs `python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py`. Add `--json` when you need the `Result` envelope rather than the human-readable form.

**A failed invocation is a hard error: halt the phase and report it. No prose fallback** — re-deriving a step the tool owns would duplicate it. Read the exit code:

- **`0`** — succeeded, no diagnostics.
- **`1`** — the command ran and reported diagnostics: a schema violation, a check finding, or an unresolvable resource. This is a **result**, not a failure. Read the diagnostics and continue at the step that asked for them.
- **`2`** — usage error, bad identifier, path escape, missing runtime prerequisite (`PyYAML`/`jsonschema`), or unparseable input. This is a **failure**. Halt.

## Step 0: Determine the Verification Target

The mode discriminator (`sweep` or `standalone`) is **passed in**, never inferred from the plan id — a standalone run can pass the same plan id as a sweep, so only the explicit discriminator distinguishes them.

`--slice <NN>` may be passed in alongside it. It plays no part in resolving the target — the plan is resolved the same way with or without it — and is carried forward to Step 1.4 unchanged.

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
3. From the change docs, note whether `scope: shared` — if so, the cross-repo conformance set is the `consumers:` list plus the repos `mc.py spec consumers <IFACE>` returns. Do not re-derive that scan by hand.
4. Take the **shard list** from the tool rather than building it:

   ```
   python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py plan shards <plan-id> [--granularity repo|module] [--slice <NN>]
   ```

   Returns one `ShardSpec` per shard — `shard` (`change-conformance|cross-repo|coupling`), `id`, `repo`, `module`, `interface` — in a stable order (change-conformance by `(repo, module)`, then cross-repo by TAG, then coupling); `--json` puts the list at `data.shards`. This list **is** the fan-out set for Step 2 — do not add to it or drop from it by reading.

   **`--slice <NN>` when the invocation carried one** (Step 0), and never otherwise. The tool restricts the list to the stories that slice holds; a slice the graph does not declare is `E_NOT_FOUND` (exit `1`), which is a target you were given wrongly, not a shard list to work around. Narrowing happens **here and nowhere else** — every step after this one treats the returned list as the whole of what it verifies, so no other step needs to know a slice was named.

   **Known limitation — one sanctioned exception:** when Step 0 resolves an ad-hoc change (no plan directory, `mc.py plan resolve` exits `E_NOT_FOUND`), there is no plan graph for `mc.py plan shards` to read, so the shard list is derived by reading instead. This is the sole exception to REQ-018's single-implementation rule; it does not extend to any path where a plan directory exists.

   Granularity is this skill's judgement, expressed as the flag: the tool defaults to per-repo coupling shards with no numeric threshold for "small repo"; pass `--granularity module` for per-module coupling shards instead.

   A workspace with no `context/shared/` tree yields no cross-repo entries and no diagnostic — that is a conforming single-repo workspace, not a failure to investigate.

Validate the plan graph on read before trusting it:

```
python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py validate plan-graph context/project/plans/<plan-id>/plan.yaml
```

## Step 2: Fan Out Conformance Shards (subagents)

Dispatch the shards **in parallel** (single message, multiple Agent tool calls). Each shard is **blind to the others** — name its exact scope (repo, module's source dir, spec/contract files, change-doc rows) in the prompt, and load only those. This keeps each agent's context minimal and lets conformance scale across a large plan.

Each shard reads code as it exists in `repos/<repo>/` and compares it to its contract:

- **Change-conformance shard** — compare the module's implemented surface against its `*-INTERFACE.md` / `*-DATAMODEL.md` spec **and** the change doc's Affected Code Paths rows for that module. Report each `missing` / `extra` / `mismatch` with the spec surface, the code path, and the exact signature/type affected. Read the module's depth first, and put it in the shard's prompt:

  ```
  python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py spec depth <target> <module>
  ```

  **At `depth: contract`, the absent DEPENDENCIES, IMPLEMENTATION and TESTING facets are expected and produce no finding of any kind** — see §"Spec depth is not drift". The shard checks that module's OVERVIEW/DATAMODEL/INTERFACE against the code and reports nothing about the three it does not have. `full` (which is also what no `depth` field means) is checked across every facet, unchanged.
- **Cross-repo shard** — for the changed shared interface, read the frozen contract in `context/shared/spec/<IFACE>/` and check each producer/consumer repo's code against it. Report `cross-repo-drift` for any repo that diverges. The bypass half of this shard is mechanical, so it runs `mc.py check coupling <repo>` (below) for each repo in the conformance set rather than looking for bypasses by reading.

  It also compares **recorded revisions** against the agreement's current one, which is a different question from whether the code matches the contract:

  ```
  python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py spec consumers <IFACE> --stale
  ```

  `--stale` reads every plan's `state.yaml` — not the spec tree, which only ever says what the contract is *now* — and returns `revision` (the current one) plus one `stale` entry per delivered story whose `contract_revisions[<IFACE>]` is below it, carrying `plan_id`, `story_id` and `built_against`. Report each as a **`stale-contract-revision`** finding carrying `built_against` and `current_revision`; an empty `stale` list is a real clean result, not a check that failed to run. Do not fold these into `cross-repo-drift` — §"A contract that moved is not a contract someone diverged from" is why. Severity is still yours: work one revision behind an additive bump is not the same as work behind a breaking one.

  ```json
  {
    "shard": "cross-repo",
    "scope": {
      "kind": "cross-repo",
      "interface": "AUTH",
      "change_ref": "PROJECT-CHANGE-014"
    },
    "findings": [
      {
        "type": "stale-contract-revision",
        "severity": "warning",
        "repo": "repo-b",
        "spec_ref": "context/shared/spec/AUTH/AUTH-INTERFACE.md",
        "surface": "AUTH agreement",
        "code_path": "src/session.ts",
        "built_against": 2,
        "current_revision": 4,
        "detail": "Story 03-02-repo-b-SESSION recorded AUTH revision 2; the agreement now carries 4. The code conforms to what it was built against — schedule remediation, do not investigate it as divergence."
      }
    ],
    "clean": false
  }
  ```
- **Coupling shard** — run the two checkers over the shard's target and judge what comes back:

  ```
  python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py check coupling <repo>
  python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py check depends-on <repo>
  ```

  Each returns a `CheckReport`; exit `1` means it found something. **STANDARD-SPEC.md §"Dependency Rules" is the definition of a violation** — the checkers implement it; this file does not restate it. What the shard does is the judgment the checkers cannot make: decide which reported violations are real findings for *this* change, give each a `type` and a `severity`, and write the `detail` that explains it.

**Each shard must return the `conformance-report.schema.json` shape**, carrying **both**:

- **`shard`** — `change-conformance|cross-repo|coupling` — the *kind* axis (which sweep produced the report).
- **`scope.kind`** — `module | repo | cross-repo` — the *granularity* axis (what breadth the report covers).

The two axes are distinct even though `cross-repo` appears on both, with different meanings: as `scope.kind` it means the report spans repos rather than covering one; as `shard` it names the shared-interface conformance sweep. Shards **write nothing** — instruct each to return its JSON object as its final message; the orchestrator is what persists it. A clean shard returns `findings: []` and `clean: true`.

## Step 3: Aggregate + Write the Change-Shaped Report

1. **Write the shard files.** The **orchestrator** — not the shard — writes each returned JSON object to `context/project/out/<plan-id>/shards/<shard-id>.json` (Step 2: shards write nothing, only return the object). `<shard-id>` is the `id` field of that shard's `ShardSpec` entry from `plan shards` (Step 1) — the tool forms it per shard kind, because no single template fits all three: a cross-repo shard spans every repo of one interface and has neither a single repo nor a module, and a coupling shard's per-repo variant has no module:

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
   | stale-contract-revision | warning | repo-b | AUTH-INTERFACE.md (rev 4) | src/session.ts | 03-02-repo-b-SESSION built against rev 2; schedule remediation |
   | coupling | warning | repo-a | — | src/orders/impl.ts | imports users/impl.ts (INTERFACE only allowed) |

   ## Suggested Follow-up

   - <which findings are an mspec change vs an mreverse reconciliation vs a code fix>
   ```

   Severity is yours to assign — the checkers and the schema admit `blocking|warning|info`, and nothing upstream decides which one a finding carries. When there are no findings, still write the report with an explicit "clean" line — a clean sweep is a real result.

3. **Write the aggregate JSON.** Alongside the Markdown report, write `context/project/out/<plan-id>/mverify-report.json` — the machine-readable counterpart, an aggregate `conformance-report`: `scope.kind: aggregate`, **no** `shard` field, and `clean: true` **iff no shard reported a finding**. Schema-validate it on write exactly like a shard file:

   ```
   python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py validate conformance-report context/project/out/<plan-id>/mverify-report.json
   ```

   The aggregate is a call site of its own: this file invokes `mc.py validate` for `plan-graph` (Step 1) and for `conformance-report` on both the shard files and this aggregate (Step 3). Step 4's plan-state write is validated inside `mc.py state conformance`, so no `validate plan-state` call appears here.

## Step 4: Record + Report

1. **Persist the result, keyed on the mode discriminator.** Three cases:

   - **`sweep`** — write **no** state. Return `{status, report, findings, deferred}` to the invoker (`mexecute`), which persists it. `status` uses `plan-state`'s `clean | drift | not-run` vocabulary — `mverify` carries no separate pass/fail vocabulary of its own; the result is written into the `conformance` block verbatim by the invoker.
   - **`standalone`, plan directory exists** — write the `conformance` block yourself:

     ```
     python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py state conformance <plan-id> --status <clean|drift> --report <report-path> --findings <count>
     ```

     `state conformance` runs `mc.py validate plan-state` over the rendered `context/project/plans/<plan-id>/state.yaml` **before** it persists anything: a block that would not validate is refused and the file is left byte-identical. No separate validation call follows the write.

   - **`standalone`, no plan directory (ad-hoc change)** — skip the state update entirely; just write the reports, noting there is no plan to record into.

2. **`deferred` is carried, not judged.** The count is whatever a **prior `mfix` run** already recorded in the plan's `conformance.deferred` (absent means `0`) — read it from `context/project/plans/<plan-id>/state.yaml` and pass it through unchanged in the returned result, so `check handoff` can score `findings - deferred` rather than `findings`.

   A deferred finding is **still detected and still reported**: it is still true, and re-detecting it every run is exactly what keeps the deferral in the ledger's accounting rather than out of sight. `mverify` does not subtract it from `findings` and does not suppress the finding in the report. Nor does it pass `--deferred` to `state conformance` — deciding that a finding *may* stand is `mfix`'s judgement about which artefact is authoritative, and `mfix` records the count itself after the standalone re-verify it asks for. This skill reports; it does not adjudicate. Carry the number; do not form an opinion about it.

3. **Report to the user** the counts by type (change / cross-repo / coupling) and severity, and point at the report file.

4. **Do not halt or rewrite.** `mverify` only detects. When run as `mexecute`'s sweep, the run does **not** stop on drift — `mexecute` folds the returned result into its own report.

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
- **No gate.** It never blocks a run; coupling and conformance drift are surfaced, not enforced.
- **No delegated verdict.** `mc.py` supplies the shard list and the mechanical checks; which findings the report carries and how severe each one is stays here.
- **No deferral.** It carries the `deferred` count and never sets, raises, or acts on it. A finding `mfix` accepted as debt is re-detected and re-reported here on every run.
- **No re-planning.** `--slice <NN>` narrows what this run looks at; deciding what the next slice should be off the back of a report is `mship`'s call.

## Asking Questions

`mverify` is usually non-interactive (it runs as a sweep). If the verification target is genuinely ambiguous and no argument disambiguates it, ask which change/plan to verify as plain markdown prose before fanning out.
