"""MSHIP's loop, against the artifacts it drives.

``mship`` is a ``SKILL.md`` and runs no code of its own, so what is mechanically
testable is not its judgment but two things: **the decision table it follows**
and **the tool surface it drives**.

The decision table is read out of the skill that owns it, never transcribed
here. The rules' *predicates* are written below -- a rule has to mean something
for a scenario to be answerable -- but their **order comes from the document**,
so a table reordered in ``SKILL.md`` changes what these tests compute. That is
the whole point: precedence *is* the assertion, and
:func:`test_precedence_is_what_produces_the_halt` shows it has teeth by
answering the same scenario differently under a reordered table.

The loop's mechanical steps are exercised against the **real** ``mc.py`` groups
in a synthetic ``tmp_path`` workspace -- ``plan emit``/``resolve``/``reslice``,
``state set-slice``, ``spec depth``/``revision``/``consumers --stale`` -- because
the claims worth checking are claims about those verbs: that a boundary written
before the decision is one a resumed run can read, that a delivered slice cannot
be rewritten, and that the cascade ordering is load-bearing rather than
stylistic.
"""

import io
import json
import re
import statistics
import argparse
from pathlib import Path

import pytest

from conftest import NOW, PLUGIN_ROOT
from tools import core, mc, plan, spec, state

SKILL = PLUGIN_ROOT / "skills" / "mship" / "SKILL.md"

PLAN_ID = "003-demo-plan"
IFACE = "EVENT-BUS"

#: A runnable acceptance step, and one only a person can settle.
EXIT_STEP = {"kind": "exit-code", "command": "true", "description": "it runs"}
PROSE_STEP = {"kind": "prose", "description": "someone looks at it"}


# ---------------------------------------------------------------------------
# Reading the skill -- the document that owns the rules
# ---------------------------------------------------------------------------


def skill_text():
    """Freshly read every time: these tests assert about the file on disk."""
    return SKILL.read_text(encoding="utf-8")


def section(text, heading):
    """The body under ``heading``, up to the next heading of the same depth."""
    depth = len(heading) - len(heading.lstrip("#"))
    body = ("\n" + text).split("\n" + heading, 1)
    assert len(body) == 2, "SKILL.md has no %r section" % (heading,)
    lines = []
    for line in body[1].split("\n")[1:]:
        stripped = line.strip()
        if stripped.startswith("#"):
            if len(stripped) - len(stripped.lstrip("#")) <= depth:
                break
        lines.append(line)
    return "\n".join(lines)


def table_rows(text):
    """Every markdown table row in ``text``, as stripped cells; rules dropped."""
    parsed = []
    for line in text.split("\n"):
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if all(set(cell) <= set("-: ") and cell for cell in cells):
            continue  # the header rule
        parsed.append(cells)
    return parsed


def backticked(cell):
    """Every `code span` in one cell, in order."""
    return re.findall(r"`([^`]+)`", cell)


class Rule:
    """One row of the decision table, as the document states it."""

    def __init__(self, number, key, kind, reason):
        self.number = number
        self.key = key
        self.kind = kind
        self.reason = reason

    def __repr__(self):  # pragma: no cover - failure output only
        return "Rule(%d, %r -> %s/%s)" % (self.number, self.key, self.kind, self.reason)


def decision_rules(text=None):
    """The decision table, parsed out of ``SKILL.md`` in document order."""
    body = section(text or skill_text(), "## Step 5: The Decision")
    rules = []
    for cells in table_rows(body):
        if not cells[0].isdigit():
            continue  # the header row
        outcome = backticked(cells[-1])[0].strip("{}")
        parts = [part.strip().strip('"') for part in outcome.split(",")]
        kind = parts[0]
        reason = parts[1] if kind == "stop" else None
        rules.append(Rule(int(cells[0]), backticked(cells[1])[0], kind, reason))
    return rules


# ---------------------------------------------------------------------------
# The decision -- predicates here, precedence from the document
# ---------------------------------------------------------------------------

#: The order MSHIP-INTERFACE.md ordains. Asserted against the table rather than
#: used in place of it, so the document stays the thing under test.
INTERFACE_ORDER = [
    "halted",
    "acceptance-failed",
    "budget-fired",
    "contract-defect",
    "replannable",
    "gate-asks",
    "otherwise",
]

GATES = ("unverifiable-only", "every-slice", "never")


def slice_result(**fields):
    """One slice's outcome-relevant facts, with the quiet-path defaults."""
    result = {
        "halted": False,
        "acceptance_failed": False,
        "has_prose_step": False,
        "gate": "unverifiable-only",
        "spec_defect": None,
        "findings_imply_wrong_cut": False,
        "cost_usd": None,
        "completed_costs": [],
    }
    unknown = set(fields) - set(result)
    assert not unknown, "unknown scenario field(s): %s" % sorted(unknown)
    result.update(fields)
    return result


