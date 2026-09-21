#!/usr/bin/env python3
"""Tests for check_record_discipline.py — every check refuses what it must and allows
what it must, on a throwaway git repository.

Each test builds a fresh repo in a temp directory and runs the guard as a subprocess,
exactly as a pre-commit hook would. The fixture repos set a repo-local
`core.hooksPath` to an empty directory, so the machine's global hooks do not fire on
the FIXTURE's own set-up commits; the guard under test is invoked directly.

Run:  python3 -m unittest <this file>      (or: python3 <this file>)
"""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

GUARD = Path(__file__).resolve().parent / "check_record_discipline.py"
BANNER = "> Immutable session narrative — history, not a boot surface."
BOUND = 8192

T = ".threads/sub/20260101-x"            # a thread directory
HANDOFF = f"{T}/handoff.md"
FINDINGS = f"{T}/findings-2026-01-01-first.md"
CACHE = ".threads/ORCHESTRATOR-CACHE-TEST.md"
NARRATIVE = ".threads/SESSION-HANDOFF-2026-01-01-test.md"


def handoff(ct_fill=500, marker="x", reading_extra="",
            log="### 2026-01-01 — first entry\n\nOld entry body.\n"):
    """A handoff whose Current-truth block is about ct_fill + 60 bytes."""
    return (
        "# Hand-off — test\n\n"
        "## Current truth  (overwrite each hop — do NOT append)\n\n"
        f"- **Focus:** {marker * ct_fill}\n\n"
        "## Reading order for a cold start\n\n1. thread.json\n\n"
        f"{reading_extra}"
        "## Session log (newest first)\n\n"
        f"{log}"
    )


class RepoCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="guardtest-")
        root = Path(self._tmp.name)
        self.repo = root / "repo"
        self.repo.mkdir()
        hooks = root / "no-hooks"
        hooks.mkdir()
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "guard test")
        self.git("config", "user.email", "guard-test@example.invalid")
        self.git("config", "commit.gpgsign", "false")
        self.git("config", "core.hooksPath", str(hooks))
        self.write("README.md", "fixture\n")
        self.commit("init")

    def tearDown(self):
        self._tmp.cleanup()

    # --- fixture helpers ---
    def git(self, *args, check=True):
        return subprocess.run(["git", *args], cwd=self.repo, capture_output=True,
                              text=True, check=check)

    def write(self, rel, text):
        p = self.repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def read(self, rel):
        return (self.repo / rel).read_text(encoding="utf-8")

    def commit(self, msg):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", msg)

    def stage(self, *rels):
        self.git("add", "--", *rels)

    def guard(self, *args):
        r = subprocess.run([sys.executable, str(GUARD), *args], cwd=self.repo,
                           capture_output=True, text=True)
        return r.returncode, r.stderr + r.stdout

    def assertRefused(self, needle=None):
        rc, out = self.guard()
        self.assertEqual(rc, 1, f"expected REFUSAL, got rc={rc}:\n{out}")
        if needle:
            self.assertIn(needle, out)
        return out

    def assertAllowed(self):
        rc, out = self.guard()
        self.assertEqual(rc, 0, f"expected ALLOWED, got rc={rc}:\n{out}")


# --- the two classes the guard enforced before plan-01 (regression) --------------------

class FindingsImmutable(RepoCase):
    def setUp(self):
        super().setUp()
        self.write(FINDINGS, "# Findings\n\nA measured fact.\n\n---\n\nMore.\n")
        self.commit("findings")

    def test_body_edit_refused(self):
        self.write(FINDINGS, self.read(FINDINGS) + "A new line.\n")
        self.stage(FINDINGS)
        self.assertRefused("findings body is IMMUTABLE")

    def test_superseded_banner_allowed(self):
        self.write(FINDINGS, "> SUPERSEDED by findings-2026-01-02-second.md\n\n" + self.read(FINDINGS))
        self.stage(FINDINGS)
        self.assertAllowed()

    def test_removing_a_markdown_rule_line_refused(self):
        # diff prints a removed '---' as '----'; the old parser skipped it as a header.
        # Remove ONLY that line — a blank line removed beside it would be caught anyway.
        self.write(FINDINGS, self.read(FINDINGS).replace("\n---\n", "\n"))
        self.stage(FINDINGS)
        self.assertRefused("line(s) removed")

    def test_new_findings_file_allowed(self):
        self.write(f"{T}/findings-2026-01-02-second.md", "# New\n")
        self.stage(f"{T}/findings-2026-01-02-second.md")
        self.assertAllowed()


