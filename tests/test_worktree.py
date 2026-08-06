"""The ``worktree`` command group.

Four things this suite has to establish, per TOOLS-TESTING.md §Unit:

* name composition -- both branches and the work-tree path;
* every verdict class, each from a fixture built to produce exactly that class;
* a branch name that does not parse is an ``orphan`` with null ``story_id``,
  ``run``, and ``attempt``;
* **no output ever instructs removal of an orphan** -- asserted over the whole
  rendered output and the whole JSON envelope, not just the ``verdict`` field.

A fifth follows from ``mexecute`` shipping **one slice** per run: a slice-scoped
run must name and reconcile exactly what a whole-plan run named, since the slice
is a *scope* narrowing and not a new naming axis -- and the thing a slice-scoped
run adds, its **Slice Acceptance**, has to reach ``state.yaml``, because merging
is no longer the completion criterion and no function of story statuses can say
so.

Everything runs against a synthetic workspace in ``tmp_path`` with no git
repository anywhere in it, which is also the proof that the group runs nothing:
reconciliation is driven entirely from a fixture string.
"""

import io
import json
from pathlib import Path

import pytest

from tools import core, plan as plan_group, state, worktree

PLAN_ID = "003-deterministic-mechanical-steps"
OTHER_PLAN = "004-something-else"
REPO = "ai-plugins"

APPLIED_STORY = "03-01-ai-plugins-ALPHA"
PENDING_STORY = "03-02-ai-plugins-BETA"
FAILED_STORY = "03-03-ai-plugins-GAMMA"
RUNNING_STORY = "03-04-ai-plugins-DELTA"
GHOST_STORY = "09-09-ai-plugins-GHOST"

#: Anything that could be read as "delete this tree". None of these may appear
#: anywhere in the output of a reconciliation whose verdicts are all orphans.
REMOVAL_TOKENS = (
    "remove",
    "removal",
    "removed",
    "delete",
    "deletion",
    "prune",
    "unlink",
    "rmdir",
    "rm -",
    "discard",
)

MODULE_SOURCE = Path(worktree.__file__).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Fixtures -- a synthetic plan, and porcelain text the caller "supplied"
# ---------------------------------------------------------------------------


def write_plan_graph(workspace, stories, plan_id=PLAN_ID):
    """``plan.yaml`` naming ``{story_id: repo}``."""
    lines = ["version: 2", "plan_id: %s" % plan_id, "type: full", "stories:"]
    for story_id in sorted(stories):
        lines.append("  %s:" % story_id)
        lines.append("    repo: %s" % stories[story_id])
        lines.append("    module: ALPHA")
        lines.append("    wave: 3")
    return workspace.write(
        "context/project/plans/%s/plan.yaml" % plan_id, "\n".join(lines) + "\n"
    )


def write_plan_state(workspace, statuses, plan_id=PLAN_ID, slices=None):
    """``state.yaml`` naming ``{story_id: status}``.

    ``slices`` -- ``{slice_id: status}`` -- writes the ``slices`` block a
    slice-scoped run records; omitted, the file is exactly what it always was.
    """
    lines = [
        "version: 2",
        "plan_id: %s" % plan_id,
        "run: 1",
        "updated: 2026-01-15",
        "status: in-progress",
        "stories:",
    ]
    for story_id in sorted(statuses):
        lines.append("  %s:" % story_id)
        lines.append("    repo: %s" % REPO)
        lines.append("    wave: 3")
        lines.append("    status: %s" % statuses[story_id])
        lines.append("    retries: 0")
    if slices:
        lines.append("slices:")
        for slice_id in sorted(slices):
            lines.append("  - slice: '%s'" % slice_id)
            lines.append("    status: %s" % slices[slice_id])
    return workspace.write(
        "context/project/plans/%s/state.yaml" % plan_id, "\n".join(lines) + "\n"
    )


def record(path, branch=None, head="0" * 40, bare=False, detached=False):
    """One ``git worktree list --porcelain`` record."""
    lines = ["worktree %s" % path]
    if bare:
        lines.append("bare")
        return "\n".join(lines)
    lines.append("HEAD %s" % head)
    if detached:
        lines.append("detached")
    elif branch is not None:
        lines.append("branch refs/heads/%s" % branch)
    return "\n".join(lines)


def porcelain(*records):
    """Records joined the way git emits them: blank-line separated, blank-ended."""
    return "\n\n".join(records) + "\n\n"


def story_ref(story_id, run=1, attempt=1, plan_id=PLAN_ID):
    return "%s/%s/%s/r%d/%d" % ("mexec", plan_id, story_id, run, attempt)


def run_names(workspace, plan_id=PLAN_ID, story_id=PENDING_STORY, run=1, attempt=1):
    args = workspace.args(
        verb="names", plan_id=plan_id, story_id=story_id, run=run, attempt=attempt
    )
    return worktree.run(args, workspace.ws)


