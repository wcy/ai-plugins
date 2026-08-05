"""The deprecated ``schemas/validate.py`` shim.

Two claims are checked, and they are deliberately different claims:

1. ``mc.py validate`` reproduces the **golden bytes** -- the stdout and exit
   code the previous ``schemas/validate.py`` emitted, captured by running that
   script over these exact fixtures *before* it was replaced. Comparing the new
   command against the new shim alone would be self-referential: it would pass
   whatever format the new command happened to emit. These bytes are the record
   of the old format, which is otherwise unrecoverable from the repo.
2. The shim's stdout, stderr and exit code are identical to the entry point's,
   for every kind, every exit path (0, 1, 2) and multi-file invocations.

``{d}`` in a golden template stands for the fixture directory, which is built
in ``tmp_path``: no fixture file is committed under ``plugins/metacoder/``.
"""

import sys
from pathlib import Path

import pytest

from conftest import INVALID_INSTANCES, KIND_ALIASES, VALID_INSTANCES

# --- fixture bodies the golden bytes were captured against -----------------

EXTRA_FILES = {
    "second.yaml": VALID_INSTANCES["catalog"][1],
    "malformed.yaml": "not: [yaml\n",
    "notes.txt": "hello\n",
}

# --- golden stdout, captured from the previous schemas/validate.py ---------
#
# Each entry is (argv-after-the-kind-less prefix, expected stdout, exit code).
# `OK` is followed by FOUR spaces; `FAIL` by TWO; each error line by EIGHT
# spaces and `- `.

GOLDEN = [
    # -- exit 0: every canonical kind ---------------------------------------
    (["catalog", "{d}/catalog.yaml"], "OK    {d}/catalog.yaml (catalog)\n", 0),
    (["change-frontmatter", "{d}/change.md"], "OK    {d}/change.md (change-frontmatter)\n", 0),
    (
        ["conformance-report", "{d}/conformance.json"],
        "OK    {d}/conformance.json (conformance-report)\n",
        0,
    ),
    (
        ["inconsistency-report", "{d}/inconsistency.json"],
        "OK    {d}/inconsistency.json (inconsistency-report)\n",
        0,
    ),
    (["plan-graph", "{d}/plan.yaml"], "OK    {d}/plan.yaml (plan-graph)\n", 0),
    (["plan-state", "{d}/plan-state.yaml"], "OK    {d}/plan-state.yaml (plan-state)\n", 0),
    (["project-state", "{d}/state.yaml"], "OK    {d}/state.yaml (project-state)\n", 0),
    (
        ["requirements-frontmatter", "{d}/requirements.md"],
        "OK    {d}/requirements.md (requirements-frontmatter)\n",
        0,
    ),
    (
        ["req-change-frontmatter", "{d}/req-change.md"],
        "OK    {d}/req-change.md (req-change-frontmatter)\n",
        0,
    ),
    (["story-report", "{d}/story.json"], "OK    {d}/story.json (story-report)\n", 0),
    # -- exit 0: every alias -------------------------------------------------
    (["change", "{d}/change.md"], "OK    {d}/change.md (change)\n", 0),
    (["plan", "{d}/plan.yaml"], "OK    {d}/plan.yaml (plan)\n", 0),
    (["ledger", "{d}/state.yaml"], "OK    {d}/state.yaml (ledger)\n", 0),
    (
        ["conformance", "{d}/conformance.json"],
        "OK    {d}/conformance.json (conformance)\n",
        0,
    ),
    (["story", "{d}/story.json"], "OK    {d}/story.json (story)\n", 0),
    (
        ["inconsistency", "{d}/inconsistency.json"],
        "OK    {d}/inconsistency.json (inconsistency)\n",
        0,
    ),
    (
        ["requirements", "{d}/requirements.md"],
        "OK    {d}/requirements.md (requirements)\n",
        0,
    ),
    (
        ["req-change", "{d}/req-change.md"],
        "OK    {d}/req-change.md (req-change)\n",
        0,
    ),
    # -- exit 0: the explicit <name>.schema.json form ------------------------
    (
        ["catalog.schema.json", "{d}/catalog.yaml"],
        "OK    {d}/catalog.yaml (catalog.schema.json)\n",
        0,
    ),
    # -- exit 1: schema failure ---------------------------------------------
    (
        ["catalog", "{d}/bad-catalog.yaml"],
        "FAIL  {d}/bad-catalog.yaml (catalog):\n"
        "        - <root>: {{'version': 1, 'repo': 'demo'}} is not valid under any of the given schemas\n",
        1,
    ),
    (
        ["story", "{d}/bad-story.json"],
        "FAIL  {d}/bad-story.json (story):\n        - <root>: 'repo' is a required property\n",
        1,
    ),
    # -- exit 1: a missing file fails alone (its FAIL line is on stderr) -----
    (["catalog", "{d}/missing.yaml"], "", 1),
    (
        ["catalog", "{d}/catalog.yaml", "{d}/missing.yaml"],
        "OK    {d}/catalog.yaml (catalog)\n",
        1,
    ),
    (
        ["catalog", "{d}/missing.yaml", "{d}/catalog.yaml"],
        "OK    {d}/catalog.yaml (catalog)\n",
        1,
    ),
    # -- multi-file invocations ---------------------------------------------
    (
        ["catalog", "{d}/catalog.yaml", "{d}/second.yaml"],
        "OK    {d}/catalog.yaml (catalog)\nOK    {d}/second.yaml (catalog)\n",
        0,
    ),
    (
        ["catalog", "{d}/catalog.yaml", "{d}/bad-catalog.yaml"],
        "OK    {d}/catalog.yaml (catalog)\n"
        "FAIL  {d}/bad-catalog.yaml (catalog):\n"
        "        - <root>: {{'version': 1, 'repo': 'demo'}} is not valid under any of the given schemas\n",
        1,
    ),
    # -- exit 2: refusals abort the batch after flushing what came before ----
    (["nosuchkind", "{d}/catalog.yaml"], "", 2),
    (["catalog"], "", 2),
    (
        ["catalog", "{d}/catalog.yaml", "{d}/notes.txt"],
        "OK    {d}/catalog.yaml (catalog)\n",
        2,
    ),
    (
        ["catalog", "{d}/catalog.yaml", "{d}/malformed.yaml"],
        "OK    {d}/catalog.yaml (catalog)\n",
        2,
    ),
]


