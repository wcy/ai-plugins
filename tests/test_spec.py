"""The ``spec`` group: mode detection, layer order, catalog emission, consumers.

``TOOLS-TESTING.md`` requires this suite to establish CREATE/UPDATE on empty,
partial and populated trees, that the requirements-gate result is independent
of the mode, and that ``consumers`` finds exactly the repos listing a TAG.

Two further properties are asserted here because a naive implementation passes
without them: ``catalog-emit`` is **idempotent** (emit, emit again with the
same ``--now``, byte-identical), and it **preserves** the five fields that are
not derivable from the spec tree -- ``layers``, each module's ``layer`` and
``requirements``, each INTERFACE file's ``exports``, and ``shared_interfaces``.
An emit-twice round trip alone would still pass while all five were being
dropped, which would regress a real ``CATALOG.yaml``.

Every emission runs against a ``tmp_path`` workspace built by the ``workspace``
fixture -- never the live one. The group is imported and called directly, never
through ``argv``.
"""

import pytest

from conftest import NOW
from tools import core, spec

FRONT_MATTER = "<!-- requirements: demo -->\n<!-- updated: 2026-01-01 -->\n"

REQUIREMENT = (
    "\n# Requirements — demo\n\n"
    "### REQ-001: A need someone has\n\n"
    "**Need:** Someone wants the thing to happen.\n"
    "**Rationale:** It is worth money.\n"
    "**Status:** active\n"
)

#: The tree the `demo` fixture builds: module -> filenames.
DEMO_TREE = {
    "COMMON": ["COMMON-OVERVIEW.md", "COMMON-PACKAGING.md"],
    "ALPHA": [
        "ALPHA-OVERVIEW.md",
        "ALPHA-DATAMODEL.md",
        "ALPHA-INTERFACE.md",
        "ALPHA-DEPENDENCIES.md",
        "ALPHA-IMPLEMENTATION.md",
        "ALPHA-TESTING.md",
    ],
}

#: An existing catalog carrying all five non-derivable fields.
DEMO_CATALOG = """version: 1
repo: demo
shared_interfaces:
  - EVENT-BUS
layers:
  L0-foundation:
    modules: [COMMON]
  L1-core:
    modules: [ALPHA]
modules:
  COMMON:
    layer: L0-foundation
    requirements: [REQ-001]
    files:
      - path: context/demo/spec/COMMON/COMMON-OVERVIEW.md
        facet: overview
  ALPHA:
    layer: L1-core
    requirements: [REQ-002, 'project:REQ-009']
    files:
      - path: context/demo/spec/ALPHA/ALPHA-INTERFACE.md
        facet: interface
        exports:
          - alphaClient
          - AlphaOptions
"""


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def spec_file(workspace, target, module, filename, depends=()):
    text = ""
    if depends:
        text += "<!-- depends-on: %s -->\n" % ", ".join(depends)
    text += "\n# %s\n" % filename
    return workspace.write("context/%s/spec/%s/%s" % (target, module, filename), text)


def catalog_file(workspace, target, text):
    return workspace.write("context/%s/spec/CATALOG.yaml" % target, text)


def requirements_file(workspace, target, body=REQUIREMENT):
    return workspace.write(
        "context/%s/requirements/REQUIREMENTS.md" % target, FRONT_MATTER + body
    )


def run(workspace, verb, target=None, interface=None):
    fields = {"verb": verb}
    if target is not None:
        fields["target"] = target
    if interface is not None:
        fields["interface"] = interface
    result = spec.run(workspace.args(**fields), workspace.ws)
    return result, core.exit_code(result)


def tree_snapshot(workspace):
    """Every path under the workspace root, so a stray write is visible."""
    return sorted(str(path.relative_to(workspace.root)) for path in workspace.root.rglob("*"))


@pytest.fixture
def demo(workspace):
    """A populated `demo` target: two modules, a catalog, requirements."""
    for module, filenames in DEMO_TREE.items():
        for filename in filenames:
            depends = ()
            if not filename.endswith("-OVERVIEW.md"):
                depends = ("context/demo/spec/%s/%s-OVERVIEW.md" % (module, module),)
            if filename == "COMMON-PACKAGING.md":
                depends = ("context/demo/spec/COMMON/COMMON-OVERVIEW.md",)
            spec_file(workspace, "demo", module, filename, depends)
    catalog_file(workspace, "demo", DEMO_CATALOG)
    requirements_file(workspace, "demo")
    return workspace


