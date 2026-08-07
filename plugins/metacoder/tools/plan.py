"""The ``plan`` command group.

    mc.py plan scope
    mc.py plan resolve [<plan-id>]
    mc.py plan story-id <file>
    mc.py plan waves <target>
    mc.py plan slices <plan-id>
    mc.py plan reslice <plan-id>         # SliceDraft[] as JSON on stdin
    mc.py plan emit <plan-id>            # draft graph as JSON on stdin
    mc.py plan story-emit <plan-id> <story-id>
    mc.py plan shards <plan-id> [--granularity repo|module] [--slice <NN>]

Nine verbs, each a pure function of the workspace bytes plus the injected clock:

* ``scope`` -- ``incremental`` when ``context/project/changes/`` holds a
  ``pending`` index, naming the highest-numbered one; ``full`` otherwise.
* ``resolve`` -- the plan to run, its status, its run counter **as read**, the
  first wave holding an unfinished story, the first slice that is not
  ``applied``, and the unfinished stories.
* ``story-id`` -- the story id a ``PLAN-*.md`` filename carries.
* ``waves`` -- wave assignment from catalog layer order, one wave per layer.
  Its meaning has narrowed: the order it returns now orders stories **within
  one slice**, not across a whole plan.
* ``slices`` -- the plan's slices in order, each with its acceptance, stories
  and current status. A version-1/2 graph has none, so exactly one is
  **synthesized** -- ``00``, spanning every wave -- and the synthesis happens on
  read and is *never persisted*: a legacy graph is not silently upgraded on
  disk, so a plan half-run under the old model stays what it was.
* ``reslice`` -- rewrites the **outstanding** slices from a ``SliceDraft[]``.
* ``emit`` -- ``plan.yaml``, the initial ``state.yaml``, and the ledger entry.
* ``story-emit`` -- a story file rendered from ``shared/PLAN-STORY-TEMPLATE.md``
  with the E2E Testing Hard Rules injected from ``shared/STANDARD-SPEC.md``, and
  each ``validation.increments`` step interleaved into
  ``## Implementation Tasks`` immediately after the task its ``task`` index
  names, so the list reads work-then-check repeated. Two of its substitutions
  are slice-scoped: the ``**Slice:**`` header, and the ``## Slice Acceptance``
  gate, which fires on the last wave *of the story's own slice* -- one gate per
  slice, not one per plan. ``total_waves`` stays plan-global, so a story may read
  ``**Wave:** 2 of 4`` and still carry it.
* ``shards`` -- the ``ShardSpec[]`` conformance shard list ``mverify`` fans out
  over: change-conformance by ``(repo, module)``, then cross-repo by shared
  TAG, then coupling. ``--slice`` restricts it to what that slice shipped.

``reslice`` is the loop's backward edge, and its refusals are the whole of what
keeps delivered work safe: a draft that alters, drops, reorders or renumbers a
slice already ``applied`` is rejected, as is one that would leave a story in no
slice or in two. The graph stays immutable in the sense that matters -- the
record of what was delivered cannot be rewritten -- while what is merely
*predicted* remains revisable, and a plan whose predictions cannot be corrected
is what forces a wrong approach to be finished rather than changed. **A refusal
persists nothing.**

Two boundaries this module keeps deliberately:

* **Computation only, never judgment.** ``waves`` returns layer order and the
  modules of each layer; moving a consumer later than its producer within a
  layer is ``mplan``'s call and is not encoded here. ``resolve`` returns the run
  counter exactly as ``state.yaml`` holds it -- it never generates or increments
  one.
* **One matcher for one concept.** Change documents are read through ``core``'s
  front-matter helper, and the *one* thing borrowed from ``tools/change.py`` is
  the pair that defines what a repo change reference is --
  ``REPO_CHANGE_REF_RE`` over ``section_body(text, REPO_CHANGE_SECTION)``, the
  same pair ``check handoff`` uses. ``scope`` previously carried its own
  stricter pattern, and the two disagreed: a markdown-link ``Repo Change Files``
  table satisfied the handoff while ``scope`` saw nothing and returned an empty
  list at exit 0. No *behaviour* is imported -- no verb of ``change`` is called
  from here, and no other sibling group is reached for at all.

Nothing is persisted until every artifact of the write has validated against its
schema, so a malformed plan, state file, or ledger cannot reach disk.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from tools import change, core

COMMAND = "plan"

# ---------------------------------------------------------------------------
# Workspace layout -- every path this group touches, in one place.
# ---------------------------------------------------------------------------

CHANGES_DIR = ("context", "project", "changes")
PLANS_DIR = ("context", "project", "plans")
LEDGER_PARTS = ("context", "project", "state.yaml")
LEDGER_REL = "context/project/state.yaml"
PLANS_REL = "context/project/plans"

PLAN_FILE = "plan.yaml"
STATE_FILE = "state.yaml"

#: The plan directory ``full`` scope always names.
FULL_PLAN_ID = "000-initial"

#: Schema versions this group writes. A graph carrying ``slices`` is a version-3
#: graph; one that does not stays at 2, so nothing pre-existing is migrated.
PLAN_GRAPH_VERSION = 2
PLAN_STATE_VERSION = 2
PLAN_GRAPH_SLICE_VERSION = 3
PLAN_STATE_SLICE_VERSION = 3

#: The version that declares a graph *continuously checked*: its waves carry
#: ``validation``, the barrier check run against the merged integration branch.
PLAN_GRAPH_BARRIER_VERSION = 4

#: Every graph version that carries ``slices``. The **version** is the
#: discriminator at every step, never the presence of a key, so a version added
#: above 3 has to be added here too or it silently degrades to whole-job
#: delivery -- which is the failure the version-as-discriminator rule exists to
#: prevent. ``state.yaml`` has its own version line and stays at 3: what changed
#: in 4 is the graph's shape, not the state file's.
PLAN_GRAPH_SLICE_VERSIONS = (PLAN_GRAPH_SLICE_VERSION, PLAN_GRAPH_BARRIER_VERSION)
LEDGER_VERSION = 1

#: ``sliceId`` -- ``^[0-9]{2}$``, per plan-graph.schema.json.
SLICE_ID_PATTERN = r"^[0-9]{2}$"
_SLICE_ID_RE = re.compile(SLICE_ID_PATTERN)

#: The slice a version-1/2 graph is read as carrying: one, spanning every wave.
LEGACY_SLICE_ID = "00"
LEGACY_SLICE_NAME = "whole plan"
LEGACY_SLICE_BEHAVIOR = (
    "Every wave of the plan, delivered as one slice -- the model this graph was "
    "written for."
)

#: ``slices[].status`` in ``state.yaml``; a slice with no entry is ``pending``.
SLICE_STATUSES = ("pending", "in-progress", "applied", "failed")
SLICE_APPLIED = "applied"
SLICE_PENDING = "pending"

#: The fields a ``SliceDraft`` carries -- ``SliceEntry`` minus ``status``, which
#: is written by ``state set-slice`` and never supplied by a draft.
SLICE_DRAFT_FIELDS = ("slice", "name", "behavior", "acceptance", "stories")

#: The kind at least one step of every slice's acceptance -- and, on a version-4
#: graph, of every wave's ``validation`` -- must be. JSON Schema cannot express
#: "at least one item has this value", so it is checked here.
EXIT_CODE_KIND = "exit-code"

#: The ``surface`` that same step must carry on a version-4 slice: the interface
#: the behaviour is delivered on, rather than a component beneath it.
#: ``validationStep.surface`` defaults to ``internal``, so a step that does not
#: say ``delivered`` has not claimed it -- which is what stops every step
#: written before the key existed from silently satisfying the obligation.
DELIVERED_SURFACE = "delivered"

#: ``plan_id`` -- ``<NNN>-<slug>``, per plan-graph.schema.json.
PLAN_ID_PATTERN = r"^[0-9]{3}-[a-z0-9-]+$"
_PLAN_ID_RE = re.compile(PLAN_ID_PATTERN)

#: ``storyId`` -- ``{WW}-{SS}[m]-{REPO}-{MODULE}``, per plan-graph.schema.json.
STORY_ID_PATTERN = r"^[0-9]{2}-[0-9]{2}[a-z]?-[A-Za-z0-9._-]+$"
_STORY_ID_RE = re.compile(STORY_ID_PATTERN)

STORY_FILE_PREFIX = "PLAN-"
STORY_FILE_SUFFIX = ".md"

#: ``PROJECT-CHANGE-<NNN>-<slug>.md`` -- the project-level change index.
_INDEX_RE = re.compile(r"^PROJECT-CHANGE-([0-9]{3,4})-(.+)\.md$")

#: A shared-interface spec path named inside a ``scope: shared`` change
#: document -- the ``<IFACE>`` segment is the changed TAG a cross-repo shard
#: covers.
_SHARED_SPEC_TAG_RE = re.compile(r"context/shared/spec/([A-Za-z0-9._-]+)/")

#: ``scope: shared`` is the value that makes a change document's referenced
#: shared interfaces feed the ``cross-repo`` shard kind.
SHARED_SCOPE = "shared"

#: The statuses that make an index drive an incremental plan.
SCOPE_STATUS = "pending"

#: The ledger statuses ``resolve`` treats as unfinished.
UNFINISHED_STATUSES = ("pending", "in-progress")

PLAN_STATUSES = ("pending", "in-progress", "applied", "failed")

# ---------------------------------------------------------------------------
# Plugin-tree data this group renders from (read-only, outside the workspace).
# ---------------------------------------------------------------------------

PLUGIN_ROOT = Path(core.__file__).resolve().parent.parent
SHARED_DIR = PLUGIN_ROOT / "shared"
STORY_TEMPLATE = SHARED_DIR / "PLAN-STORY-TEMPLATE.md"
STANDARD_SPEC = SHARED_DIR / "STANDARD-SPEC.md"

TEMPLATE_BEGIN = "<!-- ===== BEGIN STORY TEMPLATE ===== -->"
TEMPLATE_END = "<!-- ===== END STORY TEMPLATE ===== -->"
INCREMENTAL_BEGIN = "<!-- INCREMENTAL PLANS ONLY"
INCREMENTAL_END = "<!-- END INCREMENTAL SECTION -->"
FINAL_WAVE_GATE = "<!-- Include the section below only in stories belonging to the final wave"
INJECT_MARKER = "<!-- INJECT:E2E-HARD-RULES"
COMPLIANCE_LINE = "**Compliance Status:**"

#: The second injection point. Matched *inside* the comment rather than at its
#: start, because the template's marker deliberately does not open the line: a
#: comment beginning ``<!-- WORD:`` reads as front-matter to
#: :func:`_strip_guidance_comments` and would be emitted verbatim instead of
#: being replaced.
DELIVERED_SURFACE_MARKER = "INJECT:DELIVERED-SURFACE-RULE"

E2E_SECTION_HEADING = "## E2E Testing Hard Rules"
DELIVERED_SURFACE_SECTION_HEADING = "## Delivered-Surface Rule"

#: The section a story's ``validation.increments`` are interleaved into, and the
#: form each one renders as beneath the task it names -- both declared by
#: ``PLAN-STORY-TEMPLATE.md`` §"Implementation Tasks", which tells the agent that
#: reads the story to run the check under a task before starting the next one.
TASKS_HEADING = "## Implementation Tasks"
CHECK_PREFIX = "- *Check:*"

#: A top-level ordered-list item in that section -- one rendered task. Position
#: is the identity, not the printed digit: ``task: 1`` is the first task in the
#: list, exactly as plan-graph.schema.json words it.
_TASK_ITEM_RE = re.compile(r"^([0-9]+\.[ \t]+)\S")

_COMMENT_INLINE_RE = re.compile(r"<!--.*?-->")


# ---------------------------------------------------------------------------
# argparse wiring
# ---------------------------------------------------------------------------

_EMIT_EPILOG = """\
input contract:
  The draft plan graph is read as JSON on stdin:

      mc.py plan emit 003-my-change < draft.json

  The draft carries the judgment mplan owns -- story decomposition, context
  files, target_paths, prerequisites, and validation steps. This verb owns the
  shape: it normalises the draft into plan-graph form with schema key order,
  derives waves, parallel groups, the initial state.yaml and the ledger entry
  from it mechanically, validates all three against plan-graph / plan-state /
  project-state, and only then writes. A draft that would not validate is
  refused and nothing is persisted -- no plan.yaml, no state.yaml, no ledger
  entry. Other plans' ledger entries are never overwritten.
