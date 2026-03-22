# Research Question Decomposition

How to break a research question into searchable sub-questions and a search strategy.

## Method

Given a research query, decompose it along these axes:

### 1. Identify the Domain
What field does this question belong to? Examples: GPS/GNSS signal processing, RF front-end design, digital communications, radar signal processing.

The domain determines which conferences, journals, and communities to search.

### 2. Identify the Implementation Target
What is the user building on? Examples: Zynq SoC, Artix-7 FPGA, software-only (Python/MATLAB), ASIC.

The implementation target determines which code repositories, vendor app notes, and reference designs are relevant.

### 3. Identify the Specific Problem
What exactly needs to be solved? Break into constituent parts. Example for "coarse and fine acquisition of GPS carrier signal":
- Coarse acquisition: code phase search, Doppler frequency search, detection threshold
- Fine acquisition: code phase refinement, carrier frequency estimation
- Carrier tracking: PLL/FLL/DLL design, Costas loop, lock detection

### 4. Cross Axes to Generate Sub-Questions

For each combination of (problem component) × (question type), generate a sub-question:

**Algorithm-level:**
- What are the standard approaches? (serial search, parallel code phase, parallel frequency space)
- What are the tradeoffs? (speed vs. resource usage vs. sensitivity)
- What's the state of the art? (recent papers, novel approaches)

**Implementation-level:**
- What exists in RTL? (open-source VHDL/Verilog implementations)
- What requires a CPU? (navigation solution, almanac parsing)
- Where's the PL/PS boundary? (what goes in fabric vs. ARM on Zynq)

**Integration-level:**
- What's the signal chain? (ADC → acquisition engine → tracking loops → navigation solution)
- What interfaces are needed? (AXI, DMA, interrupt)
- What are the resource constraints? (DSP48 slices, BRAM, clock frequency)

### 5. Identify Known Sources

For the domain, identify:
- **Conferences:** ION GNSS+, IEEE PLANS, IEEE/ION POSITION LOCATION AND NAVIGATION SYMPOSIUM, IEEE AESS
- **Journals:** IEEE Transactions on Aerospace and Electronic Systems, IEEE Access, Navigation (ION journal)
- **Trade publications:** Inside GNSS, GPS World
- **Vendor sources:** Xilinx/AMD app notes (XAPP, UG), Analog Devices (RF front-end)
- **Known authors/groups:** If any are known, seed the citation tracer

### 6. Generate Search Queries

Start broad (2-3 words), then narrow based on what comes back. See `search-strategy.md` for query construction rules.

Each role gets its own queries — don't duplicate across roles:
- **ieee-searcher:** Academic terms, conference/journal names, author names
- **web-searcher:** Vendor terms (Xilinx, Vivado), tutorial/app note language, blog-style phrasing
- **code-searcher:** Project names, language filters, topic tags
- **citation-tracer:** Seed paper titles/DOIs from ieee-searcher results

## Anti-Patterns

- **Don't use the full research question as a search query.** "Ways of doing coarse and fine acquisition of the GPS carrier signal using Zynq SoC and synthesizable VHDL" returns nothing useful. Break it apart.
- **Don't search for the implementation target without the algorithm.** "Zynq VHDL" is useless. "GPS acquisition FPGA" is useful.
- **Don't duplicate queries across roles.** Each role has its own search space. The ieee-searcher searches IEEE Xplore; the web-searcher searches blogs and app notes. They should not run the same queries.
- **Don't assume the user's terminology matches the literature.** "Fine acquisition" might be called "code phase refinement" or "narrow correlator" in papers. Generate synonym variants.

## Output

Produce a research plan per `schemas/research-plan.json`:
- `core_question`: The user's query verbatim
- `domain`: Identified domain
- `implementation_target`: Identified target platform
- `sub_questions`: Array of decomposed sub-questions with assigned roles and priorities
- `search_strategy`: Known conferences, journals, authors, repos, vendor sources
- `effort_level`: targeted / focused / broad / field_mapping
- `roles_to_execute`: Which roles to run based on effort level
- `execution_order_notes`: Any sequencing dependencies
