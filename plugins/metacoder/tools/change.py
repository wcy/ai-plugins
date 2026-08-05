"""The ``change`` command group.

    mc.py change resolve <repo> [--slug <slug>]
    mc.py change index-resolve [--slug <slug>]
    mc.py change emit <path> --scope <s> --status <s> --title <t> [--repo <r>]

Two jobs live here, both owned by ``STANDARD-CHANGE.md`` and both previously
re-enacted from prose on every run:

**Sequencing.** ``resolve`` partitions a repo's ``CHANGE-<NNN>-*.md`` files on
``status`` first. A file is *continuable* only when its status is ``pending`` or
``in-progress`` **and** no plan directory corresponds to it. Everything else --
``applied``, ``superseded``, ``complete``, and every ``*-initial-spec.md``
baseline record -- is terminal, *regardless of plan existence*. Exactly one
continuable file means ``continue`` on it; anything else means ``create`` at
``max(<NNN>) + 1``, taken from the highest number present and never from a
count, so a gap in the sequence never renumbers.

Correspondence between a repo change and a plan is **mediated by the project
index, not by number**. Repo change numbers and plan directory numbers are
independent sequences: repo ``CHANGE-005`` is planned by plan directory
``003-deterministic-mechanical-steps``, numbered from ``PROJECT-CHANGE-003``.
Matching on the number would both false-negative and false-positive. The rule
is two hops: a repo change corresponds to a plan when some
``context/project/changes/PROJECT-CHANGE-<NNN>-*.md`` references it in its
**"Repo Change Files"** table *and* a plan directory exists for that index.
``index-resolve`` is unaffected -- at project scope the index number *is* the
plan directory number.

**Emission.** ``emit`` writes the front-matter block and the section skeleton
``STANDARD-CHANGE.md`` requires -- repo-level document, project-level index, or
the reduced initial-spec baseline layout, chosen from the filename. The
front-matter is validated against the ``change-frontmatter`` kind *before*
anything is persisted; a document that would not validate is refused and
nothing reaches disk. The date comes from the injected clock, so emission is
deterministic under a pinned ``--now``.

This module invokes no ``git``, reads no wall clock, and writes nothing outside
the workspace root.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from tools import core

COMMAND = "change"

#: The two statuses a change file may be re-opened from, per
#: ``STANDARD-CHANGE.md`` §"Status Lifecycle". Every other status is terminal.
CONTINUABLE_STATUSES = frozenset({"pending", "in-progress"})

#: ``CHANGE-<NNN>-initial-spec.md`` -- a baseline record, always terminal.
BASELINE_SLUG = "initial-spec"

#: The slug a ``create`` target carries when the caller supplied none. The
#: number is still allocated; the caller substitutes its own slug.
PLACEHOLDER_SLUG = "unnamed"

#: The status a freshly allocated change file is born with.
INITIAL_STATUS = "pending"

#: Front-matter ``plan`` value marking a change document with no code phase to
#: plan or execute, per ``STANDARD-CHANGE.md`` §"No-Plan-Needed Records".
PLAN_NOT_REQUIRED = "not-required"

REPO_PREFIX = "CHANGE-"
INDEX_PREFIX = "PROJECT-CHANGE-"

REPO_CHANGE_RE = re.compile(r"^CHANGE-([0-9]{3,4})-(.+)\.md$")
INDEX_CHANGE_RE = re.compile(r"^PROJECT-CHANGE-([0-9]{3,4})-(.+)\.md$")

#: A reference to a repo-level change file inside a project index's
#: "Repo Change Files" table, in either the workspace-relative form
#: (``context/<repo>/changes/CHANGE-...md``) or a relative link target
#: (``../../<repo>/changes/CHANGE-...md``).
REPO_CHANGE_REF_RE = re.compile(
    r"(?:context/)?(?:\.\./)*([A-Za-z0-9._-]+)/changes/(CHANGE-[0-9]{3,4}-[A-Za-z0-9._-]+\.md)"
)

#: The project index section that names the repo change files it covers.
REPO_CHANGE_SECTION = "Repo Change Files"

CHANGES_DIRNAME = "changes"
CONTEXT_DIRNAME = "context"
PROJECT_DIRNAME = "project"
PLANS_DIRNAME = "plans"


# ---------------------------------------------------------------------------
# TOOLS-DATAMODEL.md §"Change sequencing"
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ChangeRef:
    """One change file on disk, per TOOLS-DATAMODEL.md."""

    scope: str  # "repo" | "project"
    repo: Optional[str]  # null when scope is "project"
    number: str  # zero-padded, e.g. "005"
    slug: str
    path: str  # workspace-relative
    status: str
    baseline: bool  # true for *-initial-spec.md records
    plan_not_required: bool = False  # true when front-matter carries `plan: not-required`

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scope": self.scope,
            "repo": self.repo,
            "number": self.number,
            "slug": self.slug,
            "path": self.path,
            "status": self.status,
            "baseline": self.baseline,
            "plan_not_required": self.plan_not_required,
        }


@dataclass(frozen=True)
class SequenceDecision:
    """create-vs-continue plus the allocation, per TOOLS-DATAMODEL.md."""

    action: str  # "create" | "continue"
    target: ChangeRef
    considered: Tuple[ChangeRef, ...]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action": self.action,
            "target": self.target.to_dict(),
            "considered": [ref.to_dict() for ref in self.considered],
        }


# ---------------------------------------------------------------------------
# Argument declaration
# ---------------------------------------------------------------------------


def register(subparsers) -> None:
    """Declare ``change``'s verbs on ``mc.py``'s subparser action."""
    parser = subparsers.add_parser(
        COMMAND,
        help="resolve the change sequence and emit change documents",
        description=(
            "Create-vs-continue resolution over the change sequence, and emission of the "
            "front-matter and section skeleton STANDARD-CHANGE.md requires."
        ),
    )
    parser.set_defaults(group=COMMAND, verb=None)
    verbs = parser.add_subparsers(dest="verb", metavar="verb")

    resolve = verbs.add_parser(
        "resolve",
        help="create-vs-continue for context/<repo>/changes/",
        description="Resolve the repo-level change sequence for <repo>.",
    )
    resolve.add_argument("repo", help="repo whose context/<repo>/changes/ is sequenced")
    resolve.add_argument(
        "--slug",
        default=None,
        help="slug for the allocated file when the decision is create (default: %s)"
        % (PLACEHOLDER_SLUG,),
    )
    resolve.set_defaults(verb="resolve")

    index_resolve = verbs.add_parser(
        "index-resolve",
        help="create-vs-continue for context/project/changes/",
        description="Resolve the workspace-wide project-level change-index sequence.",
    )
    index_resolve.add_argument(
        "--slug",
        default=None,
        help="slug for the allocated file when the decision is create (default: %s)"
        % (PLACEHOLDER_SLUG,),
    )
    index_resolve.set_defaults(verb="index-resolve")

    emit = verbs.add_parser(
        "emit",
        help="write a change document's front-matter and section skeleton",
        description=(
            "Write the front-matter block and section skeleton STANDARD-CHANGE.md requires. "
            "The front-matter is validated against the change-frontmatter kind before anything "
            "is persisted."
        ),
    )
    emit.add_argument("path", help="workspace-relative path of the document to write")
    emit.add_argument("--scope", required=True, help="front-matter scope")
    emit.add_argument("--status", required=True, help="front-matter status")
    emit.add_argument("--title", required=True, help="document title")
    emit.add_argument(
        "--repo",
        default=None,
        help="the repo the document belongs to; comma-separated for a project-level index",
    )
    emit.add_argument(
        "--plan",
        default=None,
        choices=[PLAN_NOT_REQUIRED],
        help="mark a change with no code phase to plan or execute (%s)" % (PLAN_NOT_REQUIRED,),
    )
    emit.set_defaults(verb="emit")


def run(args, ws) -> core.Result:
    """Dispatch one ``change`` verb. Callable without touching ``argv``."""
    verb = getattr(args, "verb", None)
    handler = _HANDLERS.get(verb)
    if handler is None:
        return core.Result(
            command=COMMAND,
            diagnostics=[
                core.error(
                    core.E_USAGE,
                    "change requires a verb: %s" % (", ".join(sorted(_HANDLERS)),),
                )
            ],
        )
    command = "%s.%s" % (COMMAND, verb)
    diagnostics: List[core.Diagnostic] = []
    try:
        data = handler(args, ws, diagnostics)
    except core.ToolError as exc:
        diagnostics.append(exc.diagnostic)
        return core.Result(command=command, data=None, diagnostics=diagnostics)
    return core.Result(command=command, data=data, diagnostics=diagnostics)


# ---------------------------------------------------------------------------
# resolve / index-resolve
# ---------------------------------------------------------------------------


def _resolve(args, ws, diagnostics: List[core.Diagnostic]) -> Dict[str, Any]:
    """``change resolve <repo>`` -- the repo-level sequence."""
    repo = core.check_ident(getattr(args, "repo", None), "repo")
    slug = _target_slug(args)

    parts = (CONTEXT_DIRNAME, repo, CHANGES_DIRNAME)
    directory = ws.safe_path(*parts)
    considered = _scan(directory, parts, REPO_CHANGE_RE, REPO_PREFIX, "repo", repo, diagnostics)

    planned = _planned_repo_changes(ws, diagnostics)
    continuable = [
        ref
        for ref in considered
        if _is_continuable(ref) and (repo, _basename(ref.path)) not in planned
    ]
    return _decide(considered, continuable, "repo", repo, slug, parts, REPO_PREFIX).to_dict()


def _index_resolve(args, ws, diagnostics: List[core.Diagnostic]) -> Dict[str, Any]:
    """``change index-resolve`` -- the workspace-wide project-level sequence."""
    slug = _target_slug(args)

    parts = (CONTEXT_DIRNAME, PROJECT_DIRNAME, CHANGES_DIRNAME)
    directory = ws.safe_path(*parts)
    considered = _scan(
        directory, parts, INDEX_CHANGE_RE, INDEX_PREFIX, PROJECT_DIRNAME, None, diagnostics
    )

    # At project scope the index number *is* the plan directory number.
    plan_dirs = _plan_dirs(ws)
    continuable = [
        ref
        for ref in considered
        if _is_continuable(ref) and _plan_dir_for(plan_dirs, ref.number, ref.slug) is None
    ]
    return _decide(
        considered, continuable, PROJECT_DIRNAME, None, slug, parts, INDEX_PREFIX
    ).to_dict()


def _target_slug(args) -> str:
    """The slug a ``create`` target carries. Rejected, never sanitized."""
    slug = getattr(args, "slug", None)
    if slug is None:
        return PLACEHOLDER_SLUG
    return core.check_ident(slug, "slug")


def _is_continuable(ref: ChangeRef) -> bool:
    """Status half of the rule. A baseline record is terminal at any status."""
    return ref.status in CONTINUABLE_STATUSES and not ref.baseline


def _decide(
    considered: Sequence[ChangeRef],
    continuable: Sequence[ChangeRef],
    scope: str,
    repo: Optional[str],
    slug: str,
    parts: Sequence[str],
    prefix: str,
) -> SequenceDecision:
    """Exactly one continuable file continues; anything else creates."""
    if len(continuable) == 1:
        return SequenceDecision("continue", continuable[0], tuple(considered))

    # `max + 1` from the highest number present -- never from the count, so a
    # gap in the sequence never renumbers an existing file.
    highest = max([int(ref.number) for ref in considered] or [0])
    number = "%03d" % (highest + 1)
    filename = "%s%s-%s.md" % (prefix, number, slug)
    target = ChangeRef(
        scope=scope,
        repo=repo,
        number=number,
        slug=slug,
        path="/".join(list(parts) + [filename]),
        status=INITIAL_STATUS,
        baseline=_is_baseline(slug),
    )
    return SequenceDecision("create", target, tuple(considered))


def _scan(
    directory: Path,
    parts: Sequence[str],
    pattern,
    prefix: str,
    scope: str,
    repo: Optional[str],
    diagnostics: List[core.Diagnostic],
) -> List[ChangeRef]:
    """Every change file in ``directory``, in sorted order, as ``ChangeRef``s.

    An unreadable or unparseable file produces a warning diagnostic and is kept
    in the listing as terminal -- never a traceback, and never a silent drop
    that would let the next number be allocated over the top of it.
    """
    refs: List[ChangeRef] = []
    if not directory.is_dir():
        return refs
    for entry in sorted(directory.iterdir(), key=lambda item: item.name):
        if not entry.is_file() or not entry.name.startswith(prefix):
            continue
        relative = "/".join(list(parts) + [entry.name])
        match = pattern.match(entry.name)
        if match is None:
            diagnostics.append(
                core.warning(
                    core.E_UNPARSED_NAME,
                    "filename does not match %s<NNN>-<slug>.md; not sequenced" % (prefix,),
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
        status, plan_not_required = _front_matter_of(entry, relative, diagnostics)
        refs.append(
            ChangeRef(
                scope=scope,
                repo=repo,
                number=number,
                slug=slug,
                path=relative,
                status=status,
                baseline=_is_baseline(slug),
                plan_not_required=plan_not_required,
            )
        )
    return refs


def _front_matter_of(
    entry: Path, relative: str, diagnostics: List[core.Diagnostic]
) -> Tuple[str, bool]:
    """The file's front-matter ``status`` (or ``""`` with a warning) and whether it
    carries ``plan: not-required``."""
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
        return "", False
    status = matter.get("status")
    if not status:
        diagnostics.append(
            core.warning(
                core.E_PARSE,
                "front-matter carries no status; treated as terminal",
                file=relative,
            )
        )
        return "", matter.get("plan") == PLAN_NOT_REQUIRED
    return status, matter.get("plan") == PLAN_NOT_REQUIRED


def _is_baseline(slug: str) -> bool:
    return slug == BASELINE_SLUG or slug.endswith("-" + BASELINE_SLUG)


def _basename(path: str) -> str:
    return path.rsplit("/", 1)[-1]


# ---------------------------------------------------------------------------
# The two-hop correspondence: index reference, then plan directory
# ---------------------------------------------------------------------------


def _plan_dirs(ws) -> List[str]:
    """Every plan directory name under ``context/project/plans/``, sorted."""
    directory = ws.safe_path(CONTEXT_DIRNAME, PROJECT_DIRNAME, PLANS_DIRNAME)
    if not directory.is_dir():
        return []
    return sorted(entry.name for entry in directory.iterdir() if entry.is_dir())


def _plan_dir_for(plan_dirs: Sequence[str], number: str, slug: str) -> Optional[str]:
    """The plan directory an index of ``<number>-<slug>`` is planned by."""
    exact = "%s-%s" % (number, slug)
    if exact in plan_dirs:
        return exact
    prefix = number + "-"
    for name in plan_dirs:
        if name.startswith(prefix):
            return name
    return None


def _planned_repo_changes(ws, diagnostics: List[core.Diagnostic]) -> Set[Tuple[str, str]]:
    """``(repo, filename)`` for every repo change a *planned* index references.

    Only an index that itself has a plan directory contributes: a reference
    from an unplanned index leaves the repo change continuable, which is the
    whole point of keying on the index rather than on the number.
    """
    keys: Set[Tuple[str, str]] = set()
    parts = (CONTEXT_DIRNAME, PROJECT_DIRNAME, CHANGES_DIRNAME)
    directory = ws.safe_path(*parts)
    if not directory.is_dir():
        return keys
    plan_dirs = _plan_dirs(ws)
    for entry in sorted(directory.iterdir(), key=lambda item: item.name):
        if not entry.is_file():
            continue
        match = INDEX_CHANGE_RE.match(entry.name)
        if match is None:
            continue
        number, slug = match.group(1), match.group(2)
        if _plan_dir_for(plan_dirs, number, slug) is None:
            continue  # indexed but not planned -- no correspondence yet
        relative = "/".join(list(parts) + [entry.name])
        try:
            text = core.read_text(entry, relative)
        except core.ToolError as exc:
            diagnostics.append(
                core.warning(
                    exc.diagnostic.code,
                    "%s; its repo change references were not read" % (exc.diagnostic.message,),
                    file=relative,
                )
            )
            continue
        body = _section_body(text, REPO_CHANGE_SECTION)
        for repo, filename in REPO_CHANGE_REF_RE.findall(body):
            keys.add((repo, filename))
    return keys


def _section_body(text: str, title: str) -> str:
    """The body of the ``## <title>`` section, up to the next ``#``/``##``."""
    collected: List[str] = []
    inside = False
    for line in text.splitlines():
        if line.startswith("#"):
            level = len(line) - len(line.lstrip("#"))
            heading = line.lstrip("#").strip()
            if inside and level <= 2:
                break
            if level == 2 and heading.lower() == title.lower():
                inside = True
                continue
        if inside:
            collected.append(line)
    return "\n".join(collected)


