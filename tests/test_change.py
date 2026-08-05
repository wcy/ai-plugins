"""The ``change`` command group.

Establishes what TOOLS-TESTING.md requires of it -- ``create`` vs ``continue``
on every status, terminal statuses and baseline records, no renumbering across
a gap, and ``max + 1`` from the highest number present rather than the count --
plus the correction this wave lands: correspondence between a repo change and a
plan is mediated by the **project index**, never by the number.

Every fixture is a synthetic workspace in ``tmp_path`` holding exactly the one
condition under test. The group is imported and called directly, never through
``argv``.

The emission tests read ``shared/STANDARD-CHANGE.md`` and derive the required
front-matter keys and section headings from it, so a divergence between the
emitter and the document that owns the shape is caught mechanically rather than
argued.
"""

import re

import pytest

from conftest import NOW, PLUGIN_ROOT
from tools import change, core

STANDARD_CHANGE = PLUGIN_ROOT / "shared" / "STANDARD-CHANGE.md"

#: The whole status lifecycle, and what each status decides on its own. Keyed
#: on status, which is the correction: plan existence alone never decides.
STATUS_DECISIONS = {
    "pending": "continue",
    "in-progress": "continue",
    "applied": "create",
    "superseded": "create",
    "complete": "create",
}

#: A project-level index carries a narrower lifecycle than a repo-level
#: document -- `complete` is reserved for repo-level baseline records. The
#: schema is what says so, so the parametrisation is read from it.
INDEX_STATUSES = core.load_schema("change")["$defs"]["projectChange"]["properties"]["status"][
    "enum"
]


# ---------------------------------------------------------------------------
# Fixture builders -- one condition each
# ---------------------------------------------------------------------------


def _matter(**pairs):
    return "".join("<!-- %s: %s -->\n" % item for item in pairs.items())


def _repo_change(workspace, number, slug, status, repo="demo"):
    """Write ``context/<repo>/changes/CHANGE-<number>-<slug>.md``."""
    relative = "context/%s/changes/CHANGE-%s-%s.md" % (repo, number, slug)
    workspace.write(
        relative,
        _matter(change=number, scope="repo", repo=repo, status=status, date="2026-01-01")
        + "\n# CHANGE-%s: %s\n" % (number, slug),
    )
    return relative


def _index(workspace, number, slug, status="pending", refs=(), repos="demo"):
    """Write a project index whose "Repo Change Files" table names ``refs``."""
    rows = "".join("| `%s` | `%s` | row |\n" % (repos, ref) for ref in refs)
    relative = "context/project/changes/PROJECT-CHANGE-%s-%s.md" % (number, slug)
    workspace.write(
        relative,
        _matter(
            **{
                "project-change": number,
                "scope": "repo",
                "repos": repos,
                "status": status,
                "date": "2026-01-01",
            }
        )
        + "\n# PROJECT-CHANGE-%s: %s\n"
        "\n## Summary\n\nA summary.\n"
        "\n## Repo Change Files\n\n| Repo | Change File | Summary |\n|---|---|---|\n"
        "%s"
        "\n## Cross-Repo Notes\n\nNone.\n" % (number, slug, rows),
    )
    return relative


def _plan_dir(workspace, name):
    return workspace.mkdir("context/project/plans/%s" % (name,))


def _run(workspace, **fields):
    result = change.run(workspace.args(**fields), workspace.ws)
    return result, core.exit_code(result)


def _resolve(workspace, repo="demo", slug=None):
    return _run(workspace, verb="resolve", repo=repo, slug=slug)


def _index_resolve(workspace, slug=None):
    return _run(workspace, verb="index-resolve", slug=slug)


# ---------------------------------------------------------------------------
# create vs continue -- every status, one condition per fixture
# ---------------------------------------------------------------------------


def test_the_parametrised_statuses_are_the_whole_documented_lifecycle():
    schema = core.load_schema("change")
    documented = schema["$defs"]["repoChange"]["properties"]["status"]["enum"]
    assert sorted(STATUS_DECISIONS) == sorted(documented)


