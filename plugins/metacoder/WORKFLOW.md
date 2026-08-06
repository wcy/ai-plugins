# metacoder skill workflow reference

For each skill: what it needs to run (**Inputs**), what it produces or changes (**Outputs**), and where the workflow can go next (**Subsequent paths**).

The full pipeline, roughly: `mreq` → `mspec` → `mplan` → `mexecute` → `mverify` → `mfix`, with `mmigrate` as an independent bookkeeping sweep. `mquick` collapses `mspec` → `mplan` → `mexecute` (with `mreq` and `mverify` folded in) into one autonomous run.

`mreverse` is the code-first entry, but only into the *spec* — it writes no change document, so it cannot itself reach `mplan`; re-entry to the ship path is a manual `mspec` run (see F4).

Cross-cutting rules — exit codes, the hard-halt convention, and the full gate inventory — are in [Cross-cutting invariants](#cross-cutting-invariants) rather than repeated per skill. Where a behaviour below is the subject of an analysis finding, it links to it.

---

## mreq

Derives or brainstorms *why* something is being built, before `mspec` decides *how*.

**Inputs**
- Trigger phrases: "flesh out requirements", "write requirements", "what are we building this for", "derive requirements from the spec", "update requirements from the spec".
- Tier argument (`repo-a`, `shared`, `project`), explicit or inferred from the file being edited or the scope of the described need; asks if ambiguous.
- Mode: literal `audit` token routes to the AUDIT branch; otherwise **DERIVE** (explicitly from-spec) or **BRAINSTORM** (default). Bare `/mreq audit` sweeps **every** tier pairwise — the one path whose cost scales with the size of the existing set rather than the request; `/mreq <tier> audit` scopes it to one. A target genuinely named `audit` is `/mreq audit audit`.
- `mc.py req gate <tier>` — whether the tier already has valid `REQ-<NNN>` entries (amend) or is empty (seed).
- Record sequencing via `mc.py req change-resolve`/`req change-emit`; DERIVE additionally runs `mc.py check requirements <target>` to catch stranded traceability references.
- DERIVE mode reads `context/<target>/spec/COMMON/COMMON-OVERVIEW.md`, each module's `<TAG>-OVERVIEW.md`, and `CATALOG.yaml`'s `requirements:` back-reference.
- All branches read existing `context/<tier>/requirements/REQUIREMENTS.md` and `context/project/requirements/REQUIREMENTS.md` for contradiction checks.

**Outputs**
- Writes/amends `context/<tier>/requirements/REQUIREMENTS.md` (`### REQ-<NNN>-<mnemonic>: <Title>` entries; ids via `mc.py req mnemonic`/`req next`; never renumbers/deletes — uses `Status: superseded`/`stale`). Ids come from the highest id present, never a count, so reuse is structurally impossible. BRAINSTORM tags entries `Source: brainstormed`, DERIVE `Source: derived: <TAG>`. **AUDIT amends in place only** — it appends no entry and reads no spec.
- Exactly one `context/<tier>/requirements/changes/REQ-CHANGE-<NNN>-<slug>.md` per amending run, always `--status open` (mreq never closes its own record). A revision with no spec delta passes `--spec-change not-required`, which is what exempts it from `check handoff`'s open-record finding.
- Validation via `mc.py validate requirements` / `validate req-change`.
- Never touches `context/<target>/spec/**`, `CATALOG.yaml`, or spec-layer change docs — that's mspec's domain.

**Subsequent paths**
- **mspec** is the explicit downstream consumer: it later reads the open `REQ-CHANGE` record and closes it (`req change-close`) once the corresponding spec change is written.
- Unresolved contradictions (DERIVE mode, or unattended inside `mquick`) are flagged into the `REQ-CHANGE` record for a later BRAINSTORM amend or `/mreq audit`. AUDIT is the opposite: it resolves each contradiction with the user **in-run**, because a contradiction exists only in the comparison just made and leaves no artifact a later pass could act on.
- DERIVE's match key is `CATALOG.yaml`'s `requirements:` back-reference — a field `mspec` writes, not `mreq`, so a first DERIVE run against an unpopulated catalog matches nothing and appends everything (see F8).
- Broader pipeline: mreq → **mspec** → **mplan** → **mexecute** → **mverify** → **mfix**.

---

## mspec

Designs or updates a spec — the *how* — for a repo or the shared interface layer.

**Inputs**
- Trigger phrases: "spec out", "write a spec", "design the system", "architect this", "update the spec", "change the shared interface", or a described feature/change.
- Target resolution: explicit repo/interface name, the file being edited, or scope inference; asks if ambiguous.
- `mc.py spec mode <target>` — CREATE (no spec tree) vs UPDATE (tree exists), plus requirements-gate result.
- **CREATE mode**: requirements gate must pass, or mspec halts and points the user at `/mreq <target>`. If passed, reads `context/<target>/requirements/REQUIREMENTS.md` (+ project tier).
- **UPDATE mode**: reads `COMMON-OVERVIEW.md`, `CATALOG.yaml`; for `shared` target runs `mc.py spec consumers <IFACE>` for cascade consumers; pulls open `REQ-CHANGE` records via `mc.py req change-list <tier> --open`.
- Stage 1 (diagnostic) requires explicit user approval before Stage 2 (write) begins — mspec's one *approval* gate, and the point before which no file is written. It is not the only interaction: Phase 1a–1d each present a synthesis (product/change summary, module decomposition, lifecycles, decisions) and wait for confirmation.
- Stage 1 also dispatches a single **Risk & Ambiguity Scan** subagent — breaking changes, data migration, security, ambiguous requirements, dependency choices, and requirements drift as a backstop. It informs the clarification and writes nothing.

**Outputs**
- **CREATE**: full spec tree under `context/<target>/spec/` (COMMON-OVERVIEW/STACK/DECISIONS, module facets L1–L5, `CATALOG.yaml`) + baseline `CHANGE-000-initial-spec.md` with `status: complete` (no project-level index for baselines).
- **UPDATE**: modified spec files; repo-level `context/<repo>/changes/CHANGE-<NNN>-<slug>.md`; project-level `context/project/changes/PROJECT-CHANGE-<NNN>-<slug>.md`; closes covered `REQ-CHANGE` records.
- Sequencing and create-vs-continue are the tool's, never derived: `mc.py change resolve <repo> --slug` per affected repo, `change index-resolve --slug` for the index, then `change emit`. A document is **continuable only while its `status` is `pending`/`in-progress` *and* no plan directory corresponds to it**; every other status, and every baseline record, is terminal. Both are emitted `status: pending` and nothing ever transitions them (see F1).
- **Ordering is load-bearing:** Step 3c (`req change-close`) must run *after* Step 3a, because 3a allocates the `CHANGE-<NNN>` the closing back-link names and `req change-close` refuses a number that does not exist.
- **Shared-target UPDATE**: Phase 5 cascade — frozen `context/shared/changes/CHANGE-<NNN>-*.md` plus one `CHANGE-<NNN>-*.md` per consuming repo (or an "unaffected" note), all referenced in the project index (`scope: shared`, `consumers:`).
- Validation via `mc.py check all`, `check coupling`, `validate catalog/change`. Of `check all`'s five checks, `depends-on`/`coupling`/`catalog` findings **block** and must be fixed; `requirements` and `handoff` findings are reported but explicitly do **not** block — mspec states that no mspec run resolves them (see F3).

**Subsequent paths**
- Requirements gate fails (CREATE): halt, direct user to `/mreq <target>`.
- Requirements drift found mid-diagnosis: widen this change's scope now, or defer to a later `/mreq` pass.
- A needed shared interface doesn't exist yet: flag as a candidate CREATE run on `shared` first.
- `check coupling` finding a stale `depends-on` triggers the Phase 5 cascade.
- Normal next step: **mplan** turns the resulting change docs into an implementation plan (baseline CREATE records explicitly must not trigger mplan).
- **mquick** runs mspec as the first stage of its `mspec → mplan → mexecute` pipeline.

---

## mplan

Turns specs (or a change doc) into an executable, waved implementation plan. Writes no code.

**Inputs**
- Trigger phrases: "create plan", "generate plan", "make a plan", "plan the implementation", "break into stories".
- `mc.py plan scope` — returns `type` (full/incremental), `plan_id`, `plan_dir`, `project_change`, `change_files`.
- **Full plan**: every in-scope repo's `CATALOG.yaml` + referenced spec files, plus consumed `context/shared/spec/<IFACE>/*-INTERFACE.md`/`*-DATAMODEL.md`. If `repos/<repo>/src` (or `lib`) already has code, enters **Update Sub-Mode** (diff code vs. spec).
- **Incremental plan**: the driving `context/project/changes/PROJECT-CHANGE-<NNN>-*.md` and each referenced `context/<repo>/changes/CHANGE-*.md`.
- Optional scope subset from the user (e.g. "plan only repo-a").
- Wave/layer data via `mc.py plan waves <target>`; story keys via `mc.py plan story-id <file>` (never composed by hand).
- Precondition: specs must already be complete — mplan only reads spec content.

**Outputs**
- Story files `context/project/plans/<plan-id>/PLAN-{WW}-{SS}-{REPO}-{MODULE}.md` (`...-{SS}m-...-MIGRATION.md` for update-mode migrations), via `mc.py plan story-emit`.
- `plan.yaml` (immutable plan graph), the initial `state.yaml` (all stories `pending`), and the `context/project/state.yaml` ledger entry (`status: pending`) — **all three from one `mc.py plan emit <plan-id> < draft.json` invocation**, which validates each against its schema before persisting any. A refusal persists nothing; fix the draft and re-run.
- The draft carries only what judgment produced; `waves[]`, `parallel_group`, `file`, the `repos` list, the whole of `state.yaml`, and the ledger entry are the tool's. All three schemas are `additionalProperties: false` at every level, so anything an agent needs beyond them belongs in the story file, not the graph.
- **The graph contract mexecute depends on:** `target_paths` and `validation.post_story` are mandatory on every story; `validation.final` appears only on last-wave stories; same-wave siblings' `target_paths` must be **disjoint** (a `target_paths: []` Verify story is an explicit exemption, not a trivial pass). That disjointness is what makes mexecute's merges conflict-free by construction — so a merge conflict at a barrier is a *planning* defect, not a merge defect.
- No application code. Post-generation: fan-out subagent verdicts per story, then final `mc.py validate plan-graph|plan-state|project-state`.

**Subsequent paths**
- **Success**: hands `plan_dir`/`plan.yaml`/`state.yaml` to **mexecute**, which runs the plan and resumes via the project-state ledger entry.
- Fan-out validator flags issues: fix the story, re-run `plan emit` + `plan story-emit`, loop until clean before Final Validation.
- Any `mc.py` non-zero exit: hard halt, report diagnostics, fix and re-run (no hand-derived fallback). mplan states this more strictly than the shared trichotomy — see the note under [Cross-cutting invariants](#cross-cutting-invariants); it is harmless here only because mplan invokes no `check` verb, and every `validate` failure genuinely is a halt for it.

---

## mexecute

The only skill that writes feature code. Runs a plan as worktree-isolated waves and merges.

**Inputs**
- A finished plan at `context/project/plans/<plan-id>/` (`plan.yaml`, `state.yaml`), the repo `CATALOG.yaml`, and each story's `PLAN-*.md`.
- Trigger phrases: "execute the plan", "run the plan", "ship it", "build the stories", "run mexecute", or automatically after mplan.
- Optional explicit `<plan-id>` to `mc.py plan resolve`.
- Preconditions: `mc.py validate plan-graph` / `validate plan-state` must pass, and `repos/<repo>/` must be a real git repo — the run shells out to actual `git worktree` commands.
- Realized as a dynamic `Workflow` (waves in order, one agent per story, barrier, retry, sweep). If the `Workflow` tool is unavailable the same structure is emulated by hand with concurrent `Agent` calls; the `mc.py` calls are identical either way.
- Run counter: `mplan` seeds `run: 0` and `state run-increment` is unconditional and identical on a fresh plan and a resume, so the **first invocation runs as `1`**. Every branch, worktree path and `attempts[]` entry carries it.
- On resume: existing `state.yaml` (`run` counter, `resume_wave`, `pending_stories`, `integration_branches`) and leftover worktrees, reconciled via `mc.py worktree reconcile`.
- Every branch/worktree path comes from `mc.py worktree names <plan-id> <story-id> --run <n> --attempt <n>` — the sole source, for first attempts and retries alike.

**Outputs**
- Application code in `repos/<repo>/`.
- Per-repo integration branches (merge target for green stories — never the live working branch); per-story/attempt branches+worktrees, removed on success.
- State via `mc.py state` — `run-increment`, `set-plan`, `set-story`, `conformance`, `telemetry` — covering story/wave/attempt status, `attempts[]`, the conformance block and telemetry in the plan `state.yaml`, plus the project ledger (`pending → in-progress → applied|failed`). Each verb validates before it persists and refuses a write that would not validate, so an invalid state file cannot reach disk; `set-plan` writes both files in one call so they cannot disagree.
- **Four schema fields have no verb** and are hand-written into `state.yaml` alongside those calls: plan-level `integration_branches`, a story's `deferred_break`, and an attempt's `validation` and `worktree_removed` (see F6).
- A post-ship conformance sweep via **mverify** (`clean|drift|not-run`), persisted via `mc.py state conformance` **by the mexecute session after the `Workflow` returns** — not by the script (no filesystem access) and not by a barrier agent (the wave loop has ended).
- Logs under `context/project/out/<plan-id>/`; telemetry via `mc.py state telemetry`.
- Final report: stories shipped/failed/deferred, waves, retries, conformance result, contained deferred breaks, telemetry, and (if halted) which halt condition fired.

**Subsequent paths**
- **Clean sweep**: plan closed `applied` — done.
- **Drift found (non-blocking)**: recorded, doesn't halt the run; implies a follow-up **mfix** run.
- **Halt — retry exhausted** (a story fails all 3 attempts **this run**): user must act, then re-invoke **mexecute** fresh. The retry budget is per-run and resets on re-invocation; total attempts across resumes are unbounded by design, each re-invocation being the human's deliberate consent to a fresh budget. A story left `failed` is simply pending again next run.
- **Halt — unmergeable wave** (barrier conflict): signals a planning error — revisit via **mplan**, then re-run mexecute.
- **Halt — significant breaking contract change** (cascades beyond the story): report break/blast radius/options — revise via **mspec** cascade, change consuming repos, or abort — then re-invoke mexecute.
- **Contained breaking change**: not a halt — recorded as `deferred_break`, run continues.

---

## mverify

Read-only conformance check: does shipped code match spec/change/plan? Never gates, never rewrites.

**Inputs**
- Mode passed explicitly: `sweep` (mexecute's post-ship Step 3) or `standalone` (any prior change/plan, on demand).
- Optional plan/change argument (plan id, `PROJECT-CHANGE-<NNN>`, or a repo change file) via `mc.py plan resolve`.
- `plan.yaml` (validated via `validate plan-graph`); `PROJECT-CHANGE-*.md` and referenced `CHANGE-*.md` ("Affected Code Paths", "Spec Files Modified" tables).
- Shared-interface contracts in `context/shared/spec/<IFACE>/` when `scope: shared` (`mc.py spec consumers <IFACE>`).
- Shard list via `mc.py plan shards <plan-id> [--granularity repo|module]` — this list *is* the fan-out set, neither added to nor dropped from by reading. Granularity is the skill's judgment, expressed as the flag. Code under `repos/<repo>/` read-only.
- Ad-hoc change (no plan dir, `plan resolve` → `E_NOT_FOUND`): shard list derived by reading instead — the one sanctioned exception to the single-implementation rule, and only where no plan directory exists.
- Detection is delegated to the tool; **finding selection and severity (`blocking|warning|info`) are not** — the checkers supply the shard list and the mechanical checks, never the verdict.

**Outputs** — all under `context/project/out/<plan-id>/` (or `.../adhoc-<change-ref>/`)
- `shards/<shard-id>.json` — one conformance-report per shard.
- `mverify-report.md` — aggregated Markdown (Findings table + Suggested Follow-up), always written even when clean.
- `mverify-report.json` — machine-readable aggregate (`scope.kind: aggregate`, no `shard` field, `clean: true` iff no shard reported a finding). Shards themselves write nothing — they return their JSON and the orchestrator persists it.
- Ad-hoc output dir: `<change-ref>` is basename-normalized and charset-constrained to `[A-Za-z0-9._-]+` before substitution, so a user-supplied path cannot escape `context/project/out/`.
- State: **standalone mode with an existing plan dir only** — writes the `conformance` block via `mc.py state conformance`. Sweep mode returns the result to mexecute instead of writing state itself; standalone with no plan dir skips state entirely, which leaves ad-hoc drift invisible to every workspace-wide check (see F9).
- Never modifies `repos/`, spec, change, or plan content.

**Subsequent paths**
- **Sweep mode**: result folds into mexecute's report; does not halt the run.
- **Drift/findings (any mode)**: feeds a follow-up **mfix** run.
- **Findings implying a missing contract or spec tree**: **mspec** (respec, code is authoritative) or **mreverse** (reconcile spec from code) — the report's Suggested Follow-up section maps findings to which.
- **Clean**: still writes an explicit clean report, no action mandated.
- **Ambiguous target**: asks the user which change/plan to verify before fanning out.

---

## mfix

Closes a conformance report: per finding, decides whether code or spec is wrong, and fixes the root cause.

**Inputs**
- Trigger phrases: "fix the drift", "fix the mverify findings", "reconcile code and spec", "close the conformance report", or automatically after mverify reports drift.
- Findings source (in order): explicit report path → `conformance.report` in a plan's `state.yaml` → newest report under `context/project/out/*/`.
- `mc.py plan resolve` to find the owning plan; `mc.py validate conformance <report-path>` before trusting it.
- Optional user-specified finding subset.
- Re-checks each finding's `spec_ref`/`code_path` against current reality (report is evidence, not truth); signals via `mc.py check coupling`/`check depends-on`.

**Outputs**
- Code fixes: root-cause remediation, repo gates (formatter/linter/type-checker/tests) stay green.
- Spec fixes: per `STANDARD-SPEC.md` facet, `CATALOG.yaml` updated if exports/paths moved.
- Always a change file `context/<repo>/changes/CHANGE-<NNN>-<slug>.md` (`mc.py change resolve` + `change emit --plan not-required`), recording decision/reason per finding, retroactive-fix note if applicable. `--plan not-required` exempts it from the *stranded-change* and *unplanned-index* handoff rules — but **not** from the conformance-stranding rule, which only Step 6's standalone re-verify clears (see F2).
- **Scope fence:** may only change what a finding points at — no features, no restructuring past the finding's blast radius, and no repo-wide auto-fixers (`ruff --fix .`, codemods, tree-wide formatters), which would bury the change in unrelated pre-existing violations.
- Validation via `mc.py validate change/catalog`, `mc.py check depends-on`.
- Run report: per finding `code|spec|both|deferred|not-reproduced`, reason, files touched, any forced release/propagation.
- Deferred/unfixed items written into the affected spec file as tracked debt — prose, which no checker reads, so a deferral cannot clear the conformance block it came from (see F2). Lands on its own branch off the repo default branch — never an in-flight integration branch.

**Subsequent paths**
- Step 6 re-runs **mverify** in **`standalone`** mode (and `mc.py check all`) over the same scope to confirm findings closed — the mode matters, since only standalone rewrites the plan's `conformance` block, and that rewrite is what closes the handoff finding.
- Missing architecture ("no interface covers this surface"): deferred, handed to **mspec**.
- Repo with no spec tree: deferred, handed to **mreverse**.
- Fix needs new architecture, ambiguous blast radius, or requires publish/push/delete: escalates to the user instead of deciding unilaterally.
- Explicitly not chained forward by mfix itself: **mexecute** (new feature code), **mspec** (new contracts), **mreverse** (spec generation), **mplan** (re-planning) — named as the right skill for adjacent work, not invoked automatically.

---

## mreverse

Reverse-engineers spec from code (ground truth = code), or audits spec/code and cross-repo consistency. Read-only on code.

**Inputs**
- Trigger phrases: "reverse the spec", "the spec is stale", "sync the spec with the codebase", "audit the spec against the code", "do the repos agree", "check cross-repo consistency".
- Target resolution: explicit repo name(s), the repo containing the file being pointed at, or ask if ambiguous ("everything" = all repos).
- `mc.py spec mode <repo>` → CREATE or UPDATE.
- Source of truth: `repos/<repo>/` code (read-only) + `STANDARD-SPEC.md` for facets/layering/naming/`CATALOG.yaml` schema.
- UPDATE mode also reads existing `CATALOG.yaml` to detect deleted/new modules.
- Cross-repo phase (≥2 repos): each repo's `CATALOG.yaml` `shared_interfaces`, and `context/shared/spec/<IFACE>/` if present.
- **Gate:** Phase 1 stops for user confirmation of the proposed module decomposition before the Phase 2 deep-dive fan-out (the expensive step); skipped only on an explicit "fully autonomous" instruction. This is a second interactive gate in the pipeline alongside mspec's Stage 1 → Stage 2 boundary.
- No brainstorm, no described change — everything must trace to code an agent actually read.

**Outputs**
- Spec files under `context/<repo>/spec/`: `COMMON-OVERVIEW.md`, module facets (Overview/Datamodel/Interface/Dependencies/Implementation/Testing) written COMMON → modules L1→L5 → `CATALOG.yaml` last (`mc.py spec catalog-emit`). Four catalog fields are **preserved across emissions, not derived**, and stay judgment: each module's `layer` and `requirements`, each INTERFACE file's `exports`, and `shared_interfaces`. A module with no layer is refused rather than placed, so CREATE must seed `repo:`/`layers:` before the first emit.
- Phases 1–4b run **once per in-scope repo**; a single-repo run stops after 4b. Phase 2 readers return `inconsistency-report.schema.json` findings so Phase 4b's collation is mechanical.
- UPDATE mode: edits only stale sections; deletes module spec dirs + `layers:` entries for removed source code (called out in the report).
- Validation via `mc.py check all`, `validate catalog`, and `validate inconsistency-report` over both report aggregates.
- Intra-repo inconsistency report: `context/project/out/mreverse/<repo>-inconsistencies.md`.
- Cross-repo inconsistency report (≥2 repos): `context/project/out/mreverse/cross-repo-inconsistencies.md`.
- Completion summary (modules created/updated/deleted, surprising findings).
- Never modifies `repos/<repo>/` code, and never writes changes/, plans/, or `context/shared/spec/`.

**Subsequent paths**
- No automatic chaining — mreverse produces no change documents, so it cannot itself trigger mplan. It is the routing graph's only sink: `mfix` and `mmigrate` both defer work *to* it, and nothing carries that work back out in a machine-readable form (see F4).
- Fixing intra-repo inconsistencies found: a separate **mspec** → **mexecute** cycle.
- Resolving cross-repo inconsistencies: a deliberate **mspec** cascade → **mexecute** cycle.
- Changing the shared contract layer itself is out of scope — use **mspec**.

---

## mmigrate

Independent bookkeeping sweep: brings every tracked artifact's shape/identifiers/cross-references into schema conformance. Never touches spec/requirement/code content.

**Inputs**
- Trigger phrases: "migrate the artifacts", "check every catalog", "fix the schema", "audit the requirement ids", "the slugs are wrong", "fix broken depends-on links", "sweep the project files for drift", or after a schema version bump.
- All mechanical steps via `mc.py` (`check all`, `validate <kind>`, `req gate`, `req mnemonic`, `check depends-on/requirements/catalog`, `change resolve/emit`).
- Target inventory from `mc.py --json check all` → `data.targets` (every `context/<name>/` except `project`): `project-state`, `plan-graph`/`plan-state` per plan, `catalog` per target, `requirements-frontmatter`, `change-frontmatter`, `req-change-frontmatter`.
- Scope is strictly `context/**` — plugin source itself is out of scope; absent tiers are skipped, not flagged.

**Outputs**
- In-place mechanical repairs: schema-shape fixes, REQ id zero-padding/reordering, REQ heading migration to `REQ-<NNN>-<mnemonic>`, CHANGE/PROJECT-CHANGE/REQ-CHANGE filename↔frontmatter agreement, dedup of byte-identical duplicates, reference-integrity fixes (`depends-on`, catalog `facet`/`layer`/`requirements:`, malformed `Exports:` trailers).
- Any fix touching spec content gets its own `CHANGE-<NNN>-<slug>.md` (`mc.py change resolve`/`change emit --plan not-required`), then re-validated.
- Run report: `context/project/out/mmigrate/<date>/report.md` — every artifact classified clean / fixed (with CHANGE ref) / deferred (with owner named).
- Never writes spec/requirement/code content, never deletes/renames without deriving the correct value, and never edits a `REQ-CHANGE`'s `status`/`spec-change` in either direction — that transition belongs solely to `req change-close`. It likewise won't decide what `status` a CHANGE document should carry, which is part of why nothing ever closes one (see F1).

**Subsequent paths**
- Deferred items name the next skill explicitly in the report: **mreq** (missing mnemonic, orphan REQ, ambiguous duplicate id), **mspec** (content-level decisions), **mfix** (duplicate CHANGE/REQ-CHANGE files, dangling `spec-change` back-links, `exports`-vs-trailer disagreement where both parse, catalog coupling/handoff findings — left "for mfix/mreverse"), or a human (invented content, numbering collisions). `mfix` never names handoff findings as its own, so that hand-off does not land (see F3).
- Spec-content fixes triggered mid-sweep follow mfix's own change-doc discipline — both skills therefore act on the same `check depends-on`/`check catalog` output, split only by mechanical-vs-judgment (see F5).
- Does not itself perform **mplan**/**mexecute** (planning/shipping) or **mreverse** (generating a spec tree for a repo with none).

---

## mquick

One-shot autonomous run of the whole spec→ship loop, with exactly one clarification gate.

**Inputs**
- A target repo (or `shared`) and a change description; trigger phrases: "just build it", "spec and ship this", "mquick", "one-shot this feature".
- Phase A: resolves target(s) exactly as mspec Step 0 does, then `mc.py spec mode <target>` (its only direct tool call — a failed call is a hard error, no prose fallback).
- If mode is CREATE and the requirements gate fails: runs mreq's BRAINSTORM Phase 1 inline first.
- Runs mspec's Diagnostic/Clarify Stage 1 (diagnose + Risk & Ambiguity Scan, writes no files).
- Requires human approval at the end of Phase A — the single gate for the whole run; nothing else pauses after this.

**Outputs**
- **Phase B**: if mreq's Phase 1 ran, its Phase 2 appends confirmed `### REQ-<NNN>-<mnemonic>` entries to REQUIREMENTS.md — **before** mspec's write stage, and that order is load-bearing: otherwise the CREATE-mode requirements gate mquick just satisfied in Phase A would immediately re-fail. Then mspec's write stage produces spec files, change documents, validation, and (for `shared`) the cascade → a `PROJECT-CHANGE-<NNN>`. Inline contradictions flag into the run's `REQ-CHANGE` record rather than halting.
- **Phase C** (mplan): story files, `plan.yaml`, initial `state.yaml`, project-ledger entry.
- **Phase D** (mexecute): shipped code via worktree-isolated waves, barrier merges, bounded retry (N=3), validation, state, plus an automatic post-ship mverify sweep.
- **Phase E**: report — specced → planned → shipped → validated → conformed, story/wave/parallelism breakdown, retries used, open REQ-CHANGE records, conformance result, actual cost/telemetry (no pre-run cost estimate/gate).

**Subsequent paths**
- **Normal success**: full `mreq` (conditional) → `mspec` → `mplan` → `mexecute` (incl. mverify sweep) chain completes with the Phase E report.
- **Significant breaking contract change**: mexecute halts, mquick relays break/blast-radius/options and stops — user must act and re-invoke.
- **Contained breaking change**: doesn't halt, noted in the Phase E report.
- **Retry exhausted / unmergeable wave**: mexecute halts, mquick surfaces which story/wave and what's needed.
- **Conformance drift** from the post-ship sweep: doesn't halt, folded into the Phase E report (implies a likely follow-up **mfix**, though not explicitly auto-invoked).
- mquick never adds its own spec/plan/execute logic beyond the one requirements-presence check, and never gates between waves or on cost.

---

# Cross-cutting invariants

Facts that hold across skills, stated once here rather than re-read out of nine files.

**`mc.py` exit codes** (`mverify/SKILL.md` states the trichotomy; every skill assumes it):

| Exit | Meaning | Response |
|---|---|---|
| `0` | succeeded, no diagnostics | continue |
| `1` | ran and **reported findings** — a schema violation, a check finding, an unresolvable resource | a **result**, not a failure; read the diagnostics and continue at the step that asked |
| `2` | usage error, bad identifier, path escape, missing `PyYAML`/`jsonschema`, unparseable input | **failure** — halt the phase |

**Hard-halt rule.** Every skill states some form of "a failed invocation is a hard error; no prose fallback, ever" — a fallback would be a second implementation of the step. **`mmigrate` is the deliberate exception:** a schema `FAIL` or a `check` finding is the defect it exists to close, so the run continues past it; only exit `2` halts.

⚠️ **The skills do not agree on what exit `1` means.** `mverify` defines the trichotomy above (exit `1` is a *result* — read it and continue). `mplan` says flatly that "any `mc.py` call that exits non-zero halts the phase". `mmigrate` says a `FAIL` or finding is precisely what it exists to consume. In practice the three never collide, because each skill's actual call set makes its own rule correct — `mplan` invokes no `check` verb, so the only exit-`1` it can see is a schema failure, which genuinely is a halt. But the contract is stated three different ways for the same code, and a skill that later gains a `check` call would inherit the wrong one. `check all` legitimately exits `1` on any workspace carrying a handoff finding, which under F1/F2/F3 is most of them.

**Interactive gates** — the complete inventory:

| Skill | Gate | Blocking? |
|---|---|---|
| `mspec` | Stage 1 → Stage 2 boundary ("get explicit user approval"), plus per-step confirmations at Phase 1a–1d | yes |
| `mreverse` | Phase 1 module-decomposition confirmation, before the Phase 2 fan-out | yes, unless run autonomously |
| `mreq` | BRAINSTORM Phase 1 clarify; AUDIT Phase 2 resolves each contradiction in-run | yes (DERIVE is non-interactive) |
| `mquick` | Phase A only — the single gate for the whole run | yes, once |
| `mplan` | none | — |
| `mexecute` | none in-run; no between-wave gate | — (halts only on its 3 conditions) |
| `mverify` | none (asks only if the target is genuinely ambiguous) | — |
| `mfix` | escalates only on new architecture / materially different blast radius / publish-push-delete | rare |
| `mmigrate` | escalates only on genuine duplicate ids, content-demanding schema fields, outward-facing actions | rare |

**Writes code:** `mexecute` (feature) and `mfix` (remediation only). Everything else is documentation-only. `mmigrate` writes bookkeeping shape, never content.

# Artifact lifecycle — who creates, who mutates, who terminates

The column that matters is the last one. Where it reads *nobody*, the artifact accumulates.

| Artifact | Created by | Mutated by | Terminated by |
|---|---|---|---|
| `REQUIREMENTS.md` entry | `mreq` | `mreq` (amend in place), `mmigrate` (id/mnemonic shape) | `mreq` — `Status: superseded`/`stale` |
| `REQ-CHANGE-<NNN>.md` | `mreq` (`--status open`) | — | **`mspec`**, via `mc.py req change-close`; or born exempt with `--spec-change not-required` |
| `CHANGE-<NNN>.md` (repo) | `mspec`, `mfix`, `mmigrate` (`status: pending`) | `mspec` (continue rule) | **nobody** — see F1 |
| `PROJECT-CHANGE-<NNN>.md` | `mspec` (`status: pending`) | `mspec` (continue rule) | **nobody** — see F1 |
| `CHANGE-000-initial-spec.md` | `mspec` CREATE (`status: complete`) | never | born terminal |
| `plan.yaml` | `mplan` | never (immutable graph) | n/a |
| plan `state.yaml` | `mplan` | `mexecute` via `mc.py state` (+ 4 hand-written fields, F6); `mverify` standalone writes `conformance` | `mexecute` — `applied`/`failed` |
| `context/project/state.yaml` (ledger) | `mplan` (`pending`) | `mexecute` via `state set-plan` | `mexecute` — `applied`/`failed` |
| `mverify-report.{md,json}` | `mverify` | overwritten by the next run over the same plan | n/a — superseded, never closed |
| conformance finding | `mverify` | — | `mfix` fixes → re-verify overwrites the block. **A *deferred* finding is never terminable** — see F2 |
| `<repo>-inconsistencies.md` | `mreverse` | overwritten | **nobody** — no change doc, no plan hook (F4) |

# Tool-verb ownership map

Which skill invokes which `mc.py` verb. Verbs no `SKILL.md` names are dead weight in the shipped surface.

| Group | Verb | Invoked by |
|---|---|---|
| `spec` | `mode` | mspec, mreverse, mquick |
| | `catalog-emit` | mreverse |
| | `consumers` | mspec, mverify |
| | `layers` | **nobody** (F7) |
| `req` | `gate` | mreq, mmigrate |
| | `mnemonic`, `next` | mreq, mmigrate (`mnemonic` only) |
| | `change-resolve`, `change-emit` | mreq |
| | `change-close` | mspec (mreq/mmigrate reference it as *not theirs*) |
| | `change-list` | mspec |
| `change` | `resolve`, `emit` | mspec, mfix, mmigrate |
| | `index-resolve` | mspec |
| | *(no `close`/`set-status` verb exists)* | — (F1) |
| `plan` | `scope`, `waves`, `emit`, `story-emit`, `story-id` | mplan |
| | `resolve` | mexecute, mverify, mfix |
| | `shards` | mverify |
| `state` | `run-increment`, `set-plan`, `set-story`, `telemetry` | mexecute |
| | `conformance` | mexecute, mverify (standalone) |
| `worktree` | `names`, `reconcile` | mexecute |
| `check` | `all` | mspec, mreverse, mfix, mmigrate |
| | `coupling`, `depends-on` | mverify, mfix, mmigrate |
| | `requirements` | mreq, mmigrate |
| | `catalog` | mmigrate |
| | `handoff` | **nobody directly** — only transitively via `check all` (F3) |
| `validate` | all kinds | every skill at its close |
| `status` | — | **nobody** (F7) |

---

# Findings

Ordered by consequence. Each cites the evidence rather than asserting.

### F1 — `CHANGE-<NNN>` documents have no lifecycle terminator (structural)

The requirements layer has a closer: `mc.py req change-close` sets `status` and `spec-change` together, and `mspec` invokes it at Phase 3c. **The change layer has no counterpart.** `tools/change.py` exposes exactly three verbs — `resolve`, `index-resolve`, `emit` — and `emit` writes `INITIAL_STATUS = "pending"` (`change.py:65`). No skill and no verb ever transitions a repo change or a project index off `pending`:

- `mexecute` moves the **ledger** (`context/project/state.yaml`) `pending → in-progress → applied|failed`, and the plan `state.yaml`. Neither is the change document's front-matter.
- `mmigrate` refuses by rule — deciding "which `status` a change document should carry" is content, not shape.
- `mspec`'s continue rule *reads* `status` (`pending`/`in-progress` + no plan directory ⇒ continuable) but only ever writes `pending`.

Consequence: every shipped change document reads `pending` forever. `check.py:1181-1186` collects all of them into `walk.pending_changes`, which `mc.py status` renders as outstanding work — a list that grows monotonically and never drains. The asymmetry with `req change-close` looks like an omission rather than a decision.

### F2 — A deferred conformance finding permanently pins `check handoff` at error (correctness)

`_conformance_stage` (`check.py:1339-1369`) raises `E_HANDOFF` at **error** severity when a plan's `conformance` block records `findings > 0`, and suppresses it only when `highest_index > number` — where `highest_index` is the max **project-index** number in `context/project/changes/` (`check.py:1240`) and `number` is the plan's driving project change (`check.py:1404-1417`).

`mfix` writes a **repo-level** `CHANGE-<NNN>` with `--plan not-required`. That exempts the record from the stranded-change rule (`check.py:1191`) and the unplanned-index rule (`check.py:1245`) — both of which skip `plan_not_required` — but `_conformance_stage` has no such escape, and a repo-level change never raises `highest_index`. So the change document `mfix` writes cannot clear the conformance finding; only `mfix` Step 6's standalone re-verification can, by rewriting the `conformance` block (`mverify/SKILL.md:152-158`).

**Status: the documentation half is fixed.** `mfix/SKILL.md` previously ran the two rules together and implied the record settled conformance; Steps 5 and 6 now separate them, mandate `standalone` mode on the re-verify (the mode was unspecified, and it is what decides whether state is written at all), and state that the block returning to zero is what closes the finding.

**Status: the structural half is open.** The loop closes for findings that get *fixed*. It cannot close for findings `mfix` legitimately **defers** — re-verification re-detects them, so the block never returns to zero and the handoff finding stays raised at `error` for as long as the deferral stands. `mfix` now names this in its run report rather than leaving the check silently red, but that is disclosure, not a fix: **there is no machine-readable "accepted debt" state for a conformance finding**, and the tracked-debt note in the spec file is prose no checker reads. Closing it needs a schema/verb change — a `deferred` count in the `conformance` block that `_conformance_stage` subtracts, or an equivalent.

The plugin names this failure mode in its own words — `mfix/SKILL.md` Step 3: *"A gate nobody can satisfy trains everyone to ignore it — that's how the drift accumulated."*

### F3 — `check handoff` is disowned by every skill that runs it (ownership gap)

All four skills that invoke `check all` explicitly hand its handoff findings to someone else, and the terminal recipient never accepts them:

- `mspec:196, 408` — handoff findings "do not block … they name workspace-wide stranded artifacts **no `mspec` run resolves**."
- `mreverse:168-169` — "outside this checklist … their fix lies in artifacts mreverse does not write."
- `mmigrate:215-217` — "outside this skill's fence … leave them for **`mfix`/`mreverse`**."
- `mfix/SKILL.md` — **never mentions `handoff`.** It names `check coupling`, `check depends-on`, and `check all`, with no instruction for the handoff half of the last one.

No skill invokes `mc.py check handoff` directly. The stage-chain checker — arguably the plugin's best diagnostic, since it is the only thing that sees the pipeline end to end — has no consumer with a mandate to act on it.

### F4 — `mreverse` is a sink; the code-first entry cannot reach the ship path (routing gap)

`mreverse` writes no change documents and never invokes `mplan`, by explicit rule (`mreverse:258-260`). But it is the designated recipient of two deferrals: `mfix` hands it "repo with no spec tree" (`mfix:178, 189`), and `mmigrate` names it alongside `mfix` for content-level findings. Everything routed into `mreverse` must be **manually** re-entered through `mspec` to become shippable, and nothing records that pending re-entry in a machine-readable place — `mreverse`'s inconsistency reports are Markdown under `context/project/out/mreverse/`, which no checker reads.

The current text frames this as a property ("No automatic chaining — mreverse produces no change documents, so it cannot itself trigger mplan"). Structurally it is a dead end in the routing graph, and the only one.

### F5 — `mmigrate` and `mfix` both repair the same findings (overlap)

`mmigrate` Step 6 (`mmigrate:247-261`) edits `context/<repo>/spec/` — catalog fields, `depends-on` lines — and writes a change document "following `mfix`'s Step 5 discipline" with `--plan not-required`. `mfix` repairs the same `check depends-on`/`check catalog` findings from the other direction. The stated boundary is mechanical-vs-judgment, which is a real distinction, but both consume the *same checker output* and nothing prevents both from running against one defect and producing two change documents for it.

### F6 — `mexecute`'s "state only via `mc.py state`" invariant has four documented exceptions (doctrine)

`mexecute:216`: `integration_branches`, a story's `deferred_break`, and an attempt's `validation` and `worktree_removed` "you write into `state.yaml` alongside these calls" — because no verb takes an option for them. This sits directly under a heading asserting state is "written **only** through `mc.py state`". The mitigation is after-the-fact ("the next verb's validation refuses to persist over a file you got wrong"), which catches shape errors but not a plausible-but-wrong value. Four missing verb options would close it.

### F7 — Two shipped verbs are dead

`mc.py status` (`tools/status.py`, 84 lines) and `mc.py spec layers` are invoked by **zero** skills and referenced nowhere in `README.md`. `status` renders the whole stage walk — the closest thing the plugin has to an operator dashboard, and the natural consumer for F3's orphaned handoff findings. It ships and nothing routes anyone to it.

### F8 — `mreq` DERIVE's match key is written by a skill that runs after it (ordering)

DERIVE Phase 2 (`mreq:196-198`) matches drafted requirements against existing entries "using the target's `CATALOG.yaml` per-module `requirements:` back-reference as the match key". But `mreq` never writes `CATALOG.yaml` — it is `mspec`'s file (`mreq:308-309`) — and `mspec` populates `requirements:` from Phase 1e traceability (`mspec:173`). So a DERIVE run against a catalog with no `requirements:` entries yet has **no match key at all**, and every draft appends as new. First-run behaviour differs silently from steady-state, and the stability rule makes the resulting duplicates permanent.

### F9 — Ad-hoc conformance reports are invisible workspace-wide (minor)

`_conformance_verdict` (`check.py:1385-1391`) reads only `context/project/out/<plan-id>/mverify-report.json`. `mverify`'s ad-hoc path writes to `context/project/out/adhoc-<change-ref>/` (`mverify:174`) and skips the state write entirely (`mverify:160`). Consistent with "there is no plan to record into", but it means drift found by an ad-hoc verification is tracked by nothing — not the ledger, not `check handoff`, not `mc.py status`.
