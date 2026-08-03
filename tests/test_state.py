"""The ``state`` command group.

Establishes what TOOLS-TESTING.md requires of it -- each verb changes exactly
the intended field, a write that would fail schema validation is refused **and
the file on disk is unchanged**, and ``set-plan`` leaves ``state.yaml`` and the
ledger agreeing -- plus the guarantees the story adds around them: another
plan's ledger entry stays byte-identical, the run counter goes ``0 -> 1`` on a
freshly emitted plan and is never reset by another verb, an attempt recorded
against a plan whose counter is still ``0`` is refused, ``--status pending``
combined with ``--attempt`` is refused rather than guessed at, and every verb is
deterministic under a pinned ``--now``.

Every fixture is a synthetic workspace in ``tmp_path`` seeded by ``plan emit``;
the group is imported and called directly, never through ``argv``; and the file
comparisons that matter are on **bytes**, because "nothing was persisted" is a
claim about the file, not about the parse of it.
"""

import hashlib
import io
import json

import pytest

from conftest import NOW, SyntheticWorkspace
from tools import core, plan, state

#: The plan emitted for every test, and a second one that must never be touched.
PLAN_ID = "003-demo-plan"
OTHER_PLAN_ID = "004-other-plan"

STORY = "01-01-demo-ALPHA"
ABSENT_STORY = "09-09-demo-ZETA"

BRANCH = "mexec/003-demo-plan/01-01-demo-ALPHA/r1/1"
SECOND_BRANCH = "mexec/003-demo-plan/01-01-demo-ALPHA/r1/2"
WORKTREE = "repos/demo/mexec-01-01-demo-ALPHA-r1-a1"

#: The plan is emitted at ``EARLIER`` and acted on at ``NOW``, so a verb that
#: restamps ``updated`` is visible in a diff instead of hiding behind one date.
EARLIER = "2026-01-01"

REPORT = "context/project/out/003-demo-plan/mverify-report.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _draft(*story_ids):
    stories = {}
    for story_id in story_ids:
        stories[story_id] = {
            "repo": "demo",
            "module": "ALPHA",
            "wave": 1,
            "prerequisites": [],
            "target_paths": ["src/alpha.py"],
            "validation": {"post_story": [{"kind": "prose", "description": "it holds"}]},
        }
    return {"stories": stories}


def _emit(workspace, plan_id=PLAN_ID, story_ids=(STORY,), now=EARLIER):
    """Seed a plan directory, its ``state.yaml``, and its ledger entry."""
    result = plan.run(
        workspace.args(
            verb="emit",
            plan_id=plan_id,
            stdin=io.StringIO(json.dumps(_draft(*story_ids))),
            now=now,
        ),
        workspace.ws,
    )
    assert result.ok, [item.render() for item in result.diagnostics]
    return result


def _run(workspace, **fields):
    """Call the group directly and return ``(result, exit_code)``."""
    fields.setdefault("now", NOW)
    result = state.run(workspace.args(**fields), workspace.ws)
    return result, core.exit_code(result)


def _state_path(workspace, plan_id=PLAN_ID):
    return workspace.path("context/project/plans/%s/state.yaml" % plan_id)


def _ledger_path(workspace):
    return workspace.path("context/project/state.yaml")


def _load(path):
    return core.load_yaml(path, str(path))


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _codes(result):
    return [item.code for item in result.diagnostics]


def _changed(before, after, path=()):
    """Every leaf path whose value differs between two loaded documents."""
    if isinstance(before, dict) and isinstance(after, dict):
        differences = set()
        for key in set(before) | set(after):
            differences |= _changed(before.get(key), after.get(key), path + (str(key),))
        return differences
    return set() if before == after else {"/".join(path)}


def _entry_block(text, plan_id):
    """The raw lines one ledger entry occupies, for a byte comparison."""
    block = []
    capturing = False
    for line in text.splitlines(keepends=True):
        if line.startswith("  %s:" % plan_id):
            capturing = True
            block.append(line)
            continue
        if capturing:
            if line.startswith("    "):
                block.append(line)
                continue
            break
    return "".join(block)


