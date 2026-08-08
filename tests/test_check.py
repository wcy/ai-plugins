"""The ``check`` and ``status`` command groups.

What TOOLS-TESTING.md asks of this suite: *each check finds its defect in a
fixture built to contain exactly one, and reports nothing on a clean fixture*.
Every test below is one of those two shapes, so a rule that stops detecting a
violation fails here rather than returning a clean report in production.

Both directions of the ``shared_interfaces`` <-> ``depends-on`` rule are
covered, the ``stranded`` handoff rule is exercised on an ``applied`` change --
the general "complete but unconsumed" rule, not its ``pending`` example -- and
``status`` is asserted for shape, for reuse of ``check``'s stage walk, and for
byte-identical output under a pinned ``--now``.

Both groups are imported and called directly, never through ``argv``.
"""

from pathlib import Path

import pytest

from conftest import NOW, PLUGIN_ROOT
from tools import change, check, core, req, spec, status, todo

TARGET = "demo"

#: The files ``_layout`` writes per module -- kept in one place so
#: ``_catalog_text`` can declare exactly what the tree holds.
_LAYOUT_FILES = {
    "COMMON": ("COMMON-OVERVIEW.md",),
    "ALPHA": ("ALPHA-OVERVIEW.md", "ALPHA-INTERFACE.md", "ALPHA-IMPLEMENTATION.md"),
    "BETA": ("BETA-OVERVIEW.md", "BETA-INTERFACE.md", "BETA-IMPLEMENTATION.md"),
}


def _facet_of(module, filename):
    facet = spec.facet_for(filename)
    if facet is None and module == spec.COMMON_MODULE:
        facet = spec.COMMON_FACET
    return facet


# ---------------------------------------------------------------------------
# Fixture builders -- every workspace is synthetic and lives in tmp_path
# ---------------------------------------------------------------------------


def _spec(module, name, target=TARGET):
    return "context/%s/spec/%s/%s" % (target, module, name)


def _document(depends, heading):
    """One spec file. ``depends is None`` means: no front matter at all."""
    front = ""
    if depends is not None:
        front = "<!-- depends-on: %s -->\n\n" % ", ".join(depends)
    return "%s# %s\n\nBody.\n" % (front, heading)


def _layout(target=TARGET):
    """A well-formed spec tree: path -> its ``depends-on`` list."""
    return {
        # COMMON-OVERVIEW.md is the documented exemption: it carries no
        # front matter and must never be reported for the absence.
        _spec("COMMON", "COMMON-OVERVIEW.md", target): None,
        _spec("ALPHA", "ALPHA-OVERVIEW.md", target): [
            _spec("COMMON", "COMMON-OVERVIEW.md", target)
        ],
        _spec("ALPHA", "ALPHA-INTERFACE.md", target): [
            _spec("ALPHA", "ALPHA-OVERVIEW.md", target)
        ],
        _spec("ALPHA", "ALPHA-IMPLEMENTATION.md", target): [
            _spec("ALPHA", "ALPHA-INTERFACE.md", target),
            _spec("BETA", "BETA-INTERFACE.md", target),
        ],
        _spec("BETA", "BETA-OVERVIEW.md", target): [
            _spec("COMMON", "COMMON-OVERVIEW.md", target)
        ],
        _spec("BETA", "BETA-INTERFACE.md", target): [
            _spec("BETA", "BETA-OVERVIEW.md", target)
        ],
        _spec("BETA", "BETA-IMPLEMENTATION.md", target): [
            _spec("BETA", "BETA-INTERFACE.md", target)
        ],
    }


def _catalog_text(
    target=TARGET,
    modules=(("ALPHA", ("REQ-001",)), ("BETA", ("REQ-002",))),
    shared=None,
    depths=None,
):
    """A catalog declaring every file ``_layout`` writes for the given modules.

    ``COMMON`` is always included -- in both the ``layers:`` block and
    ``modules:`` -- with no ``requirements`` key, so the catalog check has a
    complete tree to compare against and ``check requirements`` is unaffected.
    """
    all_modules = list(modules)
    if not any(name == "COMMON" for name, _requirements in all_modules):
        all_modules = [("COMMON", ())] + all_modules

    lines = [
        "version: 1",
        "repo: %s" % target,
    ]
    if shared is not None:
        lines.append("shared_interfaces: [%s]" % ", ".join(shared))
    names = [name for name, _requirements in all_modules]
    lines.extend(["layers:", "  L1-core:", "    modules: [%s]" % ", ".join(names), "modules:"])
    for name, requirements in all_modules:
        lines.append("  %s:" % name)
        lines.append("    layer: L1-core")
        if requirements:
            lines.append("    requirements: [%s]" % ", ".join(requirements))
        if (depths or {}).get(name) is not None:
            lines.append("    depth: %s" % depths[name])
        lines.append("    files:")
        for filename in _LAYOUT_FILES.get(name, ("%s-OVERVIEW.md" % name,)):
            lines.append("      - path: %s" % _spec(name, filename, target))
            lines.append("        facet: %s" % _facet_of(name, filename))
    return "\n".join(lines) + "\n"


def _requirements_text(entries=("REQ-001", "REQ-002"), target=TARGET):
    out = ["<!-- requirements: %s -->" % target, "<!-- updated: 2026-01-01 -->", "", "# Requirements", ""]
    for identifier in entries:
        out.extend(
            [
                "### %s: Something" % identifier,
                "",
                "**Need:** a need",
                "**Rationale:** a rationale",
                "**Status:** active",
                "",
            ]
        )
    return "\n".join(out) + "\n"


def _tree(workspace, layout=None, catalog=None, requirements=None, target=TARGET):
    """Write a spec tree, its catalog, and its requirements tier."""
    layout = _layout(target) if layout is None else layout
    for path, depends in sorted(layout.items()):
        workspace.write(path, _document(depends, Path(path).stem))
    workspace.write(
        "context/%s/spec/CATALOG.yaml" % target,
        _catalog_text(target) if catalog is None else catalog,
    )
    workspace.write(
        "context/%s/requirements/REQUIREMENTS.md" % target,
        _requirements_text(target=target) if requirements is None else requirements,
    )
    return layout


def _change_text(number, slug, statusname, repo=TARGET, plan=None):
    plan_line = "<!-- plan: %s -->\n" % plan if plan else ""
    return (
        "<!-- change: %s -->\n"
        "<!-- scope: repo -->\n"
        "<!-- repo: %s -->\n"
        "<!-- status: %s -->\n"
        "<!-- date: 2026-01-01 -->\n"
        "%s"
        "\n# CHANGE-%s: %s\n\n## Summary\n\nA change.\n"
        % (number, repo, statusname, plan_line, number, slug)
    )


def _index_text(number, slug, statusname, change_files=(), repo=TARGET, plan=None):
    rows = "".join("| `%s` | `%s` | a change |\n" % (repo, path) for path in change_files)
    plan_line = "<!-- plan: %s -->\n" % plan if plan else ""
    return (
        "<!-- project-change: %s -->\n"
        "<!-- scope: repo -->\n"
        "<!-- repos: %s -->\n"
        "<!-- status: %s -->\n"
        "<!-- date: 2026-01-01 -->\n"
        "%s"
        "\n# PROJECT-CHANGE-%s: %s\n"
        "\n## Summary\n\nAn index.\n"
        "\n## Repo Change Files\n\n"
        "| Repo | Change File | Summary |\n"
        "|------|-------------|---------|\n"
        "%s" % (number, repo, statusname, plan_line, number, slug, rows)
    )


def _graph_text(plan_id, stories=("01-01-demo-ALPHA",), project_change=None):
    lines = [
        "version: 2",
        "plan_id: %s" % plan_id,
        "type: incremental" if project_change else "type: full",
    ]
    if project_change:
        lines.append('project_change: "%s"' % project_change)
    lines.extend(["repos:", "  - %s" % TARGET, "waves:", "  - wave: 1", "    stories:"])
    lines.extend("      - %s" % story for story in stories)
    lines.append("stories:")
    for story in stories:
        lines.extend(
            [
                "  %s:" % story,
                "    file: PLAN-%s.md" % story,
                "    repo: %s" % TARGET,
                "    module: ALPHA",
                "    wave: 1",
                "    prerequisites: []",
            ]
        )
    return "\n".join(lines) + "\n"


def _plan_state_text(plan_id, run=1, statusname="applied", stories=(("01-01-demo-ALPHA", "applied"),), conformance=None):
    lines = [
        "version: 2",
        "plan_id: %s" % plan_id,
        "run: %d" % run,
        "status: %s" % statusname,
        "stories:",
    ]
    for story, story_status in stories:
        lines.extend(
            [
                "  %s:" % story,
                "    repo: %s" % TARGET,
                "    wave: 1",
                "    status: %s" % story_status,
                "    retries: 0",
            ]
        )
    if conformance is not None:
        # ``(status, findings)`` or ``(status, findings, deferred)`` -- the
        # third element is omitted from the document when it is not supplied,
        # because an absent ``deferred`` means 0 and that is a distinct fixture
        # from one recording a deferral of zero.
        conformance_status, findings = conformance[0], conformance[1]
        lines.extend(
            [
                "conformance:",
                "  status: %s" % conformance_status,
                "  findings: %d" % findings,
            ]
        )
        if len(conformance) > 2:
            lines.append("  deferred: %d" % conformance[2])
    return "\n".join(lines) + "\n"


