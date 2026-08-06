"""The ``plan`` command group.

Establishes what TOOLS-TESTING.md requires of it -- scope full vs incremental,
resolution order ledger-then-fallback, the resume wave as the first wave holding
an unfinished story, and story-id derivation including the migration ``m``
suffix -- plus the emission guarantees the change document adds: every emitted
file validates before it is persisted, a graph that would not validate is
refused with nothing written, another plan's ledger entry is never touched, and
the four E2E rules a story carries are generated from their owning section
rather than copied by hand.

The group is imported and called directly, never through ``argv``, and the
injected clock is always ``NOW``.
"""

import io
import json
import re

import pytest

from conftest import NOW, PLUGIN_ROOT
from tools import check, core, plan

STANDARD_SPEC = PLUGIN_ROOT / "shared" / "STANDARD-SPEC.md"


# ---------------------------------------------------------------------------
# Helpers -- every fixture is synthetic and lives in tmp_path
# ---------------------------------------------------------------------------


def _run(workspace, **fields):
    """Call the group directly and return ``(result, exit_code)``."""
    result = plan.run(workspace.args(**fields), workspace.ws)
    return result, core.exit_code(result)


def _backtick_row(path):
    """The form the table has always been written in: a workspace-relative path."""
    repo = path.split("/")[1]
    return "| `%s` | `%s` | a change |\n" % (repo, path)


def _link_row(path):
    """The markdown-link form ``check handoff`` accepted and ``scope`` did not."""
    repo, filename = path.split("/")[1], path.rsplit("/", 1)[-1]
    return "| [%s](../../%s/) | [%s](../../%s/changes/%s) | a change |\n" % (
        repo,
        repo,
        filename,
        repo,
        filename,
    )


#: The two ways a ``Repo Change Files`` row can name the same change file. Both
#: must resolve identically -- the divergence between them is the defect.
TABLE_FORMS = {"backtick": _backtick_row, "link": _link_row}


def _index(number, slug, status, change_files=(), form="backtick"):
    row = TABLE_FORMS[form]
    rows = "".join(row(path) for path in change_files)
    return (
        "<!-- project-change: %s -->\n"
        "<!-- scope: repo -->\n"
        "<!-- repos: demo -->\n"
        "<!-- status: %s -->\n"
        "<!-- date: 2026-01-01 -->\n"
        "\n"
        "# PROJECT-CHANGE-%s: %s\n"
        "\n"
        "## Summary\n\nOne change.\n"
        "\n"
        "## Repo Change Files\n\n"
        "| Repo | Change File | Summary |\n"
        "|------|-------------|---------|\n"
        "%s" % (number, status, number, slug, rows)
    )


def _add_index(workspace, number, slug, status, change_files=(), form="backtick"):
    return workspace.write(
        "context/project/changes/PROJECT-CHANGE-%s-%s.md" % (number, slug),
        _index(number, slug, status, change_files, form),
    )


def _story(story_id, repo="demo", module="ALPHA", wave=1, prerequisites=(), **extra):
    story = {
        "repo": repo,
        "module": module,
        "wave": wave,
        "prerequisites": list(prerequisites),
        "target_paths": ["src/%s.py" % module.lower()],
        "validation": {"post_story": [{"kind": "prose", "description": "it holds"}]},
    }
    story.update(extra)
    return story_id, story


def _draft(*stories, **fields):
    draft = {"stories": dict(stories)}
    draft.update(fields)
    return draft


def _emit(workspace, plan_id, draft, now=NOW):
    return _run(
        workspace,
        verb="emit",
        plan_id=plan_id,
        stdin=io.StringIO(json.dumps(draft)),
        now=now,
    )


def _write_plan(
    workspace,
    plan_id,
    waves,
    statuses=None,
    run=0,
    status="pending",
    overrides=None,
    project_change=None,
):
    """A plan directory with a graph and a state file, written directly.

    ``overrides`` maps a story id to extra/overridden story fields -- ``repo``,
    ``module``, ``change_file`` -- for the shards tests that need a second
    repo or a change document a story references.
    """
    overrides = overrides or {}
    graph_stories = {}
    state_stories = {}
    for wave, story_ids in waves:
        for story_id in story_ids:
            story = dict(_story(story_id, wave=wave)[1])
            story.update(overrides.get(story_id, {}))
            story["file"] = "PLAN-%s.md" % story_id
            graph_stories[story_id] = story
            state_stories[story_id] = {
                "repo": story.get("repo", "demo"),
                "wave": wave,
                "status": (statuses or {}).get(story_id, "pending"),
                "retries": 0,
            }
    graph = {
        "version": 2,
        "plan_id": plan_id,
        "type": "incremental" if project_change else "full",
        "repos": sorted({story.get("repo", "demo") for story in graph_stories.values()}),
        "waves": [{"wave": wave, "stories": list(story_ids)} for wave, story_ids in waves],
        "stories": graph_stories,
    }
    if project_change is not None:
        graph["project_change"] = project_change
    state = {
        "version": 2,
        "plan_id": plan_id,
        "run": run,
        "status": status,
        "stories": state_stories,
    }
    workspace.write(
        "context/project/plans/%s/plan.yaml" % plan_id, core.dump_yaml(graph)
    )
    workspace.write(
        "context/project/plans/%s/state.yaml" % plan_id, core.dump_yaml(state)
    )


def _write_ledger(workspace, entries):
    plans = {
        plan_id: {"status": status, "plan_dir": "context/project/plans/%s" % plan_id}
        for plan_id, status in entries
    }
    workspace.write(
        "context/project/state.yaml", core.dump_yaml({"version": 1, "plans": plans})
    )


def _catalog(with_e2e=False):
    modules = {
        "ALPHA": {
            "layer": "L1-core",
            "files": [{"path": "context/demo/spec/ALPHA/ALPHA-OVERVIEW.md", "facet": "overview"}],
        },
        "BETA": {
            "layer": "L2-services",
            "files": [{"path": "context/demo/spec/BETA/BETA-OVERVIEW.md", "facet": "overview"}],
        },
    }
    layers = {"L1-core": {"modules": ["ALPHA"]}, "L2-services": {"modules": ["BETA"]}}
    if with_e2e:
        modules["E2E"] = {
            "layer": "L2-services",
            "files": [{"path": "context/demo/spec/E2E/E2E-TESTING.md", "facet": "test"}],
        }
        layers["L2-services"]["modules"].append("E2E")
    return {"version": 1, "repo": "demo", "layers": layers, "modules": modules}


def _add_catalog(workspace, repo="demo", with_e2e=False):
    return workspace.write(
        "context/%s/spec/CATALOG.yaml" % repo, core.dump_yaml(_catalog(with_e2e))
    )


def _e2e_rules_from_standard():
    """The four rules, read here independently of the code under test."""
    text = STANDARD_SPEC.read_text(encoding="utf-8")
    section = text.split("## E2E Testing Hard Rules", 1)[1]
    section = re.split(r"\n## |\n---", section, maxsplit=1)[0]
    return [line for line in section.split("\n") if line.startswith("- **")]


def _validates(workspace, relative, kind):
    """The file on disk, validated against ``kind``."""
    schema = core.load_schema(kind)
    instance = core.load_instance(workspace.path(relative))
    return core.validate_against(schema, instance)


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def test_a_missing_verb_is_a_usage_error(workspace):
    result, code = _run(workspace, verb=None)
    assert code == 2
    assert [d.code for d in result.diagnostics] == [core.E_USAGE]


def test_the_group_borrows_only_the_shared_matcher_from_a_sibling():
    """The one thing taken from ``tools/change.py`` is the definition of what a
    repo change reference *is* -- the same pair ``check handoff`` reads.

    Change documents themselves are still read through ``core``'s front-matter
    helper, no ``change`` verb is called from here, and no other sibling group
    is reached for at all. Duplicating the matcher instead is the defect
    CHANGE-020 removes, so this is a borrowing the module is required to make.
    """
    source = (PLUGIN_ROOT / "tools" / "plan.py").read_text(encoding="utf-8")
    assert "load_front_matter" in source
    assert "change.REPO_CHANGE_REF_RE" in source
    assert "change.section_body" in source
    # Behaviour is never borrowed: no verb of the sibling group is invoked.
    assert "change.run(" not in source
    for sibling in ("state", "worktree", "check", "spec", "req"):
        assert "from tools import %s" % sibling not in source
        assert "tools.%s" % sibling not in source


def test_the_group_carries_no_second_change_reference_matcher():
    """``_CHANGE_REF_RE`` is gone: two matchers for one concept is the defect.

    Matched on a word boundary, because the name that stays --
    ``change.REPO_CHANGE_REF_RE`` -- ends with the name that goes, and a bare
    substring search cannot tell the survivor from the deletion.
    """
    source = (PLUGIN_ROOT / "tools" / "plan.py").read_text(encoding="utf-8")
    assert re.search(r"(?<![A-Za-z0-9_])_CHANGE_REF_RE", source) is None
    assert not hasattr(plan, "_CHANGE_REF_RE")
    assert "REPO_CHANGE_REF_RE" in source  # the survivor, so this is not vacuous


# ---------------------------------------------------------------------------
# plan scope
# ---------------------------------------------------------------------------


def test_scope_is_full_when_no_index_exists(workspace):
    result, code = _run(workspace, verb="scope")
    assert code == 0
    assert result.data == {
        "type": "full",
        "plan_id": "000-initial",
        "plan_dir": "context/project/plans/000-initial",
        "project_change": None,
        "change_files": [],
    }


def test_scope_fields_match_the_datamodel(workspace):
    _add_index(workspace, "001", "first", "pending")
    result, _ = _run(workspace, verb="scope")
    assert list(result.data) == [
        "type",
        "plan_id",
        "plan_dir",
        "project_change",
        "change_files",
    ]