PREDICATES = {
    "halted": lambda s: s["halted"],
    "acceptance-failed": lambda s: s["acceptance_failed"],
    "budget-fired": lambda s: breaker_fires(s["completed_costs"], s["cost_usd"]),
    "contract-defect": lambda s: s["spec_defect"] == "contradicts-contract",
    "replannable": lambda s: (
        s["spec_defect"] == "inexpressible" or s["findings_imply_wrong_cut"]
    ),
    # every-slice asks at every boundary; unverifiable-only only where the
    # acceptance needs an eye; never asks at none.
    "gate-asks": lambda s: s["gate"] == "every-slice"
    or (s["gate"] == "unverifiable-only" and s["has_prose_step"]),
    "otherwise": lambda s: True,
}


def decide(result, rules=None):
    """``(kind, reason)`` -- the first documented rule that applies."""
    for rule in rules if rules is not None else decision_rules():
        if PREDICATES[rule.key](result):
            return rule.kind, rule.reason
    raise AssertionError("the decision table matched nothing: %r" % (result,))


def test_the_decision_table_is_seven_rules_in_the_interfaces_order():
    """Seven rules, numbered 1--7, in the order the INTERFACE ordains."""
    rules = decision_rules()
    assert [rule.number for rule in rules] == list(range(1, 8))
    assert [rule.key for rule in rules] == INTERFACE_ORDER


def test_the_table_states_that_the_first_matching_rule_wins():
    body = section(skill_text(), "## Step 5: The Decision")
    assert "the first rule that applies wins" in body


def test_every_documented_condition_has_a_predicate_and_the_converse():
    """No rule is answered by accident, and none is left unexercised."""
    assert sorted(rule.key for rule in decision_rules()) == sorted(PREDICATES)


@pytest.mark.parametrize(
    "scenario,expected",
    [
        (slice_result(), ("continue", None)),
        (
            slice_result(has_prose_step=True, gate="unverifiable-only"),
            ("ask", None),
        ),
        (slice_result(has_prose_step=True, gate="never"), ("continue", None)),
        (slice_result(has_prose_step=True, gate="every-slice"), ("ask", None)),
        (slice_result(gate="every-slice"), ("ask", None)),
        (slice_result(halted=True), ("stop", "halt")),
        (slice_result(acceptance_failed=True), ("stop", "acceptance-failed")),
        (
            slice_result(completed_costs=[1.0, 1.0, 1.0], cost_usd=4.0),
            ("stop", "budget"),
        ),
        (
            slice_result(spec_defect="contradicts-contract"),
            ("cascade", None),
        ),
        (slice_result(spec_defect="inexpressible"), ("replan", None)),
        (slice_result(findings_imply_wrong_cut=True), ("replan", None)),
    ],
    ids=[
        "all-exit-code-passes",
        "prose-under-unverifiable-only",
        "prose-under-never",
        "prose-under-every-slice",
        "exit-code-under-every-slice",
        "mexecute-halted",
        "acceptance-command-failed",
        "cost-over-the-breaker",
        "spec-defect-naming-a-contract",
        "spec-defect-replanning-absorbs",
        "findings-imply-the-cut-is-wrong",
    ],
)
def test_the_decision_table_row_by_row(scenario, expected):
    assert decide(scenario) == expected


def test_a_cost_over_the_breaker_with_only_two_completed_slices_does_not_stop():
    """Below the minimum sample there is no baseline, so rule 3 cannot fire."""
    assert decide(slice_result(completed_costs=[1.0, 1.0], cost_usd=400.0)) == (
        "continue",
        None,
    )


def test_a_halt_outranks_a_concurrent_prose_acceptance():
    """The case the precedence exists for: both rules match, rule 1 wins."""
    both = slice_result(halted=True, has_prose_step=True, gate="unverifiable-only")
    assert PREDICATES["gate-asks"](both) is True
    assert decide(both) == ("stop", "halt")


def test_precedence_is_what_produces_the_halt():
    """Teeth for the case above: reorder the table and the answer changes.

    The predicates are unchanged -- only the documented order moves -- so this
    is the assertion that the *document's* ordering is what is being tested.
    """
    both = slice_result(halted=True, has_prose_step=True)
    rules = decision_rules()
    reordered = [rule for rule in rules if rule.key == "gate-asks"] + [
        rule for rule in rules if rule.key != "gate-asks"
    ]
    assert decide(both, rules) == ("stop", "halt")
    assert decide(both, reordered) == ("ask", None)