def _ledger_text(plans=(("001-demo", "applied", "001"),)):
    lines = ["version: 1", "plans:"]
    for plan_id, plan_status, project_change in plans:
        lines.extend(
            [
                "  %s:" % plan_id,
                "    status: %s" % plan_status,
                '    project_change: "%s"' % project_change,
                "    plan_dir: context/project/plans/%s" % plan_id,
            ]
        )
    return "\n".join(lines) + "\n"


def _chain(workspace):
    """A workspace whose stage chain is complete end to end.

    mreq -> mspec -> mplan -> mexecute -> mverify all hand off cleanly, so any
    finding a test sees comes from the one defect that test introduced.
    """
    _tree(workspace)
    change_path = "context/%s/changes/CHANGE-001-demo.md" % TARGET
    workspace.write(change_path, _change_text("001", "demo", "applied"))
    workspace.write(
        "context/project/changes/PROJECT-CHANGE-001-demo.md",
        _index_text("001", "demo", "applied", [change_path]),
    )
    workspace.write("context/project/plans/001-demo/plan.yaml", _graph_text("001-demo", project_change="001"))
    workspace.write(
        "context/project/plans/001-demo/state.yaml",
        _plan_state_text("001-demo", conformance=("clean", 0)),
    )
    workspace.write("context/project/state.yaml", _ledger_text())
    return workspace


# ---------------------------------------------------------------------------
# Calling the groups -- directly, never through argv
# ---------------------------------------------------------------------------


def _check(workspace, verb, target=None):
    """Call ``check`` and return ``(result, exit_code)``."""
    result = check.run(workspace.args(verb=verb, target=target), workspace.ws)
    return result, core.exit_code(result)


def _findings(workspace, verb, target=None):
    result, _code = _check(workspace, verb, target)
    return result.data["findings"] if verb != "all" else result.data["reports"]


def _codes(findings):
    return [item["code"] for item in findings]


def _status(workspace):
    result = status.run(workspace.args(), workspace.ws)
    return result, core.exit_code(result)


# ---------------------------------------------------------------------------
# check depends-on
# ---------------------------------------------------------------------------


def test_depends_on_reports_nothing_on_a_clean_tree(workspace):
    _tree(workspace)
    result, code = _check(workspace, "depends-on", TARGET)
    assert result.data["findings"] == []
    assert result.diagnostics == []
    assert result.ok is True
    assert code == 0


def test_depends_on_reports_the_one_dangling_path(workspace):
    layout = _layout()
    layout[_spec("ALPHA", "ALPHA-OVERVIEW.md")] = [_spec("COMMON", "COMMON-GONE.md")]
    _tree(workspace, layout)

    result, code = _check(workspace, "depends-on", TARGET)
    findings = result.data["findings"]
    assert _codes(findings) == [core.E_DANGLING_DEPENDS_ON]
    assert findings[0]["file"] == _spec("ALPHA", "ALPHA-OVERVIEW.md")
    assert findings[0]["line"] == 1
    assert "COMMON-GONE.md" in findings[0]["message"]
    assert code == 1


def test_depends_on_exempts_only_the_root_file(workspace):
    """``COMMON-OVERVIEW.md`` may carry no front matter; nothing else may."""
    layout = _layout()
    layout[_spec("BETA", "BETA-OVERVIEW.md")] = None
    _tree(workspace, layout)

    findings = _findings(workspace, "depends-on", TARGET)
    assert _codes(findings) == [check.E_MISSING_DEPENDS_ON]
    assert findings[0]["file"] == _spec("BETA", "BETA-OVERVIEW.md")


def test_depends_on_report_shape(workspace):
    _tree(workspace)
    result, _code = _check(workspace, "depends-on", TARGET)
    assert result.data["check"] == "depends-on"
    assert result.data["target"] == TARGET
    assert result.command == "check.depends-on"


# ---------------------------------------------------------------------------
# check coupling
# ---------------------------------------------------------------------------


def test_coupling_reports_nothing_on_a_clean_tree(workspace):
    _tree(workspace)
    result, code = _check(workspace, "coupling", TARGET)
    assert result.data["findings"] == []
    assert result.diagnostics == []
    assert code == 0


def test_coupling_reports_implementation_to_implementation(workspace):
    layout = _layout()
    layout[_spec("ALPHA", "ALPHA-IMPLEMENTATION.md")] = [
        _spec("ALPHA", "ALPHA-INTERFACE.md"),
        _spec("BETA", "BETA-IMPLEMENTATION.md"),
    ]
    _tree(workspace, layout)

    findings = _findings(workspace, "coupling", TARGET)
    assert _codes(findings) == [check.E_COUPLING_IMPL]
    assert findings[0]["file"] == _spec("ALPHA", "ALPHA-IMPLEMENTATION.md")
    # The path still exists, so the depends-on check stays clean: the fixture
    # holds exactly one defect and it is a coupling defect.
    assert _findings(workspace, "depends-on", TARGET) == []


def test_coupling_reports_a_cross_repo_bypass(workspace):
    layout = _layout()
    other = _spec("GAMMA", "GAMMA-INTERFACE.md", "other")
    layout[_spec("ALPHA", "ALPHA-INTERFACE.md")] = [
        _spec("ALPHA", "ALPHA-OVERVIEW.md"),
        other,
    ]
    _tree(workspace, layout)
    workspace.write(other, _document([], "GAMMA-INTERFACE"))

    findings = _findings(workspace, "coupling", TARGET)
    assert _codes(findings) == [check.E_COUPLING_CROSS_REPO]
    assert "context/shared/spec/" in findings[0]["message"]


def test_coupling_allows_the_shared_contract_layer(workspace):
    """A cross-target path *through* ``context/shared/spec/``, declared in both
    places, is the one sanctioned form and is not a finding."""
    layout = _layout()
    shared = "context/shared/spec/BUS/BUS-INTERFACE.md"
    layout[_spec("ALPHA", "ALPHA-INTERFACE.md")] = [_spec("ALPHA", "ALPHA-OVERVIEW.md"), shared]
    _tree(workspace, layout, catalog=_catalog_text(shared=["BUS"]))
    workspace.write(shared, _document([], "BUS-INTERFACE"))

    assert _findings(workspace, "coupling", TARGET) == []
    assert _findings(workspace, "depends-on", TARGET) == []


def test_coupling_reports_a_declaration_with_no_dependency(workspace):
    """``shared_interfaces`` names a TAG no spec file depends on."""
    _tree(workspace, catalog=_catalog_text(shared=["BUS"]))

    findings = _findings(workspace, "coupling", TARGET)
    assert _codes(findings) == [check.E_COUPLING_SHARED_DECL]
    assert findings[0]["file"] == "context/%s/spec/CATALOG.yaml" % TARGET
    assert "shared_interfaces" in findings[0]["message"]
    assert "no spec file" in findings[0]["message"]


def test_coupling_reports_a_dependency_with_no_declaration(workspace):
    """The converse: a shared INTERFACE is depended on but never declared."""
    layout = _layout()
    shared = "context/shared/spec/BUS/BUS-INTERFACE.md"
    layout[_spec("ALPHA", "ALPHA-INTERFACE.md")] = [_spec("ALPHA", "ALPHA-OVERVIEW.md"), shared]
    _tree(workspace, layout)  # catalog carries no shared_interfaces
    workspace.write(shared, _document([], "BUS-INTERFACE"))

    findings = _findings(workspace, "coupling", TARGET)
    assert _codes(findings) == [check.E_COUPLING_SHARED_DECL]
    assert findings[0]["file"] == _spec("ALPHA", "ALPHA-INTERFACE.md")
    assert "does not declare it" in findings[0]["message"]


# ---------------------------------------------------------------------------
# check requirements
# ---------------------------------------------------------------------------


def test_requirements_reports_nothing_on_a_clean_tree(workspace):
    _tree(workspace)
    result, code = _check(workspace, "requirements", TARGET)
    assert result.data["findings"] == []
    assert code == 0


def test_requirements_reports_a_reference_to_an_absent_entry(workspace):
    _tree(workspace, catalog=_catalog_text(modules=(("ALPHA", ("REQ-001",)), ("BETA", ("REQ-002", "REQ-009")))))

    findings = _findings(workspace, "requirements", TARGET)
    assert _codes(findings) == [core.E_MISSING_REQUIREMENT]
    assert "REQ-009" in findings[0]["message"]
    assert findings[0]["file"] == "context/%s/spec/CATALOG.yaml" % TARGET


def test_requirements_reports_an_entry_no_module_references(workspace):
    _tree(workspace, requirements=_requirements_text(("REQ-001", "REQ-002", "REQ-003")))

    findings = _findings(workspace, "requirements", TARGET)
    assert _codes(findings) == [core.E_ORPHAN_REQUIREMENT]
    assert "REQ-003" in findings[0]["message"]
    assert findings[0]["file"] == "context/%s/requirements/REQUIREMENTS.md" % TARGET
    assert findings[0]["line"] > 0


