"""The ``req`` command group -- requirements-tier id allocation, the gate, and
requirements-change sequencing.

    mc.py req next <tier> [--mnemonic <slug>]
    mc.py req gate <tier>
    mc.py req change-resolve <tier> [--slug <slug>]
    mc.py req change-emit <path> --tier <t> --status <s> [--spec-change <ref>]
    mc.py req change-close <path> --change <NNN>
    mc.py req change-list <tier> [--open]

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

**Requirement ids carry an optional mnemonic.** ``REQ-<NNN>-<mnemonic>`` --
the zero-padded number is the whole of the identity, and the kebab-case
mnemonic is correctable prose. :func:`parse_req_id` is the single
implementation every caller resolves an id through -- the entry-heading
scanner below, the catalog ``requirements:`` reader, and ``check``'s coverage
pass alike -- so no caller compares raw strings. See ``TOOLS-DATAMODEL.md``
§"Requirement identifiers".

**Requirements-change records** (``REQ-CHANGE-<NNN>-<slug>.md`` under
``context/<tier>/requirements/changes/``) are sequenced and emitted the same
way ``tools/change.py`` sequences and emits change documents: partition on
status, allocate ``max + 1``, validate before persisting. ``change-close`` is
the tool's one write under ``requirements/`` -- see ``TOOLS-IMPLEMENTATION.md``
§"Ownership".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from tools import core

COMMAND = "req"

CONTEXT_DIRNAME = "context"

#: The single requirements document per tier, per STANDARD-REQ.md.
REQUIREMENTS_DIR = "requirements"
REQUIREMENTS_FILENAME = "REQUIREMENTS.md"

#: The schema kind the two-line front-matter block is validated against.
FRONT_MATTER_KIND = "requirements"

#: 2-4 kebab-case words, per STANDARD-REQ.md's mnemonic grammar -- the same
#: grammar ``STANDARD-CHANGE.md`` gives a change slug.
MNEMONIC_GRAMMAR = r"[a-z0-9]+(?:-[a-z0-9]+){1,3}"
MNEMONIC_RE = re.compile(r"^%s$" % MNEMONIC_GRAMMAR)

#: ``### REQ-<NNN>[-<mnemonic>]: <Title>`` -- the entry heading STANDARD-REQ.md
#: §"Entry Schema" defines. Four or more digits are tolerated so a sequence
#: that has outgrown three never silently stops parsing. The mnemonic suffix
#: is optional here so a bare, not-yet-migrated heading still parses.
ENTRY_HEADING_RE = re.compile(
    r"^###\s+(REQ-\d{3,}(?:-%s)?)\s*:\s*(\S.*?)\s*$" % MNEMONIC_GRAMMAR
)

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

PROJECT_TIER = "project"


# ---------------------------------------------------------------------------
# Requirement identifiers -- TOOLS-DATAMODEL.md §"Requirement identifiers"
# ---------------------------------------------------------------------------

#: Any written form of a requirement id: bare, mnemonic-bearing, or
#: ``project:``-qualified, per ``catalog.schema.json``'s widened
#: ``requirements`` item pattern -- the single grammar every caller resolves
#: against. Never used to compare raw strings; see :func:`parse_req_id`.
REQ_ID_RE = re.compile(
    r"^(?:(?P<tier>%s):)?REQ-(?P<number>[0-9]{3,})(?:-(?P<mnemonic>%s))?$"
    % (PROJECT_TIER, MNEMONIC_GRAMMAR)
)

#: A written form that does not parse as a requirement id at all -- an error
#: diagnostic, never a silent skip. A skipped reference and an absent one are
#: indistinguishable downstream, which is what would make a half-migrated
#: tier read as a collapsed one.
E_BAD_REQ_REF = "E_BAD_REQ_REF"


@dataclass(frozen=True)
class ReqId:
    """Any written form of a requirement id, resolved to its identity.

    ``number`` is the whole of the identity, per TOOLS-DATAMODEL.md: every
    caller resolves on it and none compares raw strings.
    """

    raw: str
    tier: str  # "self" | "project"
    number: str
    mnemonic: Optional[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "raw": self.raw,
            "tier": self.tier,
            "number": self.number,
            "mnemonic": self.mnemonic,
        }


def parse_req_id(raw: Any) -> ReqId:
    """Parse any written form of a requirement id into its ``ReqId``.

    The single implementation every caller uses: the entry-heading scanner in
    this module, the catalog ``requirements:`` reader and ``check``'s coverage
    pass in ``tools/check.py``. Raises ``ToolError`` carrying
    :data:`E_BAD_REQ_REF` on a form that does not parse -- never returns a
    partial or best-effort result, and never skips.
    """
    text = str(raw).strip()
    match = REQ_ID_RE.match(text)
    if match is None:
        raise core.fail(
            E_BAD_REQ_REF,
            "%r does not parse as a requirement id (want REQ-<NNN>, "
            "REQ-<NNN>-<mnemonic>, or project:REQ-<NNN>[-<mnemonic>])" % (raw,),
        )
    tier = PROJECT_TIER if match.group("tier") else "self"
    return ReqId(
        raw=text,
        tier=tier,
        number=match.group("number"),
        mnemonic=match.group("mnemonic"),
    )


def check_mnemonic(value: Any) -> str:
    """The mnemonic grammar in raising form. Rejected, never sanitized.

    Silently rewriting a bad mnemonic into an accepted one is exactly the
    class of bug ``core.check_ident`` exists to prevent for every other
    identifier the tool accepts; a mnemonic gets the same treatment.
    """
    if not isinstance(value, str) or not MNEMONIC_RE.match(value):
        raise core.fail(
            E_BAD_REQ_REF,
            "invalid mnemonic %r: must match /%s/ (rejected, never sanitized)"
            % (value, MNEMONIC_GRAMMAR),
        )
    return value


# ---------------------------------------------------------------------------
# Mnemonic derivation -- TOOLS-DATAMODEL.md §"Mnemonic derivation"
# ---------------------------------------------------------------------------

#: A ``<Title>`` yielding no usable slug. An error diagnostic, never a guess:
#: choosing words the title does not contain is authoring, which belongs to
#: the caller (``mreq``) rather than to this tool.
E_NO_MNEMONIC = "E_NO_MNEMONIC"

#: Function words dropped before the first 2-4 words are kept. Deliberately
#: minimal -- articles, prepositions, conjunctions, auxiliaries, pronouns --
#: so the candidate stays close to the title's own wording. Interrogatives
#: (``why``, ``how``, ``what``) are **not** here: they routinely carry a
#: requirement title's meaning ("Know why a need changed").
STOPWORDS = frozenset(
    """
    a an the and or but of to in on at by for from into with without as is are was
    were be been being it its that this these those their there they them
    has have had do does did will would should could can may might must
    not no than then so such own still rather per
    """.split()
)

#: Non-alphanumeric runs collapse to a single hyphen, per MMIGRATE-DATAMODEL.md.
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")

#: The upper and lower bounds MNEMONIC_GRAMMAR itself encodes.
MNEMONIC_MIN_WORDS = 2
MNEMONIC_MAX_WORDS = 4


def derive_mnemonic(title: Any) -> Optional[str]:
    """The candidate mnemonic for ``title``, or ``None`` when it yields none.

    A pure function of ``title``: no clock, no filesystem, no workspace. That
    is what makes the derivation assertable without running an agent, per
    REQ-018-agent-drives-workflow's third clause, and it is the property
    ``tests/test_determinism.py`` pins.

    The candidate is a **proposal, not a decision** -- which words are
    genuinely significant is judgment the caller may exercise by substituting
    its own choice, and whatever it chooses still goes through
    :func:`check_mnemonic` via ``req next --mnemonic``.

    Returns ``None`` rather than a partial slug when fewer than
    ``MNEMONIC_MIN_WORDS`` words survive, since a one-word slug would not
    satisfy ``MNEMONIC_RE`` and inventing a second word would be authoring.
    """
    if not isinstance(title, str):
        return None
    words = [word for word in _NON_ALNUM_RE.split(title.lower()) if word]
    significant = [word for word in words if word not in STOPWORDS]
    if len(significant) < MNEMONIC_MIN_WORDS:
        return None
    candidate = "-".join(significant[:MNEMONIC_MAX_WORDS])
    return candidate if MNEMONIC_RE.match(candidate) else None


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


@dataclass
class Entry:
    """One ``### REQ-<NNN>[-<mnemonic>]`` entry as it appears in the file."""

    id: str
    number: int
    title: str
    line: int
    mnemonic: Optional[str] = None
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
            parsed = parse_req_id(heading.group(1))
            current = Entry(
                id=parsed.raw,
                number=int(parsed.number),
                title=heading.group(2),
                line=line_number,
                mnemonic=parsed.mnemonic,
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
    return "%s/%s/%s/%s" % (CONTEXT_DIRNAME, tier, REQUIREMENTS_DIR, REQUIREMENTS_FILENAME)


def read_requirements(ws: core.Workspace, tier: str) -> RequirementsFile:
    """Resolve, read, and parse one tier's ``REQUIREMENTS.md``.

    ``tier`` passes ``Ident`` before it is used as a path component -- rejected,
    never sanitized. An absent file is not an error: it is a tier with no
    entries, which allocates ``REQ-001`` and fails the gate.
    """
    core.check_ident(tier, "tier")
    relative = requirements_path(tier)
    target = ws.safe_path(CONTEXT_DIRNAME, tier, REQUIREMENTS_DIR, REQUIREMENTS_FILENAME)
    if not target.is_file():
        return RequirementsFile(tier, relative, False, [], False, [])
    text = core.read_text(target, relative)
    matter = core.read_front_matter(text)
    errors = core.validate_against(core.load_schema(FRONT_MATTER_KIND), matter)
    return RequirementsFile(tier, relative, True, parse_entries(text), not errors, errors)


# ---------------------------------------------------------------------------
# Requirements-change sequencing -- TOOLS-DATAMODEL.md §"Requirements change
# sequencing"; mirrors tools/change.py's partition and emission exactly.
# ---------------------------------------------------------------------------

REQ_CHANGE_DIRNAME = "changes"
REQ_CHANGE_PREFIX = "REQ-CHANGE-"
REQ_CHANGE_RE = re.compile(r"^REQ-CHANGE-([0-9]{3,4})-(.+)\.md$")

#: The literal ``spec-change`` value marking a revision with no spec delta --
#: the mreq->mspec analogue of ``STANDARD-CHANGE.md``'s ``plan: not-required``.
SPEC_CHANGE_NOT_REQUIRED = "not-required"

#: The slug a ``create`` target carries when the caller supplied none.
PLACEHOLDER_SLUG = "unnamed"

#: The schema kind a requirements-change record's front-matter validates
#: against, per SCHEMAS-INTERFACE.md.
REQ_CHANGE_FRONT_MATTER_KIND = "req-change"

CHANGES_DIRNAME = "changes"


@dataclass(frozen=True)
class ReqChangeRef:
    """One requirements-change record on disk, per TOOLS-DATAMODEL.md."""

    tier: str
    number: str  # zero-padded, per-tier sequence
    slug: str
    path: str  # workspace-relative
    status: str  # "open" | "closed" | "" (unreadable -- treated as terminal)
    spec_change: Optional[str]  # the CHANGE-<NNN> that closed it, "not-required", or None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tier": self.tier,
            "number": self.number,
            "slug": self.slug,
            "path": self.path,
            "status": self.status,
            "spec_change": self.spec_change,
        }


