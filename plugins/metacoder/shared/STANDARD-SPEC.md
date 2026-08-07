# Spec Standards

Standards for spec files in a multi-repo workspace. Referenced by prompt files as the single source of truth.

---

## Multi-Repo Workspace Layout

A workspace holds **several related repositories** plus a **shared interface contract layer**. The workspace root is where Claude Code runs. The **spec is split per repository** under `context/`; plans and output are global under `context/project/`; source code lives under `repos/`.

```
<workspace>/
├── repos/
│   ├── <repo-a>/             # repository source code
│   └── <repo-b>/
└── context/
    ├── <repo-a>/
    │   ├── spec/             # this repo's spec
    │   │   ├── CATALOG.yaml  # declares shared_interfaces: [...]
    │   │   ├── COMMON/
    │   │   └── <TAG>/...
    │   └── changes/          # repo-level CHANGE-<NNN>-*.md for this repo's spec changes
    ├── <repo-b>/
    │   ├── spec/
    │   └── changes/
    ├── shared/
    │   └── spec/             # cross-repo interface specs (the contract layer)
    │       ├── CATALOG.yaml
    │       ├── COMMON/
    │       └── <IFACE>/
    │           ├── <IFACE>-OVERVIEW.md
    │           ├── <IFACE>-DATAMODEL.md
    │           └── <IFACE>-INTERFACE.md
    └── project/
        ├── plans/            # PLAN-*.md story files (each tagged with its repo)
        ├── out/              # execution output
        └── changes/          # PROJECT-CHANGE-<NNN>-*.md index documents (reference repo changes)
```

**Rules:**

- A repository's **spec** files live **only** under `context/<repo>/spec/`. Never mix two repositories' modules in one spec subtree.
- The shared interface specs live **only** under `context/shared/spec/`. They are a **higher altitude** than any single repo — they describe contracts that cross repository boundaries.
- **Repo-level changes** live in `context/<repo>/changes/` — one `CHANGE-<NNN>-*.md` per logical spec change, sequenced per repo.
- **Project-level changes** live in `context/project/changes/` — one `PROJECT-CHANGE-<NNN>-*.md` per mspec run, referencing the repo-level change files it produced. This is what `mplan` reads to scope an incremental plan.
- **Plans and output** are workspace-global in `context/project/plans/` and `context/project/out/`.
- A repo consumes a shared interface by (a) listing the interface TAG in its `CATALOG.yaml` `shared_interfaces` field, and (b) adding the shared `*-INTERFACE.md` path to the `depends-on` front-matter of the repo spec files that couple to it.
- Changing a shared interface **cascades**: every repo that lists that interface in `shared_interfaces` must have its spec re-examined and each repo's updates recorded in its own change file. The project-level change references all of them. See STANDARD-CHANGE.md → "Shared-Interface Change Cascade."

`<repo>` is the repository's directory name under `repos/`. `<IFACE>` and `<TAG>` are short ALL-CAPS identifiers.

---

## File Naming & Directory Layout

- **Per-repo COMMON files** live in `context/<repo>/spec/COMMON/COMMON-<NAME>.md`
- **Per-repo module files** live in `context/<repo>/spec/<TAG>/<TAG>-<FACET>.md` where `<TAG>` is a short ALL-CAPS identifier (e.g., `AUTH`, `API`, `CONFIG`)
- **Shared interface files** live in `context/shared/spec/<IFACE>/<IFACE>-<FACET>.md`, where `<FACET>` is restricted to `OVERVIEW`, `DATAMODEL`, or `INTERFACE` (see "Shared Interface Specs" below)
- **Shared COMMON files** (optional) live in `context/shared/spec/COMMON/COMMON-<NAME>.md`

---

## Shared Interface Specs

The `context/shared/spec/` tree is the **contract layer** of the workspace. A shared interface module defines a contract that two or more repositories agree on — an event schema, an HTTP API, an RPC surface, a shared data format, a plugin protocol.

**Contract-only — three facets:**

| Facet | Purpose |
|-------|---------|
| `<IFACE>-OVERVIEW.md` | What the contract is, which repos produce vs. consume it, applicable shared COMMON files |
| `<IFACE>-DATAMODEL.md` | The shared types, schemas, enums, and constants that cross the boundary |
| `<IFACE>-INTERFACE.md` | The public contract: function signatures, endpoint schemas, event names/payloads |