def test_requirements_reports_a_stale_mnemonic_as_a_warning(workspace):
    """A reference whose mnemonic disagrees with the entry heading still
    resolves -- it names a live requirement, not a dangling one."""
    _tree(
        workspace,
        catalog=_catalog_text(
            modules=(("ALPHA", ("REQ-001-different-name",)), ("BETA", ("REQ-002",)))
        ),
        requirements=_requirements_text(("REQ-001-original-name", "REQ-002")),
    )

    findings = _findings(workspace, "requirements", TARGET)
    assert _codes(findings) == [check.W_STALE_REQ_MNEMONIC]
    assert findings[0]["severity"] == "warning"
    assert "REQ-001" in findings[0]["message"]
    # A stale mnemonic is not a dangling reference: no orphan/missing finding.
    assert core.E_MISSING_REQUIREMENT not in _codes(findings)
    assert core.E_ORPHAN_REQUIREMENT not in _codes(findings)


def test_requirements_does_not_report_a_reference_that_agrees_with_the_heading(workspace):
    _tree(
        workspace,
        catalog=_catalog_text(
            modules=(("ALPHA", ("REQ-001-same-name",)), ("BETA", ("REQ-002",)))
        ),
        requirements=_requirements_text(("REQ-001-same-name", "REQ-002")),
    )
    assert _findings(workspace, "requirements", TARGET) == []


def test_requirements_does_not_report_a_bare_reference_as_stale(workspace):
    """A bare reference to a mnemonic-bearing entry stays conforming."""
    _tree(
        workspace,
        catalog=_catalog_text(modules=(("ALPHA", ("REQ-001",)), ("BETA", ("REQ-002",)))),
        requirements=_requirements_text(("REQ-001-has-a-mnemonic", "REQ-002")),
    )
    assert _findings(workspace, "requirements", TARGET) == []


def test_requirements_reports_an_unparseable_reference(workspace):
    """An unparseable reference is an error, never a silent skip."""
    _tree(
        workspace,
        catalog=_catalog_text(modules=(("ALPHA", ("REQ-01", "REQ-001")),)),
        requirements=_requirements_text(("REQ-001",)),
    )

    findings = _findings(workspace, "requirements", TARGET)
    assert _codes(findings) == [req.E_BAD_REQ_REF]
    assert findings[0]["severity"] == "error"
    assert "REQ-01" in findings[0]["message"]
    # The one valid reference in the same module still covers its entry.
    assert core.E_ORPHAN_REQUIREMENT not in _codes(findings)


# ---------------------------------------------------------------------------
# check catalog
# ---------------------------------------------------------------------------


def _remove_catalog_entry(text, path):
    """Drop one ``- path: <path>`` entry (and its ``facet:`` line)."""
    lines = text.splitlines()
    out = []
    skip_next = False
    for line in lines:
        if skip_next:
            skip_next = False
            continue
        if line.strip() == "- path: %s" % path:
            skip_next = True
            continue
        out.append(line)
    return "\n".join(out) + "\n"


def _append_catalog_entry(text, path, facet):
    """Add one more ``files[]`` entry to the last module in the text."""
    return text.rstrip("\n") + "\n      - path: %s\n        facet: %s\n" % (path, facet)


def _set_catalog_facet(text, path, facet):
    """Rewrite the ``facet:`` line that follows one ``- path: <path>`` entry."""
    lines = text.splitlines()
    out = []
    replace_next = False
    for line in lines:
        if replace_next:
            out.append("        facet: %s" % facet)
            replace_next = False
            continue
        out.append(line)
        if line.strip() == "- path: %s" % path:
            replace_next = True
    return "\n".join(out) + "\n"


def _set_catalog_layer(text, module, layer):
    """Rewrite one module's ``layer:`` line, leaving the ``layers:`` block as is."""
    lines = text.splitlines()
    out = []
    in_module = False
    for line in lines:
        if line == "  %s:" % module:
            in_module = True
            out.append(line)
            continue
        if in_module and line.strip().startswith("layer:"):
            out.append("    layer: %s" % layer)
            in_module = False
            continue
        out.append(line)
    return "\n".join(out) + "\n"


def test_catalog_reports_nothing_on_a_clean_tree(workspace):
    _tree(workspace)
    result, code = _check(workspace, "catalog", TARGET)
    assert result.data["findings"] == []
    assert code == 0


def test_catalog_reports_a_spec_file_the_catalog_does_not_declare(workspace):
    """File-set agreement: a spec file no entry declares."""
    path = _spec("BETA", "BETA-IMPLEMENTATION.md")
    catalog = _remove_catalog_entry(_catalog_text(), path)
    _tree(workspace, catalog=catalog)

    findings = _findings(workspace, "catalog", TARGET)
    assert _codes(findings) == [check.E_CATALOG_UNLISTED_FILE]
    assert findings[0]["file"] == path


def test_catalog_reports_an_entry_whose_path_names_no_file(workspace):
    """File-set agreement: a declared path with no matching file."""
    catalog = _append_catalog_entry(_catalog_text(), _spec("BETA", "BETA-GONE.md"), "overview")
    _tree(workspace, catalog=catalog)

    findings = _findings(workspace, "catalog", TARGET)
    assert _codes(findings) == [check.E_CATALOG_ABSENT_PATH]
    assert findings[0]["file"] == "context/%s/spec/CATALOG.yaml" % TARGET
    assert "BETA-GONE.md" in findings[0]["message"]


def test_catalog_reports_a_facet_disagreeing_with_the_filename(workspace):
    path = _spec("ALPHA", "ALPHA-OVERVIEW.md")
    catalog = _set_catalog_facet(_catalog_text(), path, "interface")
    _tree(workspace, catalog=catalog)

    findings = _findings(workspace, "catalog", TARGET)
    assert _codes(findings) == [check.E_CATALOG_FACET]
    assert findings[0]["file"] == path


def test_catalog_reports_a_layer_disagreeing_with_the_layers_block(workspace):
    catalog = _set_catalog_layer(_catalog_text(), "BETA", "L2-services")
    _tree(workspace, catalog=catalog)

    findings = _findings(workspace, "catalog", TARGET)
    assert _codes(findings) == [check.E_CATALOG_LAYER]
    assert findings[0]["file"] == "context/%s/spec/CATALOG.yaml" % TARGET


# -- the exports rule -------------------------------------------------------
#
# Every case below varies exactly one thing about ALPHA-INTERFACE.md: the
# ``Exports:`` trailer its document carries and the ``exports:`` list its
# catalog entry declares. The rest of the tree stays clean, so any finding
# comes from the defect the test introduced.

_ALPHA_INTERFACE = _spec("ALPHA", "ALPHA-INTERFACE.md")


def _set_catalog_exports(text, path, tokens):
    """Give the entry declaring ``path`` an ``exports:`` list."""
    out = []
    pending = False
    for line in text.splitlines():
        out.append(line)
        if line.strip() == "- path: %s" % path:
            pending = True
            continue
        if pending and line.strip().startswith("facet:"):
            if tokens:
                out.append("        exports:")
                out.extend("        - %s" % token for token in tokens)
            else:
                # An explicit empty list -- distinct from omitting the key.
                out.append("        exports: []")
            pending = False
    return "\n".join(out) + "\n"


def _exports_tree(workspace, trailer=None, tokens=None):
    """A clean tree, plus a trailer and/or an ``exports:`` list on ALPHA."""
    layout = _layout()
    catalog = _catalog_text()
    if tokens is not None:
        catalog = _set_catalog_exports(catalog, _ALPHA_INTERFACE, tokens)
    _tree(workspace, layout, catalog=catalog)
    if trailer is not None:
        workspace.write(
            _ALPHA_INTERFACE,
            _document(layout[_ALPHA_INTERFACE], "ALPHA-INTERFACE") + "\n%s\n" % trailer,
        )
    return _ALPHA_INTERFACE


def test_catalog_reports_a_trailer_token_the_catalog_omits(workspace):
    _exports_tree(workspace, "Exports: `alpha-run`, `alpha-stop`.", ["alpha-run"])

    findings = _findings(workspace, "catalog", TARGET)
    assert _codes(findings) == [check.E_CATALOG_EXPORTS]
    assert findings[0]["file"] == _ALPHA_INTERFACE
    assert "`alpha-stop`" in findings[0]["message"]


def test_catalog_reports_a_catalog_token_the_trailer_omits(workspace):
    _exports_tree(workspace, "Exports: `alpha-run`.", ["alpha-run", "alpha-stop"])

    findings = _findings(workspace, "catalog", TARGET)
    assert _codes(findings) == [check.E_CATALOG_EXPORTS]
    assert findings[0]["file"] == _ALPHA_INTERFACE
    assert "`alpha-stop`" in findings[0]["message"]


def test_catalog_accepts_the_same_tokens_in_a_different_order(workspace):
    """The comparison is a set: order never matters."""
    _exports_tree(workspace, "Exports: `alpha-run`, `alpha-stop`.", ["alpha-stop", "alpha-run"])

    result, code = _check(workspace, "catalog", TARGET)
    assert result.data["findings"] == []
    assert code == 0


