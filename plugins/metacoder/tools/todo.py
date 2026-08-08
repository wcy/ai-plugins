"""The ``todo`` command group -- the deferral list's only writer.

    mc.py todo add --title <t> --run <skill> --kind <k> --origin <ref>
                   --priority <p> --risk-if-unfixed <r> --regression-risk <r>
                   --cost <c> --context <text>
    mc.py todo remove <title>
    mc.py todo list [--run <skill>] [--kind <k>]

``context/project/TODO.md`` is the one list, at the one tier
(``STANDARD-TODO.md`` §"Location & Tier"), and this group is the only thing that
writes it. **No skill hand-writes an entry:** the fields are a fixed shape and a
fixed set of enums, so composing one in prose is a second implementation of
``add`` -- and a second implementation is what lets the standard and the written
entry disagree.

Three rules this module exists to enforce:

* **The standard owns the field set and the enums.** Both are read out of
  ``shared/STANDARD-TODO.md`` §"Entry schema" at run time by
  :func:`parse_field_table`, never restated here, so a member added to a closed
  enum there is accepted here without a code change and one removed there is
  refused here. The flags ``add`` declares are derived from the same table, so
  the CLI cannot drift from the schema it writes either.
* **A refusal writes nothing -- including the file.** Every field is validated
  before the first byte is written, so a rejected *first* entry leaves no
  ``TODO.md`` behind at all. An empty list is indistinguishable from a list
  nobody has filed against, and creating one on a refusal would manufacture that
  ambiguity.
* **A no-op removal is a failure, not a success.** ``remove`` refuses a title
  matching no entry, because a silent no-op reads exactly like a successful
  deletion and would leave a fixed item sitting on the list.

Resolution is removal (``STANDARD-TODO.md`` §"Resolution Is Removal"): there is
no closed state, no ``Status`` field, and nothing here writes one.

Nothing in this module invokes git, reads the wall clock, or touches ``argv``:
``Raised`` and the front-matter ``updated`` key both come from the injected
clock, and directory listings are sorted, so two runs over the same workspace
with the same ``--now`` produce byte-identical output.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from tools import core

COMMAND = "todo"

# ---------------------------------------------------------------------------
# Stable diagnostic codes owned by this module.
#
# `check todo` reports the same three codes for a hand-edited list, so "the
# checker reports every violation the emitter would have refused" holds as an
# identity between codes rather than as a claim about two wordings.
# ---------------------------------------------------------------------------

#: A required field is absent, empty, or carries text the entry format cannot
#: round-trip.
E_TODO_FIELD = "E_TODO_FIELD"

#: A value outside the closed enum `STANDARD-TODO.md` states for its field.
E_TODO_ENUM = "E_TODO_ENUM"

#: An `Origin` naming a change document or plan that does not exist on disk.
E_TODO_ORIGIN = "E_TODO_ORIGIN"

# ---------------------------------------------------------------------------
# The list itself -- STANDARD-TODO.md §"Location & Tier" and §"Front-Matter"
# ---------------------------------------------------------------------------

CONTEXT_DIRNAME = "context"
PROJECT_DIRNAME = "project"
CHANGES_DIRNAME = "changes"
PLANS_DIRNAME = "plans"
TODO_FILENAME = "TODO.md"

#: The one list, at the one tier. There is no per-repo TODO.
TODO_PARTS = (CONTEXT_DIRNAME, PROJECT_DIRNAME, TODO_FILENAME)
TODO_REL = "/".join(TODO_PARTS)

#: The schema every write is validated against before it is persisted.
TODO_KIND = "todo-frontmatter"

#: The two front-matter keys, in the order the standard writes them.
TIER_KEY = "todo"
TIER_VALUE = "project"
UPDATED_KEY = "updated"

#: The body's one heading; every entry is an `## <Title>` beneath it.
BODY_HEADING = "# Deferred Work"

# ---------------------------------------------------------------------------
# The field table -- read from STANDARD-TODO.md, never restated
# ---------------------------------------------------------------------------

PLUGIN_ROOT = Path(core.__file__).resolve().parent.parent
SHARED_DIR = PLUGIN_ROOT / "shared"
STANDARD_TODO = SHARED_DIR / "STANDARD-TODO.md"
STANDARD_TODO_DISPLAY = "shared/STANDARD-TODO.md"

#: The section holding the field table, and the table's own header row.
ENTRY_SCHEMA_HEADING = "## Entry schema"
FIELD_TABLE_HEADER = ("Field", "Value")

#: The field whose value is derived from the injected clock rather than
#: supplied, and so is the one table row that is not a flag.
RAISED_FIELD = "Raised"
DERIVED_FIELDS = (RAISED_FIELD,)

#: The two fields with rules of their own beyond presence and enum membership.
ORIGIN_FIELD = "Origin"
CONTEXT_FIELD = "Context"

#: One backtick-quoted literal inside a table cell.
_BACKTICK_RE = re.compile(r"`([^`]+)`")

#: A table row's cell separator: a pipe the author did not escape.
_ROW_SPLIT_RE = re.compile(r"(?<!\\)\|")

#: The character that marks a backticked token as a *form* rather than a value:
#: `CHANGE-<NNN>` is a shape an argument must take, not one of a closed set.
_PLACEHOLDER_MARK = "<"

#: A cell states a closed enum when it lists **two or more** literal values and
#: none of them is a placeholder form. That is the whole discriminator, and it
#: is structural on purpose: `Run` and `Kind` and the four ratings each list
#: their members, `Origin` lists two `<NNN>`-bearing forms, `Raised` lists one
#: date format, and `Context` lists nothing. Deriving enum-ness this way is what
#: keeps the *set* of enum fields owned by the standard too, rather than leaving
#: the members read from it and the fields they belong to hard-coded here.
_MIN_ENUM_MEMBERS = 2


@dataclass(frozen=True)
class FieldSpec:
    """One row of ``STANDARD-TODO.md``'s field table."""

    name: str
    enum: Optional[Tuple[str, ...]]  # None when the cell states no closed set

    @property
    def flag(self) -> str:
        """The ``add`` option this field is supplied through."""
        return "--%s" % self.name.lower()

    @property
    def dest(self) -> str:
        """The attribute argparse stores :attr:`flag` under."""
        return self.name.lower().replace("-", "_")