Shared interface modules **must not** contain `IMPLEMENTATION` or `TESTING` facets — those belong to the repos that produce or consume the contract; keeping `shared/` implementation-free prevents repos from coupling to each other's internals. Contract conformance tests go in the consuming/producing repo's own `<TAG>-TESTING.md`.

**Consumption is declared, not inferred:**

A repo couples to a shared interface only when both are true:

1. The repo's `CATALOG.yaml` lists the interface TAG under `shared_interfaces`.
2. A repo spec file names the shared `*-INTERFACE.md` in its `depends-on` front-matter.

This declaration is what lets a shared-interface change find its consumers — `mc.py spec consumers <IFACE>` returns them — and write per-repo change files for each one, referenced by the project-level change document.

---

## Spec Depth

A repo module is written at one of two **depths**, recorded as `depth` on its `CATALOG.yaml` module entry:

| Depth | Facets present | Meaning |
|-------|----------------|---------|
| `contract` | `OVERVIEW`, `DATAMODEL`, `INTERFACE` | `DEPENDENCIES`, `IMPLEMENTATION` and `TESTING` are **deliberately** unwritten. The module states what it promises; how it keeps that promise is not decided yet. |
| `full` | all six | Nothing about the module is left to write. |

- **Absence means `full`.** A module entry carrying no `depth` field is `full` — which is exactly what every catalog written before the field existed meant, so introducing it changes no existing catalog's meaning.
- **Only `contract`-depth modules may have facets missing, and only those three may be the missing ones.** Depth narrows what a module *claims*; it never licenses omitting a facet arbitrarily. A `full` module missing any facet, and a `contract` module missing `OVERVIEW`, `DATAMODEL` or `INTERFACE`, are both `mc.py check catalog` findings.
- **Deepening is a write, not a discovery.** `mc.py spec depth <target> <module>` reports a module's depth; `--set full` is refused while any of `DEPENDENCIES`, `IMPLEMENTATION` or `TESTING` is still absent, so the field can never claim coverage that is not on disk.

The rule this exists to serve: **a module must be at `contract` depth before anything can be built against it, and is deepened to `full` in the slice that builds it.** Detail written earlier is detail written before the running system could contradict it.

**This is not the same thing as a contract-only shared interface.** A shared interface module has three facets because *a contract has no internals* — it is permanently three, and an `IMPLEMENTATION` or `TESTING` facet there is a defect rather than an unwritten file (see "Shared Interface Specs" above). A repo module at `contract` depth has three *for now*.

---

## Agreement Revision

Each shared interface carries a `revision` — an integer, absent meaning `1` — on its entry in `context/shared/spec/CATALOG.yaml`.

- A cascade that changes the contract **bumps it by one**, via `mc.py spec revision <IFACE> --bump`. Nothing else writes it, and nothing ever lowers it.
- **Work built against an interface records the revision it saw.** That is what makes "this was written against the old contract" a statement anyone can check rather than an inference from dates: `mc.py spec consumers <IFACE> --stale` returns the delivered stories whose recorded revision is below the interface's current one.

`revision` and `depth` are both **preserved, not derived** across catalog emissions — alongside `layer`, `requirements`, `exports` and `shared_interfaces`. Neither can be read off the spec tree: an absent `IMPLEMENTATION` file is indistinguishable from an unwritten one, and a revision is a fact about what consumers were told, not about what is on disk today.

---

## Front-Matter (Cross-References)

Every spec file must begin with a **depends-on** comment listing files it depends on. Paths are workspace-relative and may point inside the repo or into the shared contract layer:

```markdown
<!-- depends-on: context/repo-a/spec/COMMON/COMMON-STACK.md, context/repo-a/spec/AUTH/AUTH-INTERFACE.md, context/shared/spec/EVENT-BUS/EVENT-BUS-INTERFACE.md -->
```

This tells an implementing agent exactly which files to load. A dependency on a `context/shared/spec/<IFACE>/<IFACE>-INTERFACE.md` path is the **only** sanctioned cross-repo coupling — see "Dependency Rules" below.

---

## Project Overview Document

Every repo's spec suite must include `context/<repo>/spec/COMMON/COMMON-OVERVIEW.md`. This is the **entry point for that repository's spec** — a README-style document giving a reader (human or agent) enough context to understand the product before diving into module specs. It should also name the shared interfaces the repo produces or consumes and point to `context/shared/spec/`.

### Purpose

- Orient a first-time reader: what the product is, what problem it solves, who uses it
- Walk through the **primary lifecycles end-to-end** — not API signatures, but the full user journey from command to outcome
- Serve as a **feature index**: enumerate every major capability, with a pointer to the module spec that describes it
- Explain non-obvious design decisions or architectural choices at the system level