def test_catalog_reports_a_trailer_with_no_exports_in_the_catalog(workspace):
    _exports_tree(workspace, "Exports: `alpha-run`.", None)

    findings = _findings(workspace, "catalog", TARGET)
    assert _codes(findings) == [check.E_CATALOG_EXPORTS]
    assert findings[0]["file"] == _ALPHA_INTERFACE
    assert "`alpha-run`" in findings[0]["message"]


def test_catalog_reports_exports_in_the_catalog_with_no_trailer(workspace):
    _exports_tree(workspace, None, ["alpha-run"])

    findings = _findings(workspace, "catalog", TARGET)
    assert _codes(findings) == [check.E_CATALOG_EXPORTS]
    assert findings[0]["file"] == _ALPHA_INTERFACE
    assert "`alpha-run`" in findings[0]["message"]


def test_catalog_accepts_neither_a_trailer_nor_catalog_exports(workspace):
    """Absence is symmetric: a module exporting nothing omits both."""
    _exports_tree(workspace, None, None)

    result, code = _check(workspace, "catalog", TARGET)
    assert result.data["findings"] == []
    assert code == 0


def test_catalog_reports_an_empty_exports_list_as_a_placeholder(workspace):
    """`exports: []` is the catalog-side placeholder the grammar forbids.

    Distinct from omitting the key, and the message must say which it saw --
    the two were indistinguishable before, so the diagnostic misstated the file.
    """
    _exports_tree(workspace, None, [])

    findings = _findings(workspace, "catalog", TARGET)
    assert _codes(findings) == [check.E_CATALOG_EXPORTS]
    assert findings[0]["file"] == _ALPHA_INTERFACE
    assert "empty `exports:` list" in findings[0]["message"]


@pytest.mark.parametrize(
    "trailer",
    [
        # Markdown emphasis around the prefix.
        "**Exports:** `alpha-run`.",
        # A different case.
        "EXPORTS: `alpha-run`.",
        # Blockquoted.
        "> Exports: `alpha-run`.",
    ],
)
def test_catalog_treats_a_near_miss_prefix_as_absence(workspace, trailer):
    """The absent/malformed boundary is the *literal* prefix, deliberately.

    ``TOOLS-IMPLEMENTATION.md`` states this as an accepted residual gap: the
    alternative is a heuristic about what an author meant, and a literal prefix
    is the one test that stays decidable. With no catalog `exports:` either,
    such a file passes -- pinned here so the boundary cannot move silently.
    """
    _exports_tree(workspace, trailer, None)

    result, code = _check(workspace, "catalog", TARGET)
    assert result.data["findings"] == []
    assert code == 0


def test_catalog_accepts_the_documented_token_charset(workspace):
    """Begins and ends alphanumeric; `.`, `-`, `_` permitted between."""
    tokens = ["mc.py", "change-frontmatter", "alpha_beta", "s3"]
    _exports_tree(workspace, "Exports: `mc.py`, `change-frontmatter`, `alpha_beta`, `s3`.", tokens)

    result, code = _check(workspace, "catalog", TARGET)
    assert result.data["findings"] == []
    assert code == 0


@pytest.mark.parametrize(
    "trailer",
    [
        # A trailing parenthetical -- "nothing but" governs the paragraph.
        "Exports: `alpha-run` (the entry point).",
        # An unbackticked token.
        "Exports: `alpha-run`, alpha-stop.",
        # No terminal period.
        "Exports: `alpha-run`",
        # Prose before the first token.
        "Exports: the commands `alpha-run`, `alpha-stop`.",
        # A repeated token -- each appears once and matches exactly one entry,
        # so a repeat is malformed rather than a set of one.
        "Exports: `alpha-run`, `alpha-run`.",
        # A token must end alphanumeric: `a.` is not a bare identifier.
        "Exports: `alpha-run.`.",
        # ...and must begin alphanumeric.
        "Exports: `_alpha-run`.",
    ],
)
def test_catalog_reports_a_trailer_that_does_not_parse(workspace, trailer):
    """A trailer the checker cannot read is a finding, never a silent pass."""
    _exports_tree(workspace, trailer, ["alpha-run"])

    findings = _findings(workspace, "catalog", TARGET)
    assert _codes(findings) == [check.E_CATALOG_EXPORTS]
    assert findings[0]["file"] == _ALPHA_INTERFACE
    assert findings[0]["severity"] == "error"


def test_catalog_reports_nothing_when_a_wrapped_trailer_agrees(workspace):
    """A clean fixture reports nothing from any of the four rules."""
    _exports_tree(
        workspace,
        "Exports: `alpha-run`, `alpha-stop`,\n`alpha.py`.",
        ["alpha-run", "alpha-stop", "alpha.py"],
    )

    result, code = _check(workspace, "catalog", TARGET)
    assert result.data["findings"] == []
    assert code == 0


def test_catalog_reports_all_four_rules_in_their_fixed_order(workspace):
    """file-set, facet, layer, exports -- so finding order is stable."""
    catalog = _remove_catalog_entry(_catalog_text(), _spec("BETA", "BETA-IMPLEMENTATION.md"))
    catalog = _set_catalog_facet(catalog, _spec("ALPHA", "ALPHA-OVERVIEW.md"), "datamodel")
    catalog = _set_catalog_layer(catalog, "BETA", "L2-services")
    layout = _layout()
    _tree(workspace, layout, catalog=catalog)
    workspace.write(
        _ALPHA_INTERFACE,
        _document(layout[_ALPHA_INTERFACE], "ALPHA-INTERFACE") + "\nExports: `alpha-run`.\n",
    )

    findings = _findings(workspace, "catalog", TARGET)
    assert _codes(findings) == [
        check.E_CATALOG_UNLISTED_FILE,
        check.E_CATALOG_FACET,
        check.E_CATALOG_LAYER,
        check.E_CATALOG_EXPORTS,
    ]


def test_catalog_checks_exports_against_a_shared_scope_catalog(workspace):
    """Unlike the layer rule, the exports rule is not skipped for ``shared``."""
    workspace.write(
        "context/shared/spec/CATALOG.yaml",
        "version: 1\n"
        "scope: shared\n"
        "interfaces:\n"
        "  AUTH:\n"
        "    files:\n"
        "      - path: context/shared/spec/AUTH/AUTH-OVERVIEW.md\n"
        "        facet: overview\n"
        "      - path: context/shared/spec/AUTH/AUTH-INTERFACE.md\n"
        "        facet: interface\n"
        "        exports:\n"
        "        - auth-token\n",
    )
    workspace.write("context/shared/spec/AUTH/AUTH-OVERVIEW.md", _document([], "AUTH-OVERVIEW"))
    workspace.write(
        "context/shared/spec/AUTH/AUTH-INTERFACE.md",
        _document(["context/shared/spec/AUTH/AUTH-OVERVIEW.md"], "AUTH-INTERFACE")
        + "\nExports: `auth-session`.\n",
    )

    findings = _findings(workspace, "catalog", spec.SHARED_TARGET)
    assert _codes(findings) == [check.E_CATALOG_EXPORTS, check.E_CATALOG_EXPORTS]
    assert all(item["file"] == "context/shared/spec/AUTH/AUTH-INTERFACE.md" for item in findings)
    assert "`auth-session`" in findings[0]["message"]
    assert "`auth-token`" in findings[1]["message"]
    # The layer rule stays skipped -- a shared catalog has no `layers:` block.
    assert check.E_CATALOG_LAYER not in _codes(findings)


def test_catalog_exports_findings_are_errors(workspace):
    """``E_CATALOG_EXPORTS`` is ``core.error``, like the five other codes."""
    _exports_tree(workspace, "Exports: `alpha-run`.", ["alpha-stop"])

    findings = _findings(workspace, "catalog", TARGET)
    assert [item["severity"] for item in findings] == ["error", "error"]
    assert set(_codes(findings)) == {check.E_CATALOG_EXPORTS}


# ---------------------------------------------------------------------------
# check todo
#
# Same two shapes as every other check: a fixture built to contain exactly one
# defect, and a clean fixture that reports nothing. The list is written by
# ``todo add`` wherever the entry is meant to be conforming -- a fixture
# hand-writing the *good* case would be a second implementation of the emitter
# and could drift from it -- and hand-edited only to introduce the one defect
# under test, which is the state a checker exists for.
# ---------------------------------------------------------------------------

TODO_REL = todo.TODO_REL

#: A conforming ``todo add`` call, keyed by argparse destination.
_TODO_FIELDS = {
    "run": "/mfix",
    "kind": "spec-drift",
    "origin": "CHANGE-001",
    "priority": "medium",
    "risk_if_unfixed": "low",
    "regression_risk": "low",
    "cost": "low",
    "context": "`check handoff` in plugins/metacoder/tools/check.py aggregates across repos.",
}


def _todo_origin(workspace):
    """A change document a conforming ``Origin`` resolves to."""
    workspace.write(
        "context/%s/changes/CHANGE-001-demo.md" % TARGET,
        _change_text("001", "demo", "pending"),
    )