@pytest.fixture
def emitted(workspace):
    """A workspace holding one freshly emitted plan."""
    _emit(workspace)
    return workspace


@pytest.fixture
def started(emitted):
    """The same workspace with the run counter incremented once (``run: 1``)."""
    result, code = _run(emitted, verb="run-increment", plan_id=PLAN_ID)
    assert code == 0 and result.data["run"] == 1
    return emitted


# ---------------------------------------------------------------------------
# run-increment -- the counter is read from the file, never generated
# ---------------------------------------------------------------------------


def test_run_increment_goes_zero_to_one_on_a_freshly_emitted_plan(emitted):
    assert _load(_state_path(emitted))["run"] == 0
    result, code = _run(emitted, verb="run-increment", plan_id=PLAN_ID)
    assert code == 0 and result.ok
    assert result.data["run"] == 1
    assert result.data["previous"] == 0
    assert _load(_state_path(emitted))["run"] == 1


def test_run_increment_changes_only_the_counter_and_the_write_stamp(emitted):
    before = _load(_state_path(emitted))
    _run(emitted, verb="run-increment", plan_id=PLAN_ID)
    after = _load(_state_path(emitted))
    assert _changed(before, after) == {"run", "updated"}
    assert after["updated"] == NOW


def test_run_increment_adds_one_to_whatever_the_file_holds(emitted):
    for expected in (1, 2, 3):
        result, code = _run(emitted, verb="run-increment", plan_id=PLAN_ID)
        assert code == 0
        assert result.data["run"] == expected
        assert _load(_state_path(emitted))["run"] == expected


def test_run_increment_reports_a_missing_state_file(workspace):
    result, code = _run(workspace, verb="run-increment", plan_id=PLAN_ID)
    assert code == 1 and not result.ok
    assert _codes(result) == [core.E_NO_SUCH_FILE]


# ---------------------------------------------------------------------------
# set-plan -- state.yaml and the ledger, together or not at all
# ---------------------------------------------------------------------------


def test_set_plan_leaves_state_and_ledger_agreeing(emitted):
    result, code = _run(emitted, verb="set-plan", plan_id=PLAN_ID, status="in-progress")
    assert code == 0 and result.ok
    assert result.data["written"] == [
        "context/project/plans/%s/state.yaml" % PLAN_ID,
        "context/project/state.yaml",
    ]

    plan_state = _load(_state_path(emitted))
    ledger = _load(_ledger_path(emitted))
    assert plan_state["status"] == "in-progress"
    assert ledger["plans"][PLAN_ID]["status"] == "in-progress"
    assert core.validate_against(core.load_schema("plan-state"), plan_state) == []
    assert core.validate_against(core.load_schema("project-state"), ledger) == []


def test_set_plan_changes_exactly_the_status_in_both_files(emitted):
    before_state = _load(_state_path(emitted))
    before_ledger = _load(_ledger_path(emitted))
    _run(emitted, verb="set-plan", plan_id=PLAN_ID, status="applied")
    after_state = _load(_state_path(emitted))
    after_ledger = _load(_ledger_path(emitted))

    assert _changed(before_state, after_state) == {"status", "updated"}
    assert _changed(before_ledger, after_ledger) == {
        "updated",
        "plans/%s/status" % PLAN_ID,
        "plans/%s/updated" % PLAN_ID,
    }


def test_set_plan_leaves_another_plans_ledger_entry_byte_identical(workspace):
    _emit(workspace)
    _emit(workspace, plan_id=OTHER_PLAN_ID)
    before = _ledger_path(workspace).read_text(encoding="utf-8")
    other_before = _entry_block(before, OTHER_PLAN_ID)
    assert other_before  # the comparison would be vacuous otherwise

    result, code = _run(workspace, verb="set-plan", plan_id=PLAN_ID, status="failed")
    assert code == 0 and result.ok

    after = _ledger_path(workspace).read_text(encoding="utf-8")
    assert _entry_block(after, OTHER_PLAN_ID) == other_before
    assert _load(_ledger_path(workspace))["plans"][OTHER_PLAN_ID]["status"] == "pending"


