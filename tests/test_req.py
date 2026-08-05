"""The ``req`` group: id allocation that makes reuse structurally impossible.

The property under test is REQ-017's, stated in ``TOOLS-TESTING.md`` as *next
id skips no number and reuses none*: the next id is ``max(<NNN>) + 1``, so a
gap in the sequence never renumbers and an id that has ever appeared is never
handed out again. Every id below is asserted literally -- an implementation
that allocated from a count would produce a different, visibly wrong answer in
each of these fixtures.

The group is imported and called directly, never through ``argv``, and the
clock is always the injected ``conftest.NOW``.
"""

import pytest

from conftest import NOW
from tools import core, req

FRONT_MATTER = "<!-- requirements: demo -->\n<!-- updated: 2026-01-01 -->\n"
HEADING = "\n# Requirements — demo\n\n"


def entry(number, title="A need someone has", status="active"):
    """One well-formed ``### REQ-<NNN>`` block, per STANDARD-REQ.md."""
    return (
        "### REQ-%03d: %s\n\n"
        "**Need:** Someone wants the thing to happen.\n"
        "**Rationale:** It is worth money.\n"
        "**Status:** %s\n\n" % (number, title, status)
    )


def write_requirements(workspace, tier, body="", front_matter=FRONT_MATTER):
    """Write ``context/<tier>/requirements/REQUIREMENTS.md``."""
    return workspace.write(
        "context/%s/requirements/REQUIREMENTS.md" % tier,
        front_matter + HEADING + body,
    )


def run(workspace, verb, tier):
    args = workspace.args(verb=verb, tier=tier)
    result = req.run(args, workspace.ws)
    return result, core.exit_code(result)


# ---------------------------------------------------------------------------
# `req next` -- allocated from the highest id present
# ---------------------------------------------------------------------------


def test_an_absent_file_allocates_the_first_id(workspace):
    result, code = run(workspace, "next", "demo")
    assert code == 0
    assert result.data["exists"] is False
    assert result.data["entries"] == []
    assert result.data["next"] == "REQ-001"
    assert result.data["path"] == "context/demo/requirements/REQUIREMENTS.md"


def test_a_file_with_no_entry_allocates_the_first_id(workspace):
    write_requirements(workspace, "demo")
    result, code = run(workspace, "next", "demo")
    assert code == 0
    assert result.data["exists"] is True
    assert result.data["entries"] == []
    assert result.data["next"] == "REQ-001"


def test_a_gap_in_the_sequence_never_renumbers(workspace):
    # Three entries, highest 004. Allocating from a count would return REQ-004
    # and reuse an id that is already taken.
    write_requirements(workspace, "demo", entry(1) + entry(2) + entry(4))
    result, code = run(workspace, "next", "demo")
    assert code == 0
    assert result.data["entries"] == ["REQ-001", "REQ-002", "REQ-004"]
    assert result.data["next"] == "REQ-005"


def test_a_single_high_id_allocates_above_it(workspace):
    # One entry, numbered 042. A count would say REQ-002; a length would too.
    write_requirements(workspace, "demo", entry(42))
    result, _code = run(workspace, "next", "demo")
    assert result.data["next"] == "REQ-043"


def test_a_removed_entry_is_never_reallocated(workspace):
    write_requirements(workspace, "demo", entry(1) + entry(2) + entry(3))
    assert run(workspace, "next", "demo")[0].data["next"] == "REQ-004"
    # STANDARD-REQ.md forbids deletion, but even if the file loses an entry the
    # allocator must not hand its number out again.
    write_requirements(workspace, "demo", entry(1) + entry(3))
    assert run(workspace, "next", "demo")[0].data["next"] == "REQ-004"


def test_a_malformed_entry_still_holds_its_id(workspace):
    # The entry is invalid (no Need/Rationale/Status) but its number is taken.
    write_requirements(workspace, "demo", entry(1) + "### REQ-007: Half written\n\n")
    result, code = run(workspace, "next", "demo")
    assert code == 0  # a warning, not an error
    assert result.data["entries"] == ["REQ-001", "REQ-007"]
    assert result.data["next"] == "REQ-008"
    assert [item.severity for item in result.diagnostics] == ["warning"]