def test_scope_is_incremental_on_the_highest_numbered_pending_index(workspace):
    _add_index(workspace, "001", "first", "applied")
    _add_index(workspace, "002", "second", "pending")
    _add_index(workspace, "010", "tenth", "pending")
    result, code = _run(workspace, verb="scope")
    assert code == 0
    assert result.data["type"] == "incremental"
    assert result.data["plan_id"] == "010-tenth"
    assert result.data["plan_dir"] == "context/project/plans/010-tenth"
    # A quoted string, never a bare digit run.
    assert result.data["project_change"] == "010"


@pytest.mark.parametrize("status", ["applied", "in-progress", "superseded", "complete"])
def test_a_non_pending_index_does_not_make_a_plan_incremental(workspace, status):
    _add_index(workspace, "003", "third", status)
    result, code = _run(workspace, verb="scope")
    assert code == 0
    assert result.data["type"] == "full"
    assert result.data["plan_id"] == "000-initial"
    assert result.data["project_change"] is None


def test_scope_skips_a_higher_non_pending_index(workspace):
    _add_index(workspace, "002", "second", "pending")
    _add_index(workspace, "003", "third", "applied")
    result, _ = _run(workspace, verb="scope")
    assert result.data["plan_id"] == "002-second"


def test_scope_reports_the_repo_change_files_the_index_references(workspace):
    referenced = [
        "context/demo/changes/CHANGE-002-thing.md",
        "context/other/changes/CHANGE-011-other-thing.md",
    ]
    _add_index(workspace, "004", "fourth", "pending", referenced)
    result, _ = _run(workspace, verb="scope")
    assert result.data["change_files"] == referenced


@pytest.mark.parametrize("form", sorted(TABLE_FORMS))
def test_scope_reads_the_same_change_files_from_either_table_form(workspace, form):
    """One matcher: a link-form table and a backticked one resolve identically.

    The link form is the one ``plan scope``'s own stricter pattern never saw,
    so this parametrisation is the regression the shared matcher closes.
    """
    referenced = [
        "context/demo/changes/CHANGE-002-thing.md",
        "context/other/changes/CHANGE-011-other-thing.md",
    ]
    _add_index(workspace, "004", "fourth", "pending", referenced, form=form)
    result, code = _run(workspace, verb="scope")
    assert code == 0
    assert result.data["change_files"] == referenced
    assert [item.severity for item in result.diagnostics] == []


@pytest.mark.parametrize("form", sorted(TABLE_FORMS))
def test_scope_and_check_handoff_see_the_same_references(workspace, form):
    """The two stages agreed only for one form before; now they agree for both.

    ``check handoff`` reports an index naming no repo change file. Whatever
    ``scope`` finds, the handoff must have found too -- a handoff reporting
    clean over a scope that came back empty is the failure being closed.
    """
    referenced = ["context/demo/changes/CHANGE-002-thing.md"]
    index_path = "context/project/changes/PROJECT-CHANGE-004-fourth.md"
    _add_index(workspace, "004", "fourth", "pending", referenced, form=form)

    scoped = _run(workspace, verb="scope")[0].data["change_files"]
    handoff = check.run(workspace.args(verb="handoff", target=None), workspace.ws)
    empty_index = [
        finding
        for finding in handoff.data["findings"]
        if finding.get("file") == index_path and "no repo change file" in finding["message"]
    ]
    assert scoped == referenced
    assert empty_index == []


def test_an_index_naming_nothing_is_reported_by_both_stages(workspace):
    """The negative case, so the agreement above is not vacuous."""
    index_path = "context/project/changes/PROJECT-CHANGE-004-fourth.md"
    _add_index(workspace, "004", "fourth", "pending")

    result, code = _run(workspace, verb="scope")
    handoff = check.run(workspace.args(verb="handoff", target=None), workspace.ws)

    assert code == 0  # a warning, not an error: the caller still gets the scope
    assert result.data == {
        "type": "incremental",
        "plan_id": "004-fourth",
        "plan_dir": "context/project/plans/004-fourth",
        "project_change": "004",
        "change_files": [],
    }
    assert len(result.diagnostics) == 1
    warning = result.diagnostics[0]
    assert warning.severity == core.SEVERITY_WARNING
    assert warning.file == index_path  # the diagnostic names the index
    assert "PROJECT-CHANGE-004-fourth.md" in warning.message
    assert [
        finding
        for finding in handoff.data["findings"]
        if finding.get("file") == index_path and "no repo change file" in finding["message"]
    ]


def test_an_empty_incremental_scope_is_a_warning_and_not_an_error(workspace):
    """``result.ok`` stays true, so no caller reads the warning as a failure."""
    _add_index(workspace, "004", "fourth", "pending")
    result, code = _run(workspace, verb="scope")
    assert result.ok is True
    assert code == 0
    assert [item.severity for item in result.diagnostics] == [core.SEVERITY_WARNING]


def test_a_full_scope_never_warns_about_an_empty_change_file_list(workspace):
    """``full`` means there is no index to name; an empty list is expected."""
    result, code = _run(workspace, verb="scope")
    assert code == 0
    assert result.data["change_files"] == []
    assert result.diagnostics == []


@pytest.mark.parametrize("form", sorted(TABLE_FORMS))
def test_scope_keeps_first_seen_order_and_collapses_duplicates(workspace, form):
    first = "context/demo/changes/CHANGE-009-ninth.md"
    second = "context/other/changes/CHANGE-002-second.md"
    third = "context/demo/changes/CHANGE-001-first.md"
    _add_index(
        workspace,
        "004",
        "fourth",
        "pending",
        [first, second, first, third, second],
        form=form,
    )
    result, _ = _run(workspace, verb="scope")
    # First-seen order, not sorted and not de-duplicated into set order.
    assert result.data["change_files"] == [first, second, third]


def test_scope_ignores_a_change_reference_outside_the_repo_change_section(workspace):
    """The section scoping is the sibling's too: prose elsewhere is not scope."""
    path = workspace.write(
        "context/project/changes/PROJECT-CHANGE-004-fourth.md",
        _index("004", "fourth", "pending").replace(
            "## Summary\n\nOne change.\n",
            "## Summary\n\nSupersedes context/demo/changes/CHANGE-001-first.md.\n",
        ),
    )
    assert "CHANGE-001-first.md" in path.read_text(encoding="utf-8")
    result, _ = _run(workspace, verb="scope")
    assert result.data["change_files"] == []


def test_scope_ignores_files_that_are_not_indexes(workspace):
    workspace.write("context/project/changes/notes.md", "<!-- status: pending -->\n")
    workspace.write(
        "context/project/changes/CHANGE-001-repo-level.md", "<!-- status: pending -->\n"
    )
    result, _ = _run(workspace, verb="scope")
    assert result.data["type"] == "full"


# ---------------------------------------------------------------------------
# plan story-id
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "filename,expected",
    [
        ("PLAN-01-01-repo-a-AUTH.md", "01-01-repo-a-AUTH"),
        ("PLAN-01-02m-repo-a-AUTH-MIGRATION.md", "01-02m-repo-a-AUTH-MIGRATION"),
        ("PLAN-12-09-ai-plugins-TOOLS-PLAN.md", "12-09-ai-plugins-TOOLS-PLAN"),
        (
            "context/project/plans/003-x/PLAN-02-03-ai-plugins-TOOLS.md",
            "02-03-ai-plugins-TOOLS",
        ),
    ],
)
def test_story_id_is_derived_from_the_filename(workspace, filename, expected):
    result, code = _run(workspace, verb="story-id", file=filename)
    assert code == 0
    assert result.data["story_id"] == expected


def test_the_migration_suffix_survives_derivation(workspace):
    result, _ = _run(workspace, verb="story-id", file="PLAN-01-02m-repo-a-AUTH-MIGRATION.md")
    story_id = result.data["story_id"]
    assert story_id.split("-")[1] == "02m"
    assert re.match(plan.STORY_ID_PATTERN, story_id)


@pytest.mark.parametrize("filename", ["STORY-01-01-a-B.md", "PLAN-01-01-a-B.txt", "notes.md"])
def test_a_filename_that_is_not_a_story_file_is_a_diagnostic(workspace, filename):
    result, code = _run(workspace, verb="story-id", file=filename)
    assert code == 1
    assert result.data["story_id"] is None
    assert [d.code for d in result.diagnostics] == [core.E_UNPARSED_NAME]


def test_a_name_that_does_not_match_the_story_id_pattern_is_a_diagnostic(workspace):
    result, code = _run(workspace, verb="story-id", file="PLAN-1-2-a-B.md")
    assert code == 1
    assert result.data["story_id"] is None
    assert [d.code for d in result.diagnostics] == [core.E_UNPARSED_NAME]


def test_a_derived_id_failing_ident_is_rejected_never_sanitized(workspace):
    result, code = _run(workspace, verb="story-id", file="PLAN-01-01-a B.md")
    assert code == 2
    assert [d.code for d in result.diagnostics] == [core.E_BAD_IDENT]
    assert result.data is None


# ---------------------------------------------------------------------------
# plan resolve
# ---------------------------------------------------------------------------


def test_resolve_fields_match_the_datamodel(workspace):
    _write_plan(workspace, "001-first", [(1, ["01-01-demo-ALPHA"])])
    result, _ = _run(workspace, verb="resolve", plan_id="001-first")
    assert list(result.data) == [
        "plan_id",
        "plan_dir",
        "status",
        "run",
        "resume_wave",
        "resume_slice",
        "pending_stories",
    ]


def test_resolve_takes_the_first_unfinished_ledger_plan_not_the_highest(workspace):
    for plan_id in ("001-first", "002-second", "003-third"):
        _write_plan(workspace, plan_id, [(1, ["01-01-demo-ALPHA"])])
    _write_ledger(
        workspace,
        [("001-first", "applied"), ("002-second", "in-progress"), ("003-third", "pending")],
    )
    result, code = _run(workspace, verb="resolve")
    assert code == 0
    assert result.data["plan_id"] == "002-second"


def test_resolve_falls_back_to_the_highest_numbered_plan_directory(workspace):
    for plan_id in ("001-first", "002-second", "010-tenth"):
        _write_plan(workspace, plan_id, [(1, ["01-01-demo-ALPHA"])])
    _write_ledger(workspace, [("001-first", "applied"), ("002-second", "applied")])
    result, code = _run(workspace, verb="resolve")
    assert code == 0
    assert result.data["plan_id"] == "010-tenth"