def run_reconcile(workspace, text=None, plan_id=PLAN_ID, list_from="-"):
    fields = {"verb": "reconcile", "plan_id": plan_id, "list_from": list_from}
    if text is not None:
        fields["stdin"] = io.StringIO(text)
    return worktree.run(workspace.args(**fields), workspace.ws)


@pytest.fixture
def plan(workspace):
    """A plan whose four stories cover every status a verdict can rest on."""
    write_plan_graph(
        workspace,
        {
            APPLIED_STORY: REPO,
            PENDING_STORY: REPO,
            FAILED_STORY: REPO,
            RUNNING_STORY: REPO,
        },
    )
    write_plan_state(
        workspace,
        {
            APPLIED_STORY: "applied",
            PENDING_STORY: "pending",
            FAILED_STORY: "failed",
            RUNNING_STORY: "in-progress",
        },
    )
    workspace.mkdir("repos/%s/main/.git" % REPO)
    return workspace


# ---------------------------------------------------------------------------
# names -- composition
# ---------------------------------------------------------------------------


def test_names_composes_both_branches_and_the_worktree_path(plan):
    result = run_names(plan, story_id=PENDING_STORY, run=1, attempt=1)
    assert core.exit_code(result) == 0
    assert result.data == {
        "integration": "mexec/003-deterministic-mechanical-steps/integration",
        "story": "mexec/003-deterministic-mechanical-steps/03-02-ai-plugins-BETA/r1/1",
        "worktree_path": "repos/ai-plugins/03-02-ai-plugins-BETA-r1-1",
    }


def test_names_handles_multi_digit_run_and_attempt(plan):
    result = run_names(plan, story_id=PENDING_STORY, run=12, attempt=10)
    assert core.exit_code(result) == 0
    assert result.data["integration"] == "mexec/003-deterministic-mechanical-steps/integration"
    assert (
        result.data["story"]
        == "mexec/003-deterministic-mechanical-steps/03-02-ai-plugins-BETA/r12/10"
    )
    assert result.data["worktree_path"] == "repos/ai-plugins/03-02-ai-plugins-BETA-r12-10"


def test_names_data_matches_the_datamodel_field_for_field(plan):
    result = run_names(plan)
    assert tuple(result.data) == ("integration", "story", "worktree_path")
    assert worktree.BRANCH_FIELDS == ("integration", "story", "worktree_path")


def test_the_integration_branch_does_not_depend_on_the_story(plan):
    first = run_names(plan, story_id=PENDING_STORY, run=1, attempt=1)
    second = run_names(plan, story_id=FAILED_STORY, run=7, attempt=3)
    assert first.data["integration"] == second.data["integration"]


def test_composed_names_are_pure_functions(plan):
    assert worktree.integration_branch("003-x") == "mexec/003-x/integration"
    assert worktree.story_branch("003-x", "01-01-r-M", 2, 3) == "mexec/003-x/01-01-r-M/r2/3"
    assert worktree.worktree_path("r", "01-01-r-M", 2, 3) == "repos/r/01-01-r-M-r2-3"


def test_a_composed_story_branch_decomposes_back(plan):
    branch = worktree.story_branch(PLAN_ID, PENDING_STORY, 12, 10)
    assert worktree.decompose(branch) == (PLAN_ID, PENDING_STORY, 12, 10)


# ---------------------------------------------------------------------------
# names -- refusals
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["bad id", "../escape", "plan/id", ""])
def test_names_rejects_a_bad_plan_id(plan, bad):
    result = run_names(plan, plan_id=bad)
    assert core.exit_code(result) == 2
    assert [d.code for d in result.diagnostics] == [core.E_BAD_IDENT]
    # Refused, never sanitized: no name was composed from the bad value.
    assert result.data is None


@pytest.mark.parametrize("bad", ["bad story", "story/id", "caf\u00e9"])
def test_names_rejects_a_bad_story_id(plan, bad):
    result = run_names(plan, story_id=bad)
    assert core.exit_code(result) == 2
    assert [d.code for d in result.diagnostics] == [core.E_BAD_IDENT]
    assert result.data is None


@pytest.mark.parametrize("run,attempt", [(0, 1), (1, 0), (-3, 1), (1, -1)])
def test_names_rejects_a_non_positive_counter(plan, run, attempt):
    result = run_names(plan, run=run, attempt=attempt)
    assert core.exit_code(result) == 2
    assert [d.code for d in result.diagnostics] == [core.E_USAGE]


