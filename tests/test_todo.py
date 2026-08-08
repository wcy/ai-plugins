"""The ``todo`` command group -- the deferral list's only writer.

What ``TOOLS-TESTING.md`` §"Conformance to the standards" asks of this suite:
*``todo add`` refuses each field violation in turn -- bad ``Run``, bad ``Kind``,
bad rating, unresolvable ``Origin`` -- and **writes nothing on any of them**,
asserted against a workspace with no ``TODO.md``, so a refused first entry leaves
no empty file. ``todo remove`` refuses an unmatched title.* Both shapes are here,
and the no-file-left-behind property is asserted on the filesystem rather than
on the envelope, because an empty list and a refusal are indistinguishable to a
later reader once the file exists.

The enums are the other half. ``STANDARD-TODO.md`` owns the field set and both
closed enums, and the group reads them rather than restating them, so
:func:`test_the_field_table_comes_from_the_standard_not_from_the_code` derives
against a *synthetic* standard: a member added there and nowhere else has to
show up here, which is the only assertion that can tell reading from restating
apart.

The group is imported and called directly, never through ``argv``;
``tests/test_cli.py`` covers the delivered surface.
"""

from pathlib import Path

import pytest

from conftest import NOW
from tools import core, todo

#: A conforming set of ``add`` arguments, keyed by argparse destination.
FIELDS = {
    "run": "/mfix",
    "kind": "spec-drift",
    "origin": "CHANGE-001",
    "priority": "medium",
    "risk_if_unfixed": "low",
    "regression_risk": "low",
    "cost": "low",
    "context": "`check todo` in plugins/metacoder/tools/check.py has no rule for this yet.",
}

TITLE = "check handoff reports no per-repo counts"

#: The two closed enums and the four ratings, exactly as ``STANDARD-TODO.md``
#: §"Entry schema" tables them. Restated *here* on purpose: the code derives
#: them, and a test that derived them too would agree with any derivation.
DOCUMENTED_RUNS = ("/mquick", "/mreq", "/mspec", "/mfix", "/mreverse", "human")
DOCUMENTED_KINDS = (
    "logic",
    "edge-case",
    "error-handling",
    "build",
    "compile",
    "packaging",
    "deployment",
    "test-coverage",
    "spec-drift",
    "architecture",
    "performance",
    "security",
)
DOCUMENTED_RATINGS = ("high", "medium", "low")
DOCUMENTED_FIELDS = (
    "Run",
    "Kind",
    "Origin",
    "Raised",
    "Priority",
    "Risk-if-unfixed",
    "Regression-risk",
    "Cost",
    "Context",
)
RATING_FIELDS = ("Priority", "Risk-if-unfixed", "Regression-risk", "Cost")


# ---------------------------------------------------------------------------
# Fixture builders -- synthetic, in tmp_path, like every other suite here
# ---------------------------------------------------------------------------


def _change(workspace, number="001", slug="demo", target="demo"):
    """A repo change document an ``--origin`` can resolve to."""
    return workspace.write(
        "context/%s/changes/CHANGE-%s-%s.md" % (target, number, slug),
        "<!-- change: %s -->\n"
        "<!-- scope: repo -->\n"
        "<!-- repo: %s -->\n"
        "<!-- status: pending -->\n"
        "<!-- date: 2026-01-01 -->\n"
        "\n# CHANGE-%s: %s\n" % (number, target, number, slug),
    )


def _index(workspace, number="001", slug="demo"):
    return workspace.write(
        "context/project/changes/PROJECT-CHANGE-%s-%s.md" % (number, slug),
        "<!-- project-change: %s -->\n"
        "<!-- scope: repo -->\n"
        "<!-- status: pending -->\n"
        "<!-- date: 2026-01-01 -->\n"
        "\n# PROJECT-CHANGE-%s: %s\n" % (number, number, slug),
    )


def _todo_file(workspace):
    return workspace.path(todo.TODO_REL)


