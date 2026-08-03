"""The ``worktree`` command group -- execution naming and reconciliation.

    mc.py worktree names <plan-id> <story-id> --run <n> --attempt <n>
    mc.py worktree reconcile <plan-id> --list-from <file>|-

Two verbs, both pure functions of their arguments plus the workspace bytes.

**This group computes; it never mutates git, and it never runs it.** ``reconcile``
parses the ``git worktree list --porcelain`` output that the *caller* supplies --
on stdin or in a file -- matches each record against the plan's ``state.yaml``,
and returns one ``WorktreeVerdict`` per record. Acting on a verdict is the
invoking skill's job: this is the third of the four documented split steps, where
the tool owns the worktree verdicts and ``mexecute`` keeps the salvage
discretion. Nothing here launches an external program, reads the wall clock, or
writes anything at all.

Three verdicts, and only three (TOOLS-DATAMODEL.md §"Execution naming and
worktrees"):

* ``remove`` -- the story is ``applied`` in ``state.yaml``.
* ``keep``   -- the story is present but not ``applied``, so the tree is
  retained for inspection.
* ``orphan`` -- the work tree is absent from ``state.yaml``, **or** its name does
  not parse.

**An orphan is reported, never removed.** There is deliberately no code path in
this module that turns an ``orphan`` into a removal instruction: every orphan is
built by :func:`_orphan`, which pins the verdict constant, and the reasons it
carries are worded so that no rendering of the envelope -- human or JSON -- can
be read as an instruction to delete anything.

``worktree_path`` -- a plan-level decision
------------------------------------------
``BranchNames`` requires the field, but ``TOOLS-IMPLEMENTATION.md`` §"Worktree
reconciliation" composes only the two branch names and ``names`` takes no
``<repo>`` argument, so the path is not derivable from the verb's arguments
alone. It is resolved here by reading the story's ``repo`` out of the plan graph
at ``context/project/plans/<plan-id>/plan.yaml`` and composing
``repos/<repo>/<story-id>-r<run>-<attempt>`` -- a sibling of the repo's existing
work tree, which is what the bare-repo-plus-worktrees layout of this workspace
wants and what :func:`probe_git_root` exists to confirm. A ``<story-id>`` the
plan graph does not name yields an error diagnostic and ``worktree_path: null``;
an invented path would be worse than none.

No sibling command group is imported. ``state.yaml`` is read through ``core``'s
YAML helpers, exactly as ``plan.py`` reads it.
"""

from __future__ import annotations

import argparse
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

from tools import core

COMMAND = "worktree"

# ---------------------------------------------------------------------------
# Naming -- the one place the two branch names and the path are composed.
# ---------------------------------------------------------------------------

#: The namespace every branch this pipeline creates lives under.
BRANCH_NAMESPACE = "mexec"

#: The leaf of the per-plan integration branch.
INTEGRATION_LEAF = "integration"

#: What ``git worktree list --porcelain`` prefixes a branch ref with.
BRANCH_REF_PREFIX = "refs/heads/"

#: The documented shape of a story branch, quoted back in diagnostics.
STORY_BRANCH_SHAPE = "%s/<plan-id>/<story-id>/r<run>/<attempt>" % BRANCH_NAMESPACE

#: ``mexec/<plan-id>/<story-id>/r<run>/<attempt>``, decomposed.
#:
#: ``run`` and ``attempt`` are matched without leading zeros, so decomposition is
#: the exact inverse of :func:`story_branch`: a name this group would never
#: compose does not parse, and an unparseable name is an ``orphan``.
_STORY_BRANCH_RE = re.compile(
    r"^%s/(?P<plan>[^/]+)/(?P<story>[^/]+)/r(?P<run>[1-9][0-9]*)/(?P<attempt>[1-9][0-9]*)$"
    % (re.escape(BRANCH_NAMESPACE),)
)

# ---------------------------------------------------------------------------
# Workspace layout -- every path this group touches, in one place.
# ---------------------------------------------------------------------------

PLANS_PARTS = ("context", "project", "plans")
PLANS_REL = "context/project/plans"
PLAN_FILE = "plan.yaml"
STATE_FILE = "state.yaml"

REPOS_DIR = "repos"

#: A work tree carries ``.git`` -- a directory in a plain clone, a file holding a
#: ``gitdir:`` pointer in a linked work tree. A bare repository carries neither,
#: which is what makes this probe able to tell them apart without running git.
GIT_MARKER = ".git"

