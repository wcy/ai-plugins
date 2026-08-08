"""The ``check`` command group -- the rules the skills used to judge by reading.

    mc.py check depends-on <target>
    mc.py check coupling <target>
    mc.py check requirements <target>
    mc.py check catalog <target>
    mc.py check todo
    mc.py check handoff
    mc.py check all [<target>]

Each verb returns a ``CheckReport`` (``TOOLS-DATAMODEL.md`` §"Checks and
handoff") and every finding is a ``Diagnostic`` with a stable code, so a rule
that stops detecting a violation fails a test instead of silently returning a
clean report.

Several rules live here, and **only** here -- no other module re-derives one:

* **dangling ``depends-on``** -- a front-matter path that resolves to no file.
  ``COMMON-OVERVIEW.md`` is the one file exempt from carrying the front-matter
  at all ("this file is the root"), so its absence is never reported.
* **IMPLEMENTATION-to-IMPLEMENTATION coupling** -- ``STANDARD-SPEC.md``
  §"Dependency Rules": an IMPLEMENTATION may call another module's INTERFACE and
  must never depend on another module's IMPLEMENTATION.
* **cross-repo bypass** -- all cross-repo coupling goes through
  ``context/shared/spec/``; a ``depends-on`` reaching directly into another
  target is a violation whatever it points at.
* **the double declaration** -- a shared interface is declared in *both* the
  repo catalog's ``shared_interfaces`` and some spec file's ``depends-on``.
  Either half without the other is a finding.
* **catalog file-set agreement** -- every ``.md`` under the spec tree appears
  exactly once in ``CATALOG.yaml``'s ``files``, and every declared path exists.
* **catalog facet** -- a declared entry's ``facet`` matches what the filename
  suffix implies.
* **catalog layer** -- a module's declared ``layer`` matches the layer whose
  ``layers:`` block lists it.
* **catalog exports** -- an INTERFACE file's ``Exports:`` trailer, parsed per
  ``SHARED-INTERFACE.md``'s grammar, matches the entry's ``exports:`` list as a
  **set**: both directions are reported, absence is symmetric (neither side
  present conforms, one side alone is a finding), and a paragraph that begins
  ``Exports:`` but does not parse is a finding rather than a silent pass.
* **catalog depth** -- a module *declaring* ``depth: contract`` carries
  OVERVIEW, DATAMODEL and INTERFACE, and one declaring ``depth: full`` carries
  every facet. Only a declaration is measured: an absent ``depth`` means
  ``full`` for resolution but is not an assertion this rule may fire on, which
  is what keeps every catalog written before spec depth existed conforming.
* **the deferral list** -- every entry in ``context/project/TODO.md`` carries
  each required field, a ``Run``/``Kind``/rating inside its enum, an ``Origin``
  that resolves, and a ``Context`` naming something the reader could open. The
  first three are exactly what ``todo add`` refuses on and are reported with the
  same codes; the fourth is this check's alone, because the emitter does not
  apply it.

``check handoff`` walks the stage chain once. **``stranded`` is the general
rule** ``TOOLS-DATAMODEL.md`` states -- *the artifact is complete but no
successor has consumed it* -- not its three parenthetical examples: an
``applied`` repo change that no project index references is complete and
unconsumed exactly as a ``pending`` one is, and is reported the same way. That
generality is the point of REQ-019; reading only the parenthetical would make
the check blind to the defect the change document cites as its rationale.

It also **folds the open deferrals in**, each carrying the skill it is routed
to. A deferral is not stranded in the stage sense -- nothing downstream is
waiting on it, and no absent successor would reveal it -- so it is reported as
outstanding work with an owner rather than as a broken handoff: a plain
``Diagnostic`` at ``warning`` severity under its own code, never a
``HandoffFinding`` and never folded into ``stranded``, and it never changes the
exit code. Folding it in here is what makes "picked up on the next iteration"
true, because ``check handoff`` is where someone already looks to see what is
outstanding.

The walk in :func:`walk_stages` is the *only* stage traversal in the package;
``tools/status.py`` renders its ``StatusReport`` from the same object rather
than walking a second time.

Nothing here invokes ``git``, reads the wall clock, writes anything, or touches
``argv``: every function takes a ``Workspace`` and returns data. Directory
listings are sorted throughout, so finding order is stable between runs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from tools import core, spec

COMMAND = "check"

# ---------------------------------------------------------------------------
# Stable diagnostic codes owned by this module.
#
# `core` already names the codes shared with other groups
# (E_DANGLING_DEPENDS_ON, E_MISSING_REQUIREMENT, E_ORPHAN_REQUIREMENT,
# E_HANDOFF); the ones below are the per-rule codes the coupling check needs so
# that each of its three rules is machine-matchable on its own.
# ---------------------------------------------------------------------------

#: A spec file carries no `depends-on` front matter at all.
E_MISSING_DEPENDS_ON = "E_MISSING_DEPENDS_ON"

#: An IMPLEMENTATION depends on another module's IMPLEMENTATION.
E_COUPLING_IMPL = "E_COUPLING_IMPL"

#: A `depends-on` crosses into another target without going through
#: `context/shared/spec/`.
E_COUPLING_CROSS_REPO = "E_COUPLING_CROSS_REPO"

#: A shared interface is declared in only one of the two required places.
E_COUPLING_SHARED_DECL = "E_COUPLING_SHARED_DECL"

#: A file written by a stage that does not own where it sits.
E_OWNERSHIP = "E_OWNERSHIP"

#: A catalog reference whose mnemonic disagrees with the entry's own heading.
#: It still resolves -- it names a live requirement, not a dangling one -- so
#: this is a warning, never an error.
W_STALE_REQ_MNEMONIC = "W_STALE_REQ_MNEMONIC"

#: A spec file under the tree that no catalog entry declares.
E_CATALOG_UNLISTED_FILE = "E_CATALOG_UNLISTED_FILE"

#: A catalog entry whose `path` names no file in the tree.
E_CATALOG_ABSENT_PATH = "E_CATALOG_ABSENT_PATH"

#: A `path` declared by more than one catalog entry.
E_CATALOG_DUPLICATE_PATH = "E_CATALOG_DUPLICATE_PATH"

#: A declared `facet` disagreeing with what the filename suffix implies.
E_CATALOG_FACET = "E_CATALOG_FACET"

#: A module's declared `layer` disagreeing with the `layers:` block.
E_CATALOG_LAYER = "E_CATALOG_LAYER"

#: An INTERFACE file's `Exports:` trailer disagreeing with the entry's
#: `exports:` list -- in either direction, or by not parsing at all.
E_CATALOG_EXPORTS = "E_CATALOG_EXPORTS"

#: A declared `depth` the module's own files do not support: `contract` without
#: OVERVIEW/DATAMODEL/INTERFACE, or `full` with any facet missing.

#: A conformance block recording more deferrals than findings. Reported rather
#: than scored, because a negative outstanding count is not a cleaner plan --
#: it is a block nobody can act on.
E_CONFORMANCE_DEFERRED = "E_CONFORMANCE_DEFERRED"

#: An open entry on `context/project/TODO.md`, folded into `check handoff`
#: alongside the stage chain. A warning, never an error: outstanding work with
#: an owner is not a broken handoff, and a list that blocked the check would be
#: a gate cleared only by deferring nothing. Its own code, not `E_HANDOFF`, is
#: what makes it distinguishable from a stage-chain finding by machine.
W_TODO_OPEN = "W_TODO_OPEN"

#: An open entry routed to `human` -- work no invocation of anything clears.
#: Its own code, distinct from `W_TODO_OPEN`, is the partition: every other
#: reported entry names work some run can pick up, so that group drains, and a
#: `human` item left among them would make it permanently non-empty and
#: therefore unread. A standing prompt, not a stage-chain gap.
W_TODO_HUMAN = "W_TODO_HUMAN"

#: A finished plan carrying no `sweep` declaration. A warning: the run
#: delivered, and what is missing is its statement of what it left behind --
#: which nobody can supply retroactively by blocking on it.
W_HANDOFF_SWEEP = "W_HANDOFF_SWEEP"

#: The plan statuses at which a closing sweep declaration is owed. Delivery is
#: over at both: `applied` finished it, `failed` stopped it, and a stopped run
#: is the one with the most to defer.
TERMINAL_PLAN_STATUSES = ("applied", "failed")

#: The check names, per TOOLS-DATAMODEL.md's `CheckReport.check` enum.
CHECK_DEPENDS_ON = "depends-on"
CHECK_COUPLING = "coupling"
CHECK_CATALOG = "catalog"

#: `check all` runs these per-target checks, in this order.
TARGET_CHECKS = (CHECK_DEPENDS_ON, CHECK_COUPLING, CHECK_CATALOG)

# ---------------------------------------------------------------------------
# Spec-tree vocabulary
# ---------------------------------------------------------------------------

SPEC_DIR = spec.SPEC_DIR
CONTEXT_DIRNAME = "context"

#: The one file STANDARD-SPEC.md exempts from carrying `depends-on`.
ROOT_SPEC_FILE = "COMMON-OVERVIEW.md"

IMPLEMENTATION_SUFFIX = "-IMPLEMENTATION.md"
INTERFACE_SUFFIX = "-INTERFACE.md"

#: The `facet` value a catalog entry carries for an INTERFACE document -- the
#: only entries the exports rule looks at.
INTERFACE_FACET = "interface"

# ---------------------------------------------------------------------------
# `Exports:` trailer vocabulary -- SHARED-INTERFACE.md
# §"exports-trailer-grammar". The grammar is stated once, here, and parsed
# once, in `_exports_trailer`.
# ---------------------------------------------------------------------------

#: The word the trailer paragraph begins with.
EXPORTS_TRAILER_PREFIX = "Exports:"

#: A blank line (whitespace-only lines included) separates paragraphs.
PARAGRAPH_SPLIT_RE = re.compile(r"\n[ \t]*\n")

#: One backtick-quoted bare identifier -- a command name, a schema kind, a
#: skill's frontmatter `name`. Begins and ends alphanumeric, with ``.``, ``-``
#: and ``_`` permitted between: `mc.py` is why a dot is admitted, and the
#: end-anchor is why `a.` is not a token. The charset is stated in
#: ``SHARED-INTERFACE.md`` §"exports-trailer-grammar"; this regex implements it.
EXPORTS_TOKEN_RE = re.compile(r"^`([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)`$")

#: `_exports_trailer`'s three outcomes.
EXPORTS_ABSENT = "absent"
EXPORTS_PARSED = "parsed"
EXPORTS_UNPARSEABLE = "unparseable"

#: The contract layer: the only sanctioned cross-target dependency path.
SHARED_TARGET = spec.SHARED_TARGET
SHARED_SPEC_PREFIX = "%s/%s/%s/" % (CONTEXT_DIRNAME, SHARED_TARGET, SPEC_DIR)

#: `context/shared/spec/<TAG>/<TAG>-INTERFACE.md` -- the path form a repo spec
#: file must use to consume a shared interface.
SHARED_INTERFACE_RE = re.compile(
    r"^context/shared/spec/([A-Z0-9-]+)/\1-INTERFACE\.md$"
)

PROJECT_TIER = "project"


# ---------------------------------------------------------------------------
# Handoff vocabulary -- TOOLS-DATAMODEL.md §"Checks and handoff"
# ---------------------------------------------------------------------------

#: The stage chain, in order. `from_stage` never names `mfix` and `to_stage`
#: never names `mreq`, exactly as the two enums are written.
STAGE_ORDER = ("mreq", "mspec", "mplan", "mexecute", "mverify", "mfix")

STAGE_MREQ, STAGE_MSPEC, STAGE_MPLAN, STAGE_MEXECUTE, STAGE_MVERIFY, STAGE_MFIX = STAGE_ORDER

STATE_STRANDED = "stranded"
STATE_INCOMPLETE = "incomplete"


@dataclass(frozen=True)
class HandoffFinding(core.Diagnostic):
    """``Diagnostic & {from_stage, to_stage, artifact, state}``.

    A ``Diagnostic`` subclass rather than a parallel shape, because
    ``TOOLS-DATAMODEL.md`` defines it as an intersection: every handoff finding
    is a diagnostic and is reported as one.
    """

    from_stage: str = STAGE_MSPEC
    to_stage: str = STAGE_MPLAN
    artifact: str = ""
    state: str = STATE_STRANDED

    def to_dict(self) -> Dict[str, Any]:
        payload = super().to_dict()
        payload["from_stage"] = self.from_stage
        payload["to_stage"] = self.to_stage
        payload["artifact"] = self.artifact
        payload["state"] = self.state
        return payload


def handoff(
    code: str,
    message: str,
    from_stage: str,
    to_stage: str,
    artifact: str,
    state: str,
    file: Optional[str] = None,
    line: Optional[int] = None,
    severity: str = core.SEVERITY_ERROR,
) -> HandoffFinding:
    """One handoff finding, at the given severity (``error`` by default).

    Every pre-existing call site stays ``error``, which is what sets exit 1.
    The one exception is the open-`REQ-CHANGE` finding, passed
    ``severity=core.SEVERITY_WARNING`` at its call site: a captured
    requirement awaiting its spec change is a normal in-progress state, not a
    defect, and must leave exit 0.
    """
    return HandoffFinding(
        severity,
        code,
        message,
        file,
        line,
        from_stage,
        to_stage,
        artifact,
        state,
    )


def stage_pair(one: str, other: str) -> Tuple[str, str]:
    """``(earlier, later)`` in stage-chain order.

    Ordering the pair is what keeps the two enums satisfiable: ``mreq`` is only
    ever a ``from_stage`` and ``mfix`` only ever a ``to_stage``, so a finding
    about the boundary between two stages must name them in chain order.
    """
    if STAGE_ORDER.index(one) <= STAGE_ORDER.index(other):
        return one, other
    return other, one


# ---------------------------------------------------------------------------
# CheckReport
# ---------------------------------------------------------------------------


@dataclass
class CheckReport:
    """One check's result, per TOOLS-DATAMODEL.md §"Checks and handoff"."""

    check: str
    target: Optional[str]
    findings: List[core.Diagnostic] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "check": self.check,
            "target": self.target,
            "findings": [item.to_dict() for item in self.findings],
        }