def _todo_add(workspace, title="a deferral", **overrides):
    """Write one entry through the emitter, so the fixture is what it produces."""
    fields = dict(_TODO_FIELDS)
    fields.update(overrides)
    result = todo.run(workspace.args(verb="add", title=title, **fields), workspace.ws)
    assert core.exit_code(result) == 0, [item.render() for item in result.diagnostics]
    return result


def _todo_edit(workspace, old, new):
    """The one hand edit a test introduces, applied to the emitted list."""
    path = workspace.path(TODO_REL)
    text = path.read_text(encoding="utf-8")
    assert old in text, old
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    return path


def test_todo_reports_nothing_on_a_workspace_with_no_list(workspace):
    """Nothing deferred is not a defect, and must not read as one."""
    result, code = _check(workspace, "todo")
    assert result.data["findings"] == []
    assert result.data["target"] is None
    assert code == 0


def test_todo_reports_nothing_on_a_conforming_list(workspace):
    _todo_origin(workspace)
    _todo_add(workspace)
    result, code = _check(workspace, "todo")
    assert result.data["findings"] == []
    assert code == 0


def test_todo_reports_a_missing_required_field(workspace):
    _todo_origin(workspace)
    _todo_add(workspace)
    _todo_edit(workspace, "**Cost:** low\n", "")

    findings = _findings(workspace, "todo")
    assert _codes(findings) == [todo.E_TODO_FIELD]
    assert "Cost" in findings[0]["message"]
    assert findings[0]["file"] == TODO_REL


@pytest.mark.parametrize(
    "old,new",
    [
        ("**Run:** /mfix", "**Run:** /mnope"),
        ("**Kind:** spec-drift", "**Kind:** typo"),
        ("**Priority:** medium", "**Priority:** urgent"),
        ("**Risk-if-unfixed:** low", "**Risk-if-unfixed:** none"),
        ("**Regression-risk:** low", "**Regression-risk:** unknown"),
        ("**Cost:** low", "**Cost:** cheap"),
    ],
)
def test_todo_reports_a_value_outside_its_enum(workspace, old, new):
    _todo_origin(workspace)
    _todo_add(workspace)
    _todo_edit(workspace, old, new)

    findings = _findings(workspace, "todo")
    assert _codes(findings) == [todo.E_TODO_ENUM]


def test_todo_reports_an_origin_that_resolves_to_nothing(workspace):
    _todo_origin(workspace)
    _todo_add(workspace)
    _todo_edit(workspace, "**Origin:** CHANGE-001", "**Origin:** CHANGE-404")

    findings = _findings(workspace, "todo")
    assert _codes(findings) == [todo.E_TODO_ORIGIN]
    assert "CHANGE-404" in findings[0]["message"]


@pytest.mark.parametrize(
    "context",
    ["needs more thought", "fix later", "this one is important and someone should do it"],
)
def test_todo_reports_a_context_too_thin_to_start_from(workspace, context):
    _todo_origin(workspace)
    _todo_add(workspace)
    _todo_edit(workspace, _TODO_FIELDS["context"], context)

    findings = _findings(workspace, "todo")
    assert _codes(findings) == [check.E_TODO_CONTEXT]


@pytest.mark.parametrize(
    "context",
    [
        "plugins/metacoder/tools/check.py aggregates across repos",
        "the rule lives in `context_names_something` and has no test",
        "CHANGE-029 left this open",
        "TOOLS publishes it but the catalog does not",
        "020-deferred-work-todo shipped without it",
    ],
)
def test_a_context_naming_a_file_artifact_or_identifier_is_accepted(workspace, context):
    """The rule is deliberately weak: it catches the empty gesture only."""
    _todo_origin(workspace)
    _todo_add(workspace)
    _todo_edit(workspace, _TODO_FIELDS["context"], context)

    assert _findings(workspace, "todo") == []


def test_a_sentence_abbreviation_is_not_mistaken_for_a_filename(workspace):
    """``e.g.``/``i.e.``/``etc.`` must not satisfy the rule on their own."""
    _todo_origin(workspace)
    _todo_add(workspace)
    _todo_edit(workspace, _TODO_FIELDS["context"], "it is broken, e.g. sometimes, etc.")

    assert _codes(_findings(workspace, "todo")) == [check.E_TODO_CONTEXT]


def test_todo_reports_every_violation_the_emitter_would_have_refused(workspace):
    """The checker's codes are the emitter's codes, not a parallel vocabulary."""
    assert (check.E_TODO_FIELD, check.E_TODO_ENUM, check.E_TODO_ORIGIN) == (
        todo.E_TODO_FIELD,
        todo.E_TODO_ENUM,
        todo.E_TODO_ORIGIN,
    )

    _todo_origin(workspace)
    _todo_add(workspace)
    _todo_edit(workspace, "**Run:** /mfix", "**Run:** /mnope")
    _todo_edit(workspace, "**Origin:** CHANGE-001", "**Origin:** CHANGE-404")
    _todo_edit(workspace, "**Cost:** low\n", "")

    findings = _findings(workspace, "todo")
    # Field order is the standard's, so the report is stable between runs.
    assert _codes(findings) == [todo.E_TODO_ENUM, todo.E_TODO_ORIGIN, todo.E_TODO_FIELD]


def test_todo_reports_each_entry_in_document_order(workspace):
    """Two defective entries, reported in the order the file writes them."""
    _todo_origin(workspace)
    _todo_add(workspace, title="first")
    _todo_add(workspace, title="second", kind="architecture")
    # `_todo_edit` replaces the first occurrence only, and the second entry is
    # given a `Kind` of its own, so each edit lands on the entry named beside it.
    _todo_edit(workspace, "**Run:** /mfix", "**Run:** /mnope")
    _todo_edit(workspace, "**Kind:** architecture", "**Kind:** typo")

    findings = _findings(workspace, "todo")
    assert _codes(findings) == [todo.E_TODO_ENUM, todo.E_TODO_ENUM]
    assert "'first'" in findings[0]["message"] and "Run" in findings[0]["message"]
    assert "'second'" in findings[1]["message"] and "Kind" in findings[1]["message"]
    assert findings[0]["line"] < findings[1]["line"]


def test_a_todo_finding_carries_the_entrys_line(workspace):
    _todo_origin(workspace)
    _todo_add(workspace, title="first")
    _todo_add(workspace, title="second")
    _todo_edit(workspace, "**Kind:** spec-drift", "**Kind:** typo")

    findings = _findings(workspace, "todo")
    lines = workspace.path(TODO_REL).read_text(encoding="utf-8").split("\n")
    assert lines[findings[0]["line"] - 1] == "## first"


def test_todo_findings_set_exit_1(workspace):
    _todo_origin(workspace)
    _todo_add(workspace)
    _todo_edit(workspace, "**Kind:** spec-drift", "**Kind:** typo")
    assert _check(workspace, "todo")[1] == 1


def test_todo_takes_no_target(workspace):
    """One list at one tier: a target would imply a per-repo list, and there is none."""
    result, code = _check(workspace, "todo", "../escape")
    assert code == 0
    assert result.data["target"] is None


# ---------------------------------------------------------------------------
# check handoff
# ---------------------------------------------------------------------------


def test_handoff_reports_nothing_on_a_complete_chain(workspace):
    _chain(workspace)
    result, code = _check(workspace, "handoff")
    assert result.data["findings"] == []
    assert result.data["target"] is None
    assert result.diagnostics == []
    assert code == 0


def test_handoff_strands_an_applied_change_with_no_index(workspace):
    """``stranded`` is the general rule, not its ``pending`` example.

    An ``applied`` repo change is complete; if no project index references it,
    no plan can ever reach it and it is stranded exactly as a ``pending`` one
    is. This is the defect REQ-019 exists to surface.
    """
    _chain(workspace)
    workspace.write(
        "context/%s/changes/CHANGE-002-orphaned.md" % TARGET,
        _change_text("002", "orphaned", "applied"),
    )

    findings = _findings(workspace, "handoff")
    assert len(findings) == 1
    finding = findings[0]
    assert finding["code"] == core.E_HANDOFF
    assert finding["state"] == "stranded"
    assert finding["from_stage"] == "mspec"
    assert finding["to_stage"] == "mplan"
    assert finding["artifact"] == "context/%s/changes/CHANGE-002-orphaned.md" % TARGET


def test_handoff_strands_a_pending_change_with_no_index(workspace):
    _chain(workspace)
    workspace.write(
        "context/%s/changes/CHANGE-002-fresh.md" % TARGET,
        _change_text("002", "fresh", "pending"),
    )

    findings = _findings(workspace, "handoff")
    assert len(findings) == 1
    assert findings[0]["state"] == "stranded"
    assert "CHANGE-002-fresh.md" in findings[0]["artifact"]


def test_handoff_does_not_strand_a_pending_change_marked_plan_not_required(workspace):
    _chain(workspace)
    workspace.write(
        "context/%s/changes/CHANGE-002-fresh.md" % TARGET,
        _change_text("002", "fresh", "pending", plan="not-required"),
    )

    findings = _findings(workspace, "handoff")
    assert findings == []


