# AD9986 on ZCU102 + ADI Kuiper Linux (SD boot) — reference

ADI AD9986-FMCB-EBZ on a **ZCU102 Rev1.0**, running **ADI Kuiper Linux** booted
from an **SD card**. This is a *flash-a-prebuilt-image* workflow, **not** a build:
you do not compile an FSBL or run `bootgen` here — the FSBL/PMUFW/ATF/U-Boot are
already inside ADI's prebuilt `BOOT.BIN`, and you deploy that file unchanged.
(Contrast the AD9081 no-OS path, where you *do* build `fsbl.elf` and `bootgen`
it into a QSPI image — see `ad9081-zcu102-noos.md`. Don't blur the two: different
boot device, different FSBL provenance, different SW6 setting.)

- **Board:** ZCU102 **Rev1.0**
- **Mezzanine:** AD9986-FMCB-EBZ-W3 on **FMC HPC0**, 122.88 MHz vcxo
- **OS image:** ADI Kuiper Linux **2022_R2 Patch1** (`image_2024-04-04-ADI-Kuiper-full.zip`)
- **Boot device:** SD card (FAT32 BOOT partition + ext4 rootfs)
- **Login:** `root` / `analog`; `iiod` runs by default (libiio network backend, TCP 30431)

## Boot-mode switches — SD boot (this is the hard-won one)

> **Set SW6 = `1-ON, 2-OFF, 3-OFF, 4-OFF` for SD boot on this Rev1.0 board.**
> Convention on ZCU102 SW6: switch **up = ON**, **down = OFF**.

This was confirmed empirically over **~3 wasted boot attempts**. The verification
signal is the U-Boot banner line:

```
Bootmode: LVL_SHFT_SD_MODE1
```

which is PS_MODE `0001`. With this setting the board loads the **2022.2 FSBL**
(`Release 2022.2  Feb 7 2024`) that you actually deployed on the SD card.

**Xilinx UG1182 Table 13-1 is WRONG for this board rev.** It prescribes the
opposite polarity for SD boot — `SW6.1=OFF, SW6.2/3/4=ON` (mode `1110`,
"SD1-LS"). With that setting the BootROM loaded a **stale 2018.3 FSBL** from an
alternate device (onboard QSPI/eMMC) and hung at the misleading message:

```
PMU-FW is not running
```

The tell that you're on the wrong setting: the FSBL banner reads
`Release 2018.3  Nov 8 2018` instead of `Release 2022.2`. That proves the board
is booting *something other than your SD card*. ADI's AD9081 quickstart wiki gives
the correct setting in `OFF,OFF,OFF,ON` notation (read SW6.1→SW6.4) — which is
the same physical config as `SW6.1=ON, 2-4=OFF` once you account for its notation.
Trust the empirical `LVL_SHFT_SD_MODE1` echo, not the Xilinx table, for Rev1.0.

| SW6 (this board, SD boot) | 1 | 2 | 3 | 4 |
|---|---|---|---|---|
| **Correct (works)** | **ON** | OFF | OFF | OFF |
| UG1182 "SD1-LS" (boots stale FSBL, hangs) | OFF | ON | ON | ON |

> For **QSPI boot** you'd use a different SW6 pattern entirely — see the AD9081
> no-OS reference (`QSPI32 = ON OFF ON ON`). This Kuiper board boots from SD; QSPI
> boot is not part of this flow.

## Prepare the SD card

### 1. Get the right image — and *only* the right one

Use **ADI Kuiper Linux 2022_R2 Patch1**:

```
https://swdownloads.analog.com/cse/kuiper/image_2024-04-04-ADI-Kuiper-full.zip
  → 2024-04-04-ADI-Kuiper-full.img   (~11.5 GiB, kernel 5.15, GitHub tag 2022_r2_p1)
```

**Do NOT use any 2023_R2 release.** ADI's own 2023_R2 release notes list
*"AD9081 with 122.88 MHz oscillator doesn't boot … due to a SPI issue. Please use
a previous Kuiper Linux release until we will publish a fix."* 2022_R2 Patch1 is
the known-good release for vcxo122p88; it locked first try.

### 2. Flash the .img to the card

ADI doesn't publish a `.bmap` sidecar, so `bmaptool` needs `--nobmap`:

```bash
sudo bmaptool copy --nobmap 2024-04-04-ADI-Kuiper-full.img /dev/sdX
# or:
sudo dd if=2024-04-04-ADI-Kuiper-full.img of=/dev/sdX bs=4M status=progress conv=fsync
```