def test_resolve_falls_back_with_no_ledger_at_all(workspace):
    _write_plan(workspace, "004-fourth", [(1, ["01-01-demo-ALPHA"])])
    result, code = _run(workspace, verb="resolve")
    assert code == 0
    assert result.data["plan_id"] == "004-fourth"


def test_resolve_reports_when_there_is_nothing_to_resolve(workspace):
    result, code = _run(workspace, verb="resolve")
    assert code == 1
    assert [d.code for d in result.diagnostics] == [core.E_NOT_FOUND]


def test_an_explicit_plan_id_wins_over_the_ledger(workspace):
    _write_plan(workspace, "001-first", [(1, ["01-01-demo-ALPHA"])])
    _write_plan(workspace, "002-second", [(1, ["01-01-demo-ALPHA"])])
    _write_ledger(workspace, [("001-first", "in-progress")])
    result, _ = _run(workspace, verb="resolve", plan_id="002-second")
    assert result.data["plan_id"] == "002-second"


def test_the_resume_wave_is_the_first_wave_holding_an_unfinished_story(workspace):
    _write_plan(
        workspace,
        "001-first",
        [
            (1, ["01-01-demo-ALPHA"]),
            (2, ["02-01-demo-BETA", "02-02-demo-GAMMA"]),
            (3, ["03-01-demo-DELTA"]),
        ],
        statuses={
            "01-01-demo-ALPHA": "applied",
            "02-01-demo-BETA": "applied",
            "02-02-demo-GAMMA": "failed",
            "03-01-demo-DELTA": "pending",
        },
    )
    result, code = _run(workspace, verb="resolve", plan_id="001-first")
    assert code == 0
    assert result.data["resume_wave"] == 2
    assert result.data["pending_stories"] == ["02-02-demo-GAMMA", "03-01-demo-DELTA"]


def test_the_resume_wave_is_null_when_every_story_is_applied(workspace):
    _write_plan(
        workspace,
        "001-first",
        [(1, ["01-01-demo-ALPHA"]), (2, ["02-01-demo-BETA"])],
        statuses={"01-01-demo-ALPHA": "applied", "02-01-demo-BETA": "applied"},
        status="applied",
    )
    result, _ = _run(workspace, verb="resolve", plan_id="001-first")
    assert result.data["resume_wave"] is None
    assert result.data["pending_stories"] == []
    assert result.data["status"] == "applied"


def test_the_run_counter_is_returned_as_read_and_never_incremented(workspace):
    _write_plan(workspace, "001-first", [(1, ["01-01-demo-ALPHA"])], run=4)
    before = workspace.path("context/project/plans/001-first/state.yaml").read_bytes()
    first, _ = _run(workspace, verb="resolve", plan_id="001-first")
    second, _ = _run(workspace, verb="resolve", plan_id="001-first")
    assert first.data["run"] == 4
    assert second.data["run"] == 4
    after = workspace.path("context/project/plans/001-first/state.yaml").read_bytes()
    assert after == before


def test_a_plan_with_no_state_file_resolves_as_run_zero_and_all_pending(workspace):
    _write_plan(workspace, "001-first", [(1, ["01-01-demo-ALPHA"])])
    workspace.path("context/project/plans/001-first/state.yaml").unlink()
    result, code = _run(workspace, verb="resolve", plan_id="001-first")
    assert code == 0  # a warning, not an error
    assert result.data["run"] == 0
    assert result.data["resume_wave"] == 1
    assert result.data["pending_stories"] == ["01-01-demo-ALPHA"]
    assert [d.severity for d in result.diagnostics] == ["warning"]


def test_an_unknown_plan_directory_is_reported(workspace):
    result, code = _run(workspace, verb="resolve", plan_id="009-nope")
    assert code == 1
    assert [d.code for d in result.diagnostics] == [core.E_NOT_FOUND]


def test_a_plan_id_that_escapes_the_pattern_is_rejected(workspace):
    result, code = _run(workspace, verb="resolve", plan_id="../escape")
    assert code == 2
    assert [d.code for d in result.diagnostics] == [core.E_BAD_IDENT]


# ---------------------------------------------------------------------------
# plan waves
# ---------------------------------------------------------------------------


def test_waves_follow_catalog_layer_order_one_wave_per_layer(workspace):
    _add_catalog(workspace, with_e2e=True)
    result, code = _run(workspace, verb="waves", target="demo")
    assert code == 0
    assert result.data["waves"] == [
        {"wave": 1, "layer": "L1-core", "modules": ["ALPHA"]},
        {"wave": 2, "layer": "L2-services", "modules": ["BETA", "E2E"]},
    ]


def test_waves_performs_no_consumer_producer_reordering(workspace):
    """The modules of a layer come back in the order the catalog declares them.

    ``CONSUMER`` is listed before the ``PRODUCER`` it depends on; moving it
    later is mplan's judgment, so the assignment must leave the order alone.
    """
    catalog = {
        "version": 1,
        "repo": "demo",
        "layers": {"L1-core": {"modules": ["CONSUMER", "PRODUCER"]}},
        "modules": {
            "CONSUMER": {
                "layer": "L1-core",
                "files": [
                    {
                        "path": "context/demo/spec/CONSUMER/CONSUMER-OVERVIEW.md",
                        "facet": "overview",
                        "depends_on": ["context/demo/spec/PRODUCER/PRODUCER-INTERFACE.md"],
                    }
                ],
            },
            "PRODUCER": {
                "layer": "L1-core",
                "files": [
                    {"path": "context/demo/spec/PRODUCER/PRODUCER-INTERFACE.md", "facet": "interface"}
                ],
            },
        },
    }
    workspace.write("context/demo/spec/CATALOG.yaml", core.dump_yaml(catalog))
    result, _ = _run(workspace, verb="waves", target="demo")
    assert result.data["waves"] == [
        {"wave": 1, "layer": "L1-core", "modules": ["CONSUMER", "PRODUCER"]}
    ]


def test_waves_orders_the_shared_layer_before_the_numbered_ones(workspace):
    catalog = _catalog()
    catalog["layers"]["LS-shared"] = {"modules": ["CONTRACT"]}
    catalog["modules"]["CONTRACT"] = {
        "layer": "LS-shared",
        "files": [{"path": "context/demo/spec/CONTRACT/CONTRACT-INTERFACE.md", "facet": "interface"}],
    }
    workspace.write("context/demo/spec/CATALOG.yaml", core.dump_yaml(catalog))
    result, _ = _run(workspace, verb="waves", target="demo")
    assert [wave["layer"] for wave in result.data["waves"]] == [
        "LS-shared",
        "L1-core",
        "L2-services",
    ]


def test_a_module_missing_from_its_layer_list_is_still_assigned(workspace):
    catalog = _catalog()
    catalog["layers"]["L1-core"]["modules"] = []
    workspace.write("context/demo/spec/CATALOG.yaml", core.dump_yaml(catalog))
    result, _ = _run(workspace, verb="waves", target="demo")
    assert result.data["waves"][0] == {"wave": 1, "layer": "L1-core", "modules": ["ALPHA"]}


def test_waves_reports_a_missing_catalog(workspace):
    result, code = _run(workspace, verb="waves", target="demo")
    assert code == 1
    assert [d.code for d in result.diagnostics] == [core.E_NO_SUCH_FILE]


# ---------------------------------------------------------------------------
# plan emit
# ---------------------------------------------------------------------------


def test_emit_writes_three_files_that_each_validate(workspace):
    draft = _draft(
        _story("01-01-demo-ALPHA"),
        _story("02-01-demo-BETA", module="BETA", wave=2, prerequisites=["01-01-demo-ALPHA"]),
        project_change="003",
    )
    result, code = _emit(workspace, "003-my-change", draft)
    assert code == 0, result.diagnostics
    assert result.data["written"] == [
        "context/project/plans/003-my-change/plan.yaml",
        "context/project/plans/003-my-change/state.yaml",
        "context/project/state.yaml",
    ]
    assert _validates(workspace, "context/project/plans/003-my-change/plan.yaml", "plan-graph") == []
    assert _validates(workspace, "context/project/plans/003-my-change/state.yaml", "plan-state") == []
    assert _validates(workspace, "context/project/state.yaml", "ledger") == []


def test_emit_seeds_every_story_pending_with_run_zero(workspace):
    draft = _draft(
        _story("01-01-demo-ALPHA"),
        _story("02-01-demo-BETA", module="BETA", wave=2),
        project_change="003",
    )
    _emit(workspace, "003-my-change", draft)
    state = core.load_yaml(workspace.path("context/project/plans/003-my-change/state.yaml"))
    assert state["run"] == 0
    assert state["status"] == "pending"
    assert [entry["status"] for entry in state["stories"].values()] == ["pending", "pending"]
    assert [entry["retries"] for entry in state["stories"].values()] == [0, 0]


def test_emit_derives_waves_and_parallel_groups_from_the_stories(workspace):
    draft = _draft(
        _story("01-01-demo-ALPHA"),
        _story("02-01-demo-BETA", module="BETA", wave=2),
        _story("02-02-demo-GAMMA", module="GAMMA", wave=2),
        project_change="003",
    )
    _emit(workspace, "003-my-change", draft)
    graph = core.load_yaml(workspace.path("context/project/plans/003-my-change/plan.yaml"))
    assert graph["waves"] == [
        {"wave": 1, "stories": ["01-01-demo-ALPHA"]},
        {"wave": 2, "stories": ["02-01-demo-BETA", "02-02-demo-GAMMA"]},
    ]
    assert graph["stories"]["01-01-demo-ALPHA"]["parallel_group"] == []
    assert graph["stories"]["02-01-demo-BETA"]["parallel_group"] == ["02-02-demo-GAMMA"]
    assert graph["stories"]["02-02-demo-GAMMA"]["file"] == "PLAN-02-02-demo-GAMMA.md"
    assert graph["repos"] == ["demo"]
    assert graph["generated"] == NOW