def _add(workspace, title=TITLE, now=NOW, **overrides):
    """Call ``todo add`` directly and return ``(result, exit_code)``."""
    fields = dict(FIELDS)
    fields.update(overrides)
    args = workspace.args(verb="add", title=title, now=now, **fields)
    result = todo.run(args, workspace.ws)
    return result, core.exit_code(result)


def _remove(workspace, title=TITLE, now=NOW):
    args = workspace.args(verb="remove", title=title, now=now)
    result = todo.run(args, workspace.ws)
    return result, core.exit_code(result)


def _list(workspace, run=None, kind=None):
    args = workspace.args(verb="list", run=run, kind=kind)
    result = todo.run(args, workspace.ws)
    return result, core.exit_code(result)


def _codes(result):
    return [item.code for item in result.diagnostics]


# ---------------------------------------------------------------------------
# The standard owns the field set and the enums
# ---------------------------------------------------------------------------


def test_the_field_table_is_the_nine_documented_fields_in_order():
    assert tuple(item.name for item in todo.field_specs()) == DOCUMENTED_FIELDS


def test_every_closed_enum_is_read_from_the_standard():
    enums = {item.name: item.enum for item in todo.field_specs()}
    assert enums["Run"] == DOCUMENTED_RUNS
    assert enums["Kind"] == DOCUMENTED_KINDS
    for field in RATING_FIELDS:
        assert enums[field] == DOCUMENTED_RATINGS, field


def test_a_field_stating_no_closed_set_carries_no_enum():
    """``Origin`` states two placeholder *forms* and ``Raised`` one date format.

    Neither is a set of values, and reading either as one would refuse every
    real argument the field is supposed to take.
    """
    enums = {item.name: item.enum for item in todo.field_specs()}
    assert enums["Origin"] is None
    assert enums["Raised"] is None
    assert enums["Context"] is None


def test_the_field_table_comes_from_the_standard_not_from_the_code():
    """Derive against a synthetic standard: a restated table cannot pass this.

    The escaped-pipe alternation is here deliberately -- it is how the shipped
    document writes a rating, and splitting a row on every ``|`` would read the
    closed set as three separate cells and so as no enum at all.
    """
    text = (
        "# Deferred Work Standards\n"
        "\n## Entry schema\n"
        "\n```markdown\n## <Title>\n\n**Mood:** cheerful\n```\n"
        "\n| Field | Value |\n"
        "|---|---|\n"
        "| `Mood` | `cheerful`, `grim` |\n"
        "| `Weight` | `heavy` \\| `light` — how much it weighs |\n"
        "| `Origin` | the `THING-<NNN>` it came from |\n"
        "| `Note` | free prose |\n"
        "\n## Something else\n"
    )
    specs = todo.parse_field_table(text)
    assert [item.name for item in specs] == ["Mood", "Weight", "Origin", "Note"]
    assert specs[0].enum == ("cheerful", "grim")
    assert specs[1].enum == ("heavy", "light")
    assert specs[2].enum is None
    assert specs[3].enum is None


def test_a_standard_with_no_entry_schema_section_is_a_named_diagnostic():
    with pytest.raises(core.ToolError) as excinfo:
        todo.parse_field_table("# Deferred Work Standards\n\n## Purpose\n\nWords.\n")
    assert excinfo.value.diagnostic.code == core.E_PARSE


def test_the_flags_add_declares_are_the_table_minus_the_derived_field():
    """``Raised`` comes from the injected clock, so it is the one row with no flag."""
    flags = [item.flag for item in todo.supplied_specs()]
    assert "--raised" not in flags
    assert flags == [
        "--%s" % name.lower() for name in DOCUMENTED_FIELDS if name != todo.RAISED_FIELD
    ]


# ---------------------------------------------------------------------------
# add -- the write path
# ---------------------------------------------------------------------------


