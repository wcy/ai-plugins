"""Path safety and the ``Ident`` guard.

Two invariants, both from TOOLS-IMPLEMENTATION.md §"Path safety":

* a path escaping ``--workspace`` -- by ``..`` traversal, by an absolute
  component, or through a symlink -- is refused with ``E_PATH_ESCAPE`` and
  exit 2;
* every identifier argument failing ``Ident`` is refused with ``E_BAD_IDENT``
  and **the refused value is never rewritten into a usable one**.
"""

import pytest

from conftest import VALID_INSTANCES
from tools import core, validate


def _run(workspace, kind, files):
    args = workspace.args(kind=kind, files=[str(item) for item in files])
    result = validate.run(args, workspace.ws)
    return result, core.exit_code(result)


# ---------------------------------------------------------------------------
# E_PATH_ESCAPE via `..` traversal
# ---------------------------------------------------------------------------


def test_dotdot_traversal_is_refused_by_safe_path(workspace, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside")
    (outside / "secret.yaml").write_text("version: 1\n", encoding="utf-8")
    ws = workspace.ws
    with pytest.raises(core.ToolError) as excinfo:
        ws.safe_path("..", outside.name, "secret.yaml")
    assert excinfo.value.diagnostic.code == core.E_PATH_ESCAPE


def test_dotdot_traversal_through_the_command_exits_2(workspace):
    outside = workspace.root.parent / "escaped.yaml"
    outside.write_text(VALID_INSTANCES["catalog"][1], encoding="utf-8")
    result, code = _run(workspace, "catalog", ["../escaped.yaml"])
    assert code == 2
    assert [d.code for d in result.diagnostics] == [core.E_PATH_ESCAPE]
    # The escape aborted the batch: nothing outside the root was ever read.
    assert result.data["files"] == []
    assert result.data["lines"] == []


def test_absolute_path_outside_the_root_is_refused(workspace):
    outside = workspace.root.parent / "absolute.yaml"
    outside.write_text(VALID_INSTANCES["catalog"][1], encoding="utf-8")
    result, code = _run(workspace, "catalog", [outside])
    assert code == 2
    assert [d.code for d in result.diagnostics] == [core.E_PATH_ESCAPE]


def test_a_path_inside_the_root_is_accepted(workspace):
    inside = workspace.add_instance("catalog")
    result, code = _run(workspace, "catalog", [inside])
    assert code == 0
    # The relative form of the very same file is accepted too.
    result, code = _run(workspace, "catalog", ["instances/catalog.yaml"])
    assert code == 0


# ---------------------------------------------------------------------------
# E_PATH_ESCAPE via symlink
# ---------------------------------------------------------------------------


def test_symlink_out_of_the_root_is_refused_by_safe_path(workspace, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside-link")
    (outside / "secret.yaml").write_text("version: 1\n", encoding="utf-8")
    (workspace.root / "escape").symlink_to(outside, target_is_directory=True)

    ws = workspace.ws
    with pytest.raises(core.ToolError) as excinfo:
        ws.safe_path("escape", "secret.yaml")
    assert excinfo.value.diagnostic.code == core.E_PATH_ESCAPE


def test_symlink_out_of_the_root_through_the_command_exits_2(workspace, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside-link-cmd")
    (outside / "catalog.yaml").write_text(VALID_INSTANCES["catalog"][1], encoding="utf-8")
    (workspace.root / "escape").symlink_to(outside, target_is_directory=True)

    result, code = _run(workspace, "catalog", ["escape/catalog.yaml"])
    assert code == 2
    assert [d.code for d in result.diagnostics] == [core.E_PATH_ESCAPE]
    assert result.data["lines"] == []


def test_symlink_to_a_file_out_of_the_root_is_refused(workspace, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside-file")
    target = outside / "catalog.yaml"
    target.write_text(VALID_INSTANCES["catalog"][1], encoding="utf-8")
    (workspace.root / "linked.yaml").symlink_to(target)

    result, code = _run(workspace, "catalog", ["linked.yaml"])
    assert code == 2
    assert [d.code for d in result.diagnostics] == [core.E_PATH_ESCAPE]


def test_symlink_that_stays_inside_the_root_is_accepted(workspace):
    real = workspace.add_instance("catalog")
    (workspace.root / "linked.yaml").symlink_to(real)
    result, code = _run(workspace, "catalog", ["linked.yaml"])
    assert code == 0


# ---------------------------------------------------------------------------
# E_BAD_IDENT -- rejected, never sanitized
# ---------------------------------------------------------------------------

BAD_IDENTS = [
    "../catalog",
    "cat/alog",
    "cat alog",
    "catalog;rm -rf /",
    "",
    "..",  # matches the character class but must still never escape
    "cat\nalog",
    "catalog\n",  # a trailing newline must not slip past the anchor
    "caté",
    "cat*",
    "$catalog",
]

GOOD_IDENTS = [
    "catalog",
    "catalog.schema.json",
    "change-frontmatter",
    "01-01-ai-plugins-TOOLS-CORE",
    "003-deterministic-mechanical-steps",
    "_",
]


@pytest.mark.parametrize("value", [v for v in BAD_IDENTS if v != ".."])
def test_is_ident_rejects_bad_values(value):
    assert core.is_ident(value) is False
    diagnostic = core.require_ident(value, "schema kind")
    assert diagnostic is not None
    assert diagnostic.code == core.E_BAD_IDENT
    assert diagnostic.severity == "error"
    # The refused value is quoted back verbatim, not repaired.
    assert repr(value) in diagnostic.message


@pytest.mark.parametrize("value", GOOD_IDENTS)
def test_is_ident_accepts_good_values(value):
    assert core.is_ident(value) is True
    assert core.require_ident(value) is None
    assert core.check_ident(value) == value  # returned unchanged


def test_require_ident_returns_a_diagnostic_not_a_repaired_value():
    outcome = core.require_ident("cat/alog", "schema kind")
    assert isinstance(outcome, core.Diagnostic)
    assert not isinstance(outcome, str)


def test_no_sanitising_helper_exists():
    """There is deliberately no API that turns a rejected value into a good one."""
    for name in ("sanitize_ident", "sanitise_ident", "slugify", "coerce_ident", "fix_ident"):
        assert not hasattr(core, name), "core must not offer %r" % name


@pytest.mark.parametrize("value", [v for v in BAD_IDENTS if v != ".."])
def test_bad_kind_argument_is_refused_with_exit_2(workspace, value):
    path = workspace.add_instance("catalog")
    result, code = _run(workspace, value, [path])
    assert code == 2
    assert [d.code for d in result.diagnostics] == [core.E_BAD_IDENT]
    # Nothing was validated under a repaired kind.
    assert result.data["files"] == []
    assert result.data["lines"] == []
    assert result.data["kind"] == value  # echoed back exactly as supplied


def test_a_bad_kind_is_not_silently_repaired_into_a_working_one(workspace):
    """`cat/alog` must not become `catalog`, and `../catalog` must not become `catalog`."""
    path = workspace.add_instance("catalog")
    for bad in ("cat/alog", "../catalog", "catalog/../catalog"):
        result, code = _run(workspace, bad, [path])
        assert code == 2
        assert [d.code for d in result.diagnostics] == [core.E_BAD_IDENT]
        assert result.data["lines"] == []
    # The genuinely correct spelling still works, proving the refusal was the
    # tool's choice and not an inability to resolve the schema at all.
    result, code = _run(workspace, "catalog", [path])
    assert code == 0


def test_a_dot_dot_kind_cannot_reach_outside_the_schema_directory():
    """`..` satisfies the Ident charset, so containment must catch it."""
    assert core.is_ident("..") is True
    with pytest.raises(core.ToolError) as excinfo:
        core.resolve_kind("..")
    assert excinfo.value.diagnostic.code == core.E_UNKNOWN_KIND


def test_resolve_kind_never_leaves_the_schema_directory():
    for kind in core.CANONICAL_KINDS:
        resolved = core.resolve_kind(kind)
        assert resolved.parent == core.SCHEMA_DIR


def test_ident_pattern_is_the_documented_one():
    assert core.IDENT_PATTERN == r"^[A-Za-z0-9._-]+$"


# ---------------------------------------------------------------------------
# Exit-code mapping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "code",
    [core.E_BAD_IDENT, core.E_PATH_ESCAPE, core.E_MISSING_PREREQ, core.E_USAGE],
)
def test_refusal_codes_map_to_exit_2(code):
    result = core.Result(command="validate", diagnostics=[core.error(code, "nope")])
    assert core.exit_code(result) == 2


def test_error_diagnostics_map_to_exit_1_and_clean_results_to_0():
    assert core.exit_code(core.Result(command="validate")) == 0
    reported = core.Result(
        command="validate", diagnostics=[core.error(core.E_SCHEMA_INVALID, "bad")]
    )
    assert core.exit_code(reported) == 1