def test_a_failed_acceptance_outranks_a_spec_defect():
    """Re-planning around work that did not run is planning against nothing."""
    both = slice_result(acceptance_failed=True, spec_defect="contradicts-contract")
    assert decide(both) == ("stop", "acceptance-failed")


def test_a_contract_defect_outranks_one_re_planning_could_absorb():
    both = slice_result(
        spec_defect="contradicts-contract", findings_imply_wrong_cut=True
    )
    assert decide(both) == ("cascade", None)


def test_rules_one_to_three_are_the_stops_and_rule_six_is_the_only_question():
    """`REQ-008`'s exception is rule 6 alone; the stops ask nobody anything."""
    rules = {rule.number: rule for rule in decision_rules()}
    assert [rules[number].kind for number in (1, 2, 3)] == ["stop"] * 3
    assert [rules[number].reason for number in (1, 2, 3)] == [
        "halt",
        "acceptance-failed",
        "budget",
    ]
    assert [rule.number for rule in decision_rules() if rule.kind == "ask"] == [6]
    body = section(skill_text(), "## Step 5: The Decision")
    assert "Rules 1–3 are outcomes reported, not questions asked" in body
    assert "the whole of `REQ-008`'s exception" in body


def test_no_gate_policy_can_reach_rule_six_under_never():
    """`never` asks at no boundary, whatever the acceptance looks like."""
    for prose in (True, False):
        assert decide(slice_result(gate="never", has_prose_step=prose)) == (
            "continue",
            None,
        )


# ---------------------------------------------------------------------------
# The gate policy, and the acceptance an unasked boundary records
# ---------------------------------------------------------------------------


def gate_table():
    """``gate -> the recorded-acceptance cell``, read out of the skill."""
    body = section(skill_text(), "### The gate policy")
    recorded = {}
    for cells in table_rows(body):
        names = backticked(cells[0])
        if not names or names[0] not in GATES:
            continue
        recorded[names[0]] = cells[-1]
    return recorded


def test_the_gate_table_covers_exactly_the_three_policies():
    assert sorted(gate_table()) == sorted(GATES)


def test_under_gate_never_an_unverifiable_acceptance_is_recorded_unconfirmed():
    """Never silently `pass`: `unconfirmed` is a third value, and it is used."""
    cell = gate_table()["never"]
    assert "unconfirmed" in backticked(cell)
    assert "pass" not in backticked(cell)


@pytest.mark.parametrize("kind", ["plan-state", "slice-report"])
def test_unconfirmed_is_a_value_both_schemas_admit(kind):
    """The skill records it through the tool, so both writers must accept it."""
    schema = core.load_schema(kind)
    if kind == "plan-state":
        enum = schema["$defs"]["sliceState"]["properties"]["acceptance"]["enum"]
    else:
        enum = schema["properties"]["acceptance"]["enum"]
    assert "unconfirmed" in enum
    assert {"pass", "fail"} <= set(enum)


def test_every_documented_outcome_is_one_the_schemas_record():
    """A decision the state writer cannot express is one that gets lost."""
    documented = {rule.kind for rule in decision_rules()}
    recorded = set(core.load_schema("slice-report")["properties"]["outcome"]["enum"])
    state_enum = core.load_schema("plan-state")["$defs"]["sliceState"]["properties"]
    assert documented <= recorded
    assert documented <= set(state_enum["outcome"]["enum"])


def test_the_stop_reasons_are_the_three_the_datamodel_names():
    reasons = {rule.reason for rule in decision_rules() if rule.kind == "stop"}
    assert reasons == {"halt", "acceptance-failed", "budget"}


# ---------------------------------------------------------------------------
# The budget breaker -- parameters from the document, arithmetic here
# ---------------------------------------------------------------------------

#: The baselines this test knows how to compute. A document naming anything
#: else fails loudly here rather than being silently approximated.
BASELINES = {"median": statistics.median}


def breaker_parameters():
    """``{statistic, minimum, threshold, excluded}``, read out of the skill."""
    body = section(skill_text(), "### The budget breaker")
    parameters = {}
    for cells in table_rows(body):
        name = cells[0].lower()
        spans = backticked(cells[1])
        if name.startswith("baseline"):
            parameters["statistic"] = spans[0]
        elif name.startswith("minimum"):
            parameters["minimum"] = int(spans[0])
        elif name.startswith("threshold"):
            parameters["threshold"] = float(spans[0])
        elif name.startswith("excluded"):
            parameters["excluded"] = spans
    assert sorted(parameters) == ["excluded", "minimum", "statistic", "threshold"]
    return parameters


def breaker_sample(costs):
    """The completed slices the breaker compares against."""
    assert "null" in breaker_parameters()["excluded"]
    return [cost for cost in costs if cost is not None]


