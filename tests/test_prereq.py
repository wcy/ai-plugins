"""Runtime prerequisites.

`PyYAML` and `jsonschema` are required, never installed by the plugin. With
either absent, every command exits 2 with ``E_MISSING_PREREQ`` naming the
package and the install command -- never an ``ImportError`` traceback.

The absent import is simulated by binding ``None`` into ``sys.modules``, which
is exactly what Python does to a package it refuses to load: a subsequent
``import`` raises ``ImportError``. Nothing here uninstalls anything.
"""

import sys

import pytest

from conftest import NOW, VALID_INSTANCES
from tools import core, mc, validate

ABSENT = [("yaml", "PyYAML"), ("jsonschema", "jsonschema")]


@pytest.fixture(params=ABSENT, ids=[module for module, _ in ABSENT])
def absent_package(request, monkeypatch):
    """Simulate one prerequisite being unimportable for the duration of a test."""
    module_name, package = request.param
    monkeypatch.setitem(sys.modules, module_name, None)
    return module_name, package


def test_simulation_actually_makes_the_import_fail(absent_package):
    module_name, _package = absent_package
    with pytest.raises(ImportError):
        __import__(module_name)


# ---------------------------------------------------------------------------
# The guard itself
# ---------------------------------------------------------------------------


def test_require_prereq_raises_a_named_diagnostic(absent_package):
    module_name, package = absent_package
    with pytest.raises(core.ToolError) as excinfo:
        core.require_prereq(module_name)
    diagnostic = excinfo.value.diagnostic
    assert diagnostic.code == core.E_MISSING_PREREQ
    assert package in diagnostic.message
    assert core.INSTALL_COMMAND in diagnostic.message
    assert core.INSTALL_COMMAND == "pip install pyyaml jsonschema"


def test_require_prereqs_checks_every_package(absent_package):
    _module_name, package = absent_package
    with pytest.raises(core.ToolError) as excinfo:
        core.require_prereqs()
    assert excinfo.value.diagnostic.code == core.E_MISSING_PREREQ
    assert package in excinfo.value.diagnostic.message


def test_both_prerequisites_are_declared():
    assert core.PREREQUISITES == (("yaml", "PyYAML"), ("jsonschema", "jsonschema"))


# ---------------------------------------------------------------------------
# Every command exits 2, with no traceback
# ---------------------------------------------------------------------------


def _assert_missing_prereq(code, out, err, package):
    assert code == 2
    assert out == ""
    assert "E_MISSING_PREREQ" not in out
    assert package in err
    assert "pip install pyyaml jsonschema" in err
    assert "Traceback" not in err
    assert "ImportError" not in err
    assert "ModuleNotFoundError" not in err


def test_the_cli_exits_2_with_no_traceback(workspace, absent_package, capsys):
    _module_name, package = absent_package
    path = workspace.add_instance("catalog")
    code = mc.main(["mc.py", "--workspace", str(workspace.root), "--now", NOW,
                    "validate", "catalog", str(path)])
    captured = capsys.readouterr()
    _assert_missing_prereq(code, captured.out, captured.err, package)


@pytest.mark.parametrize("group", list(mc.GROUPS))
def test_every_group_hits_the_guard_before_dispatch(workspace, absent_package, capsys, group):
    """The guard sits on the dispatch path, so it fires for all nine groups --
    including the eight whose modules do not exist yet."""
    _module_name, package = absent_package
    code = mc.main(["mc.py", "--workspace", str(workspace.root), "--now", NOW, group])
    captured = capsys.readouterr()
    _assert_missing_prereq(code, captured.out, captured.err, package)


def test_the_json_envelope_also_reports_it(workspace, absent_package, capsys):
    import json

    _module_name, package = absent_package
    code = mc.main(["mc.py", "--workspace", str(workspace.root), "--now", NOW, "--json",
                    "validate", "catalog"])
    captured = capsys.readouterr()
    assert code == 2
    payload = json.loads(captured.out)
    assert payload["ok"] is False
    assert [d["code"] for d in payload["diagnostics"]] == [core.E_MISSING_PREREQ]
    assert package in payload["diagnostics"][0]["message"]


# ---------------------------------------------------------------------------
# The group API keeps working when the packages are present
# ---------------------------------------------------------------------------


def test_yaml_absent_is_reported_when_a_group_is_called_directly(workspace, monkeypatch):
    """A group imported and called without argv still refuses, never tracebacks."""
    path = workspace.add_instance("catalog")
    monkeypatch.setitem(sys.modules, "yaml", None)
    args = workspace.args(kind="catalog", files=[str(path)])
    result = validate.run(args, workspace.ws)
    assert core.exit_code(result) == 2
    assert [d.code for d in result.diagnostics] == [core.E_MISSING_PREREQ]
    assert "PyYAML" in result.diagnostics[0].message


def test_jsonschema_absent_is_reported_when_a_group_is_called_directly(workspace, monkeypatch):
    path = workspace.write("instances/story.json", VALID_INSTANCES["story-report"][1])
    monkeypatch.setitem(sys.modules, "jsonschema", None)
    args = workspace.args(kind="story", files=[str(path)])
    result = validate.run(args, workspace.ws)
    assert core.exit_code(result) == 2
    assert [d.code for d in result.diagnostics] == [core.E_MISSING_PREREQ]
    assert "jsonschema" in result.diagnostics[0].message


def test_importing_the_package_never_needs_the_prerequisites():
    """`core.py` must import with either package absent, or the guard could
    never run in the first place: both imports are behind ``require_prereq``."""
    import ast
    from pathlib import Path

    tree = ast.parse(Path(core.__file__).read_text(encoding="utf-8"))
    top_level = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            top_level.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            top_level.add(node.module.split(".")[0])
    assert "yaml" not in top_level
    assert "jsonschema" not in top_level