def _req_change_dir_parts(tier: str) -> Tuple[str, ...]:
    return (CONTEXT_DIRNAME, tier, REQUIREMENTS_DIR, REQ_CHANGE_DIRNAME)


def _scan_req_changes(
    ws: core.Workspace, tier: str, diagnostics: List[core.Diagnostic]
) -> List[ReqChangeRef]:
    """Every ``REQ-CHANGE-<NNN>-<slug>.md`` record for ``tier``, sorted.

    An unreadable or unparseable record produces a warning diagnostic and is
    kept in the listing as terminal -- never a traceback, and never a silent
    drop that would let the next number be allocated over the top of it.
    """
    parts = _req_change_dir_parts(tier)
    directory = ws.safe_path(*parts)
    refs: List[ReqChangeRef] = []
    if not directory.is_dir():
        return refs
    for entry in sorted(directory.iterdir(), key=lambda item: item.name):
        if not entry.is_file() or not entry.name.startswith(REQ_CHANGE_PREFIX):
            continue
        relative = "/".join(list(parts) + [entry.name])
        match = REQ_CHANGE_RE.match(entry.name)
        if match is None:
            diagnostics.append(
                core.warning(
                    core.E_UNPARSED_NAME,
                    "filename does not match %s<NNN>-<slug>.md; not sequenced"
                    % (REQ_CHANGE_PREFIX,),
                    file=relative,
                )
            )
            continue
        number, slug = match.group(1), match.group(2)
        if not core.is_ident(slug):
            diagnostics.append(
                core.warning(
                    core.E_BAD_IDENT,
                    "slug %r is not a valid identifier; not sequenced" % (slug,),
                    file=relative,
                )
            )
            continue
        try:
            matter = core.load_front_matter(entry, relative)
        except core.ToolError as exc:
            diagnostics.append(
                core.warning(
                    exc.diagnostic.code,
                    "%s; treated as terminal" % (exc.diagnostic.message,),
                    file=relative,
                )
            )
            refs.append(
                ReqChangeRef(tier=tier, number=number, slug=slug, path=relative, status="", spec_change=None)
            )
            continue
        status = matter.get("status") or ""
        refs.append(
            ReqChangeRef(
                tier=tier,
                number=number,
                slug=slug,
                path=relative,
                status=status,
                spec_change=matter.get("spec-change"),
            )
        )
    return refs