def test_set_plan_refuses_a_plan_the_ledger_does_not_carry(emitted):
    ledger = _load(_ledger_path(emitted))
    del ledger["plans"][PLAN_ID]
    ledger["plans"][OTHER_PLAN_ID] = {
        "status": "pending",
        "plan_dir": "context/project/plans/%s" % OTHER_PLAN_ID,
    }
    emitted.write("context/project/state.yaml", core.dump_yaml(ledger))
    state_digest = _digest(_state_path(emitted))
    ledger_digest = _digest(_ledger_path(emitted))

    result, code = _run(emitted, verb="set-plan", plan_id=PLAN_ID, status="applied")
    assert code == 1 and not result.ok
    assert _codes(result) == [core.E_NOT_FOUND]
    assert _digest(_state_path(emitted)) == state_digest
    assert _digest(_ledger_path(emitted)) == ledger_digest


# ---------------------------------------------------------------------------
# set-story -- status, attempts, and the two fields the CLI does not carry
# ---------------------------------------------------------------------------


def test_set_story_status_changes_only_that_story(emitted):
    before = _load(_state_path(emitted))
    result, code = _run(
        emitted, verb="set-story", plan_id=PLAN_ID, story_id=STORY, status="in-progress"
    )
    assert code == 0 and result.ok
    after = _load(_state_path(emitted))
    assert _changed(before, after) == {"stories/%s/status" % STORY, "updated"}
    assert after["stories"][STORY]["status"] == "in-progress"


def test_set_story_records_an_attempt_with_run_and_result_derived(started):
    result, code = _run(
        started,
        verb="set-story",
        plan_id=PLAN_ID,
        story_id=STORY,
        status="in-progress",
        attempt=1,
        branch=BRANCH,
        worktree=WORKTREE,
    )
    assert code == 0 and result.ok

    story = _load(_state_path(started))["stories"][STORY]
    assert story["attempts"] == [
        {
            "run": 1,
            "attempt": 1,
            "worktree": WORKTREE,
            "branch": BRANCH,
            "result": "in-progress",
        }
    ]
    # run comes from the file's top-level counter, result from --status, and
    # both are emitted in schema key order.
    assert list(story["attempts"][0]) == ["run", "attempt", "worktree", "branch", "result"]
    assert story["retries"] == 1
    assert story["status"] == "in-progress"


def test_set_story_updates_the_matching_attempt_in_place(started):
    _run(
        started,
        verb="set-story",
        plan_id=PLAN_ID,
        story_id=STORY,
        status="in-progress",
        attempt=1,
        branch=BRANCH,
        worktree=WORKTREE,
    )
    result, code = _run(
        started,
        verb="set-story",
        plan_id=PLAN_ID,
        story_id=STORY,
        status="applied",
        attempt=1,
    )
    assert code == 0 and result.ok

    story = _load(_state_path(started))["stories"][STORY]
    assert len(story["attempts"]) == 1
    assert story["attempts"][0]["result"] == "applied"
    assert story["attempts"][0]["branch"] == BRANCH
    assert story["attempts"][0]["worktree"] == WORKTREE
    assert story["retries"] == 1


def test_set_story_keeps_retries_consistent_with_the_attempts_recorded(started):
    _run(
        started,
        verb="set-story",
        plan_id=PLAN_ID,
        story_id=STORY,
        status="failed",
        attempt=1,
        branch=BRANCH,
    )
    result, code = _run(
        started,
        verb="set-story",
        plan_id=PLAN_ID,
        story_id=STORY,
        status="applied",
        attempt=2,
        branch=SECOND_BRANCH,
    )
    assert code == 0 and result.ok

    story = _load(_state_path(started))["stories"][STORY]
    assert [entry["attempt"] for entry in story["attempts"]] == [1, 2]
    assert [entry["result"] for entry in story["attempts"]] == ["failed", "applied"]
    assert story["retries"] == 2


