# AD9986 GPS NCO Frequency Planning Reference

Review status: promoted from the ZCU102 streaming-system FPGA thread on
2026-05-08 after the clean L1 profiles were created and hardware-validated.

Use this reference when planning AD9081/AD9986 GPS front-end profiles, especially
when the task involves converter-clock selection, CDDC/FDDC or CDUC/FDUC NCO
placement, JESD mode selection, and whether the HDL operating point must be
rebuilt.

Key reviewed takeaways:

- `3932.16 MHz = 122.88 MHz x 32` is the clean converter clock for the GPS
  10.23 MHz frequency grid. GPS L1 gives `1575.42 / 3932.16 = 26257 / 65536`,
  FTW48 `112772956291072`, 32 trailing binary zeros, and 0 Hz error.
- `2949.12 MHz = 122.88 MHz x 24` is a poor L1 NCO point because the reduced
  denominator contains a factor of 3 and the FTW has 0 trailing binary zeros.
- The verified `6144-l1-clean` profile uses RX CDDC/FDDC `4 x 16` to
  61.44 MSPS and TX CDUC/FDUC `4 x 8` to 122.88 MSPS. PL-side rate conversion
  from the GPS app boundary is RX `/15` and TX `x30`.
- The verified `24576-clean-l1` profile uses RX/TX `4 x 4` with 245.76 MSPS
  JESD sample rates. PL-side rate conversion from the GPS app boundary is
  `/60` and `x60`; it is a separate HDL operating point.
- The quad-band L1/L2/L5/Iridium section is the durable NCO plan: CDDC center
  1404.24 MHz and four FDDC offsets produce exact FTWs for all bands while
  retaining M8/L4 at 61.44 MSPS per channel.

Terminology note: CDDC/FDDC are RX-side names. The TX return path uses the
corresponding CDUC/FDUC stages, with the same main-vs-channel NCO placement
discipline.

Source provenance: originally written as
`.threads/fpga/20260424-zcu102-streaming-system/ad9986_gps_l1_l2_l5_IRIDIUM_nco_analysis.md`.

---

# Original Analysis: AD9986 NCO Frequency Planning for GPS L1

## Objective

Determine the optimal ADC clock rate and NCO configuration to receive GPS L1 (1575.42 MHz) on the AD9986 with zero or minimal NCO phase truncation noise, using the on-board 122.88 MHz crystal as the clock source.

## Hardware Configuration

- Device: AD9986-4D2AC (functionally equivalent to AD9082, same silicon)
- Eval board: AD9986-FMCB-EBZ
- Carrier: ZCU102
- Crystal: 122.88 MHz
- ADC: Dual 12-bit, max 6 GSPS
- NCO accumulator width: 48 bits (both CDDC and FDDC)
- CDDC decimation options: 1, 2, 3, 4, 6
- FDDC decimation options: 1, 2, 3, 4, 6, 8, 12, 16, 24
- HDL reference design: `analogdevicesinc/hdl/projects/ad9081_fmca_ebz/zcu102`

## GPS L1 Signal Parameters

All GPS L1 signals share the same carrier frequency: 1575.42 MHz (= 154 × 10.23 MHz).

- L1 C/A: BPSK(1), 1.023 Mchip/s, ~2.046 MHz bandwidth
- L1C data: BOC(1,1), 1.023 Mchip/s
- L1C pilot: TMBOC (BOC(1,1) + BOC(6,1)), ~4.092 MHz bandwidth
- L1 P(Y): BPSK(10), 10.23 Mchip/s, ~20.46 MHz bandwidth (encrypted)

C/A and L1C are on the same carrier. Separation is done in the correlator (PRN code domain), not in the DDC (frequency domain).

## Key Finding: ADC Clock Rate Selection

### The Problem

