# Domain: GNSS Array Signal Processing

Domain reference for GNSS antenna array work — Controlled Reception Pattern Antennas (CRPAs), adaptive beamforming (Power Minimization, MVDR, LCMV, STAP), DOA estimation, calibration and carrier-phase bias, multi-channel coherent receivers, and FPGA implementations of array DSP. Sits at the intersection of GNSS signal processing and classical array signal processing.

**See also `gnss-signal-processing-soc.md`** for receiver-pipeline-flavored work (acquisition / tracking / PVT). Queries that touch both — e.g., "CRPA output integrated with a tracking loop" — should load both files. The two domains share GPS L1 RF context but have disjoint synonym tables and largely disjoint author/venue lists.

## Conferences

| Name | Abbreviation | Notes |
|------|-------------|-------|
| ION GNSS+ | ION GNSS+ | Premier GNSS conference; CRPA / multi-antenna sessions consistent year over year |
| IEEE/ION Position Location and Navigation Symposium | IEEE/ION PLANS | Joint conference; Gupta CRPA papers, Akos reference-element beamforming |
| IEEE Sensor Array and Multichannel Signal Processing Workshop | SAM | **Array-DSP-native venue.** Daneshmand 2014 structural-interference, DLR follow-on. Not in the receiver-pipeline domain file. |
| IEEE International Conference on Acoustics, Speech and Signal Processing | ICASSP | Adaptive-array-processing fundamentals, occasional GNSS-applied papers |
| European Signal Processing Conference | EUSIPCO | Array-processing methodology with occasional GNSS application papers |
| IEEE Aerospace Conference | IEEE Aerospace | Broader scope; includes CRPA-on-platform and anti-jam sessions |
| IEEE Radar Conference | RadarConf | STAP fundamentals (radar-flavored); transfers cleanly to GNSS interference |
| IEEE Workshop on Signal Processing Advances in Wireless Communications | SPAWC | Array processing for wireless; relevant for blind / robust beamforming |
| European Navigation Conference | ENC | European GNSS counterpart to ION GNSS+ |
| GNU Radio Conference | GRCon | Not peer-reviewed but high practical value for multi-channel coherent SDR receiver bring-up (Hennerich GRCon19 phase-coherent transceiver) |

## Journals

| Name | Notes |
|------|-------|
| NAVIGATION (Journal of the Institute of Navigation) | **Top open-access venue for CRPA quantitative work.** Bamberg/Konovaltsev/Meurer 2023 70(1) phase-bias, STAP 70(3) 2023, Wu/Closas 2025 72(1) federated jamming classifier, NAVI 73(1) 2026 jammer/spoofer localization, Borio 2012 self-contained calibration, O'Brien/Gupta 2010 zero-bias filter |
| IEEE Transactions on Aerospace and Electronic Systems | Top venue; receiver-architecture and signal-processing theory; Mehr/Dovis 2024 ML-CRPA, Kim/Iltis 2004 STAP |
| IEEE Transactions on Antennas and Propagation | Patch-array design, mutual coupling, CRPA antenna characterization |
| IEEE Transactions on Signal Processing | Adaptive-array-DSP fundamentals; algorithm theory |
| IEEE Signal Processing Magazine | Tutorial-style coverage; Van Veen & Buckley 1988 (the foundational primer) lives here |
| IEEE Antennas and Wireless Propagation Letters | Shorter-form patch-array and mutual-coupling work |
| IEEE Access | Open-access; FPGA / array-implementation papers; variable quality |
| GPS Solutions (Springer) | Calibration, carrier-phase characterization, integration |
| Sensors / Applied Sciences / Remote Sensing (MDPI) | High-volume open-access; ZCU102/ZCU216 hardware-implementation papers (Gomes 2024+2025), calibration (Caizzone 2014), beam steering (Burchfield 2025), wideband-interference (Xu 2024). **Variable quality — check citation counts and reproducibility.** |
| IET Radar, Sonar & Navigation | STAP and array-processing crossover from radar |
| IEEE Journal of Selected Topics in Signal Processing | Themed array-processing issues |

## Trade Publications

