#!/usr/bin/env python3
"""sd_boot.py -- prepare a bootable SD card for a Zynq / ZynqMP board.

Board-agnostic. Three subcommands, in the order you normally need them:

  flash    write a base OS image (.img/.img.xz/.zip) to a removable device
  deploy   place the per-board boot artifacts on the FAT32 BOOT partition
  verify   re-check a card against the manifest deploy wrote on it

`deploy` is the one you run most: a freshly flashed vendor image is usually
NOT bootable for your board yet, because vendors ship a multi-board bundle
with artifacts in per-board subdirectories and the boot loader only reads the
partition root. Deploying is a handful of copies plus a rename -- easy to get
subtly wrong, and each mistake costs a boot cycle.

WHY THIS IS CAREFUL ABOUT DEVICES
`flash` writes raw bytes to a block device. Pointing it at the wrong /dev
node destroys a disk. So: it is dry-run by default, it refuses non-removable
devices, it refuses anything holding a mounted / or /home, it refuses devices
larger than a size ceiling (an SD card is not 2 TB), and it requires you to
repeat the device name in --confirm. None of those guards can be satisfied by
accident.

EXAMPLES
  # See what would happen -- no writes:
  sd_boot.py flash --image <base>.img --device /dev/sdX
  # Actually write it:
  sd_boot.py flash --image <base>.img --device /dev/sdX --confirm /dev/sdX

  # Deploy a socks build's deploy set onto the mounted BOOT partition:
  sd_boot.py deploy --boot-mount /run/media/<user>/BOOT \\
      --from-build-output systems/builds/<run-label>/build-output.json

  # Fully generic: name the pairs yourself
  sd_boot.py deploy --boot-mount <mnt> --set out/BOOT.BIN:BOOT.BIN \\
      --set out/Image:Image --set out/board.dtb:system.dtb

  sd_boot.py verify --boot-mount <mnt>

Part of the zynq-boot skill. Board-specific facts -- boot-mode switches, which
base image, what a good boot looks like -- live in references/<board>.md; this
script deliberately knows none of them.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

MANIFEST_NAME = "sd-deployed-manifest.json"

# Default mapping from a socks build-output.json artifact `kind` to the name
# the boot loader expects at the FAT32 partition root. The rename is the point:
# a build bank names artifacts for provenance (system_<variant>.dtb), the boot
# loader wants a fixed name (system.dtb). Override per board with --map.
DEFAULT_KIND_MAP = {
    "boot-image": "BOOT.BIN",
    "kernel-image": "Image",
    "device-tree-blob": "system.dtb",
}

# Artifacts that belong in the ROOTFS, not on the BOOT partition. Listed so
# deploy can say why it skipped them rather than silently dropping them.
ROOTFS_KINDS = {
    "r5-firmware-elf": "/lib/firmware/ (loaded by remoteproc, not the boot loader)",
    "kernel-module": "/lib/modules/ or wherever the rootfs expects it",
    "fpga-bitstream": "already inside BOOT.BIN as the PL partition",
    "fpga-xsa": "a build input, never deployed",
}

SIZE_CEILING_GIB = 128


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def die(msg: str) -> None:
    sys.exit(f"ERROR: {msg}")


# --------------------------------------------------------------- device guards

def block_device_facts(device: str) -> dict:
    """Everything the guards need, straight from the kernel."""
    name = os.path.basename(os.path.realpath(device))
    # A partition (sdb1, mmcblk0p1) is never a flash target -- we want the disk.
    sysfs = Path("/sys/block") / name
    if not sysfs.is_dir():
        die(f"{device} is not a whole block device (partitions are not flash "
            f"targets -- pass the disk, e.g. /dev/sdX not /dev/sdX1)")
    def read(rel, default=""):
        try:
            return (sysfs / rel).read_text().strip()
        except OSError:
            return default
    size_bytes = int(read("size", "0")) * 512
    return {
        "name": name,
        "removable": read("removable") == "1",
        "size_bytes": size_bytes,
        "size_gib": size_bytes / (1 << 30),
        "model": read("device/model") or "?",
    }


def mounted_sources() -> dict:
    """mountpoint -> source device, from the kernel's own view."""
    out = {}
    try:
        for line in Path("/proc/self/mounts").read_text().splitlines():
            parts = line.split()
            if len(parts) >= 2:
                out.setdefault(parts[1], parts[0])
    except OSError:
        pass
    return out


