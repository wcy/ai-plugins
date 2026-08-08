"""The delivered surface: every verb, invoked through ``mc.py`` as a subprocess.

``TOOLS-TESTING.md`` §"Delivered Surface" names this module's delivered surface
as the **``mc.py`` command line** -- an exit code, stdout, and the files a verb
writes. That is what every skill invokes and the only thing a caller can
observe; the Python package beneath it is an internal component no consumer
imports.

The rest of the suite calls each group in process. That is the right tool for
branch coverage of a function's internals and it cannot reach the seam this file
exists to cover: an unregistered subcommand, a flag that never reaches its
handler, a diagnostic written to the wrong stream, a non-zero path that exits
``0``. Every one of those leaves the in-process tests green, and every one of
them breaks every caller.

**The verb list is derived, never authored here.** :data:`COMMANDS` comes from
``mc.py``'s own argument declaration -- ``mc.GROUPS`` plus each group module's
``register()`` -- which is the CLI surface ``TOOLS-INTERFACE.md`` publishes. A
verb added to the tool without a case in :func:`cases` fails
:func:`test_every_verb_the_cli_declares_is_exercised_through_it`, so coverage
cannot be satisfied by a list this file supplies to itself. Only the arguments
each verb is given are authored, and the expectation attached to them.

Every fixture is synthetic and lives in ``tmp_path``, every invocation injects
``--now``, and nothing here runs ``git``, reaches a network, or invokes an
agent. The workspace's plan is produced by the tool itself at :data:`SEED_NOW`,
so what the cases run against is what a real run leaves behind.
"""

import argparse
import json
import subprocess
import sys

import pytest

from conftest import MC, NOW, SyntheticWorkspace
from tools import core, mc

#: The clock the *fixture* is built at, deliberately different from ``NOW``.
SEED_NOW = "2026-01-01"

PLAN_ID = "001-alpha-retry"
SECOND_PLAN_ID = "005-second-plan"
FIRST_STORY = "01-01-demo-ALPHA"
LAST_STORY = "02-01-demo-BETA"

CATALOG_REL = "context/demo/spec/CATALOG.yaml"
LISTING_REL = "listings/worktrees.txt"
REPORT_REL = "context/project/out/%s/mverify-report.json" % PLAN_ID
BRANCH = "mexec/%s/%s/r1/1" % (PLAN_ID, LAST_STORY)
WORKTREE = "repos/demo/%s-r1-1" % LAST_STORY


PENDING_CHANGE = "context/demo/changes/CHANGE-001-alpha-retry.md"
APPLIED_CHANGE = "context/demo/changes/CHANGE-002-beta-timeout.md"
NEW_CHANGE = "context/demo/changes/CHANGE-009-emitted-here.md"


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


CATALOG = """\
version: 1
repo: demo
shared_interfaces:
  - EVENT-BUS
layers:
  L0-foundation:
    modules: [COMMON]
  L1-core:
    modules: [ALPHA]
  L2-services:
    modules: [BETA]
modules:
  COMMON:
    layer: L0-foundation
    files:
      - path: context/demo/spec/COMMON/COMMON-OVERVIEW.md
        facet: overview
  ALPHA:
    layer: L1-core
    files:
      - path: context/demo/spec/ALPHA/ALPHA-OVERVIEW.md
        facet: overview
      - path: context/demo/spec/ALPHA/ALPHA-INTERFACE.md
        facet: interface
        exports: [alphaClient]
  BETA:
    layer: L2-services
    files:
      - path: context/demo/spec/BETA/BETA-OVERVIEW.md
        facet: overview
"""

#: Caller-supplied ``git worktree list --porcelain`` output. Supplied as data:
#: this suite never runs git, and neither does the verb that reads it.
WORKTREE_LISTING = """\
worktree repos/demo/main
HEAD 1111111111111111111111111111111111111111
branch refs/heads/main

worktree %s
HEAD 2222222222222222222222222222222222222222
branch refs/heads/%s
""" % (WORKTREE, BRANCH)

CONFORMANCE_REPORT = (
    '{\n  "scope": {\n    "kind": "aggregate"\n  },\n  "findings": [],\n  "clean": true\n}\n'
)

COMMON_OVERVIEW = "context/demo/spec/COMMON/COMMON-OVERVIEW.md"
SHARED_INTERFACE = "context/shared/spec/EVENT-BUS/EVENT-BUS-INTERFACE.md"


