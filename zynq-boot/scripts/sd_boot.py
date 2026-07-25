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


def partitions_of(disk_name: str) -> set[str]:
    """Kernel-authoritative partition list for a disk.

    Do NOT do this by string prefix: `loop10` starts with `loop1` but is a
    different DISK, and `sda10` starts with `sda1` but is a sibling partition.
    /sys/block/<disk>/<part>/partition exists exactly for real partitions, so
    ask rather than guess."""
    names = {disk_name}
    base = Path("/sys/block") / disk_name
    try:
        for child in base.iterdir():
            if (child / "partition").is_file():
                names.add(child.name)
    except OSError:
        pass
    return names


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


def looks_like_disk_image(image: Path) -> str | None:
    """Cheap sanity check that `image` is a whole-disk image, not some other
    file. Returns a description, or None if nothing recognisable is there.

    Writing a non-image raw to a card produces a card that flashes 'fine' and
    then does not boot, with no clue why -- so it is worth one read of 512 B."""
    try:
        with image.open("rb") as fh:
            sector = fh.read(512)
            fh.seek(512)
            gpt = fh.read(8)
    except OSError:
        return None
    if gpt == b"EFI PART":
        return "GPT"
    if len(sector) == 512 and sector[510:512] == b"\x55\xaa":
        return "MBR/DOS partition table"
    return None


def show_target_contents(device: str) -> None:
    """Print what is on the device we are about to destroy.

    Model and size alone do not tell you whether this is last week's good card
    or your photo backup. The partition table does."""
    print("\n  current contents of the target (ALL OF THIS WILL BE ERASED):")
    try:
        out = subprocess.run(
            ["lsblk", "-o", "NAME,SIZE,FSTYPE,LABEL,MOUNTPOINT", device],
            capture_output=True, text=True, timeout=10)
        body = (out.stdout or "").rstrip()
        print("\n".join(f"      {ln}" for ln in body.splitlines()) if body
              else "      (lsblk returned nothing)")
    except (OSError, subprocess.SubprocessError):
        print("      (could not read the partition table)")


def guard_flash_target(device: str, allow_nonremovable: bool,
                       size_ceiling_gib: float) -> dict:
    facts = block_device_facts(device)
    mounts = mounted_sources()
    own = partitions_of(facts["name"])
    for critical in ("/", "/home", "/boot"):
        src = mounts.get(critical)
        if src and os.path.basename(os.path.realpath(src)) in own:
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
            if os.path.basename(os.path.realpath(src)) in own]
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

    # CAPACITY. Writing more bytes than the card holds truncates silently: the
    # write "succeeds", the tail is missing, and you find out when the rootfs
    # will not mount. Check before touching anything.
    image_bytes = image.stat().st_size
    if image_bytes > facts["size_bytes"]:
        die(f"{image.name} is {image_bytes / (1<<30):.2f} GiB but {args.device} "
            f"holds only {facts['size_gib']:.2f} GiB -- the write would be "
            f"truncated and the card would look flashed but not boot.")
    headroom = facts["size_bytes"] - image_bytes
    print(f"  capacity: fits, {headroom / (1<<30):.2f} GiB spare")

    kind = looks_like_disk_image(image)
    if kind is None and not args.skip_image_check:
        die(f"{image.name} has no MBR or GPT partition table in its first "
            f"sector, so it does not look like a whole-disk image. Writing it "
            f"raw yields a card that flashes cleanly and never boots. Override "
            f"with --skip-image-check if you know it is a bare filesystem.")
    print(f"  image is: {kind or 'UNRECOGNISED (check skipped)'}")

    show_target_contents(args.device)

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
    # Drop the page cache, or the readback below re-reads what we just wrote
    # from RAM and every card passes -- including the failing one.
    subprocess.run(["sudo", "sh", "-c", "echo 3 > /proc/sys/vm/drop_caches"],
                   capture_output=True)

    if args.verify == "none":
        print("  written. VERIFY SKIPPED (--verify none) -- a bad card, a "
              "truncated write or a mid-write dropout would look identical to "
              "success from here.")
        return 0

    span = image_bytes if args.verify == "full" else min(image_bytes, 256 << 20)
    print(f"  verifying {span / (1<<20):.0f} MiB read back from the card"
          f"{' (whole image)' if args.verify == 'full' else ' (--verify full for all)'}...")
    if not verify_written(image, args.device, span):
        die(f"READBACK MISMATCH -- what is on {args.device} is not what was "
            f"written. Do not boot this card. Usual causes: a failing or "
            f"counterfeit card, a flaky reader/hub, or the card removed early.")
    print(f"  readback OK over {span / (1<<20):.0f} MiB")
    print("  done. Re-seat the card so the new partitions are re-read, then "
          "run `deploy`.")
    return 0


