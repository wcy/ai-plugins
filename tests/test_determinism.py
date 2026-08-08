"""Determinism, asserted rather than claimed.

``TOOLS-IMPLEMENTATION.md`` §Determinism makes four promises: given the same
workspace bytes and the same injected ``--now``, every command produces
byte-identical output; emitted YAML uses schema key order, fixed indentation and
no line wrapping, so re-emitting an unchanged file diffs empty; nothing depends
on filesystem iteration order; and nothing else varies -- no randomness, no
wall-clock read outside the injected clock, no dependence on locale or on any
environment variable but ``CLAUDE_PLUGIN_ROOT``.

**The command list is derived, never authored here.** :data:`COMMANDS` comes
from ``mc.py``'s own argument declaration -- ``mc.GROUPS`` plus each group
module's ``register()`` -- which is the CLI surface ``TOOLS-INTERFACE.md``
specifies. A verb added to the tool without a case in :func:`arguments` fails
:func:`test_every_command_the_cli_declares_has_a_determinism_case`, so coverage
cannot be satisfied by a count this file supplies to itself. Which of those
commands *emits* is derived too: a command emits iff running it changes the
workspace bytes, measured rather than declared.

Every fixture is synthetic and lives in ``tmp_path``; no fixture file is
committed under ``plugins/metacoder/``. Every case injects a clock -- ``NOW``
for the command under test, :data:`SEED_NOW` for the fixture it runs against --
so nothing here can pass only on a particular day.
"""

import argparse
import ast
import io
import json
import shutil
from pathlib import Path

import pytest

from conftest import NOW, SyntheticWorkspace
from tools import core, mc, req

#: The clock the *fixture* is built at. Deliberately different from ``NOW``, the
#: clock every command under test runs at, so a timestamp carried over from the
#: build is distinguishable from one the run wrote.
SEED_NOW = "2026-01-01"

PLAN_ID = "003-demo-plan"
SECOND_PLAN_ID = "005-second-plan"
FIRST_STORY = "01-01-demo-ALPHA"
#: Wave 2 of 2 -- the last wave, so its story file carries both injection points.
LAST_STORY = "02-01-demo-BETA"

CATALOG_REL = "context/demo/spec/CATALOG.yaml"
LISTING_REL = "listings/worktrees.txt"
REPORT_REL = "context/project/out/003-demo-plan/mverify-report.json"
BRANCH = "mexec/003-demo-plan/02-01-demo-BETA/r1/1"
WORKTREE = "repos/demo/02-01-demo-BETA-r1-1"

#: The requirements-change record `req.change-close` closes, and the fresh
#: path `req.change-emit` writes -- both under the fixture's own tier.
REQ_CHANGE_CLOSE_REL = "context/demo/requirements/changes/REQ-CHANGE-001-tightened-scope.md"
REQ_CHANGE_EMIT_REL = "context/demo/requirements/changes/REQ-CHANGE-002-emitted-here.md"

#: The deferral the fixture already carries -- what ``todo remove`` deletes and
#: ``todo list`` reads. Filed through ``todo add`` at :data:`SEED_NOW` like every
#: other seeded artifact, so the fixture is what the tool leaves behind.
TODO_TITLE = "check handoff reports no per-repo counts"
TODO_FIELDS = {
    "run": "/mfix",
    "kind": "spec-drift",
    "origin": "CHANGE-001",
    "priority": "low",
    "risk_if_unfixed": "low",
    "regression_risk": "low",
    "cost": "low",
    "context": "`check handoff` in plugins/metacoder/tools/check.py aggregates across repos.",
}

#: Emitted files whose top-level key order must follow their schema's.
SCHEMA_ORDERED = (
    ("context/project/plans/%s/plan.yaml" % PLAN_ID, "plan-graph"),
    ("context/project/plans/%s/state.yaml" % PLAN_ID, "plan-state"),
    ("context/project/state.yaml", "project-state"),
    (CATALOG_REL, "catalog"),
)


# ---------------------------------------------------------------------------
# The CLI surface, taken from the CLI
# ---------------------------------------------------------------------------


def _verb_names(parser):
    """The verb names one group parser declares; ``[]`` for a verbless group."""
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return sorted(action.choices)
    return []


def cli_commands():
    """Every ``group[.verb]`` ``mc.py`` declares, derived from its own parsers."""
    commands = []
    for group in mc.GROUPS:
        module = mc.load_group(group)
        parser = argparse.ArgumentParser(prog="mc.py")
        subparsers = parser.add_subparsers(dest="group")
        module.register(subparsers)
        verbs = _verb_names(subparsers.choices[group])
        if verbs:
            commands.extend("%s.%s" % (group, verb) for verb in verbs)
        else:
            commands.append(group)
    return sorted(commands)


COMMANDS = cli_commands()


# ---------------------------------------------------------------------------
# The synthetic workspace every case runs against
# ---------------------------------------------------------------------------

REQUIREMENTS = """\
<!-- requirements: demo -->
<!-- updated: 2026-01-01 -->

# Requirements — demo

### REQ-001: The mechanical steps are performed by the tool

**Need:** A run should not spend model effort reproducing a computable result.
**Rationale:** Cheaper, and the same every time.
**Status:** active

### REQ-002: The same request produces the same run

**Need:** Two runs over the same inputs should agree byte for byte.
**Rationale:** A step that varies cannot be reviewed.
**Status:** active
"""