def test_repeated_allocation_strictly_increases(workspace):
    body = ""
    allocated = []
    for _ in range(4):
        nxt = run(workspace, "next", "demo")[0].data["next"]
        allocated.append(nxt)
        body += entry(int(nxt.split("-")[1]))
        write_requirements(workspace, "demo", body)
    assert allocated == ["REQ-001", "REQ-002", "REQ-003", "REQ-004"]
    assert len(set(allocated)) == len(allocated)  # nothing reused


def test_entries_are_reported_in_document_order(workspace):
    write_requirements(workspace, "demo", entry(9) + entry(2) + entry(5))
    result, _code = run(workspace, "next", "demo")
    assert result.data["entries"] == ["REQ-009", "REQ-002", "REQ-005"]
    assert result.data["next"] == "REQ-010"


def test_the_three_tiers_keep_independent_sequences(workspace):
    write_requirements(workspace, "demo", entry(7))
    write_requirements(workspace, "shared", entry(1))
    assert run(workspace, "next", "demo")[0].data["next"] == "REQ-008"
    assert run(workspace, "next", "shared")[0].data["next"] == "REQ-002"
    assert run(workspace, "next", "project")[0].data["next"] == "REQ-001"


def test_allocation_is_deterministic(workspace):
    write_requirements(workspace, "demo", entry(1) + entry(4))
    first = run(workspace, "next", "demo")[0].to_json()
    second = run(workspace, "next", "demo")[0].to_json()
    assert first == second


# ---------------------------------------------------------------------------
# `req gate`
# ---------------------------------------------------------------------------


def test_gate_is_false_for_an_absent_file(workspace):
    result, code = run(workspace, "gate", "demo")
    assert code == 0
    assert result.data["exists"] is False
    assert result.data["gate_passes"] is False


def test_gate_is_false_for_a_stub_with_no_entry(workspace):
    write_requirements(workspace, "demo")
    result, code = run(workspace, "gate", "demo")
    assert code == 0
    assert result.data["exists"] is True
    assert result.data["gate_passes"] is False


def test_gate_is_true_for_one_valid_entry(workspace):
    write_requirements(workspace, "demo", entry(1))
    result, code = run(workspace, "gate", "demo")
    assert code == 0
    assert result.data["entries"] == ["REQ-001"]
    assert result.data["gate_passes"] is True


@pytest.mark.parametrize("status", ["active", "stale", "superseded"])
def test_every_documented_status_is_a_valid_entry(workspace, status):
    write_requirements(workspace, "demo", entry(1, status=status))
    assert run(workspace, "gate", "demo")[0].data["gate_passes"] is True


def test_gate_is_false_when_the_status_is_not_a_documented_value(workspace):
    write_requirements(workspace, "demo", entry(1, status="applied"))
    result, _code = run(workspace, "gate", "demo")
    assert result.data["gate_passes"] is False


def test_gate_is_false_when_a_required_field_is_missing(workspace):
    write_requirements(
        workspace,
        "demo",
        "### REQ-001: Half written\n\n**Need:** Someone wants it.\n\n",
    )
    result, _code = run(workspace, "gate", "demo")
    assert result.data["entries"] == ["REQ-001"]
    assert result.data["gate_passes"] is False


def test_gate_is_false_when_the_front_matter_does_not_validate(workspace):
    write_requirements(
        workspace, "demo", entry(1), front_matter="<!-- requirements: demo -->\n"
    )
    result, code = run(workspace, "gate", "demo")
    assert code == 0
    assert result.data["gate_passes"] is False
    assert [item.code for item in result.diagnostics] == [core.E_SCHEMA_INVALID]


def test_gate_ignores_a_valid_entry_in_another_tier(workspace):
    write_requirements(workspace, "shared", entry(1))
    assert run(workspace, "gate", "demo")[0].data["gate_passes"] is False
    assert run(workspace, "gate", "shared")[0].data["gate_passes"] is True


# ---------------------------------------------------------------------------
# Identifiers and usage
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("verb", ["next", "gate"])
@pytest.mark.parametrize("tier", ["../escape", "/etc", "demo/../..", "bad tier", ""])
def test_a_bad_tier_is_rejected_never_sanitized(workspace, verb, tier):
    result, code = run(workspace, verb, tier)
    assert code == 2
    assert [item.code for item in result.diagnostics] == [core.E_BAD_IDENT]
    assert result.data is None
    # Nothing was created for the refused value, under any spelling.
    assert list(workspace.path("context").glob("*")) == []


