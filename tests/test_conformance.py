"""Conformance to the documents that own the conventions.

``TOOLS-TESTING.md`` §"Conformance to the standards" makes three claims about
what ``TOOLS`` emits, and this suite turns each into a mechanical check so a
divergence fails a test instead of being argued:

* **Every emitted artifact validates against its schema.** The artifact-to-kind
  mapping is read out of ``schemas/README.md``'s producer/consumer table -- the
  document that owns it -- rather than transcribed here.
* **Emitted change documents carry the front-matter keys and section headings
  ``STANDARD-CHANGE.md`` requires.** The expected keys and headings are derived
  by reading that document; a list copied into this file would be a second
  authored copy of the same convention and would drift from the first.
* **The E2E rules ``plan story-emit`` injects match ``STANDARD-SPEC.md``
  §"E2E Testing Hard Rules" verbatim**, at both injection points. The check is
  shown to have teeth by a negative case: a *copy* of the standard in
  ``tmp_path`` is perturbed by one word and the comparison must report the
  mismatch. The real ``plugins/metacoder/shared/STANDARD-SPEC.md`` is never
  written to, and the case asserts that too.
* **The Delivered-Surface Rule it injects matches §"Delivered-Surface Rule"
  verbatim**, asserted the same way and with the same negative case -- and
  additionally asserted to render on **every** story, including one emitted in
  a repo whose catalog names no E2E module. That last case is the whole
  difference between the two injections: the E2E rules carry a catalog
  condition and this one carries none.

The standards stay authoritative on disagreement: ``TOOLS`` is the consumer
obliged to conform, so a failure here is a defect in the tool, not a cue to
relax the check.

Also here, because they are the same kind of claim about the tool applied to the
tool: ``TOOLS-IMPLEMENTATION.md`` §"Package shape" -- a run leaves no
``__pycache__`` under ``plugins/metacoder/`` -- and ``TOOLS-TESTING.md``
§"Not covered": no test invokes an agent, a network service, or ``git``, and no
fixture file is committed under the plugin tree.
"""

import ast
import fnmatch
import importlib
import inspect
import io
import json
import os
import re
import shutil
import sys
import argparse
import re
from pathlib import Path

import pytest

import conftest
from conftest import NOW, PLUGIN_ROOT, REPO_ROOT
from tools import check, core, mc, plan, spec

TESTS_DIR = Path(__file__).resolve().parent
TOOLS_DIR = PLUGIN_ROOT / "tools"
SHARED_DIR = PLUGIN_ROOT / "shared"
SCHEMAS_README = PLUGIN_ROOT / "schemas" / "README.md"
STANDARD_CHANGE = SHARED_DIR / "STANDARD-CHANGE.md"
STANDARD_SPEC = SHARED_DIR / "STANDARD-SPEC.md"
STANDARD_REQ = SHARED_DIR / "STANDARD-REQ.md"

E2E_HEADING = "## E2E Testing Hard Rules"
DELIVERED_SURFACE_HEADING = "## Delivered-Surface Rule"

PLAN_ID = "001-retry-policy"
FIRST_STORY = "01-01-demo-ALPHA"
LAST_STORY = "02-01-demo-BETA"
SEED_NOW = "2026-01-01"


# ---------------------------------------------------------------------------
# Markdown readers. Everything this suite expects is read out of the document
# that owns it -- these are the readers, and they are the only authored part.
# ---------------------------------------------------------------------------


def section(text, heading):
    """The body under ``heading``, up to the next heading of the same level or higher.

    Fence-aware: ``STANDARD-CHANGE.md`` documents each change-document shape as a
    fenced example that contains its own ``#`` headings, and a reader that
    stopped at one of those would return half a section.
    """
    level = len(heading) - len(heading.lstrip("#"))
    lines = text.split("\n")
    assert heading in lines, "no %r section" % (heading,)
    body = []
    fenced_open = False
    for line in lines[lines.index(heading) + 1 :]:
        if line.startswith("```"):
            fenced_open = not fenced_open
        elif (
            not fenced_open
            and line.startswith("#")
            and len(line) - len(line.lstrip("#")) <= level
        ):
            break
        body.append(line)
    return "\n".join(body)


def fenced(text):
    """The first fenced code block in ``text``."""
    lines = text.split("\n")
    start = next(index for index, line in enumerate(lines) if line.startswith("```"))
    end = next(index for index in range(start + 1, len(lines)) if lines[index].startswith("```"))
    return "\n".join(lines[start + 1 : end])


_FRONT_MATTER_KEY = re.compile(r"^<!--\s*([A-Za-z0-9_-]+):")
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*$")


def front_matter_keys(text):
    """The front-matter keys a document (or a documented example) carries, in order."""
    keys = []
    for line in text.split("\n"):
        match = _FRONT_MATTER_KEY.match(line.strip())
        if match:
            keys.append(match.group(1))
    return keys