class SessionLogAppendOnly(RepoCase):
    def setUp(self):
        super().setUp()
        self.write(HANDOFF, handoff())
        self.commit("handoff")

    def test_past_entry_edit_refused(self):
        self.write(HANDOFF, self.read(HANDOFF).replace("Old entry body.", "Rewritten body."))
        self.stage(HANDOFF)
        self.assertRefused("Session log is APPEND-ONLY")

    def test_prepending_an_entry_allowed(self):
        new = self.read(HANDOFF).replace(
            "## Session log (newest first)\n\n",
            "## Session log (newest first)\n\n### 2026-01-02 — second\n\nNew entry.\n\n")
        self.write(HANDOFF, new)
        self.stage(HANDOFF)
        self.assertAllowed()

    # The ordinary hop close — overwrite a Current truth, prepend a Session-log entry, one
    # commit. When the old block holds a '### ' sub-heading git aligns the hunks so the
    # header line prints as removed and re-added; a check that reads hunk positions
    # refuses it (it did, 2026-09-21). The check reads the two files' content instead.
    SUBHEADED = (
        "# Hand-off — test\n\n"
        "## Current truth\n\n"
        "- **Focus 0:** old state.\n- **Focus 1:** older state.\n\n"
        "### Settled\n\nA settled paragraph.\n\n"
        "## Session log (append-only)\n\n"
        "### 2026-01-01 — first entry\n\n- Old entry body.\n"
    )

    def hop_close(self, old_entry="### 2026-01-01 — first entry\n\n- Old entry body.\n"):
        self.write(HANDOFF, self.SUBHEADED)
        self.commit("sub-headed handoff")
        self.write(HANDOFF, (
            "# Hand-off — test\n\n"
            "## Current truth\n\n"
            "- **Focus:** present state.\n\n"
            "## Session log (append-only)\n\n"
            "### 2026-01-02 — second entry\n\n- New entry.\n\n"
            f"{old_entry}"))
        self.stage(HANDOFF)

    def test_block_overwrite_plus_prepend_allowed_though_git_misaligns_the_header(self):
        self.hop_close()
        diff = self.git("diff", "--cached", "--unified=0", "--", HANDOFF).stdout
        self.assertIn("\n-## Session log", diff, "fixture no longer reproduces the misalignment")
        self.assertAllowed()

    def test_block_overwrite_plus_prepend_with_a_past_entry_edited_refused(self):
        self.hop_close(old_entry="### 2026-01-01 — first entry\n\n- Rewritten body.\n")
        self.assertRefused("Session log is APPEND-ONLY")

    def test_block_overwrite_plus_prepend_with_a_past_entry_deleted_refused(self):
        self.hop_close(old_entry="")
        self.assertRefused("Session log is APPEND-ONLY")

    def test_renaming_the_header_line_refused(self):
        self.write(HANDOFF, self.read(HANDOFF).replace(
            "## Session log (newest first)", "## Session log (oldest first)"))
        self.stage(HANDOFF)
        self.assertRefused("Session log is APPEND-ONLY")

    def test_moving_a_past_entry_above_the_header_refused(self):
        moved = self.read(HANDOFF).replace("### 2026-01-01 — first entry\n\nOld entry body.\n", "")
        moved = moved.replace("## Session log (newest first)",
                              "### 2026-01-01 — first entry\n\nOld entry body.\n\n"
                              "## Session log (newest first)")
        self.write(HANDOFF, moved)
        self.stage(HANDOFF)
        self.assertRefused("Session log is APPEND-ONLY")