def _is_req_change_continuable(ref: ReqChangeRef) -> bool:
    """A record is continuable only while ``open`` and ``spec_change`` unset.

    ``closed`` is terminal, and so is an ``open`` record carrying
    ``spec_change: not-required`` -- it is a revision with no spec delta,
    complete as written.
    """
    return ref.status == "open" and ref.spec_change is None


def _find_change_file(ws: core.Workspace, tier: Optional[str], number: str) -> Optional[Path]:
    """The ``CHANGE-<number>-*.md`` file under ``context/<tier>/changes/``.

    ``None`` when ``tier`` is absent, the directory does not exist, or no file
    names that number -- the caller reports this as a refusal, never a
    fabricated success.
    """
    if not tier:
        return None
    directory, escape = ws.resolve_path(CONTEXT_DIRNAME, tier, CHANGES_DIRNAME)
    if escape is not None or not directory.is_dir():
        return None
    prefix = "CHANGE-%s-" % number
    for entry in sorted(directory.iterdir(), key=lambda item: item.name):
        if entry.is_file() and entry.name.startswith(prefix) and entry.name.endswith(".md"):
            return entry
    return None


def _title_from_slug(slug: str) -> str:
    """A human-readable title derived mechanically from a slug."""
    return " ".join(word.capitalize() for word in slug.split("-"))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def register(subparsers) -> None:
    """Declare ``req``'s verbs on ``mc.py``'s subparser action."""
    parser = subparsers.add_parser(
        COMMAND,
        help="requirements-tier id allocation, the CREATE-mode gate, and requirements-change sequencing",
        description=(
            "Allocate the next REQ-<NNN> for a tier, report its gate result, or sequence and "
            "emit the tier's REQ-CHANGE-<NNN>-<slug>.md provenance records."
        ),
    )
    verbs = parser.add_subparsers(dest="verb", metavar="verb", required=True)

    next_parser = verbs.add_parser(
        "next",
        help="the next free REQ-<NNN>[-<mnemonic>] for a tier",
        description=(
            "Return max(<NNN>) + 1 for the tier -- never an id derived from a count. "
            "--mnemonic composes REQ-<NNN>-<mnemonic>, validated against the mnemonic "
            "grammar and never sanitized."
        ),
    )
    next_parser.add_argument("tier", help="a repo name, 'shared', or 'project'")
    next_parser.add_argument(
        "--mnemonic",
        default=None,
        help="2-4 kebab-case words to append to the allocated id; rejected, never sanitized",
    )

    mnemonic_parser = verbs.add_parser(
        "mnemonic",
        help="the candidate mnemonic for a requirement entry's <Title>",
        description=(
            "Derive the candidate mnemonic from a <Title>: lowercased, non-alphanumerics "
            "collapsed to hyphens, the first 2-4 significant words kept. The candidate is a "
            "proposal the caller may substitute; whatever it uses goes to req next --mnemonic, "
            "which validates it. A title yielding no usable slug reports E_NO_MNEMONIC rather "
            "than a guess."
        ),
    )
    mnemonic_parser.add_argument("title", help="the entry's <Title>, verbatim")

    gate_parser = verbs.add_parser(
        "gate",
        help="whether the tier holds at least one valid entry",
        description="Report whether the tier's REQUIREMENTS.md satisfies the CREATE-mode gate.",
    )
    gate_parser.add_argument("tier", help="a repo name, 'shared', or 'project'")

    change_resolve_parser = verbs.add_parser(
        "change-resolve",
        help="create-vs-continue for context/<tier>/requirements/changes/",
        description="Resolve the per-tier requirements-change sequence.",
    )
    change_resolve_parser.add_argument("tier", help="a repo name, 'shared', or 'project'")
    change_resolve_parser.add_argument(
        "--slug",
        default=None,
        help="slug for the allocated file when the decision is create (default: %s)"
        % (PLACEHOLDER_SLUG,),
    )

    change_emit_parser = verbs.add_parser(
        "change-emit",
        help="write a requirements-change record's front-matter and section skeleton",
        description=(
            "Write REQ-CHANGE-<NNN>-<slug>.md's front matter and section skeleton, validated "
            "against req-change-frontmatter before anything is persisted."
        ),
    )
    change_emit_parser.add_argument("path", help="workspace-relative path of the record to write")
    change_emit_parser.add_argument("--tier", required=True, help="the tier the record belongs to")
    change_emit_parser.add_argument(
        "--status", required=True, choices=["open", "closed"], help="front-matter status"
    )
    change_emit_parser.add_argument(
        "--spec-change",
        default=None,
        help="CHANGE-<NNN> covering this record, or 'not-required'",
    )

    change_close_parser = verbs.add_parser(
        "change-close",
        help="close a requirements-change record -- the tool's one write under requirements/",
        description=(
            "Set status: closed and spec-change: CHANGE-<NNN> together, validated against "
            "req-change-frontmatter before persisting. Refuses an already-closed record and a "
            "CHANGE-<NNN> that does not exist."
        ),
    )
    change_close_parser.add_argument("path", help="workspace-relative path of the record to close")
    change_close_parser.add_argument(
        "--change", required=True, help="the zero-padded number of the covering CHANGE-<NNN>"
    )

    change_list_parser = verbs.add_parser(
        "change-list",
        help="a tier's requirements-change records, newest first",
        description="List REQ-CHANGE records for a tier; --open restricts to those still awaiting a spec change.",
    )
    change_list_parser.add_argument("tier", help="a repo name, 'shared', or 'project'")
    change_list_parser.add_argument(
        "--open", action="store_true", help="restrict to records still awaiting a spec change"
    )

    parser.set_defaults(group=COMMAND)