### Format

Write it as **narrative prose with section headers**, not as a table dump or bullet list. A reader should be able to follow along like a quickstart guide. Use code blocks for CLI invocations. Use diagrams (ASCII or Mermaid) for flow if helpful.

### Required Sections

```markdown
# {Product Name} — Overview

## What It Is
One to three paragraphs: the product's purpose, the problem it solves, and the intended user.
Do not describe implementation — describe value.

## Core Concepts
A glossary of 5–10 domain terms the reader needs before the lifecycle sections make sense.
Example entries: domain-specific terms the reader must know to follow the lifecycle sections.

## Primary Lifecycles
One H3 subsection per major user journey. Each subsection must:
- Name the lifecycle (e.g., "User Registration", "Processing an Order")
- Walk it step by step from the user's first command to the final output
- Name every component involved (commands, agents, files written) in the order they activate
- End with what the user has at the conclusion

Cover all major flows. If a product has 3 main lifecycles, all 3 must appear here.

## Feature Index
A two-column table: Feature | Spec Module. Maps every notable capability to its spec location.
Covers all user-facing commands, agents, and subsystems.

## System Architecture (optional but recommended)
A high-level diagram or description of the layers and how data/control flows between them.
Reference CATALOG.yaml for the authoritative layer list.
```

### Guidelines

- **Length:** 200–500 lines. Long enough to be genuinely useful; short enough to fit in context.
- **Audience:** An engineer starting to implement the system from scratch, and an AI agent about to generate code.
- **Do not duplicate** module-level detail — if something is covered in a module OVERVIEW or INTERFACE, reference that file instead.
- **Stays current:** update `COMMON-OVERVIEW.md` when a module is added or a lifecycle changes.
- **No `depends-on` front-matter required** — this file is the root; nothing precedes it.

---

## COMMON Files

Project-wide standards referenced by multiple modules. Requirements:

- Each file must be **short and focused** — they are injected into context alongside module specs
- Keep each under **~150 lines**
- Examples: `COMMON-DIRECTORY-LAYOUT.md`, `COMMON-CODING-STANDARDS.md`, `COMMON-TESTING-STANDARDS.md`, `COMMON-STACK.md`, `COMMON-ERROR-HANDLING.md`

---

## Module File Structure

Every module directory contains exactly these files:

| File | Purpose | Guidance |
|------|---------|----------|
| `<TAG>-OVERVIEW.md` | Module summary, responsibilities, and which COMMON files apply | **Short.** Shared context — prepended when an agent works on any file in this module, and imported by other modules that depend on it. |
| `<TAG>-DATAMODEL.md` | Types, schemas, constants, enums, config shapes | Use TypeScript-style type definitions. Include validation rules and defaults. |
| `<TAG>-INTERFACE.md` | Public contract: exported functions, API endpoints, events, CLI commands | The **only** file other modules should couple to. Define types, function signatures, endpoint schemas, event names. Precise enough that a *consuming* module can code against this without reading IMPLEMENTATION. |
| `<TAG>-DEPENDENCIES.md` | External packages and internal module dependencies | List each dependency with: name, version constraint (if external), and *why* it's needed. For internal deps, reference the depended-on module's OVERVIEW and INTERFACE files by path. |
| `<TAG>-IMPLEMENTATION.md` | Internal logic, algorithms, state machines, data flow | Describe behavior, not code. Use pseudocode or flow descriptions. Reference INTERFACE and DATAMODEL by path. |
| `<TAG>-TESTING.md` | Unit, integration, and E2E test specifications for this module | Describe test scenarios, not test code. Specify what each test validates and its pass/fail criteria. |

---

## Facet Tags (Horizontal Axis)

Each file type has a **facet** determining its role in the dependency chain:

| Facet | Files | Depends On | Can Be Referenced By |
|-------|-------|------------|---------------------|
| `facet:overview` | `*-OVERVIEW.md` | COMMON files only | All files in same module |
| `facet:datamodel` | `*-DATAMODEL.md` | Same module's OVERVIEW, COMMON-CODING-STANDARDS | Same module's INTERFACE, IMPLEMENTATION, TESTING |
| `facet:interface` | `*-INTERFACE.md` | Same module's DATAMODEL, COMMON-ERROR-HANDLING | **Any module** (this is the coupling boundary) |
| `facet:impl` | `*-IMPLEMENTATION.md` | Same module's INTERFACE + DATAMODEL, other modules' INTERFACE only | Same module's TESTING only |
| `facet:deps` | `*-DEPENDENCIES.md` | Same module's OVERVIEW, COMMON-STACK | Same module's IMPLEMENTATION |
| `facet:test` | `*-TESTING.md` | Same module's INTERFACE + IMPLEMENTATION, COMMON-TESTING-STANDARDS | Nothing (leaf node) |