def parse_field_table(text: str) -> List[FieldSpec]:
    """The field table under §"Entry schema", as ``FieldSpec`` in table order.

    Pure in its input, so the derivation is exercisable against a synthetic
    standard rather than only against the shipped one.
    """
    lines = text.split("\n")
    try:
        start = next(
            index for index, line in enumerate(lines) if line.strip() == ENTRY_SCHEMA_HEADING
        )
    except StopIteration:
        raise core.fail(
            core.E_PARSE,
            "no section %r" % (ENTRY_SCHEMA_HEADING,),
            file=STANDARD_TODO_DISPLAY,
        )

    rows: List[List[str]] = []
    seen_header = False
    fenced = False
    for line in lines[start + 1 :]:
        stripped = line.strip()
        # The section opens with a fenced example of an entry, and that example
        # carries its own `## <Title>` line. A fence is skipped whole, so the
        # illustration cannot be mistaken for the end of the section.
        if stripped.startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            continue
        if stripped.startswith("## ") or stripped == "---":
            break
        if not stripped.startswith("|"):
            if seen_header:
                break  # the table ended
            continue
        cells = _row_cells(stripped)
        if not seen_header:
            if tuple(cells[:2]) == FIELD_TABLE_HEADER:
                seen_header = True
            continue
        if all(set(cell) <= set("-: ") for cell in cells):
            continue  # the header separator
        rows.append(cells)

    specs: List[FieldSpec] = []
    for cells in rows:
        if len(cells) < 2:
            continue
        name = cells[0].strip("`").strip()
        if not name:
            continue
        specs.append(FieldSpec(name, _enum_of(cells[1])))
    if not specs:
        raise core.fail(
            core.E_PARSE,
            "the %r section carries no %s table" % (ENTRY_SCHEMA_HEADING, " | ".join(FIELD_TABLE_HEADER)),
            file=STANDARD_TODO_DISPLAY,
        )
    return specs


def _row_cells(line: str) -> List[str]:
    """One table row's cells, split on **unescaped** pipes only.

    A rating cell writes its alternation as ``` `high` \\| `medium` \\| `low` ```,
    so splitting on every ``|`` would cut that closed set into three separate
    cells and read the field as stating no enum at all.
    """
    parts = _ROW_SPLIT_RE.split(line.strip())
    if parts and not parts[0].strip():
        parts = parts[1:]
    if parts and not parts[-1].strip():
        parts = parts[:-1]
    return [part.replace("\\|", "|").strip() for part in parts]


