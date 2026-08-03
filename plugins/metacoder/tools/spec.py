"""The ``spec`` command group -- spec-tree resolution and catalog emission.

    mc.py spec mode <target>
    mc.py spec layers <target>
    mc.py spec catalog-emit <target>
    mc.py spec consumers <IFACE>

``spec mode`` is one of the four documented **split steps**
(``TOOLS-IMPLEMENTATION.md`` §"Split steps"): this module answers whether a
spec tree exists and whether the requirements gate passes, and *nothing else*.
Whether the user is describing a new product despite an existing tree is
judgment, and it stays with ``mspec``'s prose -- there is deliberately no code
here that reads a description, a change file, or anything else that could
encode it.

``spec catalog-emit`` derives from the tree only what the tree actually states:
the module list, each module's ``files[]``, and each file's ``path``, ``facet``
and ``depends_on``. Five fields are **preserved verbatim** from the existing
catalog because none of them is derivable from ``depends-on`` front-matter --
``layers``, each module's ``layer``, each module's ``requirements``, each
INTERFACE file's ``exports``, and ``shared_interfaces``. Dropping any of them
would regress a real ``CATALOG.yaml`` while an emit-twice round trip still
passed. A module the existing catalog places in no layer -- neither in its
module entry nor in the ``layers`` block -- is refused rather than assigned
one, and an emission that would not validate is refused rather than persisted.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from tools import core, req

COMMAND = "spec"

SPEC_DIR = "spec"
CATALOG_FILENAME = "CATALOG.yaml"

#: The schema kind every emission is validated against before it is persisted.
CATALOG_KIND = "catalog"

#: The contract layer lives only at ``context/shared/spec/`` and its catalog is
#: the other flavor in ``catalog.schema.json``.
SHARED_TARGET = "shared"

#: Module/interface directory names, per ``catalog.schema.json``'s
#: ``propertyNames`` pattern and STANDARD-SPEC.md's ALL-CAPS ``<TAG>``.
MODULE_NAME_RE = re.compile(r"^[A-Z0-9-]+$")

SPEC_SUFFIX = ".md"

#: Filename suffix -> facet, per STANDARD-SPEC.md §"Facet Tags".
FACET_BY_SUFFIX = (
    ("-OVERVIEW.md", "overview"),
    ("-DATAMODEL.md", "datamodel"),
    ("-INTERFACE.md", "interface"),
    ("-DEPENDENCIES.md", "deps"),
    ("-IMPLEMENTATION.md", "impl"),
    ("-TESTING.md", "test"),
)

#: Emission order within a module: the facet order STANDARD-SPEC.md
#: §"Process & Ordering Rules" mandates for writing them.
FACET_ORDER = ("overview", "datamodel", "interface", "deps", "impl", "test")

#: ``COMMON-<NAME>.md`` files carry no facet suffix. STANDARD-SPEC.md §"COMMON
#: Files" gives them the same role as an OVERVIEW -- shared context injected
#: alongside module specs -- and that is the facet an existing catalog records
#: for them.
COMMON_MODULE = "COMMON"
COMMON_FACET = "overview"

#: The front-matter key every spec file carries.
DEPENDS_ON_KEY = "depends-on"

#: ``LS-shared`` sits above every repo layer; ``L<N>-*`` ascend from there.
_LAYER_RE = re.compile(r"^L(S|\d+)-")


# ---------------------------------------------------------------------------
# Tree walking -- every listing sorted, so output never depends on filesystem
# iteration order.
# ---------------------------------------------------------------------------


def spec_dir_path(target: str) -> str:
    """The workspace-relative spec directory for ``target``."""
    return "context/%s/%s" % (target, SPEC_DIR)


def catalog_path(target: str) -> str:
    """The workspace-relative ``CATALOG.yaml`` for ``target``."""
    return "%s/%s" % (spec_dir_path(target), CATALOG_FILENAME)


def module_dirs(spec_dir) -> List[str]:
    """Sorted module directory names under ``spec_dir``."""
    if not spec_dir.is_dir():
        return []
    return sorted(
        entry.name
        for entry in spec_dir.iterdir()
        if entry.is_dir() and MODULE_NAME_RE.match(entry.name)
    )


def spec_files(module_dir) -> List[str]:
    """Sorted spec filenames directly inside one module directory."""
    if not module_dir.is_dir():
        return []
    return sorted(
        entry.name
        for entry in module_dir.iterdir()
        if entry.is_file() and entry.name.endswith(SPEC_SUFFIX)
    )


def facet_for(filename: str) -> Optional[str]:
    """The facet a filename's suffix names, or ``None`` when it names none."""
    for suffix, facet in FACET_BY_SUFFIX:
        if filename.endswith(suffix) and len(filename) > len(suffix):
            return facet
    return None