@pytest.mark.parametrize("status,action", sorted(STATUS_DECISIONS.items()))
def test_status_alone_decides_create_vs_continue(workspace, status, action):
    """One change file, no plans, no index: only ``status`` can decide."""
    path = _repo_change(workspace, "001", "alpha", status)
    result, code = _resolve(workspace)

    assert code == 0
    assert result.ok is True
    assert result.data["action"] == action
    if action == "continue":
        assert result.data["target"]["path"] == path
        assert result.data["target"]["status"] == status
    else:
        assert result.data["target"]["number"] == "002"
        assert result.data["target"]["status"] == "pending"


@pytest.mark.parametrize("status", ["applied", "superseded", "complete"])
def test_terminal_status_stays_terminal_with_no_plan_directory(workspace, status):
    """The regression the correction exists to prevent.

    ``context/project/plans/`` does not exist at all here, so a rule keyed on
    plan existence would re-open the file instead of allocating the next number.
    """
    _repo_change(workspace, "001", "alpha", status)
    assert not workspace.path("context/project/plans").exists()

    result, _ = _resolve(workspace)
    assert result.data["action"] == "create"
    assert result.data["target"]["number"] == "002"


def test_baseline_record_is_terminal_even_when_pending(workspace):
    """A ``*-initial-spec.md`` record is terminal at any status."""
    _repo_change(workspace, "000", "initial-spec", "pending")
    result, _ = _resolve(workspace)

    assert result.data["considered"][0]["baseline"] is True
    assert result.data["action"] == "create"
    assert result.data["target"]["number"] == "001"


def test_baseline_record_at_status_complete_is_terminal(workspace):
    _repo_change(workspace, "000", "initial-spec", "complete")
    result, _ = _resolve(workspace)
    assert result.data["action"] == "create"
    assert result.data["target"]["baseline"] is False


def test_two_continuable_files_create_rather_than_pick_one(workspace):
    _repo_change(workspace, "001", "alpha", "pending")
    _repo_change(workspace, "002", "beta", "in-progress")

    result, _ = _resolve(workspace)
    assert result.data["action"] == "create"
    assert result.data["target"]["number"] == "003"


def test_a_file_with_no_status_is_terminal_and_warned_about(workspace):
    workspace.write(
        "context/demo/changes/CHANGE-001-alpha.md",
        "<!-- change: 001 -->\n\n# CHANGE-001\n",
    )
    result, code = _resolve(workspace)

    assert code == 0  # a malformed sibling never blocks allocation
    assert [d.severity for d in result.diagnostics] == ["warning"]
    assert result.diagnostics[0].code == core.E_PARSE
    assert result.data["action"] == "create"
    assert result.data["target"]["number"] == "002"


def test_a_filename_that_does_not_parse_is_warned_about_not_sequenced(workspace):
    workspace.write("context/demo/changes/CHANGE-notes.md", "# notes\n")
    _repo_change(workspace, "001", "alpha", "applied")

    result, code = _resolve(workspace)
    assert code == 0
    assert [d.code for d in result.diagnostics] == [core.E_UNPARSED_NAME]
    assert [ref["number"] for ref in result.data["considered"]] == ["001"]
    assert result.data["target"]["number"] == "002"


# ---------------------------------------------------------------------------
# Correspondence is mediated by the project index, not by the number
# ---------------------------------------------------------------------------


def test_pending_change_referenced_by_a_planned_index_is_terminal(workspace):
    """Hop one: the index names it. Hop two: the index has a plan directory."""
    path = _repo_change(workspace, "005", "alpha", "pending")
    _index(workspace, "003", "beta", refs=[path])
    _plan_dir(workspace, "003-beta")

    result, _ = _resolve(workspace)
    assert result.data["action"] == "create"
    assert result.data["target"]["number"] == "006"