# ---------------------------------------------------------------------------
# The spec tree -- one sorted walk, shared by all three per-target checks
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SpecFile:
    """One spec file and the ``depends-on`` block it declares."""

    target: str
    module: str
    name: str
    path: str  # workspace-relative
    facet: Optional[str]
    declared: bool  # a `depends-on` front-matter line is present
    depends_on: Tuple[str, ...]
    line: Optional[int]  # 1-based line of the front-matter comment

    @property
    def is_implementation(self) -> bool:
        return self.name.endswith(IMPLEMENTATION_SUFFIX)

    @property
    def is_root(self) -> bool:
        """``COMMON-OVERVIEW.md`` -- exempt from carrying ``depends-on``."""
        return self.name == ROOT_SPEC_FILE


def spec_files(ws: core.Workspace, target: str) -> List[SpecFile]:
    """Every spec file under ``context/<target>/spec/``, in sorted order.

    Module directories and filenames both come from ``tools/spec.py``'s sorted
    listings, so two runs over the same bytes produce the same order.
    """
    spec_dir = ws.safe_path(CONTEXT_DIRNAME, target, SPEC_DIR)
    collected: List[SpecFile] = []
    for module in spec.module_dirs(spec_dir):
        module_dir = ws.safe_path(CONTEXT_DIRNAME, target, SPEC_DIR, module)
        for name in spec.spec_files(module_dir):
            relative = "%s/%s/%s" % (spec.spec_dir_path(target), module, name)
            path = ws.safe_path(CONTEXT_DIRNAME, target, SPEC_DIR, module, name)
            text = core.read_text(path, relative)
            matter = core.read_front_matter(text)
            collected.append(
                SpecFile(
                    target=target,
                    module=module,
                    name=name,
                    path=relative,
                    facet=spec.facet_for(name),
                    declared=spec.DEPENDS_ON_KEY in matter,
                    # The values are parsed by `spec.depends_on`, the one
                    # implementation of that split.
                    depends_on=tuple(spec.depends_on(path, relative)),
                    line=_front_matter_line(text, spec.DEPENDS_ON_KEY),
                )
            )
    return collected