def breaker_fires(completed_costs, cost_usd):
    """The breaker, as the parameters table parameterises it."""
    parameters = breaker_parameters()
    sample = breaker_sample(completed_costs)
    if len(sample) < parameters["minimum"] or cost_usd is None:
        return False
    baseline = BASELINES[parameters["statistic"]](sample)
    return cost_usd >= parameters["threshold"] * baseline


def test_the_parameters_are_the_documented_ones():
    parameters = breaker_parameters()
    assert parameters["statistic"] in BASELINES
    assert parameters["minimum"] == 3
    assert parameters["threshold"] == 4.0
    assert "null" in parameters["excluded"]


def test_the_breaker_fires_at_the_threshold_and_not_below_it():
    assert breaker_fires([1.0, 1.0, 1.0], 4.0) is True
    assert breaker_fires([1.0, 1.0, 1.0], 3.99) is False


def test_the_baseline_is_a_median_not_a_mean():
    """`[1, 1, 1, 20]` must not raise the baseline enough to suppress a firing.

    A mean baseline is `5.75`, whose `4x` is `23`: the same slice would sail
    through. The case is written so the two answers differ.
    """
    costs = [1.0, 1.0, 1.0, 20.0]
    assert breaker_fires(costs, 4.0) is True
    assert 4.0 < breaker_parameters()["threshold"] * statistics.mean(costs)


def test_fewer_than_three_completed_slices_never_fires():
    for costs in ([], [1.0], [1.0, 1.0]):
        assert breaker_fires(costs, 1_000_000.0) is False


def test_a_null_cost_is_excluded_from_the_sample_not_counted_as_zero():
    assert breaker_sample([4.0, None, 4.0]) == [4.0, 4.0]
    # Two real observations and two blanks: still below the minimum sample.
    # Counted as zero the sample would be four entries with a median of 2.0,
    # and a cost of 10.0 would fire.
    assert breaker_fires([4.0, 4.0, None, None], 10.0) is False
    counted_as_zero = [0.0 if cost is None else cost for cost in [4.0, 4.0, None, None]]
    assert 10.0 >= 4.0 * statistics.median(counted_as_zero)


def test_the_baseline_is_observed_cost_and_never_a_pre_run_estimate():
    """`REQ-024` asks for evidence; a figure quoted up front is prediction."""
    body = section(skill_text(), "### The budget breaker")
    assert "never an estimate made before the run" in body
    telemetry = core.load_schema("slice-report")["$defs"]["telemetry"]
    assert "never a pre-run estimate" in telemetry["description"]
    assert sorted(telemetry["properties"]) == ["cost_usd", "tokens", "wall_clock_s"]


def test_the_breaker_reads_a_cost_field_the_telemetry_type_carries():
    parameters = section(skill_text(), "### The budget breaker")
    assert "cost_usd" in parameters
    assert "cost_usd" in core.load_schema("plan-state")["$defs"]["telemetry"]["properties"]


# ---------------------------------------------------------------------------
# The tool surface the skill names
# ---------------------------------------------------------------------------

_GLOBAL_FLAGS = {"--json"}
_GLOBAL_OPTS_WITH_VALUE = {"--workspace", "--now"}


def registered_options():
    """``(group, verb) -> option strings``, read out of each group's register."""
    options = {}
    for name in mc.GROUPS:
        parser = argparse.ArgumentParser(prog="mc.py")
        subparsers = parser.add_subparsers(dest="group")
        mc.load_group(name).register(subparsers)
        for action in subparsers.choices[name]._actions:
            if not isinstance(action, argparse._SubParsersAction):
                continue
            for verb, verb_parser in action.choices.items():
                flags = set()
                for verb_action in verb_parser._actions:
                    flags |= set(verb_action.option_strings)
                options[(name, verb)] = flags
    return options


def documented_invocations(text=None):
    """``(group, verb, [rest])`` for every ``mc.py`` line the skill spells out."""
    for match in re.finditer(r"mc\.py((?:[ \t]+[^\s`\n]+)+)", text or skill_text()):
        tokens = match.group(1).split()
        index = 0
        while index < len(tokens):
            if tokens[index] in _GLOBAL_OPTS_WITH_VALUE:
                index += 2
            elif tokens[index] in _GLOBAL_FLAGS:
                index += 1
            else:
                break
        if index + 1 >= len(tokens):
            continue
        yield tokens[index], tokens[index + 1], tokens[index + 2 :]


def test_the_skill_documents_the_verbs_the_loop_is_built_from():
    """Guards the derivation below: no invocations would make it vacuous."""
    named = {(group, verb) for group, verb, _rest in documented_invocations()}
    assert {
        ("plan", "resolve"),
        ("plan", "slices"),
        ("plan", "reslice"),
        ("state", "set-slice"),
        ("spec", "depth"),
        ("spec", "consumers"),
    } <= named