def emitted(workspace, target="demo"):
    return core.load_yaml(workspace.path("context/%s/spec/CATALOG.yaml" % target))


# ---------------------------------------------------------------------------
# `spec mode` -- tree presence and the gate, and nothing else
# ---------------------------------------------------------------------------


def test_mode_is_create_when_no_spec_tree_exists(workspace):
    result, code = run(workspace, "mode", "demo")
    assert code == 0
    assert result.data["mode"] == "CREATE"
    assert result.data["spec_dir"] == "context/demo/spec"


def test_mode_is_create_when_the_spec_dir_holds_no_module(workspace):
    workspace.mkdir("context/demo/spec")
    assert run(workspace, "mode", "demo")[0].data["mode"] == "CREATE"


def test_mode_is_update_on_a_partial_tree(workspace):
    # One module directory, one file, no catalog yet.
    spec_file(workspace, "demo", "ALPHA", "ALPHA-OVERVIEW.md")
    assert run(workspace, "mode", "demo")[0].data["mode"] == "UPDATE"


def test_mode_is_update_on_a_populated_tree(demo):
    assert run(demo, "mode", "demo")[0].data["mode"] == "UPDATE"


def test_mode_returns_exactly_the_specmode_shape(demo):
    data = run(demo, "mode", "demo")[0].data
    assert list(data) == ["target", "mode", "spec_dir", "requirements"]
    assert list(data["requirements"]) == ["path", "exists", "entries", "gate_passes"]
    assert data["target"] == "demo"
    assert data["requirements"]["path"] == "context/demo/requirements/REQUIREMENTS.md"


@pytest.mark.parametrize("build", ["empty", "partial", "populated"])
def test_the_gate_result_is_independent_of_the_mode(workspace, build):
    if build == "partial":
        spec_file(workspace, "demo", "ALPHA", "ALPHA-OVERVIEW.md")
    elif build == "populated":
        for filename in DEMO_TREE["ALPHA"]:
            spec_file(workspace, "demo", "ALPHA", filename)
        catalog_file(workspace, "demo", DEMO_CATALOG)

    # No requirements yet: the gate fails whatever the mode is.
    without = run(workspace, "mode", "demo")[0].data
    assert without["mode"] == ("CREATE" if build == "empty" else "UPDATE")
    assert without["requirements"]["exists"] is False
    assert without["requirements"]["gate_passes"] is False

    # The very same tree, now with requirements: the mode does not move.
    requirements_file(workspace, "demo")
    with_requirements = run(workspace, "mode", "demo")[0].data
    assert with_requirements["mode"] == without["mode"]
    assert with_requirements["requirements"]["gate_passes"] is True
    assert with_requirements["requirements"]["entries"] == ["REQ-001"]


def test_mode_writes_nothing(workspace):
    requirements_file(workspace, "demo")
    before = tree_snapshot(workspace)
    run(workspace, "mode", "demo")
    assert tree_snapshot(workspace) == before


# ---------------------------------------------------------------------------
# `spec layers`
# ---------------------------------------------------------------------------


def test_layers_returns_modules_in_layer_order(workspace):
    # Declared deliberately out of order, and with a two-digit layer that a
    # lexical sort would place before L2.
    catalog_file(
        workspace,
        "demo",
        "version: 1\n"
        "repo: demo\n"
        "layers:\n"
        "  L10-late:\n"
        "    modules: [OMEGA]\n"
        "  L2-services:\n"
        "    modules: [BETA, GAMMA]\n"
        "  L0-foundation:\n"
        "    modules: [COMMON]\n"
        "  LS-shared:\n"
        "    modules: [EVENT-BUS]\n"
        "modules: {}\n",
    )
    result, code = run(workspace, "layers", "demo")
    assert code == 0
    assert [item["layer"] for item in result.data["layers"]] == [
        "LS-shared",
        "L0-foundation",
        "L2-services",
        "L10-late",
    ]
    # Within a layer the catalog's own order is kept -- it is authored, not derived.
    assert result.data["modules"] == ["EVENT-BUS", "COMMON", "BETA", "GAMMA", "OMEGA"]