def test_emit_uses_schema_key_order(workspace):
    draft = _draft(_story("01-01-demo-ALPHA"), project_change="003")
    _emit(workspace, "003-my-change", draft)
    text = workspace.path("context/project/plans/003-my-change/plan.yaml").read_text()
    keys = [line.split(":", 1)[0] for line in text.split("\n") if line and not line[0].isspace() and not line.startswith("-")]
    assert keys == ["version", "plan_id", "generated", "type", "project_change", "repos", "waves", "stories"]


def test_project_change_is_emitted_as_a_quoted_string(workspace):
    draft = _draft(_story("01-01-demo-ALPHA"), project_change="010")
    _emit(workspace, "010-my-change", draft)
    text = workspace.path("context/project/plans/010-my-change/plan.yaml").read_text()
    assert "project_change: '010'\n" in text
    ledger_text = workspace.path("context/project/state.yaml").read_text()
    assert "project_change: '010'\n" in ledger_text
    # And it survives the round trip as a string, not YAML 1.1's octal 8.
    ledger = core.load_yaml(workspace.path("context/project/state.yaml"))
    assert ledger["plans"]["010-my-change"]["project_change"] == "010"


def test_a_numeric_project_change_is_normalised_to_a_padded_string(workspace):
    draft = _draft(_story("01-01-demo-ALPHA"), project_change=3)
    result, code = _emit(workspace, "003-my-change", draft)
    assert code == 0, result.diagnostics
    graph = core.load_yaml(workspace.path("context/project/plans/003-my-change/plan.yaml"))
    assert graph["project_change"] == "003"
    assert graph["type"] == "incremental"


def test_a_draft_with_no_project_change_is_a_full_plan(workspace):
    draft = _draft(_story("01-01-demo-ALPHA"))
    result, code = _emit(workspace, "000-initial", draft)
    assert code == 0, result.diagnostics
    graph = core.load_yaml(workspace.path("context/project/plans/000-initial/plan.yaml"))
    assert graph["type"] == "full"
    assert graph["project_change"] is None


def test_version_one_sits_at_the_ledger_top_level_never_inside_an_entry(workspace):
    draft = _draft(_story("01-01-demo-ALPHA"), project_change="003")
    _emit(workspace, "003-my-change", draft)
    ledger = core.load_yaml(workspace.path("context/project/state.yaml"))
    assert ledger["version"] == 1
    assert "version" not in ledger["plans"]["003-my-change"]
    assert set(ledger["plans"]["003-my-change"]) == {
        "status",
        "project_change",
        "plan_dir",
        "updated",
    }


def test_emitting_twice_with_the_same_now_is_byte_identical(workspace):
    draft = _draft(
        _story("01-01-demo-ALPHA"),
        _story("02-01-demo-BETA", module="BETA", wave=2),
        project_change="003",
    )
    _emit(workspace, "003-my-change", draft)
    first = {
        name: workspace.path(name).read_bytes()
        for name in (
            "context/project/plans/003-my-change/plan.yaml",
            "context/project/plans/003-my-change/state.yaml",
            "context/project/state.yaml",
        )
    }
    _emit(workspace, "003-my-change", draft)
    for name, content in first.items():
        assert workspace.path(name).read_bytes() == content


def test_emitting_a_second_plan_leaves_the_first_entry_byte_identical(workspace):
    _emit(workspace, "003-first", _draft(_story("01-01-demo-ALPHA"), project_change="003"))
    before = workspace.path("context/project/state.yaml").read_text()
    first_entry = before.split("  003-first:\n")[1]

    result, code = _emit(
        workspace, "004-second", _draft(_story("01-01-demo-BETA", module="BETA"), project_change="004")
    )
    assert code == 0, result.diagnostics
    after = workspace.path("context/project/state.yaml").read_text()
    assert "  003-first:\n" + first_entry in after
    ledger = core.load_yaml(workspace.path("context/project/state.yaml"))
    assert list(ledger["plans"]) == ["003-first", "004-second"]
    assert _validates(workspace, "context/project/state.yaml", "ledger") == []


def test_a_graph_that_would_fail_validation_is_refused_and_nothing_is_persisted(workspace):
    _emit(workspace, "003-first", _draft(_story("01-01-demo-ALPHA"), project_change="003"))
    ledger_before = workspace.path("context/project/state.yaml").read_bytes()

    story_id, story = _story("04-01-demo-BETA", module="BETA")
    del story["validation"]  # required by plan-graph
    result, code = _emit(workspace, "004-second", _draft((story_id, story), project_change="004"))

    assert code == 1
    assert [d.code for d in result.diagnostics] == [core.E_SCHEMA_INVALID]
    assert result.data["written"] == []
    assert not workspace.path("context/project/plans/004-second").exists()
    assert not workspace.path("context/project/plans/004-second/plan.yaml").exists()
    assert not workspace.path("context/project/plans/004-second/state.yaml").exists()
    assert workspace.path("context/project/state.yaml").read_bytes() == ledger_before


def test_a_story_id_that_is_not_story_shaped_is_refused(workspace):
    result, code = _emit(
        workspace, "003-my-change", _draft(_story("nope"), project_change="003")
    )
    assert code == 1
    assert result.data["written"] == []
    assert not workspace.path("context/project/plans/003-my-change").exists()


def test_declared_waves_that_disagree_with_the_stories_are_refused(workspace):
    draft = _draft(
        _story("01-01-demo-ALPHA"),
        _story("02-01-demo-BETA", module="BETA", wave=2),
        project_change="003",
        waves=[{"wave": 1, "stories": ["01-01-demo-ALPHA", "02-01-demo-BETA"]}],
    )
    result, code = _emit(workspace, "003-my-change", draft)
    assert code == 1
    assert [d.code for d in result.diagnostics] == [core.E_INVALID_STATE]
    assert not workspace.path("context/project/plans/003-my-change").exists()


def test_declared_wave_order_is_honoured_when_it_agrees(workspace):
    draft = _draft(
        _story("02-01-demo-BETA", module="BETA", wave=1),
        _story("02-02-demo-ALPHA", wave=1),
        project_change="003",
        waves=[{"wave": 1, "stories": ["02-02-demo-ALPHA", "02-01-demo-BETA"]}],
    )
    result, code = _emit(workspace, "003-my-change", draft)
    assert code == 0, result.diagnostics
    graph = core.load_yaml(workspace.path("context/project/plans/003-my-change/plan.yaml"))
    assert graph["waves"] == [{"wave": 1, "stories": ["02-02-demo-ALPHA", "02-01-demo-BETA"]}]


def test_a_draft_plan_id_that_disagrees_with_the_argument_is_refused(workspace):
    draft = _draft(_story("01-01-demo-ALPHA"), plan_id="009-other", project_change="003")
    result, code = _emit(workspace, "003-my-change", draft)
    assert code == 1
    assert [d.code for d in result.diagnostics] == [core.E_INVALID_STATE]
    assert not workspace.path("context/project/plans/003-my-change").exists()


def test_a_draft_that_is_not_json_is_refused(workspace):
    result, code = _run(
        workspace, verb="emit", plan_id="003-my-change", stdin=io.StringIO("not json")
    )
    assert code == 2
    assert [d.code for d in result.diagnostics] == [core.E_PARSE]
    assert not workspace.path("context/project/plans/003-my-change").exists()


def test_an_empty_draft_is_refused(workspace):
    result, code = _run(workspace, verb="emit", plan_id="003-my-change", stdin=io.StringIO("  "))
    assert code == 2
    assert [d.code for d in result.diagnostics] == [core.E_PARSE]


def test_a_draft_with_no_stories_is_refused(workspace):
    result, code = _emit(workspace, "003-my-change", {"type": "full"})
    assert code == 1
    assert [d.code for d in result.diagnostics] == [core.E_INVALID_STATE]
    assert not workspace.path("context/project/plans/003-my-change").exists()


def test_emit_rejects_a_plan_id_that_is_not_plan_shaped(workspace):
    result, code = _emit(workspace, "../escape", _draft(_story("01-01-demo-ALPHA")))
    assert code == 2
    assert [d.code for d in result.diagnostics] == [core.E_BAD_IDENT]


def test_an_emitted_plan_resolves_to_wave_one_with_every_story_pending(workspace):
    draft = _draft(
        _story("01-01-demo-ALPHA"),
        _story("02-01-demo-BETA", module="BETA", wave=2),
        project_change="003",
    )
    _emit(workspace, "003-my-change", draft)
    result, code = _run(workspace, verb="resolve")
    assert code == 0
    assert result.data["plan_id"] == "003-my-change"
    assert result.data["run"] == 0
    assert result.data["status"] == "pending"
    assert result.data["resume_wave"] == 1
    assert result.data["pending_stories"] == ["01-01-demo-ALPHA", "02-01-demo-BETA"]


# ---------------------------------------------------------------------------
# plan story-emit
# ---------------------------------------------------------------------------


def _emit_story(workspace, with_e2e=True, story_id="02-01-demo-BETA", plan_type="incremental"):
    _add_catalog(workspace, with_e2e=with_e2e)
    fields = {"project_change": "003"} if plan_type == "incremental" else {}
    draft = _draft(
        _story("01-01-demo-ALPHA", change_file="context/demo/changes/CHANGE-002-thing.md"),
        _story(
            "02-01-demo-BETA",
            module="BETA",
            wave=2,
            prerequisites=["01-01-demo-ALPHA"],
            change_file="context/demo/changes/CHANGE-002-thing.md",
        ),
        _story("02-02-demo-E2E", module="E2E", wave=2, prerequisites=["01-01-demo-ALPHA"]),
        **fields
    )
    plan_id = "003-my-change" if plan_type == "incremental" else "000-initial"
    result, code = _emit(workspace, plan_id, draft)
    assert code == 0, result.diagnostics
    result, code = _run(workspace, verb="story-emit", plan_id=plan_id, story_id=story_id)
    assert code == 0, result.diagnostics
    text = workspace.path("context/project/plans/%s/PLAN-%s.md" % (plan_id, story_id)).read_text()
    return result, text