CATALOG = """\
version: 1
generated: 2026-01-01
repo: demo
shared_interfaces:
  - EVENT-BUS
layers:
  L0-foundation:
    modules: [COMMON]
  L1-core:
    modules: [ALPHA]
  L2-services:
    modules: [BETA, E2E]
modules:
  COMMON:
    layer: L0-foundation
    requirements: [REQ-001]
    files:
      - path: context/demo/spec/COMMON/COMMON-OVERVIEW.md
        facet: overview
  ALPHA:
    layer: L1-core
    requirements: [REQ-002]
    files:
      - path: context/demo/spec/ALPHA/ALPHA-OVERVIEW.md
        facet: overview
      - path: context/demo/spec/ALPHA/ALPHA-INTERFACE.md
        facet: interface
        exports: [alphaClient]
      - path: context/demo/spec/ALPHA/ALPHA-IMPLEMENTATION.md
        facet: impl
  BETA:
    layer: L2-services
    files:
      - path: context/demo/spec/BETA/BETA-OVERVIEW.md
        facet: overview
      - path: context/demo/spec/BETA/BETA-INTERFACE.md
        facet: interface
        exports: [betaClient]
  E2E:
    layer: L2-services
    files:
      - path: context/demo/spec/E2E/E2E-OVERVIEW.md
        facet: overview
      - path: context/demo/spec/E2E/E2E-TESTING.md
        facet: test
"""

#: Caller-supplied ``git worktree list --porcelain`` output. Supplied as data:
#: this suite never runs git, and neither does the group that reads it.
WORKTREE_LISTING = """\
worktree repos/demo/main
HEAD 1111111111111111111111111111111111111111
branch refs/heads/main

worktree repos/demo/02-01-demo-BETA-r1-1
HEAD 2222222222222222222222222222222222222222
branch refs/heads/mexec/003-demo-plan/02-01-demo-BETA/r1/1

worktree repos/demo/integration
HEAD 3333333333333333333333333333333333333333
branch refs/heads/mexec/003-demo-plan/integration
"""

CONFORMANCE_REPORT = (
    '{\n  "scope": {\n    "kind": "aggregate"\n  },\n  "findings": [],\n  "clean": true\n}\n'
)

COMMON_OVERVIEW = "context/demo/spec/COMMON/COMMON-OVERVIEW.md"
SHARED_INTERFACE = "context/shared/spec/EVENT-BUS/EVENT-BUS-INTERFACE.md"


def _spec(module, filename, depends=()):
    text = ""
    if depends:
        text += "<!-- depends-on: %s -->\n" % ", ".join(depends)
    text += "\n# %s\n\nBody.\n" % filename[: -len(".md")]
    return "context/demo/spec/%s/%s" % (module, filename), text


def _change(number, slug, status):
    return (
        "context/demo/changes/CHANGE-%s-%s.md" % (number, slug),
        "<!-- change: %s -->\n"
        "<!-- scope: repo -->\n"
        "<!-- repo: demo -->\n"
        "<!-- status: %s -->\n"
        "<!-- date: 2026-01-01 -->\n"
        "\n# CHANGE-%s: %s\n" % (number, status, number, slug),
    )


def _index(number, slug, status, refs):
    rows = "".join("| `demo` | `%s` | a row |\n" % ref for ref in refs)
    return (
        "context/project/changes/PROJECT-CHANGE-%s-%s.md" % (number, slug),
        "<!-- project-change: %s -->\n"
        "<!-- scope: repo -->\n"
        "<!-- repos: demo -->\n"
        "<!-- status: %s -->\n"
        "<!-- date: 2026-01-01 -->\n"
        "\n# PROJECT-CHANGE-%s: %s\n"
        "\n## Summary\n\nOne change.\n"
        "\n## Repo Change Files\n\n"
        "| Repo | Change File | Summary |\n"
        "|------|-------------|---------|\n"
        "%s" % (number, status, number, slug, rows),
    )


def _req_change(number, slug, status):
    return (
        "context/demo/requirements/changes/REQ-CHANGE-%s-%s.md" % (number, slug),
        "<!-- req-change: %s -->\n"
        "<!-- tier: demo -->\n"
        "<!-- status: %s -->\n"
        "<!-- date: 2026-01-01 -->\n"
        "\n# REQ-CHANGE-%s: %s\n" % (number, status, number, slug),
    )