def test_names_refuses_to_invent_a_path_for_an_unknown_story(plan):
    result = run_names(plan, story_id=GHOST_STORY)
    assert core.exit_code(result) == 1
    assert [d.code for d in result.diagnostics] == [core.E_NOT_FOUND]
    # The branch names still hold -- only the path needs the plan graph.
    assert result.data["story"] == story_ref(GHOST_STORY)
    assert result.data["worktree_path"] is None


def test_names_reports_a_missing_plan_graph(workspace):
    result = run_names(workspace)
    assert core.exit_code(result) == 1
    diagnostic = result.diagnostics[0]
    assert diagnostic.code == core.E_NOT_FOUND
    assert diagnostic.file == "context/project/plans/%s/plan.yaml" % PLAN_ID
    assert result.data["worktree_path"] is None


def test_names_reports_a_story_with_no_repo(workspace):
    workspace.write(
        "context/project/plans/%s/plan.yaml" % PLAN_ID,
        "version: 2\nplan_id: %s\nstories:\n  %s:\n    wave: 3\n" % (PLAN_ID, PENDING_STORY),
    )
    result = run_names(workspace)
    assert core.exit_code(result) == 1
    assert [d.code for d in result.diagnostics] == [core.E_INVALID_STATE]
    assert result.data["worktree_path"] is None


def test_run_rejects_an_unknown_verb(workspace):
    result = worktree.run(workspace.args(verb=None), workspace.ws)
    assert core.exit_code(result) == 2
    assert [d.code for d in result.diagnostics] == [core.E_USAGE]


# ---------------------------------------------------------------------------
# Repo git-root probing -- filesystem only, sorted
# ---------------------------------------------------------------------------


def test_probe_prefers_the_repo_directory_when_it_is_itself_a_work_tree(workspace):
    workspace.mkdir("repos/solo/.git")
    assert worktree.probe_git_root(workspace.ws, "solo") == "repos/solo"


def test_probe_falls_back_to_the_first_sorted_subdirectory_work_tree(workspace):
    # A bare-repo-plus-worktrees layout: .bare holds the repository, and the
    # checkouts sit beside it. Created out of order on purpose.
    workspace.mkdir("repos/%s/zulu/.git" % REPO)
    workspace.mkdir("repos/%s/main/.git" % REPO)
    workspace.mkdir("repos/%s/.bare/objects" % REPO)
    assert worktree.probe_git_root(workspace.ws, REPO) == "repos/%s/main" % REPO


def test_probe_skips_subdirectories_that_are_not_work_trees(workspace):
    workspace.mkdir("repos/%s/aaa-not-a-checkout" % REPO)
    workspace.mkdir("repos/%s/bbb/.git" % REPO)
    assert worktree.probe_git_root(workspace.ws, REPO) == "repos/%s/bbb" % REPO


def test_probe_returns_none_when_nothing_is_a_work_tree(workspace):
    workspace.mkdir("repos/%s/empty" % REPO)
    assert worktree.probe_git_root(workspace.ws, REPO) is None
    assert worktree.probe_git_root(workspace.ws, "absent") is None


def test_names_warns_but_still_composes_when_no_work_tree_is_present(workspace):
    write_plan_graph(workspace, {PENDING_STORY: REPO})
    result = run_names(workspace)
    assert core.exit_code(result) == 0  # a warning, not an error
    assert [d.severity for d in result.diagnostics] == ["warning"]
    assert result.data["worktree_path"] == "repos/ai-plugins/03-02-ai-plugins-BETA-r1-1"


def test_probe_rejects_a_bad_repo_name(workspace):
    with pytest.raises(core.ToolError) as excinfo:
        worktree.probe_git_root(workspace.ws, "../escape")
    assert excinfo.value.diagnostic.code == core.E_BAD_IDENT


# ---------------------------------------------------------------------------
# Porcelain parsing
# ---------------------------------------------------------------------------


def test_parse_porcelain_reads_the_record_form():
    text = porcelain(
        record("/w/one", branch="mexec/003-x/01-01-r-M/r1/1"),
        record("/w/bare", bare=True),
        record("/w/det", detached=True),
    )
    records = worktree.parse_porcelain(text)
    assert [entry["worktree"] for entry in records] == ["/w/one", "/w/bare", "/w/det"]
    assert records[0]["branch"] == "refs/heads/mexec/003-x/01-01-r-M/r1/1"
    assert "bare" in records[1]
    assert "detached" in records[2]
    assert "branch" not in records[2]


def test_parse_porcelain_tolerates_a_missing_separator_and_trailing_blanks():
    text = (
        "worktree /w/one\nHEAD %s\nbranch refs/heads/a\n"
        "worktree /w/two\nHEAD %s\nbranch refs/heads/b\n\n\n" % ("a" * 40, "b" * 40)
    )
    records = worktree.parse_porcelain(text)
    assert [entry["worktree"] for entry in records] == ["/w/one", "/w/two"]