def test_an_unknown_verb_is_a_usage_error(workspace):
    args = workspace.args(verb="allocate", tier="demo")
    result = req.run(args, workspace.ws)
    assert core.exit_code(result) == 2
    assert [item.code for item in result.diagnostics] == [core.E_USAGE]


def test_the_clock_is_injected_and_never_read(workspace):
    # `req` writes nothing and stamps nothing; the injected clock is present on
    # the namespace and simply unused, which is what keeps runs reproducible.
    args = workspace.args(verb="next", tier="demo")
    assert args.now == NOW
    assert req.run(args, workspace.ws).ok


# ---------------------------------------------------------------------------
# `parse_req_id` -- the single normalization point
# ---------------------------------------------------------------------------


def test_parse_req_id_resolves_bare_mnemonic_and_project_forms_to_the_same_number():
    bare = req.parse_req_id("REQ-015")
    mnemonic = req.parse_req_id("REQ-015-reference-recognition")
    qualified = req.parse_req_id("project:REQ-015")

    assert bare.number == mnemonic.number == qualified.number == "015"
    assert bare.tier == "self"
    assert mnemonic.tier == "self"
    assert mnemonic.mnemonic == "reference-recognition"
    assert bare.mnemonic is None
    assert qualified.tier == "project"
    assert qualified.mnemonic is None


def test_parse_req_id_qualified_form_with_a_mnemonic():
    parsed = req.parse_req_id("project:REQ-004-cross-repo-audit")
    assert parsed.tier == "project"
    assert parsed.number == "004"
    assert parsed.mnemonic == "cross-repo-audit"


def test_parse_req_id_preserves_the_raw_form():
    assert req.parse_req_id("REQ-015-reference-recognition").raw == "REQ-015-reference-recognition"


@pytest.mark.parametrize(
    "raw",
    [
        "REQ-4",  # fewer than three digits
        "REQ-015-",  # trailing hyphen, no mnemonic
        "REQ-015-x",  # a single-word mnemonic -- the grammar needs 2-4 words
        "REQ-015-Reference-Recognition",  # uppercase
        "req-015",  # lowercase prefix
        "REQ015",  # no separator
        "notproject:REQ-015",  # not the `project:` qualifier
        "",
    ],
)
def test_parse_req_id_raises_on_a_form_that_does_not_parse(raw):
    with pytest.raises(core.ToolError) as excinfo:
        req.parse_req_id(raw)
    assert excinfo.value.diagnostic.code == req.E_BAD_REQ_REF
    assert excinfo.value.diagnostic.severity == core.SEVERITY_ERROR


def test_check_mnemonic_accepts_two_to_four_kebab_words():
    for value in ("ab-cd", "a-b-c", "a-b-c-d"):
        assert req.check_mnemonic(value) == value


def test_check_mnemonic_rejects_and_never_sanitizes():
    with pytest.raises(core.ToolError) as excinfo:
        req.check_mnemonic("Reference Recognition")
    assert excinfo.value.diagnostic.code == req.E_BAD_REQ_REF


# ---------------------------------------------------------------------------
# `req next --mnemonic`
# ---------------------------------------------------------------------------


def test_next_with_mnemonic_composes_the_full_id(workspace):
    write_requirements(workspace, "demo", entry(4))
    args = workspace.args(verb="next", tier="demo", mnemonic="reference-recognition")
    result = req.run(args, workspace.ws)
    assert core.exit_code(result) == 0
    assert result.data["next"] == "REQ-005-reference-recognition"


def test_next_without_mnemonic_stays_bare(workspace):
    args = workspace.args(verb="next", tier="demo", mnemonic=None)
    result = req.run(args, workspace.ws)
    assert result.data["next"] == "REQ-001"


@pytest.mark.parametrize(
    "mnemonic", ["Reference Recognition", "x", "a_b", "UPPER-CASE", "one-two-three-four-five"]
)
def test_next_refuses_a_mnemonic_failing_the_grammar_never_sanitizing(workspace, mnemonic):
    args = workspace.args(verb="next", tier="demo", mnemonic=mnemonic)
    result = req.run(args, workspace.ws)
    assert core.exit_code(result) != 0
    assert [item.code for item in result.diagnostics] == [req.E_BAD_REQ_REF]
    # The refused value never appears composed into an allocated id.
    assert result.data is None


