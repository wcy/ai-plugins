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

import argparse
import hashlib
import inspect
import io
import json
from pathlib import Path

import pytest

from conftest import NOW, SyntheticWorkspace
from tools import core, plan, state

#: The plan emitted for every test, and a second one that must never be touched.
PLAN_ID = "003-demo-plan"
OTHER_PLAN_ID = "004-other-plan"

STORY = "01-01-demo-ALPHA"
ABSENT_STORY = "09-09-demo-ZETA"

#: A second story in the same wave as ``STORY``, and one in a later wave -- the
#: two the wave mapping needs to be observable at all.
SIBLING = "01-02-demo-BETA"
LATER = "02-01-demo-GAMMA"

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


def _draft(*story_ids, waves=None):
    """A draft graph; ``waves`` maps a story id to its wave, default 1."""
    waves = waves or {}
    stories = {}
    for story_id in story_ids:
        module = story_id.rsplit("-", 1)[-1]
        stories[story_id] = {
            "repo": "demo",
            "module": module,
            "wave": waves.get(story_id, 1),
            "prerequisites": [],
            "target_paths": ["src/%s.py" % module.lower()],
            "validation": {"post_story": [{"kind": "prose", "description": "it holds"}]},
        }
    return {"stories": stories}


def _emit(workspace, plan_id=PLAN_ID, story_ids=(STORY,), now=EARLIER, waves=None):
    """Seed a plan directory, its ``state.yaml``, and its ledger entry."""
    result = plan.run(
        workspace.args(
            verb="emit",
            plan_id=plan_id,
            stdin=io.StringIO(json.dumps(_draft(*story_ids, waves=waves))),
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
    """Every leaf path whose value differs between two loaded documents.

    Lists are walked by index, so a wave write reads as ``waves/0/status``
    rather than collapsing the whole block into one opaque ``waves``.
    """
    if isinstance(before, dict) and isinstance(after, dict):
        differences = set()
        for key in set(before) | set(after):
            differences |= _changed(before.get(key), after.get(key), path + (str(key),))
        return differences
    if isinstance(before, list) and isinstance(after, list) and len(before) == len(after):
        differences = set()
        for index, (one, other) in enumerate(zip(before, after)):
            differences |= _changed(one, other, path + (str(index),))
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
def two_waves(workspace):
    """Two stories in wave 1 and one in wave 2 -- enough to see the mapping."""
    _emit(workspace, story_ids=(STORY, SIBLING, LATER), waves={LATER: 2})
    return workspace


def _waves(workspace, plan_id=PLAN_ID):
    """``wave number -> status`` as the file on disk holds it."""
    document = _load(_state_path(workspace, plan_id))
    return {entry["wave"]: entry["status"] for entry in document["waves"]}


def _set(workspace, story_id, status):
    result, code = _run(
        workspace, verb="set-story", plan_id=PLAN_ID, story_id=story_id, status=status
    )
    assert code == 0 and result.ok, [item.render() for item in result.diagnostics]
    return result


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


def test_set_story_changes_only_that_story_and_its_wave(emitted):
    """The write touches three leaves, and the third is the point.

    ``waves[].status`` is derived from the same stories in the same commit --
    that is the contract ``set-story`` gained, not a stray write: no other
    story, and no other wave, is rewritten.
    """
    before = _load(_state_path(emitted))
    result, code = _run(
        emitted, verb="set-story", plan_id=PLAN_ID, story_id=STORY, status="in-progress"
    )
    assert code == 0 and result.ok
    after = _load(_state_path(emitted))
    assert _changed(before, after) == {
        "stories/%s/status" % STORY,
        "waves/0/status",
        "updated",
    }
    assert after["stories"][STORY]["status"] == "in-progress"
    assert after["waves"][0]["status"] == "in-progress"


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
# set-story -- the containing wave, derived and written in the same commit
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "statuses, expected",
    [
        ([], "complete"),  # vacuously: every story of an empty wave is applied
        (["pending"], "pending"),
        (["pending", "pending"], "pending"),
        (["applied"], "complete"),
        (["applied", "applied"], "complete"),
        (["in-progress"], "in-progress"),
        (["applied", "pending"], "in-progress"),
        (["in-progress", "pending"], "in-progress"),
        (["failed"], "failed"),
        (["failed", "pending"], "failed"),
        (["failed", "applied"], "failed"),
        # The ranking clause: failed outranks in-progress, in either order.
        (["failed", "in-progress"], "failed"),
        (["in-progress", "failed"], "failed"),
    ],
)
def test_the_wave_mapping_is_total_and_failed_outranks_in_progress(statuses, expected):
    """The whole mapping, exercised where it is stated rather than through a file."""
    assert state.wave_status(statuses) == expected
    assert expected in state.WAVE_STATUSES


def test_the_wave_mapping_is_stated_in_exactly_one_named_place():
    """Neither the applier nor the verb re-decides what a wave status is."""
    for function in (state._set_story, state._apply_wave_status):
        body = inspect.getsource(function)
        for status in state.WAVE_STATUSES:
            assert '"%s"' % status not in body, function.__name__
    assert callable(state.wave_status)


def test_a_wave_is_complete_only_once_every_story_is_applied(two_waves):
    assert _waves(two_waves) == {1: "pending", 2: "pending"}
    _set(two_waves, STORY, "applied")
    assert _waves(two_waves)[1] == "in-progress"  # SIBLING is still pending
    _set(two_waves, SIBLING, "applied")
    assert _waves(two_waves)[1] == "complete"


def test_a_wave_holding_a_failed_story_is_failed_even_beside_a_running_one(two_waves):
    _set(two_waves, STORY, "in-progress")
    assert _waves(two_waves)[1] == "in-progress"
    _set(two_waves, SIBLING, "failed")
    assert _waves(two_waves)[1] == "failed"


def test_a_failed_story_keeps_the_wave_failed_when_a_sibling_starts(two_waves):
    """The ranking is not an artefact of which story was written last."""
    _set(two_waves, STORY, "failed")
    assert _waves(two_waves)[1] == "failed"
    _set(two_waves, SIBLING, "in-progress")
    assert _waves(two_waves)[1] == "failed"


def test_a_wave_with_a_story_still_pending_is_in_progress(two_waves):
    _set(two_waves, STORY, "in-progress")
    assert _waves(two_waves)[1] == "in-progress"


def test_a_wave_no_story_has_touched_stays_pending(two_waves):
    """Wave 2 is never written by a wave-1 story, and never leaves ``pending``."""
    _set(two_waves, STORY, "applied")
    _set(two_waves, SIBLING, "applied")
    assert _waves(two_waves) == {1: "complete", 2: "pending"}


def test_a_wave_returns_to_in_progress_when_a_story_is_reopened(two_waves):
    """Derived, not latched: the wave follows its stories in both directions."""
    _set(two_waves, STORY, "applied")
    _set(two_waves, SIBLING, "applied")
    assert _waves(two_waves)[1] == "complete"
    _set(two_waves, SIBLING, "in-progress")
    assert _waves(two_waves)[1] == "in-progress"


def test_the_wave_and_the_story_are_written_in_one_commit(two_waves):
    """A write refused by schema validation leaves **both** unchanged on disk."""
    _run(two_waves, verb="run-increment", plan_id=PLAN_ID)
    digest = _digest(_state_path(two_waves))
    result, code = _run(
        two_waves,
        verb="set-story",
        plan_id=PLAN_ID,
        story_id=STORY,
        status="applied",
        attempt=4,  # attempt.attempt has maximum: 3
        branch=BRANCH,
    )
    assert code == 1 and not result.ok
    assert _codes(result) == [core.E_SCHEMA_INVALID]
    assert result.data["written"] == []
    assert _digest(_state_path(two_waves)) == digest
    assert _load(_state_path(two_waves))["stories"][STORY]["status"] == "pending"
    assert _waves(two_waves)[1] == "pending"


def test_the_wave_write_still_validates_against_plan_state(two_waves):
    _set(two_waves, STORY, "applied")
    document = _load(_state_path(two_waves))
    assert core.validate_against(core.load_schema("plan-state"), document) == []


def test_a_story_whose_wave_matches_no_entry_leaves_waves_untouched(emitted):
    """Defensive only: the schema already requires the entry, so no diagnostic."""
    document = _load(_state_path(emitted))
    document["stories"][STORY]["wave"] = 9
    emitted.write("context/project/plans/%s/state.yaml" % PLAN_ID, core.dump_yaml(document))
    before = _load(_state_path(emitted))

    result, code = _run(
        emitted, verb="set-story", plan_id=PLAN_ID, story_id=STORY, status="applied"
    )
    assert code == 0 and result.ok
    assert result.diagnostics == []
    after = _load(_state_path(emitted))
    assert after["waves"] == before["waves"]
    assert _changed(before, after) == {"stories/%s/status" % STORY, "updated"}


def test_no_other_verb_writes_a_wave_status(two_waves):
    """``set-story`` is the only writer; the field has one master."""
    before = _waves(two_waves)
    _run(two_waves, verb="run-increment", plan_id=PLAN_ID)
    _run(two_waves, verb="set-plan", plan_id=PLAN_ID, status="in-progress")
    _run(two_waves, verb="conformance", plan_id=PLAN_ID, status="clean", report=REPORT, findings=0)
    _run(two_waves, verb="telemetry", plan_id=PLAN_ID, cost=1.0, tokens=2, wall_clock=3.0)
    assert _waves(two_waves) == before


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


#: ``--<flag>`` -> the ``telemetry`` key it writes, and a value for it.
TELEMETRY_FIELDS = {
    "cost": ("cost_usd", 1.25),
    "tokens": ("tokens", 4096),
    "wall_clock": ("wall_clock_s", 42.5),
}


@pytest.mark.parametrize("omitted", sorted(TELEMETRY_FIELDS))
def test_telemetry_writes_null_for_a_single_omitted_field(emitted, omitted):
    """The CLI was stricter than its own schema; each flag now stands alone."""
    supplied = {
        flag: value for flag, (_key, value) in TELEMETRY_FIELDS.items() if flag != omitted
    }
    result, code = _run(emitted, verb="telemetry", plan_id=PLAN_ID, **supplied)
    assert code == 0 and result.ok

    block = _load(_state_path(emitted))["telemetry"]
    assert block[TELEMETRY_FIELDS[omitted][0]] is None
    for flag, (key, value) in TELEMETRY_FIELDS.items():
        if flag != omitted:
            assert block[key] == value
    assert core.validate_against(core.load_schema("plan-state"), _load(_state_path(emitted))) == []


def test_telemetry_with_every_field_omitted_writes_three_nulls(emitted):
    """Two true fields and a null beat nothing at all -- and so does no field."""
    result, code = _run(emitted, verb="telemetry", plan_id=PLAN_ID)
    assert code == 0 and result.ok
    document = _load(_state_path(emitted))
    assert document["telemetry"] == {"cost_usd": None, "tokens": None, "wall_clock_s": None}
    assert core.validate_against(core.load_schema("plan-state"), document) == []


def test_telemetry_serialises_an_omitted_field_as_yaml_null(emitted):
    _run(emitted, verb="telemetry", plan_id=PLAN_ID, tokens=4096)
    text = _state_path(emitted).read_text(encoding="utf-8")
    assert "cost_usd: null" in text
    assert "wall_clock_s: null" in text


def test_the_telemetry_flags_are_optional_at_the_parser_too(emitted):
    """Through ``argv``: no flag is ``required``, so the call is accepted."""
    completed = emitted.run_cli(
        "--workspace", emitted.root, "--now", NOW, "state", "telemetry", PLAN_ID
    )
    assert completed.returncode == 0, completed.stderr
    assert _load(_state_path(emitted))["telemetry"] == {
        "cost_usd": None,
        "tokens": None,
        "wall_clock_s": None,
    }


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


# ---------------------------------------------------------------------------
# set-slice -- one commit over two files, and a status that is an input
# ---------------------------------------------------------------------------

SLICE = "00"
SECOND_SLICE = "01"


def _set_slice(workspace, slice_id=SLICE, status="applied", **extra):
    return _run(
        workspace, verb="set-slice", plan_id=PLAN_ID, slice_id=slice_id, status=status, **extra
    )


def test_set_slice_writes_the_entry_and_the_ledger_counters_in_one_commit(emitted):
    result, code = _set_slice(emitted)
    assert code == 0 and result.ok
    assert result.data["written"] == [
        "context/project/plans/%s/state.yaml" % PLAN_ID,
        "context/project/state.yaml",
    ]

    state = _load(_state_path(emitted))
    ledger = _load(_ledger_path(emitted))
    assert state["slices"] == [{"slice": SLICE, "status": "applied"}]
    assert ledger["plans"][PLAN_ID]["slices_total"] == 1
    assert ledger["plans"][PLAN_ID]["slices_applied"] == 1
    assert core.validate_against(core.load_schema("plan-state"), state) == []
    assert core.validate_against(core.load_schema("project-state"), ledger) == []


def test_set_slice_records_acceptance_and_outcome(emitted):
    _set_slice(emitted, status="failed", acceptance="fail", outcome="stop")
    entry = _load(_state_path(emitted))["slices"][0]
    assert entry == {
        "slice": SLICE,
        "status": "failed",
        "acceptance": "fail",
        "outcome": "stop",
    }


def test_a_slice_status_is_not_derived_from_its_stories(emitted):
    """Every story applied, acceptance failed: the slice is `failed`.

    No function of story statuses can produce that, which is exactly why the
    status is an input here and derived for a wave.
    """
    _run(emitted, verb="run-increment", plan_id=PLAN_ID)
    _run(
        emitted,
        verb="set-story",
        plan_id=PLAN_ID,
        story_id=STORY,
        status="applied",
        attempt=1,
        branch=BRANCH,
    )
    _set_slice(emitted, status="failed", acceptance="fail")

    state = _load(_state_path(emitted))
    assert state["stories"][STORY]["status"] == "applied"
    assert state["waves"][0]["status"] == "complete"
    assert state["slices"][0]["status"] == "failed"


def test_set_slice_counts_only_the_applied_slices(emitted):
    _set_slice(emitted, SLICE, "applied")
    _set_slice(emitted, SECOND_SLICE, "in-progress")

    state = _load(_state_path(emitted))
    ledger = _load(_ledger_path(emitted))
    assert [entry["slice"] for entry in state["slices"]] == [SLICE, SECOND_SLICE]
    assert ledger["plans"][PLAN_ID]["slices_total"] == 2
    assert ledger["plans"][PLAN_ID]["slices_applied"] == 1


def test_set_slice_updates_an_existing_entry_in_place(emitted):
    _set_slice(emitted, SLICE, "in-progress")
    _set_slice(emitted, SLICE, "applied", acceptance="pass")

    state = _load(_state_path(emitted))
    assert len(state["slices"]) == 1
    assert state["slices"][0]["status"] == "applied"
    assert _load(_ledger_path(emitted))["plans"][PLAN_ID]["slices_applied"] == 1


def test_set_slice_leaves_the_two_files_agreeing_after_any_sequence(emitted):
    for slice_id, status in ((SLICE, "in-progress"), (SECOND_SLICE, "applied"), (SLICE, "applied")):
        _set_slice(emitted, slice_id, status)
        state = _load(_state_path(emitted))
        ledger = _load(_ledger_path(emitted))["plans"][PLAN_ID]
        applied = len([item for item in state["slices"] if item["status"] == "applied"])
        assert ledger["slices_applied"] == applied
        assert ledger["slices_total"] == len(state["slices"])


def test_a_refused_set_slice_writes_neither_file(emitted):
    ledger = _load(_ledger_path(emitted))
    del ledger["plans"][PLAN_ID]
    ledger["plans"][OTHER_PLAN_ID] = {
        "status": "pending",
        "plan_dir": "context/project/plans/%s" % OTHER_PLAN_ID,
    }
    emitted.write("context/project/state.yaml", core.dump_yaml(ledger))
    state_digest = _digest(_state_path(emitted))
    ledger_digest = _digest(_ledger_path(emitted))

    result, code = _set_slice(emitted)
    assert code == 1 and not result.ok
    assert _digest(_state_path(emitted)) == state_digest
    assert _digest(_ledger_path(emitted)) == ledger_digest


@pytest.mark.parametrize("slice_id", ["../escape", "0", "000", "zz", ""])
def test_a_bad_slice_id_is_rejected_never_sanitized(emitted, slice_id):
    digest = _digest(_state_path(emitted))
    result, code = _set_slice(emitted, slice_id)
    assert code == 2 and not result.ok
    assert [item.code for item in result.diagnostics] == [core.E_BAD_IDENT]
    assert _digest(_state_path(emitted)) == digest


# ---------------------------------------------------------------------------
# The four fields that used to be hand-written into state.yaml
# ---------------------------------------------------------------------------


def test_integration_branches_round_trip_through_set_plan(emitted):
    result, code = _run(
        emitted,
        verb="set-plan",
        plan_id=PLAN_ID,
        status="in-progress",
        integration_branches=json.dumps({"demo": "mexec/%s/integration" % PLAN_ID}),
    )
    assert code == 0 and result.ok
    state = _load(_state_path(emitted))
    assert state["integration_branches"] == {"demo": "mexec/%s/integration" % PLAN_ID}
    assert core.validate_against(core.load_schema("plan-state"), state) == []


def test_integration_branches_merge_rather_than_replace(emitted):
    _run(
        emitted,
        verb="set-plan",
        plan_id=PLAN_ID,
        status="in-progress",
        integration_branches=json.dumps({"demo": "mexec/x/integration"}),
    )
    _run(
        emitted,
        verb="set-plan",
        plan_id=PLAN_ID,
        status="in-progress",
        integration_branches=json.dumps({"other": "mexec/y/integration"}),
    )
    assert _load(_state_path(emitted))["integration_branches"] == {
        "demo": "mexec/x/integration",
        "other": "mexec/y/integration",
    }


def test_set_plan_without_the_option_leaves_the_map_alone(emitted):
    _run(
        emitted,
        verb="set-plan",
        plan_id=PLAN_ID,
        status="in-progress",
        integration_branches=json.dumps({"demo": "mexec/x/integration"}),
    )
    before = _load(_state_path(emitted))
    _run(emitted, verb="set-plan", plan_id=PLAN_ID, status="applied")
    after = _load(_state_path(emitted))
    # Both calls ran at NOW, so `updated` is unchanged and `status` is the whole
    # delta: the branch map is not rewritten by a call that did not name it.
    assert _changed(before, after) == {"status"}


def test_validation_round_trips_through_set_story(started):
    result, code = _run(
        started,
        verb="set-story",
        plan_id=PLAN_ID,
        story_id=STORY,
        status="applied",
        attempt=1,
        branch=BRANCH,
        validation="pass",
    )
    assert code == 0 and result.ok
    story = _load(_state_path(started))["stories"][STORY]
    assert story["validation"] == {"post_story": "pass"}
    assert story["attempts"][0]["validation"] == "pass"


def test_worktree_removed_round_trips_through_set_story(started):
    result, code = _run(
        started,
        verb="set-story",
        plan_id=PLAN_ID,
        story_id=STORY,
        status="applied",
        attempt=1,
        branch=BRANCH,
        worktree=WORKTREE,
        worktree_removed=True,
    )
    assert code == 0 and result.ok
    attempt = _load(_state_path(started))["stories"][STORY]["attempts"][0]
    assert attempt["worktree_removed"] is True


def test_worktree_removed_needs_an_attempt_to_record_against(started):
    digest = _digest(_state_path(started))
    result, code = _run(
        started,
        verb="set-story",
        plan_id=PLAN_ID,
        story_id=STORY,
        status="applied",
        worktree_removed=True,
    )
    assert code == 2 and not result.ok
    assert [item.code for item in result.diagnostics] == [core.E_USAGE]
    assert _digest(_state_path(started)) == digest


def test_deferred_break_round_trips_through_set_story(started):
    result, code = _run(
        started,
        verb="set-story",
        plan_id=PLAN_ID,
        story_id=STORY,
        status="applied",
        deferred_break=json.dumps(
            {"blast_radius": "contained", "detail": "one out-of-tree importer"}
        ),
    )
    assert code == 0 and result.ok
    story = _load(_state_path(started))["stories"][STORY]
    assert story["deferred_break"] == {
        "blast_radius": "contained",
        "detail": "one out-of-tree importer",
    }
    assert core.validate_against(core.load_schema("plan-state"), _load(_state_path(started))) == []


def test_a_significant_blast_radius_is_refused_rather_than_recorded(started):
    """Only a *contained* break is deferred; a significant one halts the run."""
    digest = _digest(_state_path(started))
    result, code = _run(
        started,
        verb="set-story",
        plan_id=PLAN_ID,
        story_id=STORY,
        status="applied",
        deferred_break=json.dumps({"blast_radius": "significant", "detail": "cascades"}),
    )
    assert code == 1 and not result.ok
    assert [item.code for item in result.diagnostics] == [core.E_SCHEMA_INVALID]
    assert _digest(_state_path(started)) == digest


@pytest.mark.parametrize(
    "option,value",
    [
        ("integration_branches", "not json"),
        ("integration_branches", "[1, 2]"),
    ],
)
def test_a_malformed_json_option_is_refused_before_anything_is_written(emitted, option, value):
    digest = _digest(_state_path(emitted))
    result, code = _run(
        emitted, verb="set-plan", plan_id=PLAN_ID, status="applied", **{option: value}
    )
    assert code == 2 and not result.ok
    assert _digest(_state_path(emitted)) == digest


def _verb_parsers():
    """The parser each ``state`` verb declares, read off ``register`` itself."""
    parser = argparse.ArgumentParser(prog="mc.py")
    subparsers = parser.add_subparsers(dest="group")
    state.register(subparsers)
    for action in subparsers.choices[state.COMMAND]._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action.choices
    raise AssertionError("the state group declares no verbs")


def _options(verb):
    return {
        option
        for action in _verb_parsers()[verb]._actions
        for option in action.option_strings
    }


def test_the_four_previously_verb_less_fields_each_have_a_verb_now():
    """The invariant "state is written only through ``mc.py state``" held with
    four standing exceptions until these options existed. It now holds with
    none, which is why no test in this file hand-writes ``state.yaml``."""
    assert "--integration-branches" in _options("set-plan")
    assert {"--validation", "--worktree-removed", "--deferred-break"} <= _options("set-story")


#: Field names that exist only in ``state.yaml``. ``validation`` is deliberately
#: absent: the plan *graph* carries a block of the same name, so the name alone
#: cannot distinguish a hand-written state value from a legitimate draft field.
#: Its round trip is asserted directly instead, above.
STATE_ONLY_FIELDS = ("integration_branches", "worktree_removed", "deferred_break",
                     "contract_revisions")


@pytest.mark.parametrize("name", STATE_ONLY_FIELDS)
def test_no_test_in_this_file_writes_one_of_those_fields_by_hand(name):
    """The claim above, checked against this file rather than asserted about it.

    Each of these reaches ``state.yaml`` through its verb; a test constructing
    one as a document key would be exercising a hand-written value, which is the
    practice the options exist to end. Reading one back (``story[name]``) is
    fine and is what the round-trip cases do -- it is the ``"<name>":`` key form
    that would mean a document was being built here.
    """
    source = Path(__file__).read_text(encoding="utf-8")
    assert '"%s":' % name not in source
    assert "'%s':" % name not in source


# ---------------------------------------------------------------------------
# contract_revisions -- merged, never replaced
# ---------------------------------------------------------------------------


def test_contract_revisions_round_trip_through_set_story(started):
    result, code = _run(
        started,
        verb="set-story",
        plan_id=PLAN_ID,
        story_id=STORY,
        status="applied",
        contract_revisions=json.dumps({"EVENT-BUS": 2}),
    )
    assert code == 0 and result.ok
    assert _load(_state_path(started))["stories"][STORY]["contract_revisions"] == {"EVENT-BUS": 2}


def test_contract_revisions_merge_rather_than_replace(started):
    _run(
        started,
        verb="set-story",
        plan_id=PLAN_ID,
        story_id=STORY,
        status="in-progress",
        contract_revisions=json.dumps({"EVENT-BUS": 2}),
    )
    _run(
        started,
        verb="set-story",
        plan_id=PLAN_ID,
        story_id=STORY,
        status="applied",
        contract_revisions=json.dumps({"USER-API": 1}),
    )
    assert _load(_state_path(started))["stories"][STORY]["contract_revisions"] == {
        "EVENT-BUS": 2,
        "USER-API": 1,
    }


def test_a_later_revision_for_the_same_tag_overwrites_that_one_only(started):
    _run(
        started,
        verb="set-story",
        plan_id=PLAN_ID,
        story_id=STORY,
        status="in-progress",
        contract_revisions=json.dumps({"EVENT-BUS": 1, "USER-API": 1}),
    )
    _run(
        started,
        verb="set-story",
        plan_id=PLAN_ID,
        story_id=STORY,
        status="applied",
        contract_revisions=json.dumps({"EVENT-BUS": 3}),
    )
    assert _load(_state_path(started))["stories"][STORY]["contract_revisions"] == {
        "EVENT-BUS": 3,
        "USER-API": 1,
    }


def test_set_story_without_the_option_leaves_the_map_alone(started):
    _run(
        started,
        verb="set-story",
        plan_id=PLAN_ID,
        story_id=STORY,
        status="in-progress",
        contract_revisions=json.dumps({"EVENT-BUS": 2}),
    )
    before = _load(_state_path(started))
    _run(started, verb="set-story", plan_id=PLAN_ID, story_id=STORY, status="applied")
    after = _load(_state_path(started))
    assert "contract_revisions" not in _changed(before, after)


# ---------------------------------------------------------------------------
# conformance --deferred
# ---------------------------------------------------------------------------


def test_conformance_records_the_deferred_count(emitted):
    result, code = _run(
        emitted,
        verb="conformance",
        plan_id=PLAN_ID,
        status="drift",
        report=REPORT,
        findings=4,
        deferred=3,
    )
    assert code == 0 and result.ok
    block = _load(_state_path(emitted))["conformance"]
    assert block == {"status": "drift", "report": REPORT, "findings": 4, "deferred": 3}


def test_an_omitted_deferred_count_leaves_the_key_absent(emitted):
    """Absent means 0, so every conformance block written before keeps its meaning."""
    _run(
        emitted,
        verb="conformance",
        plan_id=PLAN_ID,
        status="drift",
        report=REPORT,
        findings=4,
    )
    assert "deferred" not in _load(_state_path(emitted))["conformance"]