def test_parse_porcelain_keeps_paths_containing_spaces():
    records = worktree.parse_porcelain("worktree /w/two words\nHEAD %s\n" % ("c" * 40))
    assert records[0]["worktree"] == "/w/two words"


def test_parse_porcelain_never_raises_on_nonsense():
    assert worktree.parse_porcelain("") == []
    assert worktree.parse_porcelain("\n\n\n") == []
    records = worktree.parse_porcelain("garbage\nmore garbage\n")
    assert records and "worktree" not in records[0]


# ---------------------------------------------------------------------------
# reconcile -- one verdict per record, every class reachable
# ---------------------------------------------------------------------------


def test_reconcile_returns_one_verdict_per_record_in_listing_order(plan):
    text = porcelain(
        record("/w/applied", branch=story_ref(APPLIED_STORY)),
        record("/w/pending", branch=story_ref(PENDING_STORY)),
        record("/w/ghost", branch=story_ref(GHOST_STORY)),
    )
    result = run_reconcile(plan, text)
    assert [entry["path"] for entry in result.data] == ["/w/applied", "/w/pending", "/w/ghost"]
    assert [entry["verdict"] for entry in result.data] == ["remove", "keep", "orphan"]


def test_remove_when_the_story_is_applied(plan):
    result = run_reconcile(plan, porcelain(record("/w/a", branch=story_ref(APPLIED_STORY, 2, 3))))
    assert core.exit_code(result) == 0
    entry = result.data[0]
    assert entry["verdict"] == "remove"
    assert entry["story_id"] == APPLIED_STORY
    assert entry["run"] == 2
    assert entry["attempt"] == 3
    assert "applied" in entry["reason"]


@pytest.mark.parametrize(
    "story_id,status",
    [(PENDING_STORY, "pending"), (FAILED_STORY, "failed"), (RUNNING_STORY, "in-progress")],
)
def test_keep_when_the_story_is_not_applied(plan, story_id, status):
    result = run_reconcile(plan, porcelain(record("/w/k", branch=story_ref(story_id))))
    entry = result.data[0]
    assert entry["verdict"] == "keep"
    assert entry["story_id"] == story_id
    assert status in entry["reason"]


def test_orphan_when_the_story_is_absent_from_state(plan):
    result = run_reconcile(plan, porcelain(record("/w/g", branch=story_ref(GHOST_STORY, 4, 2))))
    entry = result.data[0]
    assert entry["verdict"] == "orphan"
    # The name parsed, so its facts are reported; the story simply is not there.
    assert entry["story_id"] == GHOST_STORY
    assert entry["run"] == 4
    assert entry["attempt"] == 2
    assert "state.yaml" in entry["reason"]


@pytest.mark.parametrize(
    "branch",
    [
        "main",
        "feature/whatever",
        "mexec/003-deterministic-mechanical-steps/03-02-ai-plugins-BETA",
        "mexec/003-deterministic-mechanical-steps/03-02-ai-plugins-BETA/r1",
        "mexec/003-deterministic-mechanical-steps/03-02-ai-plugins-BETA/1/1",
        "mexec/003-deterministic-mechanical-steps/03-02-ai-plugins-BETA/r0/1",
        "mexec/003-deterministic-mechanical-steps/03-02-ai-plugins-BETA/r01/1",
        "mexec/003-deterministic-mechanical-steps/03-02-ai-plugins-BETA/rx/1",
        "mexec/003-deterministic-mechanical-steps//r1/1",
    ],
)
def test_a_name_that_does_not_parse_is_an_orphan_with_null_fields(plan, branch):
    result = run_reconcile(plan, porcelain(record("/w/x", branch=branch)))
    entry = result.data[0]
    assert entry["verdict"] == "orphan"
    assert entry["story_id"] is None
    assert entry["run"] is None
    assert entry["attempt"] is None
    assert entry["branch"] == branch


def test_the_integration_branch_is_an_orphan_not_a_story(plan):
    branch = "mexec/%s/integration" % PLAN_ID
    result = run_reconcile(plan, porcelain(record("/w/i", branch=branch)))
    entry = result.data[0]
    assert entry["verdict"] == "orphan"
    assert entry["story_id"] is None
    assert "integration" in entry["reason"]


def test_a_branch_from_another_plan_is_an_orphan(plan):
    branch = story_ref(APPLIED_STORY, plan_id=OTHER_PLAN)
    result = run_reconcile(plan, porcelain(record("/w/o", branch=branch)))
    entry = result.data[0]
    # The very same story id is `applied` in this plan's state.yaml; matching on
    # the id alone would have removed another plan's work tree.
    assert entry["verdict"] == "orphan"
    assert OTHER_PLAN in entry["reason"]