def test_add_creates_the_list_with_its_front_matter_when_absent(workspace):
    _change(workspace)
    assert not _todo_file(workspace).exists()

    result, code = _add(workspace)
    assert code == 0
    assert result.data["created"] is True
    assert result.data["title"] == TITLE
    assert result.data["path"] == todo.TODO_REL

    text = _todo_file(workspace).read_text(encoding="utf-8")
    assert text.startswith("<!-- todo: project -->\n<!-- updated: %s -->\n" % NOW)
    assert todo.BODY_HEADING in text


def test_the_front_matter_it_writes_validates_against_its_schema(workspace):
    _change(workspace)
    _add(workspace)
    matter = core.read_front_matter(_todo_file(workspace).read_text(encoding="utf-8"))
    assert matter == {"todo": "project", "updated": NOW}
    assert core.validate_against(core.load_schema(todo.TODO_KIND), matter) == []


def test_add_writes_every_field_in_the_standards_own_order(workspace):
    _change(workspace)
    _add(workspace)
    entries = todo.parse_entries(_todo_file(workspace).read_text(encoding="utf-8"))
    assert [entry.title for entry in entries] == [TITLE]
    assert list(entries[0].fields) == list(DOCUMENTED_FIELDS)
    assert entries[0].get("Run") == FIELDS["run"]
    assert entries[0].get("Context") == FIELDS["context"]


def test_raised_comes_from_the_injected_clock_not_the_wall_clock(workspace):
    _change(workspace)
    _add(workspace, now="2026-03-04")
    text = _todo_file(workspace).read_text(encoding="utf-8")
    assert "**Raised:** 2026-03-04" in text
    assert "<!-- updated: 2026-03-04 -->" in text


def test_add_appends_and_leaves_the_earlier_entry_intact(workspace):
    _change(workspace)
    _add(workspace, title="first")
    _add(workspace, title="second", now="2026-02-02")
    text = _todo_file(workspace).read_text(encoding="utf-8")
    entries = todo.parse_entries(text)
    assert [entry.title for entry in entries] == ["first", "second"]
    assert entries[0].get("Raised") == NOW
    assert entries[1].get("Raised") == "2026-02-02"
    # The most recent write owns `updated`; the earlier entry keeps its `Raised`.
    assert "<!-- updated: 2026-02-02 -->" in text


def test_re_adding_the_same_list_at_the_same_clock_is_byte_identical(workspace):
    _change(workspace)
    _add(workspace, title="first")
    first = _todo_file(workspace).read_text(encoding="utf-8")
    _add(workspace, title="second", now="2026-02-02")
    second = _todo_file(workspace).read_text(encoding="utf-8")

    _todo_file(workspace).unlink()
    _add(workspace, title="first")
    assert _todo_file(workspace).read_text(encoding="utf-8") == first
    _add(workspace, title="second", now="2026-02-02")
    assert _todo_file(workspace).read_text(encoding="utf-8") == second


# ---------------------------------------------------------------------------
# add -- every refusal, and nothing written on any of them
# ---------------------------------------------------------------------------

#: One violation per case: the field, the offending value, and the code.
REFUSALS = [
    ("run", "/mnope", todo.E_TODO_ENUM),
    ("run", "mfix", todo.E_TODO_ENUM),
    ("kind", "typo", todo.E_TODO_ENUM),
    ("priority", "urgent", todo.E_TODO_ENUM),
    ("risk_if_unfixed", "none", todo.E_TODO_ENUM),
    ("regression_risk", "unknown", todo.E_TODO_ENUM),
    ("cost", "cheap", todo.E_TODO_ENUM),
    ("run", "", todo.E_TODO_FIELD),
    ("kind", "   ", todo.E_TODO_FIELD),
    ("context", "", todo.E_TODO_FIELD),
    ("origin", "CHANGE-404", todo.E_TODO_ORIGIN),
    ("origin", "PROJECT-CHANGE-001", todo.E_TODO_ORIGIN),
    ("origin", "099-no-such-plan", todo.E_TODO_ORIGIN),
]