def test_set_story_refuses_an_attempt_while_the_run_counter_is_zero(emitted):
    digest = _digest(_state_path(emitted))
    result, code = _run(
        emitted,
        verb="set-story",
        plan_id=PLAN_ID,
        story_id=STORY,
        status="in-progress",
        attempt=1,
        branch=BRANCH,
    )
    assert code == 1 and not result.ok
    assert _codes(result) == [core.E_INVALID_STATE]
    assert _digest(_state_path(emitted)) == digest


def test_set_story_refuses_pending_combined_with_an_attempt(started):
    digest = _digest(_state_path(started))
    result, code = _run(
        started,
        verb="set-story",
        plan_id=PLAN_ID,
        story_id=STORY,
        status="pending",
        attempt=1,
        branch=BRANCH,
    )
    assert code == 2 and not result.ok
    assert _codes(result) == [core.E_USAGE]
    assert _digest(_state_path(started)) == digest


def test_set_story_refuses_attempt_refs_without_an_attempt_number(started):
    digest = _digest(_state_path(started))
    result, code = _run(
        started,
        verb="set-story",
        plan_id=PLAN_ID,
        story_id=STORY,
        status="applied",
        branch=BRANCH,
    )
    assert code == 2 and not result.ok
    assert _codes(result) == [core.E_USAGE]
    assert _digest(_state_path(started)) == digest


def test_set_story_refuses_a_new_attempt_with_no_branch(started):
    digest = _digest(_state_path(started))
    result, code = _run(
        started,
        verb="set-story",
        plan_id=PLAN_ID,
        story_id=STORY,
        status="applied",
        attempt=1,
    )
    assert code == 2 and not result.ok
    assert _codes(result) == [core.E_USAGE]
    assert _digest(_state_path(started)) == digest


def test_set_story_reports_a_story_the_state_file_does_not_hold(started):
    digest = _digest(_state_path(started))
    result, code = _run(
        started, verb="set-story", plan_id=PLAN_ID, story_id=ABSENT_STORY, status="applied"
    )
    assert code == 1 and not result.ok
    assert _codes(result) == [core.E_NOT_FOUND]
    assert _digest(_state_path(started)) == digest


# ---------------------------------------------------------------------------
# The refusal gate -- validated on the serialisation, before any byte lands
# ---------------------------------------------------------------------------


def test_a_write_that_would_fail_the_schema_is_refused_and_the_file_is_unchanged(started):
    """``--attempt 4`` parses but fails ``attempt.attempt``'s ``maximum: 3``.

    An out-of-enum ``--status`` would be an argparse usage error, rejected
    before the validate-then-write gate is ever reached; this value gets all the
    way to the gate, which is the thing under test.
    """
    digest = _digest(_state_path(started))
    result, code = _run(
        started,
        verb="set-story",
        plan_id=PLAN_ID,
        story_id=STORY,
        status="applied",
        attempt=4,
        branch=BRANCH,
    )
    assert code == 1 and not result.ok
    assert _codes(result) == [core.E_SCHEMA_INVALID]
    assert result.data["written"] == []
    assert _digest(_state_path(started)) == digest


def test_a_refused_conformance_write_leaves_the_file_unchanged(emitted):
    digest = _digest(_state_path(emitted))
    result, code = _run(
        emitted,
        verb="conformance",
        plan_id=PLAN_ID,
        status="clean",
        report=REPORT,
        findings=-1,  # conformance.findings has minimum: 0
    )
    assert code == 1 and not result.ok
    assert _codes(result) == [core.E_SCHEMA_INVALID]
    assert _digest(_state_path(emitted)) == digest