def test_layers_on_the_populated_fixture(demo):
    result, _code = run(demo, "layers", "demo")
    assert result.data["modules"] == ["COMMON", "ALPHA"]
    assert result.data["path"] == "context/demo/spec/CATALOG.yaml"


def test_layers_reports_a_missing_catalog(workspace):
    result, code = run(workspace, "layers", "demo")
    assert code == 1
    assert [item.code for item in result.diagnostics] == [core.E_NOT_FOUND]
    assert result.data["layers"] == []


def test_layers_reports_a_catalog_with_no_layers(workspace):
    catalog_file(workspace, "demo", "version: 1\nrepo: demo\nmodules: {}\n")
    result, code = run(workspace, "layers", "demo")
    assert code == 1
    assert [item.code for item in result.diagnostics] == [core.E_INVALID_STATE]


# ---------------------------------------------------------------------------
# `spec catalog-emit` -- derived from the tree, preserving the rest
# ---------------------------------------------------------------------------


def test_emit_derives_every_module_file_and_facet(demo):
    result, code = run(demo, "catalog-emit", "demo")
    assert code == 0
    assert result.data["written"] is True
    document = emitted(demo)
    assert sorted(document["modules"]) == ["ALPHA", "COMMON"]
    facets = {
        item["path"].rsplit("/", 1)[-1]: item["facet"]
        for item in document["modules"]["ALPHA"]["files"]
    }
    assert facets == {
        "ALPHA-OVERVIEW.md": "overview",
        "ALPHA-DATAMODEL.md": "datamodel",
        "ALPHA-INTERFACE.md": "interface",
        "ALPHA-DEPENDENCIES.md": "deps",
        "ALPHA-IMPLEMENTATION.md": "impl",
        "ALPHA-TESTING.md": "test",
    }
    # A COMMON-<NAME>.md file carries no facet suffix; it is an overview.
    assert [item["facet"] for item in document["modules"]["COMMON"]["files"]] == [
        "overview",
        "overview",
    ]
    # Files are emitted in facet order, not filesystem order.
    assert [item["path"] for item in document["modules"]["ALPHA"]["files"]] == [
        "context/demo/spec/ALPHA/ALPHA-%s.md" % suffix
        for suffix in (
            "OVERVIEW",
            "DATAMODEL",
            "INTERFACE",
            "DEPENDENCIES",
            "IMPLEMENTATION",
            "TESTING",
        )
    ]


def test_emit_derives_depends_on_from_the_front_matter(demo):
    run(demo, "catalog-emit", "demo")
    files = {item["path"]: item for item in emitted(demo)["modules"]["ALPHA"]["files"]}
    assert files["context/demo/spec/ALPHA/ALPHA-DATAMODEL.md"]["depends_on"] == [
        "context/demo/spec/ALPHA/ALPHA-OVERVIEW.md"
    ]
    # A file with no front-matter carries no `depends_on` key at all.
    assert "depends_on" not in files["context/demo/spec/ALPHA/ALPHA-OVERVIEW.md"]


def test_emit_validates_against_the_catalog_kind(demo):
    run(demo, "catalog-emit", "demo")
    outcome = core.validate_instance(
        "catalog", demo.path("context/demo/spec/CATALOG.yaml"), demo.ws
    )
    assert outcome.ok, outcome.to_json()


def test_emit_is_idempotent_with_the_same_now(demo):
    run(demo, "catalog-emit", "demo")
    first = demo.path("context/demo/spec/CATALOG.yaml").read_bytes()
    run(demo, "catalog-emit", "demo")
    second = demo.path("context/demo/spec/CATALOG.yaml").read_bytes()
    assert first == second
    assert b"generated: '%s'" % NOW.encode("utf-8") in first