@pytest.mark.parametrize("field,value,expected", REFUSALS)
def test_each_field_violation_is_refused_and_nothing_is_written(
    workspace, field, value, expected
):
    """The property the change exists for: a refusal leaves **no file at all**.

    Asserted against a workspace with no ``TODO.md``, because an empty list is
    indistinguishable from a list nobody has filed against, and creating one on
    a refusal would manufacture exactly that ambiguity.
    """
    _change(workspace)
    result, code = _add(workspace, **{field: value})

    assert code == 1
    assert _codes(result) == [expected]
    assert not _todo_file(workspace).exists()
    assert not workspace.path("context/project").exists()


def test_a_refusal_leaves_an_existing_list_byte_identical(workspace):
    _change(workspace)
    _add(workspace, title="already here")
    before = _todo_file(workspace).read_text(encoding="utf-8")

    result, code = _add(workspace, title="rejected", kind="typo")
    assert code == 1
    assert _codes(result) == [todo.E_TODO_ENUM]
    assert _todo_file(workspace).read_text(encoding="utf-8") == before


def test_an_empty_title_is_a_usage_error(workspace):
    _change(workspace)
    result, code = _add(workspace, title="   ")
    assert code == 2
    assert _codes(result) == [core.E_USAGE]
    assert not _todo_file(workspace).exists()


def test_a_value_that_would_not_round_trip_is_refused(workspace):
    """A field value carrying a line the entry format reads as a heading or as
    another field cannot be read back, so it is refused rather than written."""
    _change(workspace)
    result, code = _add(workspace, context="fine\n## smuggled heading")
    assert code == 1
    assert _codes(result) == [todo.E_TODO_FIELD]
    assert not _todo_file(workspace).exists()


def test_a_second_entry_with_the_same_title_is_refused(workspace):
    """Removal is keyed on the title, so a duplicate would make it undefined."""
    _change(workspace)
    _add(workspace)
    before = _todo_file(workspace).read_text(encoding="utf-8")
    result, code = _add(workspace)
    assert code == 1
    assert _codes(result) == [core.E_AMBIGUOUS]
    assert _todo_file(workspace).read_text(encoding="utf-8") == before


def test_add_does_not_apply_the_context_rule(workspace):
    """The thin-``Context`` rule is ``check todo``'s, per TOOLS-INTERFACE.md.

    Pinned so the split between the emitter's refusals and the checker's
    findings cannot move without a test saying so.
    """
    _change(workspace)
    _result, code = _add(workspace, context="Needs more thought.")
    assert code == 0


# ---------------------------------------------------------------------------
# Origin resolution -- the same obligation `req change-close` places on a CHANGE
# ---------------------------------------------------------------------------


def test_origin_resolves_a_repo_change_in_any_target(workspace):
    _change(workspace, number="007", slug="late-retry", target="other")
    assert todo.resolve_origin(workspace.ws, "CHANGE-007") == (
        "context/other/changes/CHANGE-007-late-retry.md"
    )
    _result, code = _add(workspace, origin="CHANGE-007")
    assert code == 0


def test_origin_resolves_a_project_change(workspace):
    _index(workspace, number="003", slug="the-index")
    assert todo.resolve_origin(workspace.ws, "PROJECT-CHANGE-003") == (
        "context/project/changes/PROJECT-CHANGE-003-the-index.md"
    )
    _result, code = _add(workspace, origin="PROJECT-CHANGE-003")
    assert code == 0


def test_origin_resolves_a_plan_id(workspace):
    workspace.mkdir("context/project/plans/020-deferred-work-todo")
    assert todo.resolve_origin(workspace.ws, "020-deferred-work-todo") == (
        "context/project/plans/020-deferred-work-todo"
    )
    _result, code = _add(workspace, origin="020-deferred-work-todo")
    assert code == 0


