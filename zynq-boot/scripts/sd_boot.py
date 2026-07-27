#!/usr/bin/env python3
"""sd_boot.py -- prepare a bootable SD card for a Zynq / ZynqMP board.

Board-agnostic. Five subcommands, in the order you normally need them:

  flash             write a base OS image (.img/.img.xz/.zip) to a removable device
  deploy            place the per-board boot artifacts on the FAT32 BOOT partition
  verify            re-check a card against the manifest deploy wrote on it
  provision-rootfs  place the recipe's declared capture/serving stack on the
                    mounted ext4 ROOTFS partition (the half deploy cannot do)
  verify-rootfs     re-check a rootfs against the manifest provisioning wrote

`deploy` is the one you run most: a freshly flashed vendor image is usually
NOT bootable for your board yet, because vendors ship a multi-board bundle
with artifacts in per-board subdirectories and the boot loader only reads the
partition root. Deploying is a handful of copies plus a rename -- easy to get
subtly wrong, and each mistake costs a boot cycle.

`provision-rootfs` exists because a flashed + deployed card BOOTS but does
nothing useful: the entire capture/serving stack lives on the rootfs, and
restoring it by hand over SSH is dozens of copies, modes, and systemd enables
-- each one rediscovered the hard way when a card dies. The recipe's
stages.linux.rootfs_provision block declares the set once; this subcommand
applies it offline and writes /root/provision-manifest.json binding the
rootfs to the exact image it was provisioned for (the R5 firmware and kernel
modules are per-image artifacts -- a stale pair booting against a new
bitstream is the failure the manifest exists to prevent).

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

  # Provision the capture stack onto the mounted ext4 rootfs partition:
  sd_boot.py provision-rootfs --rootfs-mount /run/media/<user>/rootfs \\
      --from-build-output systems/builds/<run-label>/build-output.json \\
      --external sdrangel-plan06-bundle=<path to banked bundle> \\
      --boot-mount /run/media/<user>/BOOT --write

  # An image whose artifacts come from more than one build -- a full bundle
  # plus a later DT-only rebuild. --from-build-output is REPEATABLE on both
  # deploy and provision-rootfs; later wins per artifact kind, so this card
  # gets the full set with the newer DTB substituted:
  sd_boot.py deploy --boot-mount <mnt> \\
      --from-build-output systems/builds/<full-run>/build-output.json \\
      --from-build-output systems/builds/<dt-run>/build-output.json --write

  sd_boot.py verify-rootfs --rootfs-mount <mnt>

Part of the zynq-boot skill. Board-specific facts -- boot-mode switches, which
base image, what a good boot looks like -- live in references/<board>.md; this
script deliberately knows none of them.
"""

from __future__ import annotations

import argparse
import fnmatch
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