def _enum_of(cell: str) -> Optional[Tuple[str, ...]]:
    """The closed set a table cell states, or ``None`` when it states none."""
    tokens = [match.group(1).strip() for match in _BACKTICK_RE.finditer(cell)]
    tokens = [token for token in tokens if token]
    if len(tokens) < _MIN_ENUM_MEMBERS:
        return None
    if any(_PLACEHOLDER_MARK in token for token in tokens):
        return None
    ordered: List[str] = []
    for token in tokens:
        if token not in ordered:
            ordered.append(token)
    return tuple(ordered)


_FIELD_CACHE: List[FieldSpec] = []


def field_specs() -> List[FieldSpec]:
    """The shipped standard's field table, read once per process."""
    if not _FIELD_CACHE:
        _FIELD_CACHE.extend(
            parse_field_table(core.read_text(STANDARD_TODO, STANDARD_TODO_DISPLAY))
        )
    return list(_FIELD_CACHE)


def supplied_specs() -> List[FieldSpec]:
    """The fields ``add`` takes as flags -- every row but the derived ones."""
    return [item for item in field_specs() if item.name not in DERIVED_FIELDS]


# ---------------------------------------------------------------------------
# Entries
# ---------------------------------------------------------------------------

#: An entry heading. `## <Title>` and nothing else on the line.
ENTRY_HEADING_RE = re.compile(r"^##[ \t]+(\S.*?)[ \t]*$")

#: One field line inside an entry: `**<Field>:** <value>`.
ENTRY_FIELD_RE = re.compile(r"^\*\*([A-Za-z][A-Za-z0-9-]*):\*\*[ \t]*(.*)$")

#: Any heading line -- an entry ends at the next one whatever its level.
_HEADING_RE = re.compile(r"^#{1,6}[ \t]")


@dataclass(frozen=True)
class TodoEntry:
    """One ``## <Title>`` entry and the field block beneath it."""

    title: str
    fields: Dict[str, str]
    start: int  # 0-based index of the heading line in the text it was parsed from
    end: int  # 0-based exclusive end, up to the next heading or the end of the text

    @property
    def line(self) -> int:
        """The heading's 1-based line number, for a diagnostic."""
        return self.start + 1

    def get(self, name: str) -> str:
        return self.fields.get(name, "")

    def to_dict(self) -> Dict[str, object]:
        """The entry as data: its title and the fields it carries.

        ``start``/``end``/``line`` are indices into whatever text the entry was
        parsed from and are deliberately absent -- published, they would be read
        as file line numbers, which they are only for a caller that parsed the
        whole file.
        """
        payload: Dict[str, object] = {"title": self.title}
        payload.update(self.fields)
        return payload


def parse_entries(text: str) -> List[TodoEntry]:
    """Every entry in ``text``, in document order.

    Indices are into ``text.splitlines()``, so a caller that parsed the whole
    file reads ``entry.line`` as a file line number directly, and one that
    parsed only the body splices on ``start``/``end`` directly.
    """
    lines = text.split("\n")
    entries: List[TodoEntry] = []
    index = 0
    while index < len(lines):
        match = ENTRY_HEADING_RE.match(lines[index])
        if match is None:
            index += 1
            continue
        start = index
        index += 1
        fields: Dict[str, str] = {}
        last: Optional[str] = None
        while index < len(lines) and not _HEADING_RE.match(lines[index]):
            field = ENTRY_FIELD_RE.match(lines[index])
            if field is not None:
                last = field.group(1)
                fields[last] = field.group(2).strip()
            elif last is not None and lines[index].strip():
                # A field's value may wrap; `Context` routinely does.
                fields[last] = ("%s\n%s" % (fields[last], lines[index])).strip()
            elif last is not None and not lines[index].strip():
                last = None  # a blank line ends the wrapped value
            index += 1
        entries.append(TodoEntry(match.group(1).strip(), fields, start, index))
    return entries


def render_entry(title: str, values: Dict[str, str], specs: Sequence[FieldSpec]) -> str:
    """One entry block, fields in the standard's own table order."""
    lines = ["## %s" % title, ""]
    lines.extend("**%s:** %s" % (item.name, values[item.name]) for item in specs)
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Origin resolution -- the same obligation `req change-close` places on the
# CHANGE-<NNN> it records: the named document has to exist.
# ---------------------------------------------------------------------------