def test_origin_resolves_on_the_number_not_the_slug(workspace):
    _change(workspace, number="012", slug="whatever-it-was-called")
    assert todo.resolve_origin(workspace.ws, "CHANGE-012") is not None
    assert todo.resolve_origin(workspace.ws, "CHANGE-012-a-stale-slug") is not None


def test_a_project_change_does_not_satisfy_a_bare_change_reference(workspace):
    _index(workspace, number="001")
    assert todo.resolve_origin(workspace.ws, "CHANGE-001") is None


def test_an_origin_that_is_not_an_identifier_is_refused(workspace):
    _change(workspace)
    result, code = _add(workspace, origin="../escape")
    assert code == 1
    assert _codes(result) == [todo.E_TODO_ORIGIN]
    assert not _todo_file(workspace).exists()


# ---------------------------------------------------------------------------
# remove -- resolution is removal, and a no-op removal is a failure
# ---------------------------------------------------------------------------


def test_remove_deletes_the_named_entry_and_leaves_the_others(workspace):
    _change(workspace)
    _add(workspace, title="first")
    _add(workspace, title="second")
    _add(workspace, title="third")

    result, code = _remove(workspace, title="second", now="2026-04-05")
    assert code == 0
    assert result.data["removed"] == 1

    text = _todo_file(workspace).read_text(encoding="utf-8")
    assert [entry.title for entry in todo.parse_entries(text)] == ["first", "third"]
    assert "second" not in text
    assert "<!-- updated: 2026-04-05 -->" in text


def test_remove_refuses_a_title_matching_no_entry(workspace):
    _change(workspace)
    _add(workspace, title="first")
    before = _todo_file(workspace).read_text(encoding="utf-8")

    result, code = _remove(workspace, title="never filed")
    assert code == 1
    assert _codes(result) == [core.E_NOT_FOUND]
    assert _todo_file(workspace).read_text(encoding="utf-8") == before


def test_remove_refuses_when_the_list_does_not_exist(workspace):
    result, code = _remove(workspace, title="anything")
    assert code == 1
    assert _codes(result) == [core.E_NOT_FOUND]
    assert not _todo_file(workspace).exists()


def test_remove_refuses_a_title_two_hand_written_entries_share(workspace):
    _change(workspace)
    _add(workspace, title="doubled")
    text = _todo_file(workspace).read_text(encoding="utf-8")
    body = core.strip_front_matter(text)
    entry = body[body.index("## doubled") :]
    workspace.write(todo.TODO_REL, text.rstrip("\n") + "\n\n" + entry)
    before = _todo_file(workspace).read_text(encoding="utf-8")

    result, code = _remove(workspace, title="doubled")
    assert code == 1
    assert _codes(result) == [core.E_AMBIGUOUS]
    assert _todo_file(workspace).read_text(encoding="utf-8") == before


def test_removing_the_last_entry_leaves_a_readable_empty_list(workspace):
    _change(workspace)
    _add(workspace, title="only one")
    _remove(workspace, title="only one", now="2026-05-06")

    text = _todo_file(workspace).read_text(encoding="utf-8")
    assert text == (
        "<!-- todo: project -->\n<!-- updated: 2026-05-06 -->\n\n%s\n" % todo.BODY_HEADING
    )
    assert todo.parse_entries(text) == []


def test_an_entry_can_be_added_again_after_it_was_removed(workspace):
    _change(workspace)
    _add(workspace, title="recurring")
    _remove(workspace, title="recurring")
    _result, code = _add(workspace, title="recurring")
    assert code == 0


# ---------------------------------------------------------------------------
# list -- newest first, and the filters a skill routes on
# ---------------------------------------------------------------------------


def _titles(result):
    return [entry["title"] for entry in result.data["entries"]]