def layout():
    """Every file the fixture writes, as an ordered list of ``(path, text)``.

    The order is load-bearing: :func:`build` walks it forwards and the
    iteration-order case walks it backwards, so the two trees hold identical
    bytes created in opposite orders.
    """
    return [
        ("context/demo/requirements/REQUIREMENTS.md", REQUIREMENTS),
        _req_change("001", "tightened-scope", "open"),
        (COMMON_OVERVIEW, "\n# COMMON-OVERVIEW\n\nThe root file: exempt from depends-on.\n"),
        (
            SHARED_INTERFACE,
            "<!-- depends-on: %s -->\n\n# EVENT-BUS-INTERFACE\n" % COMMON_OVERVIEW,
        ),
        _spec("ALPHA", "ALPHA-OVERVIEW.md", [COMMON_OVERVIEW]),
        _spec(
            "ALPHA",
            "ALPHA-INTERFACE.md",
            ["context/demo/spec/ALPHA/ALPHA-OVERVIEW.md", SHARED_INTERFACE],
        ),
        _spec("ALPHA", "ALPHA-IMPLEMENTATION.md", ["context/demo/spec/ALPHA/ALPHA-INTERFACE.md"]),
        _spec("BETA", "BETA-OVERVIEW.md", [COMMON_OVERVIEW]),
        _spec(
            "BETA",
            "BETA-INTERFACE.md",
            [
                "context/demo/spec/BETA/BETA-OVERVIEW.md",
                "context/demo/spec/ALPHA/ALPHA-INTERFACE.md",
            ],
        ),
        _spec("E2E", "E2E-OVERVIEW.md", [COMMON_OVERVIEW]),
        _spec("E2E", "E2E-TESTING.md", ["context/demo/spec/E2E/E2E-OVERVIEW.md"]),
        (CATALOG_REL, CATALOG),
        _change("001", "alpha-retry", "pending"),
        _change("002", "beta-timeout", "applied"),
        _index(
            "001", "alpha-retry", "pending", ["context/demo/changes/CHANGE-002-beta-timeout.md"]
        ),
        (LISTING_REL, WORKTREE_LISTING),
        (REPORT_REL, CONFORMANCE_REPORT),
    ]


def _story(story_id, module, wave):
    return story_id, {
        "repo": "demo",
        "module": module,
        "wave": wave,
        "prerequisites": [] if wave == 1 else [FIRST_STORY],
        "change_file": "context/demo/changes/CHANGE-001-alpha-retry.md",
        "target_paths": ["src/%s.py" % module.lower()],
        "validation": {"post_story": [{"kind": "prose", "description": "it holds"}]},
    }


def draft(project_change="001"):
    """The draft plan graph the emitted plans are built from."""
    return {
        "project_change": project_change,
        "stories": dict([_story(FIRST_STORY, "ALPHA", 1), _story(LAST_STORY, "BETA", 2)]),
    }


#: The sliced plan `state sweep` writes to. Separate from :data:`PLAN_ID`,
#: which is deliberately unsliced.
SWEEP_PLAN_ID = "004-sweep-plan"

#: The ``SliceDraft[]`` ``plan reslice`` is exercised with: one slice holding
#: both of the fixture's stories, so every story lands in exactly one.
SLICE_DRAFTS = [
    {
        "slice": "00",
        "name": "walking skeleton",
        "behavior": "the plan runs end to end",
        "acceptance": [
            {"kind": "exit-code", "command": "true", "description": "it runs"}
        ],
        "stories": [FIRST_STORY, LAST_STORY],
    }
]


def call(workspace, group, verb, **fields):
    """Call one group directly -- never through ``argv`` -- and return its Result."""
    module = mc.load_group(group)
    if verb is not None:
        fields["verb"] = verb
    return module.run(workspace.args(**fields), workspace.ws)


def build(root, reverse=False):
    """A synthetic workspace holding everything every command needs.

    The plan directory, its state file and the ledger are produced by the tool
    itself at :data:`SEED_NOW`, so the fixture is what a real run leaves behind
    rather than a hand-written approximation of it.
    """
    workspace = SyntheticWorkspace(root)
    entries = layout()
    for relative, text in reversed(entries) if reverse else entries:
        workspace.write(relative, text)
    workspace.mkdir("repos/demo")

    result = call(
        workspace,
        "plan",
        "emit",
        plan_id=PLAN_ID,
        stdin=io.StringIO(json.dumps(draft())),
        now=SEED_NOW,
    )
    assert result.ok, [item.render() for item in result.diagnostics]
    result = call(workspace, "state", "run-increment", plan_id=PLAN_ID, now=SEED_NOW)
    assert result.ok, [item.render() for item in result.diagnostics]
    # A second, *sliced* plan, whose state.yaml therefore carries the sweep
    # version. The plan above stays unsliced on purpose -- two cases rely on its
    # graph declaring no `slices` -- and `state sweep` cannot write to it,
    # because a plan predating the declaration cannot declare. That refusal is
    # the same rule `check handoff` applies when it skips such a plan, so the
    # fixture holds one plan of each kind rather than bending either.
    result = call(
        workspace,
        "plan",
        "emit",
        plan_id=SWEEP_PLAN_ID,
        stdin=io.StringIO(json.dumps(dict(draft(), version=3, slices=SLICE_DRAFTS))),
        now=SEED_NOW,
    )
    assert result.ok, [item.render() for item in result.diagnostics]
    result = call(
        workspace, "todo", "add", title=TODO_TITLE, now=SEED_NOW, **dict(TODO_FIELDS)
    )
    assert result.ok, [item.render() for item in result.diagnostics]
    return workspace


@pytest.fixture
def fixture(tmp_path):
    """One built workspace, for the cases that need only one."""
    return build(tmp_path / "workspace")