def depends_on(path, display: str) -> List[str]:
    """The file's ``depends-on`` front-matter, in the order it was written."""
    matter = core.load_front_matter(path, display)
    raw = matter.get(DEPENDS_ON_KEY, "")
    return [item.strip() for item in raw.split(",") if item.strip()]


def layer_sort_key(name: str) -> Tuple[int, int, str]:
    """Order layers ``LS-shared`` -> ``L0-*`` -> ``L1-*`` -> ... -> unrecognised."""
    match = _LAYER_RE.match(name)
    if match is None:
        return (2, 0, name)
    token = match.group(1)
    if token == "S":
        return (0, 0, name)
    return (1, int(token), name)


# ---------------------------------------------------------------------------
# The existing catalog -- the sole source of the five preserved fields
# ---------------------------------------------------------------------------


class ExistingCatalog:
    """What an emission preserves from the catalog already on disk."""

    def __init__(self, document: Optional[Dict[str, Any]]) -> None:
        self.document: Dict[str, Any] = document or {}

    def _entries(self, key: str) -> Dict[str, Any]:
        value = self.document.get(key)
        return value if isinstance(value, dict) else {}

    def module_names(self, key: str) -> List[str]:
        """Declared module order, so an emission does not reshuffle the file."""
        return list(self._entries(key).keys())

    def field(self, key: str, module: str, name: str) -> Any:
        entry = self._entries(key).get(module)
        if not isinstance(entry, dict):
            return None
        return entry.get(name)

    def exports(self, key: str) -> Dict[str, Any]:
        """path -> ``exports``, for every file that carries them."""
        found: Dict[str, Any] = {}
        for entry in self._entries(key).values():
            if not isinstance(entry, dict):
                continue
            files = entry.get("files")
            if not isinstance(files, list):
                continue
            for item in files:
                if isinstance(item, dict) and "exports" in item and "path" in item:
                    found[item["path"]] = item["exports"]
        return found

    def top(self, name: str) -> Any:
        return self.document.get(name)

    def layer_of(self, module: str) -> Optional[str]:
        """The module's layer, from either place the catalog states it.

        ``modules.<TAG>.layer`` is the direct statement and wins. Failing that,
        the ``layers`` block is consulted, because a module a catalog has
        already placed in a layer is not a module whose layer is unknown --
        reading it there preserves an assignment that exists rather than
        inventing one that does not.
        """
        direct = self.field("modules", module, "layer")
        if direct is not None:
            return direct
        layers = self.document.get("layers")
        if not isinstance(layers, dict):
            return None
        for name in layers:
            entry = layers.get(name)
            listed = entry.get("modules") if isinstance(entry, dict) else None
            if isinstance(listed, list) and module in listed:
                return name
        return None


def load_existing_catalog(ws: core.Workspace, target: str) -> ExistingCatalog:
    """Read the catalog already on disk, or an empty one when there is none."""
    relative = catalog_path(target)
    path = ws.safe_path("context", target, SPEC_DIR, CATALOG_FILENAME)
    if not path.is_file():
        return ExistingCatalog(None)
    document = core.load_yaml(path, relative)
    if document is None:
        return ExistingCatalog(None)
    if not isinstance(document, dict):
        raise core.fail(
            core.E_PARSE,
            "existing catalog is not a mapping; refusing to emit over it and "
            "lose the fields it should preserve",
            file=relative,
        )
    return ExistingCatalog(document)


# ---------------------------------------------------------------------------
# `spec mode`
# ---------------------------------------------------------------------------