def _spec(module, filename, depends=(), trailer=""):
    text = ""
    if depends:
        text += "<!-- depends-on: %s -->\n" % ", ".join(depends)
    text += "\n# %s\n\nBody.\n" % filename[: -len(".md")]
    if trailer:
        text += "\n%s\n" % trailer
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


def layout():
    """Every file the fixture writes, as ``(path, text)`` pairs."""
    return [
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
            trailer="Exports: `alphaClient`.",
        ),
        _spec("BETA", "BETA-OVERVIEW.md", [COMMON_OVERVIEW]),
        (CATALOG_REL, CATALOG),
        _change("001", "alpha-retry", "pending"),
        _change("002", "beta-timeout", "applied"),
        _index("001", "alpha-retry", "pending", [PENDING_CHANGE, APPLIED_CHANGE]),
        (LISTING_REL, WORKTREE_LISTING),
        (REPORT_REL, CONFORMANCE_REPORT),
    ]


def _story(story_id, module, wave):
    return story_id, {
        "repo": "demo",
        "module": module,
        "wave": wave,
        "prerequisites": [] if wave == 1 else [FIRST_STORY],
        "change_file": PENDING_CHANGE,
        "target_paths": ["src/%s.py" % module.lower()],
        "validation": {"post_story": [{"kind": "prose", "description": "it holds"}]},
    }


def draft(project_change="001"):
    """The draft plan graph the fixture's plans are emitted from."""
    return {
        "project_change": project_change,
        "slices": [dict(entry) for entry in SLICE_DRAFTS],
        "stories": dict([_story(FIRST_STORY, "ALPHA", 1), _story(LAST_STORY, "BETA", 2)]),
    }


#: The ``SliceDraft[]`` every draft here carries, and the one ``plan reslice``
#: is exercised with: a single slice holding both stories, so every story lands
#: in exactly one and slice ``00`` spans both layers the plan touches.
SLICE_DRAFTS = [
    {
        "slice": "00",
        "name": "walking skeleton",
        "behavior": "the plan runs end to end",
        "acceptance": [{"kind": "exit-code", "command": "true", "description": "it runs"}],
        "stories": [FIRST_STORY, LAST_STORY],
    }
]


def run(workspace, *argv, stdin=None):
    """Run ``mc.py`` as a subprocess -- the only way this file calls the tool.

    ``conftest``'s helper cannot feed stdin, and two verbs read their draft from
    it, so the runner lives here where the cases that need it are.
    """
    return subprocess.run(
        [sys.executable, str(MC)] + [str(item) for item in argv],
        capture_output=True,
        cwd=str(workspace.root),
        input=None if stdin is None else stdin.encode("utf-8"),
    )


@pytest.fixture
def workspace(tmp_path):
    """A synthetic workspace holding everything every verb needs.

    The plan directory, its state file and the ledger are written by ``mc.py``
    itself -- through the very surface under test -- rather than hand-built, so
    a case never runs against an approximation of what the tool produces.
    """
    ws = SyntheticWorkspace(tmp_path / "workspace")
    for relative, text in layout():
        ws.write(relative, text)
    ws.mkdir("repos/demo")
    completed = run(
        ws, "--workspace", ws.root, "--now", SEED_NOW, "plan", "emit", PLAN_ID,
        stdin=json.dumps(draft()),
    )
    assert completed.returncode == 0, completed.stderr.decode()
    # A started run: an attempt always belongs to one, so `state set-story`
    # cannot be exercised against a plan whose counter is still 0.
    completed = run(
        ws, "--workspace", ws.root, "--now", SEED_NOW, "state", "run-increment", PLAN_ID
    )
    assert completed.returncode == 0, completed.stderr.decode()
    return ws


# One case per verb. Only the arguments are authored -- the list is derived.
# ---------------------------------------------------------------------------


class Case:
    """One CLI invocation and what it must produce.

    ``argv`` is everything after the global options; ``exit_code`` is the
    documented code the invocation must exit with; ``stdout`` is the substrings
    that must appear on stdout, which is what makes the assertion about the
    verb's own output rather than merely about it having run.
    """

    def __init__(self, *argv, exit_code=0, stdout=(), stdin=None):
        self.argv = [str(item) for item in argv]
        self.exit_code = exit_code
        self.stdout = tuple(stdout)
        self.stdin = stdin