def headings(text, level):
    """Every heading of exactly ``level`` in ``text``, in order."""
    found = []
    for line in text.split("\n"):
        match = _HEADING.match(line)
        if match and len(match.group(1)) == level:
            found.append(match.group(2))
    return found


# ---------------------------------------------------------------------------
# The artifact-to-schema mapping, read out of schemas/README.md
# ---------------------------------------------------------------------------

_ROW = re.compile(r"^\|(.+)\|$")
_TICKED = re.compile(r"`([^`]+)`")

#: Placeholders the producer/consumer table writes into its path patterns.
_PLACEHOLDER = re.compile(r"<[^>]+>")


def documented_artifacts():
    """``{glob: kind}`` from ``schemas/README.md``'s producer/consumer table.

    Rows whose "Validates" column names a *return value* rather than a path --
    ``story-report``, ``inconsistency-report`` -- contribute no glob, which is
    correct: nothing writes them into the workspace.
    """
    body = section(SCHEMAS_README.read_text(encoding="utf-8"), "## Producer/consumer contract")
    mapping = {}
    for line in body.split("\n"):
        match = _ROW.match(line.strip())
        if match is None:
            continue
        # `\|` is an escaped pipe *inside* a cell, not a cell boundary.
        cells = [cell.strip() for cell in re.split(r"(?<!\\)\|", match.group(1))]
        if len(cells) < 2 or not cells[0].startswith("`"):
            continue
        schema = _TICKED.findall(cells[0])[0]
        if not schema.endswith(".schema.json"):
            continue
        kind = schema[: -len(".schema.json")]
        for token in _TICKED.findall(cells[1]):
            if not token.endswith((".yaml", ".yml", ".json", ".md")):
                continue
            mapping[_PLACEHOLDER.sub("*", token.replace("\\|", "|"))] = kind
    return mapping


def matches(relative, glob):
    """Segment-wise glob match, so ``*`` never swallows a path separator."""
    parts = relative.split("/")
    pattern = glob.split("/")
    if len(pattern) == 1:
        return fnmatch.fnmatchcase(parts[-1], pattern[0])
    if len(parts) != len(pattern):
        return False
    return all(fnmatch.fnmatchcase(part, item) for part, item in zip(parts, pattern))


def kind_for(relative, mapping):
    """The schema kind that claims ``relative``, or ``None``."""
    for glob, kind in mapping.items():
        if matches(relative, glob):
            return kind
    return None


# ---------------------------------------------------------------------------
# One full emission run, in tmp_path
# ---------------------------------------------------------------------------


CATALOG = """\
version: 1
repo: demo
layers:
  L1-core:
    modules: [ALPHA]
  L2-services:
    modules: [BETA, E2E]
modules:
  ALPHA:
    layer: L1-core
    files:
      - path: context/demo/spec/ALPHA/ALPHA-OVERVIEW.md
        facet: overview
      - path: context/demo/spec/ALPHA/ALPHA-INTERFACE.md
        facet: interface
        exports: [alphaClient]
  BETA:
    layer: L2-services
    files:
      - path: context/demo/spec/BETA/BETA-OVERVIEW.md
        facet: overview
  E2E:
    layer: L2-services
    files:
      - path: context/demo/spec/E2E/E2E-TESTING.md
        facet: test
"""

CONFORMANCE_REPORT = (
    '{\n  "scope": {\n    "kind": "aggregate"\n  },\n  "findings": [],\n  "clean": true\n}\n'
)

REPO_CHANGE = "context/demo/changes/CHANGE-001-retry-policy.md"
INDEX_CHANGE = "context/project/changes/PROJECT-CHANGE-001-retry-policy.md"
BASELINE_CHANGE = "context/demo/changes/CHANGE-000-initial-spec.md"
REPORT_REL = "context/project/out/%s/mverify-report.json" % PLAN_ID


def call(workspace, group, verb, **fields):
    """Call one group directly -- never through ``argv``."""
    module = importlib.import_module("tools.%s" % group)
    if verb is not None:
        fields["verb"] = verb
    result = module.run(workspace.args(**fields), workspace.ws)
    assert result.ok, "%s %s: %s" % (group, verb, [d.render() for d in result.diagnostics])
    return result


def _story(story_id, module, wave):
    return story_id, {
        "repo": "demo",
        "module": module,
        "wave": wave,
        "prerequisites": [] if wave == 1 else [FIRST_STORY],
        "change_file": REPO_CHANGE,
        "target_paths": ["src/%s.py" % module.lower()],
        "validation": {"post_story": [{"kind": "prose", "description": "it holds"}]},
    }


