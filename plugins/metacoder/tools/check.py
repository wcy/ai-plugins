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

from tools import change, core, plan, req, spec, todo

COMMAND = "check"

#: The three codes `todo add` refuses on, re-exported under this module's names
#: so `check todo` reports a hand-edited violation with the very code the
#: emitter would have refused it with. One definition, two call sites: the
#: checker and the emitter cannot describe the same defect differently.
E_TODO_FIELD = todo.E_TODO_FIELD
E_TODO_ENUM = todo.E_TODO_ENUM
E_TODO_ORIGIN = todo.E_TODO_ORIGIN

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
E_CATALOG_DEPTH = "E_CATALOG_DEPTH"

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
CHECK_REQUIREMENTS = "requirements"
CHECK_CATALOG = "catalog"
CHECK_TODO = "todo"
CHECK_HANDOFF = "handoff"

#: `check all` runs these per-target checks, in this order.
TARGET_CHECKS = (CHECK_DEPENDS_ON, CHECK_COUPLING, CHECK_REQUIREMENTS, CHECK_CATALOG)

#: `check all` then runs these workspace-wide checks, in this order -- the same
#: order TOOLS-INTERFACE.md's table lists them in. Neither takes a target: one
#: list at one tier, and one stage chain across the whole workspace.
WORKSPACE_CHECKS = (CHECK_TODO, CHECK_HANDOFF)

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
# check requirements
# ---------------------------------------------------------------------------


def _canonical_req_id(number: Any) -> str:
    """The bare canonical form of a requirement number -- the display form of
    the identity ``parse_req_id`` resolves every written form onto."""
    return req.ID_FORMAT % int(number)


@dataclass
class RequirementCoverage:
    """One target's catalog references measured against its requirements tier.

    Every dict and set here is keyed on the *canonical* bare id -- the number
    alone, per ``parse_req_id`` -- never on the raw written text. That is what
    lets a mnemonic-bearing reference and a bare entry heading resolve to the
    same requirement instead of comparing as different strings.
    """

    target: str
    catalog_path: str
    requirements_path: str
    #: canonical id -> the modules referencing it, in catalog order.
    referenced: Dict[str, List[str]] = field(default_factory=dict)
    #: canonical id -> the line it is declared on in the tier document.
    present: Dict[str, Optional[int]] = field(default_factory=dict)
    #: canonical id -> that entry's own mnemonic (or ``None``).
    present_mnemonic: Dict[str, Optional[str]] = field(default_factory=dict)
    #: `project:REQ-<NNN>` references, measured against the project tier.
    project_referenced: Dict[str, List[str]] = field(default_factory=dict)
    project_present: Set[str] = field(default_factory=set)
    project_present_mnemonic: Dict[str, Optional[str]] = field(default_factory=dict)
    #: `(canonical id, reference mnemonic, module, tier)` for every reference
    #: that carried a mnemonic -- checked against `present_mnemonic` once the
    #: whole tier has been read.
    mnemonic_refs: List[Tuple[str, str, str, str]] = field(default_factory=list)
    #: `(raw, module)` for every catalog reference that does not parse at all.
    unparsed: List[Tuple[str, str]] = field(default_factory=list)

    @property
    def dangling(self) -> List[str]:
        """Referenced ids that no requirements tier declares."""
        missing = [item for item in sorted(self.referenced) if item not in self.present]
        missing.extend(
            "%s:%s" % (PROJECT_TIER, item)
            for item in sorted(self.project_referenced)
            if item not in self.project_present
        )
        return missing

    @property
    def uncovered(self) -> List[str]:
        """Declared ids that no module references."""
        return [item for item in sorted(self.present) if item not in self.referenced]

    @property
    def stale_mnemonics(self) -> List[Tuple[str, str, str]]:
        """`(canonical id, reference mnemonic, module)` where the reference's
        mnemonic disagrees with the entry's own heading. A reference to an
        absent entry is reported as dangling, never as stale, and a
        reference agreeing with the heading is not reported at all."""
        stale: List[Tuple[str, str, str]] = []
        for canonical, ref_mnemonic, module, tier in self.mnemonic_refs:
            present = self.project_present if tier == PROJECT_TIER else self.present
            if canonical not in present:
                continue
            heading = (
                self.project_present_mnemonic if tier == PROJECT_TIER else self.present_mnemonic
            ).get(canonical)
            if heading == ref_mnemonic:
                continue
            stale.append((canonical, ref_mnemonic, module))
        return stale


def requirement_coverage(ws: core.Workspace, target: str) -> RequirementCoverage:
    """Read one target's catalog references and its requirements tier."""
    catalog, catalog_rel, exists = _catalog(ws, target)
    document = req.read_requirements(ws, target)
    coverage = RequirementCoverage(
        target=target, catalog_path=catalog_rel, requirements_path=document.path
    )
    for entry in document.entries:
        canonical = _canonical_req_id(entry.number)
        coverage.present.setdefault(canonical, entry.line)
        coverage.present_mnemonic.setdefault(canonical, entry.mnemonic)
    if exists and catalog is not None:
        for module in catalog.module_names("modules"):
            listed = catalog.field("modules", module, "requirements")
            if not isinstance(listed, list):
                continue
            for raw in listed:
                try:
                    parsed = req.parse_req_id(raw)
                except core.ToolError:
                    coverage.unparsed.append((str(raw), module))
                    continue
                canonical = _canonical_req_id(parsed.number)
                table = (
                    coverage.project_referenced
                    if parsed.tier == PROJECT_TIER
                    else coverage.referenced
                )
                table.setdefault(canonical, []).append(module)
                if parsed.mnemonic is not None:
                    coverage.mnemonic_refs.append((canonical, parsed.mnemonic, module, parsed.tier))
    if coverage.project_referenced:
        for entry in req.read_requirements(ws, PROJECT_TIER).entries:
            canonical = _canonical_req_id(entry.number)
            coverage.project_present.add(canonical)
            coverage.project_present_mnemonic.setdefault(canonical, entry.mnemonic)
    return coverage