def _front_matter_line(text: str, key: str) -> Optional[int]:
    """The 1-based line carrying ``<!-- key: ... -->``, or ``None``."""
    for number, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        if not stripped:
            continue
        match = core.FRONT_MATTER_RE.match(stripped)
        if match is None:
            return None  # the front-matter block ends at the first other line
        if match.group(1) == key:
            return number
    return None


def _catalog(ws: core.Workspace, target: str) -> Tuple[Optional[spec.ExistingCatalog], str, bool]:
    """``(catalog, relative_path, exists)`` for ``context/<target>/spec/``."""
    relative = spec.catalog_path(target)
    path = ws.safe_path(CONTEXT_DIRNAME, target, SPEC_DIR, spec.CATALOG_FILENAME)
    if not path.is_file():
        return None, relative, False
    return spec.load_existing_catalog(ws, target), relative, True


# ---------------------------------------------------------------------------
# check depends-on
# ---------------------------------------------------------------------------


def depends_on_findings(ws: core.Workspace, target: str) -> List[core.Diagnostic]:
    """Every ``depends-on`` path that resolves to no file.

    A file carrying no ``depends-on`` block at all is reported too -- the
    front-matter is mandatory on every spec file, with ``COMMON-OVERVIEW.md``
    the single documented exemption.
    """
    findings: List[core.Diagnostic] = []
    for item in spec_files(ws, target):
        if not item.declared:
            if not item.is_root:
                findings.append(
                    core.error(
                        E_MISSING_DEPENDS_ON,
                        "spec file carries no `%s` front matter; only %s is exempt"
                        % (spec.DEPENDS_ON_KEY, ROOT_SPEC_FILE),
                        file=item.path,
                    )
                )
            continue
        for reference in item.depends_on:
            resolved, escape = ws.resolve_path(reference)
            if escape is not None:
                findings.append(
                    core.Diagnostic(
                        escape.severity, escape.code, escape.message, item.path, item.line
                    )
                )
                continue
            if not resolved.exists():
                findings.append(
                    core.error(
                        core.E_DANGLING_DEPENDS_ON,
                        "`%s` names %r, which does not exist"
                        % (spec.DEPENDS_ON_KEY, reference),
                        file=item.path,
                        line=item.line,
                    )
                )
    return findings


