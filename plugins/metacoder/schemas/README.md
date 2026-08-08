# Schemas

Machine-readable JSON Schemas (Draft 2020-12) for the metacoder artifacts — source of truth for
the *shape* of every automated file. `../shared/` holds the prose standards for *content and
conventions*.

This module is **purely declarative**: twelve JSON documents plus this `README.md`, no executable
code.

## Kinds and aliases

Twelve canonical kinds, each resolving to `<kind>.schema.json` in this directory:

`catalog`, `change-frontmatter`, `conformance-report`, `inconsistency-report`, `plan-graph`,
`plan-state`, `project-state`, `story-report`, `slice-report`, `requirements-frontmatter`,
`req-change-frontmatter`, `todo-frontmatter`.

Ten friendly aliases name the same documents:

| Alias | Kind |
|-------|------|
| `change` | `change-frontmatter` |
| `plan` | `plan-graph` |
| `ledger` | `project-state` |
| `conformance` | `conformance-report` |
| `story` | `story-report` |
| `slice` | `slice-report` |
| `inconsistency` | `inconsistency-report` |
| `requirements` | `requirements-frontmatter` |
| `req-change` | `req-change-frontmatter` |
| `todo` | `todo-frontmatter` |

The kinds and the aliases are this module's contract. The CLI that accepts them lives in `../tools/`:

```
python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py validate <kind> <file> [<file> ...]
```

## Producer/consumer contract

| Schema | Validates | Written by | Read by |
|--------|-----------|------------|---------|
| `catalog.schema.json` | `context/<repo>/spec/CATALOG.yaml`, `context/shared/spec/CATALOG.yaml` | mspec, mreverse | mplan, mverify, mexecute |
| `change-frontmatter.schema.json` | `CHANGE-*.md` / `PROJECT-CHANGE-*.md` front-matter | mspec | mplan, mverify |
| `plan-graph.schema.json` | `context/project/plans/<plan-id>/plan.yaml` | mplan | mexecute, mverify |
| `project-state.schema.json` | `context/project/state.yaml` | mplan, mexecute | all entry points (resume) |
| `plan-state.schema.json` | `context/project/plans/<plan-id>/state.yaml` | mplan (initial), mexecute, mverify (the `conformance` block, standalone runs only) | mexecute, mverify |
| `conformance-report.schema.json` | each shard's returned JSON, persisted as `context/project/out/<plan-id>/shards/<shard-id>.json`, and the aggregate `context/project/out/<plan-id>/mverify-report.json` — four validations per sweep | mverify's orchestrator (shards return JSON and write nothing) | mverify, mexecute |
| `story-report.schema.json` | mexecute wave-agent return | mexecute subagents | mexecute, mship (the `spec_defect` field only) |
| `slice-report.schema.json` | mship per-slice return | mship | mship, mquick (relayed into its Phase E report) |
| `inconsistency-report.schema.json` | mreverse reader return + aggregate | mreverse subagents | mreverse |
| `requirements-frontmatter.schema.json` | `context/<repo\|shared\|project>/requirements/REQUIREMENTS.md` front-matter | mreq | mspec (read-only — Prerequisites gate + Risk Scan's requirements-drift category) |
| `req-change-frontmatter.schema.json` | `context/<tier>/requirements/changes/REQ-CHANGE-*.md` front-matter | mreq (authors the record), tools (`req change-close` writes the closing transition) | mspec, tools (`check handoff`), mmigrate |

The "Written by" and "Read by" columns name the skill whose step produces or consumes the artifact.
Every one of those reads and writes now goes through `../tools/`, which validates each artifact
before persisting it — so validation is no longer a step a skill can omit.

Three rows validate a *return value* rather than a path — `story-report`, `slice-report`,
`inconsistency-report` — because nothing writes those into the workspace. What mship does persist,
the aggregate `mship-run.md` in the plan's `context/project/out/` directory, is rendered Markdown
assembled from validated slice returns, not itself a schema-checked artifact.

## Two levels of state

- **Project ledger** (`project-state.schema.json`) — one entry per plan; the thing resume reads
  first to find the unfinished plan.
- **Plan state** (`plan-state.schema.json`) — per-slice, per-wave and per-story status, retry counts,
  worktree refs (per story *and* per retry attempt), validation + conformance results, telemetry.

The plan **graph** (`plan-graph.schema.json`, immutable structure emitted by mplan) is deliberately
separate from plan **state** (mutable execution status); both live in the plan directory.

## Slices, spec depth, and agreement revisions

A **slice** is the unit of delivery: the set of stories that together make one behaviour run end to
end. `version: 3` is what declares a plan graph slice-bearing — the version, not the presence of the
`slices` key, is the discriminator, so a malformed version-3 graph is rejected rather than silently
degrading to whole-job delivery. A version `1` or `2` graph carries no `slices` and is read as one
implicit slice containing every wave, which is exactly the delivery model it was written for.

Every other field added alongside them is optional, and absent means what the artifact already
meant:

| Field | Where | Absent means |
|-------|-------|--------------|
| `depth` | `catalog`, per module | `full` — the module is described in every facet |
| `revision` | `catalog`, per shared interface | `1` — the agreement has never been cascaded |
| `slices` | `plan-graph`, `plan-state` | one implicit slice covering every wave |
| `conformance.deferred` | `plan-state` | `0` — no finding has been accepted as debt |
| `slices_total` / `slices_applied` | `project-state`, per plan | a pre-slice plan, not zero progress |
| `contract_revisions` | `plan-state`, `story-report` | the story consumed no shared interface |

That is why no existing catalog, plan, or state file needs migrating: nothing that validated before
these fields existed stops validating now.