# ---------------------------------------------------------------------------
# Per-command arguments. One entry per command in COMMANDS -- the module
# docstring says why the *list* is derived and only the arguments are authored.
# ---------------------------------------------------------------------------


def arguments():
    """Every command's argument fields, freshly built on each call.

    Freshly, because ``plan emit`` consumes a stream: a namespace reused across
    two invocations would leave the second reading an exhausted stdin, turning a
    determinism failure into a parse failure.
    """
    return {
        "validate": {"kind": "catalog", "files": [CATALOG_REL]},
        "change.resolve": {"repo": "demo", "slug": "retry-policy"},
        "change.index-resolve": {"slug": "retry-policy"},
        "change.emit": {
            "path": "context/demo/changes/CHANGE-009-emitted-here.md",
            "scope": "repo",
            "status": "pending",
            "title": "Emitted Here",
            "repo": "demo",
        },
        "change.close": {
            "path": "context/demo/changes/CHANGE-001-alpha-retry.md",
            "status": "applied",
        },
        "spec.mode": {"target": "demo"},
        "spec.layers": {"target": "demo"},
        "spec.catalog-emit": {"target": "demo"},
        "spec.consumers": {"interface": "EVENT-BUS"},
        # The reporting form of each: `--set`/`--bump` are what write, and they
        # have their own coverage in test_spec.py.
        "spec.depth": {"target": "demo", "module": "ALPHA", "set": None},
        "spec.revision": {"interface": "EVENT-BUS", "bump": False},
        "req.next": {"tier": "demo"},
        "req.mnemonic": {"title": "Recover a specification from code that never had one"},
        "req.gate": {"tier": "demo"},
        "req.change-resolve": {"tier": "demo", "slug": "narrowed-scope"},
        "req.change-emit": {
            "path": REQ_CHANGE_EMIT_REL,
            "tier": "demo",
            "status": "open",
        },
        "req.change-close": {"path": REQ_CHANGE_CLOSE_REL, "change": "002"},
        "req.change-list": {"tier": "demo", "open": True},
        "plan.scope": {},
        "plan.resolve": {"plan_id": None},
        "plan.story-id": {"file": "PLAN-%s.md" % LAST_STORY},
        "plan.waves": {"target": "demo"},
        "plan.slices": {"plan_id": PLAN_ID},
        "plan.reslice": {
            "plan_id": PLAN_ID,
            "stdin": io.StringIO(json.dumps(SLICE_DRAFTS)),
        },
        "plan.emit": {
            "plan_id": SECOND_PLAN_ID,
            "stdin": io.StringIO(json.dumps(draft(project_change="002"))),
        },
        "plan.story-emit": {"plan_id": PLAN_ID, "story_id": LAST_STORY},
        "plan.shards": {"plan_id": PLAN_ID},
        "state.run-increment": {"plan_id": PLAN_ID},
        "state.set-plan": {"plan_id": PLAN_ID, "status": "in-progress"},
        "state.set-story": {
            "plan_id": PLAN_ID,
            "story_id": LAST_STORY,
            "status": "applied",
            "attempt": 1,
            "branch": BRANCH,
            "worktree": WORKTREE,
        },
        "state.set-slice": {
            "plan_id": PLAN_ID,
            "slice_id": "00",
            "status": "applied",
            "acceptance": "pass",
            "outcome": "continue",
        },
        "state.conformance": {
            "plan_id": PLAN_ID,
            "status": "clean",
            "report": REPORT_REL,
            "findings": 0,
        },
        "state.telemetry": {"plan_id": PLAN_ID, "cost": 1.5, "tokens": 4096, "wall_clock": 61.5},
        # `filed: 0` deliberately: the case that must still write a block, since
        # a declared zero and an absent block are the two states `sweep` exists
        # to keep apart. A case passing a non-zero count would not exercise it.
        "state.sweep": {"plan_id": SWEEP_PLAN_ID, "filed": 0, "titles": None},
        "worktree.names": {"plan_id": PLAN_ID, "story_id": LAST_STORY, "run": 1, "attempt": 1},
        "worktree.reconcile": {"plan_id": PLAN_ID, "list_from": LISTING_REL},
        # A title the fixture does not already carry, so the case writes; the
        # fixture's own entry is what `todo.remove` deletes.
        "todo.add": dict(TODO_FIELDS, title="a second deferral"),
        "todo.remove": {"title": TODO_TITLE},
        "todo.list": {"run": None, "kind": None},
        "check.depends-on": {"target": "demo"},
        "check.coupling": {"target": "demo"},
        "check.requirements": {"target": "demo"},
        "check.catalog": {"target": "demo"},
        "check.todo": {},
        "check.handoff": {},
        "check.all": {"target": "demo"},
        "status": {},
    }


#: The commands this file has a case for, read off the same table the cases run
#: from -- there is deliberately no second list to keep in step with it.
COVERED = sorted(arguments())