# --- the overwrite-and-bounded class ---------------------------------------------------

class CurrentTruthBound(RepoCase):
    def base(self, fill):
        self.write(HANDOFF, handoff(fill))
        self.commit("base handoff")

    def test_under_bound_growth_allowed(self):
        self.base(500)
        self.write(HANDOFF, handoff(7000))
        self.stage(HANDOFF)
        self.assertAllowed()

    def test_crossing_the_bound_refused(self):
        self.base(7000)
        self.write(HANDOFF, handoff(9000))
        self.stage(HANDOFF)
        self.assertRefused("Current truth is BOUNDED")

    def test_over_bound_growing_refused(self):
        self.base(9000)
        self.write(HANDOFF, handoff(9500))
        self.stage(HANDOFF)
        self.assertRefused("not smaller than HEAD's")

    def test_over_bound_rewritten_same_size_refused(self):
        self.base(9000)
        self.write(HANDOFF, handoff(9000, marker="y"))
        self.stage(HANDOFF)
        self.assertRefused("not smaller than HEAD's")

    def test_over_bound_shrinking_allowed(self):
        self.base(12000)
        self.write(HANDOFF, handoff(9000))
        self.stage(HANDOFF)
        self.assertAllowed()

    def test_over_bound_block_untouched_refused(self):
        # Operator ruling 2026-09-20, "literal reading": only a Session-log entry is added
        # (here an ORCHESTRATOR NOTE) and the over-bound block is byte-identical — still
        # refused, because every commit to an over-bound handoff must shrink its block.
        self.base(12000)
        new = self.read(HANDOFF).replace(
            "## Session log (newest first)\n\n",
            "## Session log (newest first)\n\n### 2026-01-02 — ORCHESTRATOR NOTE: x\n\nNote.\n\n")
        self.write(HANDOFF, new)
        self.stage(HANDOFF)
        self.assertRefused("leaves it unchanged")

    def test_over_bound_block_untouched_but_note_plus_shrink_allowed(self):
        # The same Session-log entry passes once the commit also shrinks the block.
        self.base(12000)
        new = handoff(9000).replace(
            "## Session log (newest first)\n\n",
            "## Session log (newest first)\n\n### 2026-01-02 — second\n\nEntry.\n\n")
        self.write(HANDOFF, new)
        self.stage(HANDOFF)
        self.assertAllowed()

    def test_new_handoff_over_bound_refused(self):
        self.write(HANDOFF, handoff(9000))
        self.stage(HANDOFF)
        self.assertRefused("a new handoff")

    def test_new_handoff_under_bound_allowed(self):
        self.write(HANDOFF, handoff(500))
        self.stage(HANDOFF)
        self.assertAllowed()

    def test_measures_the_section_not_bytes_above_the_log(self):
        # A small block with 20 KB of other material above the Session log.
        self.base(500)
        self.write(HANDOFF, handoff(600, reading_extra="## Charter\n\n" + "c" * 20000 + "\n\n"))
        self.stage(HANDOFF)
        self.assertAllowed()

    def test_a_session_log_title_naming_current_truth_is_not_the_block(self):
        self.base(500)
        log = ("### 2026-01-02 — Current truth rewritten\n\n" + "z" * 20000 + "\n\n"
               "### 2026-01-01 — first entry\n\nOld entry body.\n")
        self.write(HANDOFF, handoff(600, log=log))
        self.stage(HANDOFF)
        self.assertAllowed()

    def test_handoff_outside_a_threads_tree_ignored(self):
        self.write("docs/handoff.md", handoff(20000))
        self.stage("docs/handoff.md")
        self.assertAllowed()


# --- caches ----------------------------------------------------------------------------

