"""Shared fixtures for the TOOLS suite.

Every fixture builds a *synthetic* workspace in ``tmp_path``: no fixture file is
committed under ``plugins/metacoder/``, because the whole plugin tree ships into
every user's marketplace cache and test data is not part of what they installed.

Group modules are imported and called directly, never through ``argv``, and the
injected clock (``--now``) is supplied by :data:`NOW` -- no test reads the wall
clock.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

# `mc.py` sets this before importing the package, but under pytest the modules
# are imported directly, so the no-__pycache__ guarantee has to be re-stated
# here -- before any `tools` import -- and exported so subprocesses inherit it.
sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGIN_ROOT = REPO_ROOT / "plugins" / "metacoder"
MC = PLUGIN_ROOT / "tools" / "mc.py"
SHIM = PLUGIN_ROOT / "schemas" / "validate.py"

if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

#: The injected clock. Every test that needs a timestamp uses this.
NOW = "2026-01-15"

#: One valid instance per canonical kind: kind -> (filename, content).
VALID_INSTANCES = {
    "catalog": (
        "catalog.yaml",
        "version: 1\n"
        "repo: demo\n"
        "layers:\n"
        "  L1-core:\n"
        "    modules: [ALPHA]\n"
        "modules:\n"
        "  ALPHA:\n"
        "    layer: L1-core\n"
        "    files:\n"
        "      - path: context/demo/spec/ALPHA/ALPHA-OVERVIEW.md\n"
        "        facet: overview\n",
    ),
    "change-frontmatter": (
        "change.md",
        "<!-- change: 001 -->\n"
        "<!-- scope: repo -->\n"
        "<!-- repo: demo -->\n"
        "<!-- status: pending -->\n"
        "<!-- date: 2026-01-01 -->\n"
        "\n# CHANGE-001\n",
    ),
    "conformance-report": (
        "conformance.json",
        '{\n  "scope": {\n    "kind": "aggregate"\n  },\n  "findings": [],\n  "clean": true\n}\n',
    ),
    "inconsistency-report": (
        "inconsistency.json",
        '{\n  "scope": {\n    "kind": "intra-repo",\n    "repo": "demo"\n  },\n  "findings": []\n}\n',
    ),
    "plan-graph": (
        "plan.yaml",
        "version: 2\n"
        "plan_id: 001-demo\n"
        "type: full\n"
        "repos:\n"
        "  - demo\n"
        "waves:\n"
        "  - wave: 1\n"
        "    stories:\n"
        "      - 01-01-demo-ALPHA\n"
        "stories:\n"
        "  01-01-demo-ALPHA:\n"
        "    file: PLAN-01-01-demo-ALPHA.md\n"
        "    repo: demo\n"
        "    module: ALPHA\n"
        "    wave: 1\n"
        "    prerequisites: []\n"
        "    target_paths:\n"
        "      - src/alpha.py\n"
        "    validation:\n"
        "      post_story:\n"
        "        - kind: prose\n"
        "          description: it works\n",
    ),
    "plan-state": (
        "plan-state.yaml",
        "version: 2\n"
        "plan_id: 001-demo\n"
        "run: 0\n"
        "status: pending\n"
        "stories:\n"
        "  01-01-demo-ALPHA:\n"
        "    repo: demo\n"
        "    wave: 1\n"
        "    status: pending\n"
        "    retries: 0\n",
    ),
    "project-state": (
        "state.yaml",
        "version: 1\n"
        "plans:\n"
        "  001-demo:\n"
        "    status: pending\n"
        "    plan_dir: context/project/plans/001-demo/\n",
    ),
    "requirements-frontmatter": (
        "requirements.md",
        "<!-- requirements: demo -->\n<!-- updated: 2026-01-01 -->\n\n# Requirements\n",
    ),
    "story-report": (
        "story.json",
        '{\n'
        '  "story_id": "01-01-demo-ALPHA",\n'
        '  "repo": "demo",\n'
        '  "run": 1,\n'
        '  "attempt": 1,\n'
        '  "status": "applied",\n'
        '  "branch": "mexec/001-demo/01-01-demo-ALPHA/r1/1"\n'
        '}\n',
    ),
}

#: alias -> canonical kind, per SCHEMAS-INTERFACE.md.
KIND_ALIASES = {
    "change": "change-frontmatter",
    "plan": "plan-graph",
    "ledger": "project-state",
    "conformance": "conformance-report",
    "story": "story-report",
    "inconsistency": "inconsistency-report",
    "requirements": "requirements-frontmatter",
}

#: One instance per kind that parses but fails its schema.
INVALID_INSTANCES = {
    "catalog": ("bad-catalog.yaml", "version: 1\nrepo: demo\n"),
    "story-report": ("bad-story.json", '{\n  "story_id": "nope"\n}\n'),
}


class SyntheticWorkspace:
    """A workspace root built in ``tmp_path``, plus helpers to populate it."""

    def __init__(self, root):
        self.root = Path(root).resolve()
        (self.root / "repos").mkdir(parents=True, exist_ok=True)
        (self.root / "context").mkdir(parents=True, exist_ok=True)

    # -- building -----------------------------------------------------------
    def write(self, relative, text):
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        return target

    def mkdir(self, relative):
        target = self.root / relative
        target.mkdir(parents=True, exist_ok=True)
        return target

    def path(self, relative):
        return self.root / relative

    def add_repo(self, name):
        return self.mkdir(Path("repos") / name)

    def add_target(self, name):
        return self.mkdir(Path("context") / name / "spec")

    def add_instance(self, kind, table=None):
        """Write the canned instance for ``kind`` and return its path."""
        source = VALID_INSTANCES if table is None else table
        filename, content = source[kind]
        return self.write(Path("instances") / filename, content)

    def add_all_instances(self):
        return {kind: self.add_instance(kind) for kind in VALID_INSTANCES}

    # -- using --------------------------------------------------------------
    @property
    def ws(self):
        from tools import core

        return core.Workspace(self.root)

    def args(self, **fields):
        """An argv-free stand-in for the namespace ``mc.py`` builds."""
        import argparse

        fields.setdefault("now", NOW)
        fields.setdefault("json_out", False)
        fields.setdefault("workspace", self.root)
        return argparse.Namespace(**fields)

    def run_cli(self, *argv, cwd=None):
        """Run ``mc.py`` as a subprocess. Returns ``CompletedProcess`` (bytes)."""
        return subprocess.run(
            [sys.executable, str(MC)] + [str(item) for item in argv],
            capture_output=True,
            cwd=str(cwd or self.root),
        )

    def run_shim(self, *argv, cwd=None):
        """Run the deprecated ``schemas/validate.py`` shim as a subprocess."""
        return subprocess.run(
            [sys.executable, str(SHIM)] + [str(item) for item in argv],
            capture_output=True,
            cwd=str(cwd or self.root),
        )


@pytest.fixture
def workspace(tmp_path):
    """A synthetic workspace rooted in ``tmp_path``."""
    return SyntheticWorkspace(tmp_path)


@pytest.fixture
def multi_repo_workspace(tmp_path):
    """Two consumer repos plus a ``context/shared/`` tree with a ``scope:
    shared`` cascade -- the only fixture that can exercise ``plan shards``'s
    cross-repo entries and ``check catalog`` against a shared catalog, neither
    of which the single-repo ``workspace`` fixture can reach.

    ``repo-a`` and ``repo-b`` each list the ``AUTH`` TAG under
    ``shared_interfaces`` and carry a ``scope: shared`` repo-level change file
    naming the shared interface path; the project index carries the matching
    ``consumers:`` front-matter.
    """
    ws = SyntheticWorkspace(tmp_path)

    ws.write(
        "context/shared/spec/CATALOG.yaml",
        "version: 1\n"
        "scope: shared\n"
        "interfaces:\n"
        "  AUTH:\n"
        "    files:\n"
        "      - path: context/shared/spec/AUTH/AUTH-OVERVIEW.md\n"
        "        facet: overview\n"
        "      - path: context/shared/spec/AUTH/AUTH-INTERFACE.md\n"
        "        facet: interface\n"
        "        depends_on:\n"
        "          - context/shared/spec/AUTH/AUTH-OVERVIEW.md\n",
    )
    ws.write("context/shared/spec/AUTH/AUTH-OVERVIEW.md", "# AUTH -- shared interface\n")
    ws.write(
        "context/shared/spec/AUTH/AUTH-INTERFACE.md",
        "<!-- depends-on: context/shared/spec/AUTH/AUTH-OVERVIEW.md -->\n\n# AUTH -- Interface\n",
    )

    for repo in ("repo-a", "repo-b"):
        ws.write(
            "context/%s/spec/CATALOG.yaml" % repo,
            "version: 1\n"
            "repo: %s\n"
            "shared_interfaces: [AUTH]\n"
            "layers:\n"
            "  L1-core:\n"
            "    modules: [WIDGET]\n"
            "modules:\n"
            "  WIDGET:\n"
            "    layer: L1-core\n"
            "    files:\n"
            "      - path: context/%s/spec/WIDGET/WIDGET-OVERVIEW.md\n"
            "        facet: overview\n" % (repo, repo),
        )
        ws.write(
            "context/%s/spec/WIDGET/WIDGET-OVERVIEW.md" % repo,
            "# WIDGET -- %s\n" % repo,
        )
        ws.write(
            "context/%s/changes/CHANGE-001-auth-consumer.md" % repo,
            "<!-- change: 001 -->\n"
            "<!-- scope: shared -->\n"
            "<!-- repo: %s -->\n"
            "<!-- status: pending -->\n"
            "<!-- date: 2026-01-01 -->\n"
            "\n# CHANGE-001: AUTH cascade -- %s\n"
            "\n## Affected Code Paths\n\n"
            "- context/shared/spec/AUTH/AUTH-INTERFACE.md\n" % (repo, repo),
        )

    ws.write(
        "context/project/changes/PROJECT-CHANGE-001-auth-cascade.md",
        "<!-- project-change: 001 -->\n"
        "<!-- scope: shared -->\n"
        "<!-- repos: repo-a, repo-b -->\n"
        "<!-- status: pending -->\n"
        "<!-- consumers: repo-a, repo-b -->\n"
        "<!-- date: 2026-01-01 -->\n"
        "\n# PROJECT-CHANGE-001: AUTH cascade\n"
        "\n## Summary\n\nFreezes AUTH.\n"
        "\n## Repo Change Files\n\n"
        "| Repo | Change File | Summary |\n"
        "|------|-------------|---------|\n"
        "| `repo-a` | `context/repo-a/changes/CHANGE-001-auth-consumer.md` | consumer |\n"
        "| `repo-b` | `context/repo-b/changes/CHANGE-001-auth-consumer.md` | consumer |\n",
    )
    return ws


@pytest.fixture
def instances(workspace):
    """Every canonical kind's valid instance, written into the workspace."""
    return workspace.add_all_instances()