#: The verbs ``TOOLS-INTERFACE.md`` documents as writing files: ``change emit``,
#: ``change close``, ``spec catalog-emit``, ``plan emit``, ``plan reslice``,
#: ``plan story-emit``, ``todo add``, ``todo remove``, and every ``state`` verb.
#: ``todo list`` is deliberately not among them -- it reads.
#: :func:`test_the_measured_emitting_set_is_the_documented_one` checks this
#: against what the tool actually writes, so it cannot quietly go stale.
#:
#: ``spec depth`` and ``spec revision`` write only under ``--set``/``--bump``;
#: the case :func:`arguments` gives each is the reporting form, so neither
#: appears here and neither may write.
DOCUMENTED_EMITTING = sorted(
    {"change.emit", "change.close", "spec.catalog-emit"}
    | {"plan.emit", "plan.reslice", "plan.story-emit"}
    | {"req.change-emit", "req.change-close"}
    | {"todo.add", "todo.remove"}
    | {name for name in COMMANDS if name.startswith("state.")}
)

#: The one emitter whose second identical invocation is *not* expected to leave
#: the workspace byte-identical, and by design: ``TOOLS-IMPLEMENTATION.md``
#: §Determinism says the run counter is read from ``state.yaml`` and never
#: generated, so incrementing it twice reaches 3. Its determinism is the same
#: property the others have -- same input bytes, same output bytes -- and is
#: asserted for it alongside them.
NOT_IDEMPOTENT = frozenset({"state.run-increment"})

IDEMPOTENT_EMITTERS = [name for name in DOCUMENTED_EMITTING if name not in NOT_IDEMPOTENT]


def invoke(workspace, command, **overrides):
    """Run one command against ``workspace``, at the injected clock ``NOW``."""
    group, _, verb = command.partition(".")
    fields = arguments()[command]
    fields.update(overrides)
    return call(workspace, group, verb or None, **fields)


def image(root):
    """``{workspace-relative path: bytes}`` for every file under ``root``."""
    root = Path(root)
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def assert_same_image(first, second):
    assert sorted(first) == sorted(second)
    for relative in sorted(first):
        assert first[relative] == second[relative], relative


# ---------------------------------------------------------------------------
# Coverage: the list of commands is the CLI's, not this file's
# ---------------------------------------------------------------------------


def test_every_command_the_cli_declares_has_a_determinism_case():
    """A verb added to ``mc.py`` without a case here fails right here."""
    assert COVERED == COMMANDS


def test_the_derived_command_list_is_not_empty_and_spans_every_group():
    """Guards the derivation itself: an empty list makes coverage vacuous."""
    assert len(COMMANDS) > len(mc.GROUPS)
    assert sorted({name.partition(".")[0] for name in COMMANDS}) == sorted(mc.GROUPS)


# ---------------------------------------------------------------------------
# Same inputs, same bytes -- for every command, emitting or not
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("command", COMMANDS)
def test_the_same_inputs_produce_byte_identical_output(tmp_path, command):
    """Build, run, throw the tree away, rebuild identically, run again.

    Two independent runs over the same workspace bytes at the same ``--now``,
    from the same root, so any difference can only come from the tool: the
    workspace image and the ``--json`` envelope must both match byte for byte.
    """
    root = tmp_path / "workspace"

    workspace = build(root)
    first_envelope = invoke(workspace, command).to_json()
    first_image = image(root)

    shutil.rmtree(str(root))
    workspace = build(root)
    second_envelope = invoke(workspace, command).to_json()
    second_image = image(root)

    assert first_envelope == second_envelope
    assert_same_image(first_image, second_image)


@pytest.mark.parametrize("command", COMMANDS)
def test_the_json_envelope_is_the_documented_shape(fixture, command):
    """Comparing envelopes means something only if they are the envelope."""
    envelope = json.loads(invoke(fixture, command).to_json())
    assert list(envelope) == ["ok", "command", "data", "diagnostics"]
    assert envelope["command"].partition(".")[0] == command.partition(".")[0]
    assert envelope["ok"] == (
        not any(item["severity"] == "error" for item in envelope["diagnostics"])
    )


# ---------------------------------------------------------------------------
# Which commands emit is measured, not declared
# ---------------------------------------------------------------------------


def measure_emitting(tmp_path):
    """Every command whose invocation changes the workspace bytes."""
    emitting = []
    for command in COMMANDS:
        root = tmp_path / command.replace(".", "-")
        workspace = build(root)
        before = image(root)
        invoke(workspace, command)
        if image(root) != before:
            emitting.append(command)
    return emitting


def test_the_measured_emitting_set_is_the_documented_one(tmp_path):
    """What writes files is exactly what the interface says writes files.

    Both directions matter: an emitting verb missing from the set would escape
    the re-emission cases below, and a verb writing when its documented return is
    a value would be a defect of its own.
    """
    assert measure_emitting(tmp_path) == DOCUMENTED_EMITTING


@pytest.mark.parametrize("command", DOCUMENTED_EMITTING)
def test_re_emitting_an_unchanged_workspace_is_an_empty_diff(fixture, command):
    """Run the emitter, then run it again: every file it wrote is unchanged."""
    invoke(fixture, command)
    after_first = image(fixture.root)

    invoke(fixture, command)
    after_second = image(fixture.root)

    if command in NOT_IDEMPOTENT:
        assert after_first != after_second
        return
    assert_same_image(after_first, after_second)