def test_the_same_change_is_continuable_once_the_plan_directory_is_gone(workspace):
    """The other direction of the same rule: no plan, so no correspondence."""
    path = _repo_change(workspace, "005", "alpha", "pending")
    _index(workspace, "003", "beta", refs=[path])
    assert not workspace.path("context/project/plans").exists()

    result, _ = _resolve(workspace)
    assert result.data["action"] == "continue"
    assert result.data["target"]["path"] == path


def test_a_number_that_coincides_with_a_plan_directory_still_continues(workspace):
    """Number matching must not be what decides -- the false-negative half."""
    path = _repo_change(workspace, "005", "alpha", "pending")
    _plan_dir(workspace, "005-unrelated")

    result, _ = _resolve(workspace)
    assert result.data["action"] == "continue"
    assert result.data["target"]["path"] == path


def test_an_unreferenced_change_beside_a_same_numbered_plan_still_continues(workspace):
    """The false-positive half: same number, different subject, no reference."""
    path = _repo_change(workspace, "003", "alpha", "pending")
    _index(workspace, "003", "beta", refs=["context/demo/changes/CHANGE-009-other.md"])
    _plan_dir(workspace, "003-beta")

    result, _ = _resolve(workspace)
    assert result.data["action"] == "continue"
    assert result.data["target"]["path"] == path


def test_a_reference_from_an_unplanned_index_does_not_make_it_terminal(workspace):
    path = _repo_change(workspace, "005", "alpha", "pending")
    _index(workspace, "003", "beta", refs=[path])
    _plan_dir(workspace, "004-something-else")

    result, _ = _resolve(workspace)
    assert result.data["action"] == "continue"


def test_a_relative_link_target_counts_as_a_reference(workspace):
    """Indexes written by hand link relatively; both forms must be seen."""
    path = _repo_change(workspace, "005", "alpha", "pending")
    _index(workspace, "003", "beta", refs=["../../demo/changes/CHANGE-005-alpha.md"])
    _plan_dir(workspace, "003-beta")

    result, _ = _resolve(workspace)
    assert result.data["action"] == "create"
    assert path  # the file exists; it is terminal because the index names it


def test_a_reference_outside_the_repo_change_files_section_is_ignored(workspace):
    """Only the documented table mediates correspondence."""
    path = _repo_change(workspace, "005", "alpha", "pending")
    workspace.write(
        "context/project/changes/PROJECT-CHANGE-003-beta.md",
        _matter(
            **{
                "project-change": "003",
                "scope": "repo",
                "repos": "demo",
                "status": "pending",
                "date": "2026-01-01",
            }
        )
        + "\n# PROJECT-CHANGE-003: beta\n"
        "\n## Summary\n\nMentions `%s` in prose only.\n"
        "\n## Repo Change Files\n\n| Repo | Change File | Summary |\n|---|---|---|\n" % (path,),
    )
    _plan_dir(workspace, "003-beta")

    result, _ = _resolve(workspace)
    assert result.data["action"] == "continue"


def test_a_reference_to_another_repos_change_does_not_match(workspace):
    _repo_change(workspace, "005", "alpha", "pending", repo="demo")
    _index(workspace, "003", "beta", refs=["context/other/changes/CHANGE-005-alpha.md"])
    _plan_dir(workspace, "003-beta")

    result, _ = _resolve(workspace)
    assert result.data["action"] == "continue"


# ---------------------------------------------------------------------------
# Allocation comes from the maximum, never from the count
# ---------------------------------------------------------------------------


def test_allocation_from_the_highest_number_present_not_the_count(workspace):
    """One file, numbered 007: the next number is 008, not 002."""
    _repo_change(workspace, "007", "alpha", "applied")
    result, _ = _resolve(workspace)

    assert len(result.data["considered"]) == 1
    assert result.data["action"] == "create"
    assert result.data["target"]["number"] == "008"