class CacheNoPrepend(RepoCase):
    def setUp(self):
        super().setUp()
        self.write(CACHE, "# Orchestrator cache\n\n## Decisions in force\n\n1. A ruling.\n")
        self.commit("cache")

    def prepend(self, line):
        self.write(CACHE, self.read(CACHE).replace("## Decisions", f"{line}\n\n## Decisions"))
        self.stage(CACHE)

    def test_start_here_refused(self):
        self.prepend("- **START HERE (2026-01-02)** — the hop is home")
        self.assertRefused("prepend marker")

    def test_read_this_first_refused(self):
        self.prepend("**READ THIS FIRST:** the state changed")
        self.assertRefused("prepend marker")

    def test_supersedes_below_refused(self):
        self.prepend("This entry supersedes everything below.")
        self.assertRefused("prepend marker")

    def test_ordinary_decision_allowed(self):
        self.write(CACHE, self.read(CACHE) + "2. Another ruling.\n")
        self.stage(CACHE)
        self.assertAllowed()

    def test_existing_marker_not_re_added_allowed(self):
        self.prepend("- **START HERE** — an old marker")
        self.commit("old marker, committed before the guard existed")
        self.write(CACHE, self.read(CACHE) + "2. Another ruling.\n")
        self.stage(CACHE)
        self.assertAllowed()

    def test_new_cache_with_marker_refused(self):
        path = ".threads/ORCHESTRATOR-CACHE-NEW.md"
        self.write(path, "# Cache\n\nSTART HERE — a new cache born with a marker\n")
        self.stage(path)
        self.assertRefused("prepend marker")


# --- narratives ------------------------------------------------------------------------

class Narratives(RepoCase):
    def test_edit_refused(self):
        self.write(NARRATIVE, f"# Session\n\n{BANNER}\n\nWhat happened.\n")
        self.commit("narrative")
        self.write(NARRATIVE, self.read(NARRATIVE) + "An addendum.\n")
        self.stage(NARRATIVE)
        self.assertRefused("narrative is IMMUTABLE")

    def test_banner_backfill_allowed(self):
        self.write(NARRATIVE, "# Session\n\nWhat happened.\n")
        self.commit("old narrative without the banner")
        self.write(NARRATIVE, f"{BANNER}\n\n" + self.read(NARRATIVE))
        self.stage(NARRATIVE)
        self.assertAllowed()

    def test_new_without_banner_refused(self):
        self.write(NARRATIVE, "# Session\n\nWhat happened.\n")
        self.stage(NARRATIVE)
        self.assertRefused("must carry the pinned banner")

    def test_new_with_banner_allowed(self):
        self.write(NARRATIVE, f"# Session\n\n{BANNER}\n\nWhat happened.\n")
        self.stage(NARRATIVE)
        self.assertAllowed()

    def test_new_with_banner_below_line_ten_refused(self):
        self.write(NARRATIVE, "# Session\n" + "\n" * 10 + f"{BANNER}\n")
        self.stage(NARRATIVE)
        self.assertRefused("must carry the pinned banner")


# --- merges ----------------------------------------------------------------------------

BOUNDS_FILE = ".threads/record-discipline.json"
OTHER_CACHE = ".threads/ORCHESTRATOR-CACHE-OTHER-LANE.md"
PROJECT_MD = "CLAUDE.md"                                # outside .threads/, listed by path


def cache(index="x" * 100, decisions=("1. **First ruling** (operator, 2026-01-01)",)):
    return ("# Cache\n\n## Knowledge index\n\n- " + index + "\n\n"
            "## Decisions in force\n\n" + "\n".join(decisions) + "\n\n"
            "## Update rule\n\nSame commit.\n")