def test_the_run_counter_is_read_from_the_file_rather_than_generated(fixture):
    """The one non-idempotent verb is non-idempotent for the documented reason."""
    path = fixture.path("context/project/plans/%s/state.yaml" % PLAN_ID)
    assert core.load_yaml(path, "state.yaml")["run"] == 1
    invoke(fixture, "state.run-increment")
    assert core.load_yaml(path, "state.yaml")["run"] == 2
    invoke(fixture, "state.run-increment")
    assert core.load_yaml(path, "state.yaml")["run"] == 3


# ---------------------------------------------------------------------------
# The story template's two slice fields, rendered deterministically
# ---------------------------------------------------------------------------

#: The header field ``shared/PLAN-STORY-TEMPLATE.md`` gained with the slice
#: loop: the delivery slice a story belongs to. It is part of what
#: ``plan story-emit`` renders, so it is subject to the same promise every
#: other emission makes -- same workspace bytes, same injected clock, same
#: output bytes.
SLICE_HEADER_FIELD = "**Slice:**"

#: The end-to-end section's heading, renamed from
#: ``## Final Validation (last wave only)``. Pinned here so that renaming it
#: again without moving the renderer with it fails in this file rather than
#: silently emitting a story with neither name.
SLICE_ACCEPTANCE_HEADING = "## Slice Acceptance"

#: The name the section carried before the rename. No rendered story may still
#: carry it -- two differently-named end-to-end sections would be exactly the
#: divergence the rename exists to remove.
RETIRED_ACCEPTANCE_HEADING = "## Final Validation"

#: The header line a version-1/2 graph must render, whole: its synthesized slice
#: ``00``, named ``whole plan``. Pinned as a whole line rather than counted,
#: because the template's own unsubstituted ``{NN} — {slice name}`` satisfies a
#: count and satisfies nothing else.
LEGACY_SLICE_LINE = "**Slice:** 00 — whole plan"

#: The two placeholders the header line carries in the template. No rendered
#: story, on any graph version, may still hold either.
SLICE_PLACEHOLDERS = ("{NN}", "{slice name}")


def rendered_story(workspace, story_id=LAST_STORY):
    """Emit one story file and return its text.

    :data:`LAST_STORY` by default: the acceptance section is gated, and the
    fixture's graph declares no ``slices``, so its one synthetic slice spans
    every wave -- the version-1/2 case whose rendering the rename must leave
    otherwise unchanged.
    """
    result = invoke(workspace, "plan.story-emit", story_id=story_id)
    assert result.ok, [item.render() for item in result.diagnostics]
    return workspace.path(result.data["file"]).read_text(encoding="utf-8")


def test_the_rendered_story_carries_both_new_template_fields(fixture):
    """Determinism asserted over an absent field would be vacuously true."""
    text = rendered_story(fixture)
    assert text.count(SLICE_HEADER_FIELD) == 1
    # Counting the field says only that the template line survived. It counted
    # 1 while the renderer emitted `{NN} — {slice name}` verbatim, which is how
    # an unfilled header shipped: the line is pinned whole, and both
    # placeholders are asserted gone from the story entirely.
    assert LEGACY_SLICE_LINE in text
    for placeholder in SLICE_PLACEHOLDERS:
        assert placeholder not in text
    assert text.count(SLICE_ACCEPTANCE_HEADING) == 1
    assert RETIRED_ACCEPTANCE_HEADING not in text


def test_both_new_fields_render_byte_identically_twice(tmp_path):
    """Build, render, throw the tree away, rebuild identically, render again."""
    root = tmp_path / "workspace"

    workspace = build(root)
    first = rendered_story(workspace)

    shutil.rmtree(str(root))
    workspace = build(root)
    second = rendered_story(workspace)

    assert first == second
    assert first.count(SLICE_HEADER_FIELD) == second.count(SLICE_HEADER_FIELD) == 1
    assert first.count(SLICE_ACCEPTANCE_HEADING) == second.count(SLICE_ACCEPTANCE_HEADING) == 1


def test_the_slice_header_is_unconditional_and_the_acceptance_section_is_not(fixture):
    """The header is on every story; the acceptance section is gated.

    What distinguishes a gated section from a renamed one: a story the gate
    excludes loses the section while keeping the header field.
    """
    text = rendered_story(fixture, story_id=FIRST_STORY)
    assert LEGACY_SLICE_LINE in text
    for placeholder in SLICE_PLACEHOLDERS:
        assert placeholder not in text
    assert SLICE_ACCEPTANCE_HEADING not in text
    assert RETIRED_ACCEPTANCE_HEADING not in text


def test_a_legacy_graph_renders_exactly_what_its_declared_equivalent_does(fixture):
    """Byte-identity between the synthesized slice and the declared same slice.

    The fixture's graph declares no ``slices``, so it is read as carrying the
    synthesized ``00`` spanning every wave. Resliced into precisely that slice
    -- same id, same name, same members -- the graph becomes version 3 and the
    rendering must not move a byte. The synthesized slice is not a second
    rendering path kept in step by hand; it is the one path fed a derived
    value, and this is what says so.
    """
    legacy = rendered_story(fixture)
    equivalent = dict(SLICE_DRAFTS[0], slice="00", name="whole plan")
    result = invoke(fixture, "plan.reslice", stdin=io.StringIO(json.dumps([equivalent])))
    assert result.ok, [item.render() for item in result.diagnostics]

    graph = core.load_yaml(fixture.path("context/project/plans/%s/plan.yaml" % PLAN_ID))
    assert graph["version"] == 3  # the legacy read is no longer in play

    assert rendered_story(fixture) == legacy
    assert LEGACY_SLICE_LINE in legacy


