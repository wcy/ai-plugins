"""The ``req`` command group -- requirements-tier id allocation and the gate.

    mc.py req next <tier>
    mc.py req gate <tier>

``<tier>`` is a repo name, ``shared``, or ``project``; it resolves to
``context/<tier>/requirements/REQUIREMENTS.md`` per ``STANDARD-REQ.md``
§"Location & Tiers". Each tier keeps its own independent ``REQ-<NNN>``
sequence.

**Ids are allocated from the highest id present, never from a count.** That is
the whole point of this group: allocating from a count (or a length, or an
enumeration index) reuses an id the moment the sequence has a gap, and
``STANDARD-REQ.md``'s stability rule says nothing is ever renumbered or
reused. Allocating from ``max(<NNN>) + 1`` makes reuse structurally impossible
rather than merely discouraged -- there is deliberately no code path here that
counts anything to produce an id.

``spec mode`` reuses :func:`read_requirements` for the requirements half of the
``SpecMode`` shape, so the gate has exactly one implementation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List

from tools import core

COMMAND = "req"

#: The single requirements document per tier, per STANDARD-REQ.md.
REQUIREMENTS_DIR = "requirements"
REQUIREMENTS_FILENAME = "REQUIREMENTS.md"

#: The schema kind the two-line front-matter block is validated against.
FRONT_MATTER_KIND = "requirements"

#: ``### REQ-<NNN>: <Title>`` -- the entry heading STANDARD-REQ.md §"Entry Schema"
#: defines. Four or more digits are tolerated so a sequence that has outgrown
#: three never silently stops parsing.
ENTRY_HEADING_RE = re.compile(r"^###\s+(REQ-(\d{3,}))\s*:\s*(\S.*?)\s*$")

#: Any other Markdown heading closes the entry that precedes it.
HEADING_RE = re.compile(r"^#{1,6}\s")

#: ``**Field:** value`` -- the entry's field lines.
FIELD_RE = re.compile(r"^\*\*([A-Za-z]+):\*\*\s*(.*?)\s*$")

#: Required on every entry, per STANDARD-REQ.md §"Entry Schema".
REQUIRED_FIELDS = ("Need", "Rationale", "Status")

#: The three -- and only three -- documented ``Status`` values.
STATUSES = ("active", "stale", "superseded")

#: ``REQ-<NNN>``, zero-padded to three digits.
ID_FORMAT = "REQ-%03d"

#: What an absent or entry-free tier allocates.
FIRST_ID = ID_FORMAT % 1


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


@dataclass
class Entry:
    """One ``### REQ-<NNN>`` entry as it appears in the file."""

    id: str
    number: int
    title: str
    line: int
    fields: Dict[str, str] = field(default_factory=dict)

    @property
    def valid(self) -> bool:
        """True iff the entry carries the shape STANDARD-REQ.md requires."""
        if not self.title:
            return False
        if any(name not in self.fields for name in REQUIRED_FIELDS):
            return False
        return self.fields["Status"].strip().lower() in STATUSES


def parse_entries(text: str) -> List[Entry]:
    """Every ``### REQ-<NNN>`` entry in ``text``, in document order.

    Malformed entries are returned too, marked invalid: an id that appears in
    the file is *taken* whether or not the entry around it is well formed, and
    handing it out again is exactly the reuse this module exists to prevent.
    """
    entries: List[Entry] = []
    current = None
    # `enumerate` numbers lines for diagnostics only. No id is ever derived
    # from this -- or any other -- index; see `next_id` below.
    for line_number, raw in enumerate(text.splitlines(), start=1):
        heading = ENTRY_HEADING_RE.match(raw)
        if heading is not None:
            current = Entry(
                id=heading.group(1),
                number=int(heading.group(2)),
                title=heading.group(3),
                line=line_number,
            )
            entries.append(current)
            continue
        if HEADING_RE.match(raw):
            current = None  # any other heading ends the entry it follows
            continue
        if current is None:
            continue
        match = FIELD_RE.match(raw)
        if match is not None:
            current.fields.setdefault(match.group(1), match.group(2))
    return entries


def next_id(entries: List[Entry]) -> str:
    """The next free id: ``max(<NNN>) + 1``, never ``len(entries) + 1``.

    A gap in the sequence therefore never renumbers and no id is ever handed
    out twice, which is what makes ``STANDARD-REQ.md``'s append-only rule hold
    mechanically instead of by convention.
    """
    if not entries:
        return FIRST_ID
    highest = entries[0].number
    for entry in entries:
        if entry.number > highest:
            highest = entry.number
    return ID_FORMAT % (highest + 1)


# ---------------------------------------------------------------------------
# The tier's requirements document
# ---------------------------------------------------------------------------