def test_emit_preserves_the_five_non_derivable_fields(demo):
    original = core.load_yaml(demo.path("context/demo/spec/CATALOG.yaml"))
    run(demo, "catalog-emit", "demo")
    document = emitted(demo)

    assert document["layers"] == original["layers"]
    assert document["shared_interfaces"] == original["shared_interfaces"]
    assert document["modules"]["ALPHA"]["layer"] == "L1-core"
    assert document["modules"]["COMMON"]["layer"] == "L0-foundation"
    # Order preserved too: `requirements` and `exports` are lists, not sets.
    assert document["modules"]["ALPHA"]["requirements"] == ["REQ-002", "project:REQ-009"]
    assert document["modules"]["COMMON"]["requirements"] == ["REQ-001"]
    interface = [
        item
        for item in document["modules"]["ALPHA"]["files"]
        if item["facet"] == "interface"
    ][0]
    assert interface["exports"] == ["alphaClient", "AlphaOptions"]
    # And nothing else grew an `exports` key.
    assert [item["path"] for item in document["modules"]["ALPHA"]["files"] if "exports" in item] == [
        "context/demo/spec/ALPHA/ALPHA-INTERFACE.md"
    ]


def test_emit_survives_a_second_round_trip_without_losing_them(demo):
    """The preserved fields must come back from the emission, not just the fixture."""
    run(demo, "catalog-emit", "demo")
    first = emitted(demo)
    run(demo, "catalog-emit", "demo")
    second = emitted(demo)
    assert first == second
    assert second["modules"]["ALPHA"]["requirements"] == ["REQ-002", "project:REQ-009"]
    assert second["shared_interfaces"] == ["EVENT-BUS"]


def test_emit_keeps_the_existing_module_order_and_appends_new_ones(demo):
    spec_file(demo, "demo", "ZETA", "ZETA-OVERVIEW.md")
    spec_file(demo, "demo", "BETA", "BETA-OVERVIEW.md")
    # Both are new, so neither has a preserved layer: the write is refused.
    result, code = run(demo, "catalog-emit", "demo")
    assert code == 1
    assert result.data["written"] is False
    # Give them layers and the emission keeps COMMON, ALPHA first, then sorts.
    catalog_file(
        demo,
        "demo",
        DEMO_CATALOG
        + "  ZETA:\n    layer: L1-core\n    files:\n"
        "      - path: context/demo/spec/ZETA/ZETA-OVERVIEW.md\n        facet: overview\n"
        "  BETA:\n    layer: L1-core\n    files:\n"
        "      - path: context/demo/spec/BETA/BETA-OVERVIEW.md\n        facet: overview\n",
    )
    result, code = run(demo, "catalog-emit", "demo")
    assert code == 0
    assert list(emitted(demo)["modules"]) == ["COMMON", "ALPHA", "ZETA", "BETA"]


def test_emit_takes_a_new_modules_layer_from_the_layers_block(demo):
    # A module the catalog has already placed in a layer is not a module whose
    # layer is unknown: the assignment exists, so it is preserved, not invented.
    spec_file(demo, "demo", "BETA", "BETA-OVERVIEW.md")
    catalog_file(demo, "demo", DEMO_CATALOG.replace("modules: [ALPHA]", "modules: [ALPHA, BETA]"))
    result, code = run(demo, "catalog-emit", "demo")
    assert code == 0
    assert result.data["written"] is True
    document = emitted(demo)
    assert document["modules"]["BETA"]["layer"] == "L1-core"
    assert "requirements" not in document["modules"]["BETA"]
    assert core.validate_instance(
        "catalog", demo.path("context/demo/spec/CATALOG.yaml"), demo.ws
    ).ok
    # It is appended after the modules the catalog already enumerated.
    assert list(document["modules"]) == ["COMMON", "ALPHA", "BETA"]


def test_emit_refuses_to_invent_a_layer(demo):
    spec_file(demo, "demo", "BETA", "BETA-OVERVIEW.md")
    before = demo.path("context/demo/spec/CATALOG.yaml").read_bytes()
    result, code = run(demo, "catalog-emit", "demo")
    assert code == 1
    assert result.data["written"] is False
    assert core.E_INVALID_STATE in [item.code for item in result.diagnostics]
    assert demo.path("context/demo/spec/CATALOG.yaml").read_bytes() == before


def test_emit_refuses_when_there_is_no_catalog_to_preserve_from(workspace):
    spec_file(workspace, "demo", "ALPHA", "ALPHA-OVERVIEW.md")
    result, code = run(workspace, "catalog-emit", "demo")
    assert code == 1
    assert result.data["written"] is False
    assert core.E_INVALID_STATE in [item.code for item in result.diagnostics]
    assert not workspace.path("context/demo/spec/CATALOG.yaml").exists()


