# Domain: GNSS Signal Processing — Carrier Tracking Control Loops

Domain reference for GNSS carrier tracking loop design: FLL/PLL loop filters, discriminator selection, anti-windup policies, transition gating, bandwidth validation. Focused on theory, parameter extraction, and cross-implementation comparison — not platform/SoC concerns.

## Conferences

| Name | Abbreviation | Notes |
|------|-------------|-------|
| ION GNSS+ | ION GNSS+ | Tracking loop papers, discriminator comparisons, receiver architecture |
| IEEE PLANS | IEEE PLANS | Position Location and Navigation Symposium |
| ION International Technical Meeting | ION ITM | Often has detailed receiver implementation papers |
| ION Pacific PNT | ION Pacific PNT | Emerging techniques, sometimes loop design |

## Journals

| Name | Notes |
|------|-------|
| IEEE Transactions on Aerospace and Electronic Systems | Loop filter theory, discriminator analysis, tracking performance |
| Navigation (ION journal) | Receiver design, tracking algorithms |
| GPS Solutions (Springer) | Practical receiver techniques, tracking loop tuning |
| IEEE Access | Open access; implementation-focused tracking papers |
| Sensors (MDPI) | Open access; variable quality — check citation counts |

## Foundational Textbooks and Authors

These are the canonical sources for GNSS tracking loop design. Most implementations trace their design equations back to one or more of these:

| Reference | What It Covers | Key For |
|-----------|---------------|---------|
| Kaplan & Hegarty — "Understanding GPS: Principles and Applications" (2nd/3rd ed.) | FLL/PLL design equations, discriminator taxonomy, loop filter order/bandwidth | Discriminator selection, operating ranges, anti-windup |
| Ward — tracking loop filter design (Ward 1998, or Kaplan ch. 5) | 3rd-order PLL + 2nd-order FLL assist structure, loop filter coefficients | Loop filter coefficient derivation, bandwidth relationships |
| Van Dierendonck — GPS receiver discriminator theory (1996, ION proceedings) | Original discriminator taxonomy: cross-dot atan2, four-quadrant arctan, decision-directed | Which discriminator forms are robust to data-bit transitions and why |
| Borre et al. — "A Software-Defined GPS and Galileo Receiver" (2007) | SoftGNSS (MATLAB) design decisions, complete tracking loop implementation | Specific parameter choices, transition logic, reference implementation |

Search for these by author name and short title. Citation tracing from these typically reaches the full body of tracking loop literature.

## Trade Publications

| Name | URL Pattern | Notes |
|------|------------|-------|
| Inside GNSS | `insidegnss.com` | Receiver design articles, loop tuning discussions |
| GPS World | `gpsworld.com` | Less technical but occasionally covers tracking advances |
| Navipedia (ESA) | `navipedia.net` | Reference articles on PLL/FLL design, discriminators, lock detectors |

## Vendor Sources

Not a primary axis for this domain. Vendor app notes rarely cover loop filter theory. If needed:

| Vendor | Relevance |
|--------|-----------|
| u-blox | Receiver integration notes — sometimes mentions tracking parameters |
| NovAtel | White papers on tracking in challenging environments |
| Septentrio | Technical notes on multipath-resistant tracking |

## Synonym Expansion

| User Term | Search Variants |
|-----------|----------------|
| FLL | frequency lock loop, frequency discriminator, frequency tracking |
| PLL | phase lock loop, carrier phase tracking, phase discriminator |
| Costas loop | Costas discriminator, squaring loop, data-insensitive PLL |
| FLL-assisted PLL | FLL/PLL, coupled loop, carrier-aiding, frequency-aided tracking |
| Discriminator | error detector, tracking discriminator, loop discriminator |
| Cross-dot atan2 | cross product discriminator, atan2(cross,dot), ATAN2 discriminator |
| Differential arctan | decision-directed discriminator, arctan discriminator |
| Anti-windup | integrator clamping, conditional integration, back-calculation, integrator reset |
| Loop filter | loop controller, tracking filter, 2nd-order filter, 3rd-order filter |
| Loop bandwidth | noise bandwidth, Bn, equivalent noise bandwidth |
| Lock detector | phase lock indicator, PLI, FLI, lock metric, CN0 estimator |
| Pull-in | acquisition-to-tracking handoff, initial lock, frequency pull-in |
| Nav-bit transition | data-bit edge, bit boundary, navigation data transition |
| NCO | numerically controlled oscillator, carrier NCO, code NCO |
| Coherent integration | predetection integration, coherent accumulation, integrate-and-dump |