def test_list_returns_the_open_entries_newest_first(workspace):
    _change(workspace)
    _add(workspace, title="oldest")
    _add(workspace, title="middle")
    _add(workspace, title="newest")

    result, code = _list(workspace)
    assert code == 0
    assert _titles(result) == ["newest", "middle", "oldest"]
    assert result.data["count"] == 3


def test_list_filters_by_run(workspace):
    _change(workspace)
    _add(workspace, title="for mfix", run="/mfix")
    _add(workspace, title="for mspec", run="/mspec")

    result, _code = _list(workspace, run="/mspec")
    assert _titles(result) == ["for mspec"]
    assert result.data["run"] == "/mspec"


def test_list_filters_by_kind(workspace):
    _change(workspace)
    _add(workspace, title="drifted", kind="spec-drift")
    _add(workspace, title="architectural", kind="architecture")

    result, _code = _list(workspace, kind="architecture")
    assert _titles(result) == ["architectural"]


def test_the_two_filters_intersect(workspace):
    _change(workspace)
    _add(workspace, title="both", run="/mfix", kind="spec-drift")
    _add(workspace, title="run only", run="/mfix", kind="architecture")
    _add(workspace, title="kind only", run="/mspec", kind="spec-drift")

    result, _code = _list(workspace, run="/mfix", kind="spec-drift")
    assert _titles(result) == ["both"]


def test_list_on_an_absent_list_is_empty_and_not_an_error(workspace):
    result, code = _list(workspace)
    assert code == 0
    assert result.data["entries"] == []
    assert result.data["count"] == 0
    assert not _todo_file(workspace).exists()


def test_list_publishes_every_field_of_each_entry(workspace):
    _change(workspace)
    _add(workspace)
    result, _code = _list(workspace)
    entry = result.data["entries"][0]
    assert entry["title"] == TITLE
    for name in DOCUMENTED_FIELDS:
        assert name in entry, name


def test_list_reads_and_never_writes(workspace):
    _change(workspace)
    _add(workspace)
    before = _todo_file(workspace).read_text(encoding="utf-8")
    _list(workspace)
    _list(workspace, run="/mfix")
    assert _todo_file(workspace).read_text(encoding="utf-8") == before


# ---------------------------------------------------------------------------
# Parsing a hand-edited list
# ---------------------------------------------------------------------------


def test_a_wrapped_context_paragraph_is_read_as_one_field(workspace):
    workspace.write(
        todo.TODO_REL,
        "<!-- todo: project -->\n<!-- updated: 2026-01-15 -->\n"
        "\n# Deferred Work\n"
        "\n## wrapped\n"
        "\n**Run:** /mfix\n"
        "**Context:** the first line\n"
        "and the second line\n",
    )
    entries = todo.parse_entries(_todo_file(workspace).read_text(encoding="utf-8"))
    assert entries[0].get("Context") == "the first line\nand the second line"


def test_an_entrys_line_number_is_the_file_line_when_the_file_was_parsed(workspace):
    _change(workspace)
    _add(workspace)
    text = _todo_file(workspace).read_text(encoding="utf-8")
    entry = todo.parse_entries(text)[0]
    assert text.split("\n")[entry.line - 1] == "## %s" % TITLE


# ---------------------------------------------------------------------------
# Usage and safety
# ---------------------------------------------------------------------------


def test_an_unknown_verb_is_a_usage_error(workspace):
    result = todo.run(workspace.args(verb=None), workspace.ws)
    assert core.exit_code(result) == 2
    assert _codes(result) == [core.E_USAGE]


def test_the_list_is_the_one_documented_path():
    assert todo.TODO_REL == "context/project/TODO.md"
    assert Path(todo.TODO_REL).name == "TODO.md"


def test_nothing_is_written_outside_the_workspace(workspace):
    _change(workspace)
    _add(workspace)
    written = sorted(
        str(path.relative_to(workspace.root))
        for path in workspace.root.rglob("*")
        if path.is_file()
    )
    assert todo.TODO_REL in written
    for relative in written:
        assert not relative.startswith("..")


