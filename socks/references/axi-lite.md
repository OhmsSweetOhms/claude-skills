# AXI-Lite Register File Patterns

Read this file before writing AXI-Lite register files for Zynq PS-PL peripherals.

---

## Register Map Convention

AXI-Lite peripherals use a standard 4-byte-stride register layout. The base
address is set by the Zynq block design (typically `0x43C00000` for the first
peripheral).

### Standard registers

| Offset | Name | Access | Description |
|--------|------|--------|-------------|
| 0x00 | CTRL | R/W | Control register (enable bits, soft reset) |
| 0x04 | STATUS | R/W1C | Status register (done, error, overflow flags) |
| 0x08 | TX_DATA | W | Transmit data (FIFO or direct) |
| 0x0C | RX_DATA | R | Receive data (FIFO or direct) |
| 0x10 | IRQ_EN | R/W | Interrupt enable mask |
| 0x14+ | Config | R/W | Peripheral-specific configuration |

### Address stride

All registers are 32-bit aligned with 4-byte stride:
```vhdl
constant ADDR_CTRL    : unsigned(7 downto 0) := x"00";
constant ADDR_STATUS  : unsigned(7 downto 0) := x"04";
constant ADDR_TX_DATA : unsigned(7 downto 0) := x"08";
constant ADDR_RX_DATA : unsigned(7 downto 0) := x"0C";
constant ADDR_IRQ_EN  : unsigned(7 downto 0) := x"10";
```

---

## W1C (Write-1-to-Clear) Pattern

Status registers use W1C: writing a `'1'` to a bit clears it. The firmware
reads the status, acts on set bits, then writes back the same value to clear
only those bits.

```vhdl
-- Status register with W1C
p_status : process(clk)
begin
    if rising_edge(clk) then
        if rst_n = '0' then
            status_reg <= (others => '0');
        else
            -- Hardware sets bits
            if tx_done = '1' then
                status_reg(0) <= '1';
            end if;
            if rx_valid = '1' then
                status_reg(1) <= '1';
            end if;
            if rx_error = '1' then
                status_reg(2) <= '1';
            end if;

            -- W1C: AXI write clears bits where wdata is '1'
            if axi_status_wr = '1' then
                status_reg <= status_reg and not s_axi_wdata(status_reg'range);
            end if;
        end if;
    end if;
end process p_status;
```

---

## IRQ Generation

Interrupt is the OR-reduction of `status AND irq_en`:

```vhdl
irq <= '1' when (status_reg and irq_en_reg) /= x"00000000" else '0';
```

The firmware enables specific interrupt sources by writing to IRQ_EN, then
clears them by W1C on STATUS after servicing.

---

## AXI-Lite Handshake Timing

The AXI-Lite slave responds in the **same clock cycle** as the address valid
handshake:

```
Write: AWVALID+WVALID -> AWREADY+WREADY (1 cycle) -> BVALID (next cycle)
Read:  ARVALID -> ARREADY (1 cycle) -> RVALID+RDATA (next cycle)
```

Both address and data channels must be ready simultaneously for writes.
Single-cycle response keeps the slave simple and avoids PS stalls.

---

## Soft Reset

Bit 0 of CTRL typically serves as a soft reset. The peripheral resets all
internal state when `ctrl_reg(0) = '1'`. Firmware writes CTRL=1 to reset,
then CTRL=0 to release.

```vhdl
soft_rst <= ctrl_reg(0);

-- In each process:
if rst_n = '0' or soft_rst = '1' then
    -- reset state
```

---

## Example VHDL Skeleton

```vhdl
entity my_peripheral is
    generic (
        SYS_CLK_HZ : positive := 100_000_000
    );
    port (
        clk        : in  std_logic;
        rst_n      : in  std_logic;
        -- AXI-Lite slave
        s_axi_awaddr  : in  std_logic_vector(7 downto 0);
        s_axi_awvalid : in  std_logic;
        s_axi_awready : out std_logic;
        s_axi_wdata   : in  std_logic_vector(31 downto 0);
        s_axi_wstrb   : in  std_logic_vector(3 downto 0);
        s_axi_wvalid  : in  std_logic;
        s_axi_wready  : out std_logic;
        s_axi_bresp   : out std_logic_vector(1 downto 0);
        s_axi_bvalid  : out std_logic;
        s_axi_bready  : in  std_logic;
        s_axi_araddr  : in  std_logic_vector(7 downto 0);
        s_axi_arvalid : in  std_logic;
        s_axi_arready : out std_logic;
        s_axi_rdata   : out std_logic_vector(31 downto 0);
        s_axi_rresp   : out std_logic_vector(1 downto 0);
        s_axi_rvalid  : out std_logic;
        s_axi_rready  : in  std_logic;
        -- Interrupt
        irq : out std_logic;
        -- Monitor
        mon_state : out std_logic_vector(2 downto 0)
    );
end entity my_peripheral;

architecture rtl of my_peripheral is
    -- Registers
    signal ctrl_reg   : std_logic_vector(31 downto 0) := (others => '0');
    signal status_reg : std_logic_vector(31 downto 0) := (others => '0');
    signal irq_en_reg : std_logic_vector(31 downto 0) := (others => '0');

    -- AXI write/read signals
    signal axi_wr_en  : std_logic;
    signal axi_wr_addr : unsigned(7 downto 0);
    signal axi_rd_addr : unsigned(7 downto 0);
begin
    -- AXI-Lite slave logic (single-cycle response)
    -- ... (write channel, read channel, response generation)

    -- IRQ: OR-reduction of masked status
    irq <= '1' when (status_reg and irq_en_reg) /= x"00000000" else '0';
end architecture rtl;
```