## Known Repositories

Search these for tracking loop internals — the source code IS the literature for implementation-level questions:

| Name | What to Look For | Notes |
|------|-----------------|-------|
| gnss-sdr | `src/algorithms/tracking/` — discriminators, loop filters, FLL pull-in logic, `enable_fll_pull_in`, `fll_bw_hz` | C++; most complete open-source receiver; check which signal each parameter was tuned for |
| SoftGNSS | Tracking loop MATLAB code — loop filter coefficients, discriminator choice, transition logic | MATLAB; Borre et al. textbook companion; simpler but canonical |
| gps-walkthrough | If it exists — Python/educational GPS receiver with tracking loops | Check for parameter values and design rationale in comments |
| PocketSDR | Compact SDR receiver — tracking implementation | Python/C; sometimes simpler to read than GNSS-SDR |

When in code-as-literature mode, extract: discriminator variant used, loop filter order and coefficients, bandwidth values, transition conditions, anti-windup logic, clipping/saturation limits, integrator reset policy at handoff.

## Domain-Specific Ranking Notes

### Data-Only vs. Pilot Signal (Critical Filter)

GPS L1 C/A is a **data-only signal** — no pilot channel for clean carrier wipe-off. This fundamentally constrains which tracking parameters are valid:

- **Papers that validate tracking parameters on pilot channels do NOT directly transfer.** Pilot-channel tracking avoids the nav-bit-transition problem entirely. BW values, transition thresholds, and lock detectors validated on pilots will be optimistic for data-only operation.
- **Elevate data-channel-specific results.** Flag pilot-only validations as non-transferable.
- **GNSS-SDR defaults may assume pilot availability** in some signal configurations. When extracting parameters, always check which signal the parameters were tuned for.

### Coherent Integration Time Sensitivity

Discriminator operating ranges depend on coherent integration time:
- 1 ms integration: wide unambiguous frequency range but lower sensitivity
- 10-20 ms integration: narrow unambiguous range, must handle nav-bit boundaries
- Results validated at one integration time may not apply at another

### Loop Order Matters

- 2nd-order PLL + 1st-order FLL: common for benign dynamics
- 3rd-order PLL + 2nd-order FLL: common for vehicular/dynamic use
- Anti-windup, stability margins, and bandwidth recommendations differ by loop order

### Cross-Implementation Comparison Template

The most actionable output for this domain is a comparison table across implementations:

| Design Choice | GNSS-SDR | SoftGNSS | Ward/Kaplan | Van Dierendonck |
|--------------|----------|----------|-------------|-----------------|
| FLL discriminator variant | ? | ? | ? | ? |
| Pull-in FLL BW (Hz) | ? | ? | ? | ? |
| Transition gate | ? | ? | ? | ? |
| Anti-windup policy | ? | ? | ? | ? |
| Freq error clipping | ? | ? | ? | ? |
| Nav-bit edge handling | ? | ? | ? | ? |
| Integrator reset at handoff | ? | ? | ? | ? |

Research sessions in this domain should aim to fill these cells.

## Platform Matching

Not applicable — this domain is algorithm/theory focused. Results from any implementation language (C++, MATLAB, Python, VHDL) are equally valid for parameter extraction. FPGA-specific tracking implementations are covered by the `gnss-signal-processing-soc` domain reference.

## Code Search Limitations

No language-specific limitations. GNSS-SDR (C++) and SoftGNSS (MATLAB) are well-indexed on GitHub. Standard `gh api search/repositories` and even code search work for these languages.
