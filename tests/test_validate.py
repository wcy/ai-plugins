"""The ``validate`` command group.

Establishes what TOOLS-TESTING.md requires of it: every kind and every alias
resolves, an unknown kind is rejected, each documented exit code is produced by
the condition documented for it, and a missing file fails alone without
aborting the batch. The group is imported and called directly -- never through
``argv`` -- and asserted on the ``Result`` envelope.

The core I/O the group stands on (deterministic YAML emission in schema key
order, front-matter read/write) is covered at the bottom, because waves 2-4
build their emitters on it.
"""

import pytest

from conftest import INVALID_INSTANCES, KIND_ALIASES, NOW, VALID_INSTANCES
from tools import core, validate


def _run(workspace, kind, files):
    args = workspace.args(kind=kind, files=[str(item) for item in files])
    result = validate.run(args, workspace.ws)
    return result, core.exit_code(result)


# ---------------------------------------------------------------------------
# Kinds and aliases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", sorted(VALID_INSTANCES))
def test_every_canonical_kind_resolves_and_validates(workspace, kind):
    path = workspace.add_instance(kind)
    result, code = _run(workspace, kind, [path])
    assert code == 0
    assert result.ok is True
    assert result.diagnostics == []
    assert result.data["lines"] == ["OK    %s (%s)" % (path, kind)]


@pytest.mark.parametrize("alias,canonical", sorted(KIND_ALIASES.items()))
def test_every_alias_resolves_to_its_canonical_schema(workspace, alias, canonical):
    path = workspace.add_instance(canonical)
    result, code = _run(workspace, alias, [path])
    assert code == 0
    assert result.ok is True
    # The alias, not the canonical name, is echoed back in the human output.
    assert result.data["lines"] == ["OK    %s (%s)" % (path, alias)]
    assert core.resolve_kind(alias) == core.resolve_kind(canonical)


def test_alias_table_is_exactly_the_eight_documented_aliases():
    assert core.KIND_ALIASES == KIND_ALIASES
    assert set(core.CANONICAL_KINDS) == set(VALID_INSTANCES)
    assert len(core.CANONICAL_KINDS) == 10
    assert len(core.KIND_ALIASES) == 8


@pytest.mark.parametrize("kind", sorted(VALID_INSTANCES))
def test_explicit_schema_filename_resolves(workspace, kind):
    path = workspace.add_instance(kind)
    explicit = "%s.schema.json" % kind
    result, code = _run(workspace, explicit, [path])
    assert code == 0
    assert result.data["lines"] == ["OK    %s (%s)" % (path, explicit)]


def test_req_change_frontmatter_resolves_and_validates_a_conforming_record(workspace):
    path = workspace.add_instance("req-change-frontmatter")
    result, code = _run(workspace, "req-change-frontmatter", [path])
    assert code == 0
    assert result.ok is True


def test_req_change_frontmatter_rejects_a_closed_record_with_no_spec_change(workspace):
    path = workspace.write(
        "instances/bad-req-change.md",
        "<!-- req-change: 001 -->\n"
        "<!-- tier: demo -->\n"
        "<!-- status: closed -->\n"
        "<!-- date: 2026-01-01 -->\n"
        "\n# REQ-CHANGE-001: Tightened Scope\n",
    )
    result, code = _run(workspace, "req-change-frontmatter", [path])
    assert code == 1
    assert [d.code for d in result.diagnostics] == [core.E_SCHEMA_INVALID]


def test_unknown_kind_is_rejected_with_exit_2(workspace):
    path = workspace.add_instance("catalog")
    result, code = _run(workspace, "nosuchkind", [path])
    assert code == 2
    assert [d.code for d in result.diagnostics] == [core.E_UNKNOWN_KIND]
    assert "nosuchkind" in result.diagnostics[0].message
    # Every canonical kind is offered, so the failure is actionable.
    for kind in core.CANONICAL_KINDS:
        assert kind in result.diagnostics[0].message
    # Nothing was validated: an unresolvable kind aborts before the batch.
    assert result.data["files"] == []
    assert result.data["lines"] == []