# ---------------------------------------------------------------------------
# check coupling
# ---------------------------------------------------------------------------


def coupling_findings(ws: core.Workspace, target: str) -> List[core.Diagnostic]:
    """The three dependency rules, each with its own stable code."""
    files = spec_files(ws, target)
    findings: List[core.Diagnostic] = []
    findings.extend(_implementation_coupling(files))
    findings.extend(_cross_repo_coupling(files, target))
    findings.extend(_shared_declaration(ws, files, target))
    return findings


def _implementation_coupling(files: Sequence[SpecFile]) -> List[core.Diagnostic]:
    """IMPLEMENTATION depending on another module's IMPLEMENTATION."""
    findings: List[core.Diagnostic] = []
    for item in files:
        if not item.is_implementation:
            continue
        for reference in item.depends_on:
            if not reference.endswith(IMPLEMENTATION_SUFFIX):
                continue
            other = _module_of(reference)
            if other is not None and (other[0], other[1]) == (item.target, item.module):
                continue  # its own module's file, which is not a cross-module edge
            findings.append(
                core.error(
                    E_COUPLING_IMPL,
                    "IMPLEMENTATION depends on another module's IMPLEMENTATION (%s); "
                    "an IMPLEMENTATION may depend on another module's INTERFACE only"
                    % (reference,),
                    file=item.path,
                    line=item.line,
                )
            )
    return findings