"""

_RESLICE_EPILOG = """\
input contract:
  The slice drafts are read as a JSON array on stdin:

      mc.py plan reslice 003-my-change < slices.json

  Each entry is a SliceDraft -- slice, name, behavior, acceptance, stories --
  and carries no status: a slice's status is written by `state set-slice` and
  is never supplied by a draft. Two obligations JSON Schema cannot state are
  checked here: every story of the graph appears in exactly one slice, and at
  least one acceptance step of every slice is kind: exit-code. A slice already
  applied must survive the draft unaltered, in place and under the same id.
"""

_STORY_EMIT_EPILOG = """\
notes:
  Renders shared/PLAN-STORY-TEMPLATE.md into <plan-dir>/PLAN-<story-id>.md.
  Placeholders the plan graph answers -- repo, module, layer, wave numbers,
  prerequisites, parallel group, change file, target paths -- are substituted;
  the rest are left in place for mplan's judgment. The four E2E Testing Hard
  Rules are injected verbatim from shared/STANDARD-SPEC.md at both
  INJECT:E2E-HARD-RULES markers, gated exactly as the markers are (the repo's
  CATALOG.yaml must name an E2E module or an E2E test-facet file). The four
  Delivered-Surface Rules are injected from that same file at their own
  INJECT:DELIVERED-SURFACE-RULE marker and are ungated: every module has a
  surface its behaviour is delivered on, so there is no catalog condition
  under which the rule does not apply.

  A version-4 story's validation.increments are interleaved into ## Implemen-
  tation Tasks: each step renders as a `- *Check:*` line immediately beneath
  the task its `task` index names, so the list reads work-then-check repeated
  rather than work followed by one closing check. A `task` the rendered list
  does not reach is placed after the last task and reported as a warning --
  never a refusal, since this verb is called once per story in a fan-out.
"""


def register(subparsers) -> None:
    """Declare ``plan``'s verbs on ``mc.py``'s subparser action."""
    parser = subparsers.add_parser(
        COMMAND,
        help="plan scope, resolution, wave assignment, and emission",
        description="Scope, resolve, and emit plans and their story files.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.set_defaults(group=COMMAND, verb=None)
    verbs = parser.add_subparsers(dest="verb", metavar="<verb>")

    verbs.add_parser(
        "scope",
        help="full vs incremental, and the driving project change",
        description=(
            "Report incremental when context/project/changes/ holds a pending "
            "index (naming the highest-numbered one), full otherwise."
        ),
    )

    resolve = verbs.add_parser(
        "resolve",
        help="the plan to run: status, run counter, resume wave, pending stories",
        description=(
            "Resolve a plan explicitly, or take the first ledger plan whose "
            "status is pending or in-progress, falling back to the "
            "highest-numbered plan directory. The run counter is returned as "
            "read -- never generated, never incremented."
        ),
    )
    resolve.add_argument("plan_id", nargs="?", default=None, metavar="<plan-id>")

    story_id = verbs.add_parser(
        "story-id",
        help="the story id a PLAN-*.md filename carries",
        description="Derive the story id from a PLAN-*.md filename.",
    )
    story_id.add_argument("file", metavar="<file>")

    waves = verbs.add_parser(
        "waves",
        help="layer-ordered wave assignment for a target's CATALOG.yaml",
        description=(
            "Assign waves from catalog layer order, one wave per layer, the "
            "modules of a layer being parallel siblings. Assignment only: no "
            "consumer/producer reordering, which stays mplan's judgment."
        ),
    )
    waves.add_argument("target", metavar="<target>")

    slices = verbs.add_parser(
        "slices",
        help="the plan's slices in order, with acceptance, stories and status",
        description=(
            "Read the graph's slices. A version-1/2 graph has none, so exactly "
            "one is synthesized -- 00, spanning every wave, its acceptance the "
            "graph's validation.final. The synthesis happens on read and is "
            "never written back, so a legacy plan is not silently upgraded."
        ),
    )
    slices.add_argument("plan_id", metavar="<plan-id>")

    reslice = verbs.add_parser(
        "reslice",
        help="rewrite the outstanding slices from a SliceDraft[] on stdin",
        description=(
            "Replace the plan's slices with the SliceDraft[] supplied as JSON on "
            "stdin. Refuses a draft that alters, drops, reorders or renumbers an "
            "applied slice, and one that leaves a story in no slice or in two. A "
            "refusal persists nothing."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_RESLICE_EPILOG,
    )
    reslice.add_argument("plan_id", metavar="<plan-id>")

    emit = verbs.add_parser(
        "emit",
        help="write plan.yaml, the initial state.yaml, and the ledger entry",
        description="Emit a plan from the draft graph supplied as JSON on stdin.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_EMIT_EPILOG,
    )
    emit.add_argument("plan_id", metavar="<plan-id>")

    story_emit = verbs.add_parser(
        "story-emit",
        help="write a story file from the shared template, injecting the E2E rules",
        description="Render one story file from shared/PLAN-STORY-TEMPLATE.md.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_STORY_EMIT_EPILOG,
    )
    story_emit.add_argument("plan_id", metavar="<plan-id>")
    story_emit.add_argument("story_id", metavar="<story-id>")

    shards = verbs.add_parser(
        "shards",
        help="the conformance shard list mverify fans out over",
        description=(
            "Derive ShardSpec[] for the plan: change-conformance entries by "
            "(repo, module), cross-repo entries by shared-interface TAG, then "
            "coupling entries -- per repo by default, per (repo, module) with "
            "--granularity module."
        ),
    )
    shards.add_argument("plan_id", metavar="<plan-id>")
    shards.add_argument(
        "--granularity",
        choices=("repo", "module"),
        default="repo",
        help="coupling shard granularity (default: repo)",
    )
    shards.add_argument(
        "--slice",
        dest="slice",
        default=None,
        metavar="<NN>",
        help="restrict the list to what that slice shipped",
    )


def run(args, ws) -> core.Result:
    """Dispatch one ``plan`` verb. Importable and callable without ``argv``."""
    verb = getattr(args, "verb", None)
    handler = _VERBS.get(verb)
    if handler is None:
        return core.Result(
            command=COMMAND,
            diagnostics=[
                core.error(
                    core.E_USAGE,
                    "plan requires a verb: %s" % (", ".join(sorted(_VERBS)),),
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


def _plan_id_diagnostic(value: Any) -> Optional[core.Diagnostic]:
    diagnostic = core.require_ident(value, "plan-id")
    if diagnostic is not None:
        return diagnostic
    if _PLAN_ID_RE.match(value) is None:
        return core.error(
            core.E_BAD_IDENT,
            "invalid plan-id %r: must match /%s/ (rejected, never sanitized)"
            % (value, PLAN_ID_PATTERN.strip("^$")),
        )
    return None


def _check_plan_id(value: Any) -> str:
    diagnostic = _plan_id_diagnostic(value)
    if diagnostic is not None:
        raise core.ToolError(diagnostic)
    return value


def _story_id_diagnostic(value: Any) -> Optional[core.Diagnostic]:
    diagnostic = core.require_ident(value, "story-id")
    if diagnostic is not None:
        return diagnostic
    if _STORY_ID_RE.match(value) is None:
        return core.error(
            core.E_UNPARSED_NAME,
            "invalid story-id %r: must match /%s/" % (value, STORY_ID_PATTERN.strip("^$")),
        )
    return None


def _check_story_id(value: Any) -> str:
    diagnostic = _story_id_diagnostic(value)
    if diagnostic is not None:
        raise core.ToolError(diagnostic)
    return value


def _plan_dir_rel(plan_id: str) -> str:
    return "%s/%s" % (PLANS_REL, plan_id)


# ---------------------------------------------------------------------------
# plan scope
# ---------------------------------------------------------------------------


def _scope(args, ws, now) -> core.Result:
    """``PlanScope`` -- full vs incremental, and the driving project change."""
    command = "%s.scope" % COMMAND
    diagnostics: List[core.Diagnostic] = []
    index = _highest_pending_index(ws, diagnostics)
    if index is None:
        data = {
            "type": "full",
            "plan_id": FULL_PLAN_ID,
            "plan_dir": _plan_dir_rel(FULL_PLAN_ID),
            "project_change": None,
            "change_files": [],
        }
        return core.Result(command=command, data=data, diagnostics=diagnostics)

    number, plan_id, path = index
    change_files = _referenced_change_files(path)
    data = {
        "type": "incremental",
        "plan_id": plan_id,
        "plan_dir": _plan_dir_rel(plan_id),
        "project_change": number,
        "change_files": change_files,
    }
    if not change_files:
        # A warning, not an error, and the scope is still returned: the caller
        # needs to see *which* index came back empty in order to fix it, and
        # failing the command outright withholds exactly that. Silence is the
        # one option not available -- an index a plan cannot scope is a
        # stranded artifact, and a stage reports rather than silently accepts
        # an input its predecessor left incomplete.
        diagnostics.append(
            core.warning(
                core.E_NOT_FOUND,
                "incremental scope on %s references no repo change file under "
                "`## %s`, so this plan would scope nothing"
                % (path.name, change.REPO_CHANGE_SECTION),
                file=_index_rel(path),
            )
        )
    return core.Result(command=command, data=data, diagnostics=diagnostics)


def _index_rel(path: Path) -> str:
    """The workspace-relative path of a project change index."""
    return "%s/%s" % ("/".join(CHANGES_DIR), path.name)


def _highest_pending_index(
    ws, diagnostics: List[core.Diagnostic]
) -> Optional[Tuple[str, str, Path]]:
    """The highest-numbered ``pending`` project change index, or ``None``.

    A file whose ``status`` is anything else -- ``in-progress``, ``applied``,
    ``superseded`` -- does not make a plan incremental.
    """
    directory = ws.safe_path(*CHANGES_DIR)
    if not directory.is_dir():
        return None
    best: Optional[Tuple[int, str, str, Path]] = None
    for entry in sorted(directory.iterdir(), key=lambda item: item.name):
        if not entry.is_file():
            continue
        match = _INDEX_RE.match(entry.name)
        if match is None:
            continue
        number, slug = match.group(1), match.group(2)
        relative = _index_rel(entry)
        matter = core.load_front_matter(entry, relative)
        if matter.get("status") != SCOPE_STATUS:
            continue
        plan_id = "%s-%s" % (number, slug)
        if _plan_id_diagnostic(plan_id) is not None:
            diagnostics.append(
                core.error(
                    core.E_UNPARSED_NAME,
                    "index filename does not yield a plan-id matching /%s/"
                    % (PLAN_ID_PATTERN.strip("^$"),),
                    file=relative,
                )
            )
            continue
        candidate = (int(number), number, plan_id, entry)
        if best is None or candidate[0] > best[0]:
            best = candidate
    if best is None:
        return None
    return best[1], best[2], best[3]


def _referenced_change_files(path: Path) -> List[str]:
    """The repo-level change files an index references, in first-seen order.

    The matcher and the section scoping are ``change``'s, unchanged and not
    copied: ``REPO_CHANGE_REF_RE`` over ``section_body(text,
    REPO_CHANGE_SECTION)`` is exactly what ``check handoff`` reads, so an index
    the handoff calls complete can never scope empty here. The pattern captures
    ``(repo, filename)`` for both table forms -- the backticked
    workspace-relative path and the markdown link with its ``../`` prefix -- so
    the workspace-relative path is rebuilt from the captures rather than taken
    from the matched text, and both forms yield the same string.
    """
    body = change.section_body(core.read_text(path), change.REPO_CHANGE_SECTION)
    seen: List[str] = []
    for repo, filename in change.REPO_CHANGE_REF_RE.findall(body):
        value = "%s/%s/%s/%s" % (
            change.CONTEXT_DIRNAME,
            repo,
            change.CHANGES_DIRNAME,
            filename,
        )
        if value not in seen:
            seen.append(value)
    return seen


# ---------------------------------------------------------------------------
# plan resolve
# ---------------------------------------------------------------------------


def _resolve(args, ws, now) -> core.Result:
    """``PlanResolution`` -- plan, status, run, resume wave, pending stories."""
    command = "%s.resolve" % COMMAND
    diagnostics: List[core.Diagnostic] = []
    explicit = getattr(args, "plan_id", None)
    ledger = _load_ledger(ws)
    if explicit:
        plan_id = _check_plan_id(explicit)
    else:
        plan_id = _first_unfinished_ledger_plan(ledger)
        if plan_id is None:
            plan_id = _highest_numbered_plan_dir(ws)
        if plan_id is None:
            return core.Result(
                command=command,
                diagnostics=[
                    core.error(
                        core.E_NOT_FOUND,
                        "no plan to resolve: the ledger names no pending or "
                        "in-progress plan and %s holds no plan directory" % (PLANS_REL,),
                        file=LEDGER_REL,
                    )
                ],
            )

    plan_dir = ws.safe_path(*(PLANS_DIR + (plan_id,)))
    plan_dir_rel = _plan_dir_rel(plan_id)
    if not plan_dir.is_dir():
        return core.Result(
            command=command,
            diagnostics=[core.error(core.E_NOT_FOUND, "no such plan directory", file=plan_dir_rel)],
        )

    graph_rel = "%s/%s" % (plan_dir_rel, PLAN_FILE)
    graph_path = plan_dir / PLAN_FILE
    if not graph_path.is_file():
        return core.Result(
            command=command,
            diagnostics=[core.error(core.E_NOT_FOUND, "no such file", file=graph_rel)],
        )
    graph = core.load_yaml(graph_path, graph_rel)
    if not isinstance(graph, dict):
        return core.Result(
            command=command,
            diagnostics=[core.error(core.E_PARSE, "plan graph is not a mapping", file=graph_rel)],
        )

    state_rel = "%s/%s" % (plan_dir_rel, STATE_FILE)
    state_path = plan_dir / STATE_FILE
    state: Dict[str, Any] = {}
    if state_path.is_file():
        loaded = core.load_yaml(state_path, state_rel)
        if isinstance(loaded, dict):
            state = loaded
        else:
            diagnostics.append(
                core.error(core.E_PARSE, "plan state is not a mapping", file=state_rel)
            )
    else:
        diagnostics.append(
            core.warning(
                core.E_NOT_FOUND,
                "no state file; reporting run 0 with every story unfinished",
                file=state_rel,
            )
        )

    statuses = _story_statuses(state)
    resume_wave, pending_stories = _resume(graph, statuses)
    entries, _synthesized = slice_entries(graph, state)
    data = {
        "plan_id": plan_id,
        "plan_dir": plan_dir_rel,
        "status": _plan_status(state, ledger, plan_id),
        "run": _run_counter(state),
        "resume_wave": resume_wave,
        # `resume_wave` is deliberately **not** renamed: every existing
        # state.yaml carries it, and renaming it would break resume for every
        # in-flight plan. `resume_slice` is added alongside.
        "resume_slice": _resume_slice(entries),
        "pending_stories": pending_stories,
    }
    return core.Result(command=command, data=data, diagnostics=diagnostics)


def _load_ledger(ws) -> Dict[str, Any]:
    """The project ledger, or an empty mapping when it does not exist."""
    path = ws.safe_path(*LEDGER_PARTS)
    if not path.is_file():
        return {}
    loaded = core.load_yaml(path, LEDGER_REL)
    return loaded if isinstance(loaded, dict) else {}


def _ledger_plans(ledger: Dict[str, Any]) -> Dict[str, Any]:
    plans = ledger.get("plans")
    return plans if isinstance(plans, dict) else {}


def _first_unfinished_ledger_plan(ledger: Dict[str, Any]) -> Optional[str]:
    """The **first** ledger plan whose status is pending or in-progress.

    Ledger order, not numeric order: the file records the plans in the order
    they were emitted, and the first unfinished one is the one to resume.
    """
    for plan_id, entry in _ledger_plans(ledger).items():
        if not isinstance(entry, dict):
            continue
        if entry.get("status") in UNFINISHED_STATUSES and _plan_id_diagnostic(plan_id) is None:
            return plan_id
    return None


def _highest_numbered_plan_dir(ws) -> Optional[str]:
    directory = ws.safe_path(*PLANS_DIR)
    if not directory.is_dir():
        return None
    names = [
        entry.name
        for entry in sorted(directory.iterdir(), key=lambda item: item.name)
        if entry.is_dir() and _plan_id_diagnostic(entry.name) is None
    ]
    if not names:
        return None
    return max(names, key=lambda name: (int(name[:3]), name))


def _story_statuses(state: Dict[str, Any]) -> Dict[str, str]:
    stories = state.get("stories")
    if not isinstance(stories, dict):
        return {}
    statuses: Dict[str, str] = {}
    for story_id, entry in stories.items():
        if isinstance(entry, dict) and isinstance(entry.get("status"), str):
            statuses[story_id] = entry["status"]
    return statuses


def _resume(graph: Dict[str, Any], statuses: Dict[str, str]) -> Tuple[Optional[int], List[str]]:
    """``(resume_wave, pending_stories)`` -- the first wave holding a story that
    is not ``applied``, and every story that is not ``applied`` in wave order."""
    resume: Optional[int] = None
    pending: List[str] = []
    for wave in _graph_waves(graph):
        number = wave.get("wave")
        stories = wave.get("stories")
        if not isinstance(stories, list):
            continue
        unfinished = [
            story_id
            for story_id in stories
            if isinstance(story_id, str) and statuses.get(story_id) != "applied"
        ]
        if unfinished:
            pending.extend(unfinished)
            if resume is None and isinstance(number, int):
                resume = number
    return resume, pending


# ---------------------------------------------------------------------------
# Slices -- read from the graph, or synthesized for a graph written before they
# existed. Synthesis happens on read and is never persisted.
# ---------------------------------------------------------------------------


def graph_story_order(graph: Dict[str, Any]) -> List[str]:
    """Every story the graph declares, in wave order then declared order."""
    ordered: List[str] = []
    for wave in _graph_waves(graph):
        stories = wave.get("stories")
        if not isinstance(stories, list):
            continue
        for story_id in stories:
            if isinstance(story_id, str) and story_id not in ordered:
                ordered.append(story_id)
    declared = graph.get("stories")
    if isinstance(declared, dict):
        for story_id in sorted(declared):
            if story_id not in ordered:
                ordered.append(story_id)
    return ordered


def _final_validation(graph: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The graph's ``validation.final`` steps, in story order, deduplicated.

    ``final`` is carried by the last-wave stories, so the plan's final
    validation is their union -- and that union is what a legacy plan's one
    implicit slice was accepted on.
    """
    steps: List[Dict[str, Any]] = []
    stories = graph.get("stories")
    if not isinstance(stories, dict):
        return steps
    for story_id in graph_story_order(graph):
        story = stories.get(story_id)
        validation = story.get("validation") if isinstance(story, dict) else None
        final = validation.get("final") if isinstance(validation, dict) else None
        if not isinstance(final, list):
            continue
        for step in final:
            if isinstance(step, dict) and step not in steps:
                steps.append(step)
    return steps