def test_unknown_kind_is_checked_before_any_file_is_read(workspace):
    result, code = _run(workspace, "nosuchkind", [workspace.path("instances/absent.yaml")])
    assert code == 2
    assert [d.code for d in result.diagnostics] == [core.E_UNKNOWN_KIND]


# ---------------------------------------------------------------------------
# The three documented exit codes
# ---------------------------------------------------------------------------


def test_exit_0_when_every_file_validates(workspace):
    first = workspace.add_instance("catalog")
    second = workspace.write("instances/second.yaml", VALID_INSTANCES["catalog"][1])
    result, code = _run(workspace, "catalog", [first, second])
    assert code == 0
    assert result.ok is True
    assert result.data["lines"] == [
        "OK    %s (catalog)" % first,
        "OK    %s (catalog)" % second,
    ]


@pytest.mark.parametrize("kind", sorted(INVALID_INSTANCES))
def test_exit_1_when_a_file_fails_its_schema(workspace, kind):
    path = workspace.add_instance(kind, table=INVALID_INSTANCES)
    result, code = _run(workspace, kind, [path])
    assert code == 1
    assert result.ok is False
    assert [d.code for d in result.diagnostics] == [core.E_SCHEMA_INVALID]
    lines = result.data["lines"]
    assert lines[0] == "FAIL  %s (%s):" % (path, kind)
    assert len(lines) > 1
    assert all(line.startswith("        - ") for line in lines[1:])
    assert result.data["files"][0]["ok"] is False
    assert result.data["files"][0]["errors"]


def test_exit_1_when_a_file_is_missing(workspace):
    absent = workspace.path("instances/absent.yaml")
    result, code = _run(workspace, "catalog", [absent])
    assert code == 1
    assert [d.code for d in result.diagnostics] == [core.E_NO_SUCH_FILE]
    assert result.diagnostics[0].message == "no such file"
    # The diagnostic names the file workspace-relative, per TOOLS-DATAMODEL.md.
    assert result.diagnostics[0].file == "instances/absent.yaml"


def test_exit_2_on_unparseable_yaml(workspace):
    path = workspace.write("instances/malformed.yaml", "not: [yaml\n")
    result, code = _run(workspace, "catalog", [path])
    assert code == 2
    assert [d.code for d in result.diagnostics] == [core.E_PARSE]


def test_exit_2_on_unparseable_json(workspace):
    path = workspace.write("instances/malformed.json", "{ not json\n")
    result, code = _run(workspace, "story", [path])
    assert code == 2
    assert [d.code for d in result.diagnostics] == [core.E_PARSE]


def test_exit_2_on_an_unsupported_extension(workspace):
    path = workspace.write("instances/notes.txt", "hello\n")
    result, code = _run(workspace, "catalog", [path])
    assert code == 2
    assert [d.code for d in result.diagnostics] == [core.E_UNSUPPORTED_INPUT]


def test_exit_2_wins_over_exit_1_in_a_mixed_batch(workspace):
    good = workspace.add_instance("catalog")
    absent = workspace.path("instances/absent.yaml")
    unsupported = workspace.write("instances/notes.txt", "hello\n")
    result, code = _run(workspace, "catalog", [good, absent, unsupported])
    assert code == 2
    codes = [d.code for d in result.diagnostics]
    assert core.E_NO_SUCH_FILE in codes
    assert core.E_UNSUPPORTED_INPUT in codes


# ---------------------------------------------------------------------------
# A missing file fails alone
# ---------------------------------------------------------------------------


