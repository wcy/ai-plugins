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
from tools import core, plan

STANDARD_SPEC = PLUGIN_ROOT / "shared" / "STANDARD-SPEC.md"


# ---------------------------------------------------------------------------
# Helpers -- every fixture is synthetic and lives in tmp_path
# ---------------------------------------------------------------------------


def _run(workspace, **fields):
    """Call the group directly and return ``(result, exit_code)``."""
    result = plan.run(workspace.args(**fields), workspace.ws)
    return result, core.exit_code(result)


def _index(number, slug, status, change_files=()):
    rows = "".join(
        "| `demo` | `%s` | a change |\n" % path for path in change_files
    )
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


def _add_index(workspace, number, slug, status, change_files=()):
    return workspace.write(
        "context/project/changes/PROJECT-CHANGE-%s-%s.md" % (number, slug),
        _index(number, slug, status, change_files),
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


def _write_plan(workspace, plan_id, waves, statuses=None, run=0, status="pending"):
    """A plan directory with a graph and a state file, written directly."""
    graph_stories = {}
    state_stories = {}
    for wave, story_ids in waves:
        for story_id in story_ids:
            graph_stories[story_id] = dict(_story(story_id, wave=wave)[1])
            graph_stories[story_id]["file"] = "PLAN-%s.md" % story_id
            state_stories[story_id] = {
                "repo": "demo",
                "wave": wave,
                "status": (statuses or {}).get(story_id, "pending"),
                "retries": 0,
            }
    graph = {
        "version": 2,
        "plan_id": plan_id,
        "type": "full",
        "repos": ["demo"],
        "waves": [{"wave": wave, "stories": list(story_ids)} for wave, story_ids in waves],
        "stories": graph_stories,
    }
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


def test_the_group_imports_no_sibling_command_module():
    """Change files are read through core's front-matter helper, never through
    ``tools/change.py`` -- a concurrent sibling this group must not depend on."""
    source = (PLUGIN_ROOT / "tools" / "plan.py").read_text(encoding="utf-8")
    assert "tools.change" not in source
    assert "import change" not in source
    assert "load_front_matter" in source


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
