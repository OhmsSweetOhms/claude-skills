# VHDL Coding Rules

Read this file before writing any VHDL (Stage 2). These rules target Xilinx Zynq-7000 / UltraScale with Vivado 2023.2. Libraries: `ieee.std_logic_1164` + `ieee.numeric_std` only.

---

## File header (mandatory)

Every VHDL file must have a header comment block containing:

- `Author      : OhmsSweetOhms` (never use real names or "Claude Code")
- Module name, target device, tool version
- List of generics with valid ranges
- List of ports with direction and description
- Architecture block diagram (ASCII)
- Gain/parameter formulae (if applicable)
- Integration notes (clock constraints, ASYNC_REG attributes, timing)
- Change log from previous version
- All paths in headers and comments must be relative (never absolute)

---

## Entity rules

```vhdl
entity my_module is
    generic (
        SYS_CLK_HZ : positive := 100_000_000;  -- always include valid range in comment
        DATA_W     : positive := 32;
        GAIN_W     : positive := 16             -- width of gain ports, not a magic number
    );
    port (
        clk   : in  std_logic;
        rst_n : in  std_logic;                  -- active-low synchronous reset
        ...
    );
end entity my_module;
```

- All generics must have defaults.
- Document the valid range of every generic in a comment.
- Use positive for widths; use natural for values that may be zero.
- Active-low synchronous reset (rst_n) is the Zynq-compatible convention.

---

## Architecture rules

Architecture name: always `rtl`.

One process per register group. Do not put unrelated registers in one process.
Name every process: `p_sync`, `p_edge`, `p_filter`, `p_nco`, `p_lock`, etc.

Reset branch first, always:

```vhdl
p_example : process(clk)
begin
    if rising_edge(clk) then
        if rst_n = '0' then
            reg <= (others => '0');     -- explicit reset value
        else
            reg <= next_value;
        end if;
    end if;
end process p_example;
```

---

## Saturation idiom

Use variables, not signals, inside the process:

```vhdl
p_filter : process(clk)
    variable sum_v : signed(WIDE-1 downto 0);
begin
    if rising_edge(clk) then
        if rst_n = '0' then
            result <= (others => '0');
        elsif valid = '1' then
            sum_v := a + b;
            if    sum_v > MAX_VAL then  result <= MAX_VAL;
            elsif sum_v < MIN_VAL then  result <= MIN_VAL;
            else                        result <= resize(sum_v, NARROW);
            end if;
        end if;
    end if;
end process p_filter;
```

## Saturation constants -- always write as bit-vector aggregates

```vhdl
-- CORRECT: no integer arithmetic, elaborates at any width
constant MAX_VAL : signed(N-1 downto 0) := (N-2 => '1', others => '0');  -- +2^(N-2)
constant MIN_VAL : signed(N-1 downto 0) := (N-1 => '1', N-2 => '1', others => '0');  -- -2^(N-2)

-- WRONG: 2**(N-2) overflows VHDL integer when N > 33
constant MAX_VAL : signed(N-1 downto 0) := to_signed(2**(N-2), N);  -- BUG if N>33
```

---

## Signed arithmetic -- avoid abs()

`abs(signed(-2^(N-1)))` wraps to `-2^(N-1)` in VHDL two's complement. Always use explicit two-sided comparison:

```vhdl
-- CORRECT
if (err > -BAND) and (err < BAND) then ...

-- WRONG: abs(-2^31) = -2^31 in VHDL, passes the check incorrectly
if abs(err) < BAND then ...
```

---

## Multiplier widths

The product width equals the sum of operand widths:

```vhdl
-- 32x16 -> 48-bit: exact, maps to 2 DSP48E1
prop_v := phase_err * to_signed(KP, GAIN_W);   -- signed(31) x signed(15) = signed(47)

-- Never assign a product to a signal narrower than sum of operand widths
-- without an explicit resize/shift.
```

---

## State machine enum naming

VHDL is case-insensitive. State enum values like `TX_START` or `RX_DATA` will collide with port names `tx_start` or `rx_data`. Always prefix state enum values with `ST_`:

```vhdl
type tx_state_t is (ST_TX_IDLE, ST_TX_START, ST_TX_DATA, ST_TX_PARITY, ST_TX_STOP);
type rx_state_t is (ST_RX_IDLE, ST_RX_START_DET, ST_RX_DATA, ST_RX_PARITY, ST_RX_STOP);
```