@dataclass
class RequirementsFile:
    """One tier's ``REQUIREMENTS.md``, parsed."""

    tier: str
    path: str  # workspace-relative
    exists: bool
    entries: List[Entry]
    front_matter_ok: bool
    front_matter_errors: List[str]

    @property
    def ids(self) -> List[str]:
        return [entry.id for entry in self.entries]

    @property
    def valid_entries(self) -> List[Entry]:
        return [entry for entry in self.entries if entry.valid]

    @property
    def gate_passes(self) -> bool:
        """The precondition ``mspec``'s CREATE-mode gate checks.

        The file must exist, its front-matter must satisfy the
        ``requirements`` schema kind, and it must hold at least one valid
        entry -- a stub with none does not satisfy the gate.
        """
        return self.exists and self.front_matter_ok and bool(self.valid_entries)

    @property
    def next(self) -> str:
        return next_id(self.entries)

    def summary(self) -> Dict[str, Any]:
        """The ``requirements`` sub-object of ``SpecMode``."""
        return {
            "path": self.path,
            "exists": self.exists,
            "entries": self.ids,
            "gate_passes": self.gate_passes,
        }

    def diagnostics(self) -> List[core.Diagnostic]:
        """Warnings worth reporting; none of them changes the answer."""
        notes: List[core.Diagnostic] = []
        for message in self.front_matter_errors:
            notes.append(
                core.warning(
                    core.E_SCHEMA_INVALID,
                    "front-matter does not validate against %r: %s"
                    % (FRONT_MATTER_KIND, message),
                    file=self.path,
                )
            )
        for entry in self.entries:
            if entry.valid:
                continue
            notes.append(
                core.warning(
                    core.E_INVALID_STATE,
                    "%s is missing a required field (%s) or carries an unknown Status; "
                    "its id stays allocated regardless"
                    % (entry.id, ", ".join(REQUIRED_FIELDS)),
                    file=self.path,
                    line=entry.line,
                )
            )
        return notes


def requirements_path(tier: str) -> str:
    """The workspace-relative path for ``tier``, per STANDARD-REQ.md."""
    return "context/%s/%s/%s" % (tier, REQUIREMENTS_DIR, REQUIREMENTS_FILENAME)


def read_requirements(ws: core.Workspace, tier: str) -> RequirementsFile:
    """Resolve, read, and parse one tier's ``REQUIREMENTS.md``.

    ``tier`` passes ``Ident`` before it is used as a path component -- rejected,
    never sanitized. An absent file is not an error: it is a tier with no
    entries, which allocates ``REQ-001`` and fails the gate.
    """
    core.check_ident(tier, "tier")
    relative = requirements_path(tier)
    target = ws.safe_path("context", tier, REQUIREMENTS_DIR, REQUIREMENTS_FILENAME)
    if not target.is_file():
        return RequirementsFile(tier, relative, False, [], False, [])
    text = core.read_text(target, relative)
    matter = core.read_front_matter(text)
    errors = core.validate_against(core.load_schema(FRONT_MATTER_KIND), matter)
    return RequirementsFile(tier, relative, True, parse_entries(text), not errors, errors)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def register(subparsers) -> None:
    """Declare ``req``'s verbs on ``mc.py``'s subparser action."""
    parser = subparsers.add_parser(
        COMMAND,
        help="requirements-tier id allocation and the CREATE-mode gate",
        description="Allocate the next REQ-<NNN> for a tier, or report its gate result.",
    )
    verbs = parser.add_subparsers(dest="verb", metavar="verb", required=True)

    next_parser = verbs.add_parser(
        "next",
        help="the next free REQ-<NNN> for a tier",
        description="Return max(<NNN>) + 1 for the tier -- never an id derived from a count.",
    )
    next_parser.add_argument("tier", help="a repo name, 'shared', or 'project'")

    gate_parser = verbs.add_parser(
        "gate",
        help="whether the tier holds at least one valid entry",
        description="Report whether the tier's REQUIREMENTS.md satisfies the CREATE-mode gate.",
    )
    gate_parser.add_argument("tier", help="a repo name, 'shared', or 'project'")

    parser.set_defaults(group=COMMAND)


def _next(args, ws: core.Workspace) -> core.Result:
    document = read_requirements(ws, args.tier)
    return core.Result(
        command="%s.next" % COMMAND,
        data={
            "tier": document.tier,
            "path": document.path,
            "exists": document.exists,
            "entries": document.ids,
            "next": document.next,
        },
        diagnostics=document.diagnostics(),
    )


def _gate(args, ws: core.Workspace) -> core.Result:
    document = read_requirements(ws, args.tier)
    return core.Result(
        command="%s.gate" % COMMAND,
        data={
            "tier": document.tier,
            "path": document.path,
            "exists": document.exists,
            "entries": document.ids,
            "gate_passes": document.gate_passes,
        },
        diagnostics=document.diagnostics(),
    )


VERBS = {"next": _next, "gate": _gate}


def run(args, ws: core.Workspace) -> core.Result:
    """Dispatch one ``req`` verb. Never raises; failures are diagnostics."""
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