@pytest.mark.parametrize("kwargs", [{"bare": True}, {"detached": True}, {}])
def test_a_record_with_no_branch_is_an_orphan(plan, kwargs):
    result = run_reconcile(plan, porcelain(record("/w/n", **kwargs)))
    entry = result.data[0]
    assert entry["verdict"] == "orphan"
    assert entry["branch"] == ""
    assert entry["story_id"] is None


def test_a_record_with_no_path_is_an_orphan(plan):
    result = run_reconcile(plan, "HEAD %s\nbranch refs/heads/main\n" % ("d" * 40))
    entry = result.data[0]
    assert entry["verdict"] == "orphan"
    assert entry["path"] == ""


def test_verdict_fields_match_the_datamodel_field_for_field(plan):
    text = porcelain(
        record("/w/applied", branch=story_ref(APPLIED_STORY)),
        record("/w/pending", branch=story_ref(PENDING_STORY)),
        record("/w/x", branch="main"),
    )
    result = run_reconcile(plan, text)
    for entry in result.data:
        assert tuple(entry) == ("path", "branch", "story_id", "run", "attempt", "verdict", "reason")
        assert isinstance(entry["path"], str)
        assert isinstance(entry["branch"], str)
        assert entry["story_id"] is None or core.is_ident(entry["story_id"])
        assert entry["run"] is None or isinstance(entry["run"], int)
        assert entry["attempt"] is None or isinstance(entry["attempt"], int)
        assert entry["verdict"] in ("remove", "keep", "orphan")
        assert isinstance(entry["reason"], str) and entry["reason"]
    assert worktree.VERDICT_FIELDS == (
        "path",
        "branch",
        "story_id",
        "run",
        "attempt",
        "verdict",
        "reason",
    )


# ---------------------------------------------------------------------------
# An orphan is reported, never removed
# ---------------------------------------------------------------------------


def test_no_output_ever_instructs_removal_of_an_orphan(plan):
    """Every orphan class at once, asserted over the *whole* output."""
    text = porcelain(
        record("/w/ghost", branch=story_ref(GHOST_STORY)),
        record("/w/other", branch=story_ref(APPLIED_STORY, plan_id=OTHER_PLAN)),
        record("/w/hand-made", branch="feature/by-hand"),
        record("/w/integration", branch="mexec/%s/integration" % PLAN_ID),
        record("/w/bare", bare=True),
        record("/w/detached", detached=True),
    )
    result = run_reconcile(plan, text)
    assert {entry["verdict"] for entry in result.data} == {"orphan"}

    stdout, stderr = result.render()
    envelope = result.to_json()
    for rendering in (stdout, stderr, envelope):
        lowered = rendering.lower()
        for token in REMOVAL_TOKENS:
            assert token not in lowered, "%r appears in %r" % (token, rendering)


def test_a_mixed_listing_confines_removal_language_to_applied_stories(plan):
    text = porcelain(
        record("/w/applied", branch=story_ref(APPLIED_STORY)),
        record("/w/pending", branch=story_ref(PENDING_STORY)),
        record("/w/ghost", branch=story_ref(GHOST_STORY)),
        record("/w/hand-made", branch="main"),
    )
    result = run_reconcile(plan, text)
    for entry in result.data:
        if entry["verdict"] == "remove":
            continue
        blob = json.dumps(entry).lower()
        for token in REMOVAL_TOKENS:
            assert token not in blob, "%r appears in %r" % (token, entry)
    # And exactly one work tree -- the applied one -- carries a remove verdict.
    assert [entry["path"] for entry in result.data if entry["verdict"] == "remove"] == [
        "/w/applied"
    ]


def test_no_verdict_carries_an_instruction_field(plan):
    result = run_reconcile(plan, porcelain(record("/w/g", branch=story_ref(GHOST_STORY))))
    for entry in result.data:
        assert set(entry) == set(worktree.VERDICT_FIELDS)
        for forbidden in ("command", "action", "instruction", "cleanup", "git"):
            assert forbidden not in entry


# ---------------------------------------------------------------------------
# Input handling -- the caller supplies the listing; this group runs nothing
# ---------------------------------------------------------------------------


def test_reconcile_needs_no_git_repository_at_all(plan):
    """No .git anywhere but the fake marker, and no repository is consulted."""
    for candidate in plan.root.rglob(".git"):
        assert candidate.is_dir()  # the probe fixture's placeholder only
    text = porcelain(record("/nowhere/at/all", branch=story_ref(APPLIED_STORY)))
    result = run_reconcile(plan, text)
    assert result.data[0]["verdict"] == "remove"
    # The path in the verdict is echoed from the listing, never probed.
    assert not (plan.root / "nowhere").exists()