def emit_everything(workspace):
    """Drive every emitting verb once, leaving one artifact of each kind behind."""
    for module, filename in (
        ("ALPHA", "ALPHA-OVERVIEW.md"),
        ("ALPHA", "ALPHA-INTERFACE.md"),
        ("BETA", "BETA-OVERVIEW.md"),
        ("E2E", "E2E-TESTING.md"),
    ):
        workspace.write(
            "context/demo/spec/%s/%s" % (module, filename),
            "<!-- depends-on: context/demo/spec/%s/%s -->\n\n# %s\n"
            % (module, filename, filename[: -len(".md")]),
        )
    workspace.write("context/demo/spec/CATALOG.yaml", CATALOG)
    workspace.write(REPORT_REL, CONFORMANCE_REPORT)

    call(workspace, "spec", "catalog-emit", target="demo")
    call(
        workspace,
        "change",
        "emit",
        path=REPO_CHANGE,
        scope="repo",
        status="pending",
        title="Retry Policy",
        repo="demo",
    )
    call(
        workspace,
        "change",
        "emit",
        path=BASELINE_CHANGE,
        scope="repo",
        status="complete",
        title="Initial Spec",
        repo="demo",
    )
    call(
        workspace,
        "change",
        "emit",
        path=INDEX_CHANGE,
        scope="shared",
        status="pending",
        title="Retry Policy",
        repo="demo, other",
    )
    call(
        workspace,
        "plan",
        "emit",
        plan_id=PLAN_ID,
        stdin=io.StringIO(
            json.dumps(
                {
                    "project_change": "001",
                    "stories": dict(
                        [_story(FIRST_STORY, "ALPHA", 1), _story(LAST_STORY, "BETA", 2)]
                    ),
                }
            )
        ),
        now=SEED_NOW,
    )
    call(workspace, "plan", "story-emit", plan_id=PLAN_ID, story_id=FIRST_STORY)
    call(workspace, "plan", "story-emit", plan_id=PLAN_ID, story_id=LAST_STORY)
    call(workspace, "state", "run-increment", plan_id=PLAN_ID)
    call(workspace, "state", "set-plan", plan_id=PLAN_ID, status="in-progress")
    call(
        workspace,
        "state",
        "set-story",
        plan_id=PLAN_ID,
        story_id=FIRST_STORY,
        status="applied",
        attempt=1,
        branch="mexec/%s/%s/r1/1" % (PLAN_ID, FIRST_STORY),
        worktree="repos/demo/%s-r1-1" % FIRST_STORY,
    )
    call(
        workspace,
        "state",
        "conformance",
        plan_id=PLAN_ID,
        status="clean",
        report=REPORT_REL,
        findings=0,
    )
    call(workspace, "state", "telemetry", plan_id=PLAN_ID, cost=1.5, tokens=4096, wall_clock=61.5)
    return workspace


@pytest.fixture
def emitted(workspace):
    """A synthetic workspace holding one artifact of every emitted kind."""
    return emit_everything(workspace)


def workspace_files(workspace):
    """Every file in the workspace, workspace-relative and sorted."""
    return sorted(
        str(path.relative_to(workspace.root))
        for path in workspace.root.rglob("*")
        if path.is_file()
    )


# ---------------------------------------------------------------------------
# 1. Every emitted artifact validates against its schema
# ---------------------------------------------------------------------------








# ---------------------------------------------------------------------------
# 2. Emitted change documents match STANDARD-CHANGE.md
# ---------------------------------------------------------------------------


def documented_schemas():
    """The section that owns the two full change-document shapes.

    Scoped rather than searched from the top: ``### Project-level change index``
    is also a heading under §"Change Document Location & Naming", and that one
    documents where the file goes, not what it contains.
    """
    return section(STANDARD_CHANGE.read_text(encoding="utf-8"), "## Change Document Schemas")


def documented_repo_change():
    return fenced(section(documented_schemas(), "### Repo-level change document"))


def documented_index_change():
    return fenced(section(documented_schemas(), "### Project-level change index"))


def documented_baseline():
    """The baseline record's shape: a front-matter fence plus headings named in prose."""
    body = section(STANDARD_CHANGE.read_text(encoding="utf-8"), "## Initial-Spec Baseline Records")
    return fenced(body), re.findall(r"`(## [^`]+)`", body)


def test_the_standard_is_readable_and_names_all_three_document_shapes():
    """Guards the derivation these cases rest on."""
    assert front_matter_keys(documented_repo_change())
    assert front_matter_keys(documented_index_change())
    fence, named = documented_baseline()
    assert front_matter_keys(fence)
    assert named == ["## Summary", "## Modules", "## Spec Files"]










def test_the_heading_reader_would_notice_a_missing_section():
    """The comparison above is only worth as much as the reader under it."""
    documented = documented_repo_change()
    assert len(headings(documented, 2)) >= 8
    stripped = "\n".join(
        line for line in documented.split("\n") if line != "## Affected Tests"
    )
    assert headings(stripped, 2) != headings(documented, 2)


# ---------------------------------------------------------------------------
# 3. The injected E2E rules match their owning section verbatim
# ---------------------------------------------------------------------------