def synthesized_slice(graph: Dict[str, Any]) -> Dict[str, Any]:
    """The single slice a version-1/2 graph is read as carrying.

    One slice containing everything is precisely the delivery model such a plan
    was written for, which is why synthesizing beats erroring: it lets a
    pre-existing plan run unchanged.
    """
    return {
        "slice": LEGACY_SLICE_ID,
        "name": LEGACY_SLICE_NAME,
        "behavior": LEGACY_SLICE_BEHAVIOR,
        "acceptance": _final_validation(graph),
        "stories": graph_story_order(graph),
    }


def graph_slices(graph: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], bool]:
    """``(slices, synthesized)`` for a graph, in declared order.

    The **version** is the discriminator, not the presence of the key: sniffing
    for ``slices`` would let a malformed version-3 graph silently degrade to
    whole-job delivery.
    """
    if graph.get("version") in PLAN_GRAPH_SLICE_VERSIONS:
        declared = graph.get("slices")
        entries = [entry for entry in declared if isinstance(entry, dict)] if isinstance(declared, list) else []
        return entries, False
    return [synthesized_slice(graph)], True


def _slice_statuses(state: Dict[str, Any]) -> Dict[str, str]:
    """``slice id -> status`` as ``state.yaml`` records it."""
    entries = state.get("slices")
    statuses: Dict[str, str] = {}
    if not isinstance(entries, list):
        return statuses
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        slice_id, status = entry.get("slice"), entry.get("status")
        if isinstance(slice_id, str) and isinstance(status, str):
            statuses[slice_id] = status
    return statuses


def slice_entries(
    graph: Dict[str, Any], state: Dict[str, Any]
) -> Tuple[List[Dict[str, Any]], bool]:
    """``(SliceEntry[], synthesized)`` -- the graph's slices plus their status.

    A slice's status is **read**, never derived from its stories: a slice whose
    stories all merged but whose acceptance failed is ``failed``, and no
    function of story statuses can express that.
    """
    declared, synthesized = graph_slices(graph)
    statuses = _slice_statuses(state)
    entries: List[Dict[str, Any]] = []
    for item in declared:
        entry = {field: item.get(field) for field in SLICE_DRAFT_FIELDS}
        entry["status"] = statuses.get(str(entry.get("slice")), SLICE_PENDING)
        entries.append(entry)
    return entries, synthesized


def _resume_slice(entries: Sequence[Dict[str, Any]]) -> Optional[str]:
    """The first slice that is not ``applied``; ``None`` when every one is."""
    for entry in entries:
        if entry.get("status") != SLICE_APPLIED:
            slice_id = entry.get("slice")
            return slice_id if isinstance(slice_id, str) else None
    return None


def _graph_waves(graph: Dict[str, Any]) -> List[Dict[str, Any]]:
    waves = graph.get("waves")
    if not isinstance(waves, list):
        return []
    entries = [wave for wave in waves if isinstance(wave, dict)]
    return sorted(entries, key=lambda wave: wave.get("wave") if isinstance(wave.get("wave"), int) else 0)


def _plan_status(state: Dict[str, Any], ledger: Dict[str, Any], plan_id: str) -> str:
    status = state.get("status")
    if status in PLAN_STATUSES:
        return status
    entry = _ledger_plans(ledger).get(plan_id)
    if isinstance(entry, dict) and entry.get("status") in PLAN_STATUSES:
        return entry["status"]
    return "pending"


def _run_counter(state: Dict[str, Any]) -> int:
    """The run counter exactly as ``state.yaml`` holds it."""
    run = state.get("run")
    if isinstance(run, bool) or not isinstance(run, int) or run < 0:
        return 0
    return run


def _load_graph(ws, plan_id: str) -> Tuple[Dict[str, Any], str]:
    """A plan's graph and its workspace-relative path, or a named failure."""
    graph_rel = "%s/%s" % (_plan_dir_rel(plan_id), PLAN_FILE)
    graph_path = ws.safe_path(*(PLANS_DIR + (plan_id, PLAN_FILE)))
    if not graph_path.is_file():
        raise core.ToolError(core.error(core.E_NOT_FOUND, "no such file", file=graph_rel))
    graph = core.load_yaml(graph_path, graph_rel)
    if not isinstance(graph, dict):
        raise core.ToolError(
            core.error(core.E_PARSE, "plan graph is not a mapping", file=graph_rel)
        )
    return graph, graph_rel


def _load_state(ws, plan_id: str, required: bool = False) -> Tuple[Dict[str, Any], str]:
    """A plan's state file, or an empty mapping when it does not exist."""
    state_rel = "%s/%s" % (_plan_dir_rel(plan_id), STATE_FILE)
    state_path = ws.safe_path(*(PLANS_DIR + (plan_id, STATE_FILE)))
    if not state_path.is_file():
        if required:
            raise core.ToolError(
                core.error(core.E_NOT_FOUND, "no such file", file=state_rel)
            )
        return {}, state_rel
    loaded = core.load_yaml(state_path, state_rel)
    if not isinstance(loaded, dict):
        raise core.ToolError(
            core.error(core.E_PARSE, "plan state is not a mapping", file=state_rel)
        )
    return loaded, state_rel


# ---------------------------------------------------------------------------
# plan slices -- read only; the synthesis is never written back
# ---------------------------------------------------------------------------


def _slices(args, ws, now) -> core.Result:
    """The plan's slices in order, each with its status.

    Nothing is written. On a version-1/2 graph the single synthetic slice is
    composed in memory and returned; the file on disk is left byte-identical, so
    a legacy plan is never silently upgraded by being looked at.
    """
    command = "%s.slices" % COMMAND
    plan_id = _check_plan_id(args.plan_id)
    graph, _graph_rel = _load_graph(ws, plan_id)
    state, _state_rel = _load_state(ws, plan_id)
    entries, synthesized = slice_entries(graph, state)
    return core.Result(
        command=command,
        data={
            "plan_id": plan_id,
            "plan_dir": _plan_dir_rel(plan_id),
            "synthesized": synthesized,
            "slices": entries,
        },
    )


# ---------------------------------------------------------------------------
# plan story-id
# ---------------------------------------------------------------------------