def _cross_repo_coupling(files: Sequence[SpecFile], target: str) -> List[core.Diagnostic]:
    """A ``depends-on`` reaching into another target outside ``shared/spec/``."""
    findings: List[core.Diagnostic] = []
    for item in files:
        for reference in item.depends_on:
            other = _target_of(reference)
            if other is None or other == target:
                continue
            if reference.startswith(SHARED_SPEC_PREFIX):
                continue  # the one sanctioned cross-target path
            findings.append(
                core.error(
                    E_COUPLING_CROSS_REPO,
                    "cross-repo `%s` on %r bypasses %s; all cross-repo coupling goes "
                    "through the shared contract layer"
                    % (spec.DEPENDS_ON_KEY, reference, SHARED_SPEC_PREFIX),
                    file=item.path,
                    line=item.line,
                )
            )
    return findings


def _shared_declaration(
    ws: core.Workspace, files: Sequence[SpecFile], target: str
) -> List[core.Diagnostic]:
    """``shared_interfaces`` and ``depends-on``, in both directions.

    Consumption of a shared interface is declared twice -- the TAG in the
    repo's ``CATALOG.yaml`` and the interface path in a spec file's
    ``depends-on`` -- so either half standing alone is a finding.
    """
    if target == SHARED_TARGET:
        return []  # the contract layer declares no consumption of itself
    catalog, relative, exists = _catalog(ws, target)
    declared: List[str] = []
    if exists and catalog is not None:
        listed = catalog.top("shared_interfaces")
        if isinstance(listed, list):
            declared = [str(tag) for tag in listed]

    referenced: Dict[str, str] = {}  # TAG -> first spec file naming it
    for item in files:
        for reference in item.depends_on:
            match = SHARED_INTERFACE_RE.match(reference)
            if match is not None:
                referenced.setdefault(match.group(1), item.path)

    findings: List[core.Diagnostic] = []
    for tag in sorted(set(declared)):
        if tag in referenced:
            continue
        findings.append(
            core.error(
                E_COUPLING_SHARED_DECL,
                "`shared_interfaces` declares %r but no spec file's `%s` names "
                "context/shared/spec/%s/%s-INTERFACE.md"
                % (tag, spec.DEPENDS_ON_KEY, tag, tag),
                file=relative,
            )
        )
    for tag in sorted(referenced):
        if tag in declared:
            continue
        findings.append(
            core.error(
                E_COUPLING_SHARED_DECL,
                "`%s` names the shared interface %r but `shared_interfaces` in %s does "
                "not declare it" % (spec.DEPENDS_ON_KEY, tag, relative),
                file=referenced[tag],
            )
        )
    return findings


def _path_parts(reference: str) -> List[str]:
    return [part for part in str(reference).split("/") if part]


def _target_of(reference: str) -> Optional[str]:
    """The target a ``context/<target>/...`` path names, if it names one."""
    parts = _path_parts(reference)
    if len(parts) < 2 or parts[0] != CONTEXT_DIRNAME:
        return None
    return parts[1]


def _module_of(reference: str) -> Optional[Tuple[str, str]]:
    """``(target, module)`` for a ``context/<t>/spec/<MODULE>/<file>`` path."""
    parts = _path_parts(reference)
    if len(parts) < 5 or parts[0] != CONTEXT_DIRNAME or parts[2] != SPEC_DIR:
        return None
    return parts[1], parts[3]


# ---------------------------------------------------------------------------
# check catalog
# ---------------------------------------------------------------------------


def _catalog_module_key(target: str) -> str:
    """``"modules"`` for a repo target, ``"interfaces"`` for the shared tree."""
    return "interfaces" if target == SHARED_TARGET else "modules"


def _catalog_declared_files(
    catalog: spec.ExistingCatalog, key: str
) -> List[Tuple[str, Dict[str, Any]]]:
    """``(path, entry)`` for every ``files[]`` entry, in catalog order."""
    declared: List[Tuple[str, Dict[str, Any]]] = []
    for module in catalog.module_names(key):
        entries = catalog.field(key, module, "files")
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if isinstance(entry, dict) and "path" in entry:
                declared.append((str(entry["path"]), entry))
    return declared


def _catalog_file_set(
    files: Sequence[SpecFile], declared: Sequence[Tuple[str, Dict[str, Any]]], relative: str
) -> List[core.Diagnostic]:
    """Spec files no entry declares; declared paths with no file, or repeated."""
    findings: List[core.Diagnostic] = []
    walked = {item.path: item for item in files}
    declared_paths = [path for path, _entry in declared]
    declared_set = set(declared_paths)

    for item in files:  # already sorted -- stable finding order
        if item.path not in declared_set:
            findings.append(
                core.error(
                    E_CATALOG_UNLISTED_FILE,
                    "spec file is under the tree but no catalog entry declares it",
                    file=item.path,
                )
            )

    seen: Dict[str, int] = {}
    for path in declared_paths:
        seen[path] = seen.get(path, 0) + 1
    reported_duplicate: Set[str] = set()
    for path in declared_paths:
        if path not in walked:
            findings.append(
                core.error(
                    E_CATALOG_ABSENT_PATH,
                    "catalog entry names %r, which no file in the tree matches" % (path,),
                    file=relative,
                )
            )
        if seen[path] > 1 and path not in reported_duplicate:
            reported_duplicate.add(path)
            findings.append(
                core.error(
                    E_CATALOG_DUPLICATE_PATH,
                    "%r is declared by more than one catalog entry" % (path,),
                    file=relative,
                )
            )
    return findings