def test_every_option_the_skill_documents_is_one_its_verb_accepts():
    """A skill passing a flag the verb never registered fails at run time,
    where nobody is reading the exit code, rather than here."""
    registered = registered_options()
    unknown = []
    for group, verb, rest in documented_invocations():
        if group.startswith(("<", "-")) or verb.startswith(("<", "-")):
            continue
        accepted = registered.get((group, verb))
        if accepted is None:
            unknown.append("mc.py %s %s" % (group, verb))
            continue
        for token in rest:
            token = token.strip("[](),.`").split("=")[0]
            if token.startswith("--") and len(token) > 2 and token not in accepted:
                unknown.append("mc.py %s %s %s" % (group, verb, token))
    assert unknown == []


def test_the_skill_never_claims_a_per_slice_telemetry_write():
    """The per-slice cost series lives in the run record because no ``state``
    verb writes it: ``set-slice`` takes status, acceptance and outcome only."""
    accepted = registered_options()[("state", "set-slice")]
    assert {"--status", "--acceptance", "--outcome"} <= accepted
    assert not [flag for flag in accepted if "telemetry" in flag or "cost" in flag]
    assert "the per-slice cost series lives in the run record" in skill_text()


# ---------------------------------------------------------------------------
# The loop against the real tool -- fixtures
# ---------------------------------------------------------------------------


def _story(story_id, wave):
    module = story_id.rsplit("-", 1)[-1]
    return story_id, {
        "repo": "demo",
        "module": module,
        "wave": wave,
        "prerequisites": [],
        "target_paths": ["src/%s.py" % module.lower()],
        "validation": {"post_story": [{"kind": "prose", "description": "it holds"}]},
    }


def _slice_draft(slice_id, stories, acceptance=None, name=None):
    """A slice in the shape ``plan emit``/``plan reslice`` read on stdin."""
    return {
        "slice": slice_id,
        "name": name or "slice %s" % slice_id,
        "behavior": "behaviour %s runs end to end" % slice_id,
        "acceptance": list(acceptance if acceptance is not None else [EXIT_STEP]),
        "stories": list(stories),
    }


SLICE_ZERO = ["01-01-demo-ALPHA"]
SLICE_ONE = ["02-01-demo-BETA"]
SLICES = [_slice_draft("00", SLICE_ZERO), _slice_draft("01", SLICE_ONE)]


def _plan_run(workspace, **fields):
    result = plan.run(workspace.args(**fields), workspace.ws)
    return result, core.exit_code(result)


def _state_run(workspace, **fields):
    result = state.run(workspace.args(**fields), workspace.ws)
    return result, core.exit_code(result)


def _spec_run(workspace, **fields):
    result = spec.run(workspace.args(**fields), workspace.ws)
    return result, core.exit_code(result)


def _emit(workspace, slices=None):
    """A two-slice plan of two stories, emitted through the real verb."""
    draft = {
        "stories": dict([_story("01-01-demo-ALPHA", 1), _story("02-01-demo-BETA", 2)]),
        "slices": [dict(entry) for entry in (SLICES if slices is None else slices)],
    }
    result, code = _plan_run(
        workspace,
        verb="emit",
        plan_id=PLAN_ID,
        stdin=io.StringIO(json.dumps(draft)),
    )
    assert code == 0, [item.render() for item in result.diagnostics]
    return workspace


def _resolve(workspace):
    result, code = _plan_run(workspace, verb="resolve", plan_id=PLAN_ID)
    assert code == 0, [item.render() for item in result.diagnostics]
    return result.data


def _slice_state(workspace, slice_id):
    document = core.load_yaml(
        workspace.path("context/project/plans/%s/state.yaml" % PLAN_ID)
    )
    for entry in document.get("slices", []):
        if entry.get("slice") == slice_id:
            return entry
    return None


def _reslice(workspace, drafts):
    return _plan_run(
        workspace,
        verb="reslice",
        plan_id=PLAN_ID,
        stdin=io.StringIO(json.dumps(drafts)),
    )


def _plan_bytes(workspace):
    directory = workspace.path("context/project/plans/%s" % PLAN_ID)
    return {path.name: path.read_bytes() for path in sorted(directory.iterdir())}


@pytest.fixture
def emitted(workspace):
    return _emit(workspace)


# ---------------------------------------------------------------------------
# Resume -- state written before the decision is state a resumed run can read
# ---------------------------------------------------------------------------


