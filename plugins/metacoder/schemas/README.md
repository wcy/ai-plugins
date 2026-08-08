# Schemas

Machine-readable JSON Schemas (Draft 2020-12) for the metacoder artifacts — source of truth for
the *shape* of every automated file. `../shared/` holds the prose standards for *content and
conventions*.

This module is **purely declarative**: seven JSON documents plus this `README.md`, no executable
code.

## Kinds and aliases

Seven canonical kinds, each resolving to `<kind>.schema.json` in this directory:

`catalog`, `change-frontmatter`, `finding-report`, `plan-graph`, `plan-state`, `project-state`,
`story-report`.

`finding-report` is one shape for both detection passes. `conformance-report` and
`inconsistency-report` differed only in their finding vocabulary while sharing a `scope` object, a
`findings` array and a `clean` flag; the producing pass is now a `pass` field and the vocabulary is
the union. The old names survive as aliases.

Friendly aliases name the same documents:

| Alias | Kind |
|-------|------|
| `change` | `change-frontmatter` |
| `plan` | `plan-graph` |
| `ledger` | `project-state` |
| `finding` | `finding-report` |
| `conformance` | `finding-report` |
| `inconsistency` | `finding-report` |
| `story` | `story-report` |

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
| `finding-report.schema.json` | each shard's returned JSON, persisted as `context/project/out/<plan-id>/shards/<shard-id>.json`, and the aggregate `context/project/out/<plan-id>/mverify-report.json`; also mreverse's reader returns and its aggregate | the orchestrator (shards return JSON and write nothing) | mverify, mship, mreverse |
| `story-report.schema.json` | story-agent return | mship's story subagents | mship |

The "Written by" and "Read by" columns name the skill whose step produces or consumes the artifact.
Every one of those reads and writes now goes through `../tools/`, which validates each artifact
before persisting it — so validation is no longer a step a skill can omit.

`story-report` validates a *return value* rather than a path, because nothing writes it into the
workspace. What mship does persist, the aggregate `mship-run.md` in the plan's
`context/project/out/` directory, is rendered Markdown assembled from validated returns, not itself
a schema-checked artifact.

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
