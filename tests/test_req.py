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
