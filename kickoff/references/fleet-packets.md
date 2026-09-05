# Fleet packets — authoring guide

A fleet run launches many agents inside ONE session via the Workflow tool.
The kickoff artifact here is not a markdown document — it is the
`args` object handed to the project's fleet workflow script, plus the
pre-flight discipline around launching it. Historically, fleet runs have
died on the launch mechanics (args shape, budget misjudgment, self-matching
process patterns) far more often than on the engineering content — this
guide exists to make the launch boring.

## The script owns the policy — cite it, never restate it

If the project has a fleet workflow script (e.g.
`.claude/workflows/packet-fleet.js`), its header IS the spec: model/effort
assignments, parallelism caps, token soft/hard policy, agent ceiling,
worktree/pool rules, which packet kinds are refused. Those parameters are
operator-ruled; the spec lives in the script precisely so spec and code
cannot drift. Read the script header before authoring a run. Do not copy
its numbers into prompts, docs, or this file's margins — a second copy is
a future stale copy.

**Before declaring "no fleet script exists", sweep the whole workspace,
not just this repo.** Check this repo's `.claude/workflows/`, then the
sibling checkouts under the same work root, then the user-level workflow
dir. A proven, operator-ruled script in a sibling repo beats a fresh
hand-roll every time — a real fleet hand-authored its own runner while
the operator's ruled script sat in a sibling repo that had been used that
same morning, because "the project" was read as "this checkout".

If the sweep genuinely comes up empty, hand-rolling a fleet is a RULED
event, not a judgment call:

- **Present, then stop.** Do not invoke the Workflow tool in the same
  turn a hand-rolled script is first presented — the operator rules on
  the structure before anything launches. Propose-by-doing defeats the
  gate.
- **You now own the policy the script would have owned.** A hand-rolled
  fleet has no operator-ruled header, so the authoring must state
  explicitly: model and effort PER LANE (mechanical supervision, build
  watching, and recon go to cheap models — never default every lane to
  the session model; that miss is the single most expensive one on
  record), parallelism caps, token policy, and refusal rules. Silence on
  any of these is the defect, not a default.

## Authoring `args.packets[]`

Check the script's ARGS SHAPE comment for the authoritative fields. The
recurring shape, and what makes each field good:

- **id** — short descriptive label, self-describing on its face
  (`ring-drain-recon`, not `p3`). It becomes the log/label namespace.
- **kind** — recon / impl / board (or the script's enum). Respect the
  script's refusals: board-custody work is interactive-only and a fleet
  will (correctly) bounce it. Don't disguise a board packet as impl.
- **prompt** — self-contained task text. The agent has NO conversation
  context: name the repo areas, the deliverables, and the acceptance
  check inline or via contract_path. End multi-section asks with an
  enumerated deliverable list and a completeness self-check ("re-read
  your output; every numbered section present or explicitly marked NOT
  FOUND") — partial-section returns are a proven fleet failure mode.
- **contract_path** — prefer pointing at a committed contract file over
  restating a contract in the prompt. The file survives; the prompt copy
  drifts.
- **authority_pins** — every value the agent must NOT re-derive (address
  maps, levels, interface contracts), cited to source commits. Re-derived
  "authoritative" values are how decommissioned addresses come back from
  the dead.
- **physical_state** — for any packet whose work touches board-adjacent
  facts, supply the physical-state block the agent must echo back
  verbatim. No block supplied → the agent should return blocked, not
  guess.

## The ruling boundary

Fleet agents cannot block on a human. Anything that would be a
stop-and-ask in an interactive packet returns as a structured
`blocked {question, options, lean}` instead. Author packets expecting
this: a blocked return is a SUCCESSFUL return. The cycle is:

1. Run completes with `blocked[]` in its result.
2. Surface the questions to the operator, drafted with the lean intact.
3. Relaunch with `rulings: {<packet_id>: "<answer>"}` in args plus
   `resumeFromRunId` — unblocked packets replay from cache at zero cost;
   only ruled-on packets re-run.

Never resolve a blocked question by editing the packet prompt to steer
around it — that converts an escalation into a silent guess.

## Pre-flight checklist (each item has eaten a real run)

1. **Args are actual JSON values, not a stringified blob.** A stringified
   packets array reaches the script as one string and dies on
   "no packets supplied". Well-built scripts normalize, but verify yours
   does before relying on it.
2. **Smoke before fleet.** First invocation of a new or edited script (or
   a new packet shape): ONE representative packet, ideally a cheap recon
   kind. Schema mismatches, path assumptions, and label collisions all
   surface at single-packet cost instead of fleet cost.
3. **State the budget picture at launch.** Log/announce the intended
   token spend against the script's policy and any "+Nk" turn directive
   BEFORE launching. Two real fleets starved because the shared pool was
   misjudged; saying the number out loud is the cheap fix.
4. **Count the fan-out.** Packets × verify-multiplier against the
   script's agent ceiling. If the math exceeds the ceiling, decide the
   degradation (fewer packets vs. thinner verification) yourself — don't
   let the ceiling decide silently mid-run.
5. **No process-kill patterns in packet prompts.** If a packet must
   manage processes, scope kills to recorded PIDs; broad pkill/pgrep
   patterns have self-matched the orchestration and killed sibling runs.
6. **After the run: read the journal before diagnosing.** An empty or
   odd result gets checked against the run's journal (per-agent actual
   returns) before any theory about the packets themselves.
7. **State model/effort per lane out loud.** Whether the script assigns
   them or you do, the launch announcement names which model tier each
   lane runs on and why. An expensive session model silently supervising
   mechanical builds is a budget leak the operator should never have to
   catch mid-run.
8. **Delegate pre-launch recon to cheap agents.** Interface checks, args
   archaeology, tool-PATH probing — the reconnaissance that shapes the
   packets belongs on cheap subagents returning conclusions, not inline
   in the authoring session's context.

## What the fleet returns is kickoff input for the NEXT round

Delivered work, refuted work (a full deliverable, with evidence), blocked
questions, and unverified tails are all inputs to the next emission:
rulings feed the resume round; unverified tails become explicit packets in
the next run rather than silently assumed green. Banking those results is
the orchestrator's job (threads/handoff discipline), not the fleet's.