def test_a_boundary_recorded_before_the_decision_resumes_at_the_same_slice(emitted):
    """Interrupted after ``set-slice`` and before deciding: the slice is the
    resume point, and what it proved is on record rather than lost."""
    _state_run(
        emitted,
        verb="set-slice",
        plan_id=PLAN_ID,
        slice_id="00",
        status="in-progress",
        acceptance="pass",
    )
    assert _resolve(emitted)["resume_slice"] == "00"
    entry = _slice_state(emitted, "00")
    assert entry["status"] == "in-progress" and entry["acceptance"] == "pass"
    # Null until decided: the write before the decision records no outcome.
    assert "outcome" not in entry


def test_the_resumed_slice_is_decided_once_more_and_then_left_behind(emitted):
    """"At most once more": once the outcome is recorded the loop moves on."""
    for status, extra in (
        ("in-progress", {"acceptance": "pass"}),
        ("applied", {"outcome": "continue"}),
    ):
        result, code = _state_run(
            emitted,
            verb="set-slice",
            plan_id=PLAN_ID,
            slice_id="00",
            status=status,
            **extra
        )
        assert code == 0, [item.render() for item in result.diagnostics]

    entry = _slice_state(emitted, "00")
    assert entry == {
        "slice": "00",
        "status": "applied",
        "acceptance": "pass",
        "outcome": "continue",
    }
    assert _resolve(emitted)["resume_slice"] == "01"


def test_a_boundary_that_recorded_nothing_resumes_with_the_slice_unproven(emitted):
    """Why the write comes first: deciding first and recording afterwards
    leaves an interruption with a slice that has to be run again to find out
    what it proved."""
    assert _resolve(emitted)["resume_slice"] == "00"
    assert _slice_state(emitted, "00") == {"slice": "00", "status": "pending"}


def test_the_ledger_counters_move_with_the_slice_in_one_write(emitted):
    _state_run(
        emitted,
        verb="set-slice",
        plan_id=PLAN_ID,
        slice_id="00",
        status="applied",
        acceptance="pass",
        outcome="continue",
    )
    ledger = core.load_yaml(emitted.path("context/project/state.yaml"))
    assert ledger["plans"][PLAN_ID]["slices_total"] == 2
    assert ledger["plans"][PLAN_ID]["slices_applied"] == 1


# ---------------------------------------------------------------------------
# Delivered slices are immutable; outstanding ones are not
# ---------------------------------------------------------------------------


def _apply_slice_zero(workspace):
    result, code = _state_run(
        workspace,
        verb="set-slice",
        plan_id=PLAN_ID,
        slice_id="00",
        status="applied",
        acceptance="pass",
        outcome="continue",
    )
    assert code == 0, [item.render() for item in result.diagnostics]
    return workspace


@pytest.mark.parametrize(
    "drafts,why",
    [
        (
            [_slice_draft("00", SLICE_ZERO, name="recut"), _slice_draft("01", SLICE_ONE)],
            "altered",
        ),
        ([_slice_draft("01", SLICE_ZERO + SLICE_ONE)], "dropped"),
        (
            [_slice_draft("00", SLICE_ONE), _slice_draft("01", SLICE_ZERO)],
            "reordered",
        ),
    ],
    ids=["altered", "dropped", "reordered"],
)
def test_reslice_refuses_a_draft_that_touches_an_applied_slice(emitted, drafts, why):
    """The refusal `mship` surfaces rather than retries -- and it persists
    nothing, so the plan on disk is byte-identical afterwards."""
    _apply_slice_zero(emitted)
    before = _plan_bytes(emitted)

    result, code = _reslice(emitted, drafts)
    assert code == 1 and not result.ok, why
    assert [item.code for item in result.diagnostics] == [core.E_INVALID_STATE]
    assert result.data["written"] == []
    assert _plan_bytes(emitted) == before


def test_reslice_rewrites_the_outstanding_slices_around_a_delivered_one(emitted):
    """What is merely planned stays revisable while what shipped does not."""
    _apply_slice_zero(emitted)
    drafts = [
        _slice_draft("00", SLICE_ZERO),
        _slice_draft("01", SLICE_ONE, name="recut after slice 00"),
    ]
    result, code = _reslice(emitted, drafts)
    assert code == 0, [item.render() for item in result.diagnostics]

    graph = core.load_yaml(emitted.path("context/project/plans/%s/plan.yaml" % PLAN_ID))
    assert [entry["name"] for entry in graph["slices"]] == [
        "slice 00",
        "recut after slice 00",
    ]
    # The delivered slice keeps its recorded status through the rewrite.
    assert _slice_state(emitted, "00")["status"] == "applied"