def cases():
    """One :class:`Case` per command in :data:`COMMANDS`, freshly built."""
    return {
        "validate": Case(
            "validate", "catalog", CATALOG_REL, stdout=["OK", CATALOG_REL, "(catalog)"]
        ),
        "change.resolve": Case(
            "change", "resolve", "demo", "--slug", "retry-policy",
            stdout=["action: create", "number: 003", "CHANGE-003-retry-policy.md"],
        ),
        "change.index-resolve": Case(
            "change", "index-resolve", "--slug", "retry-policy",
            stdout=["action: create", "number: 002", "PROJECT-CHANGE-002-retry-policy.md"],
        ),
        "change.emit": Case(
            "change", "emit", NEW_CHANGE,
            "--scope", "repo", "--status", "pending", "--title", "Emitted Here",
            "--repo", "demo",
            stdout=[NEW_CHANGE],
        ),
        "change.close": Case(
            "change", "close", PENDING_CHANGE, "--status", "applied",
            stdout=[PENDING_CHANGE, "pending -> applied"],
        ),
        "spec.mode": Case(
            "spec", "mode", "demo", stdout=["mode: UPDATE", "context/demo/spec"]
        ),
        "spec.catalog-emit": Case(
            "spec", "catalog-emit", "demo", stdout=[CATALOG_REL, "written: true"]
        ),
        "spec.consumers": Case(
            "spec", "consumers", "EVENT-BUS", stdout=["interface: EVENT-BUS", "- demo"]
        ),
        "spec.revision": Case("spec", "revision", "EVENT-BUS", stdout=["revision: 1"]),
        "plan.scope": Case(
            "plan", "scope", stdout=["type: incremental", "plan_id: %s" % PLAN_ID]
        ),
        "plan.resolve": Case(
            "plan", "resolve", stdout=["plan_id: %s" % PLAN_ID, "resume_wave: 1"]
        ),
        "plan.story-id": Case(
            "plan", "story-id", "PLAN-%s.md" % LAST_STORY,
            stdout=["story_id: %s" % LAST_STORY],
        ),
        "plan.waves": Case(
            "plan", "waves", "demo", stdout=["wave: 1", "ALPHA", "BETA"]
        ),
        "plan.slices": Case(
            "plan", "slices", PLAN_ID, stdout=["synthesized: false", "slice: 00"]
        ),
        "plan.reslice": Case(
            "plan", "reslice", PLAN_ID,
            stdin=json.dumps(SLICE_DRAFTS),
            stdout=["plan.yaml", "state.yaml"],
        ),
        "plan.emit": Case(
            "plan", "emit", SECOND_PLAN_ID,
            stdin=json.dumps(draft(project_change="002")),
            stdout=["plan.yaml", "state.yaml"],
        ),
        "plan.story-emit": Case(
            "plan", "story-emit", PLAN_ID, LAST_STORY,
            stdout=["PLAN-%s.md" % LAST_STORY, "slice: 00", "last_wave: true"],
        ),
        "plan.shards": Case(
            "plan", "shards", PLAN_ID,
            stdout=["shard: change-conformance", "shard: coupling"],
        ),
        "state.run-increment": Case(
            "state", "run-increment", PLAN_ID, stdout=["run: 2", "previous: 1"]
        ),
        "state.set-plan": Case(
            "state", "set-plan", PLAN_ID, "--status", "in-progress",
            stdout=["status: in-progress", "context/project/state.yaml"],
        ),
        "state.set-story": Case(
            "state", "set-story", PLAN_ID, LAST_STORY,
            "--status", "applied", "--attempt", "1",
            "--branch", BRANCH, "--worktree", WORKTREE,
            stdout=["status: applied", "attempt: 1"],
        ),
        "state.set-slice": Case(
            "state", "set-slice", PLAN_ID, "00",
            "--status", "applied", "--acceptance", "pass", "--outcome", "continue",
            stdout=["slice: 00", "status: applied", "slices_applied: 1"],
        ),
        "state.conformance": Case(
            "state", "conformance", PLAN_ID,
            "--status", "clean", "--report", REPORT_REL, "--findings", "0",
            stdout=["status: clean", REPORT_REL],
        ),
        "state.telemetry": Case(
            "state", "telemetry", PLAN_ID, "--cost", "1.5", "--tokens", "4096",
            stdout=["cost_usd: 1.5", "tokens: 4096", "wall_clock_s: null"],
        ),
        # `--filed 0` on purpose: the declaration a run makes when it left
        # nothing behind, which must still reach stdout as a written block.
        "worktree.names": Case(
            "worktree", "names", PLAN_ID, LAST_STORY, "--run", "1", "--attempt", "1",
            stdout=["mexec/%s/integration" % PLAN_ID, BRANCH, WORKTREE],
        ),
        "worktree.reconcile": Case(
            "worktree", "reconcile", PLAN_ID, "--list-from", LISTING_REL,
            stdout=["verdict: orphan", "verdict: keep"],
        ),
        "check.depends-on": Case(
            "check", "depends-on", "demo", stdout=["check: depends-on", "findings"]
        ),
        "check.coupling": Case(
            "check", "coupling", "demo", stdout=["check: coupling", "findings"]
        ),
        "check.catalog": Case(
            "check", "catalog", "demo", stdout=["check: catalog", "findings"]
        ),
        "check.all": Case(
            "check", "all", "demo",
            stdout=[
                "check: depends-on", "check: coupling", "check: catalog"
            ],
        ),

    }