def _next(args, ws: core.Workspace) -> core.Result:
    document = read_requirements(ws, args.tier)
    bare = document.next
    mnemonic = getattr(args, "mnemonic", None)
    if mnemonic is not None:
        check_mnemonic(mnemonic)
        next_value = "%s-%s" % (bare, mnemonic)
    else:
        next_value = bare
    return core.Result(
        command="%s.next" % COMMAND,
        data={
            "tier": document.tier,
            "path": document.path,
            "exists": document.exists,
            "entries": document.ids,
            "next": next_value,
        },
        diagnostics=document.diagnostics(),
    )


def _mnemonic(args, ws: core.Workspace) -> core.Result:
    """``req mnemonic <title>``. Reads no file; ``ws`` is unused by design."""
    title = getattr(args, "title", None)
    candidate = derive_mnemonic(title)
    diagnostics = []
    if candidate is None:
        diagnostics.append(
            core.error(
                E_NO_MNEMONIC,
                "title %r yields no usable mnemonic: fewer than %d words survive "
                "(deferred to the caller, never guessed at)" % (title, MNEMONIC_MIN_WORDS),
            )
        )
    return core.Result(
        command="%s.mnemonic" % COMMAND,
        data={"title": title, "candidate": candidate},
        diagnostics=diagnostics,
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


def _target_slug(args) -> str:
    """The slug a ``create`` target carries. Rejected, never sanitized."""
    slug = getattr(args, "slug", None)
    if slug is None:
        return PLACEHOLDER_SLUG
    return core.check_ident(slug, "slug")


def _change_resolve(args, ws: core.Workspace) -> core.Result:
    tier = core.check_ident(getattr(args, "tier", None), "tier")
    slug = _target_slug(args)
    diagnostics: List[core.Diagnostic] = []
    considered = _scan_req_changes(ws, tier, diagnostics)
    continuable = [ref for ref in considered if _is_req_change_continuable(ref)]

    if len(continuable) == 1:
        decision = {
            "action": "continue",
            "target": continuable[0].to_dict(),
            "considered": [ref.to_dict() for ref in considered],
        }
    else:
        # `max + 1` from the highest number present -- never from the count.
        highest = max([int(ref.number) for ref in considered] or [0])
        number = "%03d" % (highest + 1)
        filename = "%s%s-%s.md" % (REQ_CHANGE_PREFIX, number, slug)
        parts = _req_change_dir_parts(tier)
        target = ReqChangeRef(
            tier=tier,
            number=number,
            slug=slug,
            path="/".join(list(parts) + [filename]),
            status="",
            spec_change=None,
        )
        decision = {
            "action": "create",
            "target": target.to_dict(),
            "considered": [ref.to_dict() for ref in considered],
        }
    return core.Result(command="%s.change-resolve" % COMMAND, data=decision, diagnostics=diagnostics)


def _change_emit(args, ws: core.Workspace) -> core.Result:
    """``req change-emit`` -- validate, then write; never the other way round."""
    given = getattr(args, "path", None)
    if not given:
        raise core.fail(core.E_USAGE, "req change-emit requires a path")
    target = ws.safe_path(given)
    relative = ws.rel(target)
    name = Path(str(given)).name

    match = REQ_CHANGE_RE.match(name)
    if match is None:
        raise core.fail(
            core.E_USAGE,
            "filename must be %s<NNN>-<slug>.md" % (REQ_CHANGE_PREFIX,),
            file=relative,
        )
    number, slug = match.group(1), match.group(2)
    core.check_ident(slug, "slug")

    tier = core.check_ident(getattr(args, "tier", None), "tier")
    status = getattr(args, "status", None)
    if not status:
        raise core.fail(core.E_USAGE, "req change-emit requires --status", file=relative)
    spec_change = getattr(args, "spec_change", None)
    now = getattr(args, "now", None) or core.system_instant()

    matter: Dict[str, str] = {"req-change": number, "tier": tier, "status": status, "date": now}
    if spec_change:
        matter["spec-change"] = spec_change

    errors = core.validate_against(core.load_schema(REQ_CHANGE_FRONT_MATTER_KIND), matter)
    if errors:
        raise core.ToolError(
            core.error(
                core.E_SCHEMA_INVALID,
                "front-matter does not validate against req-change-frontmatter: %s"
                % ("; ".join(errors),),
                file=relative,
            )
        )

    heading = "%s%s: %s" % (REQ_CHANGE_PREFIX, number, _title_from_slug(slug))
    text = "%s\n# %s\n" % (core.render_front_matter(matter), heading)

    if target.exists():
        existing = core.read_text(target, relative)
        if existing != text:
            raise core.ToolError(
                core.error(
                    core.E_INVALID_STATE,
                    "refusing to overwrite an existing requirements-change record",
                    file=relative,
                )
            )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return core.Result(
        command="%s.change-emit" % COMMAND, data={"path": relative, "lines": [relative]}
    )


def _change_close(args, ws: core.Workspace) -> core.Result:
    """``req change-close`` -- the tool's one write under ``requirements/``."""
    given = getattr(args, "path", None)
    if not given:
        raise core.fail(core.E_USAGE, "req change-close requires a path")
    target = ws.safe_path(given)
    relative = ws.rel(target)
    name = Path(str(given)).name

    match = REQ_CHANGE_RE.match(name)
    if match is None:
        raise core.fail(
            core.E_USAGE,
            "filename must be %s<NNN>-<slug>.md" % (REQ_CHANGE_PREFIX,),
            file=relative,
        )
    # The filename is derived from free-form user description; route it
    # through the same guard every other identifier the tool accepts gets.
    core.check_ident(match.group(2), "slug")

    change_number = getattr(args, "change", None)
    if not change_number or not re.match(r"^[0-9]{3,4}$", str(change_number)):
        raise core.fail(
            core.E_USAGE,
            "--change must be a zero-padded number (3-4 digits), got %r" % (change_number,),
            file=relative,
        )

    if not target.is_file():
        raise core.fail(core.E_NO_SUCH_FILE, "no such file", file=relative)
    matter = core.load_front_matter(target, relative)

    if matter.get("status") == "closed":
        raise core.fail(core.E_INVALID_STATE, "the record is already closed", file=relative)

    change_path = _find_change_file(ws, matter.get("tier"), str(change_number))
    if change_path is None:
        raise core.fail(
            core.E_NOT_FOUND,
            "CHANGE-%s does not exist for tier %r" % (change_number, matter.get("tier")),
            file=relative,
        )

    matter["status"] = "closed"
    matter["spec-change"] = "CHANGE-%s" % (change_number,)

    errors = core.validate_against(core.load_schema(REQ_CHANGE_FRONT_MATTER_KIND), matter)
    if errors:
        raise core.ToolError(
            core.error(
                core.E_SCHEMA_INVALID,
                "front-matter does not validate against req-change-frontmatter: %s"
                % ("; ".join(errors),),
                file=relative,
            )
        )

    core.write_front_matter(target, matter)
    return core.Result(
        command="%s.change-close" % COMMAND,
        data={"path": relative, "status": "closed", "spec-change": matter["spec-change"]},
    )


def _change_list(args, ws: core.Workspace) -> core.Result:
    tier = core.check_ident(getattr(args, "tier", None), "tier")
    diagnostics: List[core.Diagnostic] = []
    considered = _scan_req_changes(ws, tier, diagnostics)
    only_open = bool(getattr(args, "open", False))
    if only_open:
        # "Still awaiting a spec change" -- the same predicate `change-resolve`
        # uses to decide continuability, not a bare `status == "open"` check:
        # an `open` record carrying `spec-change: not-required` is complete
        # as written and is not awaiting anything.
        considered = [ref for ref in considered if _is_req_change_continuable(ref)]
    # Newest first.
    ordered = sorted(considered, key=lambda ref: int(ref.number), reverse=True)
    return core.Result(
        command="%s.change-list" % COMMAND,
        data={"tier": tier, "open": only_open, "records": [ref.to_dict() for ref in ordered]},
        diagnostics=diagnostics,
    )


VERBS = {
    "next": _next,
    "mnemonic": _mnemonic,
    "gate": _gate,
    "change-resolve": _change_resolve,
    "change-emit": _change_emit,
    "change-close": _change_close,
    "change-list": _change_list,
}


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