def load_build_outputs(entries) -> tuple[dict, dict, Path, list]:
    """Merge one or more build-output.json bundles into one artifact set.

    -> (by_kind, primary_doc, primary_path, sources)
       by_kind[kind] = {"art":…, "deploy_dir":…, "label":…, "bundle":…}

    LATER bundles override EARLIER ones per artifact kind, so the last one
    named wins for anything it carries and everything else falls through.

    WHY MORE THAN ONE. A board's real image is not always one bundle. A
    DT-only rebuild (`socks_build.py --execute --stage dt`) banks a SUBSET --
    just the device-tree-blob -- while the kernel, BOOT.BIN, bitstream and R5
    ELF it boots beside are byte-identical to an earlier full bundle. Naming
    both is how you describe THAT image. The alternative is what this exists
    to prevent: provisioning a rootfs from a bundle whose DTB the card has
    never booted, binding it to the wrong image, and finding out hours later
    at R5 load or at a band that is silently off frequency.

    All bundles must declare the SAME recipe. The recipe's sd_deploy /
    rootfs_provision block is the contract for what a card needs; two
    different contracts cannot both apply, and quietly preferring one is how
    a half-described card gets built."""
    if isinstance(entries, (str, Path)):
        # A bare string is iterable, so without this it would silently walk the
        # path CHARACTER BY CHARACTER and report a pile of missing files. Any
        # caller that has not been updated for the repeatable flag lands here.
        die("load_build_outputs expects a list of build-output paths, not one "
            f"string ({entries!r}) -- --from-build-output is repeatable now")
    by_kind, sources = {}, []
    primary_doc = primary_path = primary_recipe = None
    for entry in entries:
        doc_path = Path(entry).expanduser()
        try:
            doc = json.loads(doc_path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            die(f"cannot read {doc_path}: {exc}")
        recipe_rel = doc.get("recipe_path")
        if primary_recipe is None:
            primary_recipe = recipe_rel
        elif recipe_rel != primary_recipe:
            die(f"bundles declare different recipes: {primary_recipe!r} vs "
                f"{recipe_rel!r} ({doc_path}). The recipe is the contract for "
                f"what this card needs -- two of them cannot both apply.")
        label = doc.get("run_label", doc_path.parent.name)
        deploy_dir = doc_path.parent / "deploy"
        sources.append({"run_label": label, "build_output": str(doc_path),
                        "kinds": sorted(a.get("kind") for a in doc.get("deploy", []))})
        for art in doc.get("deploy", []):
            kind = art.get("kind")
            prior = by_kind.get(kind)
            if prior is not None and prior["label"] != label:
                print(f"  override: {kind} now from {label} "
                      f"({art['sha256'][:16]}), was {prior['label']} "
                      f"({prior['art']['sha256'][:16]})")
            by_kind[kind] = {"art": art, "deploy_dir": deploy_dir,
                             "label": label, "bundle": str(doc_path)}
        primary_doc, primary_path = doc, doc_path
    if len(sources) > 1:
        print(f"  bundles: {len(sources)} build-outputs merged, later wins per "
              f"kind -- " + ", ".join(s["run_label"] for s in sources))
    return by_kind, primary_doc, primary_path, sources


def artifact_source(by_kind, kind):
    """-> (path, provenance) for one merged artifact kind, or (None, None)."""
    hit = by_kind.get(kind)
    if hit is None:
        return None, None
    return (hit["deploy_dir"] / hit["art"]["file"],
            f"{hit['label']}::{kind} sha256 {hit['art']['sha256']}")


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
        by_kind, doc, doc_path, _sources = load_build_outputs(args.from_build_output)

        # The recipe is the authority on what a bootable card needs. Prefer its
        # declared stages.linux.sd_deploy over the built-in kind map: the map
        # only knows about build artifacts, and the set that actually boots
        # includes profile files too (uEnv.txt, whose omission strands the
        # board with no static IP). Falling back to the map keeps the script
        # useful for boards with no recipe at all.
        declared = recipe_stage_block(doc, doc_path, "sd_deploy")
        if declared:
            profile_home = declared["profile_home"]
            print(f"  recipe:  {declared['recipe_rel']} declares "
                  f"{len(declared['files'])} file(s) for the boot partition")
            for entry in declared["files"]:
                frm, dest = entry["from"], entry["to"]
                if frm.startswith("artifact:"):
                    kind = frm.split(":", 1)[1]
                    src, prov = artifact_source(by_kind, kind)
                    if src is None:
                        die(f"recipe sd_deploy wants artifact kind {kind!r}, which "
                            f"no named bundle produced. Build with --stage all, "
                            f"or name the bundle that carries it with another "
                            f"--from-build-output.")
                    pairs.append((src, dest, prov))
                else:
                    pairs.append((profile_home / frm, dest,
                                  f"profile {frm}"))
            for kind, hit in by_kind.items():
                if not any(e["from"] == f"artifact:{kind}" for e in declared["files"]):
                    why = ROOTFS_KINDS.get(kind, "not in the recipe's boot set")
                    print(f"  skip:    {hit['art']['file']:38s} ({kind}) -- {why}")
            if declared.get("rootfs_note"):
                print(f"\n  ROOTFS (NOT handled here): {declared['rootfs_note']}\n")
            return pairs

        skipped = []
        for kind, hit in by_kind.items():
            fname = hit["art"]["file"]
            dest = kind_map.get(kind)
            if dest is None:
                skipped.append((fname, kind))
                continue
            # Carry the recorded sha so a corrupted bank is caught before the
            # card is touched, not after a failed boot.
            src, prov = artifact_source(by_kind, kind)
            pairs.append((src, dest, prov))
        for fname, kind in skipped:
            why = ROOTFS_KINDS.get(kind, "no boot-partition destination for this kind")
            print(f"  skip:    {fname:38s} ({kind}) -- {why}")
    return pairs


def recipe_stage_block(build_output: dict, doc_path: Path,
                       block_name: str) -> dict | None:
    """Resolve stages.linux.<block_name> from the recipe this run was built
    from. Serves both sd_deploy (boot partition) and rootfs_provision.

    build-output.json records recipe_path relative to the repo root, and the
    bundle sits at <root>/systems/builds/<label>/, so the root is three levels
    up. Returns None when there is no recipe or no declared set -- the
    sd_deploy caller then falls back to the built-in kind map."""
    rel = build_output.get("recipe_path")
    if not rel:
        return None
    root = doc_path.resolve().parent.parent.parent.parent
    recipe_path = root / rel
    if not recipe_path.is_file():
        print(f"  note: recipe {rel} not found beside this bundle -- using the "
              f"built-in kind map" if block_name == "sd_deploy" else
              f"  note: recipe {rel} not found beside this bundle -- no "
              f"declared {block_name} set is available")
        return None
    try:
        recipe = json.loads(recipe_path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    block = ((recipe.get("stages") or {}).get("linux") or {}).get(block_name)
    if not block or not block.get("files"):
        return None
    return {"files": block["files"], "profile_home": recipe_path.parent,
            "recipe_rel": rel, "rootfs_note": block.get("rootfs_note"),
            "label": block.get("boot_partition_label"),
            "repo_root": root, "block": block}


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
        # A LIST since --from-build-output became repeatable; the first entry
        # stays under the old singular key so `verify` and every existing
        # reader keep working, and the full ordered set is recorded beside it.
        manifest["build_output"] = str(args.from_build_output[0])
        if len(args.from_build_output) > 1:
            manifest["build_outputs"] = [str(p) for p in args.from_build_output]
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


# -------------------------------------------------------- provision-rootfs

# The provisioning manifest lives inside the provisioned rootfs itself, so
# the board (gps-live-ctl preconditions) and a later verify-rootfs both read
# the same record of what was placed and for WHICH image.
ROOTFS_MANIFEST_REL = "root/provision-manifest.json"

# What binds a provisioned rootfs to ITS image. The R5 firmware and kernel
# modules are per-image artifacts; a stale pair booting against a new
# bitstream is exactly the failure the boot_identity record exists to catch.
BOOT_IDENTITY_KINDS = {
    "boot-image": "BOOT.BIN",
    "kernel-image": "Image",
    "device-tree-blob": "system.dtb",
}


def guard_rootfs_root(unsafe_no_mount_check: bool) -> None:
    """A real card's rootfs preserves root ownership: /root is 0700, and the
    destinations (/lib/firmware, /usr/local/sbin, /etc/systemd) are
    root-owned. A non-root run cannot even stat inside /root -- the first
    physical-card run died on exactly that -- and files it could write would
    land owned by the host user instead of root. Desk tests provision into a
    plain directory and skip this with --unsafe-no-mount-check."""
    if unsafe_no_mount_check:
        return
    if os.geteuid() != 0:
        die("provisioning a real rootfs reads and writes root-owned paths "
            "(/root is 0700; written files must be root-owned on the card) "
            "-- re-run under sudo")


def guard_rootfs_mount(mount: Path, unsafe_no_mount_check: bool) -> None:
    if not mount.is_dir():
        die(f"rootfs mount is not a directory: {mount}")
    resolved = str(mount.resolve())
    for critical in ("/", "/home", "/usr", "/etc", "/boot"):
        if resolved == critical:
            die(f"refusing to provision into {critical} -- that is this "
                f"host's own filesystem, not a card's rootfs")
    if unsafe_no_mount_check:
        print("  UNSAFE: mount-point and fstype guards skipped "
              "(--unsafe-no-mount-check exists for desk tests against a "
              "plain directory -- never use it with a real card)")
    else:
        mounts = mounted_sources()
        if resolved not in mounts:
            die(f"{mount} is not a mount point. Mount the card's ext4 rootfs "
                f"partition and pass that path -- writing into an unmounted "
                f"directory silently fills your own disk instead of the card.")
        fstype = ""
        try:
            for line in Path("/proc/self/mounts").read_text().splitlines():
                p = line.split()
                if len(p) >= 3 and p[1] == resolved:
                    fstype = p[2]
        except OSError:
            pass
        if fstype in ("vfat", "msdos", "exfat"):
            die(f"{mount} is {fstype} -- that is the FAT BOOT partition, not "
                f"the rootfs. The capture stack lives on the ext4 rootfs; "
                f"boot artifacts go through `deploy` instead.")
        if fstype and fstype not in ("ext2", "ext3", "ext4"):
            die(f"{mount} is {fstype}, not ext2/3/4 -- this does not look "
                f"like a Linux rootfs partition.")
    # A real rootfs has etc/ and lib/. An empty or wrong partition does not,
    # and provisioning into it builds a convincing file tree no boot reads.
    for probe in ("etc", "lib"):
        if not (mount / probe).is_dir():
            die(f"{mount} has no {probe}/ -- this does not look like an "
                f"extracted Linux rootfs. Wrong partition?")


def parse_externals(entries) -> dict:
    out = {}
    for e in entries or []:
        if "=" not in e:
            die(f"--external expects NAME=PATH, got {e!r}")
        name, path = e.split("=", 1)
        out[name] = Path(path).expanduser()
    return out


def resolve_provision_source(frm: str, declared: dict, by_kind: dict,
                             deploy_dir: Path, label: str,
                             externals: dict) -> tuple[Path, str]:
    """-> (source_path, provenance). Schemes mirror sd_deploy's artifact:/
    profile-relative pair, plus repo: (board-level files shared across
    profiles) and external: (banked outside the repo, mapped on the CLI)."""
    if frm.startswith("artifact:"):
        kind = frm.split(":", 1)[1]
        src, prov = artifact_source(by_kind, kind)
        if src is None:
            die(f"recipe rootfs_provision wants artifact kind {kind!r}, which "
                f"no named bundle produced. Build with --stage all, or name "
                f"the bundle that carries it with another --from-build-output.")
        return src, prov
    if frm.startswith("repo:"):
        rel = frm.split(":", 1)[1]
        return declared["repo_root"] / rel, f"repo {rel}"
    if frm.startswith("external:"):
        name = frm.split(":", 1)[1]
        path = externals.get(name)
        if path is None:
            die(f"recipe rootfs_provision needs external source {name!r}, "
                f"which is banked OUTSIDE the repo. Pass "
                f"--external {name}=<path> to say where it lives on this host.")
        return path, f"external {name}"
    return declared["profile_home"] / frm, f"profile {frm}"


def walk_tree(src_dir: Path, excludes: list) -> list:
    """Relative paths of every file under src_dir, honoring excludes
    (fnmatch on the bare name; matching directories are pruned whole)."""
    out = []
    for base, dirs, files in os.walk(src_dir):
        dirs[:] = sorted(d for d in dirs
                         if not any(fnmatch.fnmatch(d, pat) for pat in excludes))
        for f in sorted(files):
            if any(fnmatch.fnmatch(f, pat) for pat in excludes):
                continue
            out.append(Path(base, f).relative_to(src_dir))
    return out


def plan_symlinks(block: dict, mount: Path) -> list:
    """-> [(link_rel, target, state)]. Dies on a non-symlink collision now,
    in dry run, rather than half-way through a write."""
    plans = []
    for entry in block.get("symlinks", []):
        link_rel = entry["symlink"].lstrip("/")
        target = entry["target"]
        link = mount / link_rel
        if link.is_symlink():
            old = os.readlink(link)
            state = "unchanged" if old == target else f"REPLACES -> {old}"
        elif link.exists():
            die(f"/{link_rel} exists and is NOT a symlink -- refusing to "
                f"replace a real file with a link. Something else owns it.")
        else:
            state = "new symlink"
        plans.append((link_rel, target, state))
    return plans


def check_boot_identity_against_card(boot_mount: Path,
                                     boot_identity: dict) -> None:
    """Provisioning a rootfs against a DIFFERENT card's image is exactly the
    mistake this exists to catch: the pair would boot and then fail at R5
    load or module insmod, hours later and far from the cause."""
    prior = boot_mount / MANIFEST_NAME
    if not prior.is_file():
        print(f"  note:    no {MANIFEST_NAME} on {boot_mount} -- image "
              f"identity cross-check skipped (card not deployed by this "
              f"script?)")
        return
    try:
        pdoc = json.loads(prior.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        die(f"cannot read {prior}: {exc}")
    deployed = {r.get("file"): r.get("sha256") for r in pdoc.get("files", [])}
    for name, want in boot_identity.items():
        got = deployed.get(name)
        if got and got != want:
            die(f"boot-partition identity mismatch: {name} on the card is "
                f"{got[:16]} but this build's is {want[:16]} -- this rootfs "
                f"would be provisioned for a DIFFERENT image than the card "
                f"boots. Deploy this build's boot set first, or point "
                f"--boot-mount at the right card.")
    print(f"  boot id: card's deployed BOOT.BIN/Image/system.dtb match this "
          f"build -- rootfs and image agree")


def cmd_provision_rootfs(args) -> int:
    mount = Path(args.rootfs_mount).expanduser()
    guard_rootfs_mount(mount, args.unsafe_no_mount_check)
    guard_rootfs_root(args.unsafe_no_mount_check)

    by_kind, doc, doc_path, sources = load_build_outputs(args.from_build_output)
    deploy_dir = doc_path.parent / "deploy"
    label = doc.get("run_label", doc_path.parent.name)
    externals = parse_externals(args.external)

    declared = recipe_stage_block(doc, doc_path, "rootfs_provision")
    if declared is None:
        die("this run's recipe declares no stages.linux.rootfs_provision -- "
            "there is nothing authoritative to provision from. The declared "
            "set is the contract; ad-hoc rootfs copies are how the by-hand "
            "restore drifted in the first place.")
    block = declared["block"]
    print(f"  recipe:  {declared['recipe_rel']} declares "
          f"{len(block['files'])} rootfs entries, "
          f"{len(block.get('symlinks', []))} symlink(s)")

    # boot_identity FIRST: if the bundle cannot name its own image, the
    # manifest cannot bind the rootfs to anything and provisioning is moot.
    boot_identity = {}
    for kind, name in BOOT_IDENTITY_KINDS.items():
        hit = by_kind.get(kind)
        if hit is None:
            die(f"no named bundle carries a {kind!r} artifact, so the provision "
                f"manifest cannot bind this rootfs to its image. Provision from "
                f"a full bundle (--stage all), or name additional bundles with "
                f"--from-build-output until all of "
                f"{sorted(BOOT_IDENTITY_KINDS)} are covered -- a stage-scoped "
                f"bundle alone (say a --stage dt DTB) cannot bind a rootfs.")
        boot_identity[name] = hit["art"]["sha256"]

    def resolve(frm):
        return resolve_provision_source(frm, declared, by_kind, deploy_dir,
                                        label, externals)

    # Resolve every declared entry before touching anything: a missing
    # source must abort the whole run, not strand a half-provisioned card.
    copies = []     # (src, dest_rel, mode, provenance)
    dir_plans = []  # (spec, to, n_files, total_bytes, provenance)
    for entry in block["files"]:
        if "from_dir" in entry:
            src_dir, prov = resolve(entry["from_dir"])
            if not src_dir.is_dir():
                die(f"source directory missing: {src_dir}  [{prov}]")
            to_rel = entry["to"].lstrip("/")
            rels = walk_tree(src_dir, entry.get("exclude", []))
            if not rels:
                die(f"source directory is empty after excludes: {src_dir}  "
                    f"[{prov}]")
            total = 0
            for rel in rels:
                src = src_dir / rel
                total += src.stat().st_size
                copies.append((src, str(Path(to_rel) / rel), None, prov))
            dir_plans.append((entry["from_dir"], entry["to"], len(rels),
                              total, prov))
        else:
            src, prov = resolve(entry["from"])
            if not src.is_file():
                die(f"source file missing: {src}  [{prov}]")
            mode = int(entry["mode"], 8) if entry.get("mode") else None
            copies.append((src, entry["to"].lstrip("/"), mode, prov))

    link_plans = plan_symlinks(block, mount)

    # If the boot partition is present, refuse a cross-image pairing BEFORE
    # any write, and preview the config seeding.
    boot_mount = None
    if args.boot_mount:
        boot_mount = Path(args.boot_mount).expanduser()
        if args.unsafe_no_mount_check:
            if not boot_mount.is_dir():
                die(f"boot mount is not a directory: {boot_mount}")
        else:
            guard_boot_mount(boot_mount)
        check_boot_identity_against_card(boot_mount, boot_identity)

    # FREE SPACE, same rationale as deploy: a copy that dies part-way leaves
    # a rootfs that LOOKS provisioned and fails at first chain start.
    need = sum(s.stat().st_size for s, _d, _m, _p in copies)
    replaced = sum((mount / d).stat().st_size
                   for _s, d, _m, _p in copies if (mount / d).is_file())
    stat = os.statvfs(mount)
    free = stat.f_bavail * stat.f_frsize
    print(f"  space:   need {need / (1<<20):.0f} MiB, "
          f"{free / (1<<20):.0f} MiB free "
          f"(+{replaced / (1<<20):.0f} MiB reclaimed from files being replaced)")
    if need > free + replaced:
        die(f"not enough room on {mount}: need {need / (1<<20):.0f} MiB, have "
            f"{(free + replaced) / (1<<20):.0f} MiB. A partial provision "
            f"leaves a rootfs that looks complete and fails at chain start.")

    dir_dests = {to for _spec, to, _n, _b, _p in dir_plans}
    for src, dest_rel, mode, prov in copies:
        # Files inside a tree copy are summarized by their dir line below.
        if any(("/" + dest_rel).startswith(d.rstrip("/") + "/")
               for d in dir_dests):
            continue
        target = mount / dest_rel
        if target.is_file():
            state = ("unchanged" if sha256(target) == sha256(src)
                     else f"REPLACES {sha256(target)[:16]}")
        else:
            state = "new file"
        print(f"  rootfs:  {src.name:38s} -> /{dest_rel} [{state}]"
              + (f" mode {mode:04o}" if mode is not None else ""))
        print(f"           {prov}")
    for spec, to, n, total, prov in dir_plans:
        print(f"  rootfs:  {spec:38s} -> {to} "
              f"[{n} files, {total / (1<<20):.1f} MiB]")
        print(f"           {prov}")
    for link_rel, target, state in link_plans:
        print(f"  symlink: /{link_rel} -> {target} [{state}]")

    seeds = []
    if boot_mount is not None:
        for entry in block.get("boot_config_seed", []):
            src, prov = resolve(entry["from"])
            if not src.is_file():
                die(f"boot_config_seed source missing: {src}  [{prov}]")
            dest = boot_mount / entry["to"]
            if dest.exists():
                # Operator-tuned at runtime; overwriting one silently undoes
                # the tuning -- same only-if-absent rule as the on-board
                # installer.
                print(f"  seed:    {entry['to']} exists -- left untouched")
            else:
                print(f"  seed:    {src.name} -> {entry['to']} [absent -- "
                      f"will seed from example]")
                seeds.append((src, dest))

    if not args.write:
        print("\n  DRY RUN -- nothing copied. Add --write to apply.")
        return 0

    records = []
    for src, dest_rel, mode, prov in copies:
        want = sha256(src)
        target = mount / dest_rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)      # preserves the source mode by default
        if mode is not None:
            os.chmod(target, mode)
        records.append({"path": "/" + dest_rel, "sha256": want,
                        "bytes": src.stat().st_size, "provenance": prov})

    link_records = []
    for link_rel, target, state in link_plans:
        link = mount / link_rel
        link.parent.mkdir(parents=True, exist_ok=True)
        if link.is_symlink() and os.readlink(link) != target:
            print(f"  symlink: /{link_rel} REPLACES old target "
                  f"{os.readlink(link)}")
            link.unlink()
        if not link.is_symlink():
            link.symlink_to(target)
        link_records.append({"path": "/" + link_rel, "target": target})

    for src, dest in seeds:
        shutil.copy2(src, dest)

    # ext4 through a card reader buffers heavily; verify only after a real
    # flush, or you verify the page cache instead of the card.
    os.sync()
    bad = [r for r in records
           if sha256(mount / r["path"].lstrip("/")) != r["sha256"]]
    if bad:
        die("post-copy verify FAILED for: "
            + ", ".join(r["path"] for r in bad)
            + " -- do not boot this rootfs")
    print(f"\n  verified {len(records)} file(s) on the rootfs after sync")

    manifest = {
        "schema": "provisioned-rootfs/1",
        "provisioned": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "run_label": label,
        "build_output": str(doc_path),
        # Every contributing bundle, in the order given, so a later reader can
        # reconstruct exactly which build each artifact came from. The board's
        # image is not always one bundle -- see load_build_outputs().
        "sources": sources,
        "files": records,
        "symlinks": link_records,
        "boot_identity": boot_identity,
        "note": ("Written by the zynq-boot skill's sd_boot.py "
                 "provision-rootfs. Re-check with `sd_boot.py verify-rootfs "
                 "--rootfs-mount <mnt>`. boot_identity names the image this "
                 "rootfs was provisioned FOR; gps-live-ctl refuses bring-up "
                 "when the booted /boot disagrees with it."),
    }
    man_path = mount / ROOTFS_MANIFEST_REL
    man_path.parent.mkdir(parents=True, exist_ok=True)
    man_path.write_text(json.dumps(manifest, indent=2) + "\n")
    os.sync()
    print(f"  manifest: /{ROOTFS_MANIFEST_REL}")
    print("\n  Rootfs is provisioned. First boot compiles the on-board "
          "binaries (gps-live-firstboot.service) before the chain starts.")
    return 0


def cmd_verify_rootfs(args) -> int:
    mount = Path(args.rootfs_mount).expanduser()
    guard_rootfs_root(getattr(args, "unsafe_no_mount_check", False))
    manifest_path = mount / ROOTFS_MANIFEST_REL
    if not manifest_path.is_file():
        die(f"no /{ROOTFS_MANIFEST_REL} on {mount} -- this rootfs was not "
            f"provisioned by this script, so there is nothing to verify "
            f"against.")
    doc = json.loads(manifest_path.read_text())
    bad = ok = 0
    for rec in doc["files"]:
        path = mount / rec["path"].lstrip("/")
        if not path.is_file():
            print(f"  MISSING  {rec['path']}")
            bad += 1
            continue
        got = sha256(path)
        if got != rec["sha256"]:
            print(f"  MISMATCH {rec['path']}  {got[:16]} != "
                  f"{rec['sha256'][:16]}  [{rec.get('provenance', '')}]")
            bad += 1
        else:
            ok += 1
    for rec in doc.get("symlinks", []):
        link = mount / rec["path"].lstrip("/")
        if not link.is_symlink():
            print(f"  MISSING  {rec['path']} (symlink)")
            bad += 1
        elif os.readlink(link) != rec["target"]:
            print(f"  MISLINK  {rec['path']} -> {os.readlink(link)} "
                  f"(want {rec['target']})")
            bad += 1
        else:
            ok += 1
    print(f"\n  provisioned {doc.get('provisioned', '?')} "
          f"from {doc.get('run_label', '?')}")
    print(f"  {ok} ok; " + ("ALL GREEN" if not bad else f"{bad} PROBLEM(S)"))
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
    d.add_argument("--from-build-output", action="append",
                   help="a socks build-output.json; its deploy/ set is mapped by "
                        "kind. REPEATABLE: later bundles override earlier ones "
                        "per artifact kind, which is how you describe an image "
                        "whose artifacts come from more than one build (e.g. a "
                        "full bundle plus a later --stage dt DTB rebuild). Every "
                        "override is printed before anything is written.")
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

    pr = sub.add_parser(
        "provision-rootfs",
        help="place the recipe's declared capture/serving stack on the "
             "mounted ext4 rootfs partition")
    pr.add_argument("--rootfs-mount", required=True,
                    help="the card's mounted ext4 rootfs partition")
    pr.add_argument("--from-build-output", required=True, action="append",
                    help="a socks build-output.json; its recipe's "
                         "stages.linux.rootfs_provision block is the authority. "
                         "REPEATABLE: later bundles override earlier ones per "
                         "artifact kind. The rootfs is bound to BOOT.BIN + Image "
                         "+ system.dtb together, so when those live in different "
                         "builds -- a full bundle plus a later --stage dt DTB -- "
                         "name both, in that order. All bundles must declare the "
                         "same recipe.")
    pr.add_argument("--external", action="append", metavar="NAME=PATH",
                    help="resolve a recipe external:<NAME> source banked "
                         "outside the repo (repeatable)")
    pr.add_argument("--boot-mount",
                    help="also seed the recipe's boot_config_seed files (only "
                         "if absent) and cross-check the card's deployed "
                         "image identity against this build")
    pr.add_argument("--write", action="store_true",
                    help="apply; default is a dry run")
    pr.add_argument("--unsafe-no-mount-check", action="store_true",
                    help="skip the mount-point/fstype guards; exists for desk "
                         "tests against a plain directory, never a real card")
    pr.set_defaults(func=cmd_provision_rootfs)

    vr = sub.add_parser(
        "verify-rootfs",
        help="re-check a rootfs against its provision manifest")
    vr.add_argument("--rootfs-mount", required=True)
    vr.add_argument("--unsafe-no-mount-check", action="store_true",
                    help="skip the root-privilege guard (desk tests against a "
                         "plain directory)")
    vr.set_defaults(func=cmd_verify_rootfs)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