(`/dev/sdX` = the SD card block device — verify with `lsblk` first; getting this
wrong overwrites the wrong disk.)

### 3. Deploy the per-board boot files (the BOOT partition is a multi-board bundle)

The freshly flashed FAT32 BOOT partition is **not** ready to boot — ADI ships a
multi-board bundle with artifacts in per-board subdirectories, and the
`Image`/`BOOT.BIN`/`system.dtb` must be copied to the **root** of the BOOT
partition (ADI's "Kuiper Imager" normally does this; it's just three `cp`s).

For this board/variant, copy to the BOOT FAT32 root:

| Source in bundle | → BOOT root |
|---|---|
| `zynqmp-zcu102-rev10-ad9081/m8_l4/BOOT.BIN` | `BOOT.BIN` |
| `zynqmp-zcu102-rev10-ad9081/m8_l4/m8_l4_vcxo122p88/system.dtb` | `system.dtb` |
| `zynqmp-common/Image` | `Image` |

`BOOT.BIN` (~15.2 MiB) contains FSBL + PMUFW + BL31 + bitstream + U-Boot — that's
the FSBL you depend on; you never build it yourself on this path. The
`deploy_boot.sh` helper automates the three copies.

### 4. Static IP (optional) via uEnv.txt

The DTB does not carry an IP. Set a static IP through the kernel `ip=` cmdline by
appending to `uEnv.txt` on the BOOT root:

```
ip=192.168.0.200:::255.255.255.0:analog:eth0:off
```

## Pre-flight gates (all hard-won; skipping any wastes a boot)

Power-on is gated on these four — a green check on each before applying 12 V:

1. **FMC HPC0 supply ON *before* ZCU102 PS power.** If the FMC supply is dead at
   PS power-on, the AD9986 SPI MISO floats high (`0xFF`); Linux boots fine but the
   board is "alive but blind" — driver readbacks are all `ff`. Verify after boot:
   `dmesg | grep -E "(hmc7044|ad9081)" | grep -v "Read/Write check failed|readback is ff"` is empty.
2. **SW6 = 1-ON / 2-4-OFF** (see above). Verify: U-Boot prints `LVL_SHFT_SD_MODE1`.
3. **SD write-protect tab UNLOCKED.** A locked tab makes rootfs mount read-only →
   6 sequential mount panics. Verify: `dmesg | grep "mmcblk0:" | grep -v "(ro)"`
   shows a line.
4. **BOOT FAT32 root has `BOOT.BIN` + `system.dtb` + `Image`** (step 3 above).

Also: **never seat the FMC carrier with 12 V live** — power off first.

## Boot — what a good boot looks like

Start a UART logger *before* power-on to catch the banner from t=0 (CP2108 quad
bridge enumerates as `/dev/ttyUSB1..4`; PS UART is typically `/dev/ttyUSB1`,
115200 8N1). A healthy boot shows this stage chain:

| Stage | Expected evidence |
|---|---|
| FSBL | `Release 2022.2  Feb 7 2024` (NOT 2018.3 — that means wrong SW6) |
| PMUFW | `PMUFW: v1.1` |
| ATF / BL31 | `v2.8(release):xilinx-v2023.1` |
| U-Boot | `2018.01-…` + `Bootmode: LVL_SHFT_SD_MODE1` |
| Kernel | `Linux 5.15.36-…`, `Machine model: ZynqMP ZCU102 Rev1.0` |
| Clocks | `hmc7044 … PLL1: Locked, CLKIN0 @ 122880000 Hz … PLL2: Locked @ 3000 MHz` |
| Device ID | `ad9081 spi1.0: Expected AD9081 found AD9986` |

## Symptoms → cause

| Symptom | Cause |
|---|---|
| Hangs at `PMU-FW is not running`; FSBL banner says `Release 2018.3` | Wrong SW6 (UG1182 setting) — booting a stale onboard FSBL, not your SD. Set SW6 = 1-ON/2-4-OFF |
| Linux boots but AD9986 readbacks are all `0xFF` | FMC HPC0 supply was off at PS power-on — power FMC first, then the board |
| Repeated rootfs mount panics | SD physical write-protect tab is locked |
| Board doesn't boot at all (no FSBL banner) after SW6 change | On Xilinx silicon `1-ON/2-4-OFF` reads as QSPI24 (mode 0001); if it finds nothing in QSPI it's silent — confirm the SD card is seated and deployed |
| `AD9081 with 122.88 MHz oscillator doesn't boot` (known ADI issue) | You're on a 2023_R2 image — use 2022_R2 Patch1 |
