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
import io
import json
import os
import re
import sys
import argparse
import re
from pathlib import Path

import pytest

from conftest import NOW, PLUGIN_ROOT, REPO_ROOT
from tools import core, mc, plan

TESTS_DIR = Path(__file__).resolve().parent
TOOLS_DIR = PLUGIN_ROOT / "tools"
SHARED_DIR = PLUGIN_ROOT / "shared"
SCHEMAS_README = PLUGIN_ROOT / "schemas" / "README.md"
STANDARD_CHANGE = SHARED_DIR / "STANDARD-CHANGE.md"
STANDARD_SPEC = SHARED_DIR / "STANDARD-SPEC.md"
STANDARD_REQ = SHARED_DIR / "STANDARD-REQ.md"

E2E_HEADING = "## E2E Testing Hard Rules"

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

REQUIREMENTS = """\
<!-- requirements: demo -->
<!-- updated: 2026-01-01 -->

# Requirements — demo

### REQ-001: Mechanical work costs no model effort

**Need:** The steps that follow from their inputs should be performed, not re-enacted.
**Rationale:** Reproducing a computable result costs tokens and varies.
**Status:** active
"""

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
    requirements: [REQ-001]
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
REQ_CHANGE_RECORD = "context/demo/requirements/changes/REQ-CHANGE-001-retry-policy.md"
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
    workspace.write("context/demo/requirements/REQUIREMENTS.md", REQUIREMENTS)
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
        "req",
        "change-emit",
        path=REQ_CHANGE_RECORD,
        tier="demo",
        status="open",
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


def test_the_artifact_mapping_is_read_from_the_document_that_owns_it():
    """Guards the derivation: an empty mapping would make the next case vacuous."""
    mapping = documented_artifacts()
    assert set(mapping.values()) == {
        "catalog",
        "change-frontmatter",
        "plan-graph",
        "plan-state",
        "project-state",
        "conformance-report",
        "requirements-frontmatter",
        "req-change-frontmatter",
    }
    for glob in mapping:
        assert "<" not in glob and ">" not in glob, glob


def test_every_emitted_artifact_validates_against_its_schema(emitted):
    """Whatever a schema claims to validate, and the run left on disk, validates."""
    mapping = documented_artifacts()
    checked = {}
    for relative in workspace_files(emitted):
        kind = kind_for(relative, mapping)
        if kind is None:
            continue
        outcome = core.validate_instance(kind, emitted.path(relative), emitted.ws)
        assert outcome.ok, "%s (%s): %s" % (
            relative,
            kind,
            [item.render() for item in outcome.diagnostics],
        )
        checked.setdefault(kind, []).append(relative)

    # Every kind the change document names, all exercised by one run.
    assert set(checked) == {
        "catalog",
        "change-frontmatter",
        "plan-graph",
        "plan-state",
        "project-state",
        "conformance-report",
        "requirements-frontmatter",
        "req-change-frontmatter",
    }, sorted(checked)
    assert sorted(checked["change-frontmatter"]) == sorted(
        [BASELINE_CHANGE, REPO_CHANGE, INDEX_CHANGE]
    )


def test_a_planted_invalid_artifact_is_caught_by_the_same_walk(emitted):
    """The walk has teeth: break one emitted file and it stops validating."""
    mapping = documented_artifacts()
    target = "context/project/plans/%s/plan.yaml" % PLAN_ID
    assert kind_for(target, mapping) == "plan-graph"

    document = core.load_yaml(emitted.path(target), target)
    del document["version"]
    emitted.write(target, core.dump_yaml(document))

    outcome = core.validate_instance("plan-graph", emitted.path(target), emitted.ws)
    assert outcome.ok is False
    assert [item.code for item in outcome.diagnostics] == [core.E_SCHEMA_INVALID]


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


def test_the_emitted_repo_change_carries_the_documented_keys_and_headings(emitted):
    written = emitted.path(REPO_CHANGE).read_text(encoding="utf-8")
    documented = documented_repo_change()

    # `plan` is optional and only written when `--plan` is passed.
    assert front_matter_keys(written) == [
        key for key in front_matter_keys(documented) if key != "plan"
    ]
    assert headings(written, 2) == headings(documented, 2)
    assert headings(written, 1) == ["CHANGE-001: Retry Policy"]
    # The schema's own required set is a floor under the document's list.
    required = core.load_schema("change")["$defs"]["repoChange"]["required"]
    assert set(required) <= set(front_matter_keys(written))


def test_the_emitted_project_index_carries_the_documented_keys_and_headings(emitted):
    written = emitted.path(INDEX_CHANGE).read_text(encoding="utf-8")
    documented = documented_index_change()

    # `consumers` is shared-scope-only with no flag in TOOLS-INTERFACE.md's signature;
    # `plan` is optional and only written when `--plan` is passed.
    assert front_matter_keys(written) == [
        key for key in front_matter_keys(documented) if key not in ("consumers", "plan")
    ]
    assert headings(written, 2) == headings(documented, 2)
    assert headings(written, 1) == ["PROJECT-CHANGE-001: Retry Policy"]
    required = core.load_schema("change")["$defs"]["projectChange"]["required"]
    assert set(required) <= set(front_matter_keys(written))


def test_the_emitted_baseline_record_uses_the_documented_reduced_layout(emitted):
    written = emitted.path(BASELINE_CHANGE).read_text(encoding="utf-8")
    fence, named = documented_baseline()

    assert front_matter_keys(written) == front_matter_keys(fence)
    assert headings(written, 2) == [title.lstrip("#").strip() for title in named]
    assert core.load_front_matter(emitted.path(BASELINE_CHANGE))["status"] == "complete"