def test_a_gap_in_the_sequence_never_renumbers(workspace):
    _repo_change(workspace, "001", "alpha", "applied")
    _repo_change(workspace, "004", "delta", "applied")

    result, _ = _resolve(workspace)
    assert result.data["target"]["number"] == "005"


def test_an_empty_sequence_allocates_the_first_number(workspace):
    result, code = _resolve(workspace)
    assert code == 0
    assert result.data["considered"] == []
    assert result.data["target"]["number"] == "001"


def test_the_supplied_slug_lands_in_the_allocated_path(workspace):
    _repo_change(workspace, "001", "alpha", "applied")
    result, _ = _resolve(workspace, slug="retry-policy")

    assert result.data["target"]["slug"] == "retry-policy"
    assert result.data["target"]["path"] == "context/demo/changes/CHANGE-002-retry-policy.md"


# ---------------------------------------------------------------------------
# The envelope: considered is the audit trail, and the shapes are the ones
# TOOLS-DATAMODEL.md documents
# ---------------------------------------------------------------------------


def test_considered_lists_every_file_examined_including_terminal_ones(workspace):
    _repo_change(workspace, "001", "alpha", "applied")
    _repo_change(workspace, "002", "initial-spec", "complete")
    _repo_change(workspace, "003", "gamma", "superseded")
    _repo_change(workspace, "004", "delta", "pending")

    result, _ = _resolve(workspace)
    assert [ref["number"] for ref in result.data["considered"]] == ["001", "002", "003", "004"]
    assert [ref["status"] for ref in result.data["considered"]] == [
        "applied",
        "complete",
        "superseded",
        "pending",
    ]
    assert [ref["baseline"] for ref in result.data["considered"]] == [False, True, False, False]
    assert result.data["action"] == "continue"


def test_sequence_decision_and_change_ref_match_the_datamodel_field_for_field(workspace):
    _repo_change(workspace, "001", "alpha", "pending")
    result, _ = _resolve(workspace)

    assert list(result.data) == ["action", "target", "considered"]
    for ref in [result.data["target"]] + result.data["considered"]:
        assert list(ref) == [
            "scope",
            "repo",
            "number",
            "slug",
            "path",
            "status",
            "baseline",
            "plan_not_required",
        ]
        assert isinstance(ref["number"], str)  # zero-padded string, not an int
        assert ref["scope"] == "repo"
        assert ref["repo"] == "demo"
    assert result.command == "change.resolve"


def test_project_scope_carries_a_null_repo(workspace):
    _index(workspace, "001", "alpha", status="applied")
    result, _ = _index_resolve(workspace)

    for ref in [result.data["target"]] + result.data["considered"]:
        assert ref["scope"] == "project"
        assert ref["repo"] is None
    assert result.command == "change.index-resolve"


# ---------------------------------------------------------------------------
# index-resolve: the same rule, workspace-wide
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", sorted(INDEX_STATUSES))
def test_index_resolve_keys_on_status_too(workspace, status):
    _index(workspace, "001", "alpha", status=status)

    result, _ = _index_resolve(workspace)
    assert result.data["action"] == STATUS_DECISIONS[status]


def test_index_resolve_is_terminal_once_its_plan_directory_exists(workspace):
    _index(workspace, "003", "beta", status="pending")
    _plan_dir(workspace, "003-beta")

    result, _ = _index_resolve(workspace)
    assert result.data["action"] == "create"
    assert result.data["target"]["number"] == "004"
    assert result.data["target"]["path"] == (
        "context/project/changes/PROJECT-CHANGE-004-unnamed.md"
    )


def test_index_resolve_allocates_from_the_highest_number(workspace):
    _index(workspace, "011", "alpha", status="applied")
    result, _ = _index_resolve(workspace)
    assert result.data["target"]["number"] == "012"


def test_index_resolve_ignores_repo_level_files(workspace):
    workspace.write("context/project/changes/CHANGE-001-stray.md", "# stray\n")
    result, _ = _index_resolve(workspace)
    assert result.data["considered"] == []
    assert result.data["target"]["number"] == "001"


