"""The ``status`` command group -- the whole workspace as a ``StatusReport``.

    mc.py status

`REQ-019` asks for the state of work in progress to be derived from the
workspace rather than from recall: which requirements no module covers, which
changes have no index, which indexes have no plan, which plans are unfinished,
what the conformance sweep said about each, and every artifact stranded or
incomplete between two stages.

All of that is one traversal, and it already exists: ``tools/check.py``'s
:func:`~tools.check.walk_stages`. This module renders that walk into the
``StatusReport`` shape ``TOOLS-DATAMODEL.md`` §"Workspace status" defines and
adds nothing to it -- there is deliberately no second stage walk here, because
two walks are two things to keep in step.

``generated`` comes from the injected clock (``--now``), so two runs against the
same workspace bytes with the same ``--now`` are byte-identical. The report is
data, not a verdict: findings are carried in ``handoff`` rather than raised as
error diagnostics, so ``status`` exits 0 on a workspace that ``check handoff``
exits 1 on.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from tools import check, core

COMMAND = "status"


def build_report(
    ws: core.Workspace, now: str
) -> Tuple[Dict[str, Any], List[core.Diagnostic]]:
    """The ``StatusReport``, field for field, plus the walk's read warnings."""
    walk = check.walk_stages(ws)
    return {
        "generated": now,
        "stages": {
            "requirements": {"uncovered": walk.uncovered, "dangling": walk.dangling},
            "changes": {
                "pending": walk.pending_changes,
                "unindexed": walk.unindexed_changes,
            },
            "plans": {
                "unplanned_indexes": walk.unplanned_indexes,
                "unfinished": walk.unfinished_plans,
            },
            "conformance": walk.conformance,
        },
        "handoff": [finding.to_dict() for finding in walk.findings],
    }, walk.diagnostics


def register(subparsers) -> None:
    """Declare ``status`` on ``mc.py``'s subparser action.

    The group has no verb: ``mc.py status`` is the whole interface.
    """
    parser = subparsers.add_parser(
        COMMAND,
        help="the whole workspace as a StatusReport",
        description=(
            "Report uncovered and dangling requirements, pending and unindexed changes, "
            "unplanned indexes and unfinished plans, conformance status per plan, and "
            "the handoff findings. Derived from the workspace, never from recall."
        ),
    )
    parser.set_defaults(group=COMMAND, verb=None)


def run(args, ws: core.Workspace) -> core.Result:
    """Build the report. Never raises; failures are diagnostics."""
    now = getattr(args, "now", None) or core.system_instant()
    try:
        report, diagnostics = build_report(ws, now)
    except core.ToolError as exc:
        return core.Result(command=COMMAND, diagnostics=[exc.diagnostic])
    return core.Result(command=COMMAND, data=report, diagnostics=list(diagnostics))