def _mode(args, ws: core.Workspace) -> core.Result:
    target = core.check_ident(args.target, "target")
    spec_dir = ws.safe_path("context", target, SPEC_DIR)
    modules = module_dirs(spec_dir)
    document = req.read_requirements(ws, target)
    return core.Result(
        command="%s.mode" % COMMAND,
        # Exactly the SpecMode shape, field for field. `mode` follows from the
        # presence of a module directory and from nothing else.
        data={
            "target": target,
            "mode": "UPDATE" if modules else "CREATE",
            "spec_dir": spec_dir_path(target),
            # Reported independently of `mode`: a CREATE target may already
            # have requirements and an UPDATE target may have none.
            "requirements": document.summary(),
        },
        diagnostics=document.diagnostics(),
    )


# ---------------------------------------------------------------------------
# `spec layers`
# ---------------------------------------------------------------------------


def _layers(args, ws: core.Workspace) -> core.Result:
    target = core.check_ident(args.target, "target")
    relative = catalog_path(target)
    path = ws.safe_path("context", target, SPEC_DIR, CATALOG_FILENAME)
    command = "%s.layers" % COMMAND
    if not path.is_file():
        return core.Result(
            command=command,
            data={"target": target, "path": relative, "layers": [], "modules": []},
            diagnostics=[core.error(core.E_NOT_FOUND, "no such catalog", file=relative)],
        )
    document = core.load_yaml(path, relative)
    declared = document.get("layers") if isinstance(document, dict) else None
    if not isinstance(declared, dict):
        return core.Result(
            command=command,
            data={"target": target, "path": relative, "layers": [], "modules": []},
            diagnostics=[
                core.error(core.E_INVALID_STATE, "catalog declares no layers", file=relative)
            ],
        )
    layers: List[Dict[str, Any]] = []
    ordered: List[str] = []
    for name in sorted(declared, key=layer_sort_key):
        entry = declared.get(name)
        modules = entry.get("modules") if isinstance(entry, dict) else None
        modules = [str(item) for item in modules] if isinstance(modules, list) else []
        layers.append({"layer": name, "modules": modules})
        ordered.extend(modules)
    return core.Result(
        command=command,
        data={"target": target, "path": relative, "layers": layers, "modules": ordered},
    )


# ---------------------------------------------------------------------------
# `spec catalog-emit`
# ---------------------------------------------------------------------------


def collect_modules(
    ws: core.Workspace, target: str, diagnostics: List[core.Diagnostic]
) -> Dict[str, List[Dict[str, Any]]]:
    """Walk the spec tree and derive every module's ``files[]`` from it."""
    spec_dir = ws.safe_path("context", target, SPEC_DIR)
    collected: Dict[str, List[Dict[str, Any]]] = {}
    for module in module_dirs(spec_dir):
        module_dir = ws.safe_path("context", target, SPEC_DIR, module)
        files: List[Dict[str, Any]] = []
        for filename in spec_files(module_dir):
            relative = "%s/%s/%s" % (spec_dir_path(target), module, filename)
            facet = facet_for(filename)
            if facet is None:
                if module == COMMON_MODULE:
                    facet = COMMON_FACET
                else:
                    diagnostics.append(
                        core.warning(
                            core.E_UNPARSED_NAME,
                            "filename names no facet; expected one of %s -- omitted from the catalog"
                            % (", ".join(suffix for suffix, _ in FACET_BY_SUFFIX),),
                            file=relative,
                        )
                    )
                    continue
            entry: Dict[str, Any] = {"path": relative, "facet": facet}
            # Re-resolved rather than joined: a symlinked spec file pointing out
            # of the tree is refused here, not read.
            required = depends_on(
                ws.safe_path("context", target, SPEC_DIR, module, filename), relative
            )
            if required:
                entry["depends_on"] = required
            files.append(entry)
        if not files:
            diagnostics.append(
                core.warning(
                    core.E_INVALID_STATE,
                    "module directory holds no spec file -- omitted from the catalog",
                    file="%s/%s" % (spec_dir_path(target), module),
                )
            )
            continue
        files.sort(key=lambda item: (FACET_ORDER.index(item["facet"]), item["path"]))
        collected[module] = files
    return collected


def _emission_order(declared: List[str], derived: Dict[str, List[Dict[str, Any]]]) -> List[str]:
    """Existing modules keep their place; new ones follow in sorted order."""
    order = [name for name in declared if name in derived]
    order.extend(sorted(name for name in derived if name not in declared))
    return order