# ---------------------------------------------------------------------------
# Identifiers are rejected, never sanitized
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["../x", "a/b", "", "with space"])
def test_a_bad_slug_is_rejected(workspace, bad):
    result, code = _resolve(workspace, slug=bad)
    assert code == 2
    assert [d.code for d in result.diagnostics] == [core.E_BAD_IDENT]
    assert result.data is None


@pytest.mark.parametrize("bad", ["../x", "a/b", "with space"])
def test_a_bad_repo_is_rejected(workspace, bad):
    result, code = _resolve(workspace, repo=bad)
    assert code == 2
    assert [d.code for d in result.diagnostics] == [core.E_BAD_IDENT]


def test_a_bad_slug_is_rejected_before_anything_is_read(workspace):
    """The refusal cannot depend on the workspace holding a changes tree."""
    result, code = _run(workspace, verb="index-resolve", slug="../x")
    assert code == 2
    assert [d.code for d in result.diagnostics] == [core.E_BAD_IDENT]


def test_a_missing_verb_is_a_usage_error(workspace):
    result, code = _run(workspace, verb=None)
    assert code == 2
    assert [d.code for d in result.diagnostics] == [core.E_USAGE]


# ---------------------------------------------------------------------------
# emit -- STANDARD-CHANGE.md is the authority, and the suite asserts the match
# ---------------------------------------------------------------------------

_FM_KEY_RE = re.compile(r"^<!--\s*([A-Za-z0-9_-]+):")
_H2_RE = re.compile(r"^##\s+(.+?)\s*$")
_H1_RE = re.compile(r"^#\s+(.+?)\s*$")
_INLINE_H2_RE = re.compile(r"`(##\s+[A-Za-z][A-Za-z ]*?)`")


def _standard_change_text():
    return STANDARD_CHANGE.read_text(encoding="utf-8")


def _doc_section(text, *titles):
    """The body of the heading named by ``titles``, walked in nesting order.

    Headings inside a fenced block are content, not structure, so the walk
    ignores them -- the documented skeletons are themselves full of ``#`` lines.
    """
    for title in titles:
        text = _one_doc_section(text, title)
    return text


def _one_doc_section(text, title):
    collected = []
    inside = False
    fenced = False
    level = 0
    for line in text.splitlines():
        if line.startswith("```"):
            fenced = not fenced
        elif not fenced and line.startswith("#"):
            depth = len(line) - len(line.lstrip("#"))
            heading = line.lstrip("#").strip()
            if inside and depth <= level:
                break
            if not inside and heading.lower() == title.lower():
                inside, level = True, depth
                continue
        if inside:
            collected.append(line)
    assert inside, "STANDARD-CHANGE.md has no %r section" % (title,)
    return "\n".join(collected)


def _first_fence(text):
    lines = text.splitlines()
    start = next(index for index, line in enumerate(lines) if line.startswith("```"))
    end = next(index for index in range(start + 1, len(lines)) if lines[index].startswith("```"))
    return "\n".join(lines[start + 1 : end])


def _front_matter_keys(text):
    keys = []
    for line in text.splitlines():
        match = _FM_KEY_RE.match(line.strip())
        if match:
            keys.append(match.group(1))
    return keys


def _headings(text, pattern):
    found = []
    for line in text.splitlines():
        match = pattern.match(line)
        if match:
            found.append(match.group(1))
    return found


def _required_keys(branch):
    return core.load_schema("change")["$defs"][branch]["required"]


def _emit(workspace, path, **fields):
    fields.setdefault("scope", "repo")
    fields.setdefault("status", "pending")
    fields.setdefault("title", "A Title")
    fields.setdefault("repo", "demo")
    return _run(workspace, verb="emit", path=path, **fields)