| Name | URL Pattern | Notes |
|------|------------|-------|
| Inside GNSS | `insidegnss.com` | Industry-focused articles, calibration overviews, anti-jam product coverage |
| GPS World | `gpsworld.com` | "Innovation" column has occasional CRPA-relevant pieces (e.g., 2013 DD carrier-phase calibration with COTS antennas) |
| Microwave Journal | `microwavejournal.com` | RF front-end design, array antenna pieces |

## Vendor Sources

| Vendor | Document Prefixes | Focus Areas |
|--------|------------------|-------------|
| Analog Devices | AN, CN, UG, EE-, MS-, MCS | Multi-channel coherent RX (AD9081, ADRV9009/26), JESD204C, clock distribution (HMC7044, AD9528). **Multi-channel coherent receiver bring-up content lives primarily on `wiki.analog.com` and `ez.analog.com` — formal AN/CN documents lag.** |
| AMD/Xilinx | XAPP, UG, PG | FPGA implementation; PG060 Floating-Point Operator IP, Vitis HLS Solver Library, JESD204 PHY IP, ZCU102/ZCU216 board user guides; XAPP1317 scalable matrix inverse |
| Tallysman | datasheet | L1 active patch antennas (TW3870, TW3742) |
| Antcom | datasheet | 4-element CRPA reference, single active patches (1.5G15A) |
| Maxtena | datasheet | L1 active patches (M1227HCT) |
| Trimble | datasheet | Bullet III and similar active patches |
| NovAtel (Hexagon) | product brief | GAJT-AE-N / GAJT-410ML commercial CRPA reference (40-50 dB suppression spec) |
| Septentrio / M3 Systems | product brief | Commercial CRPA references |

Search vendor docs using prefix + keywords: e.g., `"PG060" Vivado matrix inverse`, `"AD9081" JESD204C 4 channel coherent`, `site:wiki.analog.com AD9081 ZCU102`.

## Synonym Expansion

CRPA-domain terms have multiple names in the literature. Generate search variants:

| User Term | Search Variants |
|-----------|----------------|
| CRPA | controlled reception pattern antenna, multiantenna GNSS receiver, GNSS antenna array, anti-jam GNSS antenna, adaptive antenna array GPS |
| Beamforming | spatial filtering, weight-and-sum, array-pattern synthesis |
| Power Minimization | PI (Power Inversion), null steering, blind interference suppression |
| MVDR | Capon beamformer, minimum variance distortionless response, optimal beamformer |
| LCMV | linearly constrained minimum variance, Frost beamformer |
| STAP | space-time adaptive processing, joint spatial-temporal filter |
| DOA | direction of arrival, AOA, angle of arrival, bearing estimation |
| Carrier-phase bias (CRPA) | beamformer-induced phase distortion, spatial-filtering phase bias, group-delay distortion |
| Calibration | manifold characterization, steering-vector estimation, array-response measurement, gain/phase mismatch correction |
| Self-calibration | blind calibration, online calibration, GNSS-based array calibration |
| Mutual coupling | element coupling, S-parameters between elements, port-to-port isolation |
| Element | radiator, antenna port, channel (when paired with RX chain) |

## Known Repositories

Search these by name first — they are the established open-source array-DSP projects:

| Name | URL | Language | License | Notes |
|------|-----|----------|---------|-------|
| gnss-sdr/gr-dbfcttc | `github.com/gnss-sdr/gr-dbfcttc` | C++/CMake | **NO LICENSE** | CTTC GNU Radio digital beamformer for GNSS; companion to Fernández-Prades 2016 Proc. IEEE; abandoned 2016 but architecturally canonical. Treat as research-only; clean-room reimplement; email Fernández-Prades before reuse. |
| Xilinx/Vitis_Libraries | `github.com/Xilinx/Vitis_Libraries` | C++/HLS | Apache-2.0 | `solver/L1/include/hw/` ships Cholesky/QR/SVD HLS kernels (`cholesky.hpp`, `cholesky_cfloat.hpp`, `cholesky_inverse.hpp`, `qrd.hpp`, `qrf.hpp`, `qr_inverse.hpp`, `back_substitute.hpp`) — direct match for 4×4 complex Hermitian inverse in adaptive-beamforming weight solve |
| analogdevicesinc/hdl | `github.com/analogdevicesinc/hdl` | Verilog/Tcl | Per-file mostly ADI BSD-equivalent | `projects/ad9081_fmca_ebz/zcu102/` is the complete reference Vivado project for AD9081 + ZCU102 + JESD204C 4-channel coherent RX |
| analogdevicesinc/pyadi-iio | `github.com/analogdevicesinc/pyadi-iio` | Python | ADI BSD | Python control library for ADI hardware including AD9081 — DDC/NCO/DMA bring-up |
| morriswmz/doatools.py | `github.com/morriswmz/doatools.py` | Python | MIT | MVDR / MUSIC / root-MUSIC / ESPRIT + Cramér-Rao bounds. Best Python reference for DOA estimation + analytical-baseline beamforming. |
| zhiim/doa_py | `github.com/zhiim/doa_py` | Python | MIT | Lighter-weight pip-packaged DOA toolkit; ULA / UCA + broadband DOA. Pairs with doatools.py for quick-start array-geometry experiments. |
| ihalhashem/CRPA-Enabled-Airborne-GNSS-Anti-Jamming | `github.com/ihalhashem/CRPA-Enabled-Airborne-GNSS-Anti-Jamming` | MATLAB | MIT | 2-element MVDR CRPA STK study at GPS L1; useful as link-budget baseline only |

Also search: `topic:beamforming language:python`, `topic:array-signal-processing`, `topic:doa-estimation`, `"CRPA" GNSS`, `"adaptive beamforming" GNSS`.

## Foundational References

These six classical-array-DSP works appear in 3+ seed papers' bibliographies and form the canonical theoretical backbone. Cite as `foundational` flag in ranking.

| Reference | Year | Notes |
|-----------|------|-------|
| Capon, "High-Resolution Frequency-Wavenumber Spectrum Analysis," Proc. IEEE 57(8):1408-1418 | 1969 | **Origin of MVDR.** ~5728 GScholar cites. Read before any MVDR implementation. |
| Frost, "An Algorithm for Linearly Constrained Adaptive Array Processing," Proc. IEEE 60(8):926-935 | 1972 | **LCMV origin.** Constraint formulation underlying Power Minimization. |
| Compton, "The Power Inversion Adaptive Array: Concept and Performance," IEEE Trans. AES | 1979 | PI concept underlying Power-Min CRPA; cited explicitly in Bamberg 2023 |
| Compton, *Adaptive Antennas: Concepts and Performance* (textbook) | 1988 | Old but clear; classroom-level introduction |
| Van Veen & Buckley, "Beamforming: A Versatile Approach to Spatial Filtering," IEEE ASSP Magazine 5(2):4-24 | 1988 | **Canonical primer.** ~6327 GScholar cites. Read first. |
| Van Trees, *Optimum Array Processing* (textbook) | 2002 | "The bible." Comprehensive; reference for derivations |

Recent foundational anchors (still cited heavily):
- **Cuntz/Konovaltsev/Meurer 2016 Proc. IEEE 104(6):1288-1316** — practical-overview anchor; multiantenna GNSS receivers (paywalled at IEEE)
- **Fernández-Prades/Arribas/Closas 2016 Proc. IEEE 104(6):1207-1220** — robust GNSS array signal processing; companion paper to gr-dbfcttc

## Platform Matching

When ranking results, match against these platform hierarchies:

| Target | Close Equivalents | Distant (still useful) |
|--------|-------------------|----------------------|
| ZCU102 + AD9081 + JESD204C | AD9082 (single-MxFE variant, same JESD204C topology), ZCU111 RFSoC (integrated converters), ZCU216 RFSoC (Gomes 2024+2025) | Older Xilinx 7-series + AD9361, ADRV9009/26 on AD-FMCOMMS (architecturally similar coherent multi-channel) |
| Coherent multi-channel | HMC7044 clock distribution + JESD204C SYSREF/LEMC + RFPLL | Older AD9528, JESD204B (deterministic latency tighter on -C) |
| 4-element L1 patch array | Square 2×2 λ/2, triangular + center (1+3), L-shape | 3-element minimal arrays, 8+ element arrays (different DOF regime) |

