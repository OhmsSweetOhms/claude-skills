# Bare-Metal C Driver Patterns

Read this file before Stage 6 (bare-metal C driver). These patterns were proven in the USART_AXI project and apply to any AXI-Lite memory-mapped peripheral on Zynq-7000.

---

## File header conventions

- `Author: OhmsSweetOhms` in the file comment block (never use real names)
- All `#include` paths must be relative (no absolute paths)
- No hardcoded base addresses in source — use `XPAR_*_BASEADDR` macros from `xparameters.h`

---

## Register map header conventions

```c
/* Register offset defines */
#define MOD_REG_CTRL          0x00
#define MOD_REG_STATUS        0x04
#define MOD_REG_CONFIG        0x08

/* Array register access macro */
#define MOD_REG_TX_DATA(i)    (MOD_REG_TX_DATA_BASE + ((i) * 4))
#define MOD_REG_RX_DATA(i)    (MOD_REG_RX_DATA_BASE + ((i) * 4))

/* Bit field macros */
#define MOD_CTRL_TX_EN        (1U << 0)
#define MOD_CTRL_RX_EN        (1U << 1)
#define MOD_STATUS_BUSY       (1U << 0)
#define MOD_STATUS_VALID      (1U << 1)
#define MOD_STATUS_ERROR      (1U << 2)
```

Rules:
- Prefix all defines with a short module identifier (e.g. `USART_`, `DPLL_`)
- Use `(1U << N)` for bit fields, never hex literals for single bits
- Document register access type (RW, RO, W1C) in comments
- Use `0x` hex for offsets, keep consistent 2-digit or 4-digit format

---

## Hardware access abstraction

```c
#ifdef MOD_NO_XIL_BSP
static inline uint32_t mod_reg_read(uintptr_t addr)
{
    return *(volatile uint32_t *)addr;
}
static inline void mod_reg_write(uintptr_t addr, uint32_t val)
{
    *(volatile uint32_t *)addr = val;
}
#else
#include "xil_io.h"
static inline uint32_t mod_reg_read(uintptr_t addr)
{
    return Xil_In32(addr);
}
static inline void mod_reg_write(uintptr_t addr, uint32_t val)
{
    Xil_Out32(addr, val);
}
#endif
```

The `NO_XIL_BSP` fallback allows compilation and unit testing without the Xilinx standalone BSP.

---

## Driver instance struct

```c
typedef struct {
    uintptr_t base_addr;        /**< AXI base address of the IP */
    uint32_t  num_data_words;   /**< Config parameter (from VHDL generic) */
} mod_driver_t;

/* Internal helpers */
static inline uint32_t mod_read(const mod_driver_t *dev, uint32_t offset)
{
    return mod_reg_read(dev->base_addr + offset);
}
static inline void mod_write(const mod_driver_t *dev, uint32_t offset, uint32_t val)
{
    mod_reg_write(dev->base_addr + offset, val);
}
```

---

## W1C (Write-1-to-Clear) status register handling

```c
/* Clear specific status bits by writing 1 to them */
void mod_clear_status(const mod_driver_t *dev, uint32_t mask)
{
    /* Only clear clearable bits; mask off read-only bits */
    mod_write(dev, MOD_REG_STATUS, mask & ~MOD_STATUS_BUSY);
}
```

**Never read-modify-write a W1C register.** Reading returns the current status; writing back those bits clears them. Only write the specific bits you want to clear.

---

## Read-modify-write for control registers

```c
void mod_tx_enable(const mod_driver_t *dev)
{
    uint32_t ctrl = mod_read(dev, MOD_REG_CTRL);
    ctrl |= MOD_CTRL_TX_EN;
    mod_write(dev, MOD_REG_CTRL, ctrl);
}

void mod_tx_disable(const mod_driver_t *dev)
{
    uint32_t ctrl = mod_read(dev, MOD_REG_CTRL);
    ctrl &= ~MOD_CTRL_TX_EN;
    mod_write(dev, MOD_REG_CTRL, ctrl);
}
```

---

## Baud / clock divisor calculation

```c
/* divisor = sys_clk_hz / (baud_rate * oversample) - 1 */
int mod_set_baud(const mod_driver_t *dev, uint32_t sys_clk_hz, uint32_t baud_rate)
{
    if (baud_rate == 0)
        return -1;

    uint32_t divisor = sys_clk_hz / (baud_rate * 16U) - 1U;
    if (divisor > 0xFFFFU)
        return -1;

    mod_write(dev, MOD_REG_BAUD_DIV, divisor & 0xFFFFU);
    return 0;
}
```

---

## DPLL / NCO parameter computation

When the peripheral includes a DPLL for clock recovery, the driver should compute NCO frequency word and loop filter gains at runtime from `sys_clk_hz` and `bit_rate_hz`, rather than using hardcoded defaults.