def test_handoff_strands_an_index_with_no_plan(workspace):
    _chain(workspace)
    change_path = "context/%s/changes/CHANGE-002-next.md" % TARGET
    workspace.write(change_path, _change_text("002", "next", "pending"))
    workspace.write(
        "context/project/changes/PROJECT-CHANGE-002-next.md",
        _index_text("002", "next", "pending", [change_path]),
    )

    findings = _findings(workspace, "handoff")
    assert len(findings) == 1
    assert findings[0]["artifact"] == "context/project/changes/PROJECT-CHANGE-002-next.md"
    assert findings[0]["state"] == "stranded"
    assert findings[0]["to_stage"] == "mplan"


def test_handoff_does_not_strand_an_index_with_no_plan_marked_plan_not_required(workspace):
    _chain(workspace)
    change_path = "context/%s/changes/CHANGE-002-next.md" % TARGET
    workspace.write(change_path, _change_text("002", "next", "pending"))
    workspace.write(
        "context/project/changes/PROJECT-CHANGE-002-next.md",
        _index_text("002", "next", "pending", [change_path], plan="not-required"),
    )

    findings = _findings(workspace, "handoff")
    assert findings == []


def test_handoff_reports_an_index_naming_no_change(workspace):
    """``incomplete``: the successor's input is present but partial."""
    _chain(workspace)
    workspace.write("context/project/plans/002-empty/plan.yaml", _graph_text("002-empty", project_change="002"))
    workspace.write("context/project/plans/002-empty/state.yaml", _plan_state_text("002-empty"))
    workspace.write(
        "context/project/changes/PROJECT-CHANGE-002-empty.md",
        _index_text("002", "empty", "pending", []),
    )

    findings = _findings(workspace, "handoff")
    assert len(findings) == 1
    assert findings[0]["state"] == "incomplete"
    assert findings[0]["artifact"] == "context/project/changes/PROJECT-CHANGE-002-empty.md"


def test_handoff_strands_a_plan_with_no_recorded_run(workspace):
    _chain(workspace)
    workspace.write("context/project/plans/002-next/plan.yaml", _graph_text("002-next"))
    workspace.write(
        "context/project/plans/002-next/state.yaml",
        _plan_state_text("002-next", run=0, statusname="pending", stories=(("01-01-demo-ALPHA", "pending"),)),
    )

    findings = _findings(workspace, "handoff")
    assert len(findings) == 1
    assert findings[0]["state"] == "stranded"
    assert findings[0]["from_stage"] == "mplan"
    assert findings[0]["to_stage"] == "mexecute"
    assert findings[0]["artifact"] == "context/project/plans/002-next"


def test_handoff_reports_a_plan_directory_with_no_graph(workspace):
    _chain(workspace)
    workspace.write("context/project/plans/002-partial/state.yaml", _plan_state_text("002-partial"))

    findings = _findings(workspace, "handoff")
    assert len(findings) == 1
    assert findings[0]["state"] == "incomplete"
    assert findings[0]["artifact"] == "context/project/plans/002-partial/plan.yaml"


def test_handoff_strands_drift_no_change_followed(workspace):
    _chain(workspace)
    workspace.write(
        "context/project/plans/001-demo/state.yaml",
        _plan_state_text("001-demo", conformance=("drift", 3)),
    )

    findings = _findings(workspace, "handoff")
    assert len(findings) == 1
    assert findings[0]["from_stage"] == "mverify"
    assert findings[0]["to_stage"] == "mfix"
    assert findings[0]["state"] == "stranded"
    assert "3 finding" in findings[0]["message"]


def test_handoff_accepts_drift_a_later_change_answers(workspace):
    """Drift followed by a new index is consumed, not stranded."""
    _chain(workspace)
    workspace.write(
        "context/project/plans/001-demo/state.yaml",
        _plan_state_text("001-demo", conformance=("drift", 3)),
    )
    change_path = "context/%s/changes/CHANGE-002-fix.md" % TARGET
    workspace.write(change_path, _change_text("002", "fix", "pending"))
    workspace.write(
        "context/project/changes/PROJECT-CHANGE-002-fix.md",
        _index_text("002", "fix", "pending", [change_path]),
    )
    workspace.write("context/project/plans/002-fix/plan.yaml", _graph_text("002-fix", project_change="002"))
    workspace.write("context/project/plans/002-fix/state.yaml", _plan_state_text("002-fix"))

    assert _findings(workspace, "handoff") == []


def test_handoff_reports_an_ownership_violation(workspace):
    """A conformance report is mverify's; a plan directory is not its tree."""
    _chain(workspace)
    workspace.write(
        "context/project/plans/001-demo/mverify-report.json",
        '{\n  "scope": {"kind": "aggregate"},\n  "findings": [],\n  "clean": true\n}\n',
    )

    findings = _findings(workspace, "handoff")
    assert len(findings) == 1
    assert findings[0]["code"] == check.E_OWNERSHIP
    assert findings[0]["artifact"] == "context/project/plans/001-demo/mverify-report.json"
    # Named in stage-chain order, which is what keeps both enums satisfiable.
    assert (findings[0]["from_stage"], findings[0]["to_stage"]) == ("mplan", "mverify")


def test_handoff_reports_a_requirement_no_module_covers(workspace):
    _chain(workspace)
    workspace.write(
        "context/%s/requirements/REQUIREMENTS.md" % TARGET,
        _requirements_text(("REQ-001", "REQ-002", "REQ-004")),
    )

    findings = _findings(workspace, "handoff")
    assert len(findings) == 1
    assert findings[0]["artifact"] == "REQ-004"
    assert findings[0]["from_stage"] == "mreq"
    assert findings[0]["to_stage"] == "mspec"


def _req_change_text(number, tier, statusname, spec_change=None):
    spec_line = "<!-- spec-change: %s -->\n" % spec_change if spec_change else ""
    return (
        "<!-- req-change: %s -->\n"
        "<!-- tier: %s -->\n"
        "<!-- status: %s -->\n"
        "<!-- date: 2026-01-01 -->\n"
        "%s"
        "\n# REQ-CHANGE-%s: A Requirements Change\n" % (number, tier, statusname, spec_line, number)
    )


def test_handoff_reports_an_open_req_change_as_a_warning_and_leaves_exit_0(workspace):
    _chain(workspace)
    workspace.write(
        "context/%s/requirements/changes/REQ-CHANGE-001-first-pass.md" % TARGET,
        _req_change_text("001", TARGET, "open"),
    )

    result, code = _check(workspace, "handoff")
    findings = result.data["findings"]
    assert len(findings) == 1
    assert findings[0]["code"] == core.E_HANDOFF
    assert findings[0]["from_stage"] == "mreq"
    assert findings[0]["to_stage"] == "mspec"
    assert findings[0]["state"] == "stranded"
    assert findings[0]["severity"] == "warning"
    assert code == 0


def test_handoff_does_not_report_a_closed_req_change(workspace):
    _chain(workspace)
    workspace.write(
        "context/%s/requirements/changes/REQ-CHANGE-001-first-pass.md" % TARGET,
        _req_change_text("001", TARGET, "closed", spec_change="CHANGE-001"),
    )
    assert _findings(workspace, "handoff") == []


def test_handoff_exempts_an_open_req_change_carrying_spec_change_not_required(workspace):
    _chain(workspace)
    workspace.write(
        "context/%s/requirements/changes/REQ-CHANGE-001-no-delta.md" % TARGET,
        _req_change_text("001", TARGET, "open", spec_change="not-required"),
    )
    assert _findings(workspace, "handoff") == []


def test_handoff_reports_every_other_finding_at_error_with_exit_1(workspace):
    """An open REQ-CHANGE (warning) alongside a real defect (error) still
    exits 1 -- the warning never masks a genuine finding."""
    _chain(workspace)
    workspace.write(
        "context/%s/requirements/changes/REQ-CHANGE-001-first-pass.md" % TARGET,
        _req_change_text("001", TARGET, "open"),
    )
    workspace.write(
        "context/%s/changes/CHANGE-002-orphaned.md" % TARGET,
        _change_text("002", "orphaned", "applied"),
    )
    result, code = _check(workspace, "handoff")
    findings = result.data["findings"]
    assert sorted(item["severity"] for item in findings) == ["error", "warning"]
    assert code == 1


def test_handoff_charges_a_req_change_write_to_no_stage(workspace):
    """`req change-close`'s write is tool-owned -- charged to no stage, so a
    closed REQ-CHANGE record never trips the ownership rule."""
    _chain(workspace)
    workspace.write(
        "context/%s/requirements/changes/REQ-CHANGE-002-closed-by-tool.md" % TARGET,
        _req_change_text("002", TARGET, "closed", spec_change="CHANGE-001"),
    )
    findings = _findings(workspace, "handoff")
    assert [item for item in findings if item["code"] == check.E_OWNERSHIP] == []
    assert findings == []