def test_story_emit_injects_the_four_e2e_rules_at_both_markers(workspace):
    result, text = _emit_story(workspace, with_e2e=True)
    rules = _e2e_rules_from_standard()
    assert len(rules) == 4
    block = "\n".join(rules)
    assert text.count(block) == 2, text
    assert result.data["e2e_injections"] == 2

    # One copy under Post-Story Validation, one under Final Validation.
    post = text.split("## Post-Story Validation", 1)[1].split("## Final Validation", 1)[0]
    final = text.split("## Final Validation", 1)[1]
    assert block in post
    assert block in final
    # No injection marker survives into the story.
    assert "INJECT:E2E-HARD-RULES" not in text


def test_each_injected_rule_is_byte_identical_to_its_owning_section(workspace):
    _, text = _emit_story(workspace, with_e2e=True)
    for rule in _e2e_rules_from_standard():
        assert text.count(rule) == 2, rule


def test_the_injection_is_gated_on_the_catalog_naming_an_e2e_module(workspace):
    result, text = _emit_story(workspace, with_e2e=False)
    for rule in _e2e_rules_from_standard():
        assert rule not in text
    assert "INJECT:E2E-HARD-RULES" not in text
    assert result.data["e2e_injections"] == 0
    # The checkbox the marker is gated on stays, exactly as the template has it.
    assert "- [ ] E2E scenarios for this module pass (if E2E module exists in catalog)" in text


def test_the_final_validation_section_is_last_wave_only(workspace):
    _, last = _emit_story(workspace, story_id="02-01-demo-BETA")
    _, earlier = _emit_story(workspace, story_id="01-01-demo-ALPHA")
    assert "## Final Validation (last wave only)" in last
    assert "## Final Validation" not in earlier
    # The earlier wave still carries its own Post-Story rules.
    assert "\n".join(_e2e_rules_from_standard()) in earlier


def test_story_emit_fills_the_placeholders_the_graph_answers(workspace):
    _, text = _emit_story(workspace)
    assert text.startswith("<!-- depends-on: context/demo/spec/CATALOG.yaml -->\n")
    assert "# Story: BETA (L2-services) — demo" in text
    assert "**Repo:** demo" in text
    assert "**Wave:** 2 of 2" in text
    assert "**Prerequisites:** `PLAN-01-01-demo-ALPHA.md`" in text
    assert "**Parallel group:** `PLAN-02-02-demo-E2E.md`" in text
    assert "inside `repos/demo/`" in text
    assert "| `src/beta.py` |" in text
    assert "**Repo change file:** `context/demo/changes/CHANGE-002-thing.md`" in text
    assert "{repo}" not in text


def test_story_emit_drops_the_authoring_comments_but_keeps_the_front_matter(workspace):
    _, text = _emit_story(workspace)
    body = text.split("\n", 1)[1]
    assert "<!--" not in body
    assert "the repository directory under repos/" not in text
    assert "**Compliance Status:**" not in text


def test_a_full_plan_drops_the_incremental_change_scope_section(workspace):
    _, text = _emit_story(workspace, plan_type="full")
    assert "## Change Scope" not in text
    assert "END INCREMENTAL SECTION" not in text
    assert "## Context Files" in text


def test_story_emit_is_deterministic(workspace):
    _, first = _emit_story(workspace)
    _, second = _emit_story(workspace)
    assert first == second


def test_story_emit_reports_an_unknown_story(workspace):
    _add_catalog(workspace)
    _emit(workspace, "003-my-change", _draft(_story("01-01-demo-ALPHA"), project_change="003"))
    result, code = _run(
        workspace, verb="story-emit", plan_id="003-my-change", story_id="09-09-demo-NOPE"
    )
    assert code == 1
    assert [d.code for d in result.diagnostics] == [core.E_NOT_FOUND]


def test_story_emit_warns_but_still_renders_without_a_catalog(workspace):
    _emit(workspace, "003-my-change", _draft(_story("01-01-demo-ALPHA"), project_change="003"))
    result, code = _run(
        workspace, verb="story-emit", plan_id="003-my-change", story_id="01-01-demo-ALPHA"
    )
    assert code == 0
    assert [d.severity for d in result.diagnostics] == ["warning"]
    text = workspace.path("context/project/plans/003-my-change/PLAN-01-01-demo-ALPHA.md").read_text()
    assert "# Story: ALPHA — demo" in text
    for rule in _e2e_rules_from_standard():
        assert rule not in text


def test_story_emit_reports_a_missing_plan_graph(workspace):
    result, code = _run(
        workspace, verb="story-emit", plan_id="003-my-change", story_id="01-01-demo-ALPHA"
    )
    assert code == 1
    assert [d.code for d in result.diagnostics] == [core.E_NOT_FOUND]


# ---------------------------------------------------------------------------
# plan shards
# ---------------------------------------------------------------------------


def _change_document(scope="repo", repo="demo", paths=()):
    lines = [
        "<!-- change: 001 -->",
        "<!-- scope: %s -->" % scope,
        "<!-- repo: %s -->" % repo,
        "<!-- status: pending -->",
        "<!-- date: 2026-01-01 -->",
        "",
        "# CHANGE-001: a change",
        "",
        "## Affected Code Paths",
        "",
    ]
    lines.extend("- %s" % path for path in paths)
    lines.append("")
    return "\n".join(lines)


def test_shards_dedupes_repo_module_pairs_across_stories(workspace):
    _write_plan(
        workspace,
        "001-first",
        [(1, ["01-01-demo-ALPHA", "01-02-demo-ALPHA-TWO"]), (2, ["02-01-demo-BETA"])],
        overrides={"02-01-demo-BETA": {"module": "BETA"}},
    )
    result, code = _run(workspace, verb="shards", plan_id="001-first")
    assert code == 0
    change_conformance = [s for s in result.data["shards"] if s["shard"] == "change-conformance"]
    assert [s["id"] for s in change_conformance] == [
        "change-conformance-demo-ALPHA",
        "change-conformance-demo-BETA",
    ]


def test_shards_returns_the_full_entry_order(workspace):
    _write_plan(
        workspace,
        "001-first",
        [(1, ["01-01-demo-ALPHA"]), (2, ["02-01-demo-BETA"])],
        overrides={"02-01-demo-BETA": {"module": "BETA"}},
    )
    result, code = _run(workspace, verb="shards", plan_id="001-first")
    assert code == 0
    assert [s["shard"] for s in result.data["shards"]] == [
        "change-conformance",
        "change-conformance",
        "coupling",
    ]


def test_each_shard_id_form_is_byte_exact(workspace):
    _write_plan(workspace, "001-first", [(1, ["01-01-demo-ALPHA"])])
    result, code = _run(workspace, verb="shards", plan_id="001-first")
    assert code == 0
    assert [s["id"] for s in result.data["shards"]] == [
        "change-conformance-demo-ALPHA",
        "coupling-demo",
    ]


def test_every_shard_carries_exactly_the_five_datamodel_keys(workspace):
    _write_plan(workspace, "001-first", [(1, ["01-01-demo-ALPHA"])])
    result, _ = _run(workspace, verb="shards", plan_id="001-first")
    change_conformance, coupling = result.data["shards"]
    assert list(change_conformance) == ["shard", "id", "repo", "module", "interface"]
    assert change_conformance == {
        "shard": "change-conformance",
        "id": "change-conformance-demo-ALPHA",
        "repo": "demo",
        "module": "ALPHA",
        "interface": None,
    }
    assert list(coupling) == ["shard", "id", "repo", "module", "interface"]
    assert coupling == {
        "shard": "coupling",
        "id": "coupling-demo",
        "repo": "demo",
        "module": None,
        "interface": None,
    }


def test_no_shared_tree_yields_no_cross_repo_entries_and_no_diagnostic(workspace):
    workspace.write(
        "context/demo/changes/CHANGE-001-x.md", _change_document(scope="repo")
    )
    _write_plan(
        workspace,
        "001-first",
        [(1, ["01-01-demo-ALPHA"])],
        overrides={"01-01-demo-ALPHA": {"change_file": "context/demo/changes/CHANGE-001-x.md"}},
    )
    result, code = _run(workspace, verb="shards", plan_id="001-first")
    assert code == 0
    assert result.diagnostics == []
    assert [s for s in result.data["shards"] if s["shard"] == "cross-repo"] == []


def test_cross_repo_entries_on_the_multi_repo_fixture(multi_repo_workspace):
    _write_plan(
        multi_repo_workspace,
        "001-cascade",
        [(1, ["01-01-repo-a-WIDGET", "01-02-repo-b-WIDGET"])],
        overrides={
            "01-01-repo-a-WIDGET": {
                "repo": "repo-a",
                "module": "WIDGET",
                "change_file": "context/repo-a/changes/CHANGE-001-auth-consumer.md",
            },
            "01-02-repo-b-WIDGET": {
                "repo": "repo-b",
                "module": "WIDGET",
                "change_file": "context/repo-b/changes/CHANGE-001-auth-consumer.md",
            },
        },
        project_change="001",
    )
    result, code = _run(multi_repo_workspace, verb="shards", plan_id="001-cascade")
    assert code == 0
    cross_repo = [s for s in result.data["shards"] if s["shard"] == "cross-repo"]
    assert cross_repo == [
        {
            "shard": "cross-repo",
            "id": "cross-repo-AUTH",
            "repo": None,
            "module": None,
            "interface": "AUTH",
        }
    ]