REPO_CHANGE_PREFIX = "CHANGE-"
INDEX_CHANGE_PREFIX = "PROJECT-CHANGE-"

#: `CHANGE-<NNN>` / `PROJECT-CHANGE-<NNN>`, with an optional trailing slug --
#: the number is the identity, exactly as it is everywhere else in the tool.
_ORIGIN_CHANGE_RE = re.compile(r"^(PROJECT-)?CHANGE-([0-9]{3,4})(?:-.*)?$")


def resolve_origin(ws: core.Workspace, origin: str) -> Optional[str]:
    """The workspace-relative path ``origin`` names, or ``None`` if it names none.

    A `PROJECT-CHANGE-<NNN>` resolves against the project tier, a
    `CHANGE-<NNN>` against every repo tier in sorted order, and anything else is
    tried as a plan id. Returning the path rather than a boolean is what lets a
    caller report *what* it found.
    """
    if not core.is_ident(origin):
        return None
    match = _ORIGIN_CHANGE_RE.match(origin)
    if match is not None:
        number = match.group(2)
        if match.group(1):
            tiers = [PROJECT_DIRNAME]
            prefix = "%s%s-" % (INDEX_CHANGE_PREFIX, number)
        else:
            tiers = list(ws.targets)  # sorted, and excludes `project`
            prefix = "%s%s-" % (REPO_CHANGE_PREFIX, number)
        for tier in tiers:
            found = _change_named(ws, tier, prefix)
            if found is not None:
                return found
        return None
    directory, escape = ws.resolve_path(CONTEXT_DIRNAME, PROJECT_DIRNAME, PLANS_DIRNAME, origin)
    if escape is None and directory.is_dir():
        return ws.rel(directory)
    return None


def _change_named(ws: core.Workspace, tier: str, prefix: str) -> Optional[str]:
    """The first ``<prefix>*.md`` under ``context/<tier>/changes/``, sorted."""
    directory, escape = ws.resolve_path(CONTEXT_DIRNAME, tier, CHANGES_DIRNAME)
    if escape is not None or not directory.is_dir():
        return None
    for entry in sorted(directory.iterdir(), key=lambda item: item.name):
        if entry.is_file() and entry.name.startswith(prefix) and entry.name.endswith(".md"):
            return ws.rel(entry)
    return None


# ---------------------------------------------------------------------------
# Reading and writing the list
# ---------------------------------------------------------------------------


def todo_path(ws: core.Workspace) -> Path:
    """``context/project/TODO.md`` inside the workspace."""
    return ws.safe_path(*TODO_PARTS)


def read_body(path: Path) -> str:
    """The list's body, front matter removed; ``""`` when the file is absent."""
    if not path.is_file():
        return ""
    return core.strip_front_matter(core.read_text(path, TODO_REL))


def _with_heading(body: str) -> str:
    """``body`` guaranteed to open with the one body heading."""
    text = body.strip("\n")
    if not text:
        return BODY_HEADING + "\n"
    if text.split("\n", 1)[0].strip() != BODY_HEADING:
        return "%s\n\n%s\n" % (BODY_HEADING, text)
    return text + "\n"


def write_document(path: Path, body: str, now: str) -> str:
    """Persist ``body`` under fresh front matter, validated first.

    The single writer for both verbs: front matter is rebuilt rather than
    patched, so the file's shape follows from the standard rather than from
    whatever was there before, and re-writing an unchanged list at the same
    ``--now`` reproduces it byte for byte.
    """
    matter = {TIER_KEY: TIER_VALUE, UPDATED_KEY: now}
    errors = core.validate_against(core.load_schema(TODO_KIND), matter)
    if errors:
        raise core.ToolError(
            core.error(
                core.E_SCHEMA_INVALID,
                "front-matter does not validate against %s: %s" % (TODO_KIND, "; ".join(errors)),
                file=TODO_REL,
            )
        )
    text = core.render_front_matter(matter) + "\n" + _with_heading(body)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return text


# ---------------------------------------------------------------------------
# Field validation -- everything, before anything is written
# ---------------------------------------------------------------------------