def test_handoff_finding_is_a_diagnostic(workspace):
    """``HandoffFinding = Diagnostic & {...}`` -- it is reported as both."""
    _chain(workspace)
    workspace.write(
        "context/%s/changes/CHANGE-002-orphaned.md" % TARGET,
        _change_text("002", "orphaned", "applied"),
    )
    result, code = _check(workspace, "handoff")
    assert code == 1
    assert len(result.diagnostics) == 1
    diagnostic = result.diagnostics[0]
    assert isinstance(diagnostic, core.Diagnostic)
    assert diagnostic.severity == core.SEVERITY_ERROR
    assert diagnostic.to_dict()["state"] == "stranded"


# ---------------------------------------------------------------------------
# check all
# ---------------------------------------------------------------------------


def test_all_runs_every_check_for_one_target(workspace):
    _chain(workspace)
    result, code = _check(workspace, "all", TARGET)
    assert [report["check"] for report in result.data["reports"]] == [
        "depends-on",
        "coupling",
        "requirements",
        "catalog",
        "todo",
        "handoff",
    ]
    assert result.data["targets"] == [TARGET]
    assert code == 0


def test_all_includes_the_todo_check_and_its_findings(workspace):
    """``check all`` gains a check, so a defect only it sees still fails the run."""
    _chain(workspace)
    _todo_origin(workspace)
    _todo_add(workspace)
    _todo_edit(workspace, "**Kind:** spec-drift", "**Kind:** typo")

    result, code = _check(workspace, "all", TARGET)
    reports = {report["check"]: report for report in result.data["reports"]}
    assert _codes(reports["todo"]["findings"]) == [todo.E_TODO_ENUM]
    assert code == 1


def test_all_without_a_target_covers_every_target(workspace):
    _chain(workspace)
    _tree(workspace, layout=_layout("other"), catalog=_catalog_text("other"), target="other")
    result, code = _check(workspace, "all")
    assert result.data["target"] is None
    assert result.data["targets"] == ["demo", "other"]
    assert [report["target"] for report in result.data["reports"]] == [
        "demo",
        "demo",
        "demo",
        "demo",
        "other",
        "other",
        "other",
        "other",
        None,  # todo -- one list, at the project tier
        None,  # handoff -- one stage chain, across the workspace
    ]
    assert code == 0


def test_all_carries_every_finding_into_the_envelope(workspace):
    _chain(workspace)
    layout = _layout()
    layout[_spec("ALPHA", "ALPHA-OVERVIEW.md")] = [_spec("COMMON", "COMMON-GONE.md")]
    _tree(workspace, layout)

    result, code = _check(workspace, "all", TARGET)
    assert code == 1
    assert [item.code for item in result.diagnostics] == [core.E_DANGLING_DEPENDS_ON]


# ---------------------------------------------------------------------------
# Exit codes and usage
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("verb", ["depends-on", "coupling", "requirements"])
def test_exit_code_follows_the_findings(workspace, verb):
    _tree(workspace)
    assert _check(workspace, verb, TARGET)[1] == 0

    layout = _layout()
    layout[_spec("ALPHA", "ALPHA-IMPLEMENTATION.md")] = [
        _spec("BETA", "BETA-IMPLEMENTATION.md"),
        _spec("ALPHA", "ALPHA-GONE.md"),
    ]
    _tree(workspace, layout, catalog=_catalog_text(modules=(("ALPHA", ("REQ-009",)),)))
    result, code = _check(workspace, verb, TARGET)
    assert result.data["findings"], "the defective fixture must report something"
    assert code == 1


def test_an_unknown_verb_is_a_usage_error(workspace):
    result, code = _check(workspace, None)
    assert code == 2
    assert [item.code for item in result.diagnostics] == [core.E_USAGE]


def test_a_bad_target_is_rejected_never_sanitized(workspace):
    _tree(workspace)
    result, code = _check(workspace, "depends-on", "../escape")
    assert code == 2
    assert [item.code for item in result.diagnostics] == [core.E_BAD_IDENT]


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def test_status_reports_the_documented_shape(workspace):
    _chain(workspace)
    result, code = _status(workspace)
    assert code == 0
    report = result.data
    assert sorted(report) == ["generated", "handoff", "stages"]
    assert report["generated"] == NOW
    assert sorted(report["stages"]) == [
        "changes",
        "conformance",
        "plans",
        "requirements",
        "slices",
    ]
    assert sorted(report["stages"]["requirements"]) == ["dangling", "open_req_changes", "uncovered"]
    assert report["stages"]["requirements"]["open_req_changes"] == []
    assert sorted(report["stages"]["changes"]) == ["pending", "unindexed"]
    assert sorted(report["stages"]["plans"]) == ["unfinished", "unplanned_indexes"]
    # findings and deferred side by side, never pre-subtracted: four findings of
    # which three are accepted debt is a fact a net figure of one would hide.
    assert report["stages"]["conformance"] == [
        {"plan_id": "001-demo", "status": "clean", "findings": 0, "deferred": 0}
    ]
    # A pre-slice plan reports null counters, not zero: no progress and no
    # slices to progress through are different facts.
    assert report["stages"]["slices"] == [
        {"plan_id": "001-demo", "applied": None, "total": None, "current": None}
    ]
    assert report["handoff"] == []


def test_status_reports_the_work_in_progress(workspace):
    _chain(workspace)
    workspace.write(
        "context/%s/changes/CHANGE-002-orphaned.md" % TARGET,
        _change_text("002", "orphaned", "pending"),
    )
    report = _status(workspace)[0].data
    pending = report["stages"]["changes"]["pending"]
    assert [ref["path"] for ref in pending] == ["context/%s/changes/CHANGE-002-orphaned.md" % TARGET]
    assert [ref["number"] for ref in report["stages"]["changes"]["unindexed"]] == ["002"]
    assert [finding["artifact"] for finding in report["handoff"]] == [
        "context/%s/changes/CHANGE-002-orphaned.md" % TARGET
    ]


def test_status_reuses_the_check_stage_walk(workspace):
    """One traversal, two presentations -- never two stage walks."""
    _chain(workspace)
    workspace.write(
        "context/%s/changes/CHANGE-002-orphaned.md" % TARGET,
        _change_text("002", "orphaned", "applied"),
    )
    report = _status(workspace)[0].data
    handoff = _findings(workspace, "handoff")
    assert report["handoff"] == handoff


def test_status_reports_an_unfinished_plan_as_a_resolution(workspace):
    _chain(workspace)
    workspace.write("context/project/plans/002-next/plan.yaml", _graph_text("002-next"))
    workspace.write(
        "context/project/plans/002-next/state.yaml",
        _plan_state_text("002-next", run=2, statusname="in-progress", stories=(("01-01-demo-ALPHA", "pending"),)),
    )
    report = _status(workspace)[0].data
    assert report["stages"]["plans"]["unfinished"] == [
        {
            "plan_id": "002-next",
            "plan_dir": "context/project/plans/002-next",
            "status": "in-progress",
            "run": 2,
            "resume_wave": 1,
            # `resume_wave` keeps its name and its meaning; `resume_slice` sits
            # alongside it, and reads "00" on a graph written before slices.
            "resume_slice": "00",
            "pending_stories": ["01-01-demo-ALPHA"],
        }
    ]


def test_status_is_deterministic_under_a_pinned_clock(workspace):
    _chain(workspace)
    workspace.write(
        "context/%s/changes/CHANGE-002-orphaned.md" % TARGET,
        _change_text("002", "orphaned", "applied"),
    )
    first = status.run(workspace.args(), workspace.ws).to_json()
    second = status.run(workspace.args(), workspace.ws).to_json()
    assert first == second
    assert '"generated": "%s"' % NOW in first


def test_status_reports_open_req_changes_from_the_same_walk(workspace):
    """`status` renders `open_req_changes` from the same single stage walk
    `check handoff` uses -- no second traversal."""
    _chain(workspace)
    workspace.write(
        "context/%s/requirements/changes/REQ-CHANGE-001-first-pass.md" % TARGET,
        _req_change_text("001", TARGET, "open"),
    )
    result, code = _status(workspace)
    assert result.data["stages"]["requirements"]["open_req_changes"] == [
        "context/%s/requirements/changes/REQ-CHANGE-001-first-pass.md" % TARGET
    ]
    assert code == 0


def test_status_reports_findings_as_data_not_as_failure(workspace):
    """``status`` describes the workspace; ``check`` judges it."""
    _chain(workspace)
    workspace.write(
        "context/%s/changes/CHANGE-002-orphaned.md" % TARGET,
        _change_text("002", "orphaned", "applied"),
    )
    result, code = _status(workspace)
    assert result.data["handoff"]
    assert result.ok is True
    assert code == 0
    assert _check(workspace, "handoff")[1] == 1


# ---------------------------------------------------------------------------
# Module boundaries
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["check.py", "status.py"])
def test_no_dependency_on_a_concurrent_sibling(name):
    """Neither group may reach for ``tools/state.py`` or ``tools/worktree.py``."""
    source = (PLUGIN_ROOT / "tools" / name).read_text(encoding="utf-8")
    assert "tools import" in source or "from tools" in source
    for sibling in ("state", "worktree"):
        assert "from tools import %s" % sibling not in source
        assert "tools.%s" % sibling not in source