def test_missing_file_fails_alone_and_does_not_abort_the_batch(workspace):
    first = workspace.add_instance("catalog")
    absent = workspace.path("instances/absent.yaml")
    last = workspace.write("instances/last.yaml", VALID_INSTANCES["catalog"][1])
    result, code = _run(workspace, "catalog", [first, absent, last])

    assert code == 1
    # Both real files were still validated -- the batch continued past the gap.
    assert result.data["lines"] == [
        "OK    %s (catalog)" % first,
        "OK    %s (catalog)" % last,
    ]
    assert [entry["path"] for entry in result.data["files"]] == [
        str(first),
        str(absent),
        str(last),
    ]
    assert [entry["ok"] for entry in result.data["files"]] == [True, False, True]
    assert [d.code for d in result.diagnostics] == [core.E_NO_SUCH_FILE]


def test_several_missing_files_each_fail_alone(workspace):
    good = workspace.add_instance("catalog")
    first = workspace.path("instances/one.yaml")
    second = workspace.path("instances/two.yaml")
    result, code = _run(workspace, "catalog", [first, good, second])
    assert code == 1
    assert result.data["lines"] == ["OK    %s (catalog)" % good]
    assert [d.code for d in result.diagnostics] == [
        core.E_NO_SUCH_FILE,
        core.E_NO_SUCH_FILE,
    ]


# ---------------------------------------------------------------------------
# The Result envelope
# ---------------------------------------------------------------------------


def test_result_matches_the_documented_shape(workspace):
    path = workspace.add_instance("catalog")
    result, _ = _run(workspace, "catalog", [path])
    payload = result.to_dict()
    assert list(payload) == ["ok", "command", "data", "diagnostics"]
    assert payload["command"] == "validate"
    assert payload["ok"] is True
    assert payload["diagnostics"] == []


def test_ok_is_false_iff_a_diagnostic_has_severity_error():
    clean = core.Result(command="validate")
    assert clean.ok is True

    warned = core.Result(command="validate", diagnostics=[core.warning("E_X", "careful")])
    assert warned.ok is True
    assert core.exit_code(warned) == 0

    failed = core.Result(command="validate", diagnostics=[core.error(core.E_NOT_FOUND, "gone")])
    assert failed.ok is False
    assert core.exit_code(failed) == 1


def test_diagnostic_matches_the_documented_shape():
    bare = core.error("E_X", "boom")
    assert bare.to_dict() == {"severity": "error", "code": "E_X", "message": "boom"}

    located = core.error("E_X", "boom", file="context/x.yaml", line=7)
    assert located.to_dict() == {
        "severity": "error",
        "code": "E_X",
        "message": "boom",
        "file": "context/x.yaml",
        "line": 7,
    }


def test_render_puts_data_on_stdout_and_diagnostics_on_stderr(workspace):
    good = workspace.add_instance("catalog")
    absent = workspace.path("instances/absent.yaml")
    result, _ = _run(workspace, "catalog", [good, absent])
    out, err = result.render()
    assert out == "OK    %s (catalog)\n" % good
    assert err == "error: instances/absent.yaml: no such file\n"


def test_json_envelope_round_trips(workspace):
    import json

    path = workspace.add_instance("catalog")
    result, _ = _run(workspace, "catalog", [path])
    payload = json.loads(result.to_json())
    assert payload["ok"] is True
    assert payload["data"]["kind"] == "catalog"
    assert payload["data"]["files"] == [{"path": str(path), "ok": True, "errors": []}]


# ---------------------------------------------------------------------------
# The injected clock
# ---------------------------------------------------------------------------


def test_now_is_parsed_into_an_instant():
    assert core.parse_instant(NOW) == NOW
    assert core.parse_instant("2026-01-15T09:30:00Z") == "2026-01-15"
    assert core.resolve_instant(NOW) == NOW
    with pytest.raises(core.ToolError) as excinfo:
        core.parse_instant("yesterday")
    assert excinfo.value.diagnostic.code == core.E_USAGE
    with pytest.raises(core.ToolError):
        core.parse_instant("2026-13-01")