# ---------------------------------------------------------------------------
# Verdicts
# ---------------------------------------------------------------------------

VERDICT_REMOVE = "remove"
VERDICT_KEEP = "keep"
VERDICT_ORPHAN = "orphan"

#: The single story status that authorises removal.
APPLIED_STATUS = "applied"

#: ``WorktreeVerdict`` field order, per TOOLS-DATAMODEL.md.
VERDICT_FIELDS = ("path", "branch", "story_id", "run", "attempt", "verdict", "reason")

#: ``BranchNames`` field order, per TOOLS-DATAMODEL.md.
BRANCH_FIELDS = ("integration", "story", "worktree_path")


def integration_branch(plan_id: str) -> str:
    """``mexec/<plan-id>/integration``."""
    return "%s/%s/%s" % (BRANCH_NAMESPACE, plan_id, INTEGRATION_LEAF)


def story_branch(plan_id: str, story_id: str, run: int, attempt: int) -> str:
    """``mexec/<plan-id>/<story-id>/r<run>/<attempt>``."""
    return "%s/%s/%s/r%d/%d" % (BRANCH_NAMESPACE, plan_id, story_id, run, attempt)


def worktree_dir_name(story_id: str, run: int, attempt: int) -> str:
    """``<story-id>-r<run>-<attempt>`` -- the work tree's directory name."""
    return "%s-r%d-%d" % (story_id, run, attempt)


def worktree_path(repo: str, story_id: str, run: int, attempt: int) -> str:
    """``repos/<repo>/<story-id>-r<run>-<attempt>``, workspace-relative."""
    return "%s/%s/%s" % (REPOS_DIR, repo, worktree_dir_name(story_id, run, attempt))


def decompose(branch: str) -> Optional[Tuple[str, str, int, int]]:
    """``(plan_id, story_id, run, attempt)`` for a story branch, else ``None``.

    ``None`` is the answer for every name that is not exactly what
    :func:`story_branch` composes -- the integration branch, a hand-made branch,
    a name whose plan or story component fails ``Ident``. The caller turns that
    into an ``orphan``; nothing is ever repaired into a usable name here.
    """
    if not isinstance(branch, str):
        return None
    match = _STORY_BRANCH_RE.match(branch)
    if match is None:
        return None
    plan_id = match.group("plan")
    story_id = match.group("story")
    if not core.is_ident(plan_id) or not core.is_ident(story_id):
        return None
    return plan_id, story_id, int(match.group("run")), int(match.group("attempt"))


def strip_ref_prefix(ref: str) -> str:
    """``refs/heads/foo`` -> ``foo``; anything else is returned unchanged."""
    if isinstance(ref, str) and ref.startswith(BRANCH_REF_PREFIX):
        return ref[len(BRANCH_REF_PREFIX) :]
    return ref


# ---------------------------------------------------------------------------
# Repo git-root probing -- filesystem only, sorted, no git
# ---------------------------------------------------------------------------


def probe_git_root(ws: core.Workspace, repo: str) -> Optional[str]:
    """The workspace-relative work tree for ``repo``, or ``None``.

    ``repos/<repo>/`` when it is itself a work tree, otherwise its first
    immediate subdirectory that is one, **in sorted order** -- which is the
    bare-repo-plus-worktrees layout this workspace uses, where ``repos/<repo>/``
    holds a bare repository alongside one directory per checked-out branch.
    Sorting is what keeps the answer independent of filesystem iteration order.

    Purely a probe of the filesystem: no git command is involved, and nothing is
    created. It confirms where the sibling work tree :func:`worktree_path` names
    would sit; it never changes that path.
    """
    core.check_ident(repo, "repo")
    base, diagnostic = ws.resolve_path(REPOS_DIR, repo)
    if diagnostic is not None or base is None or not base.is_dir():
        return None
    if (base / GIT_MARKER).exists():
        return "%s/%s" % (REPOS_DIR, repo)
    for entry in sorted(base.iterdir(), key=lambda item: item.name):
        if entry.is_dir() and (entry / GIT_MARKER).exists():
            return "%s/%s/%s" % (REPOS_DIR, repo, entry.name)
    return None


# ---------------------------------------------------------------------------
# Porcelain parsing -- of output the caller supplies, never output we asked for
# ---------------------------------------------------------------------------