# ---------------------------------------------------------------------------
# Filesystem iteration order
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("command", COMMANDS)
def test_output_does_not_depend_on_file_creation_order(tmp_path, command):
    """The same bytes created in the opposite order produce the same output.

    Both halves of the output are compared: what lands on disk, and the
    envelope, which is where an order-sensitive *listing* -- ``considered``,
    ``scanned``, ``modules`` -- would show up.
    """
    forward = build(tmp_path / "forward")
    backward = build(tmp_path / "backward", reverse=True)

    forward_envelope = invoke(forward, command).to_json()
    backward_envelope = invoke(backward, command).to_json()

    assert forward_envelope == backward_envelope
    assert_same_image(image(forward.root), image(backward.root))


@pytest.mark.parametrize("command", COMMANDS)
def test_a_reversed_directory_listing_changes_nothing(tmp_path, monkeypatch, command):
    """Every directory listing is sorted, so reversing ``iterdir`` is a no-op.

    Creating the tree in the opposite order is not by itself proof -- a
    filesystem is free to hand back the same order either way. Reversing every
    listing the tool takes *is* proof, and it fails loudly the moment a listing
    somewhere stops being sorted.
    """
    root = tmp_path / "workspace"
    workspace = build(root)
    expected_envelope = invoke(workspace, command).to_json()
    expected = image(root)

    shutil.rmtree(str(root))
    workspace = build(root)
    original = Path.iterdir
    with monkeypatch.context() as patch:
        # Reversing what the *filesystem* returned, rather than sorting in
        # reverse: a reverse sort can coincide with the natural order and let an
        # unsorted listing through, and one that coincides proves nothing.
        patch.setattr(Path, "iterdir", lambda self: iter(list(original(self))[::-1]))
        actual_envelope = invoke(workspace, command).to_json()

    assert actual_envelope == expected_envelope
    assert_same_image(expected, image(root))


# ---------------------------------------------------------------------------
# Emitted YAML: schema key order, fixed indentation, no wrapping
# ---------------------------------------------------------------------------


def _pointer(root, ref):
    if not isinstance(ref, str) or not ref.startswith("#/"):
        return None
    node = root
    for token in ref[2:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, dict) or token not in node:
            return None
        node = node[token]
    return node


def _deref(node, root):
    for _ in range(8):
        if isinstance(node, dict) and "$ref" in node:
            node = _pointer(root, node["$ref"])
            continue
        return node
    return node


def declared_key_order(schema, instance):
    """The property order ``schema`` declares for ``instance``.

    Written here rather than borrowed from ``core.order_by_schema``: an
    expectation computed by the code under test asserts nothing.
    """
    node = _deref(schema, schema) or {}
    for branch in node.get("oneOf") or node.get("anyOf") or []:
        candidate = _deref(branch, schema) or {}
        required = candidate.get("required") or []
        if required and all(key in instance for key in required):
            node = candidate
            break
    return list((node.get("properties") or {}).keys())


def top_level_keys(text):
    """An emitted YAML document's mapping keys, in the order they were written.

    Block sequences are emitted at their parent's indent, so a ``- wave: 1``
    line sits in column 0 without being a top-level key; it belongs to the key
    above it and is skipped.
    """
    return [
        line.partition(":")[0]
        for line in text.split("\n")
        if line
        and not line[0].isspace()
        and ":" in line
        and not line.startswith("#")
        and not line.startswith("-")
    ]


@pytest.fixture
def emitted(fixture):
    """A workspace whose every schema-ordered file was written by the tool."""
    result = invoke(fixture, "spec.catalog-emit")
    assert result.ok, [item.render() for item in result.diagnostics]
    return fixture


@pytest.mark.parametrize("relative,kind", SCHEMA_ORDERED)
def test_emitted_yaml_follows_schema_key_order(emitted, relative, kind):
    text = emitted.path(relative).read_text(encoding="utf-8")
    declared = declared_key_order(core.load_schema(kind), core.load_instance(emitted.path(relative)))
    written = top_level_keys(text)

    assert written, relative
    assert set(written) <= set(declared), relative
    assert written == [key for key in declared if key in written], relative


@pytest.mark.parametrize("relative,kind", SCHEMA_ORDERED)
def test_schema_key_order_is_neither_alphabetical_nor_insertion_order(emitted, relative, kind):
    """Otherwise "follows schema order" would be satisfiable by sorting."""
    declared = declared_key_order(core.load_schema(kind), core.load_instance(emitted.path(relative)))
    written = top_level_keys(emitted.path(relative).read_text(encoding="utf-8"))
    assert sorted(declared) != declared, "%s: nothing to distinguish" % relative
    assert written != sorted(written), relative