# ---------------------------------------------------------------------------
# emit -- STANDARD-CHANGE.md owns the shape; this writes it
# ---------------------------------------------------------------------------

#: The section headings of a repo-level change document, in order, per
#: ``STANDARD-CHANGE.md`` §"Change Document Schemas".
REPO_SECTIONS: Tuple[Tuple[str, Optional[Tuple[str, ...]]], ...] = (
    ("Summary", None),
    ("Spec Files Modified", ("File", "Action", "What Changed")),
    ("Breaking Changes", None),
    ("Detailed Changes", None),
    ("Affected Code Paths", ("Spec Change", "Source File(s)", "What to Update")),
    ("Affected Tests", ("Test File", "Action", "What to Test")),
    ("Implementation Order", None),
    ("Validation Checklist", None),
)

#: The reduced layout of an initial-spec baseline record, per
#: ``STANDARD-CHANGE.md`` §"Initial-Spec Baseline Records".
BASELINE_SECTIONS: Tuple[Tuple[str, Optional[Tuple[str, ...]]], ...] = (
    ("Summary", None),
    ("Modules", None),
    ("Spec Files", None),
)

#: The section headings of a project-level index, per §"Change Document
#: Schemas". "Cross-Repo Notes" is omitted for a single-repo change, as that
#: section's own instruction requires.
INDEX_SECTIONS: Tuple[Tuple[str, Optional[Tuple[str, ...]]], ...] = (
    ("Summary", None),
    (REPO_CHANGE_SECTION, ("Repo", "Change File", "Summary")),
)
INDEX_MULTI_REPO_SECTION: Tuple[str, Optional[Tuple[str, ...]]] = ("Cross-Repo Notes", None)