def test_reconcile_reads_a_file_given_to_list_from(plan):
    text = porcelain(record("/w/applied", branch=story_ref(APPLIED_STORY)))
    plan.write("listing.txt", text)
    result = run_reconcile(plan, text=None, list_from="listing.txt")
    assert core.exit_code(result) == 0
    assert [entry["verdict"] for entry in result.data] == ["remove"]


def test_reconcile_refuses_a_listing_outside_the_workspace(plan):
    outside = plan.root.parent / "outside-listing.txt"
    outside.write_text("worktree /w/x\n", encoding="utf-8")
    result = run_reconcile(plan, text=None, list_from=str(outside))
    assert core.exit_code(result) == 2
    assert [d.code for d in result.diagnostics] == [core.E_PATH_ESCAPE]


def test_reconcile_reports_a_missing_listing_file(plan):
    result = run_reconcile(plan, text=None, list_from="absent.txt")
    assert core.exit_code(result) == 1
    assert [d.code for d in result.diagnostics] == [core.E_NO_SUCH_FILE]


def test_reconcile_requires_list_from(plan):
    result = run_reconcile(plan, text=None, list_from=None)
    assert core.exit_code(result) == 2
    assert [d.code for d in result.diagnostics] == [core.E_USAGE]


def test_reconcile_warns_on_an_empty_listing(plan):
    result = run_reconcile(plan, "")
    assert core.exit_code(result) == 0
    assert result.data == []
    assert [d.severity for d in result.diagnostics] == ["warning"]


def test_reconcile_rejects_a_bad_plan_id(plan):
    result = run_reconcile(plan, porcelain(record("/w/x", branch="main")), plan_id="bad id")
    assert core.exit_code(result) == 2
    assert [d.code for d in result.diagnostics] == [core.E_BAD_IDENT]


def test_reconcile_without_a_state_file_orphans_everything(workspace):
    """No recorded status means nothing is applied -- the safe answer."""
    text = porcelain(record("/w/a", branch=story_ref(APPLIED_STORY)))
    result = run_reconcile(workspace, text)
    assert core.exit_code(result) == 1
    assert [d.code for d in result.diagnostics] == [core.E_NOT_FOUND]
    assert [entry["verdict"] for entry in result.data] == ["orphan"]


def test_reconcile_reports_a_malformed_state_file(workspace):
    workspace.write("context/project/plans/%s/state.yaml" % PLAN_ID, "just a string\n")
    result = run_reconcile(workspace, porcelain(record("/w/a", branch=story_ref(APPLIED_STORY))))
    # An unparseable input is exit 2, per TOOLS-INTERFACE.md's exit codes.
    assert core.exit_code(result) == 2
    assert [d.code for d in result.diagnostics] == [core.E_PARSE]
    assert [entry["verdict"] for entry in result.data] == ["orphan"]


def test_verdicts_derive_from_state_alone(plan):
    """Creating the directory a verdict names changes nothing about it."""
    before = run_reconcile(plan, porcelain(record("/w/g", branch=story_ref(GHOST_STORY))))
    plan.mkdir("repos/%s/%s-r1-1" % (REPO, GHOST_STORY))
    after = run_reconcile(plan, porcelain(record("/w/g", branch=story_ref(GHOST_STORY))))
    assert before.data == after.data

    # Flipping the *status* is the only thing that moves a verdict.
    write_plan_state(plan, {GHOST_STORY: "applied"})
    flipped = run_reconcile(plan, porcelain(record("/w/g", branch=story_ref(GHOST_STORY))))
    assert flipped.data[0]["verdict"] == "remove"


# ---------------------------------------------------------------------------
# A slice-scoped run -- the names it uses, and the acceptance that closes it
# ---------------------------------------------------------------------------

SLICE = "00"
SECOND_SLICE = "01"

#: Two stories in two waves, split across two slices, so "the slice in scope"
#: and "the wave in scope" cannot be confused for one another.
SLICE_ZERO_STORY = "01-01-ai-plugins-ALPHA"
SLICE_ONE_STORY = "02-01-ai-plugins-BETA"


def sliced_draft():
    """A version-3 draft: every story in exactly one slice, acceptance runnable."""
    return {
        "stories": {
            SLICE_ZERO_STORY: {
                "repo": REPO,
                "module": "ALPHA",
                "wave": 1,
                "prerequisites": [],
                "target_paths": ["src/alpha.py"],
                "validation": {"post_story": [{"kind": "prose", "description": "it holds"}]},
            },
            SLICE_ONE_STORY: {
                "repo": REPO,
                "module": "BETA",
                "wave": 2,
                "prerequisites": [],
                "target_paths": ["src/beta.py"],
                "validation": {"post_story": [{"kind": "prose", "description": "it holds"}]},
            },
        },
        "slices": [
            {
                "slice": SLICE,
                "name": "walking skeleton",
                "behavior": "the shape of it runs end to end",
                "acceptance": [
                    {"kind": "exit-code", "command": "true", "description": "it runs"}
                ],
                "stories": [SLICE_ZERO_STORY],
            },
            {
                "slice": SECOND_SLICE,
                "name": "depth",
                "behavior": "and it still runs",
                "acceptance": [
                    {"kind": "exit-code", "command": "true", "description": "it still runs"}
                ],
                "stories": [SLICE_ONE_STORY],
            },
        ],
    }