def _catalog_facet(
    declared: Sequence[Tuple[str, Dict[str, Any]]]
) -> List[core.Diagnostic]:
    """A declared ``facet`` disagreeing with what the filename suffix implies."""
    findings: List[core.Diagnostic] = []
    for path, entry in declared:
        name = path.rsplit("/", 1)[-1]
        module = path.split("/")[-2] if "/" in path else ""
        expected = spec.facet_for(name)
        if expected is None and module == spec.COMMON_MODULE:
            expected = spec.COMMON_FACET
        if expected is None:
            continue  # genuinely unlistable -- already reported by the file-set rule
        actual = entry.get("facet")
        if actual != expected:
            findings.append(
                core.error(
                    E_CATALOG_FACET,
                    "declared facet %r disagrees with %r, which the filename implies"
                    % (actual, expected),
                    file=path,
                )
            )
    return findings


def _catalog_layer(catalog: spec.ExistingCatalog, relative: str) -> List[core.Diagnostic]:
    """A module's declared ``layer`` disagreeing with the ``layers:`` block."""
    layers_block = catalog.top("layers")
    layer_of: Dict[str, str] = {}
    if isinstance(layers_block, dict):
        for name, entry in layers_block.items():
            listed = entry.get("modules") if isinstance(entry, dict) else None
            if isinstance(listed, list):
                for module in listed:
                    layer_of.setdefault(str(module), name)

    findings: List[core.Diagnostic] = []
    for module in catalog.module_names("modules"):
        # `ExistingCatalog.layer_of` falls back to the module entry itself,
        # which would compare a value with itself -- read the block directly.
        declared_layer = catalog.field("modules", module, "layer")
        actual_layer = layer_of.get(module)
        if actual_layer is None:
            findings.append(
                core.error(
                    E_CATALOG_LAYER,
                    "module %r has declared layer %r but no `layers:` block lists it"
                    % (module, declared_layer),
                    file=relative,
                )
            )
        elif declared_layer != actual_layer:
            findings.append(
                core.error(
                    E_CATALOG_LAYER,
                    "module %r declares layer %r but the `layers:` block lists it under %r"
                    % (module, declared_layer, actual_layer),
                    file=relative,
                )
            )
    return findings


def _exports_list(tokens: Set[str]) -> str:
    """Backticked tokens, sorted -- messages must not vary between runs."""
    return ", ".join("`%s`" % token for token in sorted(tokens))


def _exports_trailer(text: str) -> Tuple[str, Set[str]]:
    """Parse an INTERFACE document's ``Exports:`` trailer.

    ``SHARED-INTERFACE.md`` §``exports-trailer-grammar``: the file's *last*
    paragraph, beginning with the word ``Exports:``, holding nothing but a
    comma-separated list of backtick-quoted bare identifiers and ending in a
    period. "Nothing but" governs the whole paragraph, and the list may wrap
    lines.

    Returns exactly one of three outcomes, and keeping them distinct is the
    point of the rule:

    * ``(EXPORTS_ABSENT, set())`` -- the last paragraph does not begin
      ``Exports:``. This is *absence*, not an error: a module exporting
      nothing omits the trailer entirely, so it is compared symmetrically
      against the entry's ``exports:``.
    * ``(EXPORTS_PARSED, tokens)`` -- the paragraph satisfies the grammar.
    * ``(EXPORTS_UNPARSEABLE, set())`` -- the paragraph begins ``Exports:``
      but violates the grammar. Never downgraded to absence: a trailer the
      checker cannot read is indistinguishable from one that disagrees.
    """
    paragraphs = [block for block in PARAGRAPH_SPLIT_RE.split(text.strip()) if block.strip()]
    if not paragraphs:
        return EXPORTS_ABSENT, set()
    last = paragraphs[-1].strip()
    if not last.startswith(EXPORTS_TRAILER_PREFIX):
        return EXPORTS_ABSENT, set()

    # The list may wrap lines, so normalize every run of whitespace to one
    # space before splitting on commas.
    body = " ".join(last[len(EXPORTS_TRAILER_PREFIX):].split())
    if not body.endswith("."):
        return EXPORTS_UNPARSEABLE, set()
    body = body[:-1].strip()
    if not body:
        return EXPORTS_UNPARSEABLE, set()

    tokens: Set[str] = set()
    for piece in body.split(","):
        match = EXPORTS_TOKEN_RE.match(piece.strip())
        if match is None:
            return EXPORTS_UNPARSEABLE, set()
        token = match.group(1)
        if token in tokens:
            # `SHARED-INTERFACE.md`: each token appears once and matches
            # exactly one catalog entry. Accumulating into a set would let a
            # repeat collapse silently and compare as conforming.
            return EXPORTS_UNPARSEABLE, set()
        tokens.add(token)
    return EXPORTS_PARSED, tokens