@pytest.mark.parametrize("relative,kind", SCHEMA_ORDERED)
def test_emitted_yaml_is_evenly_indented_and_never_wrapped(emitted, relative, kind):
    text = emitted.path(relative).read_text(encoding="utf-8")
    for line in text.split("\n"):
        indent = len(line) - len(line.lstrip(" "))
        assert indent % 2 == 0, line
    # A wrapped line would not survive a re-parse into the same document.
    assert core.parse_yaml(text, relative) == core.load_instance(emitted.path(relative))


@pytest.mark.parametrize("relative,kind", SCHEMA_ORDERED)
def test_emitted_yaml_is_already_its_own_canonical_form(emitted, relative, kind):
    path = emitted.path(relative)
    text = path.read_text(encoding="utf-8")
    assert core.dump_yaml(core.load_yaml(path, relative), core.load_schema(kind)) == text


# ---------------------------------------------------------------------------
# The injected clock is the only clock, and the environment is not an input
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("command", IDEMPOTENT_EMITTERS)
def test_the_injected_clock_is_the_only_thing_that_moves(tmp_path, command):
    """Same bytes, a different ``--now``: only dates differ, and only that date."""
    other = "2027-06-30"
    root = tmp_path / "workspace"

    workspace = build(root)
    invoke(workspace, command)
    first = image(root)

    shutil.rmtree(str(root))
    workspace = build(root)
    invoke(workspace, command, now=other)
    second = image(root)

    assert sorted(first) == sorted(second)
    for relative in sorted(first):
        if first[relative] == second[relative]:
            continue
        assert other.encode("utf-8") in second[relative], relative
        assert first[relative].replace(NOW.encode(), other.encode()) == second[relative], relative


def test_the_mnemonic_candidate_is_a_pure_function_of_the_title():
    """The property ``MMIGRATE-TESTING.md`` cites: same ``<Title>``, same candidate.

    ``mmigrate`` appends this candidate verbatim, so a derivation that varied
    between runs would let two sweeps disagree about the same title. Asserted
    on :func:`req.derive_mnemonic` directly -- it takes no workspace, so there
    is no fixture to build and nothing but the title to vary.
    """
    titles = [
        "Recover a specification from code that never had one",
        "Capture why before how",
        "Trust that tracked artifacts still match their own rules",
        "!!!",
        "",
    ]
    first = [req.derive_mnemonic(title) for title in titles]
    second = [req.derive_mnemonic(title) for title in titles]
    assert first == second
    # Re-deriving from the candidate's own source, in a different order, is
    # still the same answer -- nothing accumulates between calls.
    assert [req.derive_mnemonic(title) for title in reversed(titles)] == list(reversed(first))


def test_the_mnemonic_derivation_reads_no_clock_and_no_environment(monkeypatch):
    """No wall clock, no environment, no filesystem -- only the title."""
    monkeypatch.setenv("LC_ALL", "tr_TR.UTF-8")
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", "/nowhere")
    title = "Mechanical work should not cost model effort"
    assert req.derive_mnemonic(title) == "mechanical-work-cost-model"


def test_no_group_module_reads_a_clock_of_its_own():
    """``core.system_instant`` is the package's single wall-clock read."""
    tools_dir = Path(core.__file__).resolve().parent
    for path in sorted(tools_dir.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        if path.name == "core.py":
            assert source.count("date.today()") == 1, path.name
            continue
        for forbidden in ("date.today", "datetime.now", "time.time", "random.", "uuid"):
            assert forbidden not in source, "%s: %s" % (path.name, forbidden)


def test_no_case_in_this_suite_reads_the_wall_clock(fixture):
    """``--now`` is injected everywhere: the emission carries the pinned date."""
    assert fixture.args().now == NOW
    invoke(fixture, "change.emit")
    written = fixture.path("context/demo/changes/CHANGE-009-emitted-here.md")
    assert "<!-- date: %s -->\n" % NOW in written.read_text(encoding="utf-8")


def _environment_names(source):
    """Every environment variable name a module reads."""
    names = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute):
            if node.value.attr == "environ":
                index = node.slice
                if isinstance(index, ast.Constant) and isinstance(index.value, str):
                    names.add(index.value)
                else:
                    names.add("<computed>")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in ("getenv", "get") and node.args:
                target = node.func.value
                is_environ = (
                    isinstance(target, ast.Attribute) and target.attr == "environ"
                ) or (isinstance(target, ast.Name) and target.id == "os")
                if is_environ and node.func.attr == "getenv":
                    first = node.args[0]
                    names.add(first.value if isinstance(first, ast.Constant) else "<computed>")
                elif isinstance(target, ast.Attribute) and target.attr == "environ":
                    first = node.args[0]
                    names.add(first.value if isinstance(first, ast.Constant) else "<computed>")
    return names


def test_the_environment_is_not_an_input():
    """No variable but ``CLAUDE_PLUGIN_ROOT`` may change what a command emits."""
    tools_dir = Path(core.__file__).resolve().parent
    read = set()
    for path in sorted(tools_dir.glob("*.py")):
        read |= _environment_names(path.read_text(encoding="utf-8"))
    assert read <= {"CLAUDE_PLUGIN_ROOT"}, sorted(read)