def _story_id(args, ws, now) -> core.Result:
    """The story id a ``PLAN-*.md`` filename carries."""
    command = "%s.story-id" % COMMAND
    given = args.file
    name = Path(str(given)).name
    if not name.startswith(STORY_FILE_PREFIX) or not name.endswith(STORY_FILE_SUFFIX):
        return core.Result(
            command=command,
            data={"file": str(given), "story_id": None},
            diagnostics=[
                core.error(
                    core.E_UNPARSED_NAME,
                    "not a story filename: expected %s<story-id>%s"
                    % (STORY_FILE_PREFIX, STORY_FILE_SUFFIX),
                    file=str(given),
                )
            ],
        )
    story_id = name[len(STORY_FILE_PREFIX) : -len(STORY_FILE_SUFFIX)]
    diagnostic = _story_id_diagnostic(story_id)
    if diagnostic is not None:
        if diagnostic.code == core.E_BAD_IDENT:
            raise core.ToolError(diagnostic)
        return core.Result(
            command=command,
            data={"file": str(given), "story_id": None},
            diagnostics=[
                core.Diagnostic(
                    diagnostic.severity, diagnostic.code, diagnostic.message, str(given)
                )
            ],
        )
    return core.Result(command=command, data={"file": str(given), "story_id": story_id})


# ---------------------------------------------------------------------------
# plan waves
# ---------------------------------------------------------------------------


def _waves(args, ws, now) -> core.Result:
    """Wave assignment from catalog layer order -- assignment only.

    One wave per layer, in layer order; the modules of a layer are that wave's
    parallel siblings, kept in the order the catalog declares them. No
    consumer/producer reordering happens here: that is the judgment half of the
    split step and belongs to ``mplan``.
    """
    command = "%s.waves" % COMMAND
    target = core.check_ident(args.target, "target")
    relative = "context/%s/spec/CATALOG.yaml" % target
    path = ws.safe_path("context", target, "spec", "CATALOG.yaml")
    catalog = core.load_yaml(path, relative)
    if not isinstance(catalog, dict):
        return core.Result(
            command=command,
            diagnostics=[core.error(core.E_PARSE, "catalog is not a mapping", file=relative)],
        )
    assignment = _layer_assignment(catalog)
    waves = [
        {"wave": index, "layer": layer, "modules": modules}
        for index, (layer, modules) in enumerate(assignment, start=1)
    ]
    data = {"target": target, "catalog": relative, "waves": waves}
    diagnostics = []
    if not waves:
        diagnostics.append(
            core.error(core.E_INVALID_STATE, "catalog declares no module", file=relative)
        )
    return core.Result(command=command, data=data, diagnostics=diagnostics)


def _layer_assignment(catalog: Dict[str, Any]) -> List[Tuple[str, List[str]]]:
    """``[(layer, modules)]`` in layer order, modules in catalog order."""
    interfaces = catalog.get("interfaces")
    if isinstance(interfaces, dict) and not catalog.get("modules"):
        # A shared contract catalog: one layer, every interface a sibling.
        return [("LS-shared", sorted(interfaces))] if interfaces else []

    ordered: List[Tuple[str, List[str]]] = []
    index: Dict[str, List[str]] = {}
    layers = catalog.get("layers")
    if isinstance(layers, dict):
        for layer, entry in layers.items():
            declared = entry.get("modules") if isinstance(entry, dict) else None
            names = [name for name in declared if isinstance(name, str)] if isinstance(declared, list) else []
            index.setdefault(layer, [])
            for name in names:
                if name not in index[layer]:
                    index[layer].append(name)
    modules = catalog.get("modules")
    if isinstance(modules, dict):
        for name in sorted(modules):
            entry = modules[name]
            layer = entry.get("layer") if isinstance(entry, dict) else None
            if not isinstance(layer, str):
                continue
            index.setdefault(layer, [])
            if name not in index[layer]:
                index[layer].append(name)
    for layer in sorted(index, key=_layer_sort_key):
        if index[layer]:
            ordered.append((layer, index[layer]))
    return ordered


def _layer_sort_key(layer: str) -> Tuple[int, str]:
    """``LS-shared`` first, then ``L0`` .. ``Ln``; ties broken by name."""
    match = re.match(r"^L(S|[0-9]+)-", layer)
    if match is None:
        return (1 << 16, layer)
    token = match.group(1)
    return (-1, layer) if token == "S" else (int(token), layer)


# ---------------------------------------------------------------------------
# plan emit
# ---------------------------------------------------------------------------


def _emit(args, ws, now) -> core.Result:
    """Write ``plan.yaml``, the initial ``state.yaml``, and the ledger entry.

    The draft graph arrives as JSON on stdin (``args.stdin`` when the group is
    called directly). Every file is rendered and validated before any is
    written, so a refusal persists nothing.
    """
    command = "%s.emit" % COMMAND
    plan_id = _check_plan_id(args.plan_id)
    plan_dir_rel = _plan_dir_rel(plan_id)
    draft = _read_draft(args)
    if not isinstance(draft, dict):
        return core.Result(
            command=command,
            data={"plan_id": plan_id, "plan_dir": plan_dir_rel, "written": []},
            diagnostics=[
                core.error(core.E_PARSE, "the draft plan graph must be a JSON object", file="<stdin>")
            ],
        )
    declared = draft.get("plan_id")
    if isinstance(declared, str) and declared != plan_id:
        return core.Result(
            command=command,
            data={"plan_id": plan_id, "plan_dir": plan_dir_rel, "written": []},
            diagnostics=[
                core.error(
                    core.E_INVALID_STATE,
                    "draft plan_id %r does not match the plan-id argument %r"
                    % (declared, plan_id),
                    file="<stdin>",
                )
            ],
        )

    graph, refusals = _normalize_graph(draft, plan_id, now, ws)
    if refusals:
        return core.Result(
            command=command,
            data={"plan_id": plan_id, "plan_dir": plan_dir_rel, "written": []},
            diagnostics=refusals,
        )
    state = _derive_plan_state(graph, now)
    ledger = _derive_ledger(_load_ledger(ws), graph, now)

    plan_rel = "%s/%s" % (plan_dir_rel, PLAN_FILE)
    state_rel = "%s/%s" % (plan_dir_rel, STATE_FILE)
    pending: List[Tuple[Path, str, str]] = []
    diagnostics: List[core.Diagnostic] = []
    for data, kind, relative, parts in (
        (graph, "plan-graph", plan_rel, PLANS_DIR + (plan_id, PLAN_FILE)),
        (state, "plan-state", state_rel, PLANS_DIR + (plan_id, STATE_FILE)),
        (ledger, "project-state", LEDGER_REL, LEDGER_PARTS),
    ):
        schema = core.load_schema(kind)
        text = core.dump_yaml(data, schema)
        # Validate what will actually be on disk, not the object in memory: a
        # value that YAML would re-resolve to another type (an unquoted digit
        # run) has to fail here, before anything is written.
        errors = core.validate_against(schema, core.parse_yaml(text, relative))
        if errors:
            for message in errors:
                diagnostics.append(
                    core.error(
                        core.E_SCHEMA_INVALID,
                        "refused: does not validate against %s: %s" % (kind, message),
                        file=relative,
                    )
                )
            continue
        pending.append((ws.safe_path(*parts), text, relative))

    if diagnostics:
        return core.Result(
            command=command,
            data={"plan_id": plan_id, "plan_dir": plan_dir_rel, "written": []},
            diagnostics=diagnostics,
        )
    written: List[str] = []
    for path, text, relative in pending:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        written.append(relative)
    return core.Result(
        command=command,
        data={"plan_id": plan_id, "plan_dir": plan_dir_rel, "written": written},
    )


def _read_draft(args, what: str = "the draft plan graph") -> Any:
    """Parse a JSON draft from stdin (or ``args.stdin``)."""
    stream = getattr(args, "stdin", None)
    if stream is None:
        stream = sys.stdin
        if hasattr(stream, "isatty") and stream.isatty():
            raise core.fail(
                core.E_USAGE,
                "this verb reads %s as JSON on stdin; none was supplied" % (what,),
            )
    try:
        text = stream.read()
    except OSError as exc:  # pragma: no cover - unreadable stdin
        raise core.fail(core.E_READ, "cannot read stdin: %s" % (exc,))
    if not text.strip():
        raise core.fail(core.E_PARSE, "%s on stdin is empty" % (what,), file="<stdin>")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise core.fail(core.E_PARSE, "invalid JSON: %s" % (exc,), file="<stdin>", line=exc.lineno)


def _normalize_graph(
    draft: Dict[str, Any], plan_id: str, now: str, ws=None
) -> Tuple[Dict[str, Any], List[core.Diagnostic]]:
    """Normalise an untrusted draft into ``plan-graph`` shape.

    Everything mechanical is derived here -- waves from each story's ``wave``,
    parallel groups from the wave's siblings, ``repos`` from the stories, the
    story filename from its id, and each story's ``slice`` from the slice that
    lists it -- and everything else is carried through for the schema to accept
    or refuse.
    """
    graph: Dict[str, Any] = dict(draft)
    raw_slices = draft.get("slices")
    sliced = isinstance(raw_slices, list) and bool(raw_slices)
    graph["version"] = draft.get(
        "version", PLAN_GRAPH_SLICE_VERSION if sliced else PLAN_GRAPH_VERSION
    )
    graph["plan_id"] = plan_id
    graph["generated"] = now
    graph["project_change"] = _normalize_change_number(draft.get("project_change"))
    if "type" not in graph:
        graph["type"] = "incremental" if graph["project_change"] else "full"

    raw_stories = draft.get("stories")
    if not isinstance(raw_stories, dict):
        return graph, [
            core.error(
                core.E_INVALID_STATE,
                "the draft plan graph must carry a 'stories' object",
                file="<stdin>",
            )
        ]

    stories: Dict[str, Any] = {}
    for story_id in sorted(raw_stories):
        stories[story_id] = raw_stories[story_id]
    wave_members = _wave_members(stories)
    waves, refusals = _reconcile_waves(draft.get("waves"), wave_members)
    if refusals:
        return graph, refusals
    refusals = _wave_barrier_checks(graph["version"], waves)
    if refusals:
        return graph, refusals

    normalized: Dict[str, Any] = {}
    for story_id, raw in stories.items():
        normalized[story_id] = _normalize_story(story_id, raw, waves)
    refusals = _story_increment_checks(graph["version"], normalized)
    if refusals:
        return graph, refusals
    graph["stories"] = normalized
    graph["waves"] = waves
    if "repos" not in graph:
        graph["repos"] = sorted(
            {
                story["repo"]
                for story in normalized.values()
                if isinstance(story, dict) and isinstance(story.get("repo"), str)
            }
        )

    if not sliced:
        graph.pop("slices", None)
        for story in normalized.values():
            if isinstance(story, dict):
                story.pop("slice", None)
        return graph, []

    slices, refusals = _normalize_slices(raw_slices, normalized)
    if refusals:
        return graph, refusals
    refusals = _slice_surface_checks(graph["version"], slices)
    if refusals:
        return graph, refusals
    refusals = _slice_zero_spans_every_layer(slices, normalized, ws)
    if refusals:
        return graph, refusals
    graph["slices"] = slices
    _apply_story_slices(slices, normalized)
    return graph, []


def _normalize_slices(
    raw: Sequence[Any], stories: Dict[str, Any]
) -> Tuple[List[Dict[str, Any]], List[core.Diagnostic]]:
    """Slices in draft order, with the two obligations JSON Schema cannot state.

    Both are mechanical consequences of the draft rather than judgment, which is
    why they are refused here and not left to ``mplan``'s prose: an acceptance
    that is prose alone forces a human stop under every gate policy but
    ``never``, and a story in no slice or in two makes the delivery axis
    ambiguous.
    """
    slices: List[Dict[str, Any]] = []
    seen: List[str] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            return [], [
                core.error(
                    core.E_INVALID_STATE,
                    "slice %d of the draft is not an object" % (index,),
                    file="<stdin>",
                )
            ]
        entry = {field: item[field] for field in SLICE_DRAFT_FIELDS if field in item}
        for field, value in item.items():
            if field not in entry:
                entry[field] = value
        slice_id = entry.get("slice")
        if not isinstance(slice_id, str) or _SLICE_ID_RE.match(slice_id) is None:
            return [], [
                core.error(
                    core.E_BAD_IDENT,
                    "slice id %r must match /%s/" % (slice_id, SLICE_ID_PATTERN.strip("^$")),
                    file="<stdin>",
                )
            ]
        if slice_id in seen:
            return [], [
                core.error(
                    core.E_INVALID_STATE,
                    "slice %r is declared more than once" % (slice_id,),
                    file="<stdin>",
                )
            ]
        seen.append(slice_id)
        acceptance = entry.get("acceptance")
        if not isinstance(acceptance, list) or not any(
            isinstance(step, dict) and step.get("kind") == EXIT_CODE_KIND
            for step in acceptance
        ):
            return [], [
                core.error(
                    core.E_INVALID_STATE,
                    "slice %r has no `kind: %s` acceptance step; a slice whose "
                    "acceptance is prose alone cannot be demonstrated to run"
                    % (slice_id, EXIT_CODE_KIND),
                    file="<stdin>",
                )
            ]
        slices.append(entry)

    membership: Dict[str, List[str]] = {}
    for entry in slices:
        listed = entry.get("stories")
        for story_id in listed if isinstance(listed, list) else []:
            if isinstance(story_id, str):
                membership.setdefault(story_id, []).append(str(entry.get("slice")))
    diagnostics: List[core.Diagnostic] = []
    for story_id in sorted(stories):
        owners = membership.get(story_id, [])
        if not owners:
            diagnostics.append(
                core.error(
                    core.E_INVALID_STATE,
                    "story %r belongs to no slice" % (story_id,),
                    file="<stdin>",
                )
            )
        elif len(owners) > 1:
            diagnostics.append(
                core.error(
                    core.E_INVALID_STATE,
                    "story %r belongs to more than one slice (%s)"
                    % (story_id, ", ".join(owners)),
                    file="<stdin>",
                )
            )
    for story_id in sorted(membership):
        if story_id not in stories:
            diagnostics.append(
                core.error(
                    core.E_INVALID_STATE,
                    "slice %s lists story %r, which the graph does not declare"
                    % (membership[story_id][0], story_id),
                    file="<stdin>",
                )
            )
    if diagnostics:
        return [], diagnostics
    return slices, []