NCO phase truncation noise manifests as periodic noise floor modulation ("breathing") when the frequency tuning word (FTW) does not divide cleanly into the 48-bit accumulator space. The severity depends on the number of trailing binary zeros in the FTW. More trailing zeros = fewer spurs and less noise floor modulation. Zero trailing zeros = worst case.

### Analysis of Candidate ADC Rates

All rates derived from 122.88 MHz crystal via on-chip PLL.

#### Claim 1: 3932.16 MSPS (122.88 MHz × 32) — EXACT HIT

```
f_adc   = 122880000 * 32 = 3932160000 Hz
target  = 1575420000 Hz
ratio   = 1575420000 / 3932160000 = 26257 / 65536
```

**Verify:** 65536 = 2^16 (pure power of two in denominator).

```
FTW = round(1575420000 * 2^48 / 3932160000)
    = 112772956291072

Verify: FTW * f_adc / 2^48 = 1575420000.000000 Hz (exact)
Verify: 1575420000 * 2^48 mod 3932160000 = 0 (no remainder)
Verify: FTW trailing binary zeros = 32
```

**Result:** Zero phase truncation error. Zero noise floor modulation. This is the optimal ADC rate for GPS L1 with a 122.88 MHz crystal.

#### Claim 2: 2949.12 MSPS (122.88 MHz × 24) — WORST CASE

```
f_adc   = 122880000 * 24 = 2949120000 Hz
target  = 1575420000 Hz
ratio   = 1575420000 / 2949120000 = 26257 / 49152
```

**Verify:** 49152 = 2^14 × 3 (factor of 3 prevents clean binary ratio).

```
FTW = round(1575420000 * 2^48 / 2949120000)
    = 150363941721429

Verify: FTW * f_adc / 2^48 ≈ 1575419999.999996 Hz (error ≈ 3.6 µHz)
Verify: 1575420000 * 2^48 mod 2949120000 ≠ 0
Verify: FTW trailing binary zeros = 0
```

**Result:** Zero trailing zeros. Maximum phase truncation spur count. Worst case noise floor breathing.

#### Claim 3: 4915.20 MSPS (122.88 MHz × 40) — NEAR MISS

```
f_adc   = 122880000 * 40 = 4915200000 Hz
target  = 1575420000 Hz
ratio   = 1575420000 / 4915200000 = 26257 / 81920
```

**Verify:** 81920 = 2^14 × 5 (factor of 5 prevents clean binary ratio).

```
FTW = round(1575420000 * 2^48 / 4915200000)

Verify: error ≈ 6.9 µHz with CDDC alone
Verify: CDDC + FDDC with dec=3 achieves ≈ 1.2 µHz error
```

#### Claim 4: 5898.24 MSPS (122.88 MHz × 48) — RECOVERABLE

```
f_adc   = 122880000 * 48 = 5898240000 Hz
ratio denominator factors: verify if 3 is present
```

**Verify:** CDDC + FDDC with dec=3 or dec=6 achieves zero error (the decimation by 3 cancels the factor of 3 in the denominator).

## Recommended Configuration

### ADC Clock: 3932.16 MSPS (122.88 MHz × 32)

### CDDC NCO: 1575420000 Hz

- FTW = 112772956291072
- Trailing zeros = 32
- Error = 0 Hz
- No FDDC frequency correction needed

### Decimation Chain

Target JESD IQ rate: 61.44 MSPS (verified profile, mode 10.0)

- CDDC decimation: ×4 → 983.04 MSPS
- FDDC decimation: ×16 → 61.44 MSPS
- Total on-chip decimation: ×64
- FDDC NCO: 0 Hz (no fine tuning needed)
- PL (FPGA fabric) decimation: ÷15 → 4.096 MSPS application rate

**Verify:** 3932160000 / 64 = 61440000 Hz = 61.44 MSPS
**Verify:** 61440000 / 15 = 4096000 Hz = 4.096 MSPS (= 4 × 1.024 MHz, GPS-friendly)

### JESD204B Link Parameters (ZCU102) — Verified Profile