def test_emitted_repo_document_matches_the_standard(workspace):
    relative = "context/demo/changes/CHANGE-006-retry-policy.md"
    result, code = _emit(workspace, relative)
    assert code == 0
    assert result.data["path"] == relative

    written = workspace.path(relative).read_text(encoding="utf-8")
    documented = _first_fence(_doc_section(_standard_change_text(), "Change Document Schemas", "Repo-level change document"))

    # `plan` is the one documented key `emit` does not write unless `--plan` is passed.
    assert _front_matter_keys(written) == [
        key for key in _front_matter_keys(documented) if key != "plan"
    ]
    assert set(_required_keys("repoChange")) <= set(_front_matter_keys(written))
    assert _headings(written, _H2_RE) == _headings(documented, _H2_RE)
    assert _headings(written, _H1_RE) == ["CHANGE-006: A Title"]


def test_emitted_repo_document_validates_against_the_change_kind(workspace):
    relative = "context/demo/changes/CHANGE-006-retry-policy.md"
    _emit(workspace, relative)

    outcome = core.validate_instance("change", workspace.path(relative), workspace.ws)
    assert outcome.ok is True
    matter = core.load_front_matter(workspace.path(relative))
    assert matter == {
        "change": "006",
        "scope": "repo",
        "repo": "demo",
        "status": "pending",
        "date": NOW,
    }


def test_emit_writes_plan_not_required_when_passed(workspace):
    relative = "context/demo/changes/CHANGE-006-retry-policy.md"
    _emit(workspace, relative, plan="not-required")

    outcome = core.validate_instance("change", workspace.path(relative), workspace.ws)
    assert outcome.ok is True
    matter = core.load_front_matter(workspace.path(relative))
    assert matter["plan"] == "not-required"


def test_emitted_project_index_matches_the_standard(workspace):
    relative = "context/project/changes/PROJECT-CHANGE-004-retry-policy.md"
    result, code = _emit(workspace, relative, repo="demo, other", scope="shared")
    assert code == 0

    written = workspace.path(relative).read_text(encoding="utf-8")
    documented = _first_fence(_doc_section(_standard_change_text(), "Change Document Schemas", "Project-level change index"))

    # `consumers` is shared-scope-only with no flag in TOOLS-INTERFACE.md's signature;
    # `plan` is only written when `--plan` is passed. Neither is written here.
    assert _front_matter_keys(written) == [
        key for key in _front_matter_keys(documented) if key not in ("consumers", "plan")
    ]
    assert set(_required_keys("projectChange")) <= set(_front_matter_keys(written))
    assert _headings(written, _H2_RE) == _headings(documented, _H2_RE)
    assert _headings(written, _H1_RE) == ["PROJECT-CHANGE-004: A Title"]
    assert core.load_front_matter(workspace.path(relative))["repos"] == "demo, other"


def test_a_single_repo_index_omits_the_cross_repo_notes_section(workspace):
    """The documented instruction: omit that section for single-repo changes."""
    relative = "context/project/changes/PROJECT-CHANGE-004-retry-policy.md"
    _emit(workspace, relative, repo="demo")

    written = workspace.path(relative).read_text(encoding="utf-8")
    documented = _first_fence(_doc_section(_standard_change_text(), "Change Document Schemas", "Project-level change index"))

    assert _headings(written, _H2_RE) == [
        title for title in _headings(documented, _H2_RE) if title != "Cross-Repo Notes"
    ]
    assert core.validate_instance("change", workspace.path(relative), workspace.ws).ok is True


def test_emitted_baseline_record_uses_the_reduced_layout(workspace):
    relative = "context/demo/changes/CHANGE-000-initial-spec.md"
    result, code = _emit(workspace, relative, status="complete")
    assert code == 0

    written = workspace.path(relative).read_text(encoding="utf-8")
    section = _doc_section(_standard_change_text(), "Initial-Spec Baseline Records")

    assert _front_matter_keys(written) == _front_matter_keys(_first_fence(section))
    # The layout is stated in that section's prose, in backticks.
    assert _headings(written, _H2_RE) == [
        title.lstrip("#").strip() for title in _INLINE_H2_RE.findall(section)
    ]
    assert core.validate_instance("change", workspace.path(relative), workspace.ws).ok is True