**Critical Rule:** `facet:interface` files are the **only** cross-module coupling point. An IMPLEMENTATION file may call another module's INTERFACE, but **never** its IMPLEMENTATION.

---

## Layer Tags (Vertical Axis)

Modules are organized into implementation layers. Shared interface specs sit **above** every repo's layers — the contract altitude every participating repo depends on:

| Layer | Description | Example Modules |
|-------|-------------|-----------------|
| `LS-shared` | Cross-repo interface contracts in `context/shared/spec/` (highest altitude; depended on by repos, depends on nothing repo-specific) | EVENT-BUS, USER-API |
| `L0-foundation` | Project-wide standards within a repo (implement first) | COMMON |
| `L1-core` | Low-level services with no internal deps | CONFIG, AUTH, STORAGE |
| `L2-services` | Depends on L1 modules | USERS, NOTIFICATIONS |
| `L3-orchestration` | Central coordinators, depends on L1-L2 | WORKFLOW, SCHEDULER |
| `L4-ui` | User-facing, depends on L3 | CLI, WEB, API |
| `L5-integration` | Cross-cutting, depends on L3-L4 | E2E, MONITORING |

Adjust layer names, counts, and module assignments to fit your project. The key principle is that **higher layers depend on lower layers, never the reverse**.

---

## Dependency Rules

```yaml
# Intra-module flow (within same TAG)
OVERVIEW → DATAMODEL → INTERFACE → IMPLEMENTATION → TESTING
                ↘                  ↗
              DEPENDENCIES ────────┘

# Cross-module flow (between TAGs in the same repo)
- INTERFACE files may depend on other modules' INTERFACE files
- IMPLEMENTATION files may CALL other modules' INTERFACE
- IMPLEMENTATION files must NEVER import another module's IMPLEMENTATION
- TESTING files are leaf nodes — nothing depends on them

# Cross-repo flow (between repos, via the shared contract layer)
- A repo spec file may depend on a shared interface's INTERFACE (and, transitively, its DATAMODEL) only
- A repo spec file must NEVER depend on another repo's files directly — all cross-repo coupling goes through context/shared/spec/
- Shared interface files must NEVER depend on any repo's files (the contract layer is the highest altitude)
```

---

## CATALOG.yaml Schema

There are two flavors of catalog: one **per repo** and one for the **shared contract layer**.

### Per-repo catalog — `context/<repo>/spec/CATALOG.yaml`

```yaml
version: 1
generated: <timestamp>
repo: <repo-name>          # this repo's directory name under repos/

# Shared interface modules this repo produces or consumes. Each TAG must exist
# in context/shared/spec/CATALOG.yaml. Changing any listed interface triggers an
# UPDATE-mode mspec for this repo (see STANDARD-CHANGE.md → Shared-Interface Change Cascade).
shared_interfaces:
  - EVENT-BUS
  - USER-API

layers:
  L0-foundation:
    modules: [COMMON]
  L1-core:
    modules: [<your L1 modules>]
  L2-services:
    modules: [<your L2 modules>]
  # ... additional layers as needed

modules:
  <TAG>:
    layer: <layer-name>
    depth: contract | full        # optional; absent means full -- see "Spec Depth"
    files:
      - path: context/<repo>/spec/<TAG>/<TAG>-<FACET>.md
        facet: <facet-name>
        depends_on:
          - <path-to-dependency>      # may include context/shared/spec/<IFACE>/<IFACE>-INTERFACE.md
        exports:          # only on INTERFACE files
          - <ExportName>
```

### Shared catalog — `context/shared/spec/CATALOG.yaml`

Lists the interface modules only. It does **not** enumerate consumers — `mc.py spec consumers <IFACE>` derives that set from every per-repo catalog's `shared_interfaces` field.

```yaml
version: 1
generated: <timestamp>
scope: shared

interfaces:
  <IFACE>:
    revision: <integer>           # optional; absent means 1 -- see "Agreement Revision"
    files:
      - path: context/shared/spec/<IFACE>/<IFACE>-<FACET>.md   # facet ∈ {overview, datamodel, interface}
        facet: <facet-name>
        depends_on:
          - <path-to-dependency>      # shared COMMON or another shared interface only
        exports:          # only on INTERFACE files
          - <ExportName>
```