def _catalog_exports(
    ws: core.Workspace, declared: Sequence[Tuple[str, Dict[str, Any]]]
) -> List[core.Diagnostic]:
    """An INTERFACE file's ``Exports:`` trailer disagreeing with ``exports:``.

    The comparison is a **set** comparison in both directions, so ordering
    never matters and each direction that disagrees is its own finding.
    Unlike the layer rule this one is not skipped for the shared tree: a
    shared catalog's INTERFACE entries carry ``exports`` like any other.
    """
    findings: List[core.Diagnostic] = []
    for path, entry in declared:
        if entry.get("facet") != INTERFACE_FACET:
            continue
        file_path, diagnostic = ws.resolve_path(path)
        if diagnostic is not None or file_path is None or not file_path.is_file():
            continue  # already reported by the file-set rule
        listed = entry.get("exports")
        catalog_tokens = {str(token) for token in listed} if isinstance(listed, list) else set()
        # An `exports:` present but empty is the catalog-side placeholder
        # `SHARED-INTERFACE.md` forbids, not the same thing as omitting the key.
        # Distinguished so the diagnostic never misstates the file.
        catalog_empty = isinstance(listed, list) and not listed
        catalog_side = "declares an empty `exports:` list" if catalog_empty else (
            "has no `exports:` list"
        )
        outcome, document_tokens = _exports_trailer(core.read_text(file_path, path))

        if catalog_empty:
            findings.append(
                core.error(
                    E_CATALOG_EXPORTS,
                    "the catalog entry declares an empty `exports:` list; a module that "
                    "exports nothing omits the key entirely",
                    file=path,
                )
            )

        if outcome == EXPORTS_UNPARSEABLE:
            findings.append(
                core.error(
                    E_CATALOG_EXPORTS,
                    "the last paragraph begins %r but does not parse as an exports trailer"
                    % (EXPORTS_TRAILER_PREFIX,),
                    file=path,
                )
            )
            continue
        if outcome == EXPORTS_ABSENT:
            if catalog_tokens:
                findings.append(
                    core.error(
                        E_CATALOG_EXPORTS,
                        "the catalog declares exports %s but the document carries no "
                        "`Exports:` trailer" % (_exports_list(catalog_tokens),),
                        file=path,
                    )
                )
            continue
        if not catalog_tokens:
            findings.append(
                core.error(
                    E_CATALOG_EXPORTS,
                    "the document's `Exports:` trailer declares %s but the catalog entry %s"
                    % (_exports_list(document_tokens), catalog_side),
                    file=path,
                )
            )
            continue

        undeclared = document_tokens - catalog_tokens
        if undeclared:
            findings.append(
                core.error(
                    E_CATALOG_EXPORTS,
                    "the `Exports:` trailer declares %s, which the catalog entry omits"
                    % (_exports_list(undeclared),),
                    file=path,
                )
            )
        unwritten = catalog_tokens - document_tokens
        if unwritten:
            findings.append(
                core.error(
                    E_CATALOG_EXPORTS,
                    "the catalog entry declares %s, which the `Exports:` trailer omits"
                    % (_exports_list(unwritten),),
                    file=path,
                )
            )
    return findings


def catalog_findings(ws: core.Workspace, target: str) -> List[core.Diagnostic]:
    """Compare ``CATALOG.yaml`` against the spec tree it describes.

    Five rules, applied in this fixed order so finding order is stable
    between runs: file-set agreement, facet, layer, exports, then depth. The
    layer rule is skipped for ``spec.SHARED_TARGET``, whose catalog has no
    ``layers:`` block; the exports rule is not, since a shared catalog's
    INTERFACE entries carry ``exports`` like any other.
    """
    catalog, relative, exists = _catalog(ws, target)
    if not exists or catalog is None:
        return [core.error(core.E_NO_SUCH_FILE, "no such file", file=relative)]

    files = spec_files(ws, target)
    key = _catalog_module_key(target)
    declared = _catalog_declared_files(catalog, key)

    findings: List[core.Diagnostic] = []
    findings.extend(_catalog_file_set(files, declared, relative))
    findings.extend(_catalog_facet(declared))
    if target != SHARED_TARGET:
        findings.extend(_catalog_layer(catalog, relative))
    findings.extend(_catalog_exports(ws, declared))
    return findings


# ---------------------------------------------------------------------------
# Verbs
# ---------------------------------------------------------------------------

TARGET_RUNNERS = {
    CHECK_DEPENDS_ON: depends_on_findings,
    CHECK_COUPLING: coupling_findings,
    CHECK_CATALOG: catalog_findings,
}


def run_target_check(ws: core.Workspace, name: str, target: str) -> CheckReport:
    """One per-target check, as a ``CheckReport``."""
    return CheckReport(name, target, list(TARGET_RUNNERS[name](ws, target)))


