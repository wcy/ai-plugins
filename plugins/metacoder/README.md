# metacoder

> **v2 rebuild in progress — this document still describes v1.**
>
> `../../REDESIGN.md` is the authority on where this is going and why: ten skills reduce to five,
> the runtime instruction set drops from ~88k to ~24k tokens, the spec becomes a two-facet
> *execution* artifact, and a slice's acceptance must be demonstrated red-before-green.
>
> **Landed (build step 1).** The tool surface is reduced from ~45 verbs to 31 — the `req`, `todo`
> and `status` groups are gone, along with `spec depth`, `spec layers`, `state sweep` and
> `check requirements|todo|handoff`. Twelve schemas become seven. `state set-slice` gained
> `--armed` / `--confirmed` and their three refusals. The `mreq`, `mmigrate` and `mquick` skills
> are removed, as are `STANDARD-REQ.md`, `STANDARD-TODO.md` and `CHATFORM.md`.
>
> **Not yet done (build steps 2–6).** The remaining `SKILL.md` files and both standards still carry
> their v1 text and still document verbs that no longer exist. `tests/test_budget.py` holds each to
> a byte budget and `tests/test_conformance.py` carries one `xfail` naming exactly this gap.

A Claude Code plugin providing a structured spec-to-ship workflow for a **multi-repo workspace** that keeps codebases **coherent and consistent** — internally (code implements its spec; specs obey one standard) and across repos (a shared contract layer + change cascade + cross-repo conformance). Brainstorm and write specs per repository (and a shared cross-repo interface layer), generate modular implementation plans, deliver them **one slice at a time** as worktree-isolated waves, and verify conformance — manually gatekept or fully autonomous.

## Getting Started

