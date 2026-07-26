#!/usr/bin/env python3
"""Desk tests for sd_boot.py provision-rootfs / verify-rootfs.

No card, no root, no board: a fake repo tree (recipe + build-output.json +
deploy/ artifacts + profile files + an external bundle dir) and a fake rootfs
(etc/ + lib/ present) are built in a tmpdir, and every run uses
--unsafe-no-mount-check so the mount-point/fstype guards stand down.

    python3 test_sd_boot_provision.py            # this file
    python3 -m unittest test_sd_boot_provision   # or via unittest
"""

import contextlib
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sd_boot  # noqa: E402


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def run_cli(*argv):
    """Invoke sd_boot.main() as the CLI would; -> (rc, stdout). die() exits
    with an 'ERROR: ...' string, which comes back as rc."""
    buf = io.StringIO()
    old_argv = sys.argv
    sys.argv = ["sd_boot.py"] + list(argv)
    try:
        with contextlib.redirect_stdout(buf):
            rc = sd_boot.main()
    except SystemExit as e:
        rc = e.code
    finally:
        sys.argv = old_argv
    return rc, buf.getvalue()


def is_err(rc) -> bool:
    return isinstance(rc, str) and rc.startswith("ERROR")


class ProvisionRootfsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="sdprov."))

        # ---- fake repo root ------------------------------------------------
        self.root = self.tmp / "repo"
        self.profile = self.root / "platforms" / "profiles" / "testprof"
        (self.profile / "linux" / "capture" / "sub").mkdir(parents=True)
        (self.profile / "linux" / "capture" / "__pycache__").mkdir()
        board = self.root / "platforms" / "boards" / "testboard"
        board.mkdir(parents=True)

        # profile files
        (self.profile / "linux" / "tool.py").write_bytes(b"#!/usr/bin/env python3\n")
        cap = self.profile / "linux" / "capture"
        (cap / "ring_drain.c").write_bytes(b"int main(void){return 0;}\n")
        (cap / "sub" / "nested.h").write_bytes(b"#define N 1\n")
        (cap / "skipme.log").write_bytes(b"excluded by pattern\n")
        (cap / "model.pyc").write_bytes(b"excluded bytecode\n")
        (cap / "__pycache__" / "x.pyc").write_bytes(b"pruned dir\n")
        (self.profile / "linux" / "config.example.json").write_text('{"tuned": false}\n')
        # repo-scheme file
        (board / "mod.ko").write_bytes(b"fake kernel module\n")

        # ---- recipe --------------------------------------------------------
        self.recipe = {
            "stages": {"linux": {"rootfs_provision": {
                "files": [
                    {"from": "artifact:r5-firmware-elf",
                     "to": "/lib/firmware/r5_0_capture_rproc.elf"},
                    {"from": "repo:platforms/boards/testboard/mod.ko",
                     "to": "/root/mod.ko"},
                    {"from": "linux/tool.py", "to": "/usr/local/sbin/tool",
                     "mode": "0755"},
                    {"from_dir": "linux/capture",
                     "to": "/root/capture/linux/capture",
                     "exclude": ["__pycache__", "*.pyc", "skipme*"]},
                    {"from_dir": "external:bundle", "to": "/root/bundle"},
                ],
                "symlinks": [
                    {"symlink": "/etc/systemd/system/multi-user.target.wants/"
                                "gps-live.service",
                     "target": "/etc/systemd/system/gps-live.service"},
                ],
                "boot_config_seed": [
                    {"from": "linux/config.example.json", "to": "config.json"},
                ],
            }}},
        }
        (self.profile / "build-recipe.json").write_text(json.dumps(self.recipe))

        # ---- bundle + build-output ----------------------------------------
        run_dir = self.root / "systems" / "builds" / "run1"
        deploy = run_dir / "deploy"
        deploy.mkdir(parents=True)
        arts = {"r5-firmware-elf": ("r5_0_capture_rproc.elf", b"ELF r5 fw"),
                "boot-image": ("BOOT.BIN", b"boot bin bytes"),
                "kernel-image": ("Image", b"kernel image bytes"),
                "device-tree-blob": ("sys.dtb", b"dtb bytes")}
        self.art_sha = {}
        deploy_list = []
        for kind, (fname, data) in arts.items():
            (deploy / fname).write_bytes(data)
            self.art_sha[kind] = sha(data)
            deploy_list.append({"file": fname, "sha256": sha(data),
                                "bytes": len(data), "kind": kind})
        self.build_output = run_dir / "build-output.json"
        self.build_output.write_text(json.dumps({
            "schema": "build-output/1", "run_label": "run1",
            "recipe_path": "platforms/profiles/testprof/build-recipe.json",
            "deploy": deploy_list}))

        # ---- external bundle ----------------------------------------------
        self.bundle = self.tmp / "banked-bundle"
        (self.bundle / "bin").mkdir(parents=True)
        (self.bundle / "lib").mkdir()
        (self.bundle / "bin" / "sdrangelsrv").write_bytes(b"fake aarch64 srv")
        (self.bundle / "lib" / "libx.so").write_bytes(b"fake so")

        # ---- fake rootfs + boot mount -------------------------------------
        self.rootfs = self.tmp / "rootfs"
        (self.rootfs / "etc").mkdir(parents=True)
        (self.rootfs / "lib").mkdir()
        self.bootmnt = self.tmp / "bootmnt"
        self.bootmnt.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---- helpers ----------------------------------------------------------

    def provision(self, *extra, write=True, external=True):
        argv = ["provision-rootfs", "--rootfs-mount", str(self.rootfs),
                "--from-build-output", str(self.build_output),
                "--unsafe-no-mount-check"]
        if external:
            argv += ["--external", f"bundle={self.bundle}"]
        if write:
            argv.append("--write")
        argv += list(extra)
        return run_cli(*argv)

    def write_boot_manifest(self, image_sha=None):
        """A deploy-style manifest on the fake boot mount."""
        files = [{"file": "BOOT.BIN", "sha256": self.art_sha["boot-image"]},
                 {"file": "Image",
                  "sha256": image_sha or self.art_sha["kernel-image"]},
                 {"file": "system.dtb",
                  "sha256": self.art_sha["device-tree-blob"]}]
        (self.bootmnt / sd_boot.MANIFEST_NAME).write_text(
            json.dumps({"schema": "sd-deployed/1", "files": files}))

    # ---- cases ------------------------------------------------------------

    def test_dry_run_writes_nothing(self):
        rc, out = self.provision(write=False)
        self.assertEqual(rc, 0, out)
        self.assertIn("DRY RUN", out)
        self.assertFalse((self.rootfs / "lib/firmware").exists())
        self.assertFalse((self.rootfs / "root").exists())
        self.assertFalse((self.rootfs / "usr").exists())

    def test_write_places_files_modes_excludes_symlinks(self):
        rc, out = self.provision()
        self.assertEqual(rc, 0, out)
        # single files land, artifact + repo + profile schemes
        elf = self.rootfs / "lib/firmware/r5_0_capture_rproc.elf"
        self.assertEqual(sha(elf.read_bytes()), self.art_sha["r5-firmware-elf"])
        self.assertTrue((self.rootfs / "root/mod.ko").is_file())
        # explicit mode applied
        tool = self.rootfs / "usr/local/sbin/tool"
        self.assertEqual(tool.stat().st_mode & 0o777, 0o755)
        # tree copy honors excludes, keeps nested structure
        cdir = self.rootfs / "root/capture/linux/capture"
        self.assertTrue((cdir / "ring_drain.c").is_file())
        self.assertTrue((cdir / "sub/nested.h").is_file())
        self.assertFalse((cdir / "skipme.log").exists())
        self.assertFalse((cdir / "model.pyc").exists())
        self.assertFalse((cdir / "__pycache__").exists())
        # external tree
        self.assertTrue((self.rootfs / "root/bundle/bin/sdrangelsrv").is_file())
        # symlink created with the declared (rootfs-absolute) target
        link = self.rootfs / ("etc/systemd/system/multi-user.target.wants/"
                              "gps-live.service")
        self.assertTrue(link.is_symlink())
        self.assertEqual(os.readlink(link),
                         "/etc/systemd/system/gps-live.service")

    def test_manifest_contents(self):
        rc, out = self.provision()
        self.assertEqual(rc, 0, out)
        man = json.loads(
            (self.rootfs / "root/provision-manifest.json").read_text())
        self.assertEqual(man["schema"], "provisioned-rootfs/1")
        self.assertEqual(man["run_label"], "run1")
        self.assertEqual(man["boot_identity"],
                         {"BOOT.BIN": self.art_sha["boot-image"],
                          "Image": self.art_sha["kernel-image"],
                          "system.dtb": self.art_sha["device-tree-blob"]})
        by_path = {r["path"]: r for r in man["files"]}
        rec = by_path["/lib/firmware/r5_0_capture_rproc.elf"]
        self.assertEqual(rec["sha256"], self.art_sha["r5-firmware-elf"])
        self.assertIn("run1::r5-firmware-elf", rec["provenance"])
        # tree files are recorded individually
        self.assertIn("/root/capture/linux/capture/sub/nested.h", by_path)
        self.assertEqual(man["symlinks"][0]["target"],
                         "/etc/systemd/system/gps-live.service")

    def test_verify_green_then_red_after_tamper(self):
        rc, out = self.provision()
        self.assertEqual(rc, 0, out)
        rc, out = run_cli("verify-rootfs", "--rootfs-mount", str(self.rootfs))
        self.assertEqual(rc, 0, out)
        self.assertIn("ALL GREEN", out)
        (self.rootfs / "root/mod.ko").write_bytes(b"tampered")
        rc, out = run_cli("verify-rootfs", "--rootfs-mount", str(self.rootfs))
        self.assertEqual(rc, 1, out)
        self.assertIn("MISMATCH", out)
        self.assertIn("/root/mod.ko", out)

    def test_boot_config_seed_only_if_absent(self):
        self.write_boot_manifest()
        rc, out = self.provision("--boot-mount", str(self.bootmnt))
        self.assertEqual(rc, 0, out)
        seeded = self.bootmnt / "config.json"
        self.assertEqual(json.loads(seeded.read_text()), {"tuned": False})
        # operator tunes it; a re-provision must leave it alone
        seeded.write_text('{"tuned": true}')
        rc, out = self.provision("--boot-mount", str(self.bootmnt))
        self.assertEqual(rc, 0, out)
        self.assertIn("exists -- left untouched", out)
        self.assertEqual(json.loads(seeded.read_text()), {"tuned": True})

    def test_boot_identity_cross_check_dies_on_mismatch(self):
        self.write_boot_manifest(image_sha=sha(b"a DIFFERENT image"))
        rc, out = self.provision("--boot-mount", str(self.bootmnt))
        self.assertTrue(is_err(rc), rc)
        self.assertIn("identity mismatch", rc)
        # and nothing was written
        self.assertFalse((self.rootfs / "root").exists())

    def test_missing_external_mapping_dies_naming_the_flag(self):
        rc, out = self.provision(external=False)
        self.assertTrue(is_err(rc), rc)
        self.assertIn("--external bundle=", rc)

    def test_symlink_wrong_target_is_replaced_with_note(self):
        wants = self.rootfs / "etc/systemd/system/multi-user.target.wants"
        wants.mkdir(parents=True)
        stale = wants / "gps-live.service"
        stale.symlink_to("/etc/systemd/system/WRONG.service")
        rc, out = self.provision()
        self.assertEqual(rc, 0, out)
        self.assertIn("REPLACES", out)
        self.assertEqual(os.readlink(stale),
                         "/etc/systemd/system/gps-live.service")

    def test_partial_bundle_cannot_bind_identity(self):
        doc = json.loads(self.build_output.read_text())
        doc["deploy"] = [a for a in doc["deploy"]
                         if a["kind"] != "kernel-image"]
        self.build_output.write_text(json.dumps(doc))
        rc, out = self.provision()
        self.assertTrue(is_err(rc), rc)
        self.assertIn("kernel-image", rc)

    def test_rootfs_guard_wants_etc_and_lib(self):
        bare = self.tmp / "notrootfs"
        bare.mkdir()
        rc, out = run_cli("provision-rootfs", "--rootfs-mount", str(bare),
                          "--from-build-output", str(self.build_output),
                          "--external", f"bundle={self.bundle}",
                          "--unsafe-no-mount-check")
        self.assertTrue(is_err(rc), rc)
        self.assertIn("etc/", rc)


if __name__ == "__main__":
    unittest.main(verbosity=2)