def test_granularity_module_replaces_only_the_coupling_entries(workspace):
    _write_plan(
        workspace,
        "001-first",
        [(1, ["01-01-demo-ALPHA"]), (2, ["02-01-demo-BETA"])],
        overrides={"02-01-demo-BETA": {"module": "BETA"}},
    )
    default_result, default_code = _run(workspace, verb="shards", plan_id="001-first")
    module_result, module_code = _run(
        workspace, verb="shards", plan_id="001-first", granularity="module"
    )
    assert default_code == 0
    assert module_code == 0
    non_coupling_default = [s for s in default_result.data["shards"] if s["shard"] != "coupling"]
    non_coupling_module = [s for s in module_result.data["shards"] if s["shard"] != "coupling"]
    assert non_coupling_default == non_coupling_module
    assert [s["id"] for s in default_result.data["shards"] if s["shard"] == "coupling"] == [
        "coupling-demo"
    ]
    assert [s["id"] for s in module_result.data["shards"] if s["shard"] == "coupling"] == [
        "coupling-demo-ALPHA",
        "coupling-demo-BETA",
    ]
    for shard in module_result.data["shards"]:
        if shard["shard"] == "coupling":
            assert shard["module"] is not None


def test_shards_default_granularity_is_repo(workspace):
    _write_plan(workspace, "001-first", [(1, ["01-01-demo-ALPHA"])])
    result, code = _run(workspace, verb="shards", plan_id="001-first")
    assert code == 0
    assert result.data["granularity"] == "repo"


def test_shards_are_deterministic_across_two_runs(workspace):
    _write_plan(
        workspace,
        "001-first",
        [(1, ["01-01-demo-ALPHA"]), (2, ["02-01-demo-BETA"])],
        overrides={"02-01-demo-BETA": {"module": "BETA"}},
    )
    first, first_code = _run(workspace, verb="shards", plan_id="001-first")
    second, second_code = _run(workspace, verb="shards", plan_id="001-first")
    assert first_code == 0
    assert second_code == 0
    assert first.data == second.data


def test_shards_reports_an_unknown_plan_directory(workspace):
    result, code = _run(workspace, verb="shards", plan_id="009-nope")
    assert code == 1
    assert [d.code for d in result.diagnostics] == [core.E_NOT_FOUND]


def test_shards_rejects_a_plan_id_that_is_not_plan_shaped(workspace):
    result, code = _run(workspace, verb="shards", plan_id="../escape")
    assert code == 2
    assert [d.code for d in result.diagnostics] == [core.E_BAD_IDENT]


def test_shards_writes_nothing(workspace):
    _write_plan(workspace, "001-first", [(1, ["01-01-demo-ALPHA"])])
    plan_dir = workspace.path("context/project/plans/001-first")
    before = sorted(p.name for p in plan_dir.iterdir())
    _run(workspace, verb="shards", plan_id="001-first")
    after = sorted(p.name for p in plan_dir.iterdir())
    assert before == after


# ---------------------------------------------------------------------------
# plan slices -- read from the graph, or synthesized and never written back
# ---------------------------------------------------------------------------

#: A runnable acceptance step. At least one of these per slice is the obligation
#: JSON Schema cannot state, so every fixture slice carries one.
EXIT_STEP = {"kind": "exit-code", "command": "true", "description": "it runs"}
PROSE_STEP = {"kind": "prose", "description": "someone looks at it"}


def _slice(slice_id, stories, acceptance=None, name=None, behavior=None):
    return {
        "slice": slice_id,
        "name": name or "slice %s" % slice_id,
        "behavior": behavior or "behaviour %s runs end to end" % slice_id,
        "acceptance": list(acceptance if acceptance is not None else [EXIT_STEP]),
        "stories": list(stories),
    }


def _module_of(story_id):
    """``{WW}-{SS}-{repo}-{MODULE}`` -- the module a story id names."""
    return story_id.rsplit("-", 1)[-1]


def _write_sliced_plan(workspace, plan_id, waves, slices, slice_statuses=None):
    """A version-3 plan directory, written directly, with per-slice status.

    Each story takes its module from its own id, so a plan of two stories is a
    plan of two modules -- which is what makes the shard list observable.
    """
    overrides = {
        story_id: {"module": _module_of(story_id)}
        for _wave, story_ids in waves
        for story_id in story_ids
    }
    _write_plan(workspace, plan_id, waves, overrides=overrides)
    graph = core.load_yaml(workspace.path("context/project/plans/%s/plan.yaml" % plan_id))
    graph["version"] = 3
    graph["slices"] = [dict(entry) for entry in slices]
    for entry in slices:
        for story_id in entry["stories"]:
            graph["stories"][story_id]["slice"] = entry["slice"]
    workspace.write("context/project/plans/%s/plan.yaml" % plan_id, core.dump_yaml(graph))

    state = core.load_yaml(workspace.path("context/project/plans/%s/state.yaml" % plan_id))
    state["version"] = 3
    state["slices"] = [
        {"slice": entry["slice"], "status": (slice_statuses or {}).get(entry["slice"], "pending")}
        for entry in slices
    ]
    workspace.write("context/project/plans/%s/state.yaml" % plan_id, core.dump_yaml(state))
    assert core.validate_against(core.load_schema("plan-graph"), graph) == []
    assert core.validate_against(core.load_schema("plan-state"), state) == []


def _legacy_plan_with_final(workspace, plan_id, waves, final):
    """A version-2 plan whose last-wave story carries ``validation.final``."""
    _write_plan(workspace, plan_id, waves)
    path = workspace.path("context/project/plans/%s/plan.yaml" % plan_id)
    graph = core.load_yaml(path)
    last = waves[-1][1][-1]
    graph["stories"][last]["validation"]["final"] = list(final)
    workspace.write("context/project/plans/%s/plan.yaml" % plan_id, core.dump_yaml(graph))


def test_slices_synthesizes_exactly_one_spanning_every_wave_on_a_legacy_graph(workspace):
    _legacy_plan_with_final(
        workspace,
        "001-first",
        [(1, ["01-01-demo-ALPHA"]), (2, ["02-01-demo-BETA"])],
        [EXIT_STEP],
    )
    result, code = _run(workspace, verb="slices", plan_id="001-first")

    assert code == 0 and result.ok
    assert result.data["synthesized"] is True
    assert len(result.data["slices"]) == 1
    only = result.data["slices"][0]
    assert only["slice"] == "00"
    assert only["stories"] == ["01-01-demo-ALPHA", "02-01-demo-BETA"]
    assert only["status"] == "pending"


def test_the_synthesized_slice_takes_its_acceptance_from_validation_final(workspace):
    _legacy_plan_with_final(
        workspace, "001-first", [(1, ["01-01-demo-ALPHA"])], [EXIT_STEP, PROSE_STEP]
    )
    result, _code = _run(workspace, verb="slices", plan_id="001-first")
    assert result.data["slices"][0]["acceptance"] == [EXIT_STEP, PROSE_STEP]


def test_synthesis_leaves_the_graph_byte_identical_on_disk(workspace):
    """A legacy graph is not silently upgraded by being looked at."""
    _legacy_plan_with_final(workspace, "001-first", [(1, ["01-01-demo-ALPHA"])], [EXIT_STEP])
    graph_path = workspace.path("context/project/plans/001-first/plan.yaml")
    state_path = workspace.path("context/project/plans/001-first/state.yaml")
    graph_before, state_before = graph_path.read_bytes(), state_path.read_bytes()

    _run(workspace, verb="slices", plan_id="001-first")
    _run(workspace, verb="slices", plan_id="001-first")

    assert graph_path.read_bytes() == graph_before
    assert state_path.read_bytes() == state_before


def test_a_legacy_graph_with_no_final_validation_still_synthesizes_one_slice(workspace):
    _write_plan(workspace, "001-first", [(1, ["01-01-demo-ALPHA"])])
    result, code = _run(workspace, verb="slices", plan_id="001-first")
    assert code == 0
    assert [entry["slice"] for entry in result.data["slices"]] == ["00"]
    assert result.data["slices"][0]["acceptance"] == []


def test_slices_returns_the_graphs_own_slices_on_a_version_three_graph(workspace):
    _write_sliced_plan(
        workspace,
        "001-first",
        [(1, ["01-01-demo-ALPHA"]), (2, ["02-01-demo-BETA"])],
        [_slice("00", ["01-01-demo-ALPHA"]), _slice("01", ["02-01-demo-BETA"])],
        slice_statuses={"00": "applied"},
    )
    result, code = _run(workspace, verb="slices", plan_id="001-first")

    assert code == 0 and result.data["synthesized"] is False
    assert [entry["slice"] for entry in result.data["slices"]] == ["00", "01"]
    assert [entry["status"] for entry in result.data["slices"]] == ["applied", "pending"]


def test_a_slice_entry_carries_every_documented_field(workspace):
    _write_sliced_plan(
        workspace, "001-first", [(1, ["01-01-demo-ALPHA"])], [_slice("00", ["01-01-demo-ALPHA"])]
    )
    result, _code = _run(workspace, verb="slices", plan_id="001-first")
    assert list(result.data["slices"][0]) == [
        "slice",
        "name",
        "behavior",
        "acceptance",
        "stories",
        "status",
    ]


def test_a_slice_status_is_read_not_derived_from_its_stories(workspace):
    """Every story applied, acceptance failed: `failed`, which no function of
    story statuses can produce."""
    _write_sliced_plan(
        workspace,
        "001-first",
        [(1, ["01-01-demo-ALPHA"])],
        [_slice("00", ["01-01-demo-ALPHA"])],
        slice_statuses={"00": "failed"},
    )
    state_path = workspace.path("context/project/plans/001-first/state.yaml")
    state = core.load_yaml(state_path)
    state["stories"]["01-01-demo-ALPHA"]["status"] = "applied"
    workspace.write("context/project/plans/001-first/state.yaml", core.dump_yaml(state))

    result, _code = _run(workspace, verb="slices", plan_id="001-first")
    assert result.data["slices"][0]["status"] == "failed"


def test_slices_reports_a_plan_that_does_not_exist(workspace):
    result, code = _run(workspace, verb="slices", plan_id="009-nope")
    assert code == 1
    assert [d.code for d in result.diagnostics] == [core.E_NOT_FOUND]


# ---------------------------------------------------------------------------
# resume_slice -- added alongside resume_wave, which is never renamed
# ---------------------------------------------------------------------------