def requirements_findings(ws: core.Workspace, target: str) -> List[core.Diagnostic]:
    """Catalog references to absent ids, ids no module references, a
    reference whose mnemonic disagrees with its entry's heading, and a
    reference that does not parse as an id at all."""
    coverage = requirement_coverage(ws, target)
    findings: List[core.Diagnostic] = []
    for identifier in coverage.dangling:
        findings.append(
            core.error(
                core.E_MISSING_REQUIREMENT,
                "catalog references %s, which %s declares no entry for"
                % (identifier, coverage.requirements_path),
                file=coverage.catalog_path,
            )
        )
    for identifier in coverage.uncovered:
        findings.append(
            core.error(
                core.E_ORPHAN_REQUIREMENT,
                "%s is referenced by no module in %s" % (identifier, coverage.catalog_path),
                file=coverage.requirements_path,
                line=coverage.present.get(identifier),
            )
        )
    for raw, module in coverage.unparsed:
        findings.append(
            core.error(
                req.E_BAD_REQ_REF,
                "module %r's catalog reference %r does not parse as a requirement id"
                % (module, raw),
                file=coverage.catalog_path,
            )
        )
    for canonical, ref_mnemonic, module in coverage.stale_mnemonics:
        findings.append(
            core.warning(
                W_STALE_REQ_MNEMONIC,
                "module %r references %s-%s, which disagrees with %s's own heading "
                "mnemonic; it still resolves to a live requirement"
                % (module, canonical, ref_mnemonic, canonical),
                file=coverage.catalog_path,
            )
        )
    return findings


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