def _check_value(item: FieldSpec, value: object) -> Optional[core.Diagnostic]:
    """Presence, format and enum membership for one field. ``None`` when good."""
    if not isinstance(value, str) or not value.strip():
        return core.error(
            E_TODO_FIELD, "%s is required and must not be empty" % (item.name,), file=TODO_REL
        )
    text = value.strip()
    # A value that would parse back as a heading or as a different field cannot
    # round-trip through the entry format, so it is refused rather than written
    # into a file nothing can read back.
    for line in text.split("\n"):
        if _HEADING_RE.match(line) or ENTRY_FIELD_RE.match(line):
            return core.error(
                E_TODO_FIELD,
                "%s must not contain a line the entry format reads as a heading or a field: %r"
                % (item.name, line),
                file=TODO_REL,
            )
    if item.enum is not None and text not in item.enum:
        return core.error(
            E_TODO_ENUM,
            "%s must be one of %s, got %r" % (item.name, ", ".join(item.enum), text),
            file=TODO_REL,
        )
    return None


def check_origin(ws: core.Workspace, value: str) -> Optional[core.Diagnostic]:
    """``Origin`` names a change document or plan that exists. ``None`` when good."""
    if resolve_origin(ws, value) is None:
        return core.error(
            E_TODO_ORIGIN,
            "Origin %r names no change document or plan in this workspace" % (value,),
            file=TODO_REL,
        )
    return None


# ---------------------------------------------------------------------------
# Verbs
# ---------------------------------------------------------------------------


def _add(args, ws: core.Workspace) -> core.Result:
    """``todo add`` -- validate every field, then append. A refusal writes nothing."""
    specs = field_specs()
    supplied = supplied_specs()

    title = getattr(args, "title", None)
    if not isinstance(title, str) or not title.strip():
        raise core.fail(core.E_USAGE, "--title is required and must not be empty")
    title = title.strip()
    if "\n" in title:
        raise core.fail(core.E_USAGE, "--title must be a single line, got %r" % (title,))

    values: Dict[str, str] = {}
    for item in supplied:
        diagnostic = _check_value(item, getattr(args, item.dest, None))
        if diagnostic is not None:
            raise core.ToolError(diagnostic)
        values[item.name] = str(getattr(args, item.dest)).strip()

    diagnostic = check_origin(ws, values[ORIGIN_FIELD])
    if diagnostic is not None:
        raise core.ToolError(diagnostic)

    now = getattr(args, "now", None) or core.system_instant()
    for item in specs:
        if item.name in DERIVED_FIELDS:
            values[item.name] = now

    path = todo_path(ws)
    existed = path.is_file()
    body = _with_heading(read_body(path))
    if any(entry.title == title for entry in parse_entries(body)):
        raise core.ToolError(
            core.error(
                core.E_AMBIGUOUS,
                "an entry titled %r is already on the list; removal is keyed on the title, "
                "so a second one would make `todo remove` undefined" % (title,),
                file=TODO_REL,
            )
        )
    body = body.rstrip("\n") + "\n\n" + render_entry(title, values, specs)
    write_document(path, body, now)

    relative = ws.rel(path)
    return core.Result(
        command="%s.add" % COMMAND,
        data={"path": relative, "title": title, "created": not existed},
    )


def _remove(args, ws: core.Workspace) -> core.Result:
    """``todo remove`` -- resolution is removal, and a no-op removal is a failure."""
    title = getattr(args, "title", None)
    if not isinstance(title, str) or not title.strip():
        raise core.fail(core.E_USAGE, "todo remove requires a title")
    title = title.strip()

    path = todo_path(ws)
    relative = ws.rel(path)
    if not path.is_file():
        raise core.fail(
            core.E_NOT_FOUND, "no entry titled %r: the list does not exist" % (title,), file=TODO_REL
        )

    body = _with_heading(read_body(path))
    matches = [entry for entry in parse_entries(body) if entry.title == title]
    if not matches:
        raise core.fail(core.E_NOT_FOUND, "no entry titled %r" % (title,), file=relative)
    if len(matches) > 1:
        raise core.fail(
            core.E_AMBIGUOUS,
            "%d entries are titled %r; %r names no single entry to delete"
            % (len(matches), title, title),
            file=relative,
        )

    entry = matches[0]
    lines = body.split("\n")
    kept = lines[: entry.start] + lines[entry.end :]
    now = getattr(args, "now", None) or core.system_instant()
    write_document(path, "\n".join(kept), now)

    return core.Result(
        command="%s.remove" % COMMAND,
        data={"path": relative, "title": title, "removed": 1},
    )