def emit_sliced_plan(workspace, plan_id=PLAN_ID):
    """Seed a sliced plan, its ``state.yaml`` and its ledger entry."""
    result = plan_group.run(
        workspace.args(
            verb="emit", plan_id=plan_id, stdin=io.StringIO(json.dumps(sliced_draft()))
        ),
        workspace.ws,
    )
    assert result.ok, [item.render() for item in result.diagnostics]
    workspace.mkdir("repos/%s/main/.git" % REPO)
    return workspace


def set_slice(workspace, slice_id=SLICE, status="applied", plan_id=PLAN_ID, **extra):
    return state.run(
        workspace.args(
            verb="set-slice", plan_id=plan_id, slice_id=slice_id, status=status, **extra
        ),
        workspace.ws,
    )


def set_story(workspace, story_id, status="applied", plan_id=PLAN_ID, **extra):
    return state.run(
        workspace.args(
            verb="set-story", plan_id=plan_id, story_id=story_id, status=status, **extra
        ),
        workspace.ws,
    )


def loaded(workspace, relative):
    return core.load_yaml(workspace.path(relative), relative)


def plan_state(workspace, plan_id=PLAN_ID):
    return loaded(workspace, "context/project/plans/%s/state.yaml" % plan_id)


def ledger_entry(workspace, plan_id=PLAN_ID):
    return loaded(workspace, "context/project/state.yaml")["plans"][plan_id]


@pytest.fixture
def sliced(workspace):
    """A plan whose stories are split across two slices."""
    return emit_sliced_plan(workspace)


def test_a_slice_scoped_run_names_what_the_unsliced_graph_named(workspace):
    """The same story, the same run, the same attempt -- the same three names.

    Slicing narrows *what a run ships*; it is not a naming axis. If it were, a
    plan resliced mid-delivery would rename branches out from under the
    work trees an earlier run deliberately kept.
    """
    write_plan_graph(workspace, {SLICE_ONE_STORY: REPO})
    workspace.mkdir("repos/%s/main/.git" % REPO)
    unsliced = run_names(workspace, story_id=SLICE_ONE_STORY, run=2, attempt=3)

    emit_sliced_plan(workspace)
    scoped = run_names(workspace, story_id=SLICE_ONE_STORY, run=2, attempt=3)

    assert core.exit_code(scoped) == 0
    assert scoped.data == unsliced.data
    assert plan_state(workspace)["slices"]  # the graph really is sliced


def test_no_name_a_slice_scoped_run_uses_carries_its_slice(sliced):
    """Five branch segments and one path segment -- no room for a sixth fact."""
    result = run_names(sliced, story_id=SLICE_ONE_STORY, run=1, attempt=1)
    assert result.data["story"].split("/") == [
        "mexec",
        PLAN_ID,
        SLICE_ONE_STORY,
        "r1",
        "1",
    ]
    assert result.data["worktree_path"] == "repos/%s/%s-r1-1" % (REPO, SLICE_ONE_STORY)
    # And the decomposition reports four facts, not five: a branch cut for a
    # slice-scoped run parses exactly as one cut for a whole-plan run.
    assert worktree.decompose(result.data["story"]) == (PLAN_ID, SLICE_ONE_STORY, 1, 1)


def test_the_integration_branch_is_shared_by_every_slice(sliced):
    """One integration branch per plan, so slice 01 starts from slice 00's tip."""
    first = run_names(sliced, story_id=SLICE_ZERO_STORY)
    second = run_names(sliced, story_id=SLICE_ONE_STORY)
    assert first.data["integration"] == second.data["integration"]
    assert first.data["integration"] == worktree.integration_branch(PLAN_ID)
    assert SLICE not in first.data["integration"].split("/")


def test_reconcile_verdicts_do_not_depend_on_which_slice_a_story_is_in(workspace):
    """A recorded ``slices`` block changes no verdict: status is what decides."""
    write_plan_graph(workspace, {APPLIED_STORY: REPO, PENDING_STORY: REPO})
    statuses = {APPLIED_STORY: "applied", PENDING_STORY: "pending"}
    text = porcelain(
        record("/w/applied", branch=story_ref(APPLIED_STORY)),
        record("/w/pending", branch=story_ref(PENDING_STORY)),
    )

    write_plan_state(workspace, statuses)
    before = run_reconcile(workspace, text)

    write_plan_state(workspace, statuses, slices={SLICE: "applied", SECOND_SLICE: "in-progress"})
    after = run_reconcile(workspace, text)

    assert [entry["verdict"] for entry in after.data] == ["remove", "keep"]
    assert before.data == after.data