def test_the_emitted_documents_carry_the_injected_date(emitted):
    """A conforming document is also a deterministic one: the clock is injected."""
    for relative in (REPO_CHANGE, INDEX_CHANGE, BASELINE_CHANGE):
        assert core.load_front_matter(emitted.path(relative))["date"] == NOW


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


def owning_e2e_block(standard_path):
    """The lead-in line and the four rules, verbatim, from ``standard_path``.

    Read here with an independent reader: an expectation extracted by the code
    under test would agree with it no matter what either one said.
    """
    lines = standard_path.read_text(encoding="utf-8").split("\n")
    assert E2E_HEADING in lines, "no %r section in %s" % (E2E_HEADING, standard_path)
    lead = None
    bullets = []
    for line in lines[lines.index(E2E_HEADING) + 1 :]:
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
    post = story_text.split("## Post-Story Validation", 1)[-1].split("## Final Validation", 1)[0]
    final = story_text.split("## Final Validation", 1)[-1]
    if block not in post:
        problems.append("absent from Post-Story Validation")
    if block not in final:
        problems.append("absent from Final Validation")
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


def test_the_injected_rules_match_the_owning_section_verbatim(emitted):
    assert e2e_mismatches(story_text(emitted), STANDARD_SPEC) == []


def test_the_rules_are_injected_at_both_marked_points(emitted):
    text = story_text(emitted)
    block = owning_e2e_block(STANDARD_SPEC)
    post = text.split("## Post-Story Validation", 1)[1].split("## Final Validation", 1)[0]
    final = text.split("## Final Validation", 1)[1]

    assert block in post
    assert block in final
    assert "INJECT:E2E-HARD-RULES" not in text


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


def test_a_perturbed_standard_is_reported_as_a_mismatch(workspace, tmp_path, monkeypatch):
    """The negative case: the comparison must fail when it should.

    The generator is pointed at a *copy* in ``tmp_path``; the real
    ``plugins/metacoder/shared/STANDARD-SPEC.md`` is read for the expectation and
    is asserted byte-identical afterwards. This story may not touch it.
    """
    before = STANDARD_SPEC.read_bytes()
    copy = perturbed_copy(tmp_path)
    assert copy.read_bytes() != before

    monkeypatch.setattr(plan, "STANDARD_SPEC", copy)
    emit_everything(workspace)
    text = story_text(workspace)

    # Against the perturbed copy the injection is faithful ...
    assert e2e_mismatches(text, copy) == []
    # ... and against the owner it is not, which is the divergence this catches.
    assert e2e_mismatches(text, STANDARD_SPEC) != []
    assert "stand-ins" in text
    assert STANDARD_SPEC.read_bytes() == before


def test_the_owner_still_carries_the_unperturbed_rules(emitted):
    """The perturbation lives and dies in ``tmp_path``; the owner never sees it."""
    assert e2e_mismatches(story_text(emitted), STANDARD_SPEC) == []
    assert "stand-ins" not in STANDARD_SPEC.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 4. STANDARD-REQ.md's stability rule matches its own declared grep expression
# ---------------------------------------------------------------------------

_GREP_QE = re.compile(r"`grep -qE '([^']+)' ([^`\s]+)`")


def documented_req_stability_check():
    """The ``grep -qE`` expression STANDARD-REQ.md declares for its own
    stability rule, read out of the document rather than hardcoded here."""
    text = STANDARD_REQ.read_text(encoding="utf-8")
    match = _GREP_QE.search(text)
    assert match is not None, "STANDARD-REQ.md declares no grep -qE expression"
    return match.group(1), match.group(2)


def test_the_standard_req_declares_a_readable_grep_expression():
    """Guards the derivation this case rests on."""
    pattern, target = documented_req_stability_check()
    assert pattern
    assert target.endswith("STANDARD-REQ.md")


def test_the_standard_req_stability_rule_matches_its_own_declared_expression():
    """The claim STANDARD-REQ.md makes about itself, turned into a real
    assertion: the expression it declares actually matches its own text.

    Previously this existed only as a claim inside the document, run by
    nothing -- see TOOLS-TESTING.md §"Conformance to the standards".
    """
    pattern, _target = documented_req_stability_check()
    text = STANDARD_REQ.read_text(encoding="utf-8")
    assert re.search(pattern, text) is not None


# ---------------------------------------------------------------------------
# TOOLS-IMPLEMENTATION.md §"Package shape": no bytecode in the plugin tree
# ---------------------------------------------------------------------------


def test_the_no_bytecode_guard_is_in_place_before_any_tools_import():
    """``mc.py`` sets it for a CLI run; ``conftest`` must set it for a pytest run.

    Under pytest the group modules are imported directly, so ``mc.py`` -- and
    with it its ``sys.dont_write_bytecode`` -- is bypassed entirely.
    """
    assert sys.dont_write_bytecode is True
    assert os.environ.get("PYTHONDONTWRITEBYTECODE") == "1"


def test_a_full_run_leaves_no_bytecode_under_the_plugin_tree(workspace):
    """REQ-011's "nothing appears" clause, applied to the tool itself."""
    emit_everything(workspace)
    completed = workspace.run_cli("--workspace", workspace.root, "--now", NOW, "status")
    assert completed.returncode == 0, completed.stderr

    assert list(PLUGIN_ROOT.rglob("__pycache__")) == []
    assert list(PLUGIN_ROOT.rglob("*.pyc")) == []


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


def _invocations(text):
    """Every ``mc.py <group> [<verb>]`` an authored document spells out."""
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
        yield group, verb


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