def owning_rules_block(standard_path, heading):
    """The lead-in line and the four rules under ``heading``, verbatim.

    Read here with an independent reader: an expectation extracted by the code
    under test would agree with it no matter what either one said. One reader
    serves both injected sections, exactly as one reader in the tool produces
    both -- a second copy here would be the hand-maintained duplicate this
    suite exists to forbid.
    """
    lines = standard_path.read_text(encoding="utf-8").split("\n")
    assert heading in lines, "no %r section in %s" % (heading, standard_path)
    lead = None
    bullets = []
    for line in lines[lines.index(heading) + 1 :]:
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
    assert len(bullets) == 4, "expected four rules, found %d" % len(bullets)
    return "\n".join(([lead, ""] if lead is not None else []) + bullets)


def owning_e2e_block(standard_path):
    return owning_rules_block(standard_path, E2E_HEADING)


def owning_delivered_block(standard_path):
    return owning_rules_block(standard_path, DELIVERED_SURFACE_HEADING)


def e2e_mismatches(story_text, standard_path):
    """Every way ``story_text``'s injected rules differ from the owning section.

    The empty list is the only conforming answer. Used in both directions: the
    positive case requires it empty, the negative case requires it non-empty.
    """
    block = owning_e2e_block(standard_path)
    problems = []
    occurrences = story_text.count(block)
    if occurrences != 2:
        problems.append("the owning block appears %d times, not twice" % occurrences)
    post = story_text.split("## Post-Story Validation", 1)[-1].split("## Slice Acceptance", 1)[0]
    acceptance = story_text.split("## Slice Acceptance", 1)[-1]
    if block not in post:
        problems.append("absent from Post-Story Validation")
    if block not in acceptance:
        problems.append("absent from Slice Acceptance")
    for rule in block.split("\n"):
        if rule.strip().startswith("- ") and story_text.count(rule) != 2:
            problems.append("rule appears %d times: %s" % (story_text.count(rule), rule.strip()))
    return problems


def story_text(workspace, story_id=LAST_STORY):
    return workspace.path(
        "context/project/plans/%s/PLAN-%s.md" % (PLAN_ID, story_id)
    ).read_text(encoding="utf-8")


def test_the_generator_reads_the_rules_from_their_owning_file():
    """``plan story-emit`` injects from ``shared/STANDARD-SPEC.md`` and nowhere else."""
    assert plan.STANDARD_SPEC == STANDARD_SPEC
    assert plan.STANDARD_SPEC.is_file()






def test_the_template_still_carries_exactly_two_injection_markers():
    """Both marked points exist in the template, so "both" means two."""
    template = (SHARED_DIR / "PLAN-STORY-TEMPLATE.md").read_text(encoding="utf-8")
    assert template.count("INJECT:E2E-HARD-RULES") == 2


def test_the_rules_have_exactly_one_authored_copy():
    """Nothing under the plugin tree hand-maintains a second copy of a rule."""
    first_rule = owning_e2e_block(STANDARD_SPEC).split("\n")[-4].strip()
    carriers = [
        path
        for path in sorted(PLUGIN_ROOT.rglob("*.md"))
        if first_rule in path.read_text(encoding="utf-8")
    ]
    assert carriers == [STANDARD_SPEC]


def perturbed_copy(tmp_path):
    """A copy of the standard with one word of one rule changed."""
    text = STANDARD_SPEC.read_text(encoding="utf-8")
    head, marker, tail = text.partition(E2E_HEADING)
    assert marker, E2E_HEADING
    assert "mocks" in tail.split("\n## ", 1)[0], "the perturbation must land inside the section"
    copy = tmp_path / "STANDARD-SPEC-copy.md"
    copy.write_text(head + marker + tail.replace("mocks", "stand-ins", 1), encoding="utf-8")
    return copy






# ---------------------------------------------------------------------------
# 3b. The injected Delivered-Surface Rule matches its owning section, ungated
# ---------------------------------------------------------------------------

#: A catalog naming no E2E module at all -- the condition under which the E2E
#: injection is dropped, and the one the delivered-surface injection must
#: survive. Deliberately a different tree from :data:`CATALOG`, since the point
#: is a repo that has no E2E spec file for ``catalog-emit`` to find.
NO_E2E_CATALOG = """\
version: 1
repo: demo
layers:
  L1-core:
    modules: [ALPHA]
modules:
  ALPHA:
    layer: L1-core
    files:
      - path: context/demo/spec/ALPHA/ALPHA-OVERVIEW.md
        facet: overview
"""


def delivered_mismatches(story_text, standard_path):
    """Every way ``story_text``'s injected rule differs from the owning section.

    The empty list is the only conforming answer. The template carries a single
    delivered-surface marker, in **Post-Story Validation**, so "more than once"
    is as much a defect as "absent".
    """
    block = owning_delivered_block(standard_path)
    problems = []
    occurrences = story_text.count(block)
    if occurrences != 1:
        problems.append("the owning block appears %d times, not once" % occurrences)
    post = story_text.split("## Post-Story Validation", 1)[-1].split("## Slice Acceptance", 1)[0]
    if block not in post:
        problems.append("absent from Post-Story Validation")
    for rule in block.split("\n"):
        if rule.strip().startswith("- ") and story_text.count(rule) != 1:
            problems.append("rule appears %d times: %s" % (story_text.count(rule), rule.strip()))
    return problems