def test_resume_slice_is_the_first_slice_that_is_not_applied(workspace):
    _write_sliced_plan(
        workspace,
        "001-first",
        [(1, ["01-01-demo-ALPHA"]), (2, ["02-01-demo-BETA"])],
        [_slice("00", ["01-01-demo-ALPHA"]), _slice("01", ["02-01-demo-BETA"])],
        slice_statuses={"00": "applied"},
    )
    result, _code = _run(workspace, verb="resolve", plan_id="001-first")
    assert result.data["resume_slice"] == "01"


def test_resume_slice_is_null_when_every_slice_is_applied(workspace):
    _write_sliced_plan(
        workspace,
        "001-first",
        [(1, ["01-01-demo-ALPHA"])],
        [_slice("00", ["01-01-demo-ALPHA"])],
        slice_statuses={"00": "applied"},
    )
    result, _code = _run(workspace, verb="resolve", plan_id="001-first")
    assert result.data["resume_slice"] is None


def test_resume_slice_is_zero_zero_on_a_legacy_graph(workspace):
    _write_plan(workspace, "001-first", [(1, ["01-01-demo-ALPHA"]), (2, ["02-01-demo-BETA"])])
    result, _code = _run(workspace, verb="resolve", plan_id="001-first")
    assert result.data["resume_slice"] == "00"
    # And the wave axis is untouched: the field was added, never renamed.
    assert result.data["resume_wave"] == 1


def test_a_failed_slice_is_the_resume_point_too(workspace):
    _write_sliced_plan(
        workspace,
        "001-first",
        [(1, ["01-01-demo-ALPHA"]), (2, ["02-01-demo-BETA"])],
        [_slice("00", ["01-01-demo-ALPHA"]), _slice("01", ["02-01-demo-BETA"])],
        slice_statuses={"00": "failed"},
    )
    result, _code = _run(workspace, verb="resolve", plan_id="001-first")
    assert result.data["resume_slice"] == "00"


# ---------------------------------------------------------------------------
# plan emit -- the two slice refusals, and what a sliced emission writes
# ---------------------------------------------------------------------------


def _sliced_draft(slices, **fields):
    """A two-layer draft: ALPHA in L1-core, BETA in L2-services."""
    draft = _draft(
        _story("01-01-demo-ALPHA", module="ALPHA", wave=1),
        _story("02-01-demo-BETA", module="BETA", wave=2),
        **fields,
    )
    draft["slices"] = slices
    return draft


def test_emit_writes_a_version_three_graph_from_a_sliced_draft(workspace):
    _add_catalog(workspace)
    slices = [_slice("00", ["01-01-demo-ALPHA", "02-01-demo-BETA"])]
    result, code = _emit(workspace, "001-first", _sliced_draft(slices))
    assert code == 0, [d.render() for d in result.diagnostics]

    graph = core.load_yaml(workspace.path("context/project/plans/001-first/plan.yaml"))
    assert graph["version"] == 3
    assert [entry["slice"] for entry in graph["slices"]] == ["00"]
    # Each story carries the back-reference, derived from the slice listing it.
    assert graph["stories"]["01-01-demo-ALPHA"]["slice"] == "00"
    assert graph["stories"]["02-01-demo-BETA"]["slice"] == "00"
    assert _validates(workspace, "context/project/plans/001-first/plan.yaml", "plan-graph") == []


def test_emit_seeds_every_slice_pending_and_the_ledger_counters(workspace):
    _add_catalog(workspace)
    # One slice, spanning both layers -- the walking-skeleton rule holds, and
    # the counters are what this case is about.
    slices = [_slice("00", ["01-01-demo-ALPHA", "02-01-demo-BETA"])]

    result, code = _emit(workspace, "001-first", _sliced_draft(slices))
    assert code == 0, [d.render() for d in result.diagnostics]

    state = core.load_yaml(workspace.path("context/project/plans/001-first/state.yaml"))
    assert state["version"] == 3
    assert state["slices"] == [{"slice": "00", "status": "pending"}]

    ledger = core.load_yaml(workspace.path("context/project/state.yaml"))
    assert ledger["plans"]["001-first"]["slices_total"] == 1
    assert ledger["plans"]["001-first"]["slices_applied"] == 0


def test_a_pre_slice_emission_carries_no_slices_and_no_ledger_counters(workspace):
    _emit(workspace, "001-first", _draft(_story("01-01-demo-ALPHA")))
    graph = core.load_yaml(workspace.path("context/project/plans/001-first/plan.yaml"))
    state = core.load_yaml(workspace.path("context/project/plans/001-first/state.yaml"))
    ledger = core.load_yaml(workspace.path("context/project/state.yaml"))
    assert graph["version"] == 2 and "slices" not in graph
    assert "slices" not in state
    assert "slices_total" not in ledger["plans"]["001-first"]
    assert "slices_applied" not in ledger["plans"]["001-first"]


def test_emit_refuses_a_slice_whose_acceptance_is_entirely_prose(workspace):
    _add_catalog(workspace)
    slices = [
        _slice("00", ["01-01-demo-ALPHA", "02-01-demo-BETA"], acceptance=[PROSE_STEP, PROSE_STEP])
    ]
    result, code = _emit(workspace, "001-first", _sliced_draft(slices))

    assert code == 1 and not result.ok
    assert [d.code for d in result.diagnostics] == [core.E_INVALID_STATE]
    assert result.data["written"] == []
    assert not workspace.path("context/project/plans/001-first").exists()


def test_emit_refuses_a_first_slice_that_misses_a_layer_the_plan_touches(workspace):
    """Slice 00 is the walking skeleton, not merely the first slice listed."""
    _add_catalog(workspace)
    slices = [
        _slice("00", ["01-01-demo-ALPHA"]),
        _slice("01", ["02-01-demo-BETA"]),
    ]
    result, code = _emit(workspace, "001-first", _sliced_draft(slices))

    assert code == 1 and not result.ok
    assert [d.code for d in result.diagnostics] == [core.E_INVALID_STATE]
    assert "L2-services" in result.diagnostics[0].message
    assert not workspace.path("context/project/plans/001-first").exists()


def test_emit_accepts_a_first_slice_that_reaches_every_layer(workspace):
    _add_catalog(workspace)
    slices = [
        _slice("00", ["01-01-demo-ALPHA", "02-01-demo-BETA"]),
    ]
    result, code = _emit(workspace, "001-first", _sliced_draft(slices))
    assert code == 0, [d.render() for d in result.diagnostics]


def test_emit_refuses_a_draft_leaving_a_story_in_no_slice(workspace):
    _add_catalog(workspace)
    slices = [_slice("00", ["01-01-demo-ALPHA"])]
    draft = _sliced_draft(slices)
    result, code = _emit(workspace, "001-first", draft)
    assert code == 1
    assert [d.code for d in result.diagnostics] == [core.E_INVALID_STATE]
    assert "belongs to no slice" in result.diagnostics[0].message
    assert not workspace.path("context/project/plans/001-first").exists()


def test_emit_refuses_a_draft_putting_a_story_in_two_slices(workspace):
    _add_catalog(workspace)
    slices = [
        _slice("00", ["01-01-demo-ALPHA", "02-01-demo-BETA"]),
        _slice("01", ["02-01-demo-BETA"]),
    ]
    result, code = _emit(workspace, "001-first", _sliced_draft(slices))
    assert code == 1
    assert any("more than one slice" in d.message for d in result.diagnostics)
    assert not workspace.path("context/project/plans/001-first").exists()


def test_emit_refuses_a_duplicate_slice_id(workspace):
    _add_catalog(workspace)
    slices = [
        _slice("00", ["01-01-demo-ALPHA", "02-01-demo-BETA"]),
        _slice("00", ["02-01-demo-BETA"]),
    ]
    result, code = _emit(workspace, "001-first", _sliced_draft(slices))
    assert code == 1
    assert [d.code for d in result.diagnostics] == [core.E_INVALID_STATE]


def test_emit_rejects_a_slice_id_that_is_not_two_digits(workspace):
    _add_catalog(workspace)
    slices = [_slice("zero", ["01-01-demo-ALPHA", "02-01-demo-BETA"])]
    result, code = _emit(workspace, "001-first", _sliced_draft(slices))
    assert code == 2
    assert [d.code for d in result.diagnostics] == [core.E_BAD_IDENT]


def test_the_layer_rule_does_not_fire_when_no_catalog_names_a_layer(workspace):
    """No catalog means no layer to compare against, not an invented refusal."""
    slices = [_slice("00", ["01-01-demo-ALPHA"]), _slice("01", ["02-01-demo-BETA"])]
    result, code = _emit(workspace, "001-first", _sliced_draft(slices))
    assert code == 0, [d.render() for d in result.diagnostics]


# ---------------------------------------------------------------------------
# plan reslice -- the loop's backward edge, and what it will not rewrite
# ---------------------------------------------------------------------------


def _reslice(workspace, plan_id, drafts):
    return _run(
        workspace,
        verb="reslice",
        plan_id=plan_id,
        stdin=io.StringIO(json.dumps(drafts)),
    )


def _digests(workspace, plan_id):
    directory = workspace.path("context/project/plans/%s" % plan_id)
    return {path.name: path.read_bytes() for path in sorted(directory.iterdir())}


def test_reslice_rewrites_the_outstanding_slices(workspace):
    _write_sliced_plan(
        workspace,
        "001-first",
        [(1, ["01-01-demo-ALPHA"]), (2, ["02-01-demo-BETA"])],
        [_slice("00", ["01-01-demo-ALPHA", "02-01-demo-BETA"])],
    )
    drafts = [_slice("00", ["01-01-demo-ALPHA"]), _slice("01", ["02-01-demo-BETA"])]
    result, code = _reslice(workspace, "001-first", drafts)

    assert code == 0, [d.render() for d in result.diagnostics]
    graph = core.load_yaml(workspace.path("context/project/plans/001-first/plan.yaml"))
    assert [entry["slice"] for entry in graph["slices"]] == ["00", "01"]
    assert graph["stories"]["02-01-demo-BETA"]["slice"] == "01"
    state = core.load_yaml(workspace.path("context/project/plans/001-first/state.yaml"))
    assert state["slices"] == [
        {"slice": "00", "status": "pending"},
        {"slice": "01", "status": "pending"},
    ]
    assert _validates(workspace, "context/project/plans/001-first/plan.yaml", "plan-graph") == []
    assert _validates(workspace, "context/project/plans/001-first/state.yaml", "plan-state") == []