def test_a_slice_acceptance_result_reaches_state(sliced):
    """``set-slice --acceptance`` is how the slice's demonstration is recorded."""
    result = set_slice(sliced, SLICE, "applied", acceptance="pass")
    assert core.exit_code(result) == 0

    recorded = plan_state(sliced)["slices"]
    assert recorded[0] == {"slice": SLICE, "status": "applied", "acceptance": "pass"}
    # ...and the ledger's counters move in the same write, never separately.
    assert ledger_entry(sliced)["slices_total"] == 2
    assert ledger_entry(sliced)["slices_applied"] == 1


@pytest.mark.parametrize("acceptance", ["pass", "fail", "unconfirmed", "not-run"])
def test_every_acceptance_result_a_slice_can_carry_reaches_state(sliced, acceptance):
    set_slice(sliced, SLICE, "in-progress", acceptance=acceptance)
    assert plan_state(sliced)["slices"][0]["acceptance"] == acceptance


def test_a_slice_whose_stories_all_merged_can_still_fail_its_acceptance(sliced):
    """Merging is not the completion criterion; the acceptance is.

    No function of story statuses produces this outcome, which is why the
    slice's status is written rather than derived.
    """
    for story_id in (SLICE_ZERO_STORY, SLICE_ONE_STORY):
        assert core.exit_code(set_story(sliced, story_id, "applied")) == 0
    recorded = plan_state(sliced)
    assert {entry["status"] for entry in recorded["stories"].values()} == {"applied"}

    set_slice(sliced, SLICE, "failed", acceptance="fail")
    entry = plan_state(sliced)["slices"][0]
    assert entry["status"] == "failed"
    assert entry["acceptance"] == "fail"
    assert ledger_entry(sliced)["slices_applied"] == 0


def test_an_acceptance_nobody_ran_is_never_invented(sliced):
    """A slice recorded without one carries no acceptance at all.

    A report cannot then present an unlooked-at slice as demonstrated -- the
    field is absent rather than defaulted to something reassuring.
    """
    set_slice(sliced, SLICE, "in-progress")
    assert "acceptance" not in plan_state(sliced)["slices"][0]


def test_one_slice_s_acceptance_survives_the_next_slice_being_recorded(sliced):
    set_slice(sliced, SLICE, "applied", acceptance="pass")
    set_slice(sliced, SECOND_SLICE, "in-progress")
    recorded = plan_state(sliced)["slices"]
    assert [entry["slice"] for entry in recorded] == [SLICE, SECOND_SLICE]
    assert recorded[0]["acceptance"] == "pass"
    assert ledger_entry(sliced)["slices_applied"] == 1


def test_recording_a_slice_leaves_every_name_the_run_used_untouched(sliced):
    """The acceptance write moves state, never the names an in-flight run holds."""
    before = run_names(sliced, story_id=SLICE_ONE_STORY, run=1, attempt=1)
    set_slice(sliced, SLICE, "applied", acceptance="pass")
    after = run_names(sliced, story_id=SLICE_ONE_STORY, run=1, attempt=1)
    assert before.data == after.data


# ---------------------------------------------------------------------------
# Structural guarantees
# ---------------------------------------------------------------------------


def test_the_group_never_shells_out():
    for token in ("subprocess", "os.system", "os.popen", "shutil.which", "popen"):
        assert token not in MODULE_SOURCE, "worktree.py must not reference %r" % token


def test_the_group_imports_no_sibling_command_group():
    for token in ("tools.state", "tools.check", "import state", "import plan", "import change"):
        assert token not in MODULE_SOURCE, "worktree.py must not reference %r" % token
    assert "from tools import core" in MODULE_SOURCE


def test_reconcile_is_deterministic(plan):
    text = porcelain(
        record("/w/applied", branch=story_ref(APPLIED_STORY)),
        record("/w/pending", branch=story_ref(PENDING_STORY)),
        record("/w/x", branch="main"),
    )
    first = run_reconcile(plan, text)
    second = run_reconcile(plan, text)
    assert first.to_json() == second.to_json()


def test_nothing_is_written_by_either_verb(plan):
    before = sorted(str(item.relative_to(plan.root)) for item in plan.root.rglob("*"))
    run_names(plan)
    run_reconcile(plan, porcelain(record("/w/a", branch=story_ref(APPLIED_STORY))))
    after = sorted(str(item.relative_to(plan.root)) for item in plan.root.rglob("*"))
    assert before == after