def emit_story_without_e2e(workspace):
    """One story rendered in a repo whose catalog names no E2E module."""
    workspace.write(
        "context/demo/spec/ALPHA/ALPHA-OVERVIEW.md",
        "<!-- depends-on: context/demo/spec/ALPHA/ALPHA-OVERVIEW.md -->\n\n# ALPHA-OVERVIEW\n",
    )
    workspace.write("context/demo/spec/CATALOG.yaml", NO_E2E_CATALOG)
    call(
        workspace,
        "plan",
        "emit",
        plan_id=PLAN_ID,
        stdin=io.StringIO(json.dumps({"stories": dict([_story(FIRST_STORY, "ALPHA", 1)])})),
        now=SEED_NOW,
    )
    call(workspace, "plan", "story-emit", plan_id=PLAN_ID, story_id=FIRST_STORY)
    return story_text(workspace, FIRST_STORY)






def test_the_delivered_surface_rule_renders_where_the_catalog_names_no_e2e_module(workspace):
    """The ungated claim, in the one condition that would drop a gated rule.

    The E2E rules are absent from this story -- which is what makes the case
    evidence rather than a coincidence -- and the Delivered-Surface Rule is
    present anyway.
    """
    text = emit_story_without_e2e(workspace)

    assert owning_e2e_block(STANDARD_SPEC) not in text
    assert delivered_mismatches(text, STANDARD_SPEC) == []
    assert "INJECT:DELIVERED-SURFACE-RULE" not in text
    assert "INJECT:E2E-HARD-RULES" not in text


def test_the_template_carries_exactly_one_delivered_surface_marker():
    """One marked point, ungated: "every story" means one copy, not two."""
    template = (SHARED_DIR / "PLAN-STORY-TEMPLATE.md").read_text(encoding="utf-8")
    assert template.count("INJECT:DELIVERED-SURFACE-RULE") == 1


def test_the_delivered_surface_rule_has_exactly_one_authored_copy():
    """Nothing under the plugin tree hand-maintains a second copy of the rule."""
    first_rule = owning_delivered_block(STANDARD_SPEC).split("\n")[-4].strip()
    carriers = [
        path
        for path in sorted(PLUGIN_ROOT.rglob("*.md"))
        if first_rule in path.read_text(encoding="utf-8")
    ]
    assert carriers == [STANDARD_SPEC]


def perturbed_delivered_copy(tmp_path):
    """A copy of the standard with one word of one delivered-surface rule changed."""
    text = STANDARD_SPEC.read_text(encoding="utf-8")
    head, marker, tail = text.partition(DELIVERED_SURFACE_HEADING)
    assert marker, DELIVERED_SURFACE_HEADING
    assert "Name the surface" in tail.split("\n## ", 1)[0], "the perturbation must land inside the section"
    copy = tmp_path / "STANDARD-SPEC-delivered-copy.md"
    copy.write_text(head + marker + tail.replace("Name the surface", "Name the seam", 1), encoding="utf-8")
    return copy




# ---------------------------------------------------------------------------
# 4. STANDARD-REQ.md's stability rule matches its own declared grep expression
# ---------------------------------------------------------------------------

_GREP_QE = re.compile(r"`grep -qE '([^']+)' ([^`\s]+)`")


def test_the_no_bytecode_guard_is_in_place_before_any_tools_import():
    """``mc.py`` sets it for a CLI run; ``conftest`` must set it for a pytest run.

    Under pytest the group modules are imported directly, so ``mc.py`` -- and
    with it its ``sys.dont_write_bytecode`` -- is bypassed entirely.
    """
    assert sys.dont_write_bytecode is True
    assert os.environ.get("PYTHONDONTWRITEBYTECODE") == "1"




@pytest.fixture
def planted_bytecode():
    """A ``__pycache__/<name>.pyc`` under ``PLUGIN_ROOT``, always cleaned up.

    Removal happens in the fixture's teardown rather than in each test, so a
    test that fails part-way cannot leave the tree dirty for the assertions
    that follow it.
    """
    directory = PLUGIN_ROOT / "tools" / "__pycache__"
    artifact = directory / "planted.cpython-000.pyc"
    directory.mkdir(parents=True, exist_ok=True)
    artifact.write_bytes(b"not really bytecode")
    try:
        yield artifact
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def test_the_suite_clears_bytecode_that_was_already_there(planted_bytecode):
    """A stale artifact from an unrelated interactive probe is removed, not tolerated.

    This is the cleaning half of the rule, exercised where the fixture does it:
    ``conftest.remove_bytecode`` is what the session-scoped autouse fixture
    calls before the suite runs.
    """
    assert planted_bytecode.exists()
    removed = conftest.remove_bytecode()

    # It really did have something to do: the whole `__pycache__` went, and the
    # `*.pyc` inside it went with it.
    assert planted_bytecode.parent in removed
    assert not planted_bytecode.exists()
    assert not planted_bytecode.parent.exists()
    assert list(PLUGIN_ROOT.rglob("__pycache__")) == []
    assert list(PLUGIN_ROOT.rglob("*.pyc")) == []