def parse_porcelain(text: str) -> List[Dict[str, str]]:
    """Parse ``git worktree list --porcelain`` output into label/value records.

    The format is one record per work tree, records separated by a blank line,
    each line either a bare label (``bare``, ``detached``) or ``<label> <value>``
    (``worktree <path>``, ``HEAD <sha>``, ``branch <ref>``). A missing separator
    is tolerated -- a second ``worktree`` line starts a new record -- and so is a
    trailing blank line, which real output always has.

    Nothing here raises: a line, or a whole record, that makes no sense is kept
    as-is and becomes a verdict downstream.
    """
    records: List[Dict[str, str]] = []
    current: Dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.rstrip("\r")
        if not line.strip():
            if current:
                records.append(current)
                current = {}
            continue
        label, _sep, value = line.partition(" ")
        if label == "worktree" and current:
            records.append(current)
            current = {}
        if label not in current:  # first line of a repeated label wins
            current[label] = value
    if current:
        records.append(current)
    return records


# ---------------------------------------------------------------------------
# Verdict construction
# ---------------------------------------------------------------------------


def _verdict(
    path: str,
    branch: str,
    story_id: Optional[str],
    run: Optional[int],
    attempt: Optional[int],
    verdict: str,
    reason: str,
) -> Dict[str, Any]:
    """One ``WorktreeVerdict``, in the field order TOOLS-DATAMODEL.md gives."""
    return {
        "path": path,
        "branch": branch,
        "story_id": story_id,
        "run": run,
        "attempt": attempt,
        "verdict": verdict,
        "reason": reason,
    }


def _orphan(
    path: str,
    branch: str,
    reason: str,
    story_id: Optional[str] = None,
    run: Optional[int] = None,
    attempt: Optional[int] = None,
) -> Dict[str, Any]:
    """An ``orphan`` verdict -- reported, and only reported.

    Every orphan in this module is built here, so the verdict constant cannot
    drift and no caller can attach an instruction to act on one.
    """
    return _verdict(path, branch, story_id, run, attempt, VERDICT_ORPHAN, reason)


def verdict_for(record: Dict[str, str], plan_id: str, statuses: Dict[str, str]) -> Dict[str, Any]:
    """The verdict for one parsed porcelain record.

    ``statuses`` is the plan's ``state.yaml`` story table and is the **only**
    source of a story's status: nothing here infers one from the filesystem, from
    whether a branch exists, or from the shape of the path.
    """
    path = record.get("worktree", "")
    if not path:
        return _orphan("", "", "the record carries no work-tree path")

    raw_branch = record.get("branch")
    if raw_branch is None or not raw_branch:
        if "bare" in record:
            return _orphan(path, "", "a bare repository, which carries no story branch")
        if "detached" in record:
            return _orphan(path, "", "the work tree is on a detached HEAD and names no branch")
        return _orphan(path, "", "the record names no branch")

    branch = strip_ref_prefix(raw_branch)
    if branch == integration_branch(plan_id):
        return _orphan(path, branch, "the plan's integration branch, not a story work tree")

    parsed = decompose(branch)
    if parsed is None:
        return _orphan(
            path,
            branch,
            "the branch name does not decompose into %s" % (STORY_BRANCH_SHAPE,),
        )

    branch_plan, story_id, run, attempt = parsed
    if branch_plan != plan_id:
        return _orphan(
            path,
            branch,
            "the branch belongs to plan %r, not %r" % (branch_plan, plan_id),
            story_id=story_id,
            run=run,
            attempt=attempt,
        )

    status = statuses.get(story_id)
    if status is None:
        return _orphan(
            path,
            branch,
            "story %r is absent from %s" % (story_id, STATE_FILE),
            story_id=story_id,
            run=run,
            attempt=attempt,
        )
    if status == APPLIED_STATUS:
        return _verdict(
            path,
            branch,
            story_id,
            run,
            attempt,
            VERDICT_REMOVE,
            "story %r is %s in %s" % (story_id, APPLIED_STATUS, STATE_FILE),
        )
    return _verdict(
        path,
        branch,
        story_id,
        run,
        attempt,
        VERDICT_KEEP,
        "story %r is %s in %s; the work tree is retained for inspection"
        % (story_id, status, STATE_FILE),
    )


# ---------------------------------------------------------------------------
# state.yaml and plan.yaml -- read through core, never through a sibling group
# ---------------------------------------------------------------------------


def _plan_dir_rel(plan_id: str) -> str:
    return "%s/%s" % (PLANS_REL, plan_id)