def _emit(args, ws, diagnostics: List[core.Diagnostic]) -> Dict[str, Any]:
    """``change emit`` -- validate, then write; never the other way round."""
    given = getattr(args, "path", None)
    if not given:
        raise core.fail(core.E_USAGE, "change emit requires a path")
    target = ws.safe_path(given)
    relative = ws.rel(target)
    name = Path(str(given)).name

    index_match = INDEX_CHANGE_RE.match(name)
    repo_match = None if index_match else REPO_CHANGE_RE.match(name)
    if index_match is None and repo_match is None:
        raise core.fail(
            core.E_USAGE,
            "filename must be %s<NNN>-<slug>.md or %s<NNN>-<slug>.md" % (REPO_PREFIX, INDEX_PREFIX),
            file=relative,
        )
    match = index_match if index_match is not None else repo_match
    number, slug = match.group(1), match.group(2)
    core.check_ident(slug, "slug")

    repos = _emit_repos(args)
    title = getattr(args, "title", None)
    if not title:
        raise core.fail(core.E_USAGE, "change emit requires --title")
    now = getattr(args, "now", None) or core.system_instant()
    plan = getattr(args, "plan", None)

    if index_match is not None:
        matter = {
            "project-change": number,
            "scope": args.scope,
            "repos": ", ".join(repos),
            "status": args.status,
            "date": now,
        }
        if plan:
            matter["plan"] = plan
        heading = "%s%s: %s" % (INDEX_PREFIX, number, title)
        sections = list(INDEX_SECTIONS)
        if len(repos) > 1:
            sections.append(INDEX_MULTI_REPO_SECTION)
    else:
        if len(repos) != 1:
            raise core.fail(
                core.E_USAGE,
                "a repo-level change document takes exactly one --repo, got %d" % (len(repos),),
                file=relative,
            )
        matter = {
            "change": number,
            "scope": args.scope,
            "repo": repos[0],
            "status": args.status,
            "date": now,
        }
        if plan:
            matter["plan"] = plan
        heading = "%s%s: %s" % (REPO_PREFIX, number, title)
        sections = list(BASELINE_SECTIONS if _is_baseline(slug) else REPO_SECTIONS)

    # Validate before persisting; refuse the write on failure.
    errors = core.validate_against(core.load_schema("change"), matter)
    if errors:
        raise core.ToolError(
            core.error(
                core.E_SCHEMA_INVALID,
                "front-matter does not validate against change-frontmatter: %s"
                % ("; ".join(errors),),
                file=relative,
            )
        )

    text = _document(matter, heading, sections)
    if target.exists():
        existing = core.read_text(target, relative)
        if existing != text:
            raise core.ToolError(
                core.error(
                    core.E_INVALID_STATE,
                    "refusing to overwrite an existing change document",
                    file=relative,
                )
            )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return {"path": relative, "lines": [relative]}


def _emit_repos(args) -> List[str]:
    """``--repo`` as a list. Comma-separated for a project-level index."""
    raw = getattr(args, "repo", None)
    if raw is None or not str(raw).strip():
        raise core.fail(core.E_USAGE, "change emit requires --repo")
    names = [item.strip() for item in str(raw).split(",")]
    return [core.check_ident(name, "repo") for name in names]


def _document(
    matter: Dict[str, str],
    heading: str,
    sections: Sequence[Tuple[str, Optional[Tuple[str, ...]]]],
) -> str:
    """The front-matter block plus the section skeleton, as one document."""
    out = [core.render_front_matter(matter), "\n# %s\n" % (heading,)]
    for title, columns in sections:
        out.append("\n## %s\n" % (title,))
        if columns is not None:
            out.append("\n%s\n%s\n" % (_row(columns), _row(("---",) * len(columns))))
    return "".join(out)


def _row(cells: Sequence[str]) -> str:
    return "| %s |" % (" | ".join(cells),)


_HANDLERS = {
    "resolve": _resolve,
    "index-resolve": _index_resolve,
    "emit": _emit,
}