def _apply_story_slices(slices: Sequence[Dict[str, Any]], stories: Dict[str, Any]) -> None:
    """Write each story's ``slice`` back-reference from the slice listing it."""
    for entry in slices:
        listed = entry.get("stories")
        for story_id in listed if isinstance(listed, list) else []:
            story = stories.get(story_id)
            if isinstance(story, dict):
                story["slice"] = entry.get("slice")


def _story_layers(stories: Dict[str, Any], ws) -> Dict[str, Optional[str]]:
    """``story id -> layer``, read from each story's repo catalog.

    A repo with no catalog yields ``None`` for its stories, which drops out of
    the spanning comparison rather than inventing a layer to compare against.
    """
    catalogs: Dict[str, Dict[str, Any]] = {}
    layers: Dict[str, Optional[str]] = {}
    for story_id, story in stories.items():
        if not isinstance(story, dict):
            layers[story_id] = None
            continue
        repo, module = story.get("repo"), story.get("module")
        if ws is None or not isinstance(repo, str) or core.require_ident(repo, "repo") is not None:
            layers[story_id] = None
            continue
        if repo not in catalogs:
            path, escape = ws.resolve_path("context", repo, "spec", "CATALOG.yaml")
            catalog: Dict[str, Any] = {}
            if escape is None and path is not None and path.is_file():
                loaded = core.load_yaml(path, "context/%s/spec/CATALOG.yaml" % repo)
                catalog = loaded if isinstance(loaded, dict) else {}
            catalogs[repo] = catalog
        layers[story_id] = _module_layer(catalogs[repo], module)
    return layers


def _slice_zero_spans_every_layer(
    slices: Sequence[Dict[str, Any]], stories: Dict[str, Any], ws
) -> List[core.Diagnostic]:
    """The walking-skeleton rule: the first slice touches every layer the plan does.

    This is what makes slice ``00`` a walking skeleton rather than merely the
    first slice somebody listed -- its acceptance proves the shape of the system
    runs before any depth is built on it.
    """
    if not slices:
        return []
    layers = _story_layers(stories, ws)
    reached = {layer for layer in layers.values() if layer}
    listed = slices[0].get("stories")
    first = {
        layers.get(story_id)
        for story_id in (listed if isinstance(listed, list) else [])
        if layers.get(story_id)
    }
    missing = sorted(reached - first)
    if not missing:
        return []
    return [
        core.error(
            core.E_INVALID_STATE,
            "slice %r is the walking skeleton and must touch every layer the plan "
            "touches, but reaches none of: %s"
            % (slices[0].get("slice"), ", ".join(missing)),
            file="<stdin>",
        )
    ]


def _normalize_change_number(value: Any) -> Optional[str]:
    """``project_change`` as a string -- never a bare digit run in the YAML."""
    if value is None:
        return None
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        return "%03d" % value
    return value


def _wave_members(stories: Dict[str, Any]) -> Dict[int, List[str]]:
    members: Dict[int, List[str]] = {}
    for story_id, raw in stories.items():
        wave = raw.get("wave") if isinstance(raw, dict) else None
        if isinstance(wave, bool) or not isinstance(wave, int):
            continue
        members.setdefault(wave, []).append(story_id)
    for wave in members:
        members[wave].sort()
    return members


def _reconcile_waves(
    declared: Any, members: Dict[int, List[str]]
) -> Tuple[List[Dict[str, Any]], List[core.Diagnostic]]:
    """Waves derived from the stories, honouring a consistent declared order.

    The derivation is authoritative: a declared ``waves`` block whose membership
    disagrees with the stories' own ``wave`` fields would put an inconsistent
    graph on disk, so it is refused rather than silently rewritten.
    """
    derived = [{"wave": wave, "stories": list(members[wave])} for wave in sorted(members)]
    if not isinstance(declared, list):
        return derived, []
    ordering: Dict[int, List[str]] = {}
    barriers: Dict[int, Any] = {}
    for entry in declared:
        if not isinstance(entry, dict):
            continue
        wave = entry.get("wave")
        stories = entry.get("stories")
        if isinstance(wave, bool) or not isinstance(wave, int) or not isinstance(stories, list):
            continue
        ordering[wave] = [story for story in stories if isinstance(story, str)]
        if "validation" in entry:
            # Carried through, never derived: the barrier check is an assertion
            # about what must hold once the wave is merged, which the stories
            # cannot supply. Carrying it on a version below 4 is what lets the
            # schema *refuse* it there rather than have it silently dropped.
            barriers[wave] = entry["validation"]
    if not ordering:
        return derived, []
    if sorted(ordering) != sorted(members):
        return derived, [
            core.error(
                core.E_INVALID_STATE,
                "declared waves %s do not match the waves the stories name (%s)"
                % (sorted(ordering), sorted(members)),
                file="<stdin>",
            )
        ]
    for wave, listed in ordering.items():
        if sorted(listed) != members[wave]:
            return derived, [
                core.error(
                    core.E_INVALID_STATE,
                    "wave %d lists %s but its stories are %s"
                    % (wave, sorted(listed), members[wave]),
                    file="<stdin>",
                )
            ]
    reconciled: List[Dict[str, Any]] = []
    for wave in sorted(ordering):
        entry = {"wave": wave, "stories": list(ordering[wave])}
        if wave in barriers:
            entry["validation"] = barriers[wave]
        reconciled.append(entry)
    return reconciled, []


def _wave_barrier_checks(version: Any, waves: Sequence[Dict[str, Any]]) -> List[core.Diagnostic]:
    """Every version-4 wave carries a barrier check that actually runs.

    ``plan-graph.schema.json`` requires the ``validation`` array on a version-4
    wave and can go no further: "at least one item has this ``kind``" is a
    cross-field count JSON Schema cannot state, so ``emit`` states it. It is a
    refusal rather than a warning for the same reason the prose-acceptance rule
    is -- a wave with no runnable barrier produces a plan that *looks* checked
    and is not, and the omission is invisible in every artifact downstream of
    the emit.

    A wave declaring no ``validation`` at all and one declaring only prose are
    the same defect and are reported as one, since both leave the merged branch
    unexercised.
    """
    if version != PLAN_GRAPH_BARRIER_VERSION:
        return []
    diagnostics: List[core.Diagnostic] = []
    for entry in waves:
        steps = entry.get("validation")
        if isinstance(steps, list) and any(
            isinstance(step, dict) and step.get("kind") == EXIT_CODE_KIND for step in steps
        ):
            continue
        diagnostics.append(
            core.error(
                core.E_INVALID_STATE,
                "wave %s has no `kind: %s` validation step; a wave whose barrier "
                "check is prose alone cannot be demonstrated to run against the "
                "merged integration branch" % (entry.get("wave"), EXIT_CODE_KIND),
                file="<stdin>",
            )
        )
    return diagnostics


def _slice_surface_checks(
    version: Any, slices: Sequence[Dict[str, Any]]
) -> List[core.Diagnostic]:
    """Every version-4 slice is demonstrated through its delivered surface.

    ``STANDARD-SPEC.md`` §"Delivered-Surface Rule" is unconditional, and this is
    where a plan is made to satisfy it mechanically: at least one acceptance
    step of every slice must be **both** ``kind: exit-code`` and
    ``surface: delivered``. Neither half suffices alone -- a prose step naming
    the delivered surface cannot be demonstrated to run, and a runnable step
    beneath the surface is evidence about internals rather than about the
    behaviour that was delivered -- and JSON Schema can state neither, since
    both are cross-field counts over an array.

    It is a refusal rather than a warning for the same reason the
    prose-acceptance rule is: a slice checked only beneath its surface produces
    a plan that *looks* demonstrated and is not, and the omission is invisible
    in every artifact downstream of the emit.

    Gated on the version like the wave and story checks, and for the reason
    ``surface`` defaults to ``internal``: an unannotated step is exactly what a
    version-3 acceptance always was, so applying the rule below 4 would refuse
    every graph written before the key existed.
    """
    if version != PLAN_GRAPH_BARRIER_VERSION:
        return []
    diagnostics: List[core.Diagnostic] = []
    for entry in slices:
        acceptance = entry.get("acceptance")
        if isinstance(acceptance, list) and any(
            isinstance(step, dict)
            and step.get("kind") == EXIT_CODE_KIND
            and step.get("surface") == DELIVERED_SURFACE
            for step in acceptance
        ):
            continue
        diagnostics.append(
            core.error(
                core.E_INVALID_STATE,
                "slice %r has no `kind: %s` acceptance step carrying `surface: "
                "%s`; a slice demonstrated only beneath the surface its "
                "behaviour is delivered on has not been demonstrated to work"
                % (entry.get("slice"), EXIT_CODE_KIND, DELIVERED_SURFACE),
                file="<stdin>",
            )
        )
    return diagnostics


def _story_increment_checks(version: Any, stories: Dict[str, Any]) -> List[core.Diagnostic]:
    """Every version-4 story carries a per-increment check that actually runs.

    The story-level counterpart of ``_wave_barrier_checks``, and refused for the
    same reason: ``plan-graph.schema.json`` requires ``validation.increments`` on
    a version-4 story and can go no further, since "at least one item has this
    ``kind``" is a cross-field count JSON Schema cannot state. A story whose
    increments are prose alone produces a plan that *looks* checked during the
    work and is not, and the omission is invisible in every artifact downstream
    of the emit -- including the rendered story, where the interleave would put
    a check line no agent can run.

    A story declaring no ``increments`` at all and one declaring only prose are
    the same defect and are reported as one: both leave the story's own work
    unchecked until its closing gate.
    """
    if version != PLAN_GRAPH_BARRIER_VERSION:
        return []
    diagnostics: List[core.Diagnostic] = []
    for story_id in sorted(stories):
        story = stories[story_id]
        validation = story.get("validation") if isinstance(story, dict) else None
        steps = validation.get("increments") if isinstance(validation, dict) else None
        if isinstance(steps, list) and any(
            isinstance(step, dict) and step.get("kind") == EXIT_CODE_KIND for step in steps
        ):
            continue
        diagnostics.append(
            core.error(
                core.E_INVALID_STATE,
                "story %r has no `kind: %s` validation.increments step; a story "
                "whose per-increment checks are prose alone cannot be "
                "demonstrated to run during the work it checks"
                % (story_id, EXIT_CODE_KIND),
                file="<stdin>",
            )
        )
    return diagnostics


def _normalize_story(story_id: str, raw: Any, waves: List[Dict[str, Any]]) -> Any:
    if not isinstance(raw, dict):
        return raw  # left as-is; the schema refuses it
    story = dict(raw)
    story.setdefault("file", "%s%s%s" % (STORY_FILE_PREFIX, story_id, STORY_FILE_SUFFIX))
    story.setdefault("prerequisites", [])
    if "parallel_group" not in story:
        story["parallel_group"] = _siblings(story_id, story.get("wave"), waves)
    return story


def _siblings(story_id: str, wave: Any, waves: List[Dict[str, Any]]) -> List[str]:
    for entry in waves:
        if entry["wave"] == wave:
            return [other for other in entry["stories"] if other != story_id]
    return []


