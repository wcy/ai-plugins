# Schemas

Machine-readable JSON Schemas (Draft 2020-12) for the metacoder artifacts. They are the
source of truth for the *shape* of every automated file, complementing the prose standards in
`../shared/` (which remain the source of truth for *content and conventions*). Skills reference
them at `${CLAUDE_PLUGIN_ROOT}/schemas/…` and validate on write (mspec, mplan, mverify) and on
read (mexecute, mverify).

| Schema | Validates | Written by | Read by |
|--------|-----------|------------|---------|
| `catalog.schema.json` | `context/<repo>/spec/CATALOG.yaml` (repo flavor) and `context/shared/spec/CATALOG.yaml` (shared flavor) | mspec, mreverse | mplan, mverify, mexecute |
| `change-frontmatter.schema.json` | the `<!-- key: value -->` front-matter of `CHANGE-*.md` (repo) and `PROJECT-CHANGE-*.md` (index) | mspec | mplan, mverify |
| `plan-graph.schema.json` | `context/project/plans/<plan-id>/plan.yaml` — the immutable wave/story/dependency graph | mplan | mexecute, mverify |
| `project-state.schema.json` | `context/project/state.yaml` — the workspace ledger (per-plan status) | mplan, mexecute | all entry points (resume) |
| `plan-state.schema.json` | `context/project/plans/<plan-id>/state.yaml` — per-wave/per-story mutable execution state | mplan (initial), mexecute | mexecute, mverify |
| `conformance-report.schema.json` | a `/mverify` shard's return + the aggregate | mverify subagents | mverify, mexecute |
| `story-report.schema.json` | a `/mexecute` wave agent's return | mexecute subagents | mexecute |
| `inconsistency-report.schema.json` | a `/mreverse` reader's return + the aggregate | mreverse subagents | mreverse |

## Two levels of state

- **Project ledger** (`project-state.schema.json`) — one entry per plan; the thing resume reads
  first to find the unfinished plan.
- **Plan state** (`plan-state.schema.json`) — per-wave and per-story status, retry counts,
  worktree refs (per story *and* per retry attempt), validation + conformance results, telemetry.

The plan **graph** (`plan-graph.schema.json`, immutable structure emitted by mplan) is deliberately
separate from plan **state** (mutable execution status); both live in the plan directory.

## Validating

```
python3 ${CLAUDE_PLUGIN_ROOT}/schemas/validate.py <kind> <file> [<file> ...]
```

`<kind>` is the schema basename (e.g. `catalog`, `plan-graph`, `plan-state`) or a friendly alias
(`change`, `plan`, `ledger`, `story`, `conformance`, `inconsistency`). Input type is auto-detected
from the extension: `.yaml`/`.yml` (needs PyYAML), `.json`, or `.md` (front-matter comments are
extracted). Exit code `0` = valid, `1` = a validation error (printed with a path), `2` = a
usage/load error. Uses the `jsonschema` package when available; otherwise a self-contained
stdlib-only validator covering the subset these schemas use.

This runnable check is the mechanical enforcement point: a skill validates before it writes (reject
a malformed doc with a clear error) and after it reads (fail fast on corrupt state).