def test_reslice_keeps_what_an_outstanding_slice_already_recorded(workspace):
    _write_sliced_plan(
        workspace,
        "001-first",
        [(1, ["01-01-demo-ALPHA"]), (2, ["02-01-demo-BETA"])],
        [_slice("00", ["01-01-demo-ALPHA"]), _slice("01", ["02-01-demo-BETA"])],
        slice_statuses={"00": "failed"},
    )
    drafts = [
        _slice("00", ["01-01-demo-ALPHA"]),
        _slice("01", ["02-01-demo-BETA"], name="recut"),
    ]
    result, code = _reslice(workspace, "001-first", drafts)
    assert code == 0, [d.render() for d in result.diagnostics]
    state = core.load_yaml(workspace.path("context/project/plans/001-first/state.yaml"))
    assert state["slices"][0] == {"slice": "00", "status": "failed"}


def test_reslice_upgrades_a_legacy_graph_only_when_asked_to(workspace):
    _legacy_plan_with_final(workspace, "001-first", [(1, ["01-01-demo-ALPHA"])], [EXIT_STEP])
    result, code = _reslice(workspace, "001-first", [_slice("00", ["01-01-demo-ALPHA"])])
    assert code == 0, [d.render() for d in result.diagnostics]
    graph = core.load_yaml(workspace.path("context/project/plans/001-first/plan.yaml"))
    assert graph["version"] == 3


@pytest.mark.parametrize(
    "drafts,why",
    [
        ([], "drops"),
        ([_slice("01", ["02-01-demo-BETA"])], "renumbers"),
        (
            [_slice("01", ["02-01-demo-BETA"]), _slice("00", ["01-01-demo-ALPHA"])],
            "reorders",
        ),
        (
            [
                _slice("00", ["01-01-demo-ALPHA"], name="renamed"),
                _slice("01", ["02-01-demo-BETA"]),
            ],
            "alters the name",
        ),
        (
            [
                _slice("00", ["01-01-demo-ALPHA"], acceptance=[EXIT_STEP, PROSE_STEP]),
                _slice("01", ["02-01-demo-BETA"]),
            ],
            "alters the acceptance",
        ),
        (
            [
                _slice("00", ["01-01-demo-ALPHA", "02-01-demo-BETA"]),
            ],
            "alters the stories",
        ),
    ],
)
def test_reslice_refuses_to_touch_an_applied_slice(workspace, drafts, why):
    """What has been delivered is fixed; what is merely planned is not."""
    _write_sliced_plan(
        workspace,
        "001-first",
        [(1, ["01-01-demo-ALPHA"]), (2, ["02-01-demo-BETA"])],
        [_slice("00", ["01-01-demo-ALPHA"]), _slice("01", ["02-01-demo-BETA"])],
        slice_statuses={"00": "applied"},
    )
    before = _digests(workspace, "001-first")

    result, code = _reslice(workspace, "001-first", drafts)

    assert code == 1 and not result.ok, why
    assert result.data["written"] == []
    assert _digests(workspace, "001-first") == before


def test_reslice_allows_an_applied_slice_to_be_carried_through_unchanged(workspace):
    _write_sliced_plan(
        workspace,
        "001-first",
        [(1, ["01-01-demo-ALPHA"]), (2, ["02-01-demo-BETA"])],
        [_slice("00", ["01-01-demo-ALPHA"]), _slice("01", ["02-01-demo-BETA"])],
        slice_statuses={"00": "applied"},
    )
    drafts = [
        _slice("00", ["01-01-demo-ALPHA"]),
        _slice("01", ["02-01-demo-BETA"], name="recut", behavior="a better cut"),
    ]
    result, code = _reslice(workspace, "001-first", drafts)
    assert code == 0, [d.render() for d in result.diagnostics]
    graph = core.load_yaml(workspace.path("context/project/plans/001-first/plan.yaml"))
    assert graph["slices"][1]["name"] == "recut"
    state = core.load_yaml(workspace.path("context/project/plans/001-first/state.yaml"))
    assert state["slices"][0] == {"slice": "00", "status": "applied"}


@pytest.mark.parametrize(
    "drafts",
    [
        [_slice("00", ["01-01-demo-ALPHA"])],  # BETA left in no slice
        [
            _slice("00", ["01-01-demo-ALPHA", "02-01-demo-BETA"]),
            _slice("01", ["02-01-demo-BETA"]),
        ],  # BETA in two
    ],
)
def test_reslice_refuses_a_story_in_no_slice_or_in_two(workspace, drafts):
    _write_sliced_plan(
        workspace,
        "001-first",
        [(1, ["01-01-demo-ALPHA"]), (2, ["02-01-demo-BETA"])],
        [_slice("00", ["01-01-demo-ALPHA", "02-01-demo-BETA"])],
    )
    before = _digests(workspace, "001-first")

    result, code = _reslice(workspace, "001-first", drafts)

    assert code == 1 and not result.ok
    assert result.data["written"] == []
    assert _digests(workspace, "001-first") == before


def test_reslice_refuses_a_slice_with_no_runnable_acceptance(workspace):
    _write_sliced_plan(
        workspace,
        "001-first",
        [(1, ["01-01-demo-ALPHA"])],
        [_slice("00", ["01-01-demo-ALPHA"])],
    )
    before = _digests(workspace, "001-first")
    result, code = _reslice(
        workspace, "001-first", [_slice("00", ["01-01-demo-ALPHA"], acceptance=[PROSE_STEP])]
    )
    assert code == 1 and not result.ok
    assert _digests(workspace, "001-first") == before


def test_reslice_refuses_a_draft_that_is_not_an_array(workspace):
    _write_sliced_plan(
        workspace, "001-first", [(1, ["01-01-demo-ALPHA"])], [_slice("00", ["01-01-demo-ALPHA"])]
    )
    before = _digests(workspace, "001-first")
    result, code = _reslice(workspace, "001-first", {"slice": "00"})
    assert code == 2 and not result.ok
    assert [d.code for d in result.diagnostics] == [core.E_PARSE]
    assert _digests(workspace, "001-first") == before


def test_reslice_reports_a_plan_with_no_state_file(workspace):
    _write_sliced_plan(
        workspace, "001-first", [(1, ["01-01-demo-ALPHA"])], [_slice("00", ["01-01-demo-ALPHA"])]
    )
    workspace.path("context/project/plans/001-first/state.yaml").unlink()
    result, code = _reslice(workspace, "001-first", [_slice("00", ["01-01-demo-ALPHA"])])
    assert code == 1
    assert [d.code for d in result.diagnostics] == [core.E_NOT_FOUND]


def test_reslice_never_writes_the_ledger(workspace):
    """`state set-slice` is the only writer of the ledger's slice counters."""
    _write_sliced_plan(
        workspace,
        "001-first",
        [(1, ["01-01-demo-ALPHA"]), (2, ["02-01-demo-BETA"])],
        [_slice("00", ["01-01-demo-ALPHA", "02-01-demo-BETA"])],
    )
    _write_ledger(workspace, [("001-first", "in-progress")])
    before = workspace.path("context/project/state.yaml").read_bytes()

    _reslice(
        workspace,
        "001-first",
        [_slice("00", ["01-01-demo-ALPHA"]), _slice("01", ["02-01-demo-BETA"])],
    )
    assert workspace.path("context/project/state.yaml").read_bytes() == before


# ---------------------------------------------------------------------------
# plan shards --slice
# ---------------------------------------------------------------------------


def test_shards_restricts_the_list_to_what_a_slice_shipped(workspace):
    _write_sliced_plan(
        workspace,
        "001-first",
        [(1, ["01-01-demo-ALPHA"]), (2, ["02-01-demo-BETA"])],
        [_slice("00", ["01-01-demo-ALPHA"]), _slice("01", ["02-01-demo-BETA"])],
    )
    result, code = _run(workspace, verb="shards", plan_id="001-first", slice="01")
    assert code == 0
    assert [shard["id"] for shard in result.data["shards"]] == [
        "change-conformance-demo-BETA",
        "coupling-demo",
    ]
    assert result.data["slice"] == "01"


def test_shards_without_a_slice_still_spans_the_whole_plan(workspace):
    _write_sliced_plan(
        workspace,
        "001-first",
        [(1, ["01-01-demo-ALPHA"]), (2, ["02-01-demo-BETA"])],
        [_slice("00", ["01-01-demo-ALPHA"]), _slice("01", ["02-01-demo-BETA"])],
    )
    result, _code = _run(workspace, verb="shards", plan_id="001-first")
    assert [shard["id"] for shard in result.data["shards"]] == [
        "change-conformance-demo-ALPHA",
        "change-conformance-demo-BETA",
        "coupling-demo",
    ]


def test_shards_reports_a_slice_the_graph_does_not_declare(workspace):
    _write_sliced_plan(
        workspace, "001-first", [(1, ["01-01-demo-ALPHA"])], [_slice("00", ["01-01-demo-ALPHA"])]
    )
    result, code = _run(workspace, verb="shards", plan_id="001-first", slice="07")
    assert code == 1
    assert [d.code for d in result.diagnostics] == [core.E_NOT_FOUND]


def test_the_synthesized_slice_is_addressable_by_shards_on_a_legacy_graph(workspace):
    _write_plan(workspace, "001-first", [(1, ["01-01-demo-ALPHA"])])
    result, code = _run(workspace, verb="shards", plan_id="001-first", slice="00")
    assert code == 0
    assert [shard["id"] for shard in result.data["shards"]] == [
        "change-conformance-demo-ALPHA",
        "coupling-demo",
    ]