def _derive_plan_state(graph: Dict[str, Any], now: str) -> Dict[str, Any]:
    """The initial ``state.yaml``: every story ``pending``, ``run: 0``."""
    stories: Dict[str, Any] = {}
    for story_id, story in graph.get("stories", {}).items():
        entry: Dict[str, Any] = {}
        if isinstance(story, dict):
            entry["repo"] = story.get("repo")
            entry["wave"] = story.get("wave")
        entry["status"] = "pending"
        entry["retries"] = 0
        stories[story_id] = entry
    declared = graph.get("slices")
    sliced = graph.get("version") in PLAN_GRAPH_SLICE_VERSIONS and isinstance(declared, list)
    state: Dict[str, Any] = {
        "version": PLAN_STATE_SLICE_VERSION if sliced else PLAN_STATE_VERSION,
        "plan_id": graph.get("plan_id"),
        "run": 0,
        "updated": now,
        "status": "pending",
        "waves": [
            {"wave": wave["wave"], "status": "pending", "stories": list(wave["stories"])}
            for wave in graph.get("waves", [])
        ],
        "stories": stories,
    }
    if sliced:
        state["slices"] = [
            {"slice": entry.get("slice"), "status": SLICE_PENDING}
            for entry in declared
            if isinstance(entry, dict)
        ]
    return state


def _derive_ledger(ledger: Dict[str, Any], graph: Dict[str, Any], now: str) -> Dict[str, Any]:
    """This plan's ledger entry, added without touching any other plan's.

    ``version`` belongs to the ledger *file*: ``project-state.schema.json`` puts
    it at the top level as ``const: 1`` and each ``plans.<plan-id>`` entry is
    ``additionalProperties: false``, so a ``version`` inside an entry would be
    refused on validation.
    """
    plan_id = graph.get("plan_id")
    plans: Dict[str, Any] = {}
    for existing_id, entry in _ledger_plans(ledger).items():
        if existing_id != plan_id:
            plans[existing_id] = entry
    entry: Dict[str, Any] = {
        "status": "pending",
        "project_change": graph.get("project_change"),
        "plan_dir": _plan_dir_rel(plan_id),
        "updated": now,
    }
    declared = graph.get("slices")
    if graph.get("version") in PLAN_GRAPH_SLICE_VERSIONS and isinstance(declared, list):
        # Seeded here and thereafter maintained only by `state set-slice`, which
        # is the single writer of these two counters. A pre-slice plan carries
        # neither, and is read as pre-slice rather than as zero progress.
        entry["slices_total"] = len(declared)
        entry["slices_applied"] = 0
    plans[plan_id] = entry
    return {"version": LEDGER_VERSION, "updated": now, "plans": plans}


# ---------------------------------------------------------------------------
# plan reslice -- the loop's backward edge
# ---------------------------------------------------------------------------


def _reslice(args, ws, now) -> core.Result:
    """Rewrite the outstanding slices from a ``SliceDraft[]`` on stdin.

    The applied-slice refusal is the load-bearing one: what has been delivered
    is fixed, what is merely planned is not. Both the graph and ``state.yaml``
    are re-validated before either is written, so a refusal -- of any kind --
    leaves both files byte-identical.
    """
    command = "%s.reslice" % COMMAND
    plan_id = _check_plan_id(args.plan_id)
    plan_dir_rel = _plan_dir_rel(plan_id)
    graph, graph_rel = _load_graph(ws, plan_id)
    state, state_rel = _load_state(ws, plan_id, required=True)
    nothing = {"plan_id": plan_id, "plan_dir": plan_dir_rel, "written": []}

    draft = _read_draft(args, "the slice drafts")
    if not isinstance(draft, list):
        return core.Result(
            command=command,
            data=nothing,
            diagnostics=[
                core.error(
                    core.E_PARSE, "the slice drafts must be a JSON array", file="<stdin>"
                )
            ],
        )

    stories = graph.get("stories")
    stories = stories if isinstance(stories, dict) else {}
    slices, refusals = _normalize_slices(draft, stories)
    if refusals:
        return core.Result(command=command, data=nothing, diagnostics=refusals)

    current, _synthesized = slice_entries(graph, state)
    refusals = _applied_slices_survive(current, slices)
    if refusals:
        return core.Result(command=command, data=nothing, diagnostics=refusals)

    rewritten = dict(graph)
    # A legacy graph is upgraded to the first slice-bearing version; one already
    # at or above it keeps its own, so reslicing a version-4 graph does not
    # quietly strip the barrier checks its waves are required to carry.
    rewritten["version"] = (
        graph.get("version")
        if graph.get("version") in PLAN_GRAPH_SLICE_VERSIONS
        else PLAN_GRAPH_SLICE_VERSION
    )
    rewritten["slices"] = slices
    rewritten["stories"] = {story_id: dict(story) if isinstance(story, dict) else story
                            for story_id, story in stories.items()}
    _apply_story_slices(slices, rewritten["stories"])

    recorded = _slice_statuses(state)
    rewritten_state = dict(state)
    rewritten_state["version"] = PLAN_STATE_SLICE_VERSION
    rewritten_state["slices"] = [
        _slice_state_entry(state, entry.get("slice"), recorded)
        for entry in slices
    ]
    rewritten_state["updated"] = now

    documents = (
        (rewritten, "plan-graph", graph_rel, PLANS_DIR + (plan_id, PLAN_FILE)),
        (rewritten_state, "plan-state", state_rel, PLANS_DIR + (plan_id, STATE_FILE)),
    )
    pending: List[Tuple[Any, str, str]] = []
    diagnostics: List[core.Diagnostic] = []
    for data, kind, relative, parts in documents:
        schema = core.load_schema(kind)
        text = core.dump_yaml(data, schema)
        for message in core.validate_against(schema, core.parse_yaml(text, relative)):
            diagnostics.append(
                core.error(
                    core.E_SCHEMA_INVALID,
                    "refused: does not validate against %s: %s" % (kind, message),
                    file=relative,
                )
            )
        pending.append((ws.safe_path(*parts), text, relative))
    if diagnostics:
        return core.Result(command=command, data=nothing, diagnostics=diagnostics)

    written: List[str] = []
    for path, text, relative in pending:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        written.append(relative)
    return core.Result(
        command=command,
        data={
            "plan_id": plan_id,
            "plan_dir": plan_dir_rel,
            "slices": [entry.get("slice") for entry in slices],
            "written": written,
        },
    )


def _slice_state_entry(
    state: Dict[str, Any], slice_id: Any, recorded: Dict[str, str]
) -> Dict[str, Any]:
    """A slice's state entry, preserving what the file already recorded for it."""
    entries = state.get("slices")
    if isinstance(entries, list):
        for entry in entries:
            if isinstance(entry, dict) and entry.get("slice") == slice_id:
                return dict(entry)
    return {"slice": slice_id, "status": recorded.get(str(slice_id), SLICE_PENDING)}


def _applied_slices_survive(
    current: Sequence[Dict[str, Any]], draft: Sequence[Dict[str, Any]]
) -> List[core.Diagnostic]:
    """Refuse a draft that alters, drops, reorders or renumbers an applied slice.

    Position and content are both compared, which is what makes the one check
    cover all four: a dropped slice shifts the ones after it, a reorder moves
    it, a renumber changes its id, and an edit changes its body.
    """
    diagnostics: List[core.Diagnostic] = []
    for index, entry in enumerate(current):
        if entry.get("status") != SLICE_APPLIED:
            continue
        frozen = {field: entry.get(field) for field in SLICE_DRAFT_FIELDS}
        if index >= len(draft):
            diagnostics.append(
                core.error(
                    core.E_INVALID_STATE,
                    "slice %r is applied and the draft drops it; what has been "
                    "delivered cannot be rewritten" % (entry.get("slice"),),
                    file="<stdin>",
                )
            )
            continue
        proposed = {field: draft[index].get(field) for field in SLICE_DRAFT_FIELDS}
        if proposed != frozen:
            diagnostics.append(
                core.error(
                    core.E_INVALID_STATE,
                    "slice %r is applied but the draft alters, reorders or renumbers "
                    "it (position %d now reads %r); what has been delivered cannot be "
                    "rewritten" % (entry.get("slice"), index, proposed.get("slice")),
                    file="<stdin>",
                )
            )
    return diagnostics


# ---------------------------------------------------------------------------
# plan story-emit
# ---------------------------------------------------------------------------


def _story_emit(args, ws, now) -> core.Result:
    """Render one story file, injecting the E2E rules at both markers."""
    command = "%s.story-emit" % COMMAND
    plan_id = _check_plan_id(args.plan_id)
    story_id = _check_story_id(args.story_id)
    diagnostics: List[core.Diagnostic] = []

    plan_dir_rel = _plan_dir_rel(plan_id)
    graph_rel = "%s/%s" % (plan_dir_rel, PLAN_FILE)
    graph_path = ws.safe_path(*(PLANS_DIR + (plan_id, PLAN_FILE)))
    if not graph_path.is_file():
        return core.Result(
            command=command,
            diagnostics=[core.error(core.E_NOT_FOUND, "no such file", file=graph_rel)],
        )
    graph = core.load_yaml(graph_path, graph_rel)
    if not isinstance(graph, dict):
        return core.Result(
            command=command,
            diagnostics=[core.error(core.E_PARSE, "plan graph is not a mapping", file=graph_rel)],
        )
    stories = graph.get("stories")
    story = stories.get(story_id) if isinstance(stories, dict) else None
    if not isinstance(story, dict):
        return core.Result(
            command=command,
            diagnostics=[
                core.error(
                    core.E_NOT_FOUND, "plan graph holds no story %r" % (story_id,), file=graph_rel
                )
            ],
        )

    repo = story.get("repo")
    if not isinstance(repo, str) or core.require_ident(repo, "repo") is not None:
        return core.Result(
            command=command,
            diagnostics=[
                core.error(core.E_INVALID_STATE, "story %r names no repo" % (story_id,), file=graph_rel)
            ],
        )
    catalog, catalog_rel = _repo_catalog(ws, repo, diagnostics)
    context = _story_context(graph, story_id, story, repo, catalog, diagnostics, graph_rel)

    rules = _e2e_rules()
    delivered = _delivered_surface_rules()
    body = _template_body()
    rendered = _render_story(body, context, rules, delivered, diagnostics, graph_rel)

    name = str(story.get("file") or "%s%s%s" % (STORY_FILE_PREFIX, story_id, STORY_FILE_SUFFIX))
    if Path(name).name != name:
        return core.Result(
            command=command,
            diagnostics=[
                core.error(core.E_INVALID_STATE, "story file %r is not a bare filename" % (name,), file=graph_rel)
            ],
        )
    core.check_ident(name, "story filename")
    target = ws.safe_path(*(PLANS_DIR + (plan_id, name)))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(rendered, encoding="utf-8")
    data = {
        "plan_id": plan_id,
        "story_id": story_id,
        "file": "%s/%s" % (plan_dir_rel, name),
        "slice": context["slice"],
        "wave": context["wave"],
        # Slice-scoped, not plan-scoped: the last wave *of this story's slice*.
        # ``slice`` is published beside it so a caller can tell which slice the
        # boolean was resolved against without re-reading the graph.
        "last_wave": context["last_wave"],
        "catalog": catalog_rel,
        "e2e_injections": 2 if context["e2e"] else 0,
    }
    return core.Result(command=command, data=data, diagnostics=diagnostics)


def _repo_catalog(ws, repo: str, diagnostics: List[core.Diagnostic]):
    relative = "context/%s/spec/CATALOG.yaml" % repo
    path = ws.safe_path("context", repo, "spec", "CATALOG.yaml")
    if not path.is_file():
        diagnostics.append(
            core.warning(
                core.E_NOT_FOUND,
                "no catalog for repo %r: the layer is omitted and the E2E rules are not injected"
                % (repo,),
                file=relative,
            )
        )
        return {}, relative
    catalog = core.load_yaml(path, relative)
    return (catalog if isinstance(catalog, dict) else {}), relative


def _slice_last_wave(graph: Dict[str, Any], entry: Dict[str, Any]) -> Optional[int]:
    """The maximum wave among a slice's own member stories.

    **A slice's last wave is the maximum wave among its member stories, never a
    function of its position or its id.** Wave numbers are global to the plan
    and ``plan-graph.schema.json`` does not constrain slices to contiguous or
    non-overlapping ranges, so membership is the only derivation correct under
    every arrangement.
    """
    stories = graph.get("stories")
    if not isinstance(stories, dict):
        return None
    members = entry.get("stories")
    numbers: List[int] = []
    for story_id in members if isinstance(members, list) else []:
        member = stories.get(story_id) if isinstance(story_id, str) else None
        wave = member.get("wave") if isinstance(member, dict) else None
        if isinstance(wave, int):
            numbers.append(wave)
    return max(numbers) if numbers else None


