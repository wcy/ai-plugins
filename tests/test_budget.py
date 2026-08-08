"""The context budget: what a skill costs to load, enforced rather than intended.

Every byte in a `SKILL.md` or a `shared/` standard is loaded into a run's context
before any of the user's own code is read, and the plugin's stated purpose is to
build applications *token-efficiently*. The observed failure mode is accretion:
226 commits of design-review commentary, restated rules and back-compatibility
prose grew the shipped instruction set to ~88k tokens, at which point a single
autonomous run spent a third of its window describing its own process.

Discipline did not prevent that and will not prevent it again, so the budget is a
test. A skill that needs more than its allowance needs a smaller job, not a bigger
allowance -- raising a number here is a design decision, made deliberately, and
visible in the diff.

The per-file numbers are bytes (~4 bytes per token). They come from
`REDESIGN.md` §"Skill authoring rules", and the total is the ceiling the whole
redesign is measured against.
"""

from pathlib import Path

import pytest

from conftest import REPO_ROOT

PLUGIN_ROOT = REPO_ROOT / "plugins" / "metacoder"

#: Per-file allowances, in bytes. A file absent from this table is not budgeted;
#: :func:`test_every_runtime_loadable_file_is_budgeted` is what stops that being
#: a way to smuggle one in.
BUDGETS = {
    "skills/mspec/SKILL.md": 14_000,
    "skills/mplan/SKILL.md": 12_000,
    "skills/mship/SKILL.md": 24_000,
    "skills/mverify/SKILL.md": 16_000,
    "skills/mreverse/SKILL.md": 10_000,
    "shared/STANDARD-SPEC.md": 8_000,
    "shared/STANDARD-CHANGE.md": 6_000,
    "shared/PLAN-STORY-TEMPLATE.md": 5_000,
}

#: The ceiling on everything a run can load: ~24k tokens, down from ~88k.
TOTAL_RUNTIME_BUDGET = 96_000

#: Files still carrying their v1 text. Each is over its eventual budget today
#: and is not yet held to it -- the entry names the build step that closes it,
#: and removing an entry is what puts that file under its allowance for good.
#: **An empty table is the end state**, and the budget is not fully live until
#: it is. This is the guard being installed ahead of the work it guards, so
#: every rewrite lands against a number rather than against an intention.
PENDING_REWRITE = {
    "shared/STANDARD-SPEC.md": "rewritten to two facets at build step 2",
    "shared/STANDARD-CHANGE.md": "collapsed to one change tier at build step 2",
    "skills/mspec/SKILL.md": "rewritten at build step 3",
    "skills/mplan/SKILL.md": "rewritten at build step 3",
    "shared/PLAN-STORY-TEMPLATE.md": "re-rendered against the reduced spec at build step 3",
    "skills/mship/SKILL.md": "absorbs mexecute at build step 4",
    "skills/mexecute/SKILL.md": "merged into mship at build step 4",
    "skills/mverify/SKILL.md": "absorbs mfix at build step 5",
    "skills/mfix/SKILL.md": "merged into mverify at build step 5",
    "skills/mreverse/SKILL.md": "trimmed to two facets at build step 6",
}


def _runtime_loadable():
    """Every file a skill can pull into context at run time.

    `README.md` is excluded deliberately: it is documentation for a human
    choosing to install the plugin, and no skill loads it.
    """
    paths = sorted(PLUGIN_ROOT.glob("skills/*/SKILL.md"))
    paths += sorted(PLUGIN_ROOT.glob("shared/*.md"))
    return [path for path in paths if path.name != "README.md"]


def _rel(path):
    return path.relative_to(PLUGIN_ROOT).as_posix()


@pytest.mark.parametrize("name", sorted(set(BUDGETS) - set(PENDING_REWRITE)))
def test_a_budgeted_file_is_within_its_allowance(name):
    path = PLUGIN_ROOT / name
    assert path.is_file(), "%s is budgeted but not on disk" % name
    size = path.stat().st_size
    assert size <= BUDGETS[name], "%s is %d bytes, over its %d-byte budget by %d" % (
        name,
        size,
        BUDGETS[name],
        size - BUDGETS[name],
    )


def test_every_runtime_loadable_file_is_budgeted():
    """A new skill gets a budget, or it does not ship.

    Without this, the per-file test above is satisfiable by never adding an
    entry -- which is exactly how an unbudgeted file would grow unnoticed.
    """
    known = set(BUDGETS) | set(PENDING_REWRITE)
    unbudgeted = sorted(_rel(path) for path in _runtime_loadable() if _rel(path) not in known)
    assert unbudgeted == [], "no budget declared for: %s" % ", ".join(unbudgeted)


#: The measured total today, in bytes. It is a **ratchet**: the assertion below
#: is `<=`, so the number may be lowered as files are rewritten and may never be
#: raised. Excluding the pending files instead would make the total assertion
#: vacuous while every file is still pending, which is precisely the window the
#: guard is being installed to cover.
CURRENT_CEILING = 270_500


def test_the_runtime_surface_never_grows():
    """The ratchet. Every rewrite lowers :data:`CURRENT_CEILING`; nothing raises it.

    This is the assertion that has teeth *today*, while every file is still
    pending its rewrite -- it cannot be satisfied by adding prose anywhere.
    """
    measured = sum(path.stat().st_size for path in _runtime_loadable())
    assert measured <= CURRENT_CEILING, (
        "runtime-loadable prose grew to %d bytes, over the %d-byte ratchet. "
        "Lower the ratchet by deleting prose; never raise it."
        % (measured, CURRENT_CEILING)
    )


def test_the_whole_runtime_surface_is_within_the_total():
    """The end state: once nothing is pending, the ~24k-token ceiling is live."""
    if PENDING_REWRITE:
        pytest.skip("%d files still pending rewrite" % len(PENDING_REWRITE))
    measured = sum(path.stat().st_size for path in _runtime_loadable())
    assert measured <= TOTAL_RUNTIME_BUDGET, (
        "runtime-loadable prose is %d bytes, over the %d-byte ceiling"
        % (measured, TOTAL_RUNTIME_BUDGET)
    )


def test_the_ratchet_is_not_slack():
    """A ratchet set far above the measurement ratchets nothing.

    Without this, lowering the number could be forgotten indefinitely and the
    guard would read as green while permitting unbounded growth.
    """
    measured = sum(path.stat().st_size for path in _runtime_loadable())
    slack = CURRENT_CEILING - measured
    assert slack <= 5_000, (
        "the ratchet has %d bytes of slack above the %d measured; lower "
        "CURRENT_CEILING to match what is actually on disk" % (slack, measured)
    )


def test_the_pending_rewrites_name_the_step_that_closes_them():
    """A file parked here carries the build step that removes it, so the
    exemption cannot quietly become permanent."""
    for name, reason in PENDING_REWRITE.items():
        assert "build step" in reason, "%s: %r names no build step" % (name, reason)


def test_a_pending_file_is_one_that_actually_exists():
    """An entry for a file already deleted is a stale exemption, and a stale
    exemption is how a budget stops meaning anything."""
    missing = sorted(name for name in PENDING_REWRITE if not (PLUGIN_ROOT / name).is_file())
    assert missing == [], "pending rewrite named for absent files: %s" % ", ".join(missing)


def test_no_deleted_skill_is_still_shipped():
    """mreq, mmigrate and mquick are removed in v2; their tooling is already
    gone, so shipping the skill would ship instructions that cannot run."""
    for name in ("mreq", "mmigrate", "mquick"):
        assert not (PLUGIN_ROOT / "skills" / name).exists(), "%s is still shipped" % name
