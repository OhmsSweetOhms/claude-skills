#!/usr/bin/env python3
"""Tests for install_stop_hook.py. Every test writes to a TEMP settings file;
nothing here can reach the operator's real settings. Run as a module:
`python3 -m unittest test_install_stop_hook -v`."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
INSTALLER = HERE / "install_stop_hook.py"
sys.path.insert(0, str(HERE))
import install_stop_hook as ish  # noqa: E402

OTHER_HOOK = {"hooks": [{"type": "command", "command": "echo someone-elses-hook"}]}


def run(*args: str):
    return subprocess.run([sys.executable, str(INSTALLER), *args],
                          capture_output=True, text=True)


class Installer(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ishtest-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.settings = self.tmp / "settings.json"

    def write(self, obj) -> None:
        self.settings.write_text(json.dumps(obj, indent=2) + "\n")

    def read(self) -> dict:
        return json.loads(self.settings.read_text())

    def stop_entries(self) -> list:
        return self.read().get("hooks", {}).get("Stop", [])

    def backups(self) -> list[Path]:
        return sorted(self.tmp.glob("settings.json.bak-*"))

    # ---------------------------------------------------------------- dry run

    def test_a_dry_run_writes_nothing_and_says_so(self):
        self.write({"model": "opus"})
        out = run("--settings", str(self.settings))
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("DRY RUN", out.stdout)
        self.assertIn("installed the waiter entry", out.stdout)
        self.assertIn('"asyncRewake": true', out.stdout)
        self.assertEqual(self.read(), {"model": "opus"})
        self.assertEqual(self.backups(), [])

    def test_the_dry_run_shows_the_entry_that_would_be_written(self):
        out = run("--settings", str(self.settings))
        self.assertIn("mb.py", out.stdout)
        self.assertIn("wait --deadline 3300", out.stdout)
        self.assertIn('"timeout": 3600', out.stdout)
        self.assertFalse(self.settings.exists())

    # ----------------------------------------------------------------- apply

    def test_apply_installs_the_entry_and_backs_the_file_up(self):
        self.write({"model": "opus", "hooks": {"Stop": [OTHER_HOOK]}})
        out = run("--settings", str(self.settings), "--apply")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("WROTE", out.stdout)
        entries = self.stop_entries()
        self.assertEqual(len(entries), 2, "the other Stop hook must survive")
        self.assertEqual(entries[0], OTHER_HOOK)
        hook = entries[1]["hooks"][0]
        self.assertTrue(hook["asyncRewake"])
        self.assertEqual(hook["timeout"], 3600)
        self.assertIn("wait --deadline 3300", hook["command"])
        self.assertEqual(self.read()["model"], "opus", "unrelated settings must survive")
        self.assertEqual(len(self.backups()), 1)
        self.assertEqual(json.loads(self.backups()[0].read_text())["hooks"]["Stop"], [OTHER_HOOK])

    def test_applying_twice_changes_nothing_the_second_time(self):
        self.write({"model": "opus"})      # an existing file, so the first apply backs it up
        self.assertEqual(run("--settings", str(self.settings), "--apply").returncode, 0)
        first = self.settings.read_text()
        out = run("--settings", str(self.settings), "--apply")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("already installed, unchanged", out.stdout)
        self.assertNotIn("WROTE", out.stdout)
        self.assertEqual(self.settings.read_text(), first)
        self.assertEqual(len(self.backups()), 1, "an idempotent run must not pile up backups")

    def test_a_stale_entry_is_updated_in_place_not_duplicated(self):
        self.assertEqual(run("--settings", str(self.settings), "--apply",
                             "--deadline", "600", "--timeout", "900").returncode, 0)
        self.assertEqual(len(self.stop_entries()), 1)
        out = run("--settings", str(self.settings), "--apply")
        self.assertIn("updated the existing waiter entry", out.stdout)
        entries = self.stop_entries()
        self.assertEqual(len(entries), 1, "the waiter must never be installed twice")
        self.assertEqual(entries[0]["hooks"][0]["timeout"], 3600)
        self.assertIn("--deadline 3300", entries[0]["hooks"][0]["command"])

    def test_duplicates_already_in_the_file_are_collapsed_to_one(self):
        self.write({"hooks": {"Stop": [ish.build_entry(900, 600), OTHER_HOOK,
                                       ish.build_entry(3600, 3300)]}})
        self.assertEqual(run("--settings", str(self.settings), "--apply").returncode, 0)
        entries = self.stop_entries()
        self.assertEqual(len(entries), 2)
        self.assertEqual(sum(1 for e in entries if ish.is_ours(e)), 1)
        self.assertIn(OTHER_HOOK, entries)

    # ---------------------------------------------------------------- remove

    def test_remove_takes_out_only_our_entry(self):
        self.write({"model": "opus", "hooks": {"Stop": [OTHER_HOOK], "SessionStart": [OTHER_HOOK]}})
        self.assertEqual(run("--settings", str(self.settings), "--apply").returncode, 0)
        out = run("--settings", str(self.settings), "--apply", "--remove")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("removed 1 waiter entry", out.stdout)
        self.assertEqual(self.stop_entries(), [OTHER_HOOK])
        self.assertEqual(self.read()["hooks"]["SessionStart"], [OTHER_HOOK])
        self.assertEqual(self.read()["model"], "opus")

    def test_removing_when_absent_writes_nothing(self):
        self.write({"model": "opus"})
        out = run("--settings", str(self.settings), "--apply", "--remove")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("nothing to remove", out.stdout)
        self.assertEqual(self.read(), {"model": "opus"})
        self.assertEqual(self.backups(), [])

    def test_remove_leaves_no_empty_hooks_scaffolding_behind(self):
        self.assertEqual(run("--settings", str(self.settings), "--apply").returncode, 0)
        self.assertEqual(run("--settings", str(self.settings), "--apply", "--remove").returncode, 0)
        self.assertEqual(self.read(), {})

    # --------------------------------------------------------------- refusals

    def test_unparseable_settings_are_refused_and_left_alone(self):
        self.settings.write_text("{ not json at all\n")
        out = run("--settings", str(self.settings), "--apply")
        self.assertEqual(out.returncode, 2)
        self.assertIn("refusing to touch unparseable settings", out.stderr)
        self.assertEqual(self.settings.read_text(), "{ not json at all\n")
        self.assertEqual(self.backups(), [])

    def test_a_deadline_at_or_over_the_timeout_is_refused(self):
        for deadline, timeout in (("3600", "3600"), ("4000", "3600")):
            out = run("--settings", str(self.settings), "--apply",
                      "--deadline", deadline, "--timeout", timeout)
            self.assertEqual(out.returncode, 2, (deadline, timeout))
            self.assertIn("killed before it can renew itself", out.stderr)
        self.assertFalse(self.settings.exists())

    def test_the_installed_command_names_this_checkouts_mb_py(self):
        """A branch checkout under trial must install ITS waiter, not the
        installed skill's — the same rule the packet emitter follows."""
        self.assertEqual(run("--settings", str(self.settings), "--apply").returncode, 0)
        command = self.stop_entries()[0]["hooks"][0]["command"]
        self.assertIn(ish.portable(HERE / "mb.py"), command)
        self.assertTrue((HERE / "mb.py").is_file())


if __name__ == "__main__":
    unittest.main()