class SizeBounds(RepoCase):
    """plan-01 Step 6: an explicit, project-owned list; one section held to its form."""

    def setUp(self):
        super().setUp()
        self.write(BOUNDS_FILE, (
            '{"size_bounds": ['
            f'{{"path": "{CACHE}", "max_bytes": 400, "excluding_section": "## Decisions in force",'
            ' "entry_line_max_bytes": 120},'
            f'{{"path": "{PROJECT_MD}", "max_bytes": 300}}]}}\n'))
        self.write(CACHE, cache())
        self.write(OTHER_CACHE, cache())
        self.write(PROJECT_MD, "# Project\n\n" + "p" * 200 + "\n")
        self.commit("bounded files")

    def test_growth_within_the_bound_allowed(self):
        self.write(CACHE, cache(index="x" * 150))
        self.stage(CACHE)
        self.assertAllowed()

    def test_crossing_the_bound_refused(self):
        self.write(CACHE, cache(index="x" * 600))
        self.stage(CACHE)
        self.assertRefused("is over its bound of 400 B")

    def test_decision_lines_never_hit_the_byte_bound(self):
        many = tuple(f"{n}. **Ruling number {n}, as words** (operator, 2026-01-02)" for n in range(1, 60))
        self.write(CACHE, cache(decisions=many))             # the section alone is ~3.5 KB
        self.stage(CACHE)
        self.assertAllowed()

    def test_a_paragraph_added_to_the_decisions_section_refused(self):
        self.write(CACHE, cache(decisions=("1. **First ruling** (operator, 2026-01-01)",
                                           "   - **THE RULING, in full:** a second line of narrative.")))
        self.stage(CACHE)
        self.assertRefused("holds ONE LINE per entry")

    def test_an_over_long_decision_line_refused(self):
        self.write(CACHE, cache(decisions=("1. **First ruling** (operator, 2026-01-01)",
                                           "2. **" + "w" * 200 + "** (operator, 2026-01-02)")))
        self.stage(CACHE)
        self.assertRefused("holds ONE LINE per entry")

    def test_a_listed_file_outside_the_threads_tree_is_bounded(self):
        self.write(PROJECT_MD, "# Project\n\n" + "p" * 400 + "\n")
        self.stage(PROJECT_MD)
        self.assertRefused("is over its bound of 300 B")

    def test_an_unlisted_cache_is_not_bounded(self):
        self.write(OTHER_CACHE, cache(index="x" * 5000))
        self.stage(OTHER_CACHE)
        self.assertAllowed()

    def over_the_bound_already(self):
        self.write(BOUNDS_FILE, self.read(BOUNDS_FILE).replace('"max_bytes": 300', '"max_bytes": 100'))
        self.commit("the bound is tightened under the file")

    def test_over_the_bound_and_shrinking_allowed(self):
        self.over_the_bound_already()
        self.write(PROJECT_MD, "# Project\n\n" + "p" * 150 + "\n")
        self.stage(PROJECT_MD)
        self.assertAllowed()

    def test_over_the_bound_and_not_shrinking_refused(self):
        self.over_the_bound_already()
        self.write(PROJECT_MD, "# Project\n\n" + "q" * 200 + "\n")
        self.stage(PROJECT_MD)
        self.assertRefused("does not shrink it")

    def charter(self, sequencing="s" * 100, images="i" * 100):
        return ("# Charter\n\n## Sequencing — the order\n\n" + sequencing + "\n\n"
                "## Images\n\n" + images + "\n\n## Hard constraints on every hop\n\nTwo.\n")

    def bound_the_charters_boot_read_sections(self):
        self.write(BOUNDS_FILE, (
            '{"size_bounds": [{"path": ".threads/x/plan-02-charter.md", "max_bytes": 300,'
            ' "only_sections": ["## Sequencing", "## Hard constraints on every hop"]}]}\n'))
        self.write(".threads/x/plan-02-charter.md", self.charter())
        self.commit("a charter, its two boot-read sections bounded")

    def test_only_sections_lets_the_rest_of_the_file_grow(self):
        self.bound_the_charters_boot_read_sections()
        self.write(".threads/x/plan-02-charter.md", self.charter(images="i" * 5000))
        self.stage(".threads/x/plan-02-charter.md")
        self.assertAllowed()

    def test_only_sections_bounds_the_listed_sections_summed(self):
        self.bound_the_charters_boot_read_sections()
        self.write(".threads/x/plan-02-charter.md", self.charter(sequencing="s" * 400))
        self.stage(".threads/x/plan-02-charter.md")
        self.assertRefused("is over its bound of 300 B")

    def test_an_unreadable_list_refused(self):
        self.write(BOUNDS_FILE, '{"size_bounds": [{"max_bytes": 5}]}\n')
        self.stage(BOUNDS_FILE)
        self.assertRefused("not readable as")

    def test_conflicted_merge_stands_down_on_a_size_bound(self):
        self.git("checkout", "-q", "-b", "side")
        self.write(PROJECT_MD, "# Project\n\n" + "s" * 400 + "\n")
        self.git("add", "-A")
        self.git("commit", "-q", "--no-verify", "-m", "side grows the file")
        self.git("checkout", "-q", "main")
        self.write(PROJECT_MD, "# Project\n\n" + "m" * 210 + "\n")
        self.commit("main edits the file")
        r = self.git("merge", "side", check=False)
        self.assertNotEqual(r.returncode, 0, "fixture expected a conflict")
        self.write(PROJECT_MD, "# Project\n\n" + "s" * 400 + "\n")   # resolve: take side's
        self.stage(PROJECT_MD)
        self.assertAllowed()
        self.git("commit", "-q", "-m", "merge")
        self.write(PROJECT_MD, "# Project\n\n" + "s" * 401 + "\n")   # the next ordinary commit
        self.stage(PROJECT_MD)
        self.assertRefused("does not shrink it")