**IMPORTANT:** The JESD lane rate depends on the IQ sample rate _after on-chip decimation_, not the ADC clock rate.

For 8B10B: `Lane Rate = IQ_Rate × M × NP × (10/8) / L`

#### RX Path (ADC → FPGA)

| Item | Value |
|---|---:|
| RX JESD sample rate | 61.44 MSPS |
| RX ADC clock | 3932.16 MHz |
| RX on-chip decimation | 4 × 16 = 64 |
| RX JESD mode | mode 10.0, M8/L4/S1/NP16 |
| RX lane rate | 2.4576 Gbps |
| RX main NCO (CDDC) | 1575420000 Hz |
| RX fine NCO (FDDC) | 0 Hz |
| PL decimation to app rate | ÷15 → 4.096 MSPS |

**Verify:** Lane rate = 61.44e6 × 8 × 16 × 1.25 / 4 = 2457600000 = 2.4576 Gbps

#### TX Path (FPGA → DAC)

| Item | Value |
|---|---:|
| TX JESD sample rate | 122.88 MSPS |
| TX DAC clock | 3932.16 MHz |
| TX interpolation | 4 × 8 = 32 |
| TX JESD mode | mode 15, M8/L8/S1/NP16 |
| TX lane rate | 2.4576 Gbps |
| TX main NCO (CDUC) | 1575420000 Hz |
| TX main NCO FTW | 112772956291072 |
| TX fine NCO (FDUC) | 0 Hz |

**Verify:** Lane rate = 122.88e6 × 8 × 16 × 1.25 / 8 = 2457600000 = 2.4576 Gbps

The TX main NCO uses the same clean ratio as RX: 1575420000 / 3932160000 = 26257 / 65536. FTW = 112772956291072, 32 trailing zeros, zero phase truncation noise. The no-OS API computes TX main NCO FTW against `dac_freq_hz` (3932160000). Channel/fine NCOs are stage-scaled in the API but set to 0 Hz here — all RF placement is in the main NCO.

#### HDL Build Command

```
cd hdl/projects/ad9081_fmca_ebz/zcu102
make RX_LANE_RATE=2.4576 TX_LANE_RATE=2.4576 \
     RX_JESD_L=4 RX_JESD_M=8 RX_JESD_S=1 RX_JESD_NP=16 \
     TX_JESD_L=8 TX_JESD_M=8 TX_JESD_S=1 TX_JESD_NP=16
```

Note: RX and TX have different L (4 vs 8) because the TX IQ rate is 122.88 MSPS (2× the RX rate) but the lane rate is matched at 2.4576 Gbps.

#### Historical Caveat

This paragraph was written before the clean profiles were implemented. At that
time, the verified 61.44 profile in the codebase used TX DAC at 2949.12 MHz and
TX main NCO at 1 GHz. The clean L1 profiles have since been implemented and
hardware-validated with 3932.16 MHz converter clocks and 1575.42 MHz main NCOs.
Keep this note as provenance for why the no-OS `uc_settings` and clock/JESD
patches had to move together.

## Configuration B: Dual-Band L1 + L2 (Single Physical ADC)

One physical ADC captures both GPS L1 (1575.42 MHz) and L2 (1227.60 MHz) simultaneously. The CDDC NCO shifts to the midpoint of the two carriers, then two FDDCs separate them into independent baseband channels.

### Why This Works

All GPS carriers are integer multiples of 10.23 MHz:
- L1 = 154 × 10.23 MHz = 1575.42 MHz
- L2 = 120 × 10.23 MHz = 1227.60 MHz
- Midpoint = 137 × 10.23 MHz = 1401.51 MHz
- Offset = 17 × 10.23 MHz = ±173.91 MHz

The 3932.16 MHz clock (122.88 × 32) shares enough common factors that every GPS-derived frequency lands on a power-of-2 denominator ratio. All three NCOs hit their targets with zero error.

### NCO Plan

