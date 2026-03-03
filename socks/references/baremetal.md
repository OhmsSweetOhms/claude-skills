# Bare-Metal C Driver Patterns

Read this file before Stage 6 (bare-metal C driver). These patterns were proven in the USART_AXI project and apply to any AXI-Lite memory-mapped peripheral on Zynq-7000.

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

**Interrupt-driven:** Enable IRQ sources via IRQ_EN register, handle in ISR, clear status in ISR.

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