def test_emit_refuses_to_persist_a_catalog_that_fails_validation(demo):
    # `shared_interfaces` is preserved verbatim, so an entry that breaks the
    # schema's TAG pattern must stop the write rather than be silently dropped.
    broken = DEMO_CATALOG.replace("  - EVENT-BUS\n", "  - not a tag\n")
    catalog_file(demo, "demo", broken)
    before = demo.path("context/demo/spec/CATALOG.yaml").read_bytes()
    result, code = run(demo, "catalog-emit", "demo")
    assert code == 1
    assert result.data["written"] is False
    assert [item.code for item in result.diagnostics] == [core.E_SCHEMA_INVALID]
    assert demo.path("context/demo/spec/CATALOG.yaml").read_bytes() == before


def test_emit_refuses_to_read_a_catalog_it_cannot_preserve_from(demo):
    catalog_file(demo, "demo", "- just\n- a\n- list\n")
    result, code = run(demo, "catalog-emit", "demo")
    assert code == 2
    assert [item.code for item in result.diagnostics] == [core.E_PARSE]


def test_emit_warns_about_a_file_naming_no_facet(demo):
    spec_file(demo, "demo", "ALPHA", "ALPHA-NOTES.md")
    result, code = run(demo, "catalog-emit", "demo")
    assert code == 0  # a warning: the emission is still written
    assert [item.code for item in result.diagnostics] == [core.E_UNPARSED_NAME]
    paths = [item["path"] for item in emitted(demo)["modules"]["ALPHA"]["files"]]
    assert "context/demo/spec/ALPHA/ALPHA-NOTES.md" not in paths


def test_emit_writes_exactly_one_file(demo):
    before = tree_snapshot(demo)
    run(demo, "catalog-emit", "demo")
    assert tree_snapshot(demo) == before  # the catalog already existed
    demo.path("context/demo/spec/CATALOG.yaml").unlink()
    catalog_file(demo, "demo", DEMO_CATALOG)
    run(demo, "catalog-emit", "demo")
    assert tree_snapshot(demo) == before


def test_emit_ignores_a_module_directory_that_is_not_a_tag(demo):
    demo.write("context/demo/spec/notes/scratch.md", "# scratch\n")
    result, code = run(demo, "catalog-emit", "demo")
    assert code == 0
    assert sorted(emitted(demo)["modules"]) == ["ALPHA", "COMMON"]
    assert result.data["modules"] == ["ALPHA", "COMMON"]


def test_emit_of_the_shared_contract_layer(workspace):
    for filename, _facet in (
        ("EVENT-BUS-OVERVIEW.md", "overview"),
        ("EVENT-BUS-DATAMODEL.md", "datamodel"),
        ("EVENT-BUS-INTERFACE.md", "interface"),
    ):
        spec_file(workspace, "shared", "EVENT-BUS", filename)
    catalog_file(
        workspace,
        "shared",
        "version: 1\n"
        "scope: shared\n"
        "interfaces:\n"
        "  EVENT-BUS:\n"
        "    files:\n"
        "      - path: context/shared/spec/EVENT-BUS/EVENT-BUS-INTERFACE.md\n"
        "        facet: interface\n"
        "        exports:\n"
        "          - OrderPlaced\n",
    )
    result, code = run(workspace, "catalog-emit", "shared")
    assert code == 0
    document = emitted(workspace, "shared")
    assert document["scope"] == "shared"
    assert [item["facet"] for item in document["interfaces"]["EVENT-BUS"]["files"]] == [
        "overview",
        "datamodel",
        "interface",
    ]
    assert document["interfaces"]["EVENT-BUS"]["files"][2]["exports"] == ["OrderPlaced"]
    assert core.validate_instance(
        "catalog", workspace.path("context/shared/spec/CATALOG.yaml"), workspace.ws
    ).ok
    assert result.data["written"] is True


# ---------------------------------------------------------------------------
# `spec consumers`
# ---------------------------------------------------------------------------