def test_the_cleanup_runs_before_the_suite_and_never_after_it():
    """Order is the whole point, and it is the fixture's shape that fixes it.

    Cleaning on the way in removes the false failure; cleaning on the way *out*
    would delete exactly what the assertions look for, so the fixture must not.
    """
    fixture = conftest.clean_plugin_bytecode
    marker = fixture._fixture_function_marker
    assert marker.scope == "session"
    assert marker.autouse is True

    body = inspect.getsource(fixture._get_wrapped_function())
    before, _, after = body.partition("yield")
    assert "remove_bytecode()" in before
    assert "remove_bytecode" not in after


def test_bytecode_produced_during_a_run_still_fails_both_assertions(planted_bytecode):
    """The fixture must not mask a real violation, so both assertions keep teeth.

    The artifact is planted *after* the session fixture has run -- which is what
    bytecode written by the run itself would be -- and the two expressions
    ``test_a_full_run_leaves_no_bytecode_under_the_plugin_tree`` asserts on must
    both report it.
    """
    assert list(PLUGIN_ROOT.rglob("__pycache__")) != []
    assert list(PLUGIN_ROOT.rglob("*.pyc")) != []


def test_a_full_run_leaves_no_bytecode_under_the_plugin_tree(workspace):
    """REQ-011's "nothing appears" clause, applied to the tool itself."""
    emit_everything(workspace)
    completed = workspace.run_cli(
        "--workspace", workspace.root, "--now", NOW, "spec", "mode", "demo"
    )
    assert completed.returncode == 0, completed.stderr

    assert list(PLUGIN_ROOT.rglob("__pycache__")) == []
    assert list(PLUGIN_ROOT.rglob("*.pyc")) == []


@pytest.mark.xfail(
    strict=True,
    reason=(
        "The surviving skills still document mc.py req/todo/status/spec depth/state sweep, "
        "all removed with the v2 tool reduction. Closed by REDESIGN.md's build order steps "
        "3-6, which rewrite mspec/mplan/mship/mverify/mreverse against the reduced surface. "
        "strict=True so this fails the moment it starts passing, rather than lingering."
    ),
)
def test_every_documented_invocation_names_a_registered_verb():
    """A skill invoking a verb that does not exist fails at run time, not at review."""
    registry = _registered_verbs()
    unknown = []
    for path in sorted(PLUGIN_ROOT.rglob("*.md")):
        for group, verb in _invocations(path.read_text(encoding="utf-8")):
            if group.startswith(("<", "-")):
                continue
            where = "%s: mc.py %s %s" % (path.relative_to(REPO_ROOT), group, verb or "")
            if group not in registry:
                unknown.append(where)
            elif registry[group] and verb and not verb.startswith(("<", "-")):
                if verb not in registry[group]:
                    unknown.append(where)
    assert unknown == []


def test_the_bytecode_assertions_are_not_relaxed():
    """The rule is enforced, never retired: the assertions stay unconditional."""
    body = inspect.getsource(test_a_full_run_leaves_no_bytecode_under_the_plugin_tree)
    assert 'assert list(PLUGIN_ROOT.rglob("__pycache__")) == []' in body
    assert 'assert list(PLUGIN_ROOT.rglob("*.pyc")) == []' in body


def test_the_entry_point_sets_the_guard_before_importing_the_package():
    """Order matters: set after the import, the first import already wrote."""
    source = (TOOLS_DIR / "mc.py").read_text(encoding="utf-8")
    guard = source.index("sys.dont_write_bytecode = True")
    assert guard < source.index("from tools import core")


# ---------------------------------------------------------------------------
# TOOLS-TESTING.md §"Not covered": no agent, no network, no git, no fixtures
# ---------------------------------------------------------------------------

#: Importing any of these would mean the suite (or the tool) reaches a network.
NETWORK_MODULES = frozenset(
    {
        "aiohttp",
        "ftplib",
        "http",
        "httpx",
        "requests",
        "smtplib",
        "socket",
        "ssl",
        "telnetlib",
        "urllib",
        "xmlrpc",
    }
)

SUBPROCESS_CALLS = frozenset({"run", "call", "check_call", "check_output", "Popen"})

#: ``os`` calls that hand control to another program. The exec family names the
#: executable in its first argument; ``system``/``popen`` take a shell string and
#: are never acceptable here, so they are reported by name.
OS_EXEC_CALLS = frozenset({"execv", "execve", "execvp", "spawnv", "spawnvp", "posix_spawn"})
OS_SHELL_CALLS = frozenset({"system", "popen"})


def source_files():
    """Every Python file this repository ships or tests with."""
    return sorted(TESTS_DIR.glob("*.py")) + sorted(TOOLS_DIR.glob("*.py"))


def imported_roots(tree):
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    return roots