def story_statuses(
    ws: core.Workspace, plan_id: str, diagnostics: List[core.Diagnostic]
) -> Dict[str, str]:
    """``{story_id: status}`` from ``<plan-dir>/state.yaml``.

    An absent or malformed state file yields an empty table and an error
    diagnostic: every work tree then reads as ``orphan``, which is the safe
    answer -- with no recorded status, nothing is ``applied``.
    """
    relative = "%s/%s" % (_plan_dir_rel(plan_id), STATE_FILE)
    path = ws.safe_path(*(PLANS_PARTS + (plan_id, STATE_FILE)))
    if not path.is_file():
        diagnostics.append(core.error(core.E_NOT_FOUND, "no such file", file=relative))
        return {}
    state = core.load_yaml(path, relative)
    if not isinstance(state, dict):
        diagnostics.append(core.error(core.E_PARSE, "plan state is not a mapping", file=relative))
        return {}
    stories = state.get("stories")
    if not isinstance(stories, dict):
        diagnostics.append(
            core.error(core.E_INVALID_STATE, "plan state names no stories", file=relative)
        )
        return {}
    statuses: Dict[str, str] = {}
    for story_id, entry in stories.items():
        if isinstance(entry, dict) and isinstance(entry.get("status"), str):
            statuses[str(story_id)] = entry["status"]
    return statuses


def story_repo(
    ws: core.Workspace, plan_id: str, story_id: str, diagnostics: List[core.Diagnostic]
) -> Optional[str]:
    """``stories.<story-id>.repo`` from the plan graph, or ``None``.

    ``None`` always comes with an error diagnostic. It is the answer that keeps
    ``worktree_path`` honest: a story the plan graph does not name has no repo,
    and a repo that is guessed is a path that is invented.
    """
    relative = "%s/%s" % (_plan_dir_rel(plan_id), PLAN_FILE)
    path = ws.safe_path(*(PLANS_PARTS + (plan_id, PLAN_FILE)))
    if not path.is_file():
        diagnostics.append(core.error(core.E_NOT_FOUND, "no such file", file=relative))
        return None
    graph = core.load_yaml(path, relative)
    if not isinstance(graph, dict):
        diagnostics.append(core.error(core.E_PARSE, "plan graph is not a mapping", file=relative))
        return None
    stories = graph.get("stories")
    story = stories.get(story_id) if isinstance(stories, dict) else None
    if not isinstance(story, dict):
        diagnostics.append(
            core.error(
                core.E_NOT_FOUND, "plan graph holds no story %r" % (story_id,), file=relative
            )
        )
        return None
    repo = story.get("repo")
    if not isinstance(repo, str) or core.require_ident(repo, "repo") is not None:
        diagnostics.append(
            core.error(
                core.E_INVALID_STATE,
                "story %r names no repo matching /%s/" % (story_id, core.IDENT_PATTERN.strip("^$")),
                file=relative,
            )
        )
        return None
    return repo


# ---------------------------------------------------------------------------
# Verbs
# ---------------------------------------------------------------------------