| NCO | Frequency | FTW | Trailing zeros | Ratio | Error |
|---|---:|---:|---:|---|---:|
| CDDC (center) | 1401510000 Hz | 100323993583616 | 31 | 46717/131072 (2^17) | 0 Hz |
| FDDC 0 (→ L1) | +173910000 Hz | 49795850829824 | 33 | 5797/32768 (2^15) | 0 Hz |
| FDDC 1 (→ L2) | -173910000 Hz | -49795850829824 | 33 | 5797/32768 (2^15) | 0 Hz |

### Signal Path

```
ADC (3932.16 MSPS)
  → CDDC NCO @ 1401.51 MHz, dec ×4 (983.04 MSPS complex)
    → FDDC 0: NCO @ +173.91 MHz, dec ×16 → 61.44 MSPS (L1 baseband I/Q)
    → FDDC 1: NCO @ -173.91 MHz, dec ×16 → 61.44 MSPS (L2 baseband I/Q)
  → JESD: M=4 (2 complex channels), 2 virtual converters per band
```

Total on-chip decimation: CDDC(4) × FDDC(16) = ×64
Post-JESD PL decimation: ÷15 → 4.096 MSPS per channel (GPS app rate)

### Bandwidth Check

Post-CDDC Nyquist bandwidth = 983.04 / 2 = 491.52 MHz. The ±173.91 MHz offsets are comfortably inside.

**Verify:** 491.52 > 173.91 (offsets fit in post-CDDC passband)

### JESD204B Link Parameters

Each FDDC outputs 61.44 MSPS complex I/Q. Two channels = M=4.

| Item | Value |
|---|---:|
| RX JESD sample rate | 61.44 MSPS per channel |
| RX ADC clock | 3932.16 MHz |
| RX on-chip decimation | 4 × 16 = 64 |
| M | 4 (2 complex channels) |
| L | 2 |
| S | 1 |
| NP | 16 |
| F | 4 |
| RX lane rate | 2.4576 Gbps |

**Verify:** Lane rate = 61.44e6 × 4 × 16 × 1.25 / 2 = 2457600000 = 2.4576 Gbps

This matches the L1-only lane rate (2.4576 Gbps), so the same FPGA transceiver configuration works — only M and L change in the HDL build.

### AD9986 DDC Resources

The AD9986 (dual ADC, same as AD9082) provides per ADC pair: 2 CDDCs, 4 FDDCs. This configuration uses 1 CDDC and 2 FDDCs from one physical ADC, leaving the second ADC and remaining DDC resources available for other tasks or redundancy.

### Crossbar Routing

The CDDC output must be routed to both FDDC 0 and FDDC 1 via the 4×8 crossbar mux. Set `Crossbar4x8Mux2` so both target FDDCs receive the same CDDC output.

## Configuration C: Triple-Band L1 + L2 + L5 (Single Physical ADC)

L5 = 115 × 10.23 MHz = 1176.45 MHz. The L1-to-L5 span is 398.97 MHz, which still fits inside the CDDC dec=4 passband (491.52 MHz Nyquist).

### NCO Plan (center = 135 × 10.23 MHz = 1381.05 MHz)

Using 135 × 10.23 MHz as center gives better trailing zeros than the geometric midpoint.

| NCO | Frequency | Trailing zeros | Error |
|---|---:|---:|---:|
| CDDC (center) | 1381050000 Hz | 31 | 0 Hz |
| FDDC 0 (→ L1) | +194370000 Hz | 33 | 0 Hz |
| FDDC 1 (→ L2) | -153450000 Hz | 33 | 0 Hz |
| FDDC 2 (→ L5) | -204600000 Hz | 35 | 0 Hz |

CDDC ratio: 46035/131072 (denominator = 2^17, pure power of 2).

**Verify:** All offsets < 491.52 MHz Nyquist (max offset = 204.60 MHz)

