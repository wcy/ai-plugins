#!/usr/bin/env python3
"""``mc.py`` -- the single entry point for every deterministic metacoder step.

    python3 ${CLAUDE_PLUGIN_ROOT}/tools/mc.py [global-options] <group> <verb> [arguments]

This file holds argument parsing and dispatch and nothing else: every algorithm
lives in ``tools/core.py`` or in a command-group module, each of which exposes
``register(subparsers)`` and ``run(args, ws) -> Result`` and is callable without
touching ``argv``.

Group modules are imported lazily, so a group that is not implemented yet is
reported only when it is invoked -- never at parse time, never on ``--help``,
and never as a startup diagnostic.
"""

import sys

# Set before the package is imported, so a run leaves no __pycache__ in the
# plugin's marketplace cache (COMMON-PACKAGING.md, REQ-011).
sys.dont_write_bytecode = True

import argparse  # noqa: E402
import importlib  # noqa: E402
from pathlib import Path  # noqa: E402

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

from tools import core  # noqa: E402

#: The fixed group list. Dispatch is data-driven over exactly these names.
GROUPS = (
    "validate",
    "change",
    "spec",
    "req",
    "plan",
    "state",
    "worktree",
    "todo",
    "check",
    "status",
)

_EPILOG = "command groups:\n" + "".join("  %s\n" % name for name in GROUPS)


def build_parser() -> argparse.ArgumentParser:
    """The global parser: options, the group name, and the group's own argv."""
    parser = argparse.ArgumentParser(
        prog="mc.py",
        description="Deterministic mechanical steps for the metacoder pipeline.",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--workspace",
        metavar="ROOT",
        default=None,
        help="workspace root; all I/O is confined to it (default: the current directory)",
    )
    parser.add_argument(
        "--now",
        metavar="ISO-8601",
        default=None,
        help="injected clock for every timestamp this run writes (default: the system clock)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_out",
        help="emit the Result envelope verbatim on stdout",
    )
    parser.add_argument("group", choices=GROUPS, help="command group")
    parser.add_argument(
        "rest",
        nargs=argparse.REMAINDER,
        metavar="...",
        help="the group's own verb and arguments",
    )
    return parser


def load_group(name: str):
    """Import one group module, reporting a diagnostic rather than a traceback."""
    core.check_ident(name, "command group")
    try:
        return importlib.import_module("tools.%s" % name)
    except ImportError as exc:
        raise core.fail(
            core.E_GROUP_UNAVAILABLE,
            "command group %r is not available: %s" % (name, exc),
        )


def parse_group_args(module, name: str, rest) -> argparse.Namespace:
    """Let the group declare its own arguments, then parse them."""
    parser = argparse.ArgumentParser(prog="mc.py")
    subparsers = parser.add_subparsers(dest="group")
    module.register(subparsers)
    return parser.parse_args([name] + list(rest))


def main(argv=None) -> int:
    argv = list(sys.argv if argv is None else argv)
    args = build_parser().parse_args(argv[1:])
    try:
        workspace = core.Workspace.resolve(args.workspace)
        now = core.resolve_instant(args.now)
        core.require_prereqs()
        module = load_group(args.group)
        group_args = parse_group_args(module, args.group, args.rest)
        group_args.workspace = workspace
        group_args.now = now
        group_args.json_out = args.json_out
        result = module.run(group_args, workspace)
    except core.ToolError as exc:
        result = core.Result(command=args.group, diagnostics=[exc.diagnostic])
    core.emit(result, json_out=args.json_out)
    return core.exit_code(result)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