def _positive(value: Any, what: str) -> int:
    """A counter argument, or ``E_USAGE``. Rejected, never coerced."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise core.fail(core.E_USAGE, "%s requires a positive integer, got %r" % (what, value))
    return value


def _names(args, ws: core.Workspace) -> core.Result:
    """``BranchNames`` -- the two branch names and the work-tree path."""
    command = "%s.names" % COMMAND
    plan_id = core.check_ident(getattr(args, "plan_id", None), "plan-id")
    story_id = core.check_ident(getattr(args, "story_id", None), "story-id")
    run = _positive(getattr(args, "run", None), "--run")
    attempt = _positive(getattr(args, "attempt", None), "--attempt")

    diagnostics: List[core.Diagnostic] = []
    repo = story_repo(ws, plan_id, story_id, diagnostics)
    path: Optional[str] = None
    if repo is not None:
        path = worktree_path(repo, story_id, run, attempt)
        if probe_git_root(ws, repo) is None:
            diagnostics.append(
                core.warning(
                    core.E_NOT_FOUND,
                    "no work tree found under %s/%s; %s names a sibling of one"
                    % (REPOS_DIR, repo, path),
                    file="%s/%s" % (REPOS_DIR, repo),
                )
            )
    data = {
        "integration": integration_branch(plan_id),
        "story": story_branch(plan_id, story_id, run, attempt),
        "worktree_path": path,
    }
    return core.Result(command=command, data=data, diagnostics=diagnostics)


def _read_listing(args, ws: core.Workspace) -> Tuple[str, str]:
    """``(text, display)`` for ``--list-from <file>|-``.

    The listing is always the caller's: either a file inside the workspace or
    whatever was piped in. This group asks git for nothing.
    """
    source = getattr(args, "list_from", None)
    if source is None or str(source) == "":
        raise core.fail(
            core.E_USAGE,
            "reconcile requires --list-from <file>|- carrying "
            "'git worktree list --porcelain' output",
        )
    if str(source) == "-":
        stream = getattr(args, "stdin", None)
        if stream is None:
            stream = sys.stdin
            if hasattr(stream, "isatty") and stream.isatty():
                raise core.fail(
                    core.E_USAGE,
                    "--list-from - reads 'git worktree list --porcelain' output on "
                    "stdin; none was supplied",
                )
        try:
            return stream.read(), "<stdin>"
        except OSError as exc:  # pragma: no cover - unreadable stdin
            raise core.fail(core.E_READ, "cannot read stdin: %s" % (exc,))
    target = ws.safe_path(source)
    display = ws.rel(target)
    return core.read_text(target, display), display


def _reconcile(args, ws: core.Workspace) -> core.Result:
    """``WorktreeVerdict[]`` -- one verdict per record, in listing order."""
    command = "%s.reconcile" % COMMAND
    plan_id = core.check_ident(getattr(args, "plan_id", None), "plan-id")
    text, display = _read_listing(args, ws)
    diagnostics: List[core.Diagnostic] = []
    statuses = story_statuses(ws, plan_id, diagnostics)
    records = parse_porcelain(text)
    if not records:
        diagnostics.append(
            core.warning(core.E_PARSE, "the listing holds no work-tree record", file=display)
        )
    verdicts = [verdict_for(record, plan_id, statuses) for record in records]
    return core.Result(command=command, data=verdicts, diagnostics=diagnostics)


# ---------------------------------------------------------------------------
# argparse wiring
# ---------------------------------------------------------------------------

_NAMES_EPILOG = """\
notes:
  Both identifiers pass Ident before use and are rejected, never sanitized.
  worktree_path is repos/<repo>/<story-id>-r<run>-<attempt>, the repo coming
  from stories.<story-id>.repo in the plan graph; a story the graph does not
  name yields an error and a null path rather than an invented one.
"""

_RECONCILE_EPILOG = """\
input contract:
  The listing is supplied by the caller -- this command runs no git:

      git worktree list --porcelain | mc.py worktree reconcile 003-my-plan --list-from -

  Each record is matched against the plan's state.yaml and yields one verdict:
  remove when the story is applied, keep when it is present but not applied,
  orphan when it is absent from state.yaml or its branch name does not parse.
  An orphan is reported and nothing more -- no removal instruction is ever
  emitted for one.
"""


def register(subparsers) -> None:
    """Declare ``worktree``'s verbs on ``mc.py``'s subparser action."""
    parser = subparsers.add_parser(
        COMMAND,
        help="execution branch/work-tree naming and reconciliation verdicts",
        description=(
            "Compose the branch and work-tree names for a story attempt, and "
            "turn caller-supplied 'git worktree list --porcelain' output into "
            "one verdict per work tree. Computes only: no git command is run "
            "and nothing is removed."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.set_defaults(group=COMMAND, verb=None)
    verbs = parser.add_subparsers(dest="verb", metavar="<verb>")

    names = verbs.add_parser(
        "names",
        help="the integration branch, the story branch, and the work-tree path",
        description="Compose BranchNames for one story attempt.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_NAMES_EPILOG,
    )
    names.add_argument("plan_id", metavar="<plan-id>")
    names.add_argument("story_id", metavar="<story-id>")
    names.add_argument("--run", type=int, required=True, metavar="<n>", help="the run counter")
    names.add_argument(
        "--attempt", type=int, required=True, metavar="<n>", help="the attempt counter"
    )

    reconcile = verbs.add_parser(
        "reconcile",
        help="a verdict per work tree, from caller-supplied porcelain output",
        description="Match a work-tree listing against the plan's state.yaml.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_RECONCILE_EPILOG,
    )
    reconcile.add_argument("plan_id", metavar="<plan-id>")
    reconcile.add_argument(
        "--list-from",
        dest="list_from",
        required=True,
        metavar="<file>|-",
        help="file holding 'git worktree list --porcelain' output, or - for stdin",
    )


VERBS = {"names": _names, "reconcile": _reconcile}


def run(args, ws: core.Workspace) -> core.Result:
    """Dispatch one ``worktree`` verb. Never raises; failures are diagnostics."""
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