class Merges(RepoCase):
    def setUp(self):
        super().setUp()
        self.write(HANDOFF, handoff(500))
        self.write(FINDINGS, "# Findings\n\nA fact.\n")
        self.commit("base")

    def conflicted_merge_bringing_an_over_bound_block(self):
        self.git("checkout", "-q", "-b", "side")
        self.write(HANDOFF, handoff(9000, marker="s"))
        self.commit("side grows the block")
        self.git("checkout", "-q", "main")
        self.write(HANDOFF, handoff(600, marker="m"))
        self.commit("main edits the block")
        r = self.git("merge", "side", check=False)
        self.assertNotEqual(r.returncode, 0, "fixture expected a conflict")
        self.write(HANDOFF, handoff(9000, marker="s"))       # resolve: take side's block
        self.stage(HANDOFF)
        self.assertEqual(self.git("rev-parse", "-q", "--verify", "MERGE_HEAD",
                                  check=False).returncode, 0)

    def test_conflicted_merge_stands_down_on_the_bound(self):
        self.conflicted_merge_bringing_an_over_bound_block()
        self.assertAllowed()

    def test_conflicted_merge_still_guards_findings(self):
        self.conflicted_merge_bringing_an_over_bound_block()
        self.write(FINDINGS, self.read(FINDINGS) + "Edited during the merge.\n")
        self.stage(FINDINGS)
        self.assertRefused("findings body is IMMUTABLE")

    def test_squash_merge_is_refused(self):
        self.git("checkout", "-q", "-b", "side")
        self.write(HANDOFF, handoff(9000, marker="s"))
        self.commit("side grows the block")
        self.git("checkout", "-q", "main")
        self.git("merge", "--squash", "side")
        self.assertNotEqual(self.git("rev-parse", "-q", "--verify", "MERGE_HEAD",
                                     check=False).returncode, 0, "squash must set no MERGE_HEAD")
        self.assertRefused("Current truth is BOUNDED")


# --- the dry run -----------------------------------------------------------------------

class DryRun(RepoCase):
    def test_reports_and_exits_zero(self):
        self.write(HANDOFF, handoff(9000))
        self.commit("born over the bound (fixture has no hooks)")
        self.write(CACHE, "# Cache\n")
        self.commit("clean")
        rc, out = self.guard("--range", "HEAD~2..HEAD")
        self.assertEqual(rc, 0, out)
        self.assertIn("WOULD BE REFUSED", out)
        self.assertIn("1 of 2 non-merge commit(s)", out)


if __name__ == "__main__":
    unittest.main()