def _catalog_depth(
    catalog: spec.ExistingCatalog, key: str, relative: str
) -> List[core.Diagnostic]:
    """A declared ``depth`` the module's own files do not support.

    Only a *declaration* is measured. An absent ``depth`` resolves to ``full``
    everywhere else in the tool, but absence is not an assertion: reading it as
    one would fire on every catalog written before spec depth existed, and would
    convert an oversight into a claim -- the same reason the field is preserved
    rather than derived.
    """
    findings: List[core.Diagnostic] = []
    for module in catalog.module_names(key):
        declared = catalog.field(key, module, "depth")
        if declared not in spec.DEPTH_LEVELS:
            continue
        entries = catalog.field(key, module, "files")
        facets = {
            entry.get("facet")
            for entry in (entries if isinstance(entries, list) else [])
            if isinstance(entry, dict)
        }
        required = (
            spec.CONTRACT_FACETS
            if declared == spec.DEPTH_CONTRACT
            else spec.CONTRACT_FACETS + spec.DEEPENING_FACETS
        )
        missing = [facet for facet in required if facet not in facets]
        if not missing:
            continue
        findings.append(
            core.error(
                E_CATALOG_DEPTH,
                "module %r declares depth %r but has no %s facet"
                % (module, declared, "/".join(missing)),
                file=relative,
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
    findings.extend(_catalog_depth(catalog, key, relative))
    return findings


# ---------------------------------------------------------------------------
# check todo -- where the deferral list's guarantee actually lives
#
# `todo-frontmatter.schema.json` is shape-only by necessity: it validates two
# HTML-comment lines and can say nothing about the `## <Title>` bodies beneath
# them. Everything `todo add` refuses before it writes is therefore re-checked
# here, against the same field table and the same enums read from
# `STANDARD-TODO.md` and with the same diagnostic codes, so a hand-edited list
# is held to exactly what the emitter would have allowed.
#
# One rule is this check's alone. A `Context` too thin to start from is measured
# **structurally** -- as a Context naming no file, artifact or identifier the
# reader could open -- rather than by length, and the rule is deliberately weak:
# it catches the empty gesture, not the plausible-but-useless paragraph, and no
# checker can catch the second.
# ---------------------------------------------------------------------------

#: A `Context` that names nothing a reader could open.
E_TODO_CONTEXT = "E_TODO_CONTEXT"

#: A backticked artifact: a command, a verb, a schema kind, a symbol.
_CONTEXT_ARTIFACT_RE = re.compile(r"`[^`\s][^`]*`")

#: A filename or a path: a stem of two or more characters, a dot, and a short
#: extension. The two-character stem is what keeps `e.g.` and `i.e.` out, and
#: requiring letters after the dot is what keeps a sentence-ending `etc.` out.
_CONTEXT_FILE_RE = re.compile(r"\b[A-Za-z0-9_/-]*[A-Za-z0-9_-]{2}\.[A-Za-z]{1,6}\b")

#: An identifier the workspace uses: a requirement or change number, a plan id,
#: or a module TAG.
_CONTEXT_IDENT_RE = re.compile(
    r"\b(?:REQ|CHANGE|PROJECT-CHANGE|REQ-CHANGE)-[0-9]{3,4}\b"
    r"|\b[0-9]{3}-[a-z0-9]+(?:-[a-z0-9]+)+\b"
    r"|\b[A-Z][A-Z0-9]{2,}\b"
)

_CONTEXT_RULES = (_CONTEXT_ARTIFACT_RE, _CONTEXT_FILE_RE, _CONTEXT_IDENT_RE)

#: The entry field naming the skill an item is routed to -- the "with the skill
#: it is routed to" half of the deferral finding. `STANDARD-TODO.md` owns the
#: name and `todo list --run` filters on the same field, so it is read out of
#: `todo`'s own filter list rather than spelled again here. The suite pins both
#: the value and its membership of the standard's field table, so a rename there
#: fails a test instead of quietly routing every entry to "no skill".
TODO_RUN_FIELD = todo.LIST_FILTER_FIELDS[0]

#: The one `Run` value that is not a skill, read out of the standard's own enum
#: rather than spelled here -- it is the member carrying no leading slash, which
#: is the grammar `STANDARD-TODO.md` gives it. Deriving it keeps this partition
#: from disagreeing with the standard the way a literal would.
TODO_HUMAN_RUN = next(
    value
    for spec in todo.field_specs()
    if spec.name == TODO_RUN_FIELD
    for value in (spec.enum or ())
    if not value.startswith("/")
)


def context_names_something(text: str) -> bool:
    """Whether a ``Context`` names a file, an artifact or an identifier."""
    return any(rule.search(text) is not None for rule in _CONTEXT_RULES)


def read_todo(
    ws: core.Workspace,
) -> Tuple[List["todo.TodoEntry"], str, List[core.Diagnostic]]:
    """``(entries, path, problems)`` for ``context/project/TODO.md``.

    The one reader of the list, shared by ``check todo`` and by the deferrals
    ``check handoff`` folds in -- two readers of one file are two things to keep
    in step, and the two checks must not be able to disagree about what the list
    holds. Entries come back in document order. A workspace with no list yields
    no entries and no problem: nothing has been deferred, which is not a defect.
    """
    path, escape = ws.resolve_path(*todo.TODO_PARTS)
    if escape is not None:
        return [], todo.TODO_REL, [escape]
    relative = ws.rel(path)
    if not path.is_file():
        return [], relative, []
    try:
        text = core.read_text(path, relative)
    except core.ToolError as exc:
        return [], relative, [exc.diagnostic]
    return list(todo.parse_entries(text)), relative, []


def todo_findings(ws: core.Workspace) -> List[core.Diagnostic]:
    """Every violation ``context/project/TODO.md`` carries.

    A workspace with no list is clean rather than incomplete: nothing has been
    deferred, which is not a defect. Findings are emitted entry by entry in
    document order and, within an entry, in the standard's own field order, so
    the report is stable between runs.
    """
    entries, relative, problems = read_todo(ws)
    if problems:
        return problems

    specs = todo.field_specs()
    findings: List[core.Diagnostic] = []
    for entry in entries:
        for item in specs:
            findings.extend(_todo_field_findings(ws, entry, item, relative))
    return findings


def _todo_field_findings(
    ws: core.Workspace, entry: "todo.TodoEntry", item: "todo.FieldSpec", relative: str
) -> List[core.Diagnostic]:
    """One entry's one field: presence, enum, then the field's own rule."""
    value = entry.get(item.name).strip()
    where = "entry %r" % (entry.title,)
    if not value:
        return [
            core.error(
                E_TODO_FIELD,
                "%s is missing the required %s field" % (where, item.name),
                file=relative,
                line=entry.line,
            )
        ]
    if item.enum is not None and value not in item.enum:
        return [
            core.error(
                E_TODO_ENUM,
                "%s has %s %r, which is outside its enum: %s"
                % (where, item.name, value, ", ".join(item.enum)),
                file=relative,
                line=entry.line,
            )
        ]
    if item.name == todo.ORIGIN_FIELD and todo.resolve_origin(ws, value) is None:
        return [
            core.error(
                E_TODO_ORIGIN,
                "%s has Origin %r, which names no change document or plan" % (where, value),
                file=relative,
                line=entry.line,
            )
        ]
    if item.name == todo.CONTEXT_FIELD and not context_names_something(value):
        return [
            core.error(
                E_TODO_CONTEXT,
                "%s has a Context naming no file, artifact or identifier, so it cannot be "
                "started from a cleared context" % (where,),
                file=relative,
                line=entry.line,
            )
        ]
    return []


# ---------------------------------------------------------------------------
# check handoff -- the single stage walk
# ---------------------------------------------------------------------------

CHANGES_DIRNAME = "changes"
PLANS_DIRNAME = "plans"
OUT_DIRNAME = "out"
REQUIREMENTS_DIRNAME = req.REQUIREMENTS_DIR
REQUIREMENTS_FILENAME = req.REQUIREMENTS_FILENAME
CONFORMANCE_REPORT_STEM = "mverify-report"

CONFORMANCE_STATUSES = ("clean", "drift", "not-run")


@dataclass
class StageWalk:
    """Everything one traversal of the stage chain produced.

    ``check handoff`` reports :attr:`findings`; ``tools/status.py`` renders the
    ``StatusReport`` from the four stage lists. There is deliberately no second
    walk -- the two commands differ only in what they present.
    """

    uncovered: List[str] = field(default_factory=list)
    dangling: List[str] = field(default_factory=list)
    open_req_changes: List[str] = field(default_factory=list)
    pending_changes: List[Dict[str, Any]] = field(default_factory=list)
    unindexed_changes: List[Dict[str, Any]] = field(default_factory=list)
    unplanned_indexes: List[str] = field(default_factory=list)
    unfinished_plans: List[Dict[str, Any]] = field(default_factory=list)
    slices: List[Dict[str, Any]] = field(default_factory=list)
    conformance: List[Dict[str, Any]] = field(default_factory=list)
    findings: List[HandoffFinding] = field(default_factory=list)
    #: The open deferrals, one warning each. Deliberately **not** in
    #: :attr:`findings`: that list is the ``HandoffFinding[]`` both
    #: ``TOOLS-DATAMODEL.md`` §"Checks and handoff" and ``StatusReport.handoff``
    #: publish, and a deferral is not a handoff finding -- it has no stage pair
    #: and neither ``stranded`` nor ``incomplete`` describes it. Keeping them
    #: apart is what leaves the stage walk itself, and everything rendered from
    #: it, exactly as it was.
    deferrals: List[core.Diagnostic] = field(default_factory=list)
    #: The open entries routed to `human`, one warning each. Held apart from
    #: :attr:`deferrals` for the reason `W_TODO_HUMAN` gives: that list is a
    #: queue that drains, and these never do.
    human_deferrals: List[core.Diagnostic] = field(default_factory=list)
    diagnostics: List[core.Diagnostic] = field(default_factory=list)


def walk_stages(ws: core.Workspace) -> StageWalk:
    """Walk mreq -> mspec -> mplan -> mexecute -> mverify -> mfix once.

    :func:`_deferrals_stage` reads one more file at the end and traverses
    nothing: the chain is still walked exactly once.
    """
    walk = StageWalk()
    _requirements_stage(ws, walk)
    indexes = _changes_stage(ws, walk)
    _plans_stage(ws, walk, indexes)
    _ownership(ws, walk)
    _deferrals_stage(ws, walk)
    return walk


def handoff_findings(ws: core.Workspace) -> List[HandoffFinding]:
    """``HandoffFinding[]`` -- stranded and incomplete artifacts, plus
    ownership violations.

    The stage chain only. The open deferrals ``check handoff`` reports beside
    these are :attr:`StageWalk.deferrals`, and are not ``HandoffFinding``.
    """
    return walk_stages(ws).findings


# -- mreq -> mspec -----------------------------------------------------------


def _requirements_stage(ws: core.Workspace, walk: StageWalk) -> None:
    """A requirement no module covers, and a `REQ-CHANGE` no spec change has
    answered, have both not reached ``mspec``."""
    uncovered: List[str] = []
    dangling: List[str] = []
    for target in ws.targets:
        coverage = requirement_coverage(ws, target)
        dangling.extend(coverage.dangling)
        for identifier in coverage.uncovered:
            uncovered.append(identifier)
            walk.findings.append(
                handoff(
                    core.E_HANDOFF,
                    "%s is declared but no module in %s covers it"
                    % (identifier, coverage.catalog_path),
                    STAGE_MREQ,
                    STAGE_MSPEC,
                    identifier,
                    STATE_STRANDED,
                    file=coverage.requirements_path,
                    line=coverage.present.get(identifier),
                )
            )
    walk.uncovered = sorted(set(uncovered))
    walk.dangling = sorted(set(dangling))
    _open_req_changes_stage(ws, walk)


def _open_req_changes_stage(ws: core.Workspace, walk: StageWalk) -> None:
    """Every `REQ-CHANGE` still `open` with no spec change answering it.

    Extends the same mreq->mspec pass rather than adding a second traversal:
    `walk_stages` calls only `_requirements_stage`, and this is a step of it.
    A record carrying `spec-change: not-required` is exempt -- it is a
    revision with no spec delta, complete as written, whatever its `status`.
    Reported at `warning` severity: a captured requirement awaiting its spec
    change is a normal in-progress state, not a defect.
    """
    open_records: List[str] = []
    for tier in list(ws.targets) + [PROJECT_TIER]:
        for ref in req._scan_req_changes(ws, tier, walk.diagnostics):
            if ref.status != "open" or ref.spec_change == req.SPEC_CHANGE_NOT_REQUIRED:
                continue
            open_records.append(ref.path)
            walk.findings.append(
                handoff(
                    core.E_HANDOFF,
                    "%s is open and no spec change has answered it yet" % (ref.path,),
                    STAGE_MREQ,
                    STAGE_MSPEC,
                    ref.path,
                    STATE_STRANDED,
                    file=ref.path,
                    severity=core.SEVERITY_WARNING,
                )
            )
    walk.open_req_changes = sorted(set(open_records))


# -- mspec -> mplan ----------------------------------------------------------


def _changes_stage(ws: core.Workspace, walk: StageWalk) -> List[change.ChangeRef]:
    """Repo changes and project indexes, and what has not been consumed."""
    repo_changes: List[change.ChangeRef] = []
    for target in ws.targets:
        parts = (CONTEXT_DIRNAME, target, CHANGES_DIRNAME)
        repo_changes.extend(
            change._scan(
                ws.safe_path(*parts),
                parts,
                change.REPO_CHANGE_RE,
                change.REPO_PREFIX,
                "repo",
                target,
                walk.diagnostics,
            )
        )
    index_parts = (CONTEXT_DIRNAME, PROJECT_TIER, CHANGES_DIRNAME)
    indexes = change._scan(
        ws.safe_path(*index_parts),
        index_parts,
        change.INDEX_CHANGE_RE,
        change.INDEX_PREFIX,
        PROJECT_TIER,
        None,
        walk.diagnostics,
    )

    referenced: Set[Tuple[str, str]] = set()
    for index in indexes:
        listed = _index_references(ws, index, walk)
        referenced.update(listed)
        if not listed:
            walk.findings.append(
                handoff(
                    core.E_HANDOFF,
                    "the index names no repo change file under \"%s\"; there is nothing "
                    "for a plan to scope" % (change.REPO_CHANGE_SECTION,),
                    STAGE_MSPEC,
                    STAGE_MPLAN,
                    index.path,
                    STATE_INCOMPLETE,
                    file=index.path,
                )
            )

    for ref in repo_changes:
        if ref.status == change.INITIAL_STATUS:
            walk.pending_changes.append(ref.to_dict())
    for ref in indexes:
        if ref.status == change.INITIAL_STATUS:
            walk.pending_changes.append(ref.to_dict())

    for ref in repo_changes:
        if ref.baseline:
            continue  # a baseline record documents what exists; it is never planned
        if ref.plan_not_required:
            continue  # `plan: not-required` -- no code phase for an index to reach
        if (ref.repo, ref.path.rsplit("/", 1)[-1]) in referenced:
            continue
        walk.unindexed_changes.append(ref.to_dict())
        walk.findings.append(
            handoff(
                core.E_HANDOFF,
                "the change is %s but no project index references it, so no plan can "
                "reach it" % (ref.status or "unreadable",),
                STAGE_MSPEC,
                STAGE_MPLAN,
                ref.path,
                STATE_STRANDED,
                file=ref.path,
            )
        )
    return indexes


def _index_references(
    ws: core.Workspace, index: change.ChangeRef, walk: StageWalk
) -> Set[Tuple[str, str]]:
    """``(repo, filename)`` for every repo change an index's table names."""
    path = ws.safe_path(index.path)
    try:
        text = core.read_text(path, index.path)
    except core.ToolError as exc:
        walk.diagnostics.append(
            core.warning(
                exc.diagnostic.code,
                "%s; its repo change references were not read" % (exc.diagnostic.message,),
                file=index.path,
            )
        )
        return set()
    body = change.section_body(text, change.REPO_CHANGE_SECTION)
    return set(change.REPO_CHANGE_REF_RE.findall(body))


# -- mplan -> mexecute -> mverify -> mfix ------------------------------------


def _plans_stage(
    ws: core.Workspace, walk: StageWalk, indexes: Sequence[change.ChangeRef]
) -> None:
    """Indexes without a plan, plans without a run, and drift without a fix."""
    plan_dirs = change._plan_dirs(ws)
    ledger = plan._load_ledger(ws)
    highest_index = max([int(ref.number) for ref in indexes] or [0])

    for ref in indexes:
        if change._plan_dir_for(plan_dirs, ref.number, ref.slug) is not None:
            continue
        if ref.plan_not_required:
            continue  # `plan: not-required` -- no code phase to plan
        walk.unplanned_indexes.append(ref.path)
        walk.findings.append(
            handoff(
                core.E_HANDOFF,
                "the index has no plan directory under %s" % (plan.PLANS_REL,),
                STAGE_MSPEC,
                STAGE_MPLAN,
                ref.path,
                STATE_STRANDED,
                file=ref.path,
            )
        )

    for plan_id in plan_dirs:
        graph, state = _plan_documents(ws, plan_id, walk)
        resolution = _plan_resolution(plan_id, graph, state, ledger)
        if resolution["pending_stories"] and resolution["run"] == 0:
            walk.findings.append(
                handoff(
                    core.E_HANDOFF,
                    "the plan holds %d unfinished story/stories and state.yaml records no "
                    "run" % (len(resolution["pending_stories"]),),
                    STAGE_MPLAN,
                    STAGE_MEXECUTE,
                    resolution["plan_dir"],
                    STATE_STRANDED,
                    file="%s/%s" % (resolution["plan_dir"], plan.STATE_FILE),
                )
            )
        if resolution["pending_stories"] or resolution["status"] in plan.UNFINISHED_STATUSES:
            walk.unfinished_plans.append(resolution)
        _slice_stage(walk, plan_id, graph, state, ledger)
        _conformance_stage(ws, walk, plan_id, graph, state, ledger, highest_index)
        _sweep_stage(walk, resolution, state)


def _sweep_stage(walk: StageWalk, resolution: Dict[str, Any], state: Any) -> None:
    """Report a finished plan that never declared what it left behind.

    **The check is that the declaration is present, never that it is right.**
    Nothing can confirm a run enumerated everything it learned, and a check
    claiming to would be the false confirmation `REQ-028` describes -- so a
    block recording ``filed: 0`` clears this stage exactly as one recording five
    does. That is the point rather than a weakness: the absence becomes a claim
    somebody made instead of a silence nobody notices.

    A plan whose ``status`` is not yet terminal is skipped rather than reported
    clean: the declaration is owed at the *end* of delivery, and reporting an
    in-flight plan would train the reader to ignore the finding.

    **A state file below the sweep version is skipped too**, and for a stronger
    reason: it was written before the block existed, so its silence is not a
    missing declaration but a plan from before declarations. The obligation
    cannot reach backwards -- no run can return to a finished plan and declare
    what it left behind -- so reporting one would be a finding nobody can ever
    clear, and a check that can only be satisfied by plans yet to be written is
    one every reader learns to skip. This is the same version-as-discriminator
    rule ``plan.py`` states for ``slices``: a version-1/2 graph is a plan written
    before slices existed, and nothing needs migrating.
    """
    if resolution["status"] not in TERMINAL_PLAN_STATUSES:
        return
    if not isinstance(state, dict):
        return
    if state.get("version") != plan.PLAN_STATE_SWEEP_VERSION:
        return
    if isinstance(state.get("sweep"), dict):
        return
    walk.findings.append(
        handoff(
            W_HANDOFF_SWEEP,
            "the plan finished without a closing sweep declaration; a run that deferred "
            "nothing further records that as `state sweep --filed 0` rather than silence",
            STAGE_MEXECUTE,
            STAGE_MVERIFY,
            resolution["plan_dir"],
            STATE_INCOMPLETE,
            file="%s/%s" % (resolution["plan_dir"], plan.STATE_FILE),
            severity="warning",
        )
    )


def _plan_documents(
    ws: core.Workspace, plan_id: str, walk: StageWalk
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """A plan's graph and state, reporting a missing or malformed one."""
    plan_dir_rel = plan._plan_dir_rel(plan_id)
    documents: List[Dict[str, Any]] = []
    for filename in (plan.PLAN_FILE, plan.STATE_FILE):
        relative = "%s/%s" % (plan_dir_rel, filename)
        path = ws.safe_path(CONTEXT_DIRNAME, PROJECT_TIER, PLANS_DIRNAME, plan_id, filename)
        loaded: Any = None
        if not path.is_file():
            walk.findings.append(
                handoff(
                    core.E_HANDOFF,
                    "the plan directory holds no %s" % (filename,),
                    STAGE_MPLAN,
                    STAGE_MEXECUTE,
                    relative,
                    STATE_INCOMPLETE,
                    file=relative,
                )
            )
        else:
            try:
                loaded = core.load_yaml(path, relative)
            except core.ToolError as exc:
                walk.diagnostics.append(exc.diagnostic)
            if not isinstance(loaded, dict):
                walk.findings.append(
                    handoff(
                        core.E_HANDOFF,
                        "%s is not a mapping" % (filename,),
                        STAGE_MPLAN,
                        STAGE_MEXECUTE,
                        relative,
                        STATE_INCOMPLETE,
                        file=relative,
                    )
                )
        documents.append(loaded if isinstance(loaded, dict) else {})
    return documents[0], documents[1]


def _plan_resolution(
    plan_id: str, graph: Dict[str, Any], state: Dict[str, Any], ledger: Dict[str, Any]
) -> Dict[str, Any]:
    """A ``PlanResolution``, assembled from ``tools/plan.py``'s readers."""
    resume_wave, pending_stories = plan._resume(graph, plan._story_statuses(state))
    entries, _synthesized = plan.slice_entries(graph, state)
    return {
        "plan_id": plan_id,
        "plan_dir": plan._plan_dir_rel(plan_id),
        "status": plan._plan_status(state, ledger, plan_id),
        "run": plan._run_counter(state),
        "resume_wave": resume_wave,
        "resume_slice": plan._resume_slice(entries),
        "pending_stories": pending_stories,
    }


def _slice_stage(
    walk: StageWalk,
    plan_id: str,
    graph: Dict[str, Any],
    state: Dict[str, Any],
    ledger: Dict[str, Any],
) -> None:
    """How far through its slices each plan got.

    The counters come from the **ledger** -- that is what they are there for, and
    reading them saves a second pass over each plan's state file. The current
    slice comes from the graph and state this walk has already loaded, so this
    stage still adds no traversal of its own.

    A pre-slice plan reports ``null`` counters rather than zero: no progress made
    and no slices to make progress through are different facts, and a zero would
    present the second as the first.
    """
    entry = plan._ledger_plans(ledger).get(plan_id)
    entry = entry if isinstance(entry, dict) else {}
    total = entry.get("slices_total")
    applied = entry.get("slices_applied")
    entries, synthesized = plan.slice_entries(graph, state)
    if synthesized and not isinstance(total, int):
        walk.slices.append(
            {"plan_id": plan_id, "applied": None, "total": None, "current": None}
        )
        return
    if not isinstance(total, int) or isinstance(total, bool):
        total = len(entries)
    if not isinstance(applied, int) or isinstance(applied, bool):
        applied = len(
            [item for item in entries if item.get("status") == plan.SLICE_APPLIED]
        )
    current = next(
        (item for item in entries if item.get("status") != plan.SLICE_APPLIED), None
    )
    walk.slices.append(
        {"plan_id": plan_id, "applied": applied, "total": total, "current": current}
    )


def _conformance_stage(
    ws: core.Workspace,
    walk: StageWalk,
    plan_id: str,
    graph: Dict[str, Any],
    state: Dict[str, Any],
    ledger: Dict[str, Any],
    highest_index: int,
) -> None:
    """The sweep's verdict per plan, and drift nothing followed up on.

    **The stage scores ``findings - deferred``, not ``findings``.** A finding
    ``mfix`` legitimately accepted as debt -- no contract covers this surface
    yet, the fix needs architecture that does not exist -- would otherwise hold
    the check at ``error`` with nothing anyone could do about it, and a gate that
    cannot be satisfied is one everybody learns to route around. The two numbers
    are still reported separately, so accepted debt stays visible as debt rather
    than disappearing.
    """
    status, findings, deferred = _conformance_verdict(ws, plan_id, state, walk)
    walk.conformance.append(
        {
            "plan_id": plan_id,
            "status": status,
            "findings": findings,
            "deferred": deferred,
        }
    )
    if deferred > findings:
        # Not scored to a negative outstanding count: more deferrals than
        # findings is a block nobody can act on, so it is reported as its own
        # defect rather than read as a cleaner plan than a clean one.
        walk.findings.append(
            handoff(
                E_CONFORMANCE_DEFERRED,
                "the conformance block defers %d finding(s) but records only %d"
                % (deferred, findings),
                STAGE_MVERIFY,
                STAGE_MFIX,
                plan._plan_dir_rel(plan_id),
                STATE_INCOMPLETE,
                file="%s/%s" % (plan._plan_dir_rel(plan_id), plan.STATE_FILE),
            )
        )
        return
    if findings - deferred <= 0:
        return
    findings = findings - deferred
    # A drift report is consumed by writing the next change. Repo change numbers
    # and index numbers are independent sequences, so the successor is looked for
    # where it would be recorded: a project index numbered above this plan's.
    number = _plan_change_number(plan_id, graph, ledger)
    if highest_index > number:
        return
    walk.findings.append(
        handoff(
            core.E_HANDOFF,
            "the conformance sweep reported %d finding(s) and no change document "
            "follows it" % (findings,),
            STAGE_MVERIFY,
            STAGE_MFIX,
            plan._plan_dir_rel(plan_id),
            STATE_STRANDED,
            file="%s/%s" % (plan._plan_dir_rel(plan_id), plan.STATE_FILE),
        )
    )


def _count(value: Any) -> int:
    """A non-negative integer field, or 0 -- ``deferred`` absent means 0."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value


def _conformance_verdict(
    ws: core.Workspace, plan_id: str, state: Dict[str, Any], walk: StageWalk
) -> Tuple[str, int, int]:
    """``(status, findings, deferred)`` from the block, else from the report."""
    block = state.get("conformance")
    if isinstance(block, dict):
        status = block.get("status")
        return (
            status if status in CONFORMANCE_STATUSES else "not-run",
            _count(block.get("findings")),
            _count(block.get("deferred")),
        )
    relative = "%s/%s/%s.json" % (
        "/".join((CONTEXT_DIRNAME, PROJECT_TIER, OUT_DIRNAME)),
        plan_id,
        CONFORMANCE_REPORT_STEM,
    )
    path, escape = ws.resolve_path(
        CONTEXT_DIRNAME, PROJECT_TIER, OUT_DIRNAME, plan_id, "%s.json" % CONFORMANCE_REPORT_STEM
    )
    if escape is not None or not path.is_file():
        return "not-run", 0, 0
    try:
        report = core.load_json(path, relative)
    except core.ToolError as exc:
        walk.diagnostics.append(exc.diagnostic)
        return "not-run", 0, 0
    listed = report.get("findings") if isinstance(report, dict) else None
    count = len(listed) if isinstance(listed, list) else 0
    # A report on disk carries no deferral count -- deferral is a decision
    # `mfix` records into state.yaml, not something a sweep can report about
    # itself -- so the fallback path always scores the raw finding count.
    return ("clean" if count == 0 else "drift"), count, 0


def _plan_change_number(plan_id: str, graph: Dict[str, Any], ledger: Dict[str, Any]) -> int:
    """The index number a plan was scoped from, or its own leading number."""
    entry = plan._ledger_plans(ledger).get(plan_id)
    candidates = [
        graph.get("project_change"),
        entry.get("project_change") if isinstance(entry, dict) else None,
        plan_id.split("-", 1)[0],
    ]
    for candidate in candidates:
        try:
            return int(str(candidate))
        except (TypeError, ValueError):
            continue
    return 0


# -- ownership ---------------------------------------------------------------

#: Which stage owns what is written where. A plan directory is co-owned:
#: `mplan` seeds it and `mexecute` rewrites `state.yaml` in place.
def _directory_owners(parts: Sequence[str]) -> Optional[Tuple[str, ...]]:
    if len(parts) < 3 or parts[0] != CONTEXT_DIRNAME:
        return None
    tier, sub = parts[1], parts[2]
    if tier == PROJECT_TIER:
        if sub == PLANS_DIRNAME:
            return (STAGE_MPLAN, STAGE_MEXECUTE)
        if sub == OUT_DIRNAME:
            return (STAGE_MVERIFY,)
        if sub == CHANGES_DIRNAME:
            return (STAGE_MSPEC,)
        if sub == REQUIREMENTS_DIRNAME:
            return (STAGE_MREQ,)  # authoring writes; a REQ-CHANGE close is tool-owned, see below
        return (STAGE_MPLAN, STAGE_MEXECUTE)  # the ledger's own directory
    if sub == SPEC_DIR:
        return (STAGE_MSPEC,)
    if sub == CHANGES_DIRNAME:
        return (STAGE_MSPEC,)
    if sub == REQUIREMENTS_DIRNAME:
        return (STAGE_MREQ,)  # authoring writes; a REQ-CHANGE close is tool-owned, see below
    return None


#: A path written only through a named `mc.py` verb -- a third ownership
#: class alongside single-stage and co-owned paths. `req change-close`'s
#: write is the one instance: the ownership pass skips it rather than
#: charging it to whichever skill invoked the verb. `mreq` still owns every
#: *authoring* write under `context/<tier>/requirements/`.
TOOL_OWNED: Tuple[str, ...] = ()


def _writing_stages(name: str) -> Optional[Tuple[str, ...]]:
    """The stage that writes a file of this kind, when the kind is known.

    An unrecognised filename yields ``None`` and is never reported: this rule
    exists to catch a stage writing into another's tree, not to police every
    file a workspace happens to hold. `REQ-CHANGE-*.md` is recognised but
    yields :data:`TOOL_OWNED` rather than a stage -- see its docstring.
    """
    if name == REQUIREMENTS_FILENAME:
        return (STAGE_MREQ,)
    if req.REQ_CHANGE_RE.match(name):
        return TOOL_OWNED
    if name == plan.STATE_FILE:
        return (STAGE_MPLAN, STAGE_MEXECUTE)
    if name == plan.PLAN_FILE or (
        name.startswith(plan.STORY_FILE_PREFIX) and name.endswith(plan.STORY_FILE_SUFFIX)
    ):
        return (STAGE_MPLAN,)
    if name.startswith("%s." % CONFORMANCE_REPORT_STEM):
        return (STAGE_MVERIFY,)
    if change.REPO_CHANGE_RE.match(name) or change.INDEX_CHANGE_RE.match(name):
        return (STAGE_MSPEC,)
    if name == spec.CATALOG_FILENAME or spec.facet_for(name) is not None:
        return (STAGE_MSPEC,)
    return None


def _ownership(ws: core.Workspace, walk: StageWalk) -> None:
    """Report a file written by a stage that does not own where it sits."""
    root = ws.safe_path(CONTEXT_DIRNAME)
    if not root.is_dir():
        return
    for path in _walk_files(root):
        relative = ws.rel(path)
        parts = _path_parts(relative)
        owners = _directory_owners(parts)
        writers = _writing_stages(path.name)
        if writers == TOOL_OWNED and req.REQ_CHANGE_RE.match(path.name):
            continue  # tool-owned: charged to no stage, per TOOL_OWNED's docstring
        if not owners or not writers or set(owners) & set(writers):
            continue  # `writers is None` (unrecognised) also lands here, unchanged
        earlier, later = stage_pair(writers[0], owners[0])
        walk.findings.append(
            handoff(
                E_OWNERSHIP,
                "%s is written by %s but sits in %s's tree"
                % (path.name, "/".join(writers), "/".join(owners)),
                earlier,
                later,
                relative,
                STATE_INCOMPLETE,
                file=relative,
            )
        )


def _walk_files(directory: Path) -> List[Path]:
    """Every file under ``directory``, depth-first in sorted order."""
    found: List[Path] = []
    for entry in sorted(directory.iterdir(), key=lambda item: item.name):
        if entry.is_symlink():
            continue  # a symlink is not a file this workspace wrote
        if entry.is_dir():
            found.extend(_walk_files(entry))
        elif entry.is_file():
            found.append(entry)
    return found


# -- the open deferrals, folded in alongside the chain -----------------------


def _routed_skill(entry: "todo.TodoEntry") -> str:
    """The skill ``entry`` is routed to, as the finding names it.

    An entry whose ``Run`` is missing is still reported -- it is open work, and
    dropping it would let a malformed entry hide from the one place outstanding
    work is read. Which field is wrong is ``check todo``'s to say, and it does.
    """
    return entry.get(TODO_RUN_FIELD).strip() or "no skill (its %s field is missing)" % (
        TODO_RUN_FIELD,
    )


def _deferrals_stage(ws: core.Workspace, walk: StageWalk) -> None:
    """Every open entry on ``context/project/TODO.md``, with its routed skill.

    One warning per entry, in the list's own document order, so the report is
    stable between runs. Never an error: a deferral is outstanding work with an
    owner, and a check that a non-empty list turned red would be a gate cleared
    only by deferring nothing -- which is the gate `todo add` exists to avoid
    needing.
    """
    entries, relative, problems = read_todo(ws)
    walk.diagnostics.extend(problems)
    for entry in entries:
        routed = _routed_skill(entry)
        if routed == TODO_HUMAN_RUN:
            walk.human_deferrals.append(
                core.warning(
                    W_TODO_HUMAN,
                    "entry %r is open and awaits a person; no run clears it" % (entry.title,),
                    file=relative,
                    line=entry.line,
                )
            )
            continue
        walk.deferrals.append(
            core.warning(
                W_TODO_OPEN,
                "entry %r is open and routed to %s" % (entry.title, routed),
                file=relative,
                line=entry.line,
            )
        )


# ---------------------------------------------------------------------------
# Verbs
# ---------------------------------------------------------------------------

TARGET_RUNNERS = {
    CHECK_DEPENDS_ON: depends_on_findings,
    CHECK_COUPLING: coupling_findings,
    CHECK_REQUIREMENTS: requirements_findings,
    CHECK_CATALOG: catalog_findings,
}


def run_target_check(ws: core.Workspace, name: str, target: str) -> CheckReport:
    """One per-target check, as a ``CheckReport``."""
    return CheckReport(name, target, list(TARGET_RUNNERS[name](ws, target)))


def run_todo_check(ws: core.Workspace) -> CheckReport:
    """The workspace-wide deferral-list check, as a ``CheckReport``."""
    return CheckReport(CHECK_TODO, None, list(todo_findings(ws)))


def run_handoff_check(ws: core.Workspace) -> Tuple[CheckReport, List[core.Diagnostic]]:
    """The workspace-wide handoff check and the reads it warned about.

    The stage-chain findings first, then the open deferrals, then the entries
    awaiting a person -- appended rather than interleaved, so the chain's own
    report is byte-identical to what it was on a workspace that has deferred
    nothing, and the `human` group stays visibly separate from the queue that
    drains.
    """
    walk = walk_stages(ws)
    report = CheckReport(
        CHECK_HANDOFF,
        None,
        list(walk.findings) + list(walk.deferrals) + list(walk.human_deferrals),
    )
    return report, list(walk.diagnostics)


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


def _requirements(args, ws: core.Workspace) -> core.Result:
    target = core.check_ident(getattr(args, "target", None), "target")
    return _report_result(run_target_check(ws, CHECK_REQUIREMENTS, target))


def _catalog_check(args, ws: core.Workspace) -> core.Result:
    target = core.check_ident(getattr(args, "target", None), "target")
    return _report_result(run_target_check(ws, CHECK_CATALOG, target))


def _todo_check(args, ws: core.Workspace) -> core.Result:
    """``check todo`` -- workspace-wide, because there is one list at one tier."""
    return _report_result(run_todo_check(ws))


def _handoff(args, ws: core.Workspace) -> core.Result:
    report, diagnostics = run_handoff_check(ws)
    return _report_result(report, diagnostics)


def _all(args, ws: core.Workspace) -> core.Result:
    """Every check: the per-target ones, then the two workspace-wide ones."""
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
    reports.append(run_todo_check(ws))
    handoff_report, warnings = run_handoff_check(ws)
    reports.append(handoff_report)
    diagnostics.extend(warnings)
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
    CHECK_REQUIREMENTS: _requirements,
    CHECK_CATALOG: _catalog_check,
    CHECK_TODO: _todo_check,
    CHECK_HANDOFF: _handoff,
    "all": _all,
}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def register(subparsers) -> None:
    """Declare ``check``'s verbs on ``mc.py``'s subparser action."""
    parser = subparsers.add_parser(
        COMMAND,
        help="the mechanical spec rules and the cross-stage handoff report",
        description=(
            "Run one of the mechanical checks: dangling depends-on paths, the three "
            "dependency rules, requirement coverage, catalog agreement with the spec "
            "tree, the deferral list, or the cross-stage handoff walk. Any finding sets "
            "exit 1."
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

    requirements = verbs.add_parser(
        CHECK_REQUIREMENTS,
        help="catalog references to absent REQ-<NNN>, and requirements no module references",
        description="Measure CATALOG.yaml's requirements references against the target's tier.",
    )
    requirements.add_argument("target", help="a repo name or 'shared'")
    requirements.set_defaults(verb=CHECK_REQUIREMENTS)

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

    todo_parser = verbs.add_parser(
        CHECK_TODO,
        help="deferral entries missing a field, outside an enum, or unstartable",
        description=(
            "Check every entry in context/project/TODO.md against "
            "shared/STANDARD-TODO.md: a missing required field, a Run, Kind or rating "
            "outside its enum, an Origin naming a change or plan that does not resolve, "
            "and a Context naming no file, artifact or identifier to start from. Takes no "
            "target -- there is one list, at the project tier."
        ),
    )
    todo_parser.set_defaults(verb=CHECK_TODO)

    handoff_parser = verbs.add_parser(
        CHECK_HANDOFF,
        help="stranded and incomplete artifacts between stages, and the open deferrals",
        description=(
            "Walk the stage chain and report every artifact whose successor never "
            "consumed it, every successor input that is present but partial, and every "
            "file written by a stage that does not own it. Every open entry in "
            "context/project/TODO.md is reported alongside with the skill it is routed "
            "to, as a warning that never changes the exit code."
        ),
    )
    handoff_parser.set_defaults(verb=CHECK_HANDOFF)

    all_parser = verbs.add_parser(
        "all",
        help="every check above",
        description=(
            "Run the per-target checks for <target> -- or for every target under "
            "context/ when none is given -- plus the workspace-wide handoff check."
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
