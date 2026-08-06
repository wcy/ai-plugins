"""The ``state`` command group -- recording where a run got to.

    mc.py state run-increment <plan-id>
    mc.py state set-plan <plan-id> --status <s>
    mc.py state set-story <plan-id> <story-id> --status <s> [--attempt <n>]
                                               [--branch <b>] [--worktree <w>]
    mc.py state conformance <plan-id> --status <s> --report <path> --findings <n>
    mc.py state telemetry <plan-id> [--cost <usd>] [--tokens <n>]
                                    [--wall-clock <s>]

Five verbs over exactly two files -- ``context/project/plans/<plan-id>/state.yaml``
and the ledger ``context/project/state.yaml`` -- and one shared write path
underneath all of them:

**read, apply one change, serialise, validate the serialisation, then write.**
The validation runs on the bytes that are about to land, not on the object in
memory, and a result that would not validate is refused with **nothing
persisted** -- so a malformed write can never reach disk. The replacement is a
temp-file-then-rename, so a crash mid-write cannot truncate the file either.

Three consequences worth stating outright:

* ``set-story`` derives the containing wave's status from its stories and writes
  it in that same call, for the same reason ``set-plan`` writes two files at
  once: a position maintained separately from what it is a position *of* is a
  position that can disagree with it. The mapping is :func:`wave_status`.
* ``set-plan`` writes ``state.yaml`` *and* the ledger in the same call, and
  validates both before either is written. Leaving the two disagreeing is the
  failure this command exists to prevent. No verb touches another plan's ledger
  entry, and none rewrites a field it was not asked to change -- other than
  ``updated``, which is the write's own timestamp and comes from ``--now``.
* The run counter is only ever read from the file. ``run-increment`` adds one to
  what ``state.yaml`` holds; nothing here generates a counter from a length, a
  timestamp, or an environment value.

No sibling group is imported: this module talks to ``core`` and to the two
schemas, and to nothing else in the package.
"""

from __future__ import annotations

import argparse
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from tools import core

COMMAND = "state"

# ---------------------------------------------------------------------------
# Workspace layout -- every path this group touches, in one place.
# ---------------------------------------------------------------------------

PLANS_DIR = ("context", "project", "plans")
PLANS_REL = "context/project/plans"
LEDGER_PARTS = ("context", "project", "state.yaml")
LEDGER_REL = "context/project/state.yaml"
STATE_FILE = "state.yaml"

#: The two schema kinds every write in this module is validated against.
PLAN_STATE_KIND = "plan-state"
LEDGER_KIND = "project-state"

#: ``plan_id`` -- ``<NNN>-<slug>``, per plan-state.schema.json.
PLAN_ID_PATTERN = r"^[0-9]{3}-[a-z0-9-]+$"
_PLAN_ID_RE = re.compile(PLAN_ID_PATTERN)

#: ``storyId`` -- ``{WW}-{SS}[m]-{REPO}-{MODULE}``, per plan-state.schema.json.
STORY_ID_PATTERN = r"^[0-9]{2}-[0-9]{2}[a-z]?-[A-Za-z0-9._-]+$"
_STORY_ID_RE = re.compile(STORY_ID_PATTERN)

#: The plan/story status lifecycle both schemas share.
STATUSES = ("pending", "in-progress", "applied", "failed")

#: ``conformance.status``, per plan-state.schema.json.
CONFORMANCE_STATUSES = ("clean", "drift", "not-run")

#: ``waves[].status``, per plan-state.schema.json. The two vocabularies differ
#: deliberately: a story reaches ``applied``, a wave reaches ``complete``.
WAVE_STATUSES = ("pending", "in-progress", "complete", "failed")

#: ``--status`` -> ``attempts[].result``. ``pending`` is deliberately absent:
#: ``result`` has no ``pending`` member, so an attempt recorded against a
#: ``pending`` story is refused rather than guessed at.
ATTEMPT_RESULTS = {
    "in-progress": "in-progress",
    "applied": "applied",
    "failed": "failed",
}

#: The suffix a half-written file carries until ``os.replace`` renames it.
TEMP_SUFFIX = ".mc-tmp"


# ---------------------------------------------------------------------------
# argparse wiring
# ---------------------------------------------------------------------------