def test_no_fixture_file_is_committed_under_the_plugin_tree(workspace):
    """Every fixture is synthetic and lives in ``tmp_path``."""
    _chain(workspace)
    assert workspace.root.exists()
    assert not (PLUGIN_ROOT / "tests").exists()


# ---------------------------------------------------------------------------
# The conformance stage scores `findings - deferred`
# ---------------------------------------------------------------------------


def _with_conformance(workspace, conformance):
    workspace.write(
        "context/project/plans/001-demo/state.yaml",
        _plan_state_text("001-demo", conformance=conformance),
    )
    return workspace


def test_four_findings_with_three_deferred_still_fires(workspace):
    _chain(workspace)
    _with_conformance(workspace, ("drift", 4, 3))
    findings = _findings(workspace, "handoff")
    assert len(findings) == 1
    assert findings[0]["from_stage"] == "mverify"
    assert findings[0]["to_stage"] == "mfix"
    # The message names what is outstanding, which is one, not four.
    assert "1 finding" in findings[0]["message"]


def test_four_findings_with_four_deferred_clears_the_stage(workspace):
    """A gate nobody can satisfy is one everybody learns to route around."""
    _chain(workspace)
    _with_conformance(workspace, ("drift", 4, 4))
    result, code = _check(workspace, "handoff")
    assert code == 0 and result.ok
    assert result.data["findings"] == []


def test_an_absent_deferred_count_scores_as_zero(workspace):
    """Every conformance block written before the field keeps its meaning."""
    _chain(workspace)
    _with_conformance(workspace, ("drift", 4))
    findings = _findings(workspace, "handoff")
    assert len(findings) == 1
    assert "4 finding" in findings[0]["message"]


def test_deferred_exceeding_findings_is_itself_a_finding(workspace):
    """Not scored to a negative outstanding count -- that would read as cleaner
    than clean, and the block is one nobody can act on."""
    _chain(workspace)
    _with_conformance(workspace, ("drift", 2, 5))
    findings = _findings(workspace, "handoff")
    assert _codes(findings) == [check.E_CONFORMANCE_DEFERRED]
    assert findings[0]["state"] == "incomplete"
    assert _check(workspace, "handoff")[1] == 1


def test_status_reports_findings_and_deferrals_separately(workspace):
    """A recorded acceptance, not a suppression: both numbers stay visible."""
    _chain(workspace)
    _with_conformance(workspace, ("drift", 4, 3))
    report = _status(workspace)[0].data
    assert report["stages"]["conformance"] == [
        {"plan_id": "001-demo", "status": "drift", "findings": 4, "deferred": 3}
    ]


# ---------------------------------------------------------------------------
# The change stage drains -- `change close` is what moves a document off pending
# ---------------------------------------------------------------------------


def test_a_pending_change_leaves_the_pending_list_once_it_is_closed(workspace):
    """Until `change close` existed nothing ever moved a document off `pending`,
    so the list grew monotonically and every shipped change read as outstanding."""
    _chain(workspace)
    path = "context/%s/changes/CHANGE-002-later.md" % TARGET
    workspace.write(path, _change_text("002", "later", "pending"))
    workspace.write(
        "context/project/changes/PROJECT-CHANGE-002-later.md",
        _index_text("002", "later", "pending", [path]),
    )
    workspace.mkdir("context/project/plans/002-later")
    workspace.write("context/project/plans/002-later/plan.yaml", _graph_text("002-later", project_change="002"))
    workspace.write("context/project/plans/002-later/state.yaml", _plan_state_text("002-later"))

    walk = check.walk_stages(workspace.ws)
    assert path in [ref["path"] for ref in walk.pending_changes]

    result = change.run(
        workspace.args(verb="close", path=path, status="applied"), workspace.ws
    )
    assert result.ok, [item.render() for item in result.diagnostics]

    walk = check.walk_stages(workspace.ws)
    assert path not in [ref["path"] for ref in walk.pending_changes]


def test_closing_the_index_drains_it_from_the_pending_list_too(workspace):
    _chain(workspace)
    index = "context/project/changes/PROJECT-CHANGE-002-later.md"
    path = "context/%s/changes/CHANGE-002-later.md" % TARGET
    workspace.write(path, _change_text("002", "later", "applied"))
    workspace.write(index, _index_text("002", "later", "pending", [path]))
    workspace.mkdir("context/project/plans/002-later")
    workspace.write("context/project/plans/002-later/plan.yaml", _graph_text("002-later", project_change="002"))
    workspace.write("context/project/plans/002-later/state.yaml", _plan_state_text("002-later"))

    assert index in [ref["path"] for ref in check.walk_stages(workspace.ws).pending_changes]
    change.run(workspace.args(verb="close", path=index, status="applied"), workspace.ws)
    assert index not in [ref["path"] for ref in check.walk_stages(workspace.ws).pending_changes]


# ---------------------------------------------------------------------------
# check catalog -- the depth rules
# ---------------------------------------------------------------------------


def _depth_tree(workspace, module, files, depth):
    """A tree whose one interesting module holds exactly ``files``."""
    layout = {}
    for filename in files:
        layout[_spec(module, filename)] = (
            [] if filename.endswith("-OVERVIEW.md") else [_spec(module, "%s-OVERVIEW.md" % module)]
        )
    layout[_spec("COMMON", "COMMON-OVERVIEW.md")] = None
    for path, depends in layout.items():
        workspace.write(path, _document(depends, Path(path).stem))

    lines = [
        "version: 1",
        "repo: %s" % TARGET,
        "layers:",
        "  L1-core:",
        "    modules: [COMMON, %s]" % module,
        "modules:",
        "  COMMON:",
        "    layer: L1-core",
        "    files:",
        "      - path: %s" % _spec("COMMON", "COMMON-OVERVIEW.md"),
        "        facet: overview",
        "  %s:" % module,
        "    layer: L1-core",
    ]
    if depth is not None:
        lines.append("    depth: %s" % depth)
    lines.append("    files:")
    for filename in files:
        lines.append("      - path: %s" % _spec(module, filename))
        lines.append("        facet: %s" % _facet_of(module, filename))
    workspace.write("context/%s/spec/CATALOG.yaml" % TARGET, "\n".join(lines) + "\n")
    return workspace


CONTRACT_FILES = ("ALPHA-OVERVIEW.md", "ALPHA-DATAMODEL.md", "ALPHA-INTERFACE.md")
FULL_FILES = CONTRACT_FILES + (
    "ALPHA-DEPENDENCIES.md",
    "ALPHA-IMPLEMENTATION.md",
    "ALPHA-TESTING.md",
)


def test_a_contract_module_missing_a_contract_facet_is_reported(workspace):
    _depth_tree(workspace, "ALPHA", ("ALPHA-OVERVIEW.md", "ALPHA-INTERFACE.md"), "contract")
    findings = _findings(workspace, "catalog", TARGET)
    assert _codes(findings) == [check.E_CATALOG_DEPTH]
    assert "datamodel" in findings[0]["message"]


def test_a_contract_module_whose_implementation_is_absent_is_not_reported(workspace):
    """`contract` asserts the remaining facets are *deliberately* unwritten, so
    their absence is expected rather than a missing file."""
    _depth_tree(workspace, "ALPHA", CONTRACT_FILES, "contract")
    assert _findings(workspace, "catalog", TARGET) == []


def test_a_full_module_missing_any_facet_is_reported(workspace):
    _depth_tree(workspace, "ALPHA", CONTRACT_FILES, "full")
    findings = _findings(workspace, "catalog", TARGET)
    assert _codes(findings) == [check.E_CATALOG_DEPTH]
    for facet in ("deps", "impl", "test"):
        assert facet in findings[0]["message"]


def test_a_full_module_carrying_every_facet_is_clean(workspace):
    _depth_tree(workspace, "ALPHA", FULL_FILES, "full")
    assert _findings(workspace, "catalog", TARGET) == []


def test_a_module_declaring_no_depth_is_never_reported_by_the_rule(workspace):
    """Absence resolves to `full` everywhere else, but it is not an assertion:
    reading it as one would fire on every catalog written before depth existed."""
    _depth_tree(workspace, "ALPHA", ("ALPHA-OVERVIEW.md",), None)
    assert _findings(workspace, "catalog", TARGET) == []


def test_the_depth_rule_runs_after_the_exports_rule(workspace):
    """Finding order is fixed: file-set, facet, layer, exports, then depth."""
    _depth_tree(workspace, "ALPHA", CONTRACT_FILES, "full")
    text = workspace.path("context/%s/spec/CATALOG.yaml" % TARGET).read_text(encoding="utf-8")
    workspace.write(
        "context/%s/spec/CATALOG.yaml" % TARGET,
        _set_catalog_exports(text, _spec("ALPHA", "ALPHA-INTERFACE.md"), ["ghost"]),
    )
    findings = _findings(workspace, "catalog", TARGET)
    assert _codes(findings) == [check.E_CATALOG_EXPORTS, check.E_CATALOG_DEPTH]


def test_the_live_catalog_still_passes_the_depth_rule(workspace):
    """The whole tree this repo ships declares no depth, and must stay clean."""
    _tree(workspace)
    assert _findings(workspace, "catalog", TARGET) == []