def _story_slice(
    graph: Dict[str, Any],
    story_id: str,
    story: Dict[str, Any],
    plan_last: Optional[int],
    diagnostics: Optional[List[core.Diagnostic]],
    graph_rel: Optional[str],
) -> Tuple[str, str, Optional[int]]:
    """``(slice id, slice name, the slice's last wave)`` for one story.

    ``slices[].stories`` is the authority on membership: the story's own
    ``slice`` key is written from it at ``plan emit`` and ``plan reslice``, but
    this reads a graph off disk that may have been hand-edited since, and the
    refusal that would catch a disagreement runs only at those two verbs. All
    three degenerate cases therefore render with a ``warning`` rather than
    refuse -- ``story-emit`` is called once per story in a fan-out, and a
    graph-level defect must not become a rendering outage in stories that are
    themselves well-formed.
    """

    def note(message: str) -> None:
        if diagnostics is not None:
            diagnostics.append(
                core.warning(core.E_INVALID_STATE, message, file=graph_rel)
            )

    # ``graph_slices`` always yields at least one slice -- a version-1/2 graph
    # is read as carrying the synthesized ``00``, which lists every story -- so
    # there is no null branch and a legacy graph resolves to the plan's last
    # wave exactly as it always did.
    declared, _synthesized = graph_slices(graph)
    owners = [
        entry
        for entry in declared
        if isinstance(entry.get("stories"), list) and story_id in entry["stories"]
    ]
    if len(owners) > 1:
        note(
            "story %r is listed by slices %s; the first-listed wins"
            % (story_id, ", ".join(repr(str(entry.get("slice"))) for entry in owners))
        )
    if not owners:
        note(
            "no slice lists story %r; it renders as the whole plan and its "
            "acceptance gate falls back to the plan's last wave" % (story_id,)
        )
        return LEGACY_SLICE_ID, LEGACY_SLICE_NAME, plan_last

    entry = owners[0]
    slice_id = str(entry.get("slice") or "")
    claimed = story.get("slice")
    if isinstance(claimed, str) and claimed != slice_id:
        note(
            "story %r carries slice %r but slice %r lists it; slices[].stories "
            "is the authority" % (story_id, claimed, slice_id)
        )
    return slice_id, str(entry.get("name") or ""), _slice_last_wave(graph, entry)


def _story_context(
    graph: Dict[str, Any],
    story_id: str,
    story: Dict[str, Any],
    repo: str,
    catalog: Dict[str, Any],
    diagnostics: Optional[List[core.Diagnostic]] = None,
    graph_rel: Optional[str] = None,
) -> Dict[str, Any]:
    waves = _graph_waves(graph)
    numbers = [wave["wave"] for wave in waves if isinstance(wave.get("wave"), int)]
    total = len(numbers)
    last = max(numbers) if numbers else None
    wave = story.get("wave")
    slice_id, slice_name, slice_last = _story_slice(
        graph, story_id, story, last, diagnostics, graph_rel
    )
    prerequisites = [item for item in story.get("prerequisites", []) if isinstance(item, str)]
    siblings = [item for item in story.get("parallel_group", []) if isinstance(item, str)]
    return {
        "repo": repo,
        "module": str(story.get("module") or ""),
        "layer": _module_layer(catalog, story.get("module")),
        "slice": slice_id,
        "slice_name": slice_name,
        "wave": wave,
        # Plan-global on purpose: scoping the count to the slice would make wave
        # numbers non-monotonic against the graph's own ``waves:`` block, so a
        # story may render ``**Wave:** 2 of 4`` and still be its slice's last.
        "total_waves": total,
        "last_wave": wave == slice_last,
        "incremental": graph.get("type") == "incremental",
        "prerequisites": [_story_filename(graph, item) for item in prerequisites],
        "parallel_group": [_story_filename(graph, item) for item in siblings],
        "change_file": story.get("change_file"),
        "target_paths": [item for item in story.get("target_paths", []) if isinstance(item, str)],
        "e2e": _catalog_covers_e2e(catalog),
        # Empty on every graph below version 4, where the key does not exist --
        # which is what leaves a legacy task list rendered exactly as before.
        "increments": _story_increments(story),
    }


def _story_increments(story: Dict[str, Any]) -> List[Any]:
    validation = story.get("validation")
    steps = validation.get("increments") if isinstance(validation, dict) else None
    return list(steps) if isinstance(steps, list) else []


def _story_filename(graph: Dict[str, Any], story_id: str) -> str:
    stories = graph.get("stories")
    entry = stories.get(story_id) if isinstance(stories, dict) else None
    if isinstance(entry, dict) and isinstance(entry.get("file"), str):
        return entry["file"]
    return "%s%s%s" % (STORY_FILE_PREFIX, story_id, STORY_FILE_SUFFIX)


def _module_layer(catalog: Dict[str, Any], module: Any) -> Optional[str]:
    modules = catalog.get("modules")
    if not isinstance(modules, dict) or not isinstance(module, str):
        return None
    entry = modules.get(module)
    if isinstance(entry, dict) and isinstance(entry.get("layer"), str):
        return entry["layer"]
    return None


def _catalog_covers_e2e(catalog: Dict[str, Any]) -> bool:
    """Whether the catalog names a module whose TESTING facet covers E2E.

    The gate is read from ``CATALOG.yaml`` alone, exactly as the template's
    marker says: a module whose name carries ``E2E`` as a token, or a
    ``facet: test`` file whose own name does.
    """
    modules = catalog.get("modules")
    if not isinstance(modules, dict):
        return False
    for name in sorted(modules):
        if "E2E" in str(name).upper().split("-"):
            return True
        entry = modules[name]
        files = entry.get("files") if isinstance(entry, dict) else None
        if not isinstance(files, list):
            continue
        for item in files:
            if not isinstance(item, dict) or item.get("facet") != "test":
                continue
            path = item.get("path")
            if isinstance(path, str) and "E2E" in Path(path).name.upper():
                return True
    return False


def _template_body() -> str:
    """The block between the template's BEGIN/END markers."""
    text = core.read_text(STORY_TEMPLATE, "shared/PLAN-STORY-TEMPLATE.md")
    start = text.find(TEMPLATE_BEGIN)
    end = text.find(TEMPLATE_END)
    if start < 0 or end < 0 or end < start:
        raise core.fail(
            core.E_PARSE,
            "the story template carries no BEGIN/END marker pair",
            file="shared/PLAN-STORY-TEMPLATE.md",
        )
    return text[start + len(TEMPLATE_BEGIN) : end].strip("\n")


def _standard_rules(heading: str) -> str:
    """The four rules under ``heading``, read verbatim from their owning section.

    One reader for both injections: ``STANDARD-SPEC.md`` owns each set of rules,
    and this returns the section's lead-in line and its four bullets exactly as
    written there, so every delivered copy is generated rather than maintained
    by hand. A second reader would be a second way to read one convention, and
    the two could disagree about the same section.
    """
    display = "shared/STANDARD-SPEC.md"
    text = core.read_text(STANDARD_SPEC, display)
    lines = text.split("\n")
    try:
        start = next(index for index, line in enumerate(lines) if line.strip() == heading)
    except StopIteration:
        raise core.fail(core.E_NOT_FOUND, "no section %r" % (heading,), file=display)
    lead: Optional[str] = None
    bullets: List[str] = []
    for line in lines[start + 1 :]:
        stripped = line.strip()
        if stripped.startswith("## ") or stripped == "---":
            break
        if stripped.startswith("- "):
            bullets.append(line)
            continue
        if bullets:
            break
        if stripped.endswith(":"):
            lead = line
    if len(bullets) != 4:
        raise core.fail(
            core.E_PARSE,
            "expected four rules under %r, found %d" % (heading, len(bullets)),
            file=display,
        )
    block = bullets if lead is None else [lead, ""] + bullets
    return "\n".join(block)


def _e2e_rules() -> str:
    """The E2E Testing Hard Rules, from §"E2E Testing Hard Rules"."""
    return _standard_rules(E2E_SECTION_HEADING)


def _delivered_surface_rules() -> str:
    """The Delivered-Surface Rule, from §"Delivered-Surface Rule".

    Injected at its own marker and **ungated**: unlike the E2E rules there is no
    catalog condition under which it does not apply, because every module has a
    surface its behaviour is delivered on.
    """
    return _standard_rules(DELIVERED_SURFACE_SECTION_HEADING)


def _render_story(
    body: str,
    context: Dict[str, Any],
    rules: str,
    delivered: str,
    diagnostics: Optional[List[core.Diagnostic]] = None,
    graph_rel: Optional[str] = None,
) -> str:
    """Gate the template's conditional blocks, substitute, and clean up.

    ``rules`` is injected at each ``INJECT:E2E-HARD-RULES`` marker under the
    catalog condition those markers carry; ``delivered`` is injected at the
    ``INJECT:DELIVERED-SURFACE-RULE`` marker under no condition at all.
    """
    lines = body.split("\n")
    kept: List[str] = []
    index = 0
    total = len(lines)
    while index < total:
        line = lines[index]
        stripped = line.strip()
        if stripped.startswith(INCREMENTAL_BEGIN):
            if context["incremental"]:
                index += 1
                continue
            while index < total and INCREMENTAL_END not in lines[index]:
                index += 1
            index += 1
            continue
        if stripped.startswith(INCREMENTAL_END):
            index += 1
            continue
        if stripped.startswith(INJECT_MARKER):
            if context["e2e"]:
                kept.extend(rules.split("\n"))
            index += 1
            continue
        if stripped.startswith("<!--") and DELIVERED_SURFACE_MARKER in stripped:
            # Ungated on purpose. The E2E branch above asks the catalog first;
            # this one has nothing to ask, which is the difference the two
            # markers exist to express.
            kept.extend(delivered.split("\n"))
            index += 1
            continue
        if stripped.startswith(FINAL_WAVE_GATE):
            if not context["last_wave"]:
                break  # the final-validation section runs to the end of the template
            index += 1
            continue
        if stripped.startswith(COMPLIANCE_LINE):
            # "update sub-mode only" -- the plan graph carries no sub-mode, so
            # the line is never applicable and is dropped.
            index += 1
            continue
        kept.append(line)
        index += 1
    text = _substitute("\n".join(kept), context)
    text = _strip_guidance_comments(text)

    def note(message: str) -> None:
        if diagnostics is not None:
            diagnostics.append(core.warning(core.E_INVALID_STATE, message, file=graph_rel))

    # After the substitution pass on purpose, for the reason the ``**Slice:**``
    # header is: an increment's own command must never re-enter the fixed-order
    # sequence of replacements and be rewritten after the fact.
    text = _interleave_increments(text, context["increments"], note)
    return _tidy(text)


def _substitute(text: str, context: Dict[str, Any]) -> str:
    """Fill the placeholders the plan graph answers, in a fixed order.

    Placeholders the graph cannot answer -- context files, the change-scope
    narrative, acceptance criteria -- are left in place; they are ``mplan``'s
    judgment, not this tool's.
    """
    repo = context["repo"]
    module = context["module"]
    layer = context["layer"]
    wave = context["wave"]
    header = "# Story: %s (%s) — %s" % (module, layer, repo) if layer else "# Story: %s — %s" % (module, repo)
    replacements: List[Tuple[str, str]] = [
        ("# Story: {Module Name} ({Layer}) — {repo}", header),
        (
            "**Wave:** {wave number} of {total waves}",
            "**Wave:** %s of %s" % (wave, context["total_waves"]),
        ),
        (
            '**Prerequisites:** {list all story files from earlier waves that must complete first, or "None"}',
            "**Prerequisites:** %s" % (_file_list(context["prerequisites"], "None"),),
        ),
        (
            '**Parallel group:** {list other PLAN-{WW}-*-*.md files that run concurrently with this one, or "solo"}',
            "**Parallel group:** %s" % (_file_list(context["parallel_group"], "solo"),),
        ),
        (
            '| {file, relative to repos/{repo}/} | {what to update, from "Affected Code Paths" in the change file} |',
            _target_rows(context["target_paths"]),
        ),
        ("wave {WW+1}", "wave %s" % (wave + 1 if isinstance(wave, int) else "{WW+1}",)),
    ]
    change_file = context["change_file"]
    if isinstance(change_file, str) and change_file:
        replacements.append(
            ("`context/{repo}/changes/CHANGE-<NNN>-<slug>.md`", "`%s`" % (change_file,))
        )
    replacements.append(("{repo}", repo))
    # Deliberately after the ``{repo}`` pass. Rendering is a fixed-order
    # sequence of replacements, and a substituted value re-entering that
    # sequence is the one way it could stop being one -- a slice named with a
    # literal ``{repo}`` would otherwise be rewritten after the fact.
    replacements.append(
        (
            "**Slice:** {NN} — {slice name}",
            "**Slice:** %s — %s" % (context["slice"], context["slice_name"]),
        )
    )
    for needle, value in replacements:
        text = text.replace(needle, value)
    return text