def test_a_refused_telemetry_write_leaves_the_file_unchanged(emitted):
    digest = _digest(_state_path(emitted))
    result, code = _run(
        emitted,
        verb="telemetry",
        plan_id=PLAN_ID,
        cost="free",  # telemetry.cost_usd is number|null
        tokens=100,
        wall_clock=1.0,
    )
    assert code == 1 and not result.ok
    assert _codes(result) == [core.E_SCHEMA_INVALID]
    assert _digest(_state_path(emitted)) == digest


def test_a_refused_set_plan_writes_neither_file(emitted):
    """Both documents are validated before either is written."""
    plan_state = _load(_state_path(emitted))
    plan_state["stories"][STORY]["retries"] = 9  # maximum: 3
    emitted.write(
        "context/project/plans/%s/state.yaml" % PLAN_ID, core.dump_yaml(plan_state)
    )
    state_digest = _digest(_state_path(emitted))
    ledger_digest = _digest(_ledger_path(emitted))

    result, code = _run(emitted, verb="set-plan", plan_id=PLAN_ID, status="applied")
    assert code == 1 and not result.ok
    assert _codes(result) == [core.E_SCHEMA_INVALID]
    assert _digest(_state_path(emitted)) == state_digest
    assert _digest(_ledger_path(emitted)) == ledger_digest


def test_no_temporary_file_survives_a_write_or_a_refusal(started):
    _run(started, verb="set-plan", plan_id=PLAN_ID, status="in-progress")
    _run(
        started,
        verb="set-story",
        plan_id=PLAN_ID,
        story_id=STORY,
        status="applied",
        attempt=4,
        branch=BRANCH,
    )
    plan_dir = started.path("context/project/plans/%s" % PLAN_ID)
    assert sorted(entry.name for entry in plan_dir.iterdir()) == ["plan.yaml", "state.yaml"]
    project_dir = started.path("context/project")
    assert not [entry for entry in project_dir.iterdir() if entry.name.endswith(state.TEMP_SUFFIX)]


# ---------------------------------------------------------------------------
# conformance / telemetry
# ---------------------------------------------------------------------------


def test_conformance_writes_only_its_block(emitted):
    before = _load(_state_path(emitted))
    result, code = _run(
        emitted, verb="conformance", plan_id=PLAN_ID, status="drift", report=REPORT, findings=3
    )
    assert code == 0 and result.ok
    after = _load(_state_path(emitted))
    assert _changed(before, after) == {"conformance", "updated"}
    assert after["conformance"] == {"status": "drift", "report": REPORT, "findings": 3}


def test_telemetry_writes_only_its_block(emitted):
    before = _load(_state_path(emitted))
    result, code = _run(
        emitted, verb="telemetry", plan_id=PLAN_ID, cost=1.25, tokens=4096, wall_clock=42.5
    )
    assert code == 0 and result.ok
    after = _load(_state_path(emitted))
    assert _changed(before, after) == {"telemetry", "updated"}
    assert after["telemetry"] == {"cost_usd": 1.25, "tokens": 4096, "wall_clock_s": 42.5}


def test_the_run_counter_is_not_reset_by_any_other_verb(started):
    _run(started, verb="set-plan", plan_id=PLAN_ID, status="in-progress")
    _run(
        started,
        verb="set-story",
        plan_id=PLAN_ID,
        story_id=STORY,
        status="applied",
        attempt=1,
        branch=BRANCH,
    )
    _run(
        started, verb="conformance", plan_id=PLAN_ID, status="clean", report=REPORT, findings=0
    )
    _run(started, verb="telemetry", plan_id=PLAN_ID, cost=0.5, tokens=10, wall_clock=2.0)
    assert _load(_state_path(started))["run"] == 1


# ---------------------------------------------------------------------------
# Identifiers -- rejected, never sanitized
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("story_id", ["bad id", "../escape", "a/b"])
def test_a_bad_story_id_is_rejected_not_sanitized(started, story_id):
    digest = _digest(_state_path(started))
    result, code = _run(
        started, verb="set-story", plan_id=PLAN_ID, story_id=story_id, status="applied"
    )
    assert code == 2 and not result.ok
    assert _codes(result) == [core.E_BAD_IDENT]
    assert _digest(_state_path(started)) == digest