def test_emission_is_deterministic_under_a_pinned_now(workspace):
    relative = "context/demo/changes/CHANGE-006-retry-policy.md"
    _emit(workspace, relative)
    first = workspace.path(relative).read_text(encoding="utf-8")

    result, code = _emit(workspace, relative)
    assert code == 0
    assert workspace.path(relative).read_text(encoding="utf-8") == first
    assert NOW in first
    assert result.data["path"] == relative


def test_emission_refuses_to_overwrite_a_different_document(workspace):
    relative = "context/demo/changes/CHANGE-006-retry-policy.md"
    workspace.write(relative, "# hand-written\n")

    result, code = _emit(workspace, relative)
    assert code == 1
    assert [d.code for d in result.diagnostics] == [core.E_INVALID_STATE]
    assert workspace.path(relative).read_text(encoding="utf-8") == "# hand-written\n"


def test_front_matter_that_would_not_validate_is_refused_before_the_write(workspace):
    relative = "context/demo/changes/CHANGE-006-retry-policy.md"
    result, code = _emit(workspace, relative, status="nonsense")

    assert code == 1
    assert [d.code for d in result.diagnostics] == [core.E_SCHEMA_INVALID]
    assert not workspace.path(relative).exists()


def test_a_project_index_may_not_carry_the_baseline_status(workspace):
    """`complete` is repo-level only; the schema is what refuses it."""
    relative = "context/project/changes/PROJECT-CHANGE-004-retry-policy.md"
    result, code = _emit(workspace, relative, status="complete")

    assert code == 1
    assert [d.code for d in result.diagnostics] == [core.E_SCHEMA_INVALID]
    assert not workspace.path(relative).exists()


@pytest.mark.parametrize("name", ["notes.md", "CHANGE-6-alpha.md", "CHANGE-006-alpha.txt"])
def test_a_filename_that_is_not_a_change_document_is_a_usage_error(workspace, name):
    result, code = _emit(workspace, "context/demo/changes/%s" % (name,))
    assert code == 2
    assert [d.code for d in result.diagnostics] == [core.E_USAGE]


def test_emission_outside_the_workspace_is_refused(workspace):
    result, code = _emit(workspace, "../CHANGE-006-escape.md")
    assert code == 2
    assert [d.code for d in result.diagnostics] == [core.E_PATH_ESCAPE]
    assert not (workspace.root.parent / "CHANGE-006-escape.md").exists()


def test_a_repo_level_document_takes_exactly_one_repo(workspace):
    result, code = _emit(workspace, "context/demo/changes/CHANGE-006-alpha.md", repo="demo, other")
    assert code == 2
    assert [d.code for d in result.diagnostics] == [core.E_USAGE]


def test_a_bad_repo_is_rejected_by_emit(workspace):
    result, code = _emit(workspace, "context/demo/changes/CHANGE-006-alpha.md", repo="../x")
    assert code == 2
    assert [d.code for d in result.diagnostics] == [core.E_BAD_IDENT]


# ---------------------------------------------------------------------------
# The group computes; it does not shell out
# ---------------------------------------------------------------------------


def test_the_group_invokes_no_git_and_reads_no_clock_of_its_own():
    source = (PLUGIN_ROOT / "tools" / "change.py").read_text(encoding="utf-8")
    for forbidden in ("subprocess", "os.system", "popen", "import time", "import datetime"):
        assert forbidden not in source.lower()
    # The only timestamp is the injected one; `--now` is the sole source.
    assert source.count("system_instant") == 1


def test_resolution_writes_nothing(workspace):
    _repo_change(workspace, "001", "alpha", "pending")
    before = sorted(str(path) for path in workspace.root.rglob("*"))

    _resolve(workspace)
    _index_resolve(workspace)

    assert sorted(str(path) for path in workspace.root.rglob("*")) == before