def _list(args, ws: core.Workspace) -> core.Result:
    """``todo list`` -- the open entries, newest first, optionally filtered."""
    path = todo_path(ws)
    relative = ws.rel(path)
    entries = parse_entries(_with_heading(read_body(path)))
    # The file is append-ordered, so document order is oldest-first and
    # newest-first is its reverse. No clock is read to establish it.
    entries.reverse()

    filters = {
        item.name: getattr(args, item.dest, None)
        for item in supplied_specs()
        if getattr(args, item.dest, None)
    }
    selected = [
        entry
        for entry in entries
        if all(entry.get(name) == value for name, value in filters.items())
    ]

    data: Dict[str, object] = {"path": relative}
    for item in supplied_specs():
        if hasattr(args, item.dest):
            data[item.dest] = getattr(args, item.dest) or None
    data["count"] = len(selected)
    data["entries"] = [entry.to_dict() for entry in selected]
    return core.Result(command="%s.list" % COMMAND, data=data)


VERBS = {"add": _add, "remove": _remove, "list": _list}

#: The fields ``list`` filters on. Both are named in ``TOOLS-INTERFACE.md``
#: §`todo`: they are how a skill finds the items routed to it.
LIST_FILTER_FIELDS = ("Run", "Kind")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def register(subparsers) -> None:
    """Declare ``todo``'s verbs on ``mc.py``'s subparser action.

    The ``add`` flags are generated from ``STANDARD-TODO.md``'s field table, so
    a field added there gains its flag here without an edit and one renamed
    there cannot leave a stale flag behind.
    """
    parser = subparsers.add_parser(
        COMMAND,
        help="the project deferral list: the only writer of context/project/TODO.md",
        description=(
            "Append, delete and read entries in context/project/TODO.md. Every field is "
            "validated against shared/STANDARD-TODO.md before anything is written, and a "
            "refusal writes nothing -- including the file itself."
        ),
    )
    parser.set_defaults(group=COMMAND, verb=None)
    verbs = parser.add_subparsers(dest="verb", metavar="verb")

    specs = supplied_specs()

    add = verbs.add_parser(
        "add",
        help="append a conforming entry, creating the list if it is absent",
        description=(
            "Validate every field against shared/STANDARD-TODO.md -- the closed enums, and "
            "--origin against the change document or plan it names -- and only then append. "
            "A refusal writes nothing, so a rejected first entry leaves no empty list."
        ),
    )
    add.add_argument("--title", required=True, help="the entry's `## <Title>` heading")
    for item in specs:
        if item.enum is not None:
            help_text = "one of: %s" % ", ".join(item.enum)
        elif item.name == ORIGIN_FIELD:
            help_text = "the CHANGE-<NNN>, PROJECT-CHANGE-<NNN> or plan id this came from"
        else:
            help_text = "the %s field, per shared/STANDARD-TODO.md" % item.name
        add.add_argument(item.flag, required=True, help=help_text)
    add.set_defaults(verb="add")

    remove = verbs.add_parser(
        "remove",
        help="delete the named entry -- the only way an item leaves the list",
        description=(
            "Delete the entry with this title. Resolution is removal: there is no closed "
            "state. A title matching no entry is refused, because a silent no-op removal "
            "reads exactly like a successful one."
        ),
    )
    remove.add_argument("title", help="the entry's `## <Title>` heading, verbatim")
    remove.set_defaults(verb="remove")

    listing = verbs.add_parser(
        "list",
        help="the open entries, newest first",
        description=(
            "Read the open entries, newest first. The filters are how a skill finds the "
            "items routed to it."
        ),
    )
    for item in specs:
        if item.name in LIST_FILTER_FIELDS:
            listing.add_argument(
                item.flag, default=None, help="only entries whose %s is this" % item.name
            )
    listing.set_defaults(verb="list")


def run(args, ws: core.Workspace) -> core.Result:
    """Dispatch one ``todo`` verb. Never raises; failures are diagnostics."""
    verb = getattr(args, "verb", None)
    command = "%s.%s" % (COMMAND, verb) if verb else COMMAND
    handler = VERBS.get(verb)
    if handler is None:
        return core.Result(
            command=command,
            diagnostics=[
                core.error(core.E_USAGE, "todo requires a verb: %s" % (", ".join(sorted(VERBS)),))
            ],
        )
    try:
        return handler(args, ws)
    except core.ToolError as exc:
        return core.Result(command=command, diagnostics=[exc.diagnostic])