def test_next_mnemonic_is_deterministic(workspace):
    write_requirements(workspace, "demo", entry(1))
    args = workspace.args(verb="next", tier="demo", mnemonic="second-attempt")
    first = req.run(args, workspace.ws).to_json()
    second = req.run(args, workspace.ws).to_json()
    assert first == second


# ---------------------------------------------------------------------------
# `req change-resolve` / `change-emit` / `change-close` / `change-list`
# ---------------------------------------------------------------------------


REQ_CHANGE_FRONT_MATTER = (
    "<!-- req-change: %(number)s -->\n"
    "<!-- tier: %(tier)s -->\n"
    "<!-- status: %(status)s -->\n"
    "<!-- date: 2026-01-01 -->\n"
    "%(spec_change)s"
)


def write_req_change(workspace, tier, number, slug, status, spec_change=None):
    spec_line = "<!-- spec-change: %s -->\n" % spec_change if spec_change else ""
    text = REQ_CHANGE_FRONT_MATTER % {
        "number": number,
        "tier": tier,
        "status": status,
        "spec_change": spec_line,
    }
    text += "\n# REQ-CHANGE-%s: %s\n" % (number, slug)
    return workspace.write(
        "context/%s/requirements/changes/REQ-CHANGE-%s-%s.md" % (tier, number, slug), text
    )


def write_change(workspace, repo, number, slug, statusname):
    return workspace.write(
        "context/%s/changes/CHANGE-%s-%s.md" % (repo, number, slug),
        "<!-- change: %s -->\n"
        "<!-- scope: repo -->\n"
        "<!-- repo: %s -->\n"
        "<!-- status: %s -->\n"
        "<!-- date: 2026-01-01 -->\n"
        "\n# CHANGE-%s: %s\n" % (number, repo, statusname, number, slug),
    )


def run_req(workspace, verb, **fields):
    args = workspace.args(verb=verb, **fields)
    result = req.run(args, workspace.ws)
    return result, core.exit_code(result)


def test_change_resolve_creates_when_nothing_is_open(workspace):
    result, code = run_req(workspace, "change-resolve", tier="demo", slug=None)
    assert code == 0
    assert result.data["action"] == "create"
    assert result.data["target"]["number"] == "001"
    assert result.data["target"]["slug"] == req.PLACEHOLDER_SLUG


def test_change_resolve_continues_an_open_record_with_no_spec_change(workspace):
    write_req_change(workspace, "demo", "001", "first-pass", "open")
    result, code = run_req(workspace, "change-resolve", tier="demo", slug=None)
    assert code == 0
    assert result.data["action"] == "continue"
    assert result.data["target"]["number"] == "001"


def test_change_resolve_closed_is_terminal(workspace):
    write_req_change(workspace, "demo", "001", "first-pass", "closed", spec_change="CHANGE-001")
    result, code = run_req(workspace, "change-resolve", tier="demo", slug="next-pass")
    assert code == 0
    assert result.data["action"] == "create"
    assert result.data["target"]["number"] == "002"


def test_change_resolve_open_with_not_required_is_terminal(workspace):
    write_req_change(
        workspace, "demo", "001", "no-delta", "open", spec_change="not-required"
    )
    result, code = run_req(workspace, "change-resolve", tier="demo", slug="next-pass")
    assert code == 0
    assert result.data["action"] == "create"
    assert result.data["target"]["number"] == "002"


def test_change_resolve_allocates_from_the_highest_number_present(workspace):
    write_req_change(workspace, "demo", "001", "a", "closed", spec_change="CHANGE-001")
    write_req_change(workspace, "demo", "003", "b", "closed", spec_change="CHANGE-002")
    result, _code = run_req(workspace, "change-resolve", tier="demo", slug="c")
    assert result.data["target"]["number"] == "004"