Three FDDCs → M=6 (3 complex channels). Uses 1 CDDC + 3 FDDCs, leaving 1 FDDC spare. Same CDDC dec=4, FDDC dec=16 = total ×64, each channel at 61.44 MSPS.

This configuration requires verification that M=6 is a supported JESD204B transport layer configuration for the AD9986. If not, M=8 with two unused virtual converters may be necessary.

### Why 3932.16 MHz Is the GPS Clock

The 3932.16 MHz ADC rate produces zero-error NCO values for every civilian GPS frequency because the ratio of any GPS carrier to 3932.16 MHz reduces to a fraction with a power-of-2 denominator. This eliminates phase truncation noise on all NCOs simultaneously — a property unique to this clock rate among the 122.88 MHz PLL multiplier options.

## Configuration D: Quad-Band L1 + L2 + L5 + Iridium ALT-NAV (Single Physical ADC)

Adds the Iridium Satellite Time & Location (STL) band (1616–1626.5 MHz) to the GPS tri-band capture. STL provides jam-resistant alternative PNT independent of GNSS, with signals 20–30 dB stronger than GPS at the antenna.

### The Problem

Iridium frequencies are not multiples of 10.23 MHz. The band center (1621.25 MHz) produces an FDDC FTW with zero trailing zeros — worst case NCO noise. A brute-force search over CDDC centers and FDDC placements finds a configuration where all five NCOs are mathematically exact.

### The Solution

The FDDC for ALT-NAV does not need to be centered at 1621.25 MHz. Shifting it to 1619.28 MHz places the NCO offset at exactly 7/32 of the FDDC clock rate (983.04 MSPS), which is a pure binary fraction. The full 10.5 MHz band still falls within the FDDC passband.

```
ALT-NAV offset = 215040000 Hz
FDDC rate      = 983040000 Hz
Ratio          = 215040000 / 983040000 = 7 / 32

FTW = (7/32) × 2^48 = 7 × 2^43
```

Since 7 is odd and multiplied by 2^43, the FTW has exactly 43 trailing zeros. The phase accumulator cycles with zero truncation error.

### NCO Plan

CDDC center: 1404240000 Hz (1404.24 MHz), ratio 5851/16384 (2^14 denominator), 34 trailing zeros, zero error.

| NCO | Frequency | Offset from CDDC | Trailing zeros | Error |
|---|---:|---:|---:|---:|
| CDDC (center) | 1404240000 Hz | — | 34 | 0 Hz |
| FDDC 0 (→ L5) | 1176450000 Hz | -227790000 Hz | 33 | 0 Hz |
| FDDC 1 (→ L2) | 1227600000 Hz | -176640000 Hz | 41 | 0 Hz |
| FDDC 2 (→ L1) | 1575420000 Hz | +171180000 Hz | 34 | 0 Hz |
| FDDC 3 (→ ALT-NAV) | 1619280000 Hz | +215040000 Hz | 43 | 0 Hz |

All five NCOs are integer-exact: `frequency × 2^48 mod clock_rate = 0` for every one.

### Signal Path

```
ADC (3932.16 MSPS)
  → CDDC NCO @ 1404.24 MHz, dec ×4 (983.04 MSPS complex)
    → FDDC 0: NCO @ -227.79 MHz, dec ×16 → 61.44 MSPS (L5 baseband I/Q)
    → FDDC 1: NCO @ -176.64 MHz, dec ×16 → 61.44 MSPS (L2 baseband I/Q)
    → FDDC 2: NCO @ +171.18 MHz, dec ×16 → 61.44 MSPS (L1 baseband I/Q)
    → FDDC 3: NCO @ +215.04 MHz, dec ×16 → 61.44 MSPS (ALT-NAV baseband I/Q)
  → JESD: M=8 (4 complex channels)
```

Total on-chip decimation: CDDC(4) × FDDC(16) = ×64
Post-JESD PL decimation: ÷15 → 4.096 MSPS per GPS channel