```c
int mod_set_dpll(const mod_driver_t *dev, uint32_t sys_clk_hz,
                  uint32_t bit_rate_hz)
{
    if (bit_rate_hz == 0 || bit_rate_hz > sys_clk_hz / 2)
        return -1;

    /* freq_word = round(bit_rate_hz * 2^32 / sys_clk_hz) */
    uint64_t num = ((uint64_t)bit_rate_hz << 32) + sys_clk_hz / 2;
    uint32_t freq_word = (uint32_t)(num / sys_clk_hz);

    /* KP = round(0.10 * freq_word / 32768) = round(freq_word / 327680) */
    uint32_t kp = (freq_word + 163840U) / 327680U;
    if (kp == 0) kp = 1;
    if (kp > 0x7FFF) kp = 0x7FFF;

    /* KI = KP / 16, minimum 1 */
    uint32_t ki = kp / 16;
    if (ki == 0) ki = 1;

    mod_write(dev, MOD_REG_DPLL_FREQ_SEL, freq_word);
    mod_write(dev, MOD_REG_DPLL_REF_STEP, freq_word);  /* ref = out for 1:1 */
    mod_write(dev, MOD_REG_DPLL_KP, kp);
    mod_write(dev, MOD_REG_DPLL_KI, ki);
    mod_write(dev, MOD_REG_DPLL_UPDATE, 1);  /* latch + clear integrator */
    return 0;
}
```

The `0.10` gain factor and `KP/16` ratio for KI come from the dpll_v5 gain table — they produce stable lock across 500 kHz to 10 MHz at 100 MHz sys_clk. Keep a `_raw()` variant that takes explicit register values for special cases (asymmetric ref_step, custom loop bandwidth).

When the same formulas are needed in the SV testbench, use DPI-C to call the C implementation rather than re-deriving in SystemVerilog (see `references/xsim.md`, DPI-C section).

---

## Polling vs interrupt-driven reception

**Polling:**
```c
uint32_t mod_wait_rx(const mod_driver_t *dev)
{
    uint32_t status;
    do {
        status = mod_read(dev, MOD_REG_STATUS);
    } while ((status & MOD_STATUS_VALID) == 0);

    mod_write(dev, MOD_REG_STATUS, MOD_STATUS_VALID | MOD_STATUS_ERROR);
    return status;
}
```

### Polling first; interrupt service routines by exception

Prefer a polled main loop. A producer that bumps one free-running counter after its write completes, read by a loop that never sleeps, has no service routine to budget and no nesting to reason about. Use an interrupt only when the hardware state is perishable: it is overwritten before a poll can reach it.

When an interrupt service routine (ISR) is kept, it obeys these rules. Each exists because its absence was measured as missed ticks on a real board.

1. **Acknowledge first.** The first bus operation clears the pending or level condition, before any conditional logic. A spurious entry then costs one transaction, and a late entry does not compound.
2. **No nested interrupts.** An ISR runs to completion with interrupts masked. So its duration gates the next acknowledge: **an ISR longer than the tick period is a missed tick.** Any change that lengthens an ISR is a timing change and is reviewed as one.
3. **Bounded work only.** Every loop on an ISR path has a compile-time or configured bound, stated where the loop is written. An unbounded drain makes lateness feed itself: a late entry finds more queued, takes longer, and lands later still.
4. **A stated time budget, at one unit of load and at full load.** The design carries the ISR's worst-case transaction count and duration at both. **A budget measured at partial load is not a budget.**
5. **Nothing on the tick path is fetched from DDR.** With the MPU and caches off, every constant, table, vtable and buffer touched is a bus transaction. Code, constants and hot state on the tick path live in tightly coupled memory. Where an ISR must reach DDR it writes and never reads back.
6. **Instrumentation observes the loop; it is never inside it.** A capture, log, counter or checksum is never the destination of operational data and never on the operational path. Correctness and timing must not depend on whether instrumentation is armed.
7. **Never read back what the core just wrote.** Compute checksums and derived values from the register or local copy, not by re-reading the destination.
8. **No library or transcendental math on the tick path,** unless its tables are in tightly coupled memory and its cost is inside rule 4's budget.
9. **No assertion, logging or debug machinery on the tick path.** No `xil_printf`, no UART, no `Xil_Assert*`. An assertion an ISR cannot recover from is pure cost; a fault path that halts is the recovery.
10. **Every ISR is measurable from the host.** It publishes an occupancy or duration statistic the host can read. What cannot be measured cannot be budgeted, and rule 4 is unenforceable without it.
11. **The default shape is latch-and-defer.** In the ISR: the acknowledge, and moving perishable hardware state into memory before it is overwritten. Outside it: filters, math, checksums and publishing. A deviation is recorded with its rule-4 budget and its rule-10 measurement.

**Review checklist for an ISR in a handback:** the acknowledge is the first bus operation; every loop names its bound; the budget is stated at full load; nothing on the path lives in DDR; no print or assert; a host-readable duration statistic exists.

---

## Standard function set

Every driver should provide:

| Function | Purpose |
|----------|---------|
| `mod_init()` | Set base_addr, configure baud/sync, disable TX/RX, clear status |
| `mod_tx_enable()` / `mod_tx_disable()` | Read-modify-write CTRL register |
| `mod_rx_enable()` / `mod_rx_disable()` | Read-modify-write CTRL register |
| `mod_load_tx_frame()` | Write array of data words to TX registers |
| `mod_read_rx_frame()` | Read array of data words from RX registers |
| `mod_get_status()` | Return raw STATUS register |
| `mod_clear_status()` | W1C specific bits |
| `mod_wait_rx()` | Poll until valid frame received |
| `mod_irq_enable()` / `mod_irq_disable()` | Read-modify-write IRQ_EN register |

---

## File structure

```
sw/
├── module_name.h     # Register defines, bit fields, struct, prototypes
└── module_name.c     # Implementation with usage example in file header
```

The `.h` file should be self-contained: include `<stdint.h>`, wrap in `extern "C"` for C++ compatibility, and provide the `NO_XIL_BSP` abstraction.