#: The commands this file has a case for, read off the same table the cases run
#: from -- there is deliberately no second list to keep in step with it.
COVERED = sorted(cases())


def invoke(workspace, command, *extra, json_out=False):
    """Run one command's case through ``mc.py`` and return the completed process."""
    case = cases()[command]
    argv = ["--workspace", workspace.root, "--now", NOW]
    if json_out:
        argv.append("--json")
    return case, run(workspace, *(argv + case.argv + list(extra)), stdin=case.stdin)


def decoded(completed):
    return completed.stdout.decode("utf-8"), completed.stderr.decode("utf-8")


# ---------------------------------------------------------------------------
# Coverage: the list of verbs is the CLI's, not this file's
# ---------------------------------------------------------------------------


def test_every_verb_the_cli_declares_is_exercised_through_it():
    """A verb added to ``mc.py`` with no subprocess case fails right here.

    This is the gap the file exists to close: a verb whose *only* coverage is
    in-process can be miswired at the CLI seam while every test passes.
    """
    assert COVERED == COMMANDS


def test_the_derived_command_list_is_not_empty_and_spans_every_group():
    """Guards the derivation itself: an empty list makes coverage vacuous."""
    assert len(COMMANDS) > len(mc.GROUPS)
    assert sorted({name.partition(".")[0] for name in COMMANDS}) == sorted(mc.GROUPS)


# ---------------------------------------------------------------------------
# Every verb, through the command line: exit code and stdout
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("command", COMMANDS)
def test_the_verb_exits_as_documented_and_writes_its_result_to_stdout(workspace, command):
    case, completed = invoke(workspace, command)
    out, err = decoded(completed)

    assert completed.returncode == case.exit_code, "%s: %s" % (command, err)
    assert out.strip(), "%s wrote nothing to stdout" % command
    for fragment in case.stdout:
        assert fragment in out, "%s: %r missing from stdout:\n%s" % (command, fragment, out)
    # A diagnostic is a `Diagnostic`, never a traceback: TOOLS-IMPLEMENTATION.md
    # §Errors, and the one failure mode a subprocess test can see and an
    # in-process one cannot.
    assert "Traceback" not in err, "%s: %s" % (command, err)


@pytest.mark.parametrize("command", COMMANDS)
def test_the_verb_emits_its_result_envelope_verbatim_under_json(workspace, command):
    """``--json`` puts the ``Result`` on stdout and nothing else there.

    The envelope's ``command`` is what proves the argv reached *that* verb's
    handler rather than another one, and ``ok`` is the documented invariant the
    exit code is derived from.
    """
    case, completed = invoke(workspace, command, json_out=True)
    out, err = decoded(completed)

    assert completed.returncode == case.exit_code, "%s: %s" % (command, err)
    envelope = json.loads(out)
    assert sorted(envelope) == ["command", "data", "diagnostics", "ok"]
    assert envelope["command"] == command
    assert envelope["ok"] is (case.exit_code == 0)
    # "All non-envelope output goes to stderr" is a claim about both streams:
    # the envelope is on stdout, and no second copy of it is on stderr.
    assert not err.strip().startswith("{")
    assert "Traceback" not in err, "%s: %s" % (command, err)


# ---------------------------------------------------------------------------
# The non-zero paths -- each asserted to actually exit non-zero
# ---------------------------------------------------------------------------


def test_a_check_finding_exits_one(workspace):
    """Exit ``1``: the command ran and reported an error diagnostic.

    The dangling ``depends-on`` is introduced here rather than carried by the
    fixture, so the clean case above and this one differ by exactly one edit.
    """
    workspace.write(
        "context/demo/spec/BETA/BETA-OVERVIEW.md",
        "<!-- depends-on: context/demo/spec/GONE/GONE-OVERVIEW.md -->\n\n# BETA-OVERVIEW\n",
    )
    completed = run(
        workspace,
        "--workspace", workspace.root, "--now", NOW, "check", "depends-on", "demo"
    )
    out, err = decoded(completed)

    assert completed.returncode == 1
    assert "GONE-OVERVIEW.md" in out or "GONE-OVERVIEW.md" in err
    assert "Traceback" not in err