def _interleave_increments(
    text: str,
    increments: Sequence[Any],
    note: Optional[Callable[[str], None]] = None,
) -> str:
    """Place each ``validation.increments`` step after the task its index names.

    The rendered ``## Implementation Tasks`` list therefore reads
    work-then-check repeated rather than work followed by a single closing
    check, which is the whole of what makes the per-increment claim visible to
    the agent that reads the story -- true of the graph is not the same as
    stated in the file the agent is handed.

    A graph carrying no ``increments`` -- every version below 4 -- is returned
    untouched, so a legacy render is byte-identical.
    """
    steps = [step for step in increments if isinstance(step, dict)]
    if not steps:
        return text
    lines = text.split("\n")
    span = _tasks_span(lines)
    if span is None:
        return text
    tasks = _task_positions(lines, span)
    if not tasks:
        return text
    pending: Dict[int, List[str]] = {}
    for order, step in enumerate(steps, start=1):
        position = _increment_task(step, len(tasks), order, note)
        anchor, indent = tasks[position]
        boundary = tasks[position + 1][0] if position + 1 < len(tasks) else span[1]
        end = anchor + 1
        while end < boundary and lines[end].strip():
            end += 1
        pending.setdefault(end, []).append(indent + _check_line(step))
    rebuilt: List[str] = []
    for index, line in enumerate(lines):
        rebuilt.extend(pending.pop(index, []))
        rebuilt.append(line)
    for index in sorted(pending):
        rebuilt.extend(pending[index])
    return "\n".join(rebuilt)


def _tasks_span(lines: Sequence[str]) -> Optional[Tuple[int, int]]:
    """The half-open line range holding the task section's content."""
    start = None
    for index, line in enumerate(lines):
        if line.strip() == TASKS_HEADING:
            start = index + 1
            break
    if start is None:
        return None
    for index in range(start, len(lines)):
        stripped = lines[index].strip()
        if stripped.startswith("## ") or stripped == "---":
            return start, index
    return start, len(lines)


def _task_positions(lines: Sequence[str], span: Tuple[int, int]) -> List[Tuple[int, str]]:
    """``(line index, continuation indent)`` for each task, in rendered order."""
    positions: List[Tuple[int, str]] = []
    for index in range(span[0], span[1]):
        match = _TASK_ITEM_RE.match(lines[index])
        if match is not None:
            positions.append((index, " " * len(match.group(1))))
    return positions


def _increment_task(
    step: Dict[str, Any], count: int, order: int, note: Optional[Callable[[str], None]]
) -> int:
    """The 0-based task an increment is placed after.

    An index the rendered list does not reach falls back to the last task and
    emits a ``warning`` -- consistent with every other graph-level defect this
    verb reports rather than refuses, since ``story-emit`` is called once per
    story in a fan-out and a defect in one story's graph entry must not become a
    rendering outage. Dropping the step instead was the alternative rejected:
    the check would then exist in the graph and nowhere the agent can read it,
    which is the failure the interleave exists to prevent.
    """
    task = step.get("task")
    if isinstance(task, bool) or not isinstance(task, int):
        if note is not None:
            note(
                "increment %d names no task; it is placed after the last of the "
                "%d rendered tasks" % (order, count)
            )
        return count - 1
    if task < 1 or task > count:
        if note is not None:
            note(
                "increment %d names task %d, but the story renders %d tasks; it "
                "is placed after the last" % (order, task, count)
            )
        return count - 1
    return task - 1


def _check_line(step: Dict[str, Any]) -> str:
    """One increment as the template's ``- *Check:*`` line."""
    description = str(step.get("description") or "").strip()
    command = step.get("command")
    if step.get("kind") == EXIT_CODE_KIND and isinstance(command, str) and command.strip():
        body = "`%s`" % (command.strip(),)
        if description:
            body = "%s — %s" % (body, description)
    else:
        body = description
    return ("%s %s" % (CHECK_PREFIX, body)).rstrip()


def _file_list(names: List[str], empty: str) -> str:
    if not names:
        return empty
    return ", ".join("`%s`" % name for name in names)


def _target_rows(paths: List[str]) -> str:
    placeholder = '{what to update, from "Affected Code Paths" in the change file}'
    if not paths:
        return "| {file, relative to repos/} | %s |" % placeholder
    return "\n".join("| `%s` | %s |" % (path, placeholder) for path in paths)


def _strip_guidance_comments(text: str) -> str:
    """Drop the template's authoring comments, keeping the ``depends-on`` block.

    The comments are instructions to whoever fills the template in, not story
    content; the front-matter line is content and is mandatory.
    """
    lines = text.split("\n")
    kept: List[str] = []
    index = 0
    total = len(lines)
    while index < total:
        line = lines[index]
        stripped = line.strip()
        if stripped.startswith("<!--"):
            if core.FRONT_MATTER_RE.match(stripped):
                kept.append(line)
                index += 1
                continue
            while index < total and "-->" not in lines[index]:
                index += 1
            index += 1
            if (
                index < total
                and not lines[index].strip()
                and kept
                and not kept[-1].strip()
            ):
                index += 1  # the comment stood alone between blank lines
            continue
        if "<!--" in line and "-->" in line:
            line = _COMMENT_INLINE_RE.sub("", line).rstrip()
        kept.append(line)
        index += 1
    return "\n".join(kept)


def _tidy(text: str) -> str:
    """Collapse blank-line runs and end the file with exactly one newline."""
    lines = text.split("\n")
    kept: List[str] = []
    for line in lines:
        if not line.strip() and kept and not kept[-1].strip():
            continue
        kept.append(line.rstrip())
    while kept and not kept[0].strip():
        kept.pop(0)
    while kept and not kept[-1].strip():
        kept.pop()
    return "\n".join(kept) + "\n"


# ---------------------------------------------------------------------------
# plan shards
# ---------------------------------------------------------------------------


def _shards(args, ws, now) -> core.Result:
    """``ShardSpec[]`` -- the conformance shard list ``mverify`` fans out over.

    Stable order: change-conformance by ``(repo, module)``, then cross-repo by
    TAG, then coupling. Reads only -- no file is written and no git command is
    invoked.
    """
    command = "%s.shards" % COMMAND
    plan_id = _check_plan_id(args.plan_id)
    granularity = getattr(args, "granularity", None) or "repo"
    wanted = getattr(args, "slice", None)

    plan_dir_rel = _plan_dir_rel(plan_id)
    graph_rel = "%s/%s" % (plan_dir_rel, PLAN_FILE)
    graph_path = ws.safe_path(*(PLANS_DIR + (plan_id, PLAN_FILE)))
    if not graph_path.is_file():
        return core.Result(
            command=command,
            diagnostics=[core.error(core.E_NOT_FOUND, "no such file", file=graph_rel)],
        )
    graph = core.load_yaml(graph_path, graph_rel)
    if not isinstance(graph, dict):
        return core.Result(
            command=command,
            diagnostics=[core.error(core.E_PARSE, "plan graph is not a mapping", file=graph_rel)],
        )

    members: Optional[List[str]] = None
    if wanted is not None:
        declared, _synthesized = graph_slices(graph)
        found = [entry for entry in declared if entry.get("slice") == wanted]
        if not found:
            return core.Result(
                command=command,
                diagnostics=[
                    core.error(
                        core.E_NOT_FOUND,
                        "the plan graph declares no slice %r" % (wanted,),
                        file=graph_rel,
                    )
                ],
            )
        listed = found[0].get("stories")
        members = [story_id for story_id in listed if isinstance(story_id, str)] if isinstance(listed, list) else []

    pairs = _story_repo_modules(graph, members)
    shards: List[Dict[str, Any]] = []

    for repo, module in pairs:
        core.check_ident(repo, "repo")
        core.check_ident(module, "module")
        shards.append(
            {
                "shard": "change-conformance",
                "id": "change-conformance-%s-%s" % (repo, module),
                "repo": repo,
                "module": module,
                "interface": None,
            }
        )

    for tag in _cross_repo_tags(ws, graph, members):
        core.check_ident(tag, "interface")
        shards.append(
            {
                "shard": "cross-repo",
                "id": "cross-repo-%s" % (tag,),
                "repo": None,
                "module": None,
                "interface": tag,
            }
        )

    if granularity == "module":
        for repo, module in pairs:
            # Already Ident-checked above.
            shards.append(
                {
                    "shard": "coupling",
                    "id": "coupling-%s-%s" % (repo, module),
                    "repo": repo,
                    "module": module,
                    "interface": None,
                }
            )
    else:
        for repo in sorted({repo for repo, _ in pairs}):
            # Already Ident-checked above.
            shards.append(
                {
                    "shard": "coupling",
                    "id": "coupling-%s" % (repo,),
                    "repo": repo,
                    "module": None,
                    "interface": None,
                }
            )

    data = {"plan_id": plan_id, "granularity": granularity, "slice": wanted, "shards": shards}
    return core.Result(command=command, data=data)


def _story_repo_modules(
    graph: Dict[str, Any], members: Optional[Sequence[str]] = None
) -> List[Tuple[str, str]]:
    """Every story's ``(repo, module)``, deduped and sorted.

    ``members`` restricts the walk to one slice's stories, which is what
    ``--slice`` narrows the shard list to: what that slice shipped.
    """
    stories = graph.get("stories")
    if not isinstance(stories, dict):
        return []
    pairs = set()
    for story_id, story in stories.items():
        if members is not None and story_id not in members:
            continue
        if not isinstance(story, dict):
            continue
        repo, module = story.get("repo"), story.get("module")
        if isinstance(repo, str) and isinstance(module, str):
            pairs.add((repo, module))
    return sorted(pairs)


def _cross_repo_tags(
    ws, graph: Dict[str, Any], members: Optional[Sequence[str]] = None
) -> List[str]:
    """The shared-interface TAGs named by this graph's ``scope: shared``
    change documents -- read via ``core.load_front_matter``, never through
    ``tools/change.py``. No ``context/shared/`` tree yields no entries and no
    diagnostic: a single-repo workspace is conforming, not defective."""
    tags = set()
    for path in _change_document_paths(ws, graph, members):
        if not path.is_file():
            continue
        display = ws.rel(path)
        matter = core.load_front_matter(path, display)
        if matter.get("scope") != SHARED_SCOPE:
            continue
        text = core.read_text(path, display)
        for match in _SHARED_SPEC_TAG_RE.finditer(text):
            tags.add(match.group(1))
    return sorted(tags)


def _change_document_paths(
    ws, graph: Dict[str, Any], members: Optional[Sequence[str]] = None
) -> List[Path]:
    """Every change document the graph references: each story's
    ``change_file`` plus the project index its ``project_change`` names.

    The index is plan-wide and stays in scope even under ``--slice``; only the
    per-story change files narrow, because those are what a slice shipped."""
    paths: List[Path] = []
    seen = set()
    stories = graph.get("stories")
    if isinstance(stories, dict):
        for story_id in sorted(stories):
            story = stories[story_id]
            if members is not None and story_id not in members:
                continue
            if not isinstance(story, dict):
                continue
            change_file = story.get("change_file")
            if isinstance(change_file, str) and change_file and change_file not in seen:
                seen.add(change_file)
                paths.append(ws.safe_path(change_file))
    index_path = _project_index_path(ws, graph.get("project_change"))
    if index_path is not None:
        paths.append(index_path)
    return paths


def _project_index_path(ws, project_change: Any) -> Optional[Path]:
    """The ``PROJECT-CHANGE-<project_change>-*.md`` index, if any."""
    if not isinstance(project_change, str) or not project_change:
        return None
    directory = ws.safe_path(*CHANGES_DIR)
    if not directory.is_dir():
        return None
    for entry in sorted(directory.iterdir(), key=lambda item: item.name):
        if not entry.is_file():
            continue
        match = _INDEX_RE.match(entry.name)
        if match is not None and match.group(1) == project_change:
            return entry
    return None


_VERBS = {
    "scope": _scope,
    "resolve": _resolve,
    "story-id": _story_id,
    "waves": _waves,
    "slices": _slices,
    "reslice": _reslice,
    "emit": _emit,
    "story-emit": _story_emit,
    "shards": _shards,
}
