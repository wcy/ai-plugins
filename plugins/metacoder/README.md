# metacoder

A Claude Code plugin providing a structured spec-to-ship workflow for a **multi-repo workspace** that keeps codebases **coherent and consistent** — internally (code implements its spec; specs obey one standard) and across repos (a shared contract layer + change cascade + cross-repo conformance). Brainstorm and write specs per repository (and a shared cross-repo interface layer), generate modular implementation plans, execute them as worktree-isolated waves, and verify conformance — manually gatekept or fully autonomous.

## Getting Started

**Prerequisites:** Claude Code, and git (for `mexecute`'s worktree-isolated waves).

1. Install the plugin (see [Installation](#installation) below).
2. In your workspace root, create the scaffold the skills expect:

   ```
   mkdir -p repos context/project/{plans,out,changes} context/shared/spec
   ```

   Put (or clone) your repositories under `repos/<repo>/`.
3. Spec a repo: `/mspec` (e.g. "spec out the auth service") — writes `context/<repo>/spec/` and a baseline `CHANGE-000-initial-spec.md`.
4. Turn a change into stories: `/mplan` — writes `context/project/plans/<NNN>-<slug>/` (story files, `plan.yaml`, `state.yaml`).
5. Ship it: `/mexecute` — runs the plan as worktree-isolated waves, then a post-ship conformance sweep.
6. Re-check anytime: `/mverify` (conformance against any change/plan) or `/mreverse` (reconcile a stale spec back to the actual code, or audit consistency across repos).

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
    │   └── changes/        # repo-level CHANGE-<NNN>-*.md (one per spec change, sequenced per repo)
    ├── shared/
    │   └── spec/           # cross-repo interface contracts — OVERVIEW/DATAMODEL/INTERFACE only + CATALOG.yaml
    └── project/
        ├── plans/          # PLAN-{WW}-{SS}-{REPO}-{MODULE}.md (each story tagged with its repo)
        ├── out/            # execution output
        └── changes/        # PROJECT-CHANGE-<NNN>-*.md — index referencing repo changes; drives mplan
```

A repo consumes a shared interface by listing its TAG in the repo `CATALOG.yaml` `shared_interfaces` field and adding the shared `*-INTERFACE.md` to its `depends-on`. Changing a shared interface **cascades**: `mspec` updates each consuming repo's spec, writes a repo-level change file per repo, and produces one project-level index in `context/project/changes/` that references them all.

## Contents

```
plugins/metacoder/
├── .claude-plugin/plugin.json   # plugin manifest
├── README.md                    # this file
├── skills/
│   ├── mreq/SKILL.md            # skills/mreq/SKILL.md — requirements-authoring skill
│   ├── mspec/SKILL.md
│   ├── mreverse/SKILL.md
│   ├── mplan/
│   │   ├── SKILL.md
│   │   └── PLAN-STORY-TEMPLATE.md  # story template, read on demand via ${CLAUDE_SKILL_DIR}
│   ├── mverify/SKILL.md
│   ├── mexecute/SKILL.md
│   └── mquick/SKILL.md
├── shared/
│   ├── STANDARD-SPEC.md          # referenced via ${CLAUDE_PLUGIN_ROOT}/shared/…
│   ├── STANDARD-CHANGE.md
│   ├── STANDARD-REQ.md          # referenced via ${CLAUDE_PLUGIN_ROOT}/shared/…, mreq only
│   └── CHATFORM.md              # optional, opt-in interaction convention
└── schemas/                      # JSON Schemas for CATALOG/plan-graph/state/reports
```

Install is **marketplace-only** — the whole tree stays in the plugin's marketplace cache and
**nothing is copied into your project's `.claude/`**. Skills reach the standards and template at
runtime through `${CLAUDE_PLUGIN_ROOT}` / `${CLAUDE_SKILL_DIR}`, which rebase automatically to
wherever the plugin was installed, with no absolute paths baked in.

## Skills

These are Claude Code skills — Claude invokes them either automatically when your message matches their trigger description, or when you explicitly reference the skill by name (e.g. `/mspec`, `/mplan`).

| Skill | Writes code? | Trigger | Purpose |
|-------|:---:|---------|---------|
| `mreq` | no | "flesh out requirements", "write requirements", "what are we building this for", "derive requirements from the spec", "update requirements from the spec", etc. | Author or derive the requirements layer — user needs and business goals, the *what*, as distinct from the spec's *how* — at one of three tiers (`context/<repo>/requirements/`, `context/shared/requirements/`, `context/project/requirements/`). **BRAINSTORM** mode holds a product/business-altitude conversation; **DERIVE** mode reads an existing spec and drafts one candidate requirement per capability. Writes only `REQUIREMENTS.md`; never touches `context/<target>/spec/`. |
| `mspec` | no | "spec out", "write a spec", "update the spec", "change the shared interface", etc. | Resolve the target (a repo or `shared`), then create a new specification in `context/<target>/spec/` (CREATE) or update one (UPDATE). Runs in two separable stages — **diagnostic/clarify** (with an up-front risk & ambiguity scan) then **write** — so `/mquick` can reuse each. Writes a repo-level change file and a project-level index; a `shared` update **cascades** as an agent team (lead + one teammate per consuming repo). |
| `mreverse` | no | "reverse the spec", "regenerate the spec from code", "the spec is stale", "find inconsistencies between repos", etc. | Take code as ground truth and reconcile the spec in `context/<repo>/spec/` (per-repo), using a team of readers. **Documents inconsistencies within a repo** and, across **two or more** repos, runs a **cross-repo inconsistency pass** recorded at workspace level. Writes **no** change files and **no** plans; never invokes `mplan`; never authors `context/shared/spec/`. |
| `mplan` | no | "create plan", "generate plan", "break into stories", etc. | Turn the latest `PROJECT-CHANGE-<NNN>-*.md` (or full specs for greenfield) into story files **plus** a machine-readable plan graph (`plan.yaml`), initial plan state (`state.yaml`), and a project-ledger entry, under `context/project/plans/<NNN>-<slug>/`. Fans out subagents to validate the generated stories. A plan may span repos; each story targets exactly one. |
| `mverify` | no | "verify conformance", "did we implement the change", "check cross-repo contracts", "audit coupling", etc. | From the **change + plan + spec** files, confirm the shipped code implements them: change↔code drift, cross-repo contract conformance, and code-level coupling detection — fanned out into three kinds of subagent shard (change-conformance, cross-repo, coupling). Produces a **change-shaped** report and records it in plan state. Read-only; reports, never rewrites. Runs as `mexecute`'s post-ship sweep and can be re-run against any prior change/plan. |
| `mexecute` | **yes** | "execute the plan", "run the plan", "ship it", etc. | **The one code-writing skill.** Ships a plan as a **dynamic Workflow**: each wave's stories run concurrently, each in its **own git worktree**; validate + merge at a barrier (agent discretion), bounded retry (N=3), two-level state, then a post-ship `/mverify` sweep. |
| `mquick` | via `mexecute` | "just build it", "spec and ship this", "one-shot this", "mquick", etc. | Autonomous orchestrator: **one** clarification gate (Phase A, reusing `mspec`'s diagnostic stage + risk scan), then `mspec → mplan → mexecute` to shipped, conformed code with no further gates. A thin sequence over the real skills — not a workflow of its own. |

`mspec` reads the standards from the plugin at `${CLAUDE_PLUGIN_ROOT}/shared/STANDARD-SPEC.md` and (for UPDATE mode) `${CLAUDE_PLUGIN_ROOT}/shared/STANDARD-CHANGE.md` — resolved at runtime to the marketplace cache, never copied into your project. It loads them **lazily** — only when it reaches the writing phase (STANDARD-SPEC at the spec-write step, STANDARD-CHANGE at the change-doc step), not during the brainstorm/diagnosis phase — to keep the standards out of context while they aren't needed. `mreverse` likewise loads `STANDARD-SPEC.md` lazily, only at its Phase 3 write step, and never touches `STANDARD-CHANGE.md` since it produces no change documents. `mplan` does not read the standards directly; it works from the already-standard-compliant `CATALOG.yaml` and change documents that `mspec` produced, and reads its story template from `${CLAUDE_SKILL_DIR}/PLAN-STORY-TEMPLATE.md`.

## Shared Standards

`shared/STANDARD-SPEC.md` and `shared/STANDARD-CHANGE.md` are the single-source-of-truth rules for spec-file layout, facets/layers, CATALOG.yaml, and change-document schema. They stay in the plugin and are referenced via `${CLAUDE_PLUGIN_ROOT}/shared/…`. `mspec` loads them at the start of its writing phase (not its brainstorm phase) so its output conforms; `mplan` then consumes that conformant output (the `CATALOG.yaml` and change docs) without reloading the rules. `schemas/` holds the machine-readable JSON Schemas those docs (and the plan graph + state files) validate against.

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

**Marketplace (plugin) install only — nothing is copied into your project's `.claude/`.** This repo's marketplace manifest lives at `.claude-plugin/marketplace.json`. Register it from GitHub and install the plugin from inside Claude Code:

```
/plugin marketplace add wcy/ai-plugins
/plugin install metacoder@ai-plugins
```

(To iterate on the plugin from a local checkout instead, `/plugin marketplace add .` from the repo root also works and picks up uncommitted local edits.)

That registers all seven skills (`mspec`, `mreverse`, `mplan`, `mverify`, `mexecute`, `mquick`, `mreq`) and makes them invokable. The skills read their standards, template, and schemas straight from the plugin's marketplace cache via `${CLAUDE_PLUGIN_ROOT}` / `${CLAUDE_SKILL_DIR}`, so **you copy nothing** into the target project — a fresh `.claude/` stays empty of plugin files. `${CLAUDE_PLUGIN_ROOT}` is substituted at runtime (not baked at install) and stores no absolute paths, so it rebases automatically across machines and plugin updates.

Neither manifest declares a runtime dependency, and none is needed: `schemas/validate.py` runs on a stock Python 3, using `PyYAML`/`ruamel.yaml` and `jsonschema` only when already importable and falling back to bundled code otherwise. The plugin ships **no hooks** — nothing it installs runs automatically at session start, and installing it never installs packages into your environment.

## Workflow

Seven skills close the coherence loop **ship → validate → conform → record**. The core path is the top row; `mreverse` (reconcile from code) and `mverify` (conformance) hang off it as read-only detectors.

`mreq` sits beneath `mspec` in the diagram below: `mspec` reads `mreq`'s output (`REQUIREMENTS.md`) read-only at its own Phase 1 and never invokes `mreq` directly, and `mquick` conditionally invokes `mreq`'s BRAINSTORM Phase 1 ahead of `mspec` — folded into its single Phase A gate — only when a CREATE target's own requirements tier is still empty.

```
                          ┌──────────────────────────── mquick (autonomous) ───────────────────────────┐
                          │  one clarification gate, then runs the core path unattended                 │
                          ▼                                                                              │
   mspec  ───────►  mplan  ───────►  mexecute  ───────►  mverify (post-ship sweep)                      │
  (spec + change)   (stories +      (worktree waves,     (change↔code + cross-repo                      │
        ▲            plan.yaml +      barrier merge,       conformance + coupling)                       │
        │            state.yaml)      retry 3, state)            │                                       │
        │                                                        │ drift → change-shaped report          │
        │                                                        ▼                                       │
        └──────────────  mspec / mreverse follow-up  ◄───────────┘                                       │
                                                                                                         │
   mreverse  ─────►  reconciles spec from code + reports intra-/inter-repo inconsistencies  ─────────────┘
```

**Gatekept** — invoke `mspec → mplan → mexecute` (→ `mverify`) yourself and review at each hand-off. `mexecute` still runs every wave through internally (no between-wave gate); it halts only on a genuine blocking condition.

**Autonomous** — `mquick`: one clarification gate (Phase A, with the risk scan), then the same core path to shipped, conformed code. This **is** the small-task fast path — a small task just has little to clarify.

A typical greenfield-to-change lifecycle:

1. *(Optional, once per shared contract)* `mspec` targeting `shared` to spec the cross-repo interface contracts under `context/shared/spec/`.
2. `mspec` targeting a repo → spec files under `context/<repo>/spec/` + a baseline record `CHANGE-000-initial-spec.md`. Declare consumed contracts in the repo's `shared_interfaces`.
3. A later change: an `mspec` update (on `shared`, it cascades as an agent team across consumers) → repo change files + one project-level index in `context/project/changes/`.
4. `mplan` → story files + `plan.yaml` + `state.yaml` + a project-ledger entry under `context/project/plans/<NNN>-<slug>/`.
5. `mexecute` → ships the plan as worktree-isolated waves (barrier merge, retry N=3), then a post-ship `mverify` sweep.
6. *(Any time)* `mverify` to re-check conformance against any change/plan, or `mreverse` to reconcile a stale spec back to the code and report intra-/inter-repo inconsistencies.

Or collapse steps 2–5 into a single `mquick` run.

## Orchestration

The skills use Claude Code's orchestration primitives only where real parallelism or isolation earns them:

- **Subagents (fan-out).** `mreverse` deep-dive readers + cross-repo inconsistency readers; `mverify`'s three shard kinds (change-conformance, cross-repo, coupling); `mspec`'s up-front risk scan; `mplan`'s post-generation story validators.
- **Agent team.** The shared-interface **cascade** in `mspec`: a lead plus one teammate per consuming repo, the frozen shared contract as the sync point.
- **Dynamic Workflow.** `mexecute` **only** — parallel stories per wave, each worktree-isolated, with a barrier, bounded retry, and merge. `mquick` is a plain sequential pipeline over the skills, not a workflow. Note: `mexecute` shells out to real `git worktree` commands, so it needs a git repo under `repos/<repo>/`.
- **Two-level state (the ledger).** A project-level ledger (`context/project/state.yaml`) tracks each plan's status; a plan-level `state.yaml` tracks waves, per-story status/retries/worktree refs, and conformance. Orchestration (resume, retry, progress) reads this state, not prose. Both, plus the plan graph, change front-matter, and subagent reports, validate against JSON Schemas in `schemas/` (run `schemas/validate.py`).

## License

MIT — see [LICENSE](../../LICENSE) at the repo root.