---

## Multi-driver avoidance

A signal must be driven by exactly one process. When two processes need to coordinate on a shared counter or register, use a handshake signal:

```vhdl
-- WRONG: two processes driving tick_cnt
p_counter : process(clk) ... tick_cnt <= tick_cnt - 1; ...
p_fsm     : process(clk) ... tick_cnt <= (others => '0'); ...  -- multi-driver!

-- CORRECT: FSM sets a request flag, counter process reads it
signal reset_cnt : std_logic := '0';
p_counter : process(clk) ...
    if reset_cnt = '1' then tick_cnt <= (others => '0');
    else tick_cnt <= tick_cnt - 1; end if; ...
p_fsm     : process(clk) ... reset_cnt <= '1'; ...
```

---

## Error flag timing

When an error condition is detected in one state but the validity pulse fires in a later state, latch the error into an intermediate signal:

```vhdl
-- In PARITY state: latch the check result
rx_parity_bad <= '1' when (voted_bit /= expected_parity) else '0';

-- In STOP state: output coincident with rx_valid
parity_err <= rx_parity_bad;
rx_valid   <= '1';
```

---

## Pulse output default pattern

Pulse outputs default `'0'` before `case`/`if`, set `'1'` when active:

```vhdl
output_valid <= '0';   -- default
case state is
    when ST_DONE =>
        output_valid <= '1';   -- pulse for one cycle
```

---

## Clock domain crossing

2-FF synchroniser with ASYNC_REG attribute on both stages for all external/async inputs:

```vhdl
signal sync1 : std_logic := '0';
signal sync2 : std_logic := '0';
attribute ASYNC_REG : string;
attribute ASYNC_REG of sync1 : signal is "TRUE";
attribute ASYNC_REG of sync2 : signal is "TRUE";
```

---

## Monitor ports

Prefer promoting internal signals as proper output ports on the entity rather than using VHDL-2008 external names in a wrapper. Synthesis tools trim unconnected monitor ports automatically. Prefix with `mon_`.

```vhdl
entity my_module is
    port (
        -- ... functional ports ...
        -- Monitor outputs (synthesis tools trim if unconnected)
        mon_internal_a : out std_logic_vector(N-1 downto 0);
        mon_valid      : out std_logic
    );
end entity;

mon_internal_a <= std_logic_vector(internal_a);
mon_valid      <= valid_flag;
```

Use VHDL-2008 external names (`<<signal .u_core.X : T>>`) only when modifying the core entity is not an option.

---

## IBUF handling for external inputs

Vivado automatically inserts IBUF on all top-level input ports during synthesis. Do not instantiate IBUF explicitly. If manual IBUF control is required, instantiate them in a board-specific top-level above the wrapper using `library UNISIM; use UNISIM.vcomponents.all`.

---

## Wrapper evolution

1. **Passthrough wrapper**: Exposes internal signals as monitor ports. Core entity unchanged.
2. **Monitor-port wrapper**: Monitor signals promoted to entity ports. Wrapper is pure wire passthrough.
3. **Integration wrapper**: Wrapper adds CDC synchronisers, input muxing, clock-enable generation. Core stays clean and reusable.

Keep the core generic-parameterised and board-agnostic; put all board-specific I/O handling in the wrapper.

---

## Clock-enable edge detection

When a module generates an internal clock signal that must sample data, avoid routing it as a fabric clock. Instead, detect its edges in the sys_clk domain and use a clock enable:

```vhdl
signal output_d1  : std_logic := '0';
signal sample_en  : std_logic := '0';

p_edge_detect : process(sys_clk)
begin
    if rising_edge(sys_clk) then
        output_d1 <= output_sig;
        if edge_sel = '0' then
            sample_en <= (not output_d1) and output_sig;       -- rising edge
        else
            sample_en <= output_d1 and (not output_sig);       -- falling edge
        end if;
    end if;
end process;
```

---

## Dead code check

Before declaring done:

- Every signal declared must be both driven and read.
- Every generic must be used in at least one expression.
- Hex constants lowercase: `x"deadbeef"`, not `x"DEADBEEF"`.
- 4-space indent. Align port `:` and `in`/`out` columns.
- Direct entity instantiation with named association -- no component decls, no positional maps.
- `rising_edge(clk)` only -- never `clk'event`.
