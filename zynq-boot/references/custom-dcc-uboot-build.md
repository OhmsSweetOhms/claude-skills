# Custom DCC-console U-Boot for JTAG flashing — build reference

**STATUS: drafted from `xlnx_rebase_v2023.01_2023.2` source, NOT yet built or
hardware-verified.** Validate per `references/jtag-flash-validation-plan.md`.

One U-Boot that folds the three things the boot-mode-independent JTAG flow needs:
1. **DCC console** (`CONFIG_ARM_DCC`) → output visibility over JTAG, no UART
   (program_flash's helper works the same way — its only serial driver is `arm_dcc`).
2. **Generic JEDEC bypass** (the patch below) → `sf probe` works on any SFDP-compliant
   flash, not just chips in `spi_nor_ids[]`.
3. **`loadx`/`loady`** (`CONFIG_CMD_LOADB`) → optional in-console transfer (the JTAG
   flow's primary path is `dow -data`, so this is a convenience, not a requirement).

This becomes the `--uboot` for `scripts/jtag_qspi_flash.sh`, replacing the stock
cfgmem helper (which lacks all three).

## Which build target — read this first

There are **two** ways to deploy, with different linking:

| Target | TEXT_BASE | How it's loaded | Use |
|---|---|---|---|
| **DDR-resident** (recommended) | DDR (stock `xilinx_zynq_virt` layout) | our script `dow`s it **after** `ps7_init` brings up DDR | the JTAG flash flow + dashboard |
| OCM drop-in for `program_flash` | `0xFFFC0000`, must fit 256 KB OCM | `program_flash` loads it from `cfgmem/uboot/` | only if you want to fix `program_flash` itself |

**Build the DDR-resident target.** Because *we* control the load (`dow` to any
address after `ps7_init`), there is no OCM size fight and the defconfig delta is small.
The OCM drop-in is a separate, harder build (heavy size trimming to fit OCM) — defer it
unless you specifically need `program_flash` to work unmodified.

## 1. Source + patch

```bash
git clone https://github.com/Xilinx/u-boot-xlnx
cd u-boot-xlnx
git checkout xlnx_rebase_v2023.01_2023.2          # = Vivado/Vitis 2023.2, U-Boot 2023.01
# line offsets in a hand-authored patch drift — apply fuzzily / recount:
git apply --recount ../zynq-boot/patches/0001-spi_nor-generic-any-jedec-fallback.patch
#   or:  patch -p1 < ../zynq-boot/patches/0001-spi_nor-generic-any-jedec-fallback.patch
```

The patch adds `CONFIG_SPI_FLASH_GENERIC_ANY_JEDEC` and makes `spi_nor_read_id()`
return a generic SFDP entry instead of aborting on an unknown ID. **Why the generic
entry sets DUAL/QUAD read flags:** `spi_nor_init_params()` only parses SFDP when the
entry has one of `SPI_NOR_DUAL_READ|QUAD_READ|OCTAL_DTR_READ` set and `SKIP_SFDP`
clear (the `info->flags & (...READ)` guard in spi-nor-core.c). With `flags=0` SFDP is
never read and size stays 0. Confirmed against the tagged source.

## 2. defconfig delta

Append to `configs/xilinx_zynq_virt_defconfig` (or make a copy
`xilinx_zynq_dccflash_defconfig`):

```
# --- DCC flash-helper deltas (on top of xilinx_zynq_virt_defconfig) ---
CONFIG_SPI_FLASH_SFDP_SUPPORT=y        # REQUIRED by the generic bypass (stock cfg omits it)
CONFIG_SPI_FLASH_GENERIC_ANY_JEDEC=y   # the patch's opt-in symbol
CONFIG_ARM_DCC=y                       # DCC serial driver (CP14 on A9), enables JTAG console
CONFIG_CMD_LOADB=y                     # loadb/loadx/loady — optional (dow -data is primary)
```

`CONFIG_SPI_FLASH_SFDP_SUPPORT` is the load-bearing one: without it the generic entry
discovers nothing. The shipped cfgmem helper has SFDP compiled in, so it's compatible
with the Zynq flash stack — the stock *defconfig* just doesn't list it.

## 3. Bind the console to DCC (the part to VERIFY)

`arm_dcc.c` is a DM-serial driver, `U_BOOT_DRIVER(serial_dcc)`, of_match
`compatible = "arm,dcc"`. With DM_SERIAL the console is chosen by the DT
`chosen/stdout-path`. Add a dcc node and point stdout-path at it in the board
`-u-boot.dtsi` (e.g. `arch/arm/dts/zynq-7000-u-boot.dtsi` or your board dtsi):

```dts
/ {
	dcc: dcc {
		compatible = "arm,dcc";
		u-boot,dm-pre-reloc;
	};
	chosen {
		stdout-path = &dcc;
	};
};
```

**This is the one step I could not verify statically** — DM-serial console selection
has version/board quirks. If output doesn't appear on DCC after build, the fallbacks
are: (a) keep this and also remove the UART node from stdout-path; or (b) replicate
the helper exactly by disabling the Zynq UART driver (`# CONFIG_ZYNQ_SERIAL is not set`)
so `arm_dcc` is the only serial and becomes console by default. Settle this on the
first build per the validation plan's Stage 2 (decision tree B).

## 4. Build

```bash
source /tools/Xilinx/Vitis/2023.2/.settings64-Vitis.sh   # for the arm cross-gcc, or set CROSS_COMPILE
export CROSS_COMPILE=arm-none-eabi-                       # (or arm-linux-gnueabihf-)
make xilinx_zynq_virt_defconfig                           # or your copied defconfig
make -j"$(nproc)"
# deliverable: ./u-boot.elf  ->  scripts/jtag_qspi_flash.sh --uboot /abs/u-boot.elf
```

## Caveats (honest list)

- **Console binding (step 3) is unverified** — most likely to need a tweak.
- **SFDP-only:** a flash with no SFDP tables still won't work (generic yields size 0).
  Acceptable: it fails safe rather than writing a wrong geometry. For such a chip, add
  a real `spi_nor_ids[]` entry instead.
- **4-byte addressing (>16 MB):** relies on SFDP + `spi_nor_scan` deriving `addr_width`
  (the generic sets `addr_width=0`). Verify on a large part.
- **`CONFIG_CMD_LOADB` → `loadx`:** confirm it registers `loadx`/`loady` in this tree;
  it's optional anyway (the JTAG flow stages via `dow -data 0x01000000`).
- **DDR target only.** The OCM drop-in-for-`program_flash` variant is a separate build.

## History

- 2026-06-29: drafted from research session 20260629-093358 (binary forensics + tagged
  source). Patch `patches/0001-spi_nor-generic-any-jedec-fallback.patch`. Not built yet.
