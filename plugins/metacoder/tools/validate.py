"""The ``validate`` command group.

    mc.py validate <kind> <file> [<file> ...]

``<kind>`` is a canonical schema basename, one of the friendly aliases, or an
explicit ``<name>.schema.json``. Each file is validated independently against
the resolved schema; a missing file fails that file alone and does not abort the
batch. Everything else -- an unresolvable kind, a path escaping the workspace,
an unreadable or unparseable instance -- aborts the batch with exit 2, after the
files already processed have produced their output.

The engine lives in ``tools/core.py``; this module is the batch loop, the
argument declaration, and the registration of the eleventh kind.
"""

from __future__ import annotations

from tools import core

COMMAND = "validate"

# ---------------------------------------------------------------------------
# The eleventh kind, per SCHEMAS-INTERFACE.md
# ---------------------------------------------------------------------------

#: ``slice-report`` -- MSHIP's per-slice return. The canonical basename already
#: resolves on its own, because ``core.resolve_kind`` looks up
#: ``<basename>.schema.json`` in the schema directory; what it is registered for
#: here is the *offer* an unknown-kind diagnostic makes. The ``slice`` alias has
#: to be registered, because an alias exists only in the table.
SLICE_REPORT_KIND = "slice-report"
SLICE_ALIAS = "slice"


def _register_slice_report() -> None:
    """Add the eleventh kind and its alias to ``core``'s tables, once.

    Registered from the group that owns ``<kind>`` resolution rather than
    restated in ``core``, and idempotent, so importing this module twice cannot
    duplicate an entry. ``CANONICAL_KINDS`` stays sorted, which is the order the
    unknown-kind diagnostic lists them in.
    """
    if SLICE_REPORT_KIND not in core.CANONICAL_KINDS:
        core.CANONICAL_KINDS = tuple(sorted(core.CANONICAL_KINDS + (SLICE_REPORT_KIND,)))
    core.KIND_ALIASES.setdefault(SLICE_ALIAS, SLICE_REPORT_KIND)


_register_slice_report()


def register(subparsers) -> None:
    """Declare ``validate``'s arguments on ``mc.py``'s subparser action."""
    parser = subparsers.add_parser(
        COMMAND,
        help="validate files against a metacoder JSON Schema",
        description="Validate one or more files against a metacoder JSON Schema.",
    )
    parser.add_argument(
        "kind",
        help="canonical schema basename, a friendly alias, or <name>.schema.json",
    )
    parser.add_argument(
        "files",
        nargs="+",
        metavar="file",
        help="instance files (.yaml/.yml/.json/.md)",
    )
    parser.set_defaults(group=COMMAND)


def run(args, ws) -> core.Result:
    """Validate every file in ``args.files`` against ``args.kind``."""
    kind = args.kind
    lines = []
    files = []
    diagnostics = []
    try:
        core.resolve_kind(kind)  # fail fast, before any file is touched
        for given in args.files:
            outcome = core.validate_instance(kind, given, ws)
            lines.extend(outcome.data["lines"])
            files.extend(outcome.data["files"])
            diagnostics.extend(outcome.diagnostics)
    except core.ToolError as exc:
        diagnostics.append(exc.diagnostic)
    return core.Result(
        command=COMMAND,
        data={"kind": kind, "files": files, "lines": lines},
        diagnostics=diagnostics,
    )