def _report_result(report: CheckReport, extra: Sequence[core.Diagnostic] = ()) -> core.Result:
    diagnostics: List[core.Diagnostic] = list(report.findings)
    diagnostics.extend(extra)
    return core.Result(
        command="%s.%s" % (COMMAND, report.check), data=report.to_dict(), diagnostics=diagnostics
    )


def _depends_on(args, ws: core.Workspace) -> core.Result:
    target = core.check_ident(getattr(args, "target", None), "target")
    return _report_result(run_target_check(ws, CHECK_DEPENDS_ON, target))


def _coupling(args, ws: core.Workspace) -> core.Result:
    target = core.check_ident(getattr(args, "target", None), "target")
    return _report_result(run_target_check(ws, CHECK_COUPLING, target))


def _catalog_check(args, ws: core.Workspace) -> core.Result:
    target = core.check_ident(getattr(args, "target", None), "target")
    return _report_result(run_target_check(ws, CHECK_CATALOG, target))


def _all(args, ws: core.Workspace) -> core.Result:
    """Every per-target check, for one target or for all of them."""
    given = getattr(args, "target", None)
    if given is None:
        targets = list(ws.targets)  # sorted, and excludes `project`
    else:
        targets = [core.check_ident(given, "target")]
    reports: List[CheckReport] = []
    diagnostics: List[core.Diagnostic] = []
    for target in targets:
        for name in TARGET_CHECKS:
            reports.append(run_target_check(ws, name, target))
    findings: List[core.Diagnostic] = []
    for report in reports:
        findings.extend(report.findings)
    return core.Result(
        command="%s.all" % COMMAND,
        data={
            "target": given,
            "targets": targets,
            "reports": [report.to_dict() for report in reports],
        },
        diagnostics=findings + diagnostics,
    )


VERBS = {
    CHECK_DEPENDS_ON: _depends_on,
    CHECK_COUPLING: _coupling,
    CHECK_CATALOG: _catalog_check,
    "all": _all,
}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def register(subparsers) -> None:
    """Declare ``check``'s verbs on ``mc.py``'s subparser action."""
    parser = subparsers.add_parser(
        COMMAND,
        help="the mechanical spec rules",
        description=(
            "Run one of the mechanical checks: dangling depends-on paths, the three "
            "dependency rules, or catalog agreement with the spec "
            "tree. Any finding sets exit 1."
        ),
    )
    parser.set_defaults(group=COMMAND, verb=None)
    verbs = parser.add_subparsers(dest="verb", metavar="verb")

    depends = verbs.add_parser(
        CHECK_DEPENDS_ON,
        help="depends-on paths pointing at files that do not exist",
        description=(
            "Resolve every spec file's depends-on front matter and report the paths "
            "that resolve to nothing. COMMON-OVERVIEW.md is exempt from carrying the "
            "front matter at all."
        ),
    )
    depends.add_argument("target", help="a repo name or 'shared'")
    depends.set_defaults(verb=CHECK_DEPENDS_ON)

    coupling = verbs.add_parser(
        CHECK_COUPLING,
        help="IMPLEMENTATION-to-IMPLEMENTATION, cross-repo bypass, shared declaration",
        description=(
            "Report an IMPLEMENTATION depending on another module's IMPLEMENTATION, a "
            "depends-on crossing into another repo outside context/shared/spec/, and a "
            "shared interface declared in only one of its two required places."
        ),
    )
    coupling.add_argument("target", help="a repo name or 'shared'")
    coupling.set_defaults(verb=CHECK_COUPLING)

    catalog_parser = verbs.add_parser(
        CHECK_CATALOG,
        help="CATALOG.yaml file coverage, facet, and layer agreement with the spec tree",
        description=(
            "Compare CATALOG.yaml against the spec tree it describes: every spec file is "
            "declared exactly once and every declared path exists, each entry's facet "
            "matches the filename, and each module's layer matches the layers: block."
        ),
    )
    catalog_parser.add_argument("target", help="a repo name or 'shared'")
    catalog_parser.set_defaults(verb=CHECK_CATALOG)

    all_parser = verbs.add_parser(
        "all",
        help="every check above",
        description=(
            "Run the per-target checks for <target>, or for every target under "
            "context/ when none is given."
        ),
    )
    all_parser.add_argument("target", nargs="?", default=None, help="a repo name or 'shared'")
    all_parser.set_defaults(verb="all")


def run(args, ws: core.Workspace) -> core.Result:
    """Dispatch one ``check`` verb. Never raises; failures are diagnostics."""
    verb = getattr(args, "verb", None)
    command = "%s.%s" % (COMMAND, verb) if verb else COMMAND
    handler = VERBS.get(verb)
    if handler is None:
        return core.Result(
            command=command,
            diagnostics=[
                core.error(
                    core.E_USAGE,
                    "check requires a verb: %s" % (", ".join(sorted(VERBS)),),
                )
            ],
        )
    try:
        return handler(args, ws)
    except core.ToolError as exc:
        return core.Result(command=command, diagnostics=[exc.diagnostic])