def guard_flash_target(device: str, allow_nonremovable: bool,
                       size_ceiling_gib: float) -> dict:
    facts = block_device_facts(device)
    mounts = mounted_sources()
    for critical in ("/", "/home", "/boot"):
        src = mounts.get(critical)
        if src and os.path.basename(os.path.realpath(src)).startswith(facts["name"]):
            die(f"{device} holds the mounted {critical} -- refusing. This is your "
                f"system disk, not an SD card.")
    if not facts["removable"] and not allow_nonremovable:
        die(f"{device} is not marked removable by the kernel "
            f"(/sys/block/{facts['name']}/removable == 0). If it really is your "
            f"card reader, re-run with --allow-nonremovable; some internal "
            f"readers report 0. Check `lsblk -o NAME,SIZE,RM,MODEL` first.")
    if facts["size_gib"] > size_ceiling_gib:
        die(f"{device} is {facts['size_gib']:.1f} GiB, above the {size_ceiling_gib} "
            f"GiB ceiling -- almost certainly not the SD card you meant. Raise it "
            f"with --size-ceiling-gib if you are sure.")
    # Anything mounted off this device would be silently corrupted mid-write.
    live = [mp for mp, src in mounts.items()
            if os.path.basename(os.path.realpath(src)).startswith(facts["name"])]
    if live:
        die(f"{device} has mounted partitions ({', '.join(sorted(live))}) -- "
            f"unmount them first, or the write will corrupt what is mounted.")
    return facts


# ------------------------------------------------------------------- flash

def cmd_flash(args) -> int:
    image = Path(args.image).expanduser()
    if not image.is_file():
        die(f"image not found: {image}")
    facts = guard_flash_target(args.device, args.allow_nonremovable,
                               args.size_ceiling_gib)

    print(f"  image:   {image.name}  ({image.stat().st_size / (1<<30):.2f} GiB)")
    print(f"  device:  {args.device}  [{facts['model']}, "
          f"{facts['size_gib']:.1f} GiB, "
          f"{'removable' if facts['removable'] else 'NOT removable'}]")

    if image.suffix in (".xz", ".zip", ".gz"):
        die(f"{image.name} is compressed -- decompress it first. Writing a "
            f"compressed file raw produces an unbootable card, and doing the "
            f"decompression inline would hide how much is being written.")

    # bmaptool is faster and skips unallocated blocks; vendors rarely ship the
    # .bmap sidecar, hence --nobmap. dd is the universally-available fallback.
    if shutil.which("bmaptool"):
        cmd = ["bmaptool", "copy", "--nobmap", str(image), args.device]
    else:
        cmd = ["dd", f"if={image}", f"of={args.device}", "bs=4M",
               "status=progress", "conv=fsync"]
    printable = " ".join(cmd)

    if args.confirm != args.device:
        print(f"\n  DRY RUN -- nothing written.")
        print(f"  would run:  sudo {printable}")
        print(f"\n  To write for real, repeat the device in --confirm:")
        print(f"      --confirm {args.device}")
        return 0

    print(f"\n  writing (this erases {args.device} completely)...")
    rc = subprocess.run(["sudo"] + cmd).returncode
    if rc != 0:
        die(f"write failed (rc={rc})")
    subprocess.run(["sync"])
    print("  done. Re-seat the card so the new partitions are re-read, then "
          "run `deploy`.")
    return 0


# ------------------------------------------------------------------ deploy

