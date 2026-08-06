<!--
  Story-file template. Owned by `shared/`, not by any skill.

  DATA, not prose to re-enact. Rendered by `mc.py plan story-emit <plan-id> <story-id>`,
  which emits the block between the BEGIN/END markers as PLAN-{WW}-{SS}-{REPO}-{MODULE}.md
  with {placeholders} substituted. No skill loads this file; no agent copies from it by hand.

  Comment-gated sections (INCREMENTAL PLANS ONLY, last wave only, update sub-mode only) are
  conditional — the renderer includes them only when the condition holds, dropping them
  otherwise. Lines with an INJECT: marker are replaced whole with content from the named
  section. Each story file must stay self-contained and focused — as small as the work
  allows, with no hard line limit.
-->

<!-- ===== BEGIN STORY TEMPLATE ===== -->
<!-- depends-on: context/{repo}/spec/CATALOG.yaml -->

# Story: {Module Name} ({Layer}) — {repo}

**Repo:** {repo} <!-- repo dir under repos/ this story modifies -->
**Wave:** {wave number} of {total waves}
**Prerequisites:** {list all story files from earlier waves that must complete first, or "None"}
**Parallel group:** {list other PLAN-{WW}-*-*.md files that run concurrently with this one, or "solo"}
**Compliance Status:** {Implement — no existing code | Update — gaps found | Verify — run tests only} <!-- update sub-mode only; omit for greenfield and incremental plans -->

## Execution Model

All code changes happen inside `repos/{repo}/` (the dir named in **Repo:** above). Do not modify any other repo.

Run after all prerequisite stories complete. If parallel group is not "solo", spawn one
agent per story in the group concurrently and wait for all to finish before advancing to
wave {WW+1}.

Each agent loads **only**:
1. This story file
2. The spec files listed in **Context Files** below
3. `context/{repo}/spec/CATALOG.yaml` (for reference)

Do NOT load other story files, other modules' IMPLEMENTATION files, or TESTING files from other modules.

---

<!-- INCREMENTAL PLANS ONLY: include this section. Omit for full (000-initial) plans. -->
## Change Scope

**Repo change file:** `context/{repo}/changes/CHANGE-<NNN>-<slug>.md`

Only update the following — do not touch anything else in this module:

| Source File | What to Change |
|-------------|---------------|
| {file, relative to repos/{repo}/} | {what to update, from "Affected Code Paths" in the change file} |

**Breaking changes:** {yes/no — list them if yes, from the change file}

### What Changed

<!-- One subsection per logical change affecting this module, drawn from the change doc's
     "## Detailed Changes" section. Include only subsections relevant to this module. -->

#### {Change Title}

**Spec reference:** {spec file and section}

**Before:** {old behavior, signature, or type — from the change doc. Use "N/A" for additions.}

**After:** {new behavior, signature, or type — from the change doc. Use "N/A" for removals.}

**Rationale:** {why this changed — from the change doc}
<!-- END INCREMENTAL SECTION -->

---

## Context Files

Load these files before implementing. This is the **complete** context — do not load anything else.

<!-- Derive these lists from the depends_on fields in CATALOG.yaml for each file in this module. -->

**From foundation/shared modules:**
- {list only the foundation-layer files this module actually depends on, per depends_on in CATALOG.yaml}

**From this module:**
- {list all files belonging to this module from CATALOG.yaml}

**Cross-module dependencies (INTERFACE only):**
- {list INTERFACE files from other non-foundation modules that appear in depends_on}

**Shared interface contracts (INTERFACE/DATAMODEL only):**
- {list context/shared/spec/<IFACE>/<IFACE>-INTERFACE.md (and DATAMODEL) files this module's depends_on names — the cross-repo contracts it codes against}

---

## Implementation Tasks

<!-- Full plan: include all applicable tasks.
     Incremental plan: include only tasks for the facets listed in the change document. -->

Implement in this order:
1. **Types & Data Model** — Export all types from the module's DATAMODEL spec (if it exists)
2. **Interface Exports** — Implement all functions/classes from the module's INTERFACE spec (if it exists)
3. **Internal Logic** — Wire up internals per the module's IMPLEMENTATION spec (if it exists)
4. **Unit Tests** — Write tests per the module's TESTING spec (if it exists)

<!-- Adapt the task list to this module's actual CATALOG.yaml facets — include tasks
     only for facets that exist. -->

---

## Acceptance Criteria

- [ ] All functions in the INTERFACE spec are implemented and exported (if interface facet exists)
- [ ] Types match the DATAMODEL spec exactly (if datamodel facet exists)
- [ ] {criteria derived from the module's TESTING spec, if it exists}
- [ ] No imports from other modules' IMPLEMENTATION files
- [ ] Unit tests pass

---

## Post-Story Validation

Run before marking this story complete:

- [ ] All exported types match this module's `*-INTERFACE.md` signatures
- [ ] No imports from other modules' IMPLEMENTATION files (only INTERFACE)
- [ ] Unit tests pass for this module
- [ ] Integration points with lower-layer modules work (not stubbed)
- [ ] Full existing test suite passes with no regressions (update sub-mode only)
- [ ] E2E scenarios for this module pass (if E2E module exists in catalog)

<!-- INJECT:E2E-HARD-RULES — replace this line with the four rules from STANDARD-SPEC.md § "E2E Testing Hard Rules", verbatim, when the checkbox above applies (this repo's CATALOG.yaml has a module whose TESTING facet covers E2E); otherwise drop the line. Injected by `mc.py plan story-emit` — never hand-copied. -->

<!-- Include the section below only in stories belonging to the final wave ({WW} = last wave). -->
## Final Validation (last wave only)

Run after all waves are complete:

- [ ] E2E test scenarios pass (if E2E module exists in catalog)

<!-- INJECT:E2E-HARD-RULES — replace this line with the four rules from STANDARD-SPEC.md § "E2E Testing Hard Rules", verbatim, when the checkbox above applies (this repo's CATALOG.yaml has a module whose TESTING facet covers E2E); otherwise drop the line. Injected by `mc.py plan story-emit` — never hand-copied. -->

- [ ] No coupling violations across the entire codebase (including no cross-repo coupling outside `context/shared/spec/`)
- [ ] All acceptance criteria in every story file are checked off
- [ ] Every spec file in every in-scope repo's `CATALOG.yaml` is covered by exactly one story
- [ ] Full test suite passes with no regressions (update sub-mode only)
<!-- ===== END STORY TEMPLATE ===== -->
