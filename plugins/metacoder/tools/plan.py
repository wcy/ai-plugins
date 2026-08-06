"""The ``plan`` command group.

    mc.py plan scope
    mc.py plan resolve [<plan-id>]
    mc.py plan story-id <file>
    mc.py plan waves <target>
    mc.py plan emit <plan-id>            # draft graph as JSON on stdin
    mc.py plan story-emit <plan-id> <story-id>
    mc.py plan shards <plan-id> [--granularity repo|module]

Seven verbs, each a pure function of the workspace bytes plus the injected clock:

* ``scope`` -- ``incremental`` when ``context/project/changes/`` holds a
  ``pending`` index, naming the highest-numbered one; ``full`` otherwise.
* ``resolve`` -- the plan to run, its status, its run counter **as read**, the
  first wave holding an unfinished story, and the unfinished stories.
* ``story-id`` -- the story id a ``PLAN-*.md`` filename carries.
* ``waves`` -- wave assignment from catalog layer order, one wave per layer.
* ``emit`` -- ``plan.yaml``, the initial ``state.yaml``, and the ledger entry.
* ``story-emit`` -- a story file rendered from ``shared/PLAN-STORY-TEMPLATE.md``
  with the E2E Testing Hard Rules injected from ``shared/STANDARD-SPEC.md``.
* ``shards`` -- the ``ShardSpec[]`` conformance shard list ``mverify`` fans out
  over: change-conformance by ``(repo, module)``, then cross-repo by shared
  TAG, then coupling.

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
from typing import Any, Dict, List, Optional, Tuple

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

#: Schema versions this group writes (the current member of each ``enum``).
PLAN_GRAPH_VERSION = 2
PLAN_STATE_VERSION = 2
LEDGER_VERSION = 1

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

E2E_SECTION_HEADING = "## E2E Testing Hard Rules"

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

_STORY_EMIT_EPILOG = """\
notes:
  Renders shared/PLAN-STORY-TEMPLATE.md into <plan-dir>/PLAN-<story-id>.md.
  Placeholders the plan graph answers -- repo, module, layer, wave numbers,
  prerequisites, parallel group, change file, target paths -- are substituted;
  the rest are left in place for mplan's judgment. The four E2E Testing Hard
  Rules are injected verbatim from shared/STANDARD-SPEC.md at both
  INJECT:E2E-HARD-RULES markers, gated exactly as the markers are (the repo's
  CATALOG.yaml must name an E2E module or an E2E test-facet file).
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
    data = {
        "plan_id": plan_id,
        "plan_dir": plan_dir_rel,
        "status": _plan_status(state, ledger, plan_id),
        "run": _run_counter(state),
        "resume_wave": resume_wave,
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

    graph, refusals = _normalize_graph(draft, plan_id, now)
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


def _read_draft(args) -> Any:
    """Parse the draft plan graph from stdin (or ``args.stdin``)."""
    stream = getattr(args, "stdin", None)
    if stream is None:
        stream = sys.stdin
        if hasattr(stream, "isatty") and stream.isatty():
            raise core.fail(
                core.E_USAGE,
                "plan emit reads the draft plan graph as JSON on stdin; none was supplied",
            )
    try:
        text = stream.read()
    except OSError as exc:  # pragma: no cover - unreadable stdin
        raise core.fail(core.E_READ, "cannot read stdin: %s" % (exc,))
    if not text.strip():
        raise core.fail(core.E_PARSE, "the draft plan graph on stdin is empty", file="<stdin>")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise core.fail(core.E_PARSE, "invalid JSON: %s" % (exc,), file="<stdin>", line=exc.lineno)


def _normalize_graph(
    draft: Dict[str, Any], plan_id: str, now: str
) -> Tuple[Dict[str, Any], List[core.Diagnostic]]:
    """Normalise an untrusted draft into ``plan-graph`` shape.

    Everything mechanical is derived here -- waves from each story's ``wave``,
    parallel groups from the wave's siblings, ``repos`` from the stories, the
    story filename from its id -- and everything else is carried through for the
    schema to accept or refuse.
    """
    graph: Dict[str, Any] = dict(draft)
    graph["version"] = draft.get("version", PLAN_GRAPH_VERSION)
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

    normalized: Dict[str, Any] = {}
    for story_id, raw in stories.items():
        normalized[story_id] = _normalize_story(story_id, raw, waves)
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
    return graph, []


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
    for entry in declared:
        if not isinstance(entry, dict):
            continue
        wave = entry.get("wave")
        stories = entry.get("stories")
        if isinstance(wave, bool) or not isinstance(wave, int) or not isinstance(stories, list):
            continue
        ordering[wave] = [story for story in stories if isinstance(story, str)]
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
    return [{"wave": wave, "stories": list(ordering[wave])} for wave in sorted(ordering)], []


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
    return {
        "version": PLAN_STATE_VERSION,
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
    plans[plan_id] = {
        "status": "pending",
        "project_change": graph.get("project_change"),
        "plan_dir": _plan_dir_rel(plan_id),
        "updated": now,
    }
    return {"version": LEDGER_VERSION, "updated": now, "plans": plans}


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
    context = _story_context(graph, story_id, story, repo, catalog)

    rules = _e2e_rules()
    body = _template_body()
    rendered = _render_story(body, context, rules)

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
        "wave": context["wave"],
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


def _story_context(
    graph: Dict[str, Any], story_id: str, story: Dict[str, Any], repo: str, catalog: Dict[str, Any]
) -> Dict[str, Any]:
    waves = _graph_waves(graph)
    numbers = [wave["wave"] for wave in waves if isinstance(wave.get("wave"), int)]
    total = len(numbers)
    last = max(numbers) if numbers else None
    wave = story.get("wave")
    prerequisites = [item for item in story.get("prerequisites", []) if isinstance(item, str)]
    siblings = [item for item in story.get("parallel_group", []) if isinstance(item, str)]
    return {
        "repo": repo,
        "module": str(story.get("module") or ""),
        "layer": _module_layer(catalog, story.get("module")),
        "wave": wave,
        "total_waves": total,
        "last_wave": wave == last,
        "incremental": graph.get("type") == "incremental",
        "prerequisites": [_story_filename(graph, item) for item in prerequisites],
        "parallel_group": [_story_filename(graph, item) for item in siblings],
        "change_file": story.get("change_file"),
        "target_paths": [item for item in story.get("target_paths", []) if isinstance(item, str)],
        "e2e": _catalog_covers_e2e(catalog),
    }


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


def _e2e_rules() -> str:
    """The E2E Testing Hard Rules, read verbatim from their owning section.

    ``STANDARD-SPEC.md`` §"E2E Testing Hard Rules" owns the four rules; this
    returns the section's lead-in line and the four bullets exactly as written
    there, so the delivered copy is generated rather than maintained by hand.
    """
    display = "shared/STANDARD-SPEC.md"
    text = core.read_text(STANDARD_SPEC, display)
    lines = text.split("\n")
    try:
        start = next(
            index for index, line in enumerate(lines) if line.strip() == E2E_SECTION_HEADING
        )
    except StopIteration:
        raise core.fail(
            core.E_NOT_FOUND, "no section %r" % (E2E_SECTION_HEADING,), file=display
        )
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
            "expected four rules under %r, found %d" % (E2E_SECTION_HEADING, len(bullets)),
            file=display,
        )
    block = bullets if lead is None else [lead, ""] + bullets
    return "\n".join(block)


def _render_story(body: str, context: Dict[str, Any], rules: str) -> str:
    """Gate the template's conditional blocks, substitute, and clean up."""
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
    for needle, value in replacements:
        text = text.replace(needle, value)
    return text


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

    pairs = _story_repo_modules(graph)
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

    for tag in _cross_repo_tags(ws, graph):
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

    data = {"plan_id": plan_id, "granularity": granularity, "shards": shards}
    return core.Result(command=command, data=data)


def _story_repo_modules(graph: Dict[str, Any]) -> List[Tuple[str, str]]:
    """Every story's ``(repo, module)``, deduped and sorted."""
    stories = graph.get("stories")
    if not isinstance(stories, dict):
        return []
    pairs = set()
    for story in stories.values():
        if not isinstance(story, dict):
            continue
        repo, module = story.get("repo"), story.get("module")
        if isinstance(repo, str) and isinstance(module, str):
            pairs.add((repo, module))
    return sorted(pairs)


def _cross_repo_tags(ws, graph: Dict[str, Any]) -> List[str]:
    """The shared-interface TAGs named by this graph's ``scope: shared``
    change documents -- read via ``core.load_front_matter``, never through
    ``tools/change.py``. No ``context/shared/`` tree yields no entries and no
    diagnostic: a single-repo workspace is conforming, not defective."""
    tags = set()
    for path in _change_document_paths(ws, graph):
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


def _change_document_paths(ws, graph: Dict[str, Any]) -> List[Path]:
    """Every change document the graph references: each story's
    ``change_file`` plus the project index its ``project_change`` names."""
    paths: List[Path] = []
    seen = set()
    stories = graph.get("stories")
    if isinstance(stories, dict):
        for story_id in sorted(stories):
            story = stories[story_id]
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
    "emit": _emit,
    "story-emit": _story_emit,
    "shards": _shards,
}