def test_a_story_id_of_the_wrong_shape_is_reported_not_coerced(started):
    result, code = _run(
        started, verb="set-story", plan_id=PLAN_ID, story_id="ALPHA", status="applied"
    )
    assert not result.ok
    assert _codes(result) == [core.E_UNPARSED_NAME]


@pytest.mark.parametrize(
    "verb, extra",
    [
        ("run-increment", {}),
        ("set-plan", {"status": "applied"}),
        ("conformance", {"status": "clean", "report": REPORT, "findings": 0}),
        ("telemetry", {"cost": 1.0, "tokens": 1, "wall_clock": 1.0}),
    ],
)
def test_a_bad_plan_id_is_rejected_by_every_verb(emitted, verb, extra):
    result, code = _run(emitted, verb=verb, plan_id="Not A Plan", **extra)
    assert code == 2 and not result.ok
    assert _codes(result) == [core.E_BAD_IDENT]


def test_a_state_file_declaring_another_plan_is_refused(emitted):
    plan_state = _load(_state_path(emitted))
    plan_state["plan_id"] = OTHER_PLAN_ID
    emitted.write(
        "context/project/plans/%s/state.yaml" % PLAN_ID, core.dump_yaml(plan_state)
    )
    digest = _digest(_state_path(emitted))
    result, code = _run(emitted, verb="run-increment", plan_id=PLAN_ID)
    assert code == 1 and not result.ok
    assert _codes(result) == [core.E_INVALID_STATE]
    assert _digest(_state_path(emitted)) == digest


def test_a_missing_verb_is_a_usage_diagnostic(emitted):
    result, code = _run(emitted, verb=None)
    assert code == 2 and not result.ok
    assert _codes(result) == [core.E_USAGE]


def test_an_unknown_verb_is_a_usage_diagnostic(emitted):
    result, code = _run(emitted, verb="reset")
    assert code == 2 and not result.ok
    assert _codes(result) == [core.E_USAGE]


# ---------------------------------------------------------------------------
# Determinism -- same bytes in, same --now, same bytes out
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fields",
    [
        {"verb": "run-increment", "plan_id": PLAN_ID},
        {"verb": "set-plan", "plan_id": PLAN_ID, "status": "in-progress"},
        {
            "verb": "set-story",
            "plan_id": PLAN_ID,
            "story_id": STORY,
            "status": "applied",
            "attempt": 1,
            "branch": BRANCH,
            "worktree": WORKTREE,
        },
        {
            "verb": "conformance",
            "plan_id": PLAN_ID,
            "status": "drift",
            "report": REPORT,
            "findings": 2,
        },
        {"verb": "telemetry", "plan_id": PLAN_ID, "cost": 1.25, "tokens": 4096, "wall_clock": 42.5},
    ],
    ids=["run-increment", "set-plan", "set-story", "conformance", "telemetry"],
)
def test_every_verb_is_deterministic_under_a_pinned_now(tmp_path, fields):
    outputs = []
    for name in ("first", "second"):
        workspace = SyntheticWorkspace(tmp_path / name)
        _emit(workspace)
        _run(workspace, verb="run-increment", plan_id=PLAN_ID)
        result, code = _run(workspace, **fields)
        assert code == 0, [item.render() for item in result.diagnostics]
        outputs.append(
            (_state_path(workspace).read_bytes(), _ledger_path(workspace).read_bytes())
        )
    assert outputs[0] == outputs[1]


def test_re_applying_the_same_change_leaves_the_files_byte_identical(started):
    _run(started, verb="set-plan", plan_id=PLAN_ID, status="applied")
    state_bytes = _state_path(started).read_bytes()
    ledger_bytes = _ledger_path(started).read_bytes()
    _run(started, verb="set-plan", plan_id=PLAN_ID, status="applied")
    assert _state_path(started).read_bytes() == state_bytes
    assert _ledger_path(started).read_bytes() == ledger_bytes