def test_system_clock_is_read_in_exactly_one_place(monkeypatch):
    monkeypatch.setattr(core, "system_instant", lambda: "1999-12-31")
    assert core.resolve_instant(None) == "1999-12-31"
    assert core.resolve_instant(NOW) == NOW


# ---------------------------------------------------------------------------
# Core I/O the group stands on
# ---------------------------------------------------------------------------


def test_workspace_listings_are_sorted(workspace):
    for name in ("zeta", "alpha", "mid"):
        workspace.add_repo(name)
    for name in ("project", "zeta", "alpha"):
        workspace.add_target(name)
    ws = workspace.ws
    assert ws.repos == ["alpha", "mid", "zeta"]
    assert ws.targets == ["alpha", "zeta"]  # "project" is excluded by definition
    assert ws.to_dict() == {
        "root": str(workspace.root),
        "repos": ["alpha", "mid", "zeta"],
        "targets": ["alpha", "zeta"],
    }


def test_workspace_listings_exclude_dot_directories(workspace):
    """``repos/`` or ``context/`` may itself be a git-tracked directory; its own
    ``.git`` must never be mistaken for a repo or a target."""
    workspace.add_repo("alpha")
    workspace.add_repo(".git")
    workspace.add_target("alpha")
    workspace.add_target(".git")
    ws = workspace.ws
    assert ws.repos == ["alpha"]
    assert ws.targets == ["alpha"]


def test_dump_yaml_uses_schema_key_order_not_insertion_or_alphabetical():
    schema = core.load_schema("project-state")
    scrambled = {
        "plans": {"001-demo": {"plan_dir": "context/project/plans/001-demo/", "status": "pending"}},
        "updated": "2026-01-15",
        "version": 1,
    }
    text = core.dump_yaml(scrambled, schema)
    keys = [line.split(":")[0] for line in text.splitlines() if line and not line.startswith(" ")]
    assert keys == ["version", "updated", "plans"]
    assert "    status: pending" in text
    assert text.index("status: pending") < text.index("plan_dir:")
    # Re-emitting an unchanged document is byte-identical.
    assert core.dump_yaml(scrambled, schema) == text


def test_dump_yaml_does_not_wrap_long_lines():
    schema = core.load_schema("project-state")
    long_value = "x" * 400
    text = core.dump_yaml({"version": 1, "updated": long_value, "plans": {}}, schema)
    assert long_value in text


def test_front_matter_round_trips(workspace):
    path = workspace.write(
        "changes/CHANGE-001-demo.md",
        "<!-- change: 001 -->\n<!-- status: pending -->\n\n# CHANGE-001\n\nbody\n",
    )
    assert core.load_front_matter(path) == {"change": "001", "status": "pending"}

    core.write_front_matter(path, {"change": "001", "status": "applied"})
    assert core.load_front_matter(path) == {"change": "001", "status": "applied"}
    assert "# CHANGE-001" in path.read_text(encoding="utf-8")
    assert "pending" not in path.read_text(encoding="utf-8")


def test_load_yaml_reports_a_parse_error_rather_than_raising_yaml_error(workspace):
    path = workspace.write("instances/malformed.yaml", "a: [1\n")
    with pytest.raises(core.ToolError) as excinfo:
        core.load_yaml(path)
    assert excinfo.value.diagnostic.code == core.E_PARSE


def test_missing_file_raises_file_missing(workspace):
    with pytest.raises(core.FileMissing) as excinfo:
        core.load_yaml(workspace.path("instances/absent.yaml"))
    assert excinfo.value.diagnostic.code == core.E_NO_SUCH_FILE


def test_validate_instance_is_the_documented_single_file_front_end(workspace):
    path = workspace.add_instance("catalog")
    result = core.validate_instance("catalog", str(path), workspace.ws)
    assert result.ok is True
    assert result.data["lines"] == ["OK    %s (catalog)" % path]