_EPILOG = """\
guarantees:
  Every verb reads the file, applies one change, serialises it in schema key
  order, validates that serialisation, and only then replaces the file. A
  refused write persists nothing and leaves the file on disk byte-identical.

  set-plan writes state.yaml and the ledger in the same call, validating both
  before either is written, and never touches another plan's ledger entry.

  set-story derives the two attempts[] fields the CLI does not carry: run from
  the file's top-level counter -- which must already have been incremented, an
  attempt never belonging to run 0 -- and result from --status. --status pending
  has no legal result and is refused in combination with --attempt. It also
  derives the containing wave's status from that wave's stories and writes it in
  the same commit, so the two can never disagree; waves[].status is a record,
  nothing reads it back.

  telemetry's three flags are all optional. An omitted one is written null,
  matching plan-state, which requires none of them and types each nullable.
"""


def register(subparsers) -> None:
    """Declare ``state``'s verbs on ``mc.py``'s subparser action."""
    parser = subparsers.add_parser(
        COMMAND,
        help="record where a run got to: run counter, statuses, conformance, telemetry",
        description=(
            "Apply one change to a plan's state.yaml (and, for set-plan, the "
            "project ledger), validating the serialised result against "
            "plan-state / project-state before anything is written."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_EPILOG,
    )
    parser.set_defaults(group=COMMAND, verb=None)
    verbs = parser.add_subparsers(dest="verb", metavar="<verb>")

    increment = verbs.add_parser(
        "run-increment",
        help="increment the top-level run counter and return the new value",
        description=(
            "Read run from state.yaml, add one, persist, and return the new "
            "value. The counter is always read from the file, never generated."
        ),
    )
    increment.add_argument("plan_id", metavar="<plan-id>")

    set_plan = verbs.add_parser(
        "set-plan",
        help="set plan status in state.yaml and the ledger together",
        description=(
            "Set the plan's status in both context/project/plans/<plan-id>/"
            "state.yaml and the plans.<plan-id> ledger entry. Both are "
            "validated before either is written; no other ledger entry is "
            "touched."
        ),
    )
    set_plan.add_argument("plan_id", metavar="<plan-id>")
    set_plan.add_argument("--status", required=True, choices=STATUSES)

    set_story = verbs.add_parser(
        "set-story",
        help="record a story's status and its attempt refs",
        description=(
            "Set the story's status and, when --attempt is given, append or "
            "update the matching attempts[] entry and keep retries consistent "
            "with the attempts recorded."
        ),
    )
    set_story.add_argument("plan_id", metavar="<plan-id>")
    set_story.add_argument("story_id", metavar="<story-id>")
    set_story.add_argument("--status", required=True, choices=STATUSES)
    set_story.add_argument(
        "--attempt",
        type=int,
        default=None,
        metavar="<n>",
        help="attempt number to record against the current run",
    )
    set_story.add_argument(
        "--branch",
        default=None,
        metavar="<b>",
        help="the attempt's branch; required when the attempt is new",
    )
    set_story.add_argument(
        "--worktree",
        default=None,
        metavar="<w>",
        help="the attempt's worktree path",
    )

    conformance = verbs.add_parser(
        "conformance",
        help="write the conformance block",
        description="Write state.yaml's conformance block from a /mverify sweep.",
    )
    conformance.add_argument("plan_id", metavar="<plan-id>")
    conformance.add_argument("--status", required=True, choices=CONFORMANCE_STATUSES)
    conformance.add_argument(
        "--report", required=True, metavar="<path>", help="path to the mverify report"
    )
    conformance.add_argument(
        "--findings", required=True, type=int, metavar="<n>", help="number of findings"
    )

    telemetry = verbs.add_parser(
        "telemetry",
        help="write the telemetry block",
        description="Write state.yaml's telemetry block with the run's rough actuals.",
    )
    telemetry.add_argument("plan_id", metavar="<plan-id>")
    # None of the three is required: plan-state.schema.json gives the telemetry
    # object no required properties and types every field nullable, and the one
    # field a CLI flag most insisted on -- cost -- is the one an agent cannot
    # measure. A required flag satisfiable only by inventing a number is a gate
    # that cannot be passed honestly, and the observable result was not a loud
    # failure but an unwritten block.
    telemetry.add_argument("--cost", default=None, type=float, metavar="<usd>")
    telemetry.add_argument("--tokens", default=None, type=int, metavar="<n>")
    telemetry.add_argument("--wall-clock", default=None, type=float, metavar="<s>")


def run(args, ws) -> core.Result:
    """Dispatch one ``state`` verb. Importable and callable without ``argv``."""
    verb = getattr(args, "verb", None)
    handler = _VERBS.get(verb)
    if handler is None:
        return core.Result(
            command=COMMAND,
            diagnostics=[
                core.error(
                    core.E_USAGE,
                    "state requires a verb: %s" % (", ".join(sorted(_VERBS)),),
                )
            ],
        )
    now = getattr(args, "now", None) or core.system_instant()
    try:
        return handler(args, ws, now)
    except core.ToolError as exc:
        return core.Result(command="%s.%s" % (COMMAND, verb), diagnostics=[exc.diagnostic])


# ---------------------------------------------------------------------------
# Identifier guards -- rejected, never sanitized
# ---------------------------------------------------------------------------


def _check_plan_id(value: Any) -> str:
    """``plan-id`` as ``Ident`` *and* as ``<NNN>-<slug>``. Never rewritten."""
    diagnostic = core.require_ident(value, "plan-id")
    if diagnostic is None and _PLAN_ID_RE.match(value) is None:
        diagnostic = core.error(
            core.E_BAD_IDENT,
            "invalid plan-id %r: must match /%s/ (rejected, never sanitized)"
            % (value, PLAN_ID_PATTERN.strip("^$")),
        )
    if diagnostic is not None:
        raise core.ToolError(diagnostic)
    return value


def _check_story_id(value: Any) -> str:
    """``story-id`` as ``Ident`` first -- a bad one is ``E_BAD_IDENT``, never a
    sanitized key -- then as the ``storyId`` shape the schema will demand."""
    diagnostic = core.require_ident(value, "story-id")
    if diagnostic is None and _STORY_ID_RE.match(value) is None:
        diagnostic = core.error(
            core.E_UNPARSED_NAME,
            "invalid story-id %r: must match /%s/" % (value, STORY_ID_PATTERN.strip("^$")),
        )
    if diagnostic is not None:
        raise core.ToolError(diagnostic)
    return value


def _plan_dir_rel(plan_id: str) -> str:
    return "%s/%s" % (PLANS_REL, plan_id)


def _state_rel(plan_id: str) -> str:
    return "%s/%s" % (_plan_dir_rel(plan_id), STATE_FILE)


# ---------------------------------------------------------------------------
# The read-apply-validate-write core every verb goes through
# ---------------------------------------------------------------------------


@dataclass
class Document:
    """One loaded file, with everything the write path needs about it."""

    data: Dict[str, Any]
    kind: str
    path: Path
    relative: str


def _load_document(ws, parts: Tuple[str, ...], relative: str, kind: str) -> Document:
    """Load one YAML document, or fail with a diagnostic naming the file."""
    path = ws.safe_path(*parts)
    if not path.is_file():
        raise core.ToolError(core.error(core.E_NO_SUCH_FILE, "no such file", file=relative))
    data = core.load_yaml(path, relative)
    if not isinstance(data, dict):
        raise core.ToolError(
            core.error(core.E_PARSE, "%s is not a mapping" % (kind,), file=relative)
        )
    return Document(data, kind, path, relative)


def _load_state(ws, plan_id: str) -> Document:
    """This plan's ``state.yaml``, checked to be the plan it claims to be."""
    relative = _state_rel(plan_id)
    document = _load_document(ws, PLANS_DIR + (plan_id, STATE_FILE), relative, PLAN_STATE_KIND)
    declared = document.data.get("plan_id")
    if isinstance(declared, str) and declared != plan_id:
        raise core.ToolError(
            core.error(
                core.E_INVALID_STATE,
                "state file declares plan_id %r but sits in the directory of %r"
                % (declared, plan_id),
                file=relative,
            )
        )
    return document


def _load_ledger(ws) -> Document:
    return _load_document(ws, LEDGER_PARTS, LEDGER_REL, LEDGER_KIND)


def _commit(documents: List[Document]) -> Tuple[List[str], List[core.Diagnostic]]:
    """Serialise, validate, and only then write -- all of them or none.

    Each document is rendered in its schema's key order and the *rendered text*
    is what gets validated: a value YAML would re-resolve to another type on the
    way back in has to fail here, before any byte reaches the target path. If
    any document is refused, nothing at all is written and every file involved
    stays byte-identical.
    """
    rendered: List[Tuple[Path, str, str]] = []
    diagnostics: List[core.Diagnostic] = []
    for document in documents:
        schema = core.load_schema(document.kind)
        text = core.dump_yaml(document.data, schema)
        errors = core.validate_against(schema, core.parse_yaml(text, document.relative))
        if errors:
            for message in errors:
                diagnostics.append(
                    core.error(
                        core.E_SCHEMA_INVALID,
                        "refused: does not validate against %s: %s" % (document.kind, message),
                        file=document.relative,
                    )
                )
            continue
        rendered.append((document.path, text, document.relative))
    if diagnostics:
        return [], diagnostics
    written: List[str] = []
    for path, text, relative in rendered:
        _atomic_write(path, text)
        written.append(relative)
    return written, []


def _atomic_write(path: Path, text: str) -> None:
    """Write ``text`` to a sibling temp file, then rename it over ``path``.

    A crash between the two leaves the original file intact rather than a
    truncated one, which is the state ``mexecute`` would otherwise resume from.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(
        dir=str(path.parent), prefix="." + path.name + ".", suffix=TEMP_SUFFIX
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
        os.replace(temporary, str(path))
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:  # pragma: no cover - the rename already consumed it
            pass
        raise


def _refused(command: str, data: Dict[str, Any], diagnostics: List[core.Diagnostic]) -> core.Result:
    """The envelope for a write that was refused: nothing written."""
    payload = dict(data)
    payload["written"] = []
    return core.Result(command=command, data=payload, diagnostics=diagnostics)


# ---------------------------------------------------------------------------
# Field readers -- everything derived comes from the file, not from the caller
# ---------------------------------------------------------------------------


def _current_run(document: Document) -> int:
    """The top-level run counter, exactly as ``state.yaml`` holds it."""
    value = document.data.get("run")
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise core.ToolError(
            core.error(
                core.E_INVALID_STATE,
                "run counter is %r; expected a non-negative integer" % (value,),
                file=document.relative,
            )
        )
    return value


def _story_entry(document: Document, story_id: str) -> Dict[str, Any]:
    stories = document.data.get("stories")
    entry = stories.get(story_id) if isinstance(stories, dict) else None
    if not isinstance(entry, dict):
        raise core.ToolError(
            core.error(
                core.E_NOT_FOUND,
                "state file holds no story %r" % (story_id,),
                file=document.relative,
            )
        )
    return entry


def _ledger_entry(document: Document, plan_id: str) -> Dict[str, Any]:
    plans = document.data.get("plans")
    entry = plans.get(plan_id) if isinstance(plans, dict) else None
    if not isinstance(entry, dict):
        raise core.ToolError(
            core.error(
                core.E_NOT_FOUND,
                "the ledger holds no entry for plan %r; it is written by "
                "`plan emit` and is not invented here" % (plan_id,),
                file=document.relative,
            )
        )
    return entry


# ---------------------------------------------------------------------------
# state run-increment
# ---------------------------------------------------------------------------


def _run_increment(args, ws, now) -> core.Result:
    """Add one to the counter the file holds, and return the new value."""
    command = "%s.run-increment" % COMMAND
    plan_id = _check_plan_id(args.plan_id)
    state = _load_state(ws, plan_id)
    current = _current_run(state)
    state.data["run"] = current + 1
    state.data["updated"] = now

    written, diagnostics = _commit([state])
    if diagnostics:
        return _refused(command, {"plan_id": plan_id, "run": current}, diagnostics)
    return core.Result(
        command=command,
        data={"plan_id": plan_id, "run": current + 1, "previous": current, "written": written},
    )


# ---------------------------------------------------------------------------
# state set-plan
# ---------------------------------------------------------------------------


def _set_plan(args, ws, now) -> core.Result:
    """Set plan status in ``state.yaml`` and the ledger, in one call."""
    command = "%s.set-plan" % COMMAND
    plan_id = _check_plan_id(args.plan_id)
    status = args.status

    state = _load_state(ws, plan_id)
    ledger = _load_ledger(ws)
    entry = _ledger_entry(ledger, plan_id)

    state.data["status"] = status
    state.data["updated"] = now
    entry["status"] = status
    entry["updated"] = now
    ledger.data["updated"] = now

    data = {"plan_id": plan_id, "status": status}
    written, diagnostics = _commit([state, ledger])
    if diagnostics:
        return _refused(command, data, diagnostics)
    data["written"] = written
    return core.Result(command=command, data=data)


# ---------------------------------------------------------------------------
# state set-story
# ---------------------------------------------------------------------------


def _set_story(args, ws, now) -> core.Result:
    """Record a story's status and, optionally, one attempt's refs."""
    command = "%s.set-story" % COMMAND
    plan_id = _check_plan_id(args.plan_id)
    story_id = _check_story_id(args.story_id)
    status = args.status
    number = getattr(args, "attempt", None)
    branch = getattr(args, "branch", None)
    worktree = getattr(args, "worktree", None)

    if number is None and (branch is not None or worktree is not None):
        raise core.fail(
            core.E_USAGE,
            "--branch and --worktree record an attempt; supply --attempt <n> as well",
        )
    if number is not None and status not in ATTEMPT_RESULTS:
        raise core.fail(
            core.E_USAGE,
            "--status %s has no legal attempts[].result (%s), so it cannot be "
            "combined with --attempt" % (status, ", ".join(sorted(ATTEMPT_RESULTS))),
        )

    state = _load_state(ws, plan_id)
    story = _story_entry(state, story_id)
    story["status"] = status
    data: Dict[str, Any] = {"plan_id": plan_id, "story_id": story_id, "status": status}

    if number is not None:
        current = _current_run(state)
        if current < 1:
            raise core.ToolError(
                core.error(
                    core.E_INVALID_STATE,
                    "the top-level run counter is 0, and an attempt always "
                    "belongs to a started run; run `state run-increment %s` first"
                    % (plan_id,),
                    file=state.relative,
                )
            )
        _record_attempt(state, story, current, number, status, branch, worktree)
        data["run"] = current
        data["attempt"] = number
        data["retries"] = story.get("retries")

    _apply_wave_status(state, story)
    state.data["updated"] = now
    written, diagnostics = _commit([state])
    if diagnostics:
        return _refused(command, data, diagnostics)
    data["written"] = written
    return core.Result(command=command, data=data)


def wave_status(statuses: Iterable[Optional[str]]) -> str:
    """The wave status a wave's story statuses reduce to -- the whole mapping.

    Stated once, here, and total: every combination of the four story statuses
    lands on exactly one of :data:`WAVE_STATUSES`, and the clauses are ordered
    so that ``failed`` **outranks** ``in-progress`` -- a wave holding one failed
    and one running story is ``failed``, not ``in-progress``.

    * ``failed`` -- any story is ``failed``
    * ``complete`` -- every story is ``applied``
    * ``in-progress`` -- some story has left ``pending``
    * ``pending`` -- otherwise

    A status the schema does not name (a corrupt file, or a story listed in a
    wave the ``stories`` map does not hold) is neither ``failed`` nor
    ``applied``, so it can only hold a wave short of ``complete``; it is never
    read as progress the run did not make.
    """
    values = list(statuses)
    if any(value == "failed" for value in values):
        return "failed"
    if all(value == "applied" for value in values):
        return "complete"
    if any(value != "pending" for value in values):
        return "in-progress"
    return "pending"


def _apply_wave_status(state: Document, story: Dict[str, Any]) -> None:
    """Set the containing wave's status from its stories, in the caller's write.

    Derived rather than accepted as an argument, and written in the same
    ``_commit`` as the story it follows from, so no sequence of calls can leave
    the wave and its stories disagreeing. ``waves[].status`` is a **record, not
    an input**: ``plan resolve`` derives ``resume_wave`` from the plan graph
    crossed with the story statuses and does not read this field.

    A story whose ``wave`` matches no ``waves[]`` entry leaves ``waves``
    untouched and emits no diagnostic. ``plan-state.schema.json`` already
    requires the entry, so that is a defensive branch for a corrupt file, not a
    case with behaviour of its own.
    """
    number = story.get("wave")
    waves = state.data.get("waves")
    stories = state.data.get("stories")
    if not isinstance(waves, list) or not isinstance(stories, dict):
        return
    for entry in waves:
        if not isinstance(entry, dict) or entry.get("wave") != number:
            continue
        # Membership comes from the story records rather than from the wave's
        # own ``stories`` list: the story just written is one of them by
        # construction, so the reduction is never over an empty set, and where
        # the two disagree the records are what a status is a status *of*.
        entry["status"] = wave_status(
            member.get("status")
            for member in stories.values()
            if isinstance(member, dict) and member.get("wave") == number
        )
        return


def _record_attempt(
    state: Document,
    story: Dict[str, Any],
    run: int,
    number: int,
    status: str,
    branch: Optional[str],
    worktree: Optional[str],
) -> None:
    """Append or update this run's attempt ``number`` and re-derive ``retries``.

    Two of the four fields ``attempt`` requires are not CLI options: ``run``
    comes from the file's top-level counter and ``result`` from ``--status``.
    Nothing else about the story is rewritten.
    """
    attempts = story.get("attempts")
    if attempts is None:
        attempts = []
        story["attempts"] = attempts
    if not isinstance(attempts, list):
        raise core.ToolError(
            core.error(
                core.E_INVALID_STATE,
                "story attempts is not a list", file=state.relative,
            )
        )

    existing = None
    for candidate in attempts:
        if not isinstance(candidate, dict):
            continue
        if candidate.get("run") == run and candidate.get("attempt") == number:
            existing = candidate
            break

    if existing is None:
        if branch is None:
            raise core.fail(
                core.E_USAGE,
                "recording attempt %r of run %d requires --branch; branch names "
                "are composed by `worktree names`, not guessed at here"
                % (number, run),
            )
        entry: Dict[str, Any] = {
            "run": run,
            "attempt": number,
            "branch": branch,
            "result": ATTEMPT_RESULTS[status],
        }
        if worktree is not None:
            entry["worktree"] = worktree
        attempts.append(entry)
    else:
        existing["result"] = ATTEMPT_RESULTS[status]
        if branch is not None:
            existing["branch"] = branch
        if worktree is not None:
            existing["worktree"] = worktree

    numbers = [
        candidate["attempt"]
        for candidate in attempts
        if isinstance(candidate, dict)
        and candidate.get("run") == run
        and isinstance(candidate.get("attempt"), int)
        and not isinstance(candidate.get("attempt"), bool)
    ]
    if numbers:
        story["retries"] = max(numbers)


# ---------------------------------------------------------------------------
# state conformance / state telemetry
# ---------------------------------------------------------------------------


def _conformance(args, ws, now) -> core.Result:
    """Write the ``conformance`` block from a ``/mverify`` sweep's result."""
    command = "%s.conformance" % COMMAND
    plan_id = _check_plan_id(args.plan_id)
    state = _load_state(ws, plan_id)
    block = {
        "status": args.status,
        "report": args.report,
        "findings": args.findings,
    }
    state.data["conformance"] = block
    state.data["updated"] = now

    data = {"plan_id": plan_id, "conformance": block}
    written, diagnostics = _commit([state])
    if diagnostics:
        return _refused(command, data, diagnostics)
    data["written"] = written
    return core.Result(command=command, data=data)


def _telemetry(args, ws, now) -> core.Result:
    """Write the ``telemetry`` block -- actuals for the run, never estimates.

    Every field is optional and an omitted one is written ``null``, which is
    what ``plan-state`` already says. Recording two true fields and one ``null``
    is strictly better than recording nothing, and it keeps "actuals, never
    estimates" satisfiable rather than merely stated.
    """
    command = "%s.telemetry" % COMMAND
    plan_id = _check_plan_id(args.plan_id)
    state = _load_state(ws, plan_id)
    block = {
        "cost_usd": getattr(args, "cost", None),
        "tokens": getattr(args, "tokens", None),
        "wall_clock_s": getattr(args, "wall_clock", None),
    }
    state.data["telemetry"] = block
    state.data["updated"] = now

    data = {"plan_id": plan_id, "telemetry": block}
    written, diagnostics = _commit([state])
    if diagnostics:
        return _refused(command, data, diagnostics)
    data["written"] = written
    return core.Result(command=command, data=data)


_VERBS = {
    "run-increment": _run_increment,
    "set-plan": _set_plan,
    "set-story": _set_story,
    "conformance": _conformance,
    "telemetry": _telemetry,
}
