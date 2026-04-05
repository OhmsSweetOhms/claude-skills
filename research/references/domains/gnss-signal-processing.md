# Domain: GNSS Signal Processing

Domain reference for GPS/GNSS signal processing research, including FPGA/SoC implementation aspects.

## Conferences

| Name | Abbreviation | Notes |
|------|-------------|-------|
| ION GNSS+ | ION GNSS+ | Premier GNSS conference; acquisition, tracking, receiver design |
| IEEE PLANS | IEEE PLANS | Position Location and Navigation Symposium |
| IEEE/ION Position Location and Navigation Symposium | IEEE/ION PLANS | Joint conference; sometimes indexed separately |
| IEEE Aerospace Conference | IEEE Aerospace | Broader scope but includes GNSS/FPGA sessions |

## Journals

| Name | Notes |
|------|-------|
| IEEE Transactions on Aerospace and Electronic Systems | Top venue; receiver architecture, signal processing theory |
| IEEE Access | Open access; often has FPGA/GNSS implementation work |
| Navigation (ION journal) | ION's journal; navigation algorithms, receiver techniques |
| Sensors (MDPI) | Open access; frequent FPGA/GNSS work, variable quality — check citation counts |

## Trade Publications

| Name | URL Pattern | Notes |
|------|------------|-------|
| Inside GNSS | `insidegnss.com` | Industry-focused articles, receiver design trends |
| GPS World | `gpsworld.com` | Industry news, application stories, technology overviews |

## Vendor Sources

| Vendor | Document Prefixes | Focus Areas |
|--------|------------------|-------------|
| Xilinx/AMD | XAPP (app note), UG (user guide) | FPGA implementation, reference designs, DSP IP |
| Analog Devices | AN, CN, UG | RF front-ends, data converters, mixed-signal |
| Texas Instruments | AN, SLAA, SWRA | RF/mixed-signal, low-noise amplifiers |

Search for vendor documents using prefix + keywords: e.g., `"XAPP" GPS receiver`, `"UG" DDS FPGA`.

## Synonym Expansion

Terms in this domain have multiple names in the literature. Generate search variants:

| User Term | Search Variants |
|-----------|----------------|
| GPS acquisition | signal acquisition, code phase search, coarse acquisition |
| Fine acquisition | code phase refinement, narrow correlator, fine search |
| Carrier tracking | carrier loop, PLL, FLL, Costas loop, carrier recovery |
| Code tracking | DLL, delay-lock loop, code loop, early-late correlator |
| FPGA | programmable logic, reconfigurable hardware, HDL implementation |
| Zynq | SoC FPGA, ARM+FPGA, PS/PL, programmable SoC |
| VHDL | HDL, RTL, hardware description language |
| GPS L1 | L1 C/A, 1575.42 MHz, civil GPS signal |
| Doppler | frequency offset, carrier frequency error, Doppler shift |
| Correlator | despreader, matched filter, correlation engine |
| Navigation solution | position fix, PVT (position velocity time), least-squares positioning |
| Anti-windup | integrator clamping, conditional integration, back-calculation |
| PLL bandwidth | loop bandwidth, noise bandwidth, Bn |

## Known Repositories

Search these by name first — they are the most established open-source GNSS projects:

| Name | URL | Notes |
|------|-----|-------|
| gnss-sdr | `github.com/gnss-sdr/gnss-sdr` | C++ software-defined GNSS receiver; large, active, multi-signal |
| SoftGNSS | (MATLAB) | Borre et al. textbook companion; reference for algorithms |
| GNSS-VHDL | (search GitHub) | VHDL GNSS receiver components; smaller project |
| gps-fpga | (search GitHub) | FPGA-based GPS receiver implementations |

Also search for: `GNSS receiver FPGA`, `GPS VHDL`, `GPS Verilog`, `GNSS SDR FPGA`.

## Platform Matching

When ranking results, match against these platform hierarchies:

| Target | Close Equivalents | Distant (still useful) |
|--------|-------------------|----------------------|
| Zynq-7000 | Any 7-series (Artix-7, Kintex-7), Zynq UltraScale+ | Spartan-6, Virtex-5, non-Xilinx FPGAs |
| UltraScale+ | Zynq UltraScale+, Kintex UltraScale | 7-series (architecture similar enough) |
| Any Xilinx | Other Xilinx families | Intel/Altera (concepts transfer, details differ) |

Key FPGA resources to look for in implementations:
- **DSP48 slices** — correlator arithmetic, NCO, filters
- **Block RAM (BRAM)** — code replica storage, sample buffers
- **Clock frequency** — determines maximum sample rate and correlator throughput
- **AXI interfaces** — PS/PL data path on Zynq

## Code Search Limitations

GitHub code search has **near-zero coverage for VHDL/Verilog files**. Do not rely on `gh api search/code` for HDL entity discovery or signal tracing. Instead:
- Find repos via repo search (`gh api search/repositories`)
- Inspect repo contents via `gh api repos/{owner}/{name}/contents`
- Read README for architecture description
- Use directory structure as a proxy for architecture (e.g., `src/acquisition/`, `src/tracking/`)

## Domain-Specific Ranking Notes

- **Data-only vs. pilot signal:** GPS L1 C/A is data-only (no pilot). Techniques designed for pilot signals (e.g., Galileo E1-B/C, GPS L5) may not apply directly. Filter results accordingly.
- **Civilian vs. military signals:** Most open literature covers civilian signals. Military signal work (P(Y), M-code) is largely restricted.
- **Real-time vs. post-processing:** FPGA implementations are real-time by nature. Software-only results may describe post-processing algorithms that need adaptation for real-time constraints.
- **Sample rate dependency:** Acquisition/tracking architectures vary significantly by sample rate (e.g., 5 MHz vs. 20 MHz vs. 60+ MHz). Note the sample rate when evaluating implementations.
- **Resource utilization scaling:** A design proven on Virtex-7 may not fit on Artix-7. Check resource reports when available.