def verify_written(image: Path, device: str, span: int) -> bool:
    """Compare `span` bytes of the image against the same bytes read back off
    the device. Reading a block device needs root, hence the sudo helper."""
    want = hashlib.sha256()
    with image.open("rb") as fh:
        left = span
        while left:
            chunk = fh.read(min(1 << 20, left))
            if not chunk:
                break
            want.update(chunk)
            left -= len(chunk)
    proc = subprocess.Popen(["sudo", "head", "-c", str(span), device],
                            stdout=subprocess.PIPE)
    got = hashlib.sha256()
    read = 0
    while True:
        chunk = proc.stdout.read(1 << 20)
        if not chunk:
            break
        got.update(chunk)
        read += len(chunk)
    proc.wait()
    if read != span:
        print(f"  read back only {read} of {span} bytes")
        return False
    return want.hexdigest() == got.hexdigest()


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

    # FREE SPACE. A copy that runs out of room part-way leaves a TRUNCATED
    # Image or BOOT.BIN on a card that otherwise looks deployed -- the worst
    # possible outcome, because everything reports success.
    need = sum(s.stat().st_size for s, _d, _p in pairs)
    replaced = sum((mount / d).stat().st_size
                   for _s, d, _p in pairs if (mount / d).is_file())
    stat = os.statvfs(mount)
    free = stat.f_bavail * stat.f_frsize
    print(f"  space:   need {need / (1<<20):.0f} MiB, "
          f"{free / (1<<20):.0f} MiB free "
          f"(+{replaced / (1<<20):.0f} MiB reclaimed from files being replaced)")
    if need > free + replaced:
        die(f"not enough room on {mount}: need {need / (1<<20):.0f} MiB, have "
            f"{(free + replaced) / (1<<20):.0f} MiB. A partial copy leaves a "
            f"truncated file on a card that looks deployed.")

    # WHAT IS BEING DESTROYED. Overwriting the good card by muscle memory is
    # the realistic failure here, so say what is already there first.
    prior = mount / MANIFEST_NAME
    if prior.is_file():
        try:
            pdoc = json.loads(prior.read_text())
            print(f"  ALREADY ON THIS CARD: deployed {pdoc.get('deployed','?')}"
                  + (f" from {pdoc['build_output']}" if pdoc.get("build_output") else ""))
        except (OSError, json.JSONDecodeError):
            pass
    for src, dest, prov in pairs:
        target = mount / dest
        if target.is_file():
            old = sha256(target)
            new = sha256(src)
            state = "unchanged" if old == new else f"REPLACES {old[:16]}"
            print(f"  deploy:  {src.name:38s} -> {dest:14s} [{state}]")
        else:
            print(f"  deploy:  {src.name:38s} -> {dest:14s} [new file]")
        print(f"           {prov}")

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
    f.add_argument("--verify", choices=("quick", "full", "none"), default="quick",
                   help="read back and compare after writing: quick = first "
                        "256 MiB (partition table + boot partition, where a bad "
                        "card usually shows), full = the whole image, none = "
                        "trust the write (not advised)")
    f.add_argument("--skip-image-check", action="store_true",
                   help="allow an image with no MBR/GPT in its first sector")
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