def _with_exports(
    files: List[Dict[str, Any]], preserved: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """Re-attach each file's ``exports`` -- they live only in the catalog."""
    out: List[Dict[str, Any]] = []
    for entry in files:
        item = dict(entry)
        if entry["path"] in preserved:
            item["exports"] = preserved[entry["path"]]
        out.append(item)
    return out


def build_repo_catalog(
    target: str,
    now: str,
    existing: ExistingCatalog,
    derived: Dict[str, List[Dict[str, Any]]],
    diagnostics: List[core.Diagnostic],
) -> Dict[str, Any]:
    """Assemble a per-repo catalog: derived tree plus preserved fields."""
    relative = catalog_path(target)
    exports = existing.exports("modules")
    payload: Dict[str, Any] = {
        "version": 1,
        "generated": now,
        "repo": existing.top("repo") or target,
    }
    shared_interfaces = existing.top("shared_interfaces")
    if shared_interfaces is not None:
        payload["shared_interfaces"] = shared_interfaces
    layers = existing.top("layers")
    if layers is not None:
        payload["layers"] = layers
        if isinstance(layers, dict):
            for name in layers:
                entry = layers.get(name)
                listed = entry.get("modules") if isinstance(entry, dict) else None
                for module in listed if isinstance(listed, list) else []:
                    if module not in derived:
                        diagnostics.append(
                            core.warning(
                                core.E_INVALID_STATE,
                                "layer %r lists module %r, which has no directory in the spec tree"
                                % (name, module),
                                file=relative,
                            )
                        )
    else:
        diagnostics.append(
            core.error(
                core.E_INVALID_STATE,
                "no layer assignments to preserve: layers are never derived, so they must "
                "already exist in %s before it can be re-emitted" % (relative,),
                file=relative,
            )
        )
    modules: Dict[str, Any] = {}
    for module in _emission_order(existing.module_names("modules"), derived):
        layer = existing.layer_of(module)
        if layer is None:
            diagnostics.append(
                core.error(
                    core.E_INVALID_STATE,
                    "module %r has no layer in the existing catalog; a layer is preserved, "
                    "never derived, so add it to `layers` (or to the module entry) before "
                    "emitting" % (module,),
                    file=relative,
                )
            )
            continue
        entry: Dict[str, Any] = {"layer": layer, "files": _with_exports(derived[module], exports)}
        requirements = existing.field("modules", module, "requirements")
        if requirements is not None:
            entry["requirements"] = requirements
        modules[module] = entry
    payload["modules"] = modules
    return payload


def build_shared_catalog(
    target: str,
    now: str,
    existing: ExistingCatalog,
    derived: Dict[str, List[Dict[str, Any]]],
) -> Dict[str, Any]:
    """Assemble the contract-layer catalog: interfaces, no layers."""
    exports = existing.exports("interfaces")
    interfaces: Dict[str, Any] = {}
    for module in _emission_order(existing.module_names("interfaces"), derived):
        interfaces[module] = {"files": _with_exports(derived[module], exports)}
    return {"version": 1, "generated": now, "scope": SHARED_TARGET, "interfaces": interfaces}


def _catalog_emit(args, ws: core.Workspace) -> core.Result:
    target = core.check_ident(args.target, "target")
    relative = catalog_path(target)
    path = ws.safe_path("context", target, SPEC_DIR, CATALOG_FILENAME)
    diagnostics: List[core.Diagnostic] = []
    existing = load_existing_catalog(ws, target)
    derived = collect_modules(ws, target, diagnostics)
    if target == SHARED_TARGET:
        payload = build_shared_catalog(target, args.now, existing, derived)
    else:
        payload = build_repo_catalog(target, args.now, existing, derived, diagnostics)

    schema = core.load_schema(CATALOG_KIND)
    for message in core.validate_against(schema, payload):
        diagnostics.append(
            core.error(
                core.E_SCHEMA_INVALID,
                "emission does not validate against %r: %s" % (CATALOG_KIND, message),
                file=relative,
            )
        )
    # Refuse the write on any error: a catalog that would not validate, or one
    # missing a field that is preserved rather than derived, never reaches disk.
    written = not any(item.severity == core.SEVERITY_ERROR for item in diagnostics)
    if written:
        core.write_yaml(path, payload, schema)
    return core.Result(
        command="%s.catalog-emit" % COMMAND,
        data={
            "target": target,
            "path": relative,
            "written": written,
            "modules": sorted(derived),
        },
        diagnostics=diagnostics,
    )


# ---------------------------------------------------------------------------
# `spec consumers`
# ---------------------------------------------------------------------------


def _consumers(args, ws: core.Workspace) -> core.Result:
    interface = core.check_ident(args.interface, "interface TAG")
    diagnostics: List[core.Diagnostic] = []
    scanned: List[str] = []
    consumers: List[str] = []
    for target in ws.targets:  # already sorted, and excludes `project`
        relative = catalog_path(target)
        path, escape = ws.resolve_path("context", target, SPEC_DIR, CATALOG_FILENAME)
        if escape is not None:
            diagnostics.append(escape)
            continue
        if not path.is_file():
            continue
        scanned.append(target)
        try:
            document = core.load_yaml(path, relative)
        except core.ToolError as exc:
            # A catalog that cannot be read is reported rather than skipped in
            # silence: a cascade that misses a consumer is the failure this
            # command exists to prevent.
            diagnostics.append(exc.diagnostic)
            continue
        declared = document.get("shared_interfaces") if isinstance(document, dict) else None
        if isinstance(declared, list) and interface in declared:
            consumers.append(target)
    return core.Result(
        command="%s.consumers" % COMMAND,
        data={"interface": interface, "consumers": consumers, "scanned": scanned},
        diagnostics=diagnostics,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def register(subparsers) -> None:
    """Declare ``spec``'s verbs on ``mc.py``'s subparser action."""
    parser = subparsers.add_parser(
        COMMAND,
        help="spec-tree resolution, layer order, catalog emission, consumer lookup",
        description="Resolve a spec tree, emit its CATALOG.yaml, and find a shared interface's consumers.",
    )
    verbs = parser.add_subparsers(dest="verb", metavar="verb", required=True)

    mode_parser = verbs.add_parser(
        "mode",
        help="CREATE/UPDATE plus the requirements gate result",
        description=(
            "Report whether the target's spec tree exists and whether its requirements "
            "gate passes. Judgment -- whether the user is describing a new product "
            "despite an existing tree -- belongs to mspec, not here."
        ),
    )
    mode_parser.add_argument("target", help="a repo name or 'shared'")

    layers_parser = verbs.add_parser(
        "layers",
        help="the target's modules in layer order",
        description="Read the target's CATALOG.yaml and return its modules in layer order.",
    )
    layers_parser.add_argument("target", help="a repo name or 'shared'")

    emit_parser = verbs.add_parser(
        "catalog-emit",
        help="write CATALOG.yaml derived from the spec tree",
        description=(
            "Derive modules, files, facets and depends_on from the tree; preserve layers, "
            "each module's layer and requirements, each INTERFACE file's exports, and "
            "shared_interfaces from the existing catalog."
        ),
    )
    emit_parser.add_argument("target", help="a repo name or 'shared'")

    consumers_parser = verbs.add_parser(
        "consumers",
        help="repos listing <IFACE> under shared_interfaces",
        description="Scan every context/*/spec/CATALOG.yaml for the interface TAG.",
    )
    consumers_parser.add_argument("interface", metavar="IFACE", help="a shared interface TAG")

    parser.set_defaults(group=COMMAND)


VERBS = {
    "mode": _mode,
    "layers": _layers,
    "catalog-emit": _catalog_emit,
    "consumers": _consumers,
}


def run(args, ws: core.Workspace) -> core.Result:
    """Dispatch one ``spec`` verb. Never raises; failures are diagnostics."""
    verb = getattr(args, "verb", None)
    command = "%s.%s" % (COMMAND, verb) if verb else COMMAND
    handler = VERBS.get(verb)
    if handler is None:
        return core.Result(
            command=command,
            diagnostics=[
                core.error(
                    core.E_USAGE,
                    "unknown %s verb %r; expected one of: %s"
                    % (COMMAND, verb, ", ".join(sorted(VERBS))),
                )
            ],
        )
    try:
        return handler(args, ws)
    except core.ToolError as exc:
        return core.Result(command=command, diagnostics=[exc.diagnostic])