Key resources to look for in FPGA implementations:
- **DSP48 slices** — covariance estimator MAC chains, beamformer weight application
- **Block RAM (BRAM)** — sample buffers for block-based covariance estimation
- **HLS Cholesky / QR pipelines** — Vitis_Libraries `solver/` kernels are the canonical reuse
- **Clock frequency** — JESD204C framer rate (typically 245.76 MHz or higher)
- **Inter-channel coherence** — SYSREF distribution, sync pulse handshake, deterministic latency

## Code Search Limitations

Inherited from `gnss-signal-processing-soc.md`: GitHub code search has near-zero coverage for VHDL/Verilog. Do not rely on `gh api search/code` for HDL discovery; use repo search + directory inspection.

Additionally for this domain:
- **CRPA-specific code is sparse.** The civilian open-source landscape has essentially one canonical project (`gr-dbfcttc`) which is abandoned at source. Most published CRPA work has no companion repo. For any FPGA CRPA implementation, reuse Vitis_Libraries `solver/` kernels rather than searching for adaptive-beamforming-specific HDL.
- **Python toolkits are MATLAB-flavored.** doatools.py and doa_py both follow Van Trees / Stoica notation conventions; converting their outputs to GNSS-tracking-loop conventions requires explicit attention to column-vector / row-vector / Hermitian discipline.

## Domain-Specific Ranking Notes

- **Civilian vs military divide is large.** Most operational CRPA work (especially STAP-on-GPS, anti-jam in defense systems) is classified or unpublished. Civilian open literature is dominated by ~3 academic groups (DLR, Ohio State / NavSys, Calgary / Profound Positioning) plus the CTTC/GNSS-SDR open-source angle. When a search returns "no results," check whether the question is on the civilian side of the divide.
- **DLR Bamberg lineage is the bias-and-calibration backbone.** Bamberg/Meurer 2019 → Bamberg/Konovaltsev/Meurer 2020 → 2022 → 2023 NAVIGATION → 2023 ION GNSS+. Forward citers of Bamberg 2023 are the most-recent SOTA for carrier-phase bias work.
- **MDPI quality variance in this domain is real.** ZCU216 hardware-implementation papers (Gomes 2024+2025), beam-steering vector tracking (Burchfield 2025), and wideband-interference (Xu 2024) are well-executed. Other MDPI hits are not. Check citation counts and reproducibility evidence (published JSON / repo / data).
- **Originality probe: closed-loop classifier-driven CRPA mode selection** — civilian open literature has zero published work as of 2026-05 closing the loop between a real-time jammer-type classifier and CRPA adaptive-mode-selection switching. Verified across academic literature, GitHub, and citation-network searches. Adjacent work exists (Wu/Closas 2025 NAVI federated learning is classifier-only; Burchfield 2025 closes a DOA→beam-steering loop, not classifier→mode; Liu 2019 is classifier-only). If a session finds civilian work in this gap, it is news; re-validate the gap when planning publication-targeting research.
- **Carrier-phase bias is the dealbreaker for survey-grade CRPA applications.** Bamberg 2023 quantifies bounds; O'Brien/Gupta 2010 (zero-bias filter) and Li/Wang 2023 (robust phase compensation) propose mitigation. For non-survey applications (anti-jam for navigation), bias is a documented limitation rather than a hard blocker.
- **Self-cancellation is the architecture-level pitfall.** When a jammer's DOA aligns with one of the GPS SVs in view, naive PM/MVDR can attempt to null both. Quiescent-pattern constraints in the optimization mitigate this; not all implementations include them.
- **4 elements is a hard ceiling on suppression count.** Spatial-only beamforming with 4 elements gives DOF = 3 (can null 3 jammers max). STAP with M·L weights extends DOF but introduces wideband / temporal-correlation coupling. Do not over-claim suppression count for 4-element work.
