# Deliverable template + worked example

## Template

Fill each section with **exact net names and `file:line`**. Omit a section only
if it genuinely doesn't apply (say so).

```
# Datapath map — <design> @ <commit SHA>

## A. Handshake / backpressure chain (per instance)
| Stage | Driver valid/wr_en | Consumer ready | Real backpressure? | file:line |
Flag every free-running / no-ready stage as a SILENT-DROP point.

## B. Stuck-signal dependency chain
<stuck net> = <boolean/FSM expr> (file:line). High iff FSM in state X (file:line).
Reach/hold X requires events Y (file:line), Z (file:line). Y produced by … Z by …

## C. Reset network map
| Reset | source → gate/logic → sinks | per-run or power-on-only? | file:line |

## D. Re-arm / acceptance trigger chain
To accept the next transaction: <drain/EOT/xfer_req/reset events + file:line>.
Does the runtime/software reset clear the accepting path UNCONDITIONALLY, or does
some internal RAM/counter/sub-state sit outside its domain and persist? (crux)

## E. Multi-instance coupling
Independent (separate bases/mem/reset) or shared? One-or-all? Deadlock risk?

## F. Provenance
git log <base>..<SHA> -- <suspect paths>: did the suspect change, or predate?

## G. Root-cause hypothesis + CONFIRM/KILL test
Ranked hypothesis (confidence). CONFIRM = <observation>. KILL = <observation>.
Prefer already-instrumented (mark_debug / existing counters) probes.
Flag: static-trace inference — needs HW confirmation before building the fix.
```

## Worked example (condensed)

*A two-ring capture design wedged: on a repeated capture without reboot, the
data-offload's `s_axis_tready` sat at 0 for the whole active window on both
rings, 0 bytes reached DDR, and only a full reboot recovered it. The map:*

- **A.** The packer (`util_cpack2`) has **no `tready` port at all** — a
  silent-drop point; downstream backpressure can't stall it, it just discards.
  The FIFOs and offload downstream negotiate normally.
- **B.** `offload_sready = wr_ready`, high **only in `WR_STATE_WR`**
  (`data_offload_fsm.v:280`). Reaching/holding WR needs the read side to drain
  and cross `wr_rd_response_eot` back (`:194-197`), built from
  `rd_last_eot`/`rd_outstanding` (`:136,268-269`).
- **C.** The software reset (`src/dst_sw_resetn`) forces the offload **FSM** to
  IDLE unconditionally (`data_offload_fsm.v:203-209,249-255`) — **per-run**. But
  the internal storage RAM's reset is wired to **power-on-only** nets
  (`data_offload_bd.tcl:102-105`).
- **D. (the crux)** The runtime reset clears the FSM but **never reaches the
  storage RAM** (`util_do_ram`): its `rd_req_cnt`/`rd_active`/`wr_request_ready`
  (`util_do_ram.v:200-211`) reset only on power-on. Stale bookkeeping from the
  prior run survives every software reset **and** every firmware reload, blocking
  the next run's read handshake (`rd_request_ready = ~rd_req_cnt[1]`) → FSM never
  sustains WR → `sready=0`. Cleared only by a physical reboot.
- **E.** Two independent IP instances (separate bases, separate DDR ports,
  separate storage RAM) → the *same per-instance defect* hits both at once,
  matching "both rings wedged, no cross-coupling."
- **F.** The suspected recent change (a FIFO added the prior hop) touched only
  the FIFO/reset-gating layer — the offload/storage RTL was untouched; the wedge
  **predates** it.
- **G.** Hypothesis (high confidence, static-trace): storage-RAM reset scope gap.
  **CONFIRM** = `rd_req_cnt`/`rd_active` non-zero entering the 2nd run while the
  FSM shows a clean IDLE. **KILL** = storage state clean but `sready` still 0.
  The confirm probes (`util_do_ram.v:213-222`) are **already `mark_debug`** but
  not in the active ILA list → attach-only, no rebuild. Fix (deferred to after
  confirm): re-scope the storage-RAM reset into the software-reset domain.

**Why this map won:** the symptom was at the offload (`sready=0`), but §D
relocated the cause to a *sibling block's reset scope* — the "software reset
doesn't reach every stateful element" pattern — and exonerated both the
firmware and the recently-changed FIFO. The confirm test cost nothing extra
because the deciding signals were already instrumented.