# ---------------------------------------------------------------------------
# todo edit
#
# The verb exists because `remove` then `add` requires re-supplying every field
# the caller did not intend to change, and a Context paragraph retyped to fix a
# one-word rating is an opportunity to lose what made the entry startable.
# ---------------------------------------------------------------------------


def _edit(workspace, title=TITLE, now=NOW, **overrides):
    """Call ``todo edit`` directly and return ``(result, exit_code)``."""
    fields = {item.name.lower().replace("-", "_"): None for item in todo.supplied_specs()}
    fields.update(overrides)
    args = workspace.args(verb="edit", title=title, now=now, new_title=None, **fields)
    result = todo.run(args, workspace.ws)
    return result, core.exit_code(result)


def test_edit_changes_the_named_field(workspace):
    _change(workspace)
    _add(workspace)
    _, code = _edit(workspace, priority="low")
    assert code == 0
    assert "**Priority:** low" in _todo_file(workspace).read_text(encoding="utf-8")


def test_edit_leaves_every_other_field_byte_identical(workspace):
    """Asserted on the whole entry, not the edited field.

    An assertion reading only ``Priority`` could not see a ``Context`` paragraph
    reflowed beside it, which is precisely the loss this verb exists to prevent.
    """
    _change(workspace)
    _add(workspace)
    before = _todo_file(workspace).read_text(encoding="utf-8")

    _, code = _edit(workspace, priority="low")
    assert code == 0
    after = _todo_file(workspace).read_text(encoding="utf-8")

    changed = [
        (a, b)
        for a, b in zip(before.split("\n"), after.split("\n"))
        if a != b
    ]
    assert changed == [("**Priority:** medium", "**Priority:** low")]


def test_a_refused_edit_leaves_the_file_byte_identical(workspace):
    _change(workspace)
    _add(workspace)
    before = _todo_file(workspace).read_bytes()

    _, code = _edit(workspace, kind="not-a-kind")
    assert code != 0
    assert _todo_file(workspace).read_bytes() == before


def test_edit_refuses_a_title_matching_no_entry(workspace):
    _change(workspace)
    _add(workspace)
    result, code = _edit(workspace, title="no such entry", priority="low")
    assert code != 0
    assert core.E_NOT_FOUND in _codes(result)


def test_edit_renames_and_the_entry_is_reachable_by_the_new_title_only(workspace):
    _change(workspace)
    _add(workspace)
    args = workspace.args(
        verb="edit",
        title=TITLE,
        now=NOW,
        new_title="a renamed entry",
        **{item.name.lower().replace("-", "_"): None for item in todo.supplied_specs()}
    )
    assert core.exit_code(todo.run(args, workspace.ws)) == 0

    text = _todo_file(workspace).read_text(encoding="utf-8")
    assert "## a renamed entry" in text
    assert "## %s" % TITLE not in text

    _, code = _edit(workspace, title=TITLE, priority="low")
    assert code != 0


#: One table of bad inputs, driven through **both** verbs. Two tables would pass
#: while the implementations drifted, which is the failure the shared-validation
#: design exists to prevent -- so the coupling is asserted, not just intended.
BAD_INPUTS = [
    ("run", "/mnope"),
    ("kind", "not-a-kind"),
    ("priority", "urgent"),
    ("risk_if_unfixed", "extreme"),
    ("regression_risk", "maybe"),
    ("cost", "free"),
    ("origin", "CHANGE-404"),
]


@pytest.mark.parametrize("field,value", BAD_INPUTS)
def test_add_and_edit_refuse_the_same_violations(workspace, field, value):
    _change(workspace)
    _add(workspace)

    _, edit_code = _edit(workspace, **{field: value})
    assert edit_code != 0, "edit accepted %s=%r" % (field, value)

    _, add_code = _add(workspace, title="another entry", **{field: value})
    assert add_code != 0, "add accepted %s=%r" % (field, value)