def resolve_pairs(args) -> list[tuple[Path, str, str]]:
    """-> [(source_path, dest_name, provenance)]. Explicit --set always wins."""
    pairs: list[tuple[Path, str, str]] = []
    kind_map = dict(DEFAULT_KIND_MAP)
    for entry in args.map or []:
        if "=" not in entry:
            die(f"--map expects kind=destname, got {entry!r}")
        k, v = entry.split("=", 1)
        kind_map[k] = v

    for entry in args.set or []:
        if ":" not in entry:
            die(f"--set expects src:dest, got {entry!r}")
        src, dest = entry.rsplit(":", 1)
        pairs.append((Path(src).expanduser(), dest, "explicit --set"))

    if args.from_build_output:
        doc_path = Path(args.from_build_output).expanduser()
        try:
            doc = json.loads(doc_path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            die(f"cannot read {doc_path}: {exc}")
        deploy_dir = doc_path.parent / "deploy"
        label = doc.get("run_label", doc_path.parent.name)
        skipped = []
        for art in doc.get("deploy", []):
            kind, fname = art.get("kind"), art["file"]
            dest = kind_map.get(kind)
            if dest is None:
                skipped.append((fname, kind))
                continue
            src = deploy_dir / fname
            # Carry the recorded sha so a corrupted bank is caught before the
            # card is touched, not after a failed boot.
            pairs.append((src, dest, f"{label}::{kind} sha256 {art['sha256']}"))
        for fname, kind in skipped:
            why = ROOTFS_KINDS.get(kind, "no boot-partition destination for this kind")
            print(f"  skip:    {fname:38s} ({kind}) -- {why}")
    return pairs


def guard_boot_mount(mount: Path) -> None:
    if not mount.is_dir():
        die(f"boot mount is not a directory: {mount}")
    mounts = mounted_sources()
    resolved = str(mount.resolve())
    if resolved not in mounts:
        die(f"{mount} is not a mount point. Mount the card's FAT32 BOOT "
            f"partition and pass that path -- writing into an unmounted "
            f"directory silently fills your own disk instead of the card.")
    for critical in ("/", "/home", "/boot", "/usr", "/etc"):
        if resolved == critical:
            die(f"refusing to deploy into {critical}")
    fstype = ""
    try:
        for line in Path("/proc/self/mounts").read_text().splitlines():
            p = line.split()
            if len(p) >= 3 and p[1] == resolved:
                fstype = p[2]
    except OSError:
        pass
    if fstype and fstype not in ("vfat", "msdos", "exfat"):
        die(f"{mount} is {fstype}, not a FAT boot partition. The Zynq boot ROM "
            f"reads BOOT.BIN from a FAT partition; this looks like the rootfs.")


def cmd_deploy(args) -> int:
    mount = Path(args.boot_mount).expanduser()
    guard_boot_mount(mount)
    pairs = resolve_pairs(args)
    if not pairs:
        die("nothing to deploy -- pass --from-build-output or one or more --set")

    missing = [str(s) for s, _d, _p in pairs if not s.is_file()]
    if missing:
        die("source file(s) missing: " + ", ".join(missing))

    have = {d for _s, d, _p in pairs}
    required = set(DEFAULT_KIND_MAP.values())
    if not required <= have and not args.partial:
        die(f"incomplete boot set: missing {sorted(required - have)}. A card "
            f"without all three does not boot. Re-run with --partial if you are "
            f"deliberately updating just one file.")

    print(f"  target:  {mount}")
    for src, dest, prov in pairs:
        print(f"  deploy:  {src.name:38s} -> {dest:14s} [{prov}]")

    if not args.write:
        print("\n  DRY RUN -- nothing copied. Add --write to apply.")
        return 0

    records = []
    for src, dest, prov in pairs:
        want = sha256(src)
        target = mount / dest
        shutil.copy2(src, target)
        records.append({"file": dest, "sha256": want, "bytes": src.stat().st_size,
                        "source": src.name, "provenance": prov})
    # FAT32 through a card reader buffers heavily; verify only after a real
    # flush, or you verify the page cache instead of the card.
    os.sync()
    bad = [r for r in records if sha256(mount / r["file"]) != r["sha256"]]
    if bad:
        die("post-copy verify FAILED for: "
            + ", ".join(r["file"] for r in bad)
            + " -- do not boot this card")
    print(f"\n  verified {len(records)} file(s) on the card after sync")

    if args.uenv_ip:
        uenv = mount / "uEnv.txt"
        line = f"ip={args.uenv_ip}"
        body = uenv.read_text() if uenv.is_file() else ""
        kept = [l for l in body.splitlines() if not l.startswith("ip=")]
        uenv.write_text("\n".join(kept + [line]) + "\n")
        os.sync()
        print(f"  uEnv.txt: {line}")

    manifest = {
        "schema": "sd-deployed/1",
        "deployed": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "files": records,
        "note": ("Written by the zynq-boot skill's sd_boot.py. Re-check with "
                 "`sd_boot.py verify --boot-mount <mnt>` to find out what is "
                 "actually on this card before blaming a boot failure."),
    }
    if args.from_build_output:
        manifest["build_output"] = str(args.from_build_output)
    (mount / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2) + "\n")
    os.sync()
    print(f"  manifest: {MANIFEST_NAME}")
    print("\n  Card is deployed. Before power-on, check the board's reference "
          "for boot-mode switches, FMC power order, and the write-protect tab.")
    return 0