def test_a_draft_in_the_datamodels_own_field_names_is_refused(emitted):
    """Why the skill documents the wire shape: `SliceDraft`'s `id`/`modules`
    are not what the graph admits, and a draft using them writes nothing."""
    before = _plan_bytes(emitted)
    by_id = [
        {
            "id": "00",
            "name": "slice 00",
            "behavior": "it runs",
            "acceptance": [EXIT_STEP],
            "modules": ["ALPHA", "BETA"],
        }
    ]
    result, code = _reslice(emitted, by_id)
    assert code == 2 and [item.code for item in result.diagnostics] == [core.E_BAD_IDENT]
    assert _plan_bytes(emitted) == before

    extra = dict(_slice_draft("00", SLICE_ZERO + SLICE_ONE), modules=["ALPHA", "BETA"])
    result, code = _reslice(emitted, [extra])
    assert code == 1 and not result.ok
    assert [item.code for item in result.diagnostics] == [core.E_SCHEMA_INVALID]
    assert _plan_bytes(emitted) == before


def test_the_skill_says_a_refusal_is_surfaced_rather_than_retried():
    body = section(skill_text(), "## Re-Planning")
    assert "Surface a refusal; never retry it" in body


# ---------------------------------------------------------------------------
# Cascade ordering -- the bug that produces no error
# ---------------------------------------------------------------------------


def _shared_interface(workspace, revision=1):
    for facet in ("OVERVIEW", "DATAMODEL", "INTERFACE"):
        workspace.write(
            "context/shared/spec/%s/%s-%s.md" % (IFACE, IFACE, facet),
            "\n# %s %s\n" % (IFACE, facet),
        )
    workspace.write(
        "context/shared/spec/CATALOG.yaml",
        "version: 1\n"
        "scope: shared\n"
        "interfaces:\n"
        "  %s:\n"
        "    revision: %d\n"
        "    files:\n"
        "      - path: context/shared/spec/%s/%s-INTERFACE.md\n"
        "        facet: interface\n" % (IFACE, revision, IFACE, IFACE),
    )
    return workspace


def _delivered_against(workspace, revision):
    """A delivered story recording the revision it was built against."""
    result, code = _state_run(
        workspace,
        verb="set-story",
        plan_id=PLAN_ID,
        story_id="01-01-demo-ALPHA",
        status="applied",
        attempt=1,
        branch="mexec/%s/01-01-demo-ALPHA/r1/1" % PLAN_ID,
        contract_revisions=json.dumps({IFACE: revision}),
    )
    assert code == 0, [item.render() for item in result.diagnostics]
    return workspace


def _bump(workspace):
    result, code = _spec_run(workspace, verb="revision", interface=IFACE, bump=True)
    assert code == 0, [item.render() for item in result.diagnostics]
    return result.data["revision"]


def _stale(workspace):
    result, code = _spec_run(workspace, verb="consumers", interface=IFACE, stale=True)
    assert code == 0, [item.render() for item in result.diagnostics]
    return result.data["stale"]


def _next_slice_draft(workspace, order):
    """The next slice's draft, composed in the given order.

    ``cascade-then-stale`` is what the skill documents; ``stale-then-cascade``
    is the reversal, kept here so the failure it causes is observable.
    """
    if order == "cascade-then-stale":
        _bump(workspace)
        stale = _stale(workspace)
    else:
        stale = _stale(workspace)
        _bump(workspace)
    remediation = [item["story_id"] for item in stale]
    return _slice_draft("01", SLICE_ONE + remediation), remediation


@pytest.fixture
def cascading(workspace):
    _emit(workspace)
    _state_run(workspace, verb="run-increment", plan_id=PLAN_ID)
    _delivered_against(workspace, 1)
    _apply_slice_zero(workspace)
    return _shared_interface(workspace, revision=1)


def test_nothing_is_stale_until_the_revision_moves(cascading):
    """Delivered work built against the current revision is in step."""
    assert _stale(cascading) == []


def test_the_documented_order_puts_the_remediation_in_the_next_slices_draft(cascading):
    draft, remediation = _next_slice_draft(cascading, "cascade-then-stale")
    assert remediation == ["01-01-demo-ALPHA"]
    assert draft["stories"] == SLICE_ONE + ["01-01-demo-ALPHA"]


def test_the_reversed_order_silently_drops_the_remediation(cascading):
    """Asked before the bump, `--stale` finds everything in step and returns
    an empty list: no error, no diagnostic, no missing exit code -- the
    remediation simply never reaches the draft."""
    draft, remediation = _next_slice_draft(cascading, "stale-then-cascade")
    assert remediation == []
    assert draft["stories"] == SLICE_ONE
    # And the revision did move, so the reversal is invisible from the catalog.
    assert _spec_run(cascading, verb="revision", interface=IFACE)[0].data["revision"] == 2
    # The work is stale; only the draft that was supposed to schedule it is not.
    assert [item["story_id"] for item in _stale(cascading)] == ["01-01-demo-ALPHA"]