def _consumer_workspace(workspace):
    catalog_file(
        workspace,
        "repo-a",
        "version: 1\nrepo: repo-a\nshared_interfaces:\n  - EVENT-BUS\n  - USER-API\n",
    )
    catalog_file(workspace, "repo-b", "version: 1\nrepo: repo-b\nshared_interfaces:\n  - USER-API\n")
    catalog_file(workspace, "repo-c", "version: 1\nrepo: repo-c\nshared_interfaces:\n  - EVENT-BUS\n")
    catalog_file(workspace, "repo-d", "version: 1\nrepo: repo-d\n")  # declares none
    spec_file(workspace, "repo-e", "ALPHA", "ALPHA-OVERVIEW.md")  # no catalog at all
    return workspace


def test_consumers_finds_exactly_the_repos_listing_the_tag(workspace):
    _consumer_workspace(workspace)
    result, code = run(workspace, "consumers", interface="EVENT-BUS")
    assert code == 0
    assert result.data["consumers"] == ["repo-a", "repo-c"]
    assert result.data["interface"] == "EVENT-BUS"


def test_consumers_scans_every_catalog_in_sorted_order(workspace):
    _consumer_workspace(workspace)
    result, _code = run(workspace, "consumers", interface="USER-API")
    assert result.data["consumers"] == ["repo-a", "repo-b"]
    assert result.data["scanned"] == ["repo-a", "repo-b", "repo-c", "repo-d"]


def test_consumers_is_empty_when_no_repo_lists_the_tag(workspace):
    _consumer_workspace(workspace)
    result, code = run(workspace, "consumers", interface="PAYMENTS")
    assert code == 0
    assert result.data["consumers"] == []


def test_consumers_reports_a_catalog_it_cannot_read(workspace):
    _consumer_workspace(workspace)
    catalog_file(workspace, "repo-b", "version: 1\n  bad: [indentation\n")
    result, code = run(workspace, "consumers", interface="EVENT-BUS")
    assert code == 2
    assert [item.code for item in result.diagnostics] == [core.E_PARSE]
    # The scan still completed: a broken sibling never hides a real consumer.
    assert result.data["consumers"] == ["repo-a", "repo-c"]


def test_consumers_writes_nothing(workspace):
    _consumer_workspace(workspace)
    before = tree_snapshot(workspace)
    run(workspace, "consumers", interface="EVENT-BUS")
    assert tree_snapshot(workspace) == before


# ---------------------------------------------------------------------------
# Identifiers and usage
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("verb", ["mode", "layers", "catalog-emit"])
@pytest.mark.parametrize("target", ["../escape", "/etc", "demo/../..", "bad target", ""])
def test_a_bad_target_is_rejected_never_sanitized(workspace, verb, target):
    before = tree_snapshot(workspace)
    result, code = run(workspace, verb, target)
    assert code == 2
    assert [item.code for item in result.diagnostics] == [core.E_BAD_IDENT]
    assert result.data is None
    assert tree_snapshot(workspace) == before


def test_a_bad_interface_tag_is_rejected(workspace):
    result, code = run(workspace, "consumers", interface="../escape")
    assert code == 2
    assert [item.code for item in result.diagnostics] == [core.E_BAD_IDENT]


def test_an_unknown_verb_is_a_usage_error(workspace):
    result = spec.run(workspace.args(verb="emit", target="demo"), workspace.ws)
    assert core.exit_code(result) == 2
    assert [item.code for item in result.diagnostics] == [core.E_USAGE]


def test_mode_encodes_no_judgment_about_a_new_product(demo):
    """The answer depends on the tree and the gate -- on nothing else.

    There is no argument through which a description of the work could reach
    this command, and the result for a fixed workspace is fixed.
    """
    first = run(demo, "mode", "demo")[0].to_json()
    second = run(demo, "mode", "demo")[0].to_json()
    assert first == second
    # A change document announcing a brand-new product is exactly the input a
    # judgment call would key on. The answer does not move.
    demo.write(
        "context/demo/changes/CHANGE-001-new-product.md",
        "<!-- change: 001 -->\n<!-- status: pending -->\n\n"
        "# CHANGE-001\n\nThis describes an entirely new product.\n",
    )
    assert run(demo, "mode", "demo")[0].to_json() == first
    assert set(vars(demo.args(verb="mode", target="demo"))) == {
        "verb",
        "target",
        "now",
        "json_out",
        "workspace",
    }
