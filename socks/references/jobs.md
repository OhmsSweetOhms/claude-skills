# Job Slots — governing Vivado build/sim concurrency

`scripts/socks_jobs.py` is a weighted-slot job governor for everything
expensive SOCKS launches: Vivado synth/impl/OOC runs and xsim/xelab/xvhdl
runs. It replaces the copy-pasted single advisory flock that every caller
carried its own snippet of.

## Why it exists

The old scheme was one machine-wide advisory flock
(`${SOCKS_BUILD_CLASS_LOCK:-/tmp/socks-build-class.lock}`), taken by hand in
each runner script. Three failure modes fell out of it:

- **Sim class was unguarded.** Nothing serialized or budgeted xsim jobs, so a
  3-hour single-core integration testbench ran while more sims were launched
  on top of it. The box was oversubscribed and the long TB got slower.
- **No core or RAM budget.** The lock was binary — one holder or none. It
  could not express "two synth runs fit, three thrash," nor "ten small sims
  fit, but only five big ones."
- **Killed holders wedged the queue.** A flock dies with its holder, but any
  scheme built on lock *files* leaked state when an operator killed a job.

## Model

- State is a directory of PID-stamped slot files under
  `${SOCKS_JOBS_DIR:-/tmp/socks-jobs}`. **No daemon, no database.**
- All slot mutation happens while holding one flock'd meta lock,
  `<jobs-dir>/.meta.lock`, so admission is atomic across processes.
- A job is one slot file `slot-<class>-<pid>-<nonce>.json` recording
  `{pid, class, weight, label, cmd, started}`. Only `argv[0..2]` is recorded,
  with `argv[0]` reduced to a basename — the slot file is not a command log.
- **Admission rule:** `sum(live weights in class) + requested ≤ budget`.
- **Stale reclaim:** every scan deletes any slot whose PID is dead
  (`os.kill(pid, 0)` raises `ESRCH`). This is the fix for the wedged queue —
  an operator-killed job frees its slot the next time anyone looks.

### The two classes

| Class | Weight/job | Budget env | Default | Concurrency |
|-------|-----------|------------|---------|-------------|
| `build` | 8 | `SOCKS_BUILD_BUDGET` | 16 | 2 |
| `sim` | 2 | `SOCKS_SIM_BUDGET` | `nproc - 4` | ~10 |

**The build cap is set by RAM, not cores.** A Vivado synth+impl run peaks
around 10–12 GB; on a 30 GB workstation the third concurrent build pushes the
machine into swap and every run slows down together. Two is the safe cap —
the weight-8 / budget-16 arithmetic just encodes that.

**The sim budget leaves 4 cores unbudgeted** for the interactive session, the
editor, and the OS. xsim shards are largely single-core, so weight 2 is a
deliberate slight over-charge that keeps a little headroom.

Both budgets are env-overridable per invocation; raise `SOCKS_SIM_BUDGET` on
a bigger box, and lower `SOCKS_BUILD_BUDGET` to 8 to force strictly serial
builds on a RAM-tight machine.

## Usage

```bash
# Wrap a command in a sim slot (blocks until admitted)
python3 scripts/socks_jobs.py run --class sim --weight 2 --label my_tb -- \
    xsim my_tb_sim -R

# Wrap a Vivado build (also takes the legacy build-class flock, see below)
python3 scripts/socks_jobs.py run --class build --timeout 3600 -- \
    vivado -mode batch -source build.tcl

# What is running right now
python3 scripts/socks_jobs.py status

# Serialize around a phase without wrapping a command
python3 scripts/socks_jobs.py wait --class build --timeout 900
```

`run` blocks by default (poll every 5 s, one waiting line printed
immediately and then at most once a minute, naming who holds what). A
nonzero `--timeout` exits **75** (`EX_TEMPFAIL`) with a queue snapshot rather
than failing silently — callers should treat 75 as "retry later", not "the
job failed". `--timeout 0` (the default) waits forever, which is what a batch
gate wants.

`run` forwards SIGINT/SIGTERM to the child's process group, then releases the
slot and exits with the child's status (`128 + signal` when the child died on
a signal). A SIGKILLed wrapper leaves its slot behind, but the next scan
reclaims it.

## Reentrancy — `SOCKS_JOB_HELD`

A runner that already holds a slot may fan work out inside it without each
shard trying to take a second slot. `run` exports `SOCKS_JOB_HELD=<class>`
into the child; if that variable is already set, `run` execs the command
directly and acquires nothing.

That means a sharded gate takes **one** slot for the whole gate, sized for
the fan-out:

```bash
python3 scripts/socks_jobs.py run --class sim --weight 8 --label vec_gate -- \
    tb/run_vector_gates.sh --jobs 4      # the 4 shards inherit SOCKS_JOB_HELD
```

## xsim.py integration

`scripts/xsim.py` takes `--job-slot {auto,off}`, default `auto`. Under
`auto`, xsim.py re-execs itself through
`socks_jobs.py run --class sim --weight 2 --label <top>` unless
`SOCKS_JOB_HELD` is already set. `--job-slot off` runs completely unguarded
and never touches the slot directory — use it inside a runner that already
took a slot at a coarser granularity, or when debugging the governor itself.

This is orthogonal to `--debug {off,typical,all}`, which controls the
`xelab -debug` level and therefore how fast the sim runs. Gate runs that dump
no waveform should pass `--debug off` **and** take a slot.

## Migration note

`run --class build` **also** takes the legacy advisory flock on
`${SOCKS_BUILD_CLASS_LOCK:-/tmp/socks-build-class.lock}` for the child's
lifetime. Old-style runner scripts that flock that file by hand therefore
still exclude new-style build jobs, and vice versa, so the two schemes can
coexist while callers migrate. Do not remove that file's semantics.

Consequence while the shim is in place: the legacy lock is exclusive, so
build-class concurrency is effectively **1**, not the 2 the budget allows.
The budget arithmetic becomes the real cap only after the last hand-rolled
flock caller is migrated to `run --class build`.

Module runner scripts (`tb/run_vector_gates.sh`, OOC wrappers, and friends
in the module worktrees) are migrated per packet — not wholesale — so that
each migration is verified by that packet's own gate.

## Sharding guidance for gate suites

A slot budget only helps if the work is actually splittable. The pattern that
works (from the unified-engine q-03 ruling) is an
**interaction-versus-independent split**:

- **Independent cases** — per-PRN vectors, per-configuration rounding
  schedules, per-channel sweeps — have no shared state between them. Fan them
  out one shard per case, compile once, and aggregate the per-shard results.
- **Interaction cases** — anything probing cross-case state: channel
  scheduler arbitration, back-to-back re-arm, CSR access racing a running
  dump — must stay in one sequential shard, because the interaction *is* the
  device under test.

Applying that split to the unified-engine suite took it from **168 minutes**
sequential to **~60 minutes**, and it is the reason `--plusarg` case
selection exists in `xsim.py`.

Sizing rule: charge the parent runner a weight equal to its fan-out (`--jobs
N` → roughly weight `2N`, capped at the budget) and let the shards inherit
`SOCKS_JOB_HELD`. Charging weight 2 for a job that forks 8 shards
oversubscribes the box exactly as badly as no governor at all.

## Self-test

`python3 scripts/test_socks_jobs.py` — stdlib unittest, no Vivado needed,
runs in about 6 s. It covers admission arithmetic, stale reclaim after a
SIGKILL, the reentrancy short-circuit, SIGTERM slot release, and legacy-lock
exclusion in both directions.