def _leading_argv(node):
    """The first element of an argv expression, as source text."""
    while isinstance(node, ast.BinOp):
        node = node.left
    if isinstance(node, ast.List) and node.elts:
        return ast.unparse(node.elts[0])
    return ast.unparse(node) if node is not None else "<none>"


def spawned_commands(tree):
    """The executable every process-spawning call names, as source text."""
    spawned = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        target = node.func.value
        if not isinstance(target, ast.Name):
            continue
        if target.id == "subprocess" and node.func.attr in SUBPROCESS_CALLS:
            spawned.append(_leading_argv(node.args[0] if node.args else None))
        elif target.id == "os" and node.func.attr in OS_EXEC_CALLS:
            spawned.append(ast.unparse(node.args[0]) if node.args else "<none>")
        elif target.id == "os" and node.func.attr in OS_SHELL_CALLS:
            spawned.append("os.%s" % node.func.attr)
    return spawned


def test_no_source_file_reaches_a_network():
    for path in source_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        assert not (imported_roots(tree) & NETWORK_MODULES), path.name


def test_nothing_spawns_a_process_other_than_this_interpreter():
    """No agent, and no ``git``: the only thing spawned is ``sys.executable``."""
    for path in source_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for command in spawned_commands(tree):
            assert command == "sys.executable", "%s spawns %s" % (path.name, command)


def test_the_tool_runs_no_git_command():
    """``TOOLS`` computes work-tree verdicts; ``mexecute`` is what runs git."""
    for path in sorted(TOOLS_DIR.glob("*.py")):
        assert "subprocess" not in path.read_text(encoding="utf-8"), path.name


def test_no_fixture_file_is_committed_under_the_plugin_tree():
    """The plugin tree ships into every user's cache; test data is not part of it."""
    strays = [
        str(path.relative_to(REPO_ROOT))
        for path in sorted(PLUGIN_ROOT.rglob("*"))
        if path.name in ("conftest.py", "tests", "fixtures", "__pycache__")
        or path.name.startswith("test_")
    ]
    assert strays == []
    assert PLUGIN_ROOT not in TESTS_DIR.parents
    assert TESTS_DIR.parent == REPO_ROOT


_GLOBAL_OPTS_WITH_VALUE = ("--workspace", "--now")
_GLOBAL_FLAGS = ("--json",)


def _registered_verbs():
    """The real ``(group, verb)`` registry, read out of each group's own ``register``."""
    registry = {}
    for name in mc.GROUPS:
        parser = argparse.ArgumentParser(prog="mc.py")
        subparsers = parser.add_subparsers(dest="group")
        mc.load_group(name).register(subparsers)
        verbs = set()
        for action in subparsers.choices[name]._actions:
            if isinstance(action, argparse._SubParsersAction):
                verbs |= set(action.choices)
        registry[name] = verbs
    return registry


def _invocation_tokens(text):
    """Every ``mc.py <group> [<verb>] [<rest>...]`` an authored document spells out."""
    for match in re.finditer(r"mc\.py((?:[ \t]+[^\s`\n]+)+)", text):
        tokens = match.group(1).split()
        if tokens and tokens[0].startswith("#"):
            continue  # a directory map annotating the file, not an invocation
        index = 0
        while index < len(tokens):
            if tokens[index] in _GLOBAL_OPTS_WITH_VALUE:
                index += 2
            elif tokens[index] in _GLOBAL_FLAGS:
                index += 1
            else:
                break
        if index >= len(tokens):
            continue
        group = tokens[index]
        verb = tokens[index + 1] if index + 1 < len(tokens) else None
        yield group, verb, tokens[index + 2 :]


def _invocations(text):
    """Every ``mc.py <group> [<verb>]`` an authored document spells out."""
    for group, verb, _rest in _invocation_tokens(text):
        yield group, verb




# ---------------------------------------------------------------------------
# MVERIFY's slice-loop rules, against the artifacts they speak about
#
# ``mverify`` is a prompt and its findings are judgment, so what is mechanically
# checkable is not the judgment but the three claims the skill makes about
# *other* documents: that the option it says it passes through exists on the
# verb it names; that the finding shape it documents is one
# ``conformance-report.schema.json`` admits; and that the contract-depth
# absences it promises never to report are the ones ``check catalog`` also
# treats as expected. Each is a claim that can go stale silently -- leaving the
# skill directing a shard to pass a flag that does not exist, to return a report
# that fails validation, or to report drift the tool says is not there.
# ---------------------------------------------------------------------------

MVERIFY_SKILL = PLUGIN_ROOT / "skills" / "mverify" / "SKILL.md"

#: The finding type a cross-repo shard raises for work the contract moved under.
STALE_REVISION = "stale-contract-revision"

_FENCE = re.compile(r"^\s*```([A-Za-z0-9_+-]*)\s*$")


