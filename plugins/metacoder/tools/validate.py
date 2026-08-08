"""The ``validate`` command group.

    mc.py validate <kind> <file> [<file> ...]

``<kind>`` is a canonical schema basename, one of the friendly aliases, or an
explicit ``<name>.schema.json``. Each file is validated independently against
the resolved schema; a missing file fails that file alone and does not abort the
batch. Everything else -- an unresolvable kind, a path escaping the workspace,
an unreadable or unparseable instance -- aborts the batch with exit 2, after the
files already processed have produced their output.

The engine lives in ``tools/core.py``; this module is the batch loop, the
argument declaration, and the registration of the eleventh and twelfth kinds.
"""

from __future__ import annotations

from tools import core

COMMAND = "validate"

# ---------------------------------------------------------------------------
# The eleventh and twelfth kinds, per SCHEMAS-INTERFACE.md
# ---------------------------------------------------------------------------

#: ``slice-report`` -- MSHIP's per-slice return. The canonical basename already
#: resolves on its own, because ``core.resolve_kind`` looks up
#: ``<basename>.schema.json`` in the schema directory; what it is registered for
#: here is the *offer* an unknown-kind diagnostic makes. The ``slice`` alias has
#: to be registered, because an alias exists only in the table.
SLICE_REPORT_KIND = "slice-report"
SLICE_ALIAS = "slice"

#: ``todo-frontmatter`` -- the two-line front-matter block at the top of
#: ``context/project/TODO.md``. Registered here for the same two reasons the
#: eleventh kind is: the canonical basename resolves on its own but is not
#: *offered*, and the ``todo`` alias exists only in the table.
TODO_FRONT_MATTER_KIND = "todo-frontmatter"
TODO_ALIAS = "todo"

#: ``(canonical, alias)`` for every kind this group registers, in the order
#: ``SCHEMAS-INTERFACE.md`` introduces them. One table, one loop: a thirteenth
#: kind is a row here rather than a third near-identical function.
REGISTERED_KINDS = (
    (SLICE_REPORT_KIND, SLICE_ALIAS),
    (TODO_FRONT_MATTER_KIND, TODO_ALIAS),
)


def _register_kinds() -> None:
    """Add this group's kinds and aliases to ``core``'s tables, once.

    Registered from the group that owns ``<kind>`` resolution rather than
    restated in ``core``, and idempotent, so importing this module twice cannot
    duplicate an entry. ``CANONICAL_KINDS`` stays sorted, which is the order the
    unknown-kind diagnostic lists them in.
    """
    for canonical, alias in REGISTERED_KINDS:
        if canonical not in core.CANONICAL_KINDS:
            core.CANONICAL_KINDS = tuple(sorted(core.CANONICAL_KINDS + (canonical,)))
        core.KIND_ALIASES.setdefault(alias, canonical)


#: The pre-existing name for :func:`_register_kinds`, kept because the suite
#: exercises the registration's idempotence by calling it.
_register_slice_report = _register_kinds

_register_kinds()


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
