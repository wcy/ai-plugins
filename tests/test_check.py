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
from tools import change, check, core, spec

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
# check handoff
# ---------------------------------------------------------------------------




























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














# ---------------------------------------------------------------------------
# check handoff folds the open deferrals in
#
# The stage chain answers "what did a stage leave behind"; the list answers
# "what did a run decide not to do". Both are outstanding work, and this is
# where outstanding work is read -- so the entries are reported here, each
# carrying the skill it is routed to, and none of them ever blocks.
# ---------------------------------------------------------------------------


def _deferrals(findings):
    """The deferral findings in a handoff report, in report order."""
    return [item for item in findings if item["code"] == check.W_TODO_OPEN]


def _chain_findings(findings):
    """The stage-chain findings: the ones carrying the `HandoffFinding` keys."""
    return [item for item in findings if "state" in item]










































# ---------------------------------------------------------------------------
# check all
# ---------------------------------------------------------------------------








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
















# ---------------------------------------------------------------------------
# Module boundaries
# ---------------------------------------------------------------------------




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












# ---------------------------------------------------------------------------
# The change stage drains -- `change close` is what moves a document off pending
# ---------------------------------------------------------------------------






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




def test_a_contract_module_whose_implementation_is_absent_is_not_reported(workspace):
    """`contract` asserts the remaining facets are *deliberately* unwritten, so
    their absence is expected rather than a missing file."""
    _depth_tree(workspace, "ALPHA", CONTRACT_FILES, "contract")
    assert _findings(workspace, "catalog", TARGET) == []




def test_a_full_module_carrying_every_facet_is_clean(workspace):
    _depth_tree(workspace, "ALPHA", FULL_FILES, "full")
    assert _findings(workspace, "catalog", TARGET) == []


def test_a_module_declaring_no_depth_is_never_reported_by_the_rule(workspace):
    """Absence resolves to `full` everywhere else, but it is not an assertion:
    reading it as one would fire on every catalog written before depth existed."""
    _depth_tree(workspace, "ALPHA", ("ALPHA-OVERVIEW.md",), None)
    assert _findings(workspace, "catalog", TARGET) == []




def test_the_live_catalog_still_passes_the_depth_rule(workspace):
    """The whole tree this repo ships declares no depth, and must stay clean."""
    _tree(workspace)
    assert _findings(workspace, "catalog", TARGET) == []


# ---------------------------------------------------------------------------
# The sweep stage -- a finished plan that never declared
#
# What is checked is that the declaration is PRESENT, never that it is right.
# Nothing can confirm a run enumerated everything it learned, so a block
# recording `filed: 0` clears the stage exactly as one recording five does.
# ---------------------------------------------------------------------------


def _sweep_state(plan_id="001-demo", version=4, statusname="applied", sweep=None):
    lines = [
        "version: %d" % version,
        "plan_id: %s" % plan_id,
        "run: 1",
        "status: %s" % statusname,
        "stories:",
        "  01-01-demo-ALPHA:",
        "    repo: %s" % TARGET,
        "    wave: 1",
        "    status: applied",
        "    retries: 0",
    ]
    if sweep is not None:
        lines.append("sweep:")
        lines.append("  filed: %d" % sweep)
    return "\n".join(lines) + "\n"


def _sweep_findings(workspace, **kwargs):
    _chain(workspace)
    workspace.write("context/project/plans/001-demo/state.yaml", _sweep_state(**kwargs))
    return [
        item
        for item in _findings(workspace, "handoff")
        if item["code"] == check.W_HANDOFF_SWEEP
    ]