### ALT-NAV Band Coverage

The ALT-NAV FDDC is centered at 1619.28 MHz, 1.97 MHz below the geometric band center (1621.25 MHz). The full band maps to -3.28 MHz to +7.22 MHz relative to the FDDC center. With FDDC dec=16, the output Nyquist bandwidth is ±30.72 MHz — the 10.5 MHz band fits with wide margin.

### Resource Usage

This configuration uses ALL DDC resources on one ADC pair: 1 CDDC + 4 FDDCs. The AD9986 provides exactly 4 FDDCs per ADC pair, so this is a perfect fit. The second physical ADC is entirely free for other tasks or redundancy.

### JESD204B Link Parameters

Four complex channels = M=8.

| Item | Value |
|---|---:|
| RX JESD sample rate | 61.44 MSPS per channel |
| RX ADC clock | 3932.16 MHz |
| RX on-chip decimation | 4 × 16 = 64 |
| M | 8 (4 complex channels) |
| L | 4 |
| S | 1 |
| NP | 16 |
| RX lane rate | 2.4576 Gbps |

**Verify:** Lane rate = 61.44e6 × 8 × 16 × 1.25 / 4 = 2457600000 = 2.4576 Gbps

This matches the L1-only verified profile lane rate (2.4576 Gbps) with the same M=8/L=4 framing — no new JESD mode required.

## NCO Phase Truncation Theory (for verification)

The number of spurious products from a truncated NCO:

```
n_spurs = 2^W / GCD(FTW, 2^W) - 1
```

Where W = number of truncated bits (accumulator width minus LUT address width).

- When FTW has many trailing zeros, GCD(FTW, 2^W) is large, so n_spurs is small.
- When FTW is odd (zero trailing zeros), GCD = 1, so n_spurs = 2^W - 1 (maximum).
- The grand repetition rate (GRR) of the noise floor modulation: `GRR_period = 2^N / GCD(FTW, 2^N)` clock cycles.

At 3932.16 MSPS with FTW trailing zeros = 32:
```
GRR_period = 2^48 / 2^32 = 2^16 = 65536 clocks = 16.67 µs
```

This is fast enough that the modulation averages out in any practical measurement window.

At 2949.12 MSPS with FTW trailing zeros = 0:
```
GRR_period = 2^48 / 1 = 2^48 clocks ≈ 95,443 seconds
```

This is an extremely long cycle — the noise floor modulation would be visible as slow breathing over tens of seconds.

## AD9986 Constraints (verify against UG-1578)

The AD9986 does NOT support:
- Tx/Rx bypass mode
- Rx-to-Tx loopback
- Fast frequency hopping (FFH)
- Direct digital synthesis (DDS)
- CDUC bypass in transmit path
- CDDC bypass in receive path

The AD9986 DOES support:
- NCO Dual Modulus Mode (for non-clean NCO frequencies)
- NCO Integer-N Mode (for exact integer ratios)
- Same JESD204B/C modes as AD9082
- Same API as AD9081/AD9082

## Verification Checklist

