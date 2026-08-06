# Schemas

Machine-readable JSON Schemas (Draft 2020-12) for the metacoder artifacts — source of truth for
the *shape* of every automated file. `../shared/` holds the prose standards for *content and
conventions*.

This module is **purely declarative**: ten JSON documents plus this `README.md`, no executable
code.

## Kinds and aliases

Ten canonical kinds, each resolving to `<kind>.schema.json` in this directory:

`catalog`, `change-frontmatter`, `conformance-report`, `inconsistency-report`, `plan-graph`,
`plan-state`, `project-state`, `story-report`, `requirements-frontmatter`,
`req-change-frontmatter`.

Eight friendly aliases name the same documents:

| Alias | Kind |
|-------|------|
| `change` | `change-frontmatter` |
| `plan` | `plan-graph` |
| `ledger` | `project-state` |
| `conformance` | `conformance-report` |
| `story` | `story-report` |
| `inconsistency` | `inconsistency-report` |
| `requirements` | `requirements-frontmatter` |
| `req-change` | `req-change-frontmatter` |

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
| `story-report.schema.json` | mexecute wave-agent return | mexecute subagents | mexecute |
| `inconsistency-report.schema.json` | mreverse reader return + aggregate | mreverse subagents | mreverse |
| `requirements-frontmatter.schema.json` | `context/<repo\|shared\|project>/requirements/REQUIREMENTS.md` front-matter | mreq | mspec (read-only — Prerequisites gate + Risk Scan's requirements-drift category) |
| `req-change-frontmatter.schema.json` | `context/<tier>/requirements/changes/REQ-CHANGE-*.md` front-matter | mreq (authors the record), tools (`req change-close` writes the closing transition) | mspec, tools (`check handoff`), mmigrate |

The "Written by" and "Read by" columns name the skill whose step produces or consumes the artifact.
Every one of those reads and writes now goes through `../tools/`, which validates each artifact
before persisting it — so validation is no longer a step a skill can omit.

## Two levels of state

- **Project ledger** (`project-state.schema.json`) — one entry per plan; the thing resume reads
  first to find the unfinished plan.
- **Plan state** (`plan-state.schema.json`) — per-wave and per-story status, retry counts,
  worktree refs (per story *and* per retry attempt), validation + conformance results, telemetry.

The plan **graph** (`plan-graph.schema.json`, immutable structure emitted by mplan) is deliberately
separate from plan **state** (mutable execution status); both live in the plan directory.