def test_a_file_that_fails_its_schema_exits_one(workspace):
    """The other documented exit-``1`` condition: a validation failure."""
    workspace.write("instances/bad-catalog.yaml", "version: 1\nrepo: demo\n")
    completed = run(
        workspace,
        "--workspace", workspace.root, "--now", NOW,
        "validate", "catalog", "instances/bad-catalog.yaml",
    )
    out, _err = decoded(completed)

    assert completed.returncode == 1
    assert "FAIL" in out


def test_a_missing_file_fails_alone_without_aborting_the_batch(workspace):
    """Exit ``1``, and the file that *is* there is still reported ``OK``."""
    completed = run(
        workspace,
        "--workspace", workspace.root, "--now", NOW,
        "validate", "catalog", CATALOG_REL, "context/demo/spec/ABSENT.yaml",
    )
    out, _err = decoded(completed)

    assert completed.returncode == 1
    assert "OK" in out and CATALOG_REL in out


def test_a_missing_verb_is_a_usage_error(workspace):
    """Exit ``2``: a group named with no verb."""
    completed = run(workspace, "--workspace", workspace.root, "--now", NOW, "plan")
    assert completed.returncode == 2


def test_a_bad_identifier_is_refused_and_never_sanitized(workspace):
    """Exit ``2`` and ``E_BAD_IDENT``, with the refused value never rewritten."""
    completed = run(
        workspace,
        "--workspace", workspace.root, "--now", NOW, "plan", "slices", "../escape"
    )
    out, err = decoded(completed)

    assert completed.returncode == 2
    assert core.E_BAD_IDENT in out + err or "escape" in out + err
    assert not workspace.path("context/project/plans").joinpath("escape").exists()


def test_a_path_escaping_the_workspace_is_refused(workspace):
    """Exit ``2`` and ``E_PATH_ESCAPE`` -- ``--workspace`` bounds every path."""
    completed = run(
        workspace,
        "--workspace", workspace.root, "--now", NOW,
        "validate", "catalog", "../outside.yaml",
    )
    assert completed.returncode == 2


def test_an_unknown_group_is_a_usage_error(workspace):
    """Argparse's own refusal is still exit ``2`` at the seam."""
    completed = run(workspace, "--workspace", workspace.root, "nonesuch", "verb")
    assert completed.returncode == 2


# ---------------------------------------------------------------------------
# todo -- the refusals, through the command line
#
# The no-file-left-behind property is a claim about the *filesystem* after a
# refused invocation, so it can only be settled where the invocation is a real
# process: an in-process test that asserted it would be asserting about a
# function's return rather than about what the caller is left holding.
# ---------------------------------------------------------------------------


def _empty_workspace(tmp_path):
    """A workspace with a resolvable change and no ``TODO.md`` at all."""
    ws = SyntheticWorkspace(tmp_path / "empty")
    path, text = _change("001", "alpha-retry", "pending")
    ws.write(path, text)
    return ws












# ---------------------------------------------------------------------------
# check handoff with an open entry present -- through the command line
#
# "It never blocks" is a claim about an **exit code**, which is a property of a
# process. An in-process test can assert what `core.exit_code` computes; only a
# subprocess can assert what the caller who ran `mc.py check handoff` is left
# holding, and that is the surface every skill invokes.
# ---------------------------------------------------------------------------


def _handoff(workspace):
    """``mc.py check handoff`` as a subprocess, decoded."""
    completed = run(
        workspace, "--workspace", workspace.root, "--now", NOW, "check", "handoff"
    )
    out, err = decoded(completed)
    return completed.returncode, out, err












def test_every_non_zero_case_here_is_really_non_zero(workspace):
    """The guard on the guards: an exit-code assertion that never fires proves
    nothing, so the two non-zero families are re-run and their codes pinned."""
    escape = run(
        workspace,
        "--workspace", workspace.root, "--now", NOW, "plan", "slices", "../escape"
    )
    finding = run(
        workspace,
        "--workspace", workspace.root, "--now", NOW,
        "validate", "catalog", "context/demo/spec/ABSENT.yaml",
    )
    assert (escape.returncode, finding.returncode) == (2, 1)