# ------------------------------------------------------------------ verify

def cmd_verify(args) -> int:
    mount = Path(args.boot_mount).expanduser()
    manifest_path = mount / MANIFEST_NAME
    if not manifest_path.is_file():
        die(f"no {MANIFEST_NAME} on {mount} -- this card was not deployed by "
            f"this script, so there is nothing to verify against.")
    doc = json.loads(manifest_path.read_text())
    bad = 0
    for rec in doc["files"]:
        path = mount / rec["file"]
        if not path.is_file():
            print(f"  MISSING  {rec['file']}")
            bad += 1
            continue
        got = sha256(path)
        ok = got == rec["sha256"]
        bad += not ok
        print(f"  {'ok      ' if ok else 'MISMATCH'} {rec['file']:14s} "
              f"{got[:16]}  [{rec.get('provenance','')}]")
    print(f"\n  deployed {doc.get('deployed','?')}"
          + (f" from {doc['build_output']}" if doc.get("build_output") else ""))
    print("  ALL GREEN" if not bad else f"  {bad} PROBLEM(S)")
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("flash", help="write a base OS image to a removable device")
    f.add_argument("--image", required=True, help="decompressed .img")
    f.add_argument("--device", required=True, help="whole disk, e.g. /dev/sdX")
    f.add_argument("--confirm", default=None,
                   help="repeat --device here to actually write; omit for a dry run")
    f.add_argument("--allow-nonremovable", action="store_true",
                   help="override the removable-device guard (some internal readers)")
    f.add_argument("--size-ceiling-gib", type=float, default=SIZE_CEILING_GIB)
    f.set_defaults(func=cmd_flash)

    d = sub.add_parser("deploy", help="place boot artifacts on the FAT32 BOOT partition")
    d.add_argument("--boot-mount", required=True)
    d.add_argument("--from-build-output",
                   help="a socks build-output.json; its deploy/ set is mapped by kind")
    d.add_argument("--set", action="append", metavar="SRC:DEST",
                   help="explicit source:destination pair (repeatable)")
    d.add_argument("--map", action="append", metavar="KIND=DEST",
                   help="override the artifact-kind -> boot filename map (repeatable)")
    d.add_argument("--uenv-ip", metavar="IPSPEC",
                   help="set a static IP via the kernel ip= cmdline in uEnv.txt, "
                        "e.g. 192.168.0.200:::255.255.255.0:board:eth0:off")
    d.add_argument("--partial", action="store_true",
                   help="allow an incomplete boot set (updating one file)")
    d.add_argument("--write", action="store_true", help="apply; default is a dry run")
    d.set_defaults(func=cmd_deploy)

    v = sub.add_parser("verify", help="re-check a card against its deployed manifest")
    v.add_argument("--boot-mount", required=True)
    v.set_defaults(func=cmd_verify)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