The catalogs enable:
1. **Determine load order** — Process by layer, then by facet within each module (shared interfaces first, as the highest altitude)
2. **Validate coupling** — Reject implementations that import non-INTERFACE files, and cross-repo references that bypass `context/shared/spec/`
3. **Parallelize safely** — All modules within the same layer can be implemented concurrently
4. **Find cascade targets** — Given a changed shared interface TAG, the consuming repos are exactly those whose catalog lists it in `shared_interfaces`

---

## E2E Testing Hard Rules

Applies to all E2E tests (module-level and project-level):

- **No mocks, fakes, stubs, or test doubles.** E2E tests exercise real dependencies.
- **No skips or conditional logic** that could produce false-positive passes.
- **Must fail** if any required dependency or module is missing/unavailable.
- **Cover the primary user workflows** as defined in `COMMON-OVERVIEW.md`.

**Delivery.** This section **owns** the four rules above. A story agent's loaded context is only its
own story, its Context Files, and the repo `CATALOG.yaml` — it never reads this file. The rules reach
it by **injection** into `shared/PLAN-STORY-TEMPLATE.md` at render time, at both
`INJECT:E2E-HARD-RULES` markers (**Post-Story Validation** and **Slice Acceptance**, gated on an E2E
module existing in the catalog). `mc.py plan story-emit` performs the injection, reading the four
bullets from this section. The delivered copy is generated, never hand-maintained, so editing the
rules here updates every story emitted afterwards.

---

## Delivered-Surface Rule

Applies to every module, whatever its layer:

- **Name the surface.** A module's `TESTING` facet names the surface its behaviour is delivered on — the command line a user invokes, the artifact it emits, the endpoint it answers.
- **Reach the behaviour through it.** At least one check exercises the behaviour through that surface, not only through the components beneath it: a module delivered as a command line is checked by invoking that command; one delivered as an emitted artifact is checked against that artifact.
- **Below the surface does not count.** Confirmation obtained below the delivered surface is evidence about internals and is not counted as confirmation of the delivered behaviour.
- **No condition suspends this.** Unlike the E2E rules, this one is unconditional — every module has a delivered surface, so there is no catalog condition under which it does not apply.

**Delivery.** This section **owns** the four rules above. A story agent's loaded context is only its
own story, its Context Files, and the repo `CATALOG.yaml` — it never reads this file. The rules reach
it by **injection** into `shared/PLAN-STORY-TEMPLATE.md` at render time, at the single
`INJECT:DELIVERED-SURFACE-RULE` marker (**Post-Story Validation**, ungated — every module has a
delivered surface, so there is no catalog condition to gate on). `mc.py plan story-emit` performs the
injection, reading the four bullets from this section. The delivered copy is generated, never
hand-maintained, so editing the rules here updates every story emitted afterwards.

---

## Process & Ordering Rules

1. Write **shared interface specs before the repos that depend on them**.
2. Write **COMMON files before module files**.
3. Write **modules by layer order:** L1-core → L2-services → L3-orchestration → L4-ui → L5-integration.
4. Within each module, write **files by facet order:** OVERVIEW → DATAMODEL → INTERFACE → DEPENDENCIES → IMPLEMENTATION → TESTING (DEPENDENCIES may be written any time before IMPLEMENTATION).
5. **Validate cross-references:** every `depends-on` path must point to a file that actually exists. Run `mc.py check depends-on <target>`.
6. **Validate coupling rules:** IMPLEMENTATION files must never depend on another module's IMPLEMENTATION — only INTERFACE files. Cross-repo `depends-on` paths must point only into `context/shared/spec/` (never into another repo). Run `mc.py check coupling <target>`.
7. **Keep `shared_interfaces` honest:** every TAG listed in a repo's catalog must correspond to at least one `depends-on` on the shared `*-INTERFACE.md`, and vice versa. Run `mc.py check coupling <target>`.
8. **Keep OVERVIEW and COMMON files concise** — they are context-injected into every related task, and verbose shared files waste token budget for implementing agents.

Rules 1–4 and 8 govern how you write. Rules 5–7 are mechanical checks: invoke the checker named
above rather than re-reading paths and catalog entries by hand — `mc.py check all <target>` runs all
three at once. A non-zero exit is a hard failure to fix, not a cue to re-derive the check in prose.