**Prerequisites:** Claude Code, git (for `mexecute`'s worktree-isolated waves), and two Python
packages the skills' shared tool requires — `pip install pyyaml jsonschema`. See
[Installation](#installation) below: that one line is the plugin's only setup step and it is never
performed on your behalf.

1. Install the plugin (see [Installation](#installation) below).
2. In your workspace root, create the scaffold the skills expect:

   ```
   mkdir -p repos context/project/{plans,out,changes} context/shared/spec
   ```

   Put (or clone) your repositories under `repos/<repo>/`.
3. Spec a repo: `/mspec` (e.g. "spec out the auth service") — writes `context/<repo>/spec/` and a baseline `CHANGE-000-initial-spec.md`.
4. Turn a change into stories: `/mplan` — writes `context/project/plans/<NNN>-<slug>/` (story files, `plan.yaml`, `state.yaml`).
5. Ship it: `/mship` — delivers the plan **one slice at a time**, invoking `/mexecute` for each slice's worktree-isolated waves and `/mverify` over what it shipped, then deciding what the slices still outstanding should be. (`/mexecute` on its own still puts a whole plan through in one invocation.)
6. Re-check anytime: `/mverify` (conformance against any change/plan) or `/mreverse` (reconcile a stale spec back to the actual code, or audit consistency across repos).
7. Close what it found: `/mfix` — decides, per finding, whether the code or the spec is authoritative, and repairs the one that is wrong.
8. Keep the bookkeeping honest anytime: `/mmigrate` — sweeps every tracked artifact (plan/state/catalog YAML, change and requirements front-matter) against its own schema and identifier conventions, independent of any single change or plan.

**Already have a codebase and no specs?** Start with `/mreverse` instead of `/mspec` — it reverse-engineers the spec from code as ground truth.

**Want the whole loop in one shot?** `/mquick` collapses steps 3–5 into a single autonomous run with exactly one clarification gate.

## Workspace Layout

The workspace root is the directory Claude Code is run from. Source code lives under `repos/`; specs, plans, and output all live under `context/`:

```
<workspace>/
├── repos/
│   └── <repo>/             # repository source code
└── context/
    ├── <repo>/
    │   ├── spec/           # this repo's spec; CATALOG.yaml declares `shared_interfaces: [...]`
    │   ├── requirements/   # REQUIREMENTS.md + changes/REQ-CHANGE-<NNN>-*.md (mreq's own tier)
    │   └── changes/        # repo-level CHANGE-<NNN>-*.md (one per spec change, sequenced per repo)
    ├── shared/
    │   ├── spec/           # cross-repo interface contracts — OVERVIEW/DATAMODEL/INTERFACE only + CATALOG.yaml
    │   └── requirements/   # REQUIREMENTS.md + changes/REQ-CHANGE-<NNN>-*.md (mreq's shared tier)
    └── project/
        ├── plans/          # PLAN-{WW}-{SS}-{REPO}-{MODULE}.md (each story tagged with its repo)
        ├── out/            # execution output
        ├── requirements/   # REQUIREMENTS.md + changes/REQ-CHANGE-<NNN>-*.md (cross-cutting tier)
        └── changes/        # PROJECT-CHANGE-<NNN>-*.md — index referencing repo changes; drives mplan
```

A repo consumes a shared interface by listing its TAG in the repo `CATALOG.yaml` `shared_interfaces` field and adding the shared `*-INTERFACE.md` to its `depends-on`. Changing a shared interface **cascades**: `mspec` updates each consuming repo's spec, writes a repo-level change file per repo, and produces one project-level index in `context/project/changes/` that references them all.

## Contents

```
ai-plugins/                              # the marketplace repo
├── .claude-plugin/marketplace.json      # marketplace manifest
├── README.md                            # marketplace-level docs
├── LICENSE                              # MIT
├── pyproject.toml                       # runtime deps (PyYAML, jsonschema) + pytest config
├── tests/                               # tools/ test suite — NOT shipped
└── plugins/
    └── metacoder/                       # everything below here is what gets installed
        ├── .claude-plugin/plugin.json   # plugin manifest
        ├── README.md                    # this file
        ├── skills/
        │   ├── mreq/SKILL.md            # requirements-authoring skill
        │   ├── mspec/SKILL.md
        │   ├── mreverse/SKILL.md
        │   ├── mplan/SKILL.md
        │   ├── mverify/SKILL.md
        │   ├── mfix/SKILL.md
        │   ├── mexecute/SKILL.md
        │   ├── mship/SKILL.md           # the per-slice delivery loop
        │   ├── mquick/SKILL.md
        │   └── mmigrate/SKILL.md
        ├── tools/                       # the deterministic step layer every skill invokes
        │   ├── mc.py                    # the one entry point: ${CLAUDE_PLUGIN_ROOT}/tools/mc.py
        │   ├── core.py                  # workspace resolution, Result envelope, path/ident guards
        │   └── …                        # one module per command group (validate, change, spec,
        │                                #   req, plan, state, worktree, check, status)
        ├── shared/
        │   ├── STANDARD-SPEC.md         # referenced via ${CLAUDE_PLUGIN_ROOT}/shared/…
        │   ├── STANDARD-CHANGE.md
        │   ├── STANDARD-REQ.md          # referenced via ${CLAUDE_PLUGIN_ROOT}/shared/…, mreq only
        │   ├── STANDARD-TODO.md         # the standard context/project/TODO.md entries follow
        │   ├── PLAN-STORY-TEMPLATE.md   # story template, rendered by `mc.py plan story-emit`
        │   └── CHATFORM.md              # optional, opt-in interaction convention
        └── schemas/                     # JSON Schemas for CATALOG/plan-graph/state/reports/
                                        #   requirements/requirements-change
```

`tests/` and `pyproject.toml` sit at the **repository root, outside `plugins/metacoder/`**, and
deliberately so: the whole plugin directory is copied into every user's marketplace cache, and a
test suite is not part of what you installed.

Install is **marketplace-only** — the whole tree stays in the plugin's marketplace cache and
**nothing is copied into your project's `.claude/`**. Skills reach the standards, the story
template, and the tool at runtime through the single variable `${CLAUDE_PLUGIN_ROOT}`, which rebases
automatically to wherever the plugin was installed, with no absolute paths baked in. Nothing the
plugin runs writes back into its own installed tree either: `tools/mc.py` sets
`sys.dont_write_bytecode` before importing its package, so a run leaves no `__pycache__` in the
marketplace cache.

## Skills

These are Claude Code skills — Claude invokes them either automatically when your message matches their trigger description, or when you explicitly reference the skill by name (e.g. `/mspec`, `/mplan`).

| Skill | Writes code? | Trigger | Purpose |
|-------|:---:|---------|---------|
| `mreq` | no | "flesh out requirements", "write requirements", "what are we building this for", "derive requirements from the spec", "update requirements from the spec", etc. | Author or derive the requirements layer — user needs and business goals, the *what*, as distinct from the spec's *how* — at one of three tiers (`context/<repo>/requirements/`, `context/shared/requirements/`, `context/project/requirements/`). **BRAINSTORM** mode holds a product/business-altitude conversation, with an inline check for contradictions against what's already there; **DERIVE** mode reads an existing spec and drafts one candidate requirement per capability. An **AUDIT** branch (`/mreq audit`, `/mreq <tier> audit`) instead sweeps a whole tier pairwise for contradicting requirements and resolves each in-run with the user. Writes `REQUIREMENTS.md` and, on every amending run, a `REQ-CHANGE-<NNN>-<slug>.md` provenance record under the tier's `requirements/changes/`; never touches `context/<target>/spec/`. |
| `mspec` | no | "spec out", "write a spec", "update the spec", "change the shared interface", etc. | Resolve the target (a repo or `shared`), then create a new specification in `context/<target>/spec/` (CREATE) or update one (UPDATE). Runs in two separable stages — **diagnostic/clarify** (with an up-front risk & ambiguity scan) then **write** — so `/mquick` can reuse each. UPDATE mode also reads the tier's open `REQ-CHANGE` records as a Prerequisites input and, once its own change file is written, closes the ones this change covers via `mc.py req change-close` — `mspec` authors nothing under `requirements/` itself. Writes a repo-level change file and a project-level index; a `shared` update **cascades** as an agent team (lead + one teammate per consuming repo). |
| `mreverse` | no | "reverse the spec", "regenerate the spec from code", "the spec is stale", "find inconsistencies between repos", etc. | Take code as ground truth and reconcile the spec in `context/<repo>/spec/` (per-repo), using a team of readers. **Documents inconsistencies within a repo** and, across **two or more** repos, runs a **cross-repo inconsistency pass** recorded at workspace level. Writes **no** change files and **no** plans; never invokes `mplan`; never authors `context/shared/spec/`. |
| `mplan` | no | "create plan", "generate plan", "break into stories", etc. | Turn the latest `PROJECT-CHANGE-<NNN>-*.md` (or full specs for greenfield) into story files **plus** a machine-readable plan graph (`plan.yaml`), initial plan state (`state.yaml`), and a project-ledger entry, under `context/project/plans/<NNN>-<slug>/`. Fans out subagents to validate the generated stories. A plan may span repos; each story targets exactly one. |
| `mverify` | no | "verify conformance", "did we implement the change", "check cross-repo contracts", "audit coupling", etc. | From the **change + plan + spec** files, confirm the shipped code implements them: change↔code drift, cross-repo contract conformance, and code-level coupling detection — fanned out into three kinds of subagent shard (change-conformance, cross-repo, coupling). Produces a **change-shaped** report and records it in plan state. Read-only; reports, never rewrites. Runs as `mexecute`'s post-ship sweep and can be re-run against any prior change/plan. |
| `mfix` | **yes** | "fix the drift", "fix the mverify findings", "reconcile code and spec", "close the conformance report", etc. | Consume a `mverify` conformance report and resolve it. Per finding, decides **which artefact is authoritative** — spec-describes-intent + code-drifted → fix the code; code-is-deliberate + spec-over-promised → fix the spec; contract-does-not-exist-yet → defer to `mspec`. Fixes the root cause, not the symptom, inside the finding's fence. Writes a change file, keeps the repo's gates green, then re-runs `/mverify` to confirm closure. |
| `mexecute` | **yes** | "execute the plan", "run the plan", "ship it", etc. | Ships **one slice** of a plan as a **dynamic Workflow**: each wave's stories run concurrently, each in its **own git worktree**; validate + merge at a barrier (agent discretion), bounded retry (N=3), two-level state, the slice's **acceptance** as the demonstration that the behaviour actually runs, then a post-ship `/mverify` sweep. Given no `--slice` it runs every slice in order, which is exactly what a plan written before slices existed does. |
| `mship` | no | "deliver this slice by slice", "run the loop", "ship it incrementally", "deliver the plan a slice at a time", "mship", etc. | The **loop the other skills run inside** — it writes no spec, plan, code or conformance finding of its own; every step is an invocation of one of its siblings. Once per slice, in order: deepen that slice's modules from `contract` to `full` (`mspec`), refresh the stories of that slice alone (`mplan --slice`), ship them (`mexecute --slice`), sweep them (`mverify --slice`), then **decide** — continue, ask the developer about an acceptance no command can settle, re-plan the slices still outstanding (including an `mspec` cascade and the remediation one schedules), or stop. Owns the **gate policy** (`--gate unverifiable-only\|every-slice\|never`) and the **budget breaker**; never rewrites a slice already delivered. |
| `mquick` | via `mship` → `mexecute` | "just build it", "spec and ship this", "one-shot this", "mquick", etc. | Autonomous orchestrator: **one** clarification gate (Phase A, reusing `mspec`'s diagnostic stage + risk scan), then `mspec → mplan → mship` to shipped, conformed code. A thin sequence over the real skills — not a workflow of its own, and not a second copy of the loop: all it adds is `mship`'s gate policy, set to `unverifiable-only`, so the run pauses only where an acceptance cannot be obtained mechanically. |
| `mmigrate` | no | "migrate the artifacts", "check every catalog", "fix the schema", "audit the requirement ids", "the change numbers don't line up", "fix broken depends-on links", etc. | Sweeps every tracked artifact — `plan.yaml`/`state.yaml`, `CATALOG.yaml`, `CHANGE-<NNN>`/`PROJECT-CHANGE-<NNN>`/`REQ-CHANGE-<NNN>` front-matter, `REQUIREMENTS.md` — against its own JSON Schema and the `STANDARD-REQ.md`/`STANDARD-CHANGE.md` id/slug conventions, independent of any single change or plan. Migrates a tier still on bare `REQ-<NNN>` ids onto the mnemonic form (`REQ-<NNN>-<mnemonic>`, derived mechanically from each entry's own title) and keeps every reference's mnemonic in step with its heading afterwards. Repairs filename-vs-front-matter mismatches, malformed or duplicate ids, and dangling/missing cross-references at the root cause; defers anything that would require inventing content or judging between two equally plausible artifacts. Writes a change file only when a fix touches actual spec content. |

Standards load lazily, straight from `${CLAUDE_PLUGIN_ROOT}/shared/`, never copied into your project. `mspec` loads `STANDARD-SPEC.md` at its spec-write step and `STANDARD-CHANGE.md` at its change-doc step, keeping both out of context during brainstorm/diagnosis. `mreverse` loads `STANDARD-SPEC.md` only at its Phase 3 write step. `mplan` never reads the standards — it works from `mspec`'s already-conformant `CATALOG.yaml` and change docs, and renders stories via `python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py plan story-emit`, which injects the E2E hard rules from `STANDARD-SPEC.md` at render time so there's no second hand-maintained copy.

## Shared Standards

`shared/STANDARD-SPEC.md` and `shared/STANDARD-CHANGE.md` are the single-source-of-truth rules for spec-file layout, facets/layers, CATALOG.yaml, and change-document schema. They stay in the plugin and are referenced via `${CLAUDE_PLUGIN_ROOT}/shared/…`. `mspec` loads them at the start of its writing phase (not its brainstorm phase) so its output conforms; `mplan` then consumes that conformant output (the `CATALOG.yaml` and change docs) without reloading the rules. `schemas/` holds the machine-readable JSON Schemas those docs (and the plan graph + state files, plus requirements and requirements-change front-matter) validate against.

## Optional: Chat-Form Convention

`shared/CHATFORM.md` describes an **optional** interaction convention: rendering fixed-option
questions (e.g. during `mspec` brainstorming) as structured `<chat-form>` XML blocks for clients
that can display them. It is **not** referenced inline by any skill — `mspec` falls back to
plain-prose questions when it is absent, which is the default behavior.

It is **not loaded by default** — the skills never reference it inline, so a run without it just
uses plain-prose questions. To opt in, load it at Claude Code start by adding an import line to the
project's root `CLAUDE.md`:

```
@CHATFORM.md          # after copying shared/CHATFORM.md into your project root, or
@<path-to-plugin-install>/shared/CHATFORM.md   # to point straight at the installed plugin copy
```

Because it loads at start (not per-skill-run), once enabled it applies globally to any interactive
questioning, not just `mspec`. Leave the import out to keep the convention off.

## Installation

**Marketplace (plugin) install only — nothing is copied into your project's `.claude/`.** This repo's marketplace manifest lives at `.claude-plugin/marketplace.json`.

**Prerequisite, first.** The skills' shared tool (`tools/mc.py`) requires two Python packages:

```
pip install pyyaml jsonschema
```

This is the plugin's only setup step and it is **never** performed on your behalf. Neither manifest
format can express a runtime dependency, so the two packages are declared in the repository-root
`pyproject.toml` and stated here; the plugin never installs packages into your environment. Skipping
this line leaves every skill failing fast with a named `E_MISSING_PREREQ` diagnostic naming this
command, rather than misbehaving. The plugin also ships **no hooks** — nothing it installs runs
automatically at session start.

Then register the marketplace from GitHub and install the plugin from inside Claude Code:

```
/plugin marketplace add wcy/ai-plugins
/plugin install metacoder@ai-plugins
```

(To iterate on the plugin from a local checkout instead, `/plugin marketplace add .` from the repo root also works and picks up uncommitted local edits.)

That registers all ten skills (`mspec`, `mreverse`, `mplan`, `mverify`, `mfix`, `mexecute`, `mship`, `mquick`, `mreq`, `mmigrate`) and makes them invokable. The skills read their standards, template, schemas, and tool straight from the plugin's marketplace cache via `${CLAUDE_PLUGIN_ROOT}`, so **you copy nothing** into the target project — a fresh `.claude/` stays empty of plugin files. `${CLAUDE_PLUGIN_ROOT}` is substituted at runtime (not baked at install) and stores no absolute paths, so it rebases automatically across machines and plugin updates.

## Workflow

The core path — **ship → validate → conform → record** — is the `mspec → mplan → mship` row of the
diagram below. It does not run once over a whole job. `mship` drives it **once per slice** — a *slice* being the set of stories that
together make one behaviour run end to end — so a change arrives as a sequence of demonstrably working
increments rather than as something assembled and tested at the end. What each slice teaches is allowed to
change what the remaining slices are, which is what keeps a wrong approach from being discovered only once
it has been paid for in full.

`mreverse` and `mverify` hang off the path as read-only detectors; `mfix` closes the loop `mverify` opens.

`mreq` sits beneath `mspec`: `mspec` reads `REQUIREMENTS.md` read-only at its own Phase 1. `mquick` conditionally runs `mreq`'s BRAINSTORM Phase 1 first, folded into its single Phase A gate, only when a CREATE target's requirements tier is still empty.

`mmigrate` is a bookkeeping sweep, not a step in shipping a change: it checks that a tracked
artifact (`plan.yaml`, `CATALOG.yaml`, a `CHANGE-<NNN>` file, a `REQUIREMENTS.md` entry) is
well-formed and correctly cross-referenced on its own terms. Run it anytime — after a schema
version bump, when ids or links look wrong, or periodically.

```
   ┌───────────────────────────── mquick (autonomous) ──────────────────────────────┐
   │  one clarification gate, then everything below runs unattended: it hands the   │
   │  plan to mship with --gate unverifiable-only and adds no delivery logic at all │
   └────┬───────────────────┬──────────────────────┬────────────────────────────────┘
        ▼                   ▼                      ▼
     mspec      ───────►   mplan      ───────►   mship   ───────►  delivered, slice by slice
  (spec + change,       (stories +          (the loop the
   with a Slices         plan.yaml +         other skills
   table)                state.yaml)         run inside)
        ▲                                          │  once per slice, in order:
        │                                          ▼
        │   ┌──► deepen ────► plan ──────► execute ──────► verify ──────► decide ───┐
        │   │   (mspec:      (mplan        (mexecute       (mverify                 │
        │   │    contract     --slice)      --slice, then   --slice)                │
        │   │    → full)                    that slice's                            │
        │   │                               acceptance)                             │
        │   │                                                                       │
        │   └── next slice ◄── continue · ask the developer · re-plan what is ◄─────┘
        │                      still outstanding · stop (budget breaker,
        │                      spec defect, or an mexecute halt)
        │                                          │
        │                                          │ drift → change-shaped report
        │                                          ▼
        └───────────────  mfix (code-vs-spec) / mspec / mreverse

   mreverse  ─────►  reconciles spec from code + reports intra-/inter-repo inconsistencies
```

`mfix` closes most `mverify` findings — it decides per finding whether the code or the spec is
authoritative and fixes the one that's wrong. It defers to `mspec` when no contract exists yet,
and to `mreverse` when a repo has no spec tree at all.

**Gatekept** — invoke `mspec → mplan → mship` (→ `mverify`) yourself and review at each hand-off. Inside a run, the boundaries you are asked about are `mship`'s **gate policy**, a parameter rather than a fixed behaviour: `--gate every-slice` stops at every slice boundary, `unverifiable-only` (the default) stops only where an acceptance cannot be settled by running a command, `never` stops at none. There is still no gate *between waves* — that is inside `mexecute`, which halts only on a genuine blocking condition.

**Autonomous** — `mquick`: one clarification gate (Phase A, with the risk scan), then the same core path to shipped, conformed code, with `mship`'s gate policy set to `unverifiable-only`. This **is** the small-task fast path — a small task just has little to clarify.

A typical greenfield-to-change lifecycle:

1. *(Optional, once per shared contract)* `mspec` targeting `shared` to spec the cross-repo interface contracts under `context/shared/spec/`.
2. `mspec` targeting a repo → spec files under `context/<repo>/spec/` + a baseline record `CHANGE-000-initial-spec.md`. Declare consumed contracts in the repo's `shared_interfaces`.
3. A later change: an `mspec` update (on `shared`, it cascades as an agent team across consumers) → repo change files + one project-level index in `context/project/changes/`, including the `## Slices` table that cuts the change into behaviours that run end to end.
4. `mplan` → story files + `plan.yaml` + `state.yaml` + a project-ledger entry under `context/project/plans/<NNN>-<slug>/`. It consumes the change document's slices rather than inventing a cut of its own, and orders each slice's stories into waves by layer.
5. `mship` → delivers those slices **one at a time**. Per slice: deepen its modules to `full` (`mspec`), refresh its stories (`mplan --slice`), ship them (`mexecute --slice` — worktree-isolated waves, barrier merge, retry N=3, then the slice's acceptance), sweep them (`mverify --slice`), and decide whether to continue, ask you, re-plan what is still outstanding, or stop. The first slice is a **walking skeleton**: it touches every layer the change reaches, so the shape of the system is proven to run before any depth is built on it.
6. *(Any time)* `mverify` to re-check conformance against any change/plan, or `mreverse` to reconcile a stale spec back to the code and report intra-/inter-repo inconsistencies.

Or collapse steps 2–5 into a single `mquick` run.

Invoking `/mexecute` yourself, naming no slice, still puts a whole plan through in one go — that is what a plan written before slices existed does, wave for wave.

**Checking is continuous, not terminal.** Inside a story, the agent runs the plan graph's
`validation.increments` steps *as it implements* — each one straight after the increment it covers —
so a failure names the increment that introduced it rather than the story it surfaced in.
`validation.post_story` stays the story's closing gate rather than its only one. Then at each wave's
**barrier**, the wave's own `validation` steps run against the **merged** integration branch — the
branch every story of the run branches off and every green story merges back into — which is the
first and only point at which stories built in isolation are exercised together. Every one of these
steps is `kind: exit-code`, so running them often costs no model effort and no human attention.

**Work a run could not finish is written down, not remembered.** Some of what a run turns up outlives
the run — a conformance finding left open, a defect a story hit, a judgement no command could settle.
Rather than being reported into a session that then ends, it is recorded in `context/project/TODO.md`:
the workspace's one durable list of outstanding work, each entry routed to the skill that should take
it and carrying enough context to be picked up from a cleared session.

Two rules keep that list worth reading. Entries are written **only** through `mc.py todo add` — no
skill composes one in prose, so an entry has the same shape whichever skill deferred it.
And **resolution is removal**: a resolved entry is deleted.
There is no closed status and no closed section, so the list holds outstanding work and nothing else;
what was fixed is recorded in the change that fixed it. `shared/STANDARD-TODO.md` is the standard
those entries follow.

Correcting an entry is `mc.py todo edit`, which rewrites only the fields it is given and carries the
rest over verbatim. It re-validates the whole resulting entry through the same path `add` uses, so an
edit cannot produce an entry `add` would have refused — and the alternative, removing and re-adding,
means re-supplying every field, which is how a `Context` paragraph gets lost while fixing a one-word
rating.

Not every item is a skill's to close. An entry routed `human` — a plugin install, a credential
rotation, an approval only a person can give — names work no agent can perform, and it carries no
leading slash for exactly that reason: the other five values are commands, and this one is not.
`check handoff` reports these under their own heading rather than among the findings a run can clear,
because a queue holding one permanently unsatisfiable item is a queue nobody reads.

**A run that finishes says what it is leaving behind.** Filing at the moment of deferral only
captures what a run is *holding*; it never captures what a run *learned* and nobody wrote down. So
`mship` and `mquick` end by re-reading their own findings against the list and recording the result
with `mc.py state sweep` — including `--filed 0`, which is a claim that nothing was left rather than
a silence that looks identical to one. Nothing can check that a sweep was thorough, so what is
checked is that it happened: `check handoff` reports a finished plan carrying no declaration.

## Orchestration

The skills use Claude Code's orchestration primitives only where real parallelism or isolation earns them:

- **Subagents (fan-out).** `mreverse` deep-dive readers + cross-repo inconsistency readers; `mverify`'s three shard kinds (change-conformance, cross-repo, coupling); `mspec`'s up-front risk scan; `mplan`'s post-generation story validators.
- **Agent team.** The shared-interface **cascade** in `mspec`: a lead plus one teammate per consuming repo, the frozen shared contract as the sync point.
- **Dynamic Workflow.** `mexecute` **only** — parallel stories per wave *within the slice it was given*, each worktree-isolated, with a barrier, bounded retry, and merge. `mship` and `mquick` are plain sequential pipelines over the skills, not workflows. Note: `mexecute` shells out to real `git worktree` commands, so it needs a git repo under `repos/<repo>/`.
- **Slice iteration (the loop).** `mship` **only** — the unit of delivery is a **slice**, not a plan and not a wave. Waves order stories *inside* a slice by layer; slices are the axis delivery advances along, and a slice cuts vertically through the layers so finishing one yields something that runs. `mship` iterates them in order, running deepen → plan → execute → verify → decide for each, and it is where the three between-slice judgements live: the **gate policy** (which boundaries a human is asked about), the **budget breaker** (stop when a slice's cost departs sharply from what earlier slices of the same job cost), and **re-planning** the slices still outstanding — recut, reorder, split, drop, or cascade a shared contract and schedule remediation for work built against the older revision. A slice already delivered is never rewritten. `mship` itself writes no spec, plan, code or conformance finding; each of its five steps is an invocation of another skill at that skill's normal entry point, which is why the loop adds no second implementation of anything it drives.
- **Two-level state (the ledger).** A project-level ledger (`context/project/state.yaml`) tracks each plan's status; a plan-level `state.yaml` tracks slices (status, acceptance, outcome), waves, per-story status/retries/worktree refs, and conformance. Orchestration (resume, retry, which slice is next) reads this state, not prose — and `mship` records a slice's result *before* it decides what to do about it, so a run that stops mid-decision resumes from a recorded fact. Both files, plus the plan graph, change front-matter, and subagent reports, validate against JSON Schemas in `schemas/` (checked with `python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py validate <kind> <file>`).

## License

MIT — see [LICENSE](../../LICENSE) at the repo root.
