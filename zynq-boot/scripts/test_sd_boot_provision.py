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
        # A real bank bundle carries symlinks, some pointing at absolute paths
        # that only resolve on the BOARD (the plan-06 bundle links
        # sitecustomize.py -> /etc/python3.10/sitecustomize.py). These must be
        # reproduced as links, never dereferenced. Deliberately dangling on the
        # host, which is exactly the case that must not crash the planner.
        os.symlink("/etc/python3.10/sitecustomize.py",
                   self.bundle / "lib" / "sitecustomize.py")

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

    def second_bundle(self, kind="device-tree-blob", fname="sys2.dtb",
                      data=b"newer dtb bytes", label="run2",
                      recipe="platforms/profiles/testprof/build-recipe.json"):
        """A stage-scoped bundle carrying ONE artifact -- the shape
        `socks_build.py --execute --stage dt` banks."""
        run_dir = self.root / "systems" / "builds" / label
        deploy = run_dir / "deploy"
        deploy.mkdir(parents=True)
        (deploy / fname).write_bytes(data)
        out = run_dir / "build-output.json"
        out.write_text(json.dumps({
            "schema": "build-output/1", "run_label": label,
            "recipe_path": recipe,
            "deploy": [{"file": fname, "sha256": sha(data),
                        "bytes": len(data), "kind": kind}]}))
        return out, sha(data)

    # ---- cases ------------------------------------------------------------

    def test_second_bundle_overrides_one_kind_and_binds_the_rest(self):
        """The board's image is not always one bundle: a full set plus a later
        DT-only rebuild. Later wins per kind; everything else falls through."""
        second, dtb_sha = self.second_bundle()
        rc, out = self.provision("--from-build-output", str(second))
        self.assertEqual(rc, 0, out)
        self.assertIn("override: device-tree-blob now from run2", out)
        self.assertIn("2 build-outputs merged", out)
        man = json.loads((self.rootfs / sd_boot.ROOTFS_MANIFEST_REL).read_text())
        # The rootfs binds to the NEW dtb but the ORIGINAL BOOT.BIN and Image.
        self.assertEqual(man["boot_identity"]["system.dtb"], dtb_sha)
        self.assertEqual(man["boot_identity"]["BOOT.BIN"],
                         self.art_sha["boot-image"])
        self.assertEqual(man["boot_identity"]["Image"],
                         self.art_sha["kernel-image"])
        self.assertEqual([s["run_label"] for s in man["sources"]],
                         ["run1", "run2"])

    def test_override_order_matters(self):
        """Naming the stage-scoped bundle FIRST must leave the full bundle's
        artifact in place -- last one wins, not 'the smaller one wins'."""
        second, dtb_sha = self.second_bundle()
        argv = ["provision-rootfs", "--rootfs-mount", str(self.rootfs),
                "--from-build-output", str(second),
                "--from-build-output", str(self.build_output),
                "--external", f"bundle={self.bundle}",
                "--unsafe-no-mount-check", "--write"]
        rc, out = run_cli(*argv)
        self.assertEqual(rc, 0, out)
        man = json.loads((self.rootfs / sd_boot.ROOTFS_MANIFEST_REL).read_text())
        self.assertEqual(man["boot_identity"]["system.dtb"],
                         self.art_sha["device-tree-blob"])
        self.assertNotEqual(man["boot_identity"]["system.dtb"], dtb_sha)

    def test_bundles_with_different_recipes_are_refused(self):
        """The recipe is the contract for what the card needs; two of them
        cannot both apply, and silently preferring one builds a half-described
        card."""
        second, _ = self.second_bundle(recipe="platforms/profiles/other/build-recipe.json")
        rc, _out = self.provision("--from-build-output", str(second))
        self.assertTrue(is_err(rc), rc)
        self.assertIn("different recipes", rc)

    def test_stage_scoped_bundle_alone_still_cannot_bind(self):
        """Repeatability must not weaken the binding rule: a DTB-only bundle
        on its own has no BOOT.BIN or Image to bind the rootfs to."""
        second, _ = self.second_bundle()
        argv = ["provision-rootfs", "--rootfs-mount", str(self.rootfs),
                "--from-build-output", str(second),
                "--external", f"bundle={self.bundle}",
                "--unsafe-no-mount-check", "--write"]
        rc, _out = run_cli(*argv)
        self.assertTrue(is_err(rc), rc)
        self.assertIn("cannot bind this rootfs", rc)

    def test_load_build_outputs_refuses_a_bare_string(self):
        """A string is iterable -- without the guard it walks the path
        character by character and reports a pile of missing files."""
        with self.assertRaises(SystemExit):
            sd_boot.load_build_outputs(str(self.build_output))

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

    def test_bundle_symlink_is_reproduced_not_dereferenced(self):
        """A symlink inside a bundle tree must land as a symlink.

        Dereferencing it copies the HOST's copy of the target onto the card
        (the mounted rootfs resolves absolute links against the host), baking
        host state into the image and destroying a link that was meant to
        resolve against the board's own filesystem. Bench-found 2026-07-27 on
        the plan-06 bundle's sitecustomize.py.
        """
        rc, out = self.provision()
        self.assertEqual(rc, 0, out)
        link = self.rootfs / "root/bundle/lib/sitecustomize.py"
        self.assertTrue(link.is_symlink(), "must be a link, not a copy")
        self.assertEqual(os.readlink(link), "/etc/python3.10/sitecustomize.py")
        # and it is recorded where verify-rootfs looks for links, not as a
        # file whose sha would be the dereferenced host content
        man = json.loads(
            (self.rootfs / "root/provision-manifest.json").read_text())
        self.assertIn("/root/bundle/lib/sitecustomize.py",
                      {r["path"] for r in man["symlinks"]})
        self.assertNotIn("/root/bundle/lib/sitecustomize.py",
                         {r["path"] for r in man["files"]})

    def test_reprovision_over_existing_bundle_symlink_is_idempotent(self):
        """Re-provisioning a card that already carries the bundle must work.

        copy2 follows symlinks on both ends, so when the destination already
        holds the same link both sides resolve to one host file and it raises
        SameFileError -- aborting mid-run and stranding a half-provisioned
        card. That is what happened re-provisioning the old card 2026-07-27.
        """
        rc, out = self.provision()
        self.assertEqual(rc, 0, out)
        rc, out = self.provision()
        self.assertEqual(rc, 0, out)
        self.assertNotIn("SameFileError", out)
        link = self.rootfs / "root/bundle/lib/sitecustomize.py"
        self.assertTrue(link.is_symlink())
        self.assertEqual(os.readlink(link), "/etc/python3.10/sitecustomize.py")

    def test_regular_file_never_written_through_a_stale_symlink(self):
        """A link squatting on a destination path must be removed, not
        followed -- otherwise the copy clobbers the link's target."""
        victim = self.tmp / "victim.txt"
        victim.write_bytes(b"do not clobber me")
        dest = self.rootfs / "root/mod.ko"
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists() or dest.is_symlink():
            dest.unlink()
        os.symlink(victim, dest)
        rc, out = self.provision()
        self.assertEqual(rc, 0, out)
        self.assertFalse(dest.is_symlink(), "stale link must be replaced")
        self.assertEqual(victim.read_bytes(), b"do not clobber me")

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
        # by path, not by index: the symlinks list also carries any links
        # reproduced out of a bundle tree, whose order is not this test's
        # concern
        links = {r["path"]: r for r in man["symlinks"]}
        self.assertEqual(
            links["/etc/systemd/system/multi-user.target.wants/"
                  "gps-live.service"]["target"],
            "/etc/systemd/system/gps-live.service")

    def test_verify_green_then_red_after_tamper(self):
        rc, out = self.provision()
        self.assertEqual(rc, 0, out)
        rc, out = run_cli("verify-rootfs", "--rootfs-mount", str(self.rootfs),
                          "--unsafe-no-mount-check")
        self.assertEqual(rc, 0, out)
        self.assertIn("ALL GREEN", out)
        (self.rootfs / "root/mod.ko").write_bytes(b"tampered")
        rc, out = run_cli("verify-rootfs", "--rootfs-mount", str(self.rootfs),
                          "--unsafe-no-mount-check")
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