def test_the_stale_entry_names_the_delivered_story_and_the_gap(cascading):
    _bump(cascading)
    assert _stale(cascading) == [
        {"plan_id": PLAN_ID, "story_id": "01-01-demo-ALPHA", "built_against": 1}
    ]


def test_the_skill_documents_the_cascade_in_that_order_with_its_failure_mode():
    """The order is read out of the section, not asserted from memory."""
    body = section(skill_text(), "### Cascade — the ordering is load-bearing")
    sequence = [
        (group, verb, rest)
        for group, verb, rest in documented_invocations(body)
    ]
    assert [(group, verb) for group, verb, _rest in sequence] == [
        ("spec", "revision"),
        ("spec", "consumers"),
        ("plan", "reslice"),
    ]
    assert "--bump" in sequence[0][2]
    assert "--stale" in sequence[1][2]
    assert "empty stale list and silently drops the remediation" in body


def test_remediation_is_appended_to_the_next_slice_never_the_one_that_shipped():
    body = section(skill_text(), "### Cascade — the ordering is load-bearing")
    assert "never edited into the slice that shipped them" in body


# ---------------------------------------------------------------------------
# Step 1 -- a catalog with no depth field never triggers a deepen
# ---------------------------------------------------------------------------


def _depth_catalog(workspace, declared=None):
    entry = "  ALPHA:\n    layer: L1-core\n"
    if declared is not None:
        entry += "    depth: %s\n" % declared
    entry += (
        "    files:\n"
        "      - path: context/demo/spec/ALPHA/ALPHA-OVERVIEW.md\n"
        "        facet: overview\n"
    )
    workspace.write("context/demo/spec/ALPHA/ALPHA-OVERVIEW.md", "\n# ALPHA\n")
    workspace.write(
        "context/demo/spec/CATALOG.yaml",
        "version: 1\nrepo: demo\nlayers:\n  L1-core:\n    modules: [ALPHA]\nmodules:\n"
        + entry,
    )
    return workspace


def _depth(workspace):
    result, code = _spec_run(workspace, verb="depth", target="demo", module="ALPHA")
    assert code == 0, [item.render() for item in result.diagnostics]
    return result.data


def test_a_catalog_with_no_depth_field_reports_full_so_step_one_never_fires(workspace):
    """The whole of the backward-compatibility story: a plan carried over from
    before spec depth existed runs with the deepen step never invoked."""
    _depth_catalog(workspace, declared=None)
    reported = _depth(workspace)
    assert reported["depth"] == "full" and reported["declared"] is None
    assert reported["written"] == []


def test_only_a_module_the_catalog_calls_contract_is_deepened(workspace):
    _depth_catalog(workspace, declared="contract")
    assert _depth(workspace)["depth"] == "contract"
    _depth_catalog(workspace, declared="full")
    assert _depth(workspace)["depth"] == "full"


def test_the_skill_asks_the_catalog_rather_than_reading_the_tree():
    body = section(skill_text(), "### Step 1: Deepen — ask the catalog, do not infer")
    assert ("spec", "depth") in {
        (group, verb) for group, verb, _rest in documented_invocations(body)
    }
    assert "writes no facet and never flips the depth field" in body
    assert "skips the step silently" in body


# ---------------------------------------------------------------------------
# The shape of the skill itself
# ---------------------------------------------------------------------------


def test_the_skill_declares_its_frontmatter_name_and_a_trigger_description():
    lines = skill_text().split("\n")
    assert lines[0] == "---"
    front = lines[1 : lines.index("---", 1)]
    assert "name: mship" in front
    description = [line for line in front if line.startswith("description:")]
    assert len(description) == 1
    assert "mship" in description[0] and len(description[0]) > 80


def test_the_five_step_sequence_is_documented_in_order():
    body = section(skill_text(), "## The Per-Slice Sequence")
    steps = [cells[0] for cells in table_rows(body) if cells[0][0].isdigit()]
    assert steps == [
        "1. Deepen",
        "2. Plan",
        "3. Execute",
        "4. Verify",
        "5. Decide",
    ]


def test_the_skill_writes_no_spec_plan_code_or_finding_of_its_own():
    text = skill_text()
    assert "write spec, plan, code or conformance findings" in text
    assert "write a spec, a plan, code or a conformance finding" in text


def test_the_state_write_precedes_the_decision_in_the_documented_sequence():
    body = section(skill_text(), "### State is written before the decision, never after")
    fenced = body.split("```")[1]
    assert fenced.index("--acceptance") < fenced.index("… decide …")
    assert fenced.index("… decide …") < fenced.index("--outcome")
    assert "Deciding first and recording afterwards inverts that" in body