def test_change_emit_writes_validated_front_matter(workspace):
    result, code = run_req(
        workspace,
        "change-emit",
        path="context/demo/requirements/changes/REQ-CHANGE-001-first-pass.md",
        tier="demo",
        status="open",
        spec_change=None,
    )
    assert code == 0
    written = workspace.path(
        "context/demo/requirements/changes/REQ-CHANGE-001-first-pass.md"
    ).read_text(encoding="utf-8")
    assert "<!-- req-change: 001 -->" in written
    assert "<!-- tier: demo -->" in written
    assert "<!-- status: open -->" in written
    assert "<!-- date: %s -->" % NOW in written
    assert "spec-change" not in written
    assert core.validate_instance(
        "req-change",
        workspace.path("context/demo/requirements/changes/REQ-CHANGE-001-first-pass.md"),
        workspace.ws,
    ).ok


def test_change_emit_refuses_a_bad_filename(workspace):
    result, code = run_req(
        workspace,
        "change-emit",
        path="context/demo/requirements/changes/not-a-record.md",
        tier="demo",
        status="open",
        spec_change=None,
    )
    assert code == 2
    assert [item.code for item in result.diagnostics] == [core.E_USAGE]


def test_change_close_sets_both_fields_together(workspace):
    write_req_change(workspace, "demo", "001", "first-pass", "open")
    write_change(workspace, "demo", "005", "the-fix", "applied")
    result, code = run_req(
        workspace,
        "change-close",
        path="context/demo/requirements/changes/REQ-CHANGE-001-first-pass.md",
        change="005",
    )
    assert code == 0
    assert result.data["status"] == "closed"
    assert result.data["spec-change"] == "CHANGE-005"
    matter = core.load_front_matter(
        workspace.path("context/demo/requirements/changes/REQ-CHANGE-001-first-pass.md")
    )
    assert matter["status"] == "closed"
    assert matter["spec-change"] == "CHANGE-005"


def test_change_close_refuses_an_already_closed_record(workspace):
    write_req_change(
        workspace, "demo", "001", "first-pass", "closed", spec_change="CHANGE-005"
    )
    write_change(workspace, "demo", "005", "the-fix", "applied")
    result, code = run_req(
        workspace,
        "change-close",
        path="context/demo/requirements/changes/REQ-CHANGE-001-first-pass.md",
        change="005",
    )
    assert code != 0
    assert [item.code for item in result.diagnostics] == [core.E_INVALID_STATE]


def test_change_close_refuses_a_change_that_does_not_exist(workspace):
    write_req_change(workspace, "demo", "001", "first-pass", "open")
    result, code = run_req(
        workspace,
        "change-close",
        path="context/demo/requirements/changes/REQ-CHANGE-001-first-pass.md",
        change="099",
    )
    assert code != 0
    assert [item.code for item in result.diagnostics] == [core.E_NOT_FOUND]
    # Nothing was written: the record stays open.
    matter = core.load_front_matter(
        workspace.path("context/demo/requirements/changes/REQ-CHANGE-001-first-pass.md")
    )
    assert matter["status"] == "open"


def test_change_close_routes_the_slug_through_check_ident(workspace):
    write_req_change(workspace, "demo", "001", "not a valid slug!", "open")
    write_change(workspace, "demo", "005", "the-fix", "applied")
    # The filename on disk carries a slug `check_ident` would reject, so
    # closing it must be refused rather than silently accepted.
    bad_path = "context/demo/requirements/changes/REQ-CHANGE-001-not a valid slug!.md"
    assert workspace.path(bad_path).exists()
    result, code = run_req(workspace, "change-close", path=bad_path, change="005")
    assert code == 2
    assert [item.code for item in result.diagnostics] == [core.E_BAD_IDENT]


def test_change_list_filters_to_open_records(workspace):
    write_req_change(workspace, "demo", "001", "a", "closed", spec_change="CHANGE-001")
    write_req_change(workspace, "demo", "002", "b", "open")
    write_req_change(workspace, "demo", "003", "c", "open", spec_change="not-required")

    all_records, _code = run_req(workspace, "change-list", tier="demo", open=False)
    assert [item["number"] for item in all_records.data["records"]] == ["003", "002", "001"]

    open_only, _code = run_req(workspace, "change-list", tier="demo", open=True)
    assert [item["number"] for item in open_only.data["records"]] == ["002"]


def test_change_list_on_an_empty_tier_is_an_empty_list(workspace):
    result, code = run_req(workspace, "change-list", tier="demo", open=True)
    assert code == 0
    assert result.data["records"] == []