1. Confirm 122.88 MHz × 32 = 3932.16 MHz is a valid PLL output for the AD9986
2. Confirm `1575420000 * 2^48 mod 3932160000 == 0` (exact FTW)
3. Confirm FTW = 112772956291072 has 32 trailing binary zeros
4. Confirm 26257/65536 is the fully reduced ratio of 1575420000/3932160000
5. Confirm 65536 = 2^16
6. Confirm CDDC dec=4, FDDC dec=16 is a valid decimation chain on the AD9986
7. Confirm 3932160000 / 64 = 61440000 (JESD IQ rate)
8. Confirm 2949120000 Hz gives FTW with 0 trailing zeros for 1575420000 Hz
9. Verify JESD lane rates: RX: 61.44e6 × 8 × 16 × 1.25 / 4 = 2.4576 Gbps. TX: 122.88e6 × 8 × 16 × 1.25 / 8 = 2.4576 Gbps. Confirm mode 10.0 (RX) and mode 15 (TX) exist in JESD Mode Selector Tool for AD9986.
10. Verify AD9986 restrictions against UG-1578 product comparison table
11. Confirm 61440000 / 15 = 4096000 (PL output app rate, = 4 × 1024000)
12. Confirm TX main NCO FTW = 112772956291072 (same as RX, since DAC clock = ADC clock = 3932.16 MHz)
13. Confirm TX interpolation 4 × 8 = 32, and 122880000 × 32 = 3932160000 (DAC clock)
14. L1/L2 dual-band: Confirm CDDC NCO 1401510000 Hz → ratio 46717/131072 (131072 = 2^17)
15. L1/L2 dual-band: Confirm `1401510000 * 2^48 mod 3932160000 == 0`
16. L1/L2 dual-band: Confirm FDDC offset ±173910000 Hz at 983.04 MSPS has 33 trailing zeros and zero error
17. L1/L2 dual-band: Confirm 491.52 MHz Nyquist > 173.91 MHz max offset
18. L1/L2 dual-band: Verify lane rate M=4/L=2: 61.44e6 × 4 × 16 × 1.25 / 2 = 2.4576 Gbps
19. L1/L2/L5 triple-band: Confirm CDDC NCO 1381050000 Hz → ratio 46035/131072 (2^17), zero error
20. L1/L2/L5 triple-band: Confirm max FDDC offset 204600000 Hz < 491520000 Hz Nyquist
21. L1/L2/L5 triple-band: Confirm all three FDDC NCOs (±194.37, -153.45, -204.60 MHz) have zero error and 33+ trailing zeros
22. Verify M=6 (or M=8 fallback) is a valid JESD204B transport config for the AD9986
23. Quad-band: Confirm CDDC NCO 1404240000 Hz → ratio 5851/16384 (16384 = 2^14), zero error
24. Quad-band: Confirm `1404240000 * 2^48 mod 3932160000 == 0`
25. Quad-band: Confirm FDDC L5 offset -227790000 Hz at 983.04 MSPS is exact with 33 trailing zeros
26. Quad-band: Confirm FDDC L2 offset -176640000 Hz at 983.04 MSPS is exact with 41 trailing zeros
27. Quad-band: Confirm FDDC L1 offset +171180000 Hz at 983.04 MSPS is exact with 34 trailing zeros
28. Quad-band: Confirm FDDC ALT-NAV offset +215040000 Hz at 983.04 MSPS is exact with 43 trailing zeros
29. Quad-band: Confirm 215040000 / 983040000 = 7/32 exactly (denominator = 2^5)
30. Quad-band: Confirm ALT-NAV FDDC at 1619.28 MHz covers 1616.0–1626.5 MHz band (-3.28 to +7.22 MHz from center)
31. Quad-band: Confirm max FDDC offset 227790000 Hz < 491520000 Hz Nyquist
32. Quad-band: Verify lane rate M=8/L=4: 61.44e6 × 8 × 16 × 1.25 / 4 = 2.4576 Gbps
33. Quad-band: Confirm 1 CDDC + 4 FDDCs = total DDC allocation for one AD9986 ADC pair

## Files Provided

- `nco_clean_lut.h` — C header with 201 clean NCO frequencies near 1.5 GHz (250 MHz clock, for bench testing)
- `nco_sweep.h` — Sweep engine with Zynq sleep.h timing
- `nco_sweep_funcs.h` / `nco_sweep_funcs.c` — Portable sweep functions
- `nco_clean_lut.py` — Python generator for clean NCO LUTs (configurable center, span, clock)

Note: The LUT files were generated for a 250 MHz reference clock (the default HDL fabric clock), not the 3932.16 MHz ADC clock. Regenerate with `--fclk 3932.16e6 --center 1575.42e6` for the actual ADC-rate NCO values if needed.