def fenced_blocks(text, language):
    """Every fenced block tagged ``language``, in document order.

    ``fenced`` above returns the first block of any language; a skill documents
    its command lines and its example payloads in the same file, so the reader
    that picks the payloads out has to select on the tag. A block nested under
    a list item is indented, so the fence is matched with its indent rather than
    at column zero.
    """
    blocks, open_tag, body = [], None, []
    for line in text.split("\n"):
        match = _FENCE.match(line)
        if match is None:
            if open_tag is not None:
                body.append(line)
            continue
        if open_tag is None:
            open_tag, body = match.group(1), []
        else:
            if open_tag == language:
                blocks.append("\n".join(body))
            open_tag, body = None, []
    return blocks


def mverify_text():
    return MVERIFY_SKILL.read_text(encoding="utf-8")


def documented_reports():
    """Every shard payload ``mverify/SKILL.md`` spells out, freshly parsed."""
    return [json.loads(block) for block in fenced_blocks(mverify_text(), "json")]


def stale_findings(reports):
    return [
        finding
        for report in reports
        for finding in report["findings"]
        if finding["type"] == STALE_REVISION
    ]


def _registered_options():
    """``(group, verb) -> option strings``, read out of each group's ``register``."""
    options = {}
    for name in mc.GROUPS:
        parser = argparse.ArgumentParser(prog="mc.py")
        subparsers = parser.add_subparsers(dest="group")
        mc.load_group(name).register(subparsers)
        for action in subparsers.choices[name]._actions:
            if not isinstance(action, argparse._SubParsersAction):
                continue
            for verb, verb_parser in action.choices.items():
                flags = set()
                for verb_action in verb_parser._actions:
                    flags |= set(verb_action.option_strings)
                options[(name, verb)] = flags
    return options


def _documented_options(text):
    """``(group, verb, flag)`` for every option an invocation spells out."""
    for group, verb, rest in _invocation_tokens(text):
        if verb is None:
            continue
        for token in rest:
            token = token.strip("[](),.`").split("=")[0]
            if token.startswith("--") and len(token) > 2:
                yield group, verb, token


def test_the_mverify_skill_documents_the_slice_scope_as_a_shards_option():
    """`--slice` is a scope narrowing because it is *only* an argument to the
    shard list: a run that carried one differs from a run that did not by which
    shards `plan shards` returned, and by nothing else."""
    text = mverify_text()
    assert "--slice" in _registered_options()[("plan", "shards")]
    assert ("plan", "shards", "--slice") in set(_documented_options(text))
    assert "scope narrowing only" in text


def test_every_option_the_mverify_skill_documents_is_one_its_verb_accepts():
    """A skill passing a flag the verb never registered fails at run time,
    where nobody is reading the exit code, rather than here."""
    registered = _registered_options()
    unknown = []
    for group, verb, flag in _documented_options(mverify_text()):
        if group.startswith(("<", "-")) or verb.startswith(("<", "-")):
            continue
        accepted = registered.get((group, verb))
        if accepted is not None and flag not in accepted:
            unknown.append("mc.py %s %s %s" % (group, verb, flag))
    assert unknown == []


def test_the_mverify_examples_are_read_from_the_skill_that_owns_them():
    """Guards the derivation: no examples would make the next cases vacuous."""
    reports = documented_reports()
    assert reports, "mverify/SKILL.md documents no shard payload"
    assert stale_findings(reports), "no %s example to check" % STALE_REVISION








# -- Spec depth is not drift -------------------------------------------------

DEPTH_TARGET = "demo"
DEPTH_MODULE = "ALPHA"


def facet_name(facet):
    """``deps`` -> ``DEPENDENCIES`` -- the name an author writes, not the token."""
    return spec.FACET_FILENAME_SUFFIX[facet].strip("-")[: -len(".md")]


def depth_rule():
    return section(mverify_text(), "### Spec depth is not drift")


def contract_depth_workspace(workspace, depth):
    """A one-module tree holding exactly the contract facets, at ``depth``."""
    directory = "context/%s/spec/%s" % (DEPTH_TARGET, DEPTH_MODULE)
    lines = [
        "version: 1",
        "repo: %s" % DEPTH_TARGET,
        "layers:",
        "  L1-core:",
        "    modules: [%s]" % DEPTH_MODULE,
        "modules:",
        "  %s:" % DEPTH_MODULE,
        "    layer: L1-core",
        "    depth: %s" % depth,
        "    files:",
    ]
    for facet in spec.CONTRACT_FACETS:
        filename = "%s%s" % (DEPTH_MODULE, spec.FACET_FILENAME_SUFFIX[facet])
        workspace.write("%s/%s" % (directory, filename), "# %s\n\nBody.\n" % filename[:-3])
        lines.append("      - path: %s/%s" % (directory, filename))
        lines.append("        facet: %s" % facet)
    workspace.write("context/%s/spec/CATALOG.yaml" % DEPTH_TARGET, "\n".join(lines) + "\n")
    return workspace


def catalog_findings(workspace):
    result = check.run(workspace.args(verb="catalog", target=DEPTH_TARGET), workspace.ws)
    return result.data["findings"]






