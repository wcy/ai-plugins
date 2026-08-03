#!/usr/bin/env python3
"""DEPRECATED compatibility shim -- use ``tools/mc.py validate`` instead.

    python3 ${CLAUDE_PLUGIN_ROOT}/schemas/validate.py <kind> <file> [<file> ...]

Forwards its arguments to the entry point and carries no logic of its own: it
replaces this process with ``mc.py validate``, so stdout, stderr and the exit
code are the entry point's, unchanged. It exports no importable API -- the
engine belongs to TOOLS (see TOOLS-INTERFACE.md, "Compatibility shim").
"""

import os
import sys

_ENTRY_POINT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "mc.py"
)

if __name__ == "__main__":
    sys.stdout.flush()
    sys.stderr.flush()
    os.execv(sys.executable, [sys.executable, _ENTRY_POINT, "validate"] + sys.argv[1:])