@pytest.fixture
def fixtures(workspace):
    """Every fixture the golden bytes were captured against, in ``tmp_path``."""
    directory = workspace.path("instances")
    directory.mkdir(parents=True, exist_ok=True)
    for kind in VALID_INSTANCES:
        workspace.add_instance(kind)
    for kind in INVALID_INSTANCES:
        workspace.add_instance(kind, table=INVALID_INSTANCES)
    for name, body in EXTRA_FILES.items():
        workspace.write(Path("instances") / name, body)
    assert not (directory / "missing.yaml").exists()
    return directory


def _expand(argv, directory):
    return [item.format(d=directory) for item in argv]


def _golden_id(case):
    return " ".join(case[0])


@pytest.mark.parametrize("case", GOLDEN, ids=[_golden_id(c) for c in GOLDEN])
def test_mc_validate_reproduces_the_golden_bytes(workspace, fixtures, case):
    argv, expected_stdout, expected_code = case
    expanded = _expand(argv, fixtures)
    completed = workspace.run_cli("validate", *expanded)
    assert completed.stdout == expected_stdout.format(d=fixtures).encode()
    assert completed.returncode == expected_code


@pytest.mark.parametrize("case", GOLDEN, ids=[_golden_id(c) for c in GOLDEN])
def test_shim_matches_the_entry_point(workspace, fixtures, case):
    argv, expected_stdout, expected_code = case
    expanded = _expand(argv, fixtures)
    through_mc = workspace.run_cli("validate", *expanded)
    through_shim = workspace.run_shim(*expanded)

    assert through_shim.stdout == through_mc.stdout
    assert through_shim.stderr == through_mc.stderr
    assert through_shim.returncode == through_mc.returncode
    # ... and therefore also the golden bytes.
    assert through_shim.stdout == expected_stdout.format(d=fixtures).encode()
    assert through_shim.returncode == expected_code


def test_the_golden_set_covers_every_kind_and_alias():
    kinds = {argv[0] for argv, _stdout, _code in GOLDEN}
    for kind in VALID_INSTANCES:
        assert kind in kinds
    for alias in KIND_ALIASES:
        assert alias in kinds
    assert {code for _argv, _stdout, code in GOLDEN} == {0, 1, 2}


# ---------------------------------------------------------------------------
# The shim carries no logic and exposes no importable API
# ---------------------------------------------------------------------------


def test_shim_is_short_and_free_of_the_retired_surface():
    from conftest import SHIM

    source = SHIM.read_text(encoding="utf-8")
    assert len(source.splitlines()) < 40
    for retired in ("ALIASES", "load_schema", "load_instance", "resolve_ref", "_validate"):
        assert retired not in source


def test_shim_exposes_no_importable_api():
    """Importing the shim must define no callable surface of its own."""
    import importlib.util

    from conftest import SHIM

    spec = importlib.util.spec_from_file_location("_deprecated_shim", SHIM)
    module = importlib.util.module_from_spec(spec)
    saved = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = saved
    public = [
        name
        for name in vars(module)
        if not name.startswith("_") and callable(getattr(module, name))
    ]
    assert public == []
    for retired in ("ALIASES", "load_schema", "load_instance", "resolve_ref"):
        assert not hasattr(module, retired)
