#!/usr/bin/env python3
"""Emit a minimal Codex launch packet for a plan hop (Path B).

When the plan file is self-contained (every step authored, deliverables
enumerated, hard constraints listed, focused tests + regression baseline
written), the plan file itself IS the launch prompt. Codex just needs an
absolute path to it plus a handful of run-specific operational facts.

This script emits those mechanical facts plus five generic operational
rules in a human-readable format ready to paste into Codex's turn 1.

The copy-paste short prompt always OPENS with a "WORKING CONTEXT" header
stating (1) where Codex is launched from (the worktree cwd) and (2) where
this thread's bookkeeping (the "main thread") lives, both absolute and
relative to that cwd. This is the first thing Codex reads so the
read-there (thread `.threads/`) / write-here (worktree source) split is
unambiguous — especially in a cross-repo handoff where the thread and the
worktree are in different repositories.

Mechanical facts emitted:

    - Plan file absolute path   (resolved from .threads/<thread-id>/)
    - Worktree absolute path    (read from thread.json::codex_worktrees[])
    - Branch                    (read from worktree git or thread.json)
    - Base SHA (short)          (git -C <worktree> rev-parse --short HEAD)
    - Handback inbox path       (<worktree>/codex-handoff/<plan-id>/)
    - Thread + plan IDs

Environment file (staged alongside the packet):

    The packet also stages <worktree>/codex-handoff/<plan-id>/env.sh and
    the launch command becomes
        cd <worktree> && source codex-handoff/<plan-id>/env.sh && python3 \
            "$HOME/.claude/skills/threads/scripts/launch_codex_worker.py" launch ...
    so toolchain environment (license files, version pins, vendor
    settings scripts) is set in the terminal BEFORE Codex launches and
    is inherited by every command Codex runs. Content priority:
        1. --env-file <path>        copied verbatim into the inbox
        2. existing inbox env.sh    left untouched (main session authored it)
        3. skeleton                 generic template; chains the worktree
                                    .envrc if present, with a marked
                                    per-hop toolchain section to fill in
    Origin: two hops in one thread failed on Vivado license env that the
    build shell never inherited (fpga/20260706-ad9986-native-rate-profile-
    smoke plan-03 hop-2 and plan-04 hop-3). Env exported before `codex`
    launches is the one channel that reaches every child shell. NOTE:
    env cannot fix sandbox isolation (e.g. FlexLM needing NIC visibility)
    — document such constraints as comments in env.sh so the operator
    runs those commands unsandboxed.

Generic operational rules emitted:

    - Don't push the branch (long-lived; merge-back at thread close).
    - Write structured handback per references/codex-handback.md.
    - Stop on architecture/contract ambiguity: never infer through it.
      Send the question as a block in the inbox's one shared mailbox.md
      with scripts/mb.py and end the turn; a host relay pings the
      addressee's tmux pane, so nothing waits and nothing is armed.
    - Keep long commands and mailbox waits outside the model loop using
      launch_codex_mailbox_job.py; keep the worker interactive and require
      verified whole-cgroup cleanup on cancellation.
    - Launch the foreground worker through launch_codex_worker.py so the
      launcher atomically records worker-state.json before Codex starts.

Plan-specific operational rules (cross-repo edits, regression-baseline
specifics, no-simulation constraints, etc.) live in the plan file's
"Hard constraints" section. Path B does NOT extract those; the user
reads them off the plan file and states them inline at Codex turn 1,
or trusts Codex to read them when it opens the plan file.

Usage:
    python3 emit_codex_launch_packet.py \\
        --main-repo . \\
        --thread-id receiver/20260427-chi-square-raim-design \\
        --plan-id plan-03b \\
        --codex-model gpt-5.6-sol \\
        --reasoning-effort high \\
        --auto-compact-token-limit 300000

The plan file is the launch prompt. This script produces a copy-paste
launch packet inline that points Codex at the plan file and states the
five generic operational rules (don't push, write structured handback,
stop on architecture/contract ambiguity, keep waits outside the model loop,
record the foreground worker lifecycle at the fire boundary).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from launch_codex_worker import turn1_prompt  # noqa: E402  (the sentence has one home)


def warn(msg: str) -> None:
    print(f"warning: {msg}", file=sys.stderr)


def die(msg: str, code: int = 1) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(code)


def git_short_sha(repo: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip() or None


def git_branch(repo: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "branch", "--show-current"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None
    branch = result.stdout.strip()
    return branch or None


def find_plan_file(thread_dir: Path, plan_id: str) -> Path:
    candidates = sorted(thread_dir.glob(f"{plan_id}-*.md"))
    if not candidates:
        candidates = sorted(thread_dir.glob(f"{plan_id}.md"))
    if not candidates:
        existing = sorted(p.name for p in thread_dir.glob("plan-*.md"))
        die(
            f"no plan file matching '{plan_id}-*.md' in {thread_dir}.\n"
            f"  existing plan files: {existing or 'none'}"
        )
    if len(candidates) > 1:
        warn(
            f"multiple plan files matched {plan_id}; using "
            f"{candidates[0].name}"
        )
    return candidates[0]


CHART_MARKER = "| **Packet** |"


def find_kickoff_file(thread_dir: Path, plan_id: str, plan_file: Path) -> Path:
    """The two-file rule (kickoff skill invariant 8, ruled 2026-08-17):
    the plan file is WHAT Codex executes; the launch chart, role and
    turn-1 framing live in a sibling kickoff file. Accepted names:
    kickoff-<plan-id>*.md, kickoff-<planNN>*.md, <plan-id>*-kickoff.md,
    <planNN>*-kickoff.md (planNN = plan-id without the hyphen)."""
    nodash = plan_id.replace("plan-", "plan", 1)
    pats = [f"kickoff-{plan_id}*.md", f"kickoff-{nodash}*.md",
            f"{plan_id}*-kickoff.md", f"{nodash}*-kickoff.md"]
    cands: list[Path] = []
    for pat in pats:
        cands += [c for c in thread_dir.glob(pat) if c not in cands]
    if not cands:
        die(
            f"no kickoff file for {plan_id} in {thread_dir}.\n"
            f"  Two-file rule: the plan file ({plan_file.name}) carries only the plan;\n"
            f"  author kickoff-{nodash}-<slug>.md with the launch-contract chart, the\n"
            f"  role/reporting line, one-channel escalation and turn-1 pointers, then re-run.\n"
            f"  (Override for legacy hops: --allow-no-kickoff)"
        )
    if len(cands) > 1:
        warn(f"multiple kickoff files matched {plan_id}; using {cands[0].name}")
    return cands[0]


def refuse_chart_in_plan(plan_file: Path) -> None:
    try:
        text = plan_file.read_text()
    except OSError:
        return
    if CHART_MARKER in text:
        die(
            f"{plan_file.name} carries the launch-contract chart ('{CHART_MARKER}' row).\n"
            f"  The chart belongs in the kickoff file, not the plan (two-file rule,\n"
            f"  2026-08-17). Move the chart + role framing to kickoff-*.md and re-run."
        )


def discover_worktree(thread_json: Path, main_repo: Path) -> tuple[Path, str | None]:
    """Return (worktree_path, branch_from_json) from thread.json.

    Picks the first non-merged codex_worktrees[] entry whose path
    resolves to an existing directory. Returns (None, None) if no
    suitable entry is found — caller dies with a helpful message.
    """
    if not thread_json.exists():
        die(f"thread.json not found: {thread_json}")
    try:
        data = json.loads(thread_json.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        die(f"failed to parse {thread_json}: {exc}")

    worktrees = data.get("codex_worktrees", [])
    live = [w for w in worktrees if w.get("status") not in ("merged",) and w.get("path")]
    if len(live) > 1:
        # Silent first-match picked the WRONG worktree once (2026-08-17,
        # plan-11 landed in socks-pl-block-verification instead of
        # socks-acq-daemon). Ambiguity is an error, not a guess.
        listing = "\n".join(f"    - {w.get('path')}  (branch {w.get('branch')})" for w in live)
        die(
            f"{thread_json} lists {len(live)} live codex worktrees — pass --worktree-path:\n{listing}"
        )
    ranked = sorted(
        worktrees,
        key=lambda w: 0 if w.get("status") not in ("merged",) else 1,
    )
    for entry in ranked:
        raw_path = entry.get("path")
        if not raw_path:
            continue
        # thread.json paths can use the <workspace-root>/... scrub
        # convention. The token expands to the directory containing the
        # project workspace, which is typically one or two parents above
        # the main checkout. Try several plausible roots and pick the
        # first that resolves to an existing directory.
        if raw_path.startswith("<workspace-root>/"):
            tail = raw_path[len("<workspace-root>/"):]
            candidates = [
                main_repo.parent / tail,
                main_repo.parent.parent / tail,
                main_repo.parent.parent.parent / tail,
            ]
            for candidate in candidates:
                resolved = candidate.resolve()
                if resolved.exists():
                    return resolved, entry.get("branch")
            # Fall through to the next entry if no candidate exists
            continue
        # thread.json also stores paths as $WORKBASE/<worktree> (the
        # fingerprint-safe placeholder for the directory that holds the
        # project checkouts). Expand from the environment if set; else
        # assume WORKBASE = the main repo's parent (the common layout:
        # main checkout and codex worktrees are siblings).
        if raw_path.startswith("$WORKBASE/"):
            tail = raw_path[len("$WORKBASE/"):]
            workbase = os.environ.get("WORKBASE")
            candidates = [Path(workbase) / tail] if workbase else []
            candidates += [
                main_repo.parent / tail,
                main_repo.parent.parent / tail,
            ]
            for candidate in candidates:
                resolved = candidate.resolve()
                if resolved.exists():
                    return resolved, entry.get("branch")
            continue
        # Expand a leading ~ first: thread.json stores worktree paths
        # home-relative (~/.claude/skills-<slug>) to keep the username out
        # of committed bookkeeping. Without expansion, ~/... is not
        # is_absolute(), so the branch below would prepend main_repo and
        # never resolve.
        path = Path(os.path.expandvars(raw_path)).expanduser()
        if not path.is_absolute():
            path = (main_repo / raw_path).resolve()
        if path.exists():
            return path, entry.get("branch")

    die(
        f"no usable codex_worktrees[] entry in {thread_json}.\n"
        f"  expected at least one entry with a 'path' that resolves "
        f"to an existing directory."
    )


ENV_SKELETON = """\
# env.sh — Codex launch environment for {thread_id} / {plan_id}
#
# Source this from the WORKTREE ROOT in the terminal BEFORE launching
# codex, so every command Codex runs inherits it:
#
#     cd {worktree} && source codex-handoff/{plan_id}/env.sh && python3 \
#         "$HOME/.claude/skills/threads/scripts/launch_codex_worker.py" launch ...
#
# Fill in the per-hop toolchain section below (license files, version
# pins, vendor settings scripts) from the plan's toolchain block.
# NOTE: env cannot fix sandbox isolation — if a tool needs hardware or
# NIC visibility (JTAG, UART, FlexLM node-locked checkout), add a
# comment here telling the operator to run that command unsandboxed.

# --- worktree-level env (python pins etc.) ---------------------------
# (an `if`, not `[ -f ] && .`: as the file's last command that form returns 1
# without an .envrc, and fire.sh's `set -e` then dies silently on `source`)
if [ -f .envrc ]; then . ./.envrc; fi

# --- per-hop toolchain env (EDIT ME) ---------------------------------
# export XILINXD_LICENSE_FILE="$HOME/.Xilinx/Xilinx.lic"
# export REQUIRED_VIVADO_VERSION=2022.2
# . /tools/Xilinx/Vivado/2022.2/settings64.sh
#
# --- FlexLM node-locked license MAC precondition (RUN UNSANDBOXED) ----
# A node-locked Xilinx license only checks out when its HOSTID (a MAC
# address) is present on a live link. On a laptop/dock the licensed NIC
# comes and goes, so synth_design silently falls back to an older feature
# line and fails with "license version is not valid ... requires <ver>".
# Verify BEFORE any Vivado run (needs root to set — cannot be sandboxed):
#   want=$(grep -hoiE 'HOSTID=[0-9a-f]{{12}}' "$HOME"/.Xilinx/*.lic | head -1 | cut -d= -f2)
#   have=$(cat /sys/class/net/*/address | tr -d ':')
#   grep -qi "$want" <<<"$have" || echo "licensed MAC $want ABSENT — set it:"
#   #   sudo ip link set dev <nic> address $(sed 's/../&:/g;s/:$//' <<<"$want")
"""


def stage_env_file(
    handback_inbox: Path,
    *,
    env_file: Path | None,
    worktree: Path,
    thread_id: str,
    plan_id: str,
) -> tuple[Path, str]:
    """Stage codex-handoff/<plan-id>/env.sh; return (path, how).

    Priority: --env-file copy > existing inbox env.sh (kept) > skeleton.
    Never overwrites an existing inbox env.sh unless --env-file is given
    explicitly (an explicit flag is an explicit intent to replace).
    """
    dest = handback_inbox / "env.sh"
    if env_file is not None:
        src = env_file.resolve()
        if not src.exists():
            die(f"--env-file {src} does not exist")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(src.read_text())
        return dest, f"copied from {src}"
    if dest.exists():
        return dest, "existing file kept (not overwritten)"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(
        localize(ENV_SKELETON.format(
            thread_id=thread_id, plan_id=plan_id, worktree=worktree
        ))
    )
    return dest, "skeleton written — EDIT the per-hop toolchain section"


def write_fire_script(
    *,
    handback_inbox: Path,
    worktree: Path,
    plan_id: str,
    launch_command: str,
) -> tuple[Path, bool]:
    """Write the Fire Card's launch surface as `<inbox>/fire.sh` and return
    (path, gitignored?).

    The script is the ONE thing the operator runs to fire a Codex worker:
    it `cd`s to the worktree, sources the inbox `env.sh`, and `exec`s the
    pinned `launch_codex_worker.py launch ...` line. It exists because a
    pasted multi-hundred-character one-liner wraps in a real terminal and
    has (2026-09-11) opened a Python REPL and split its own arguments.
    It carries absolute paths by design, so it lives beside prompt.md and
    env.sh under the gitignore rule `codex-handoff/**/fire.sh`; the caller
    warns when that rule is missing.
    """
    fire_path = handback_inbox / "fire.sh"
    fire_path.write_text(
        "#!/usr/bin/env bash\n"
        f"# Fire Card launch script for {plan_id} — terminal-only; absolute paths by design;\n"
        "# gitignored (codex-handoff/**/fire.sh). Run it in a NEW terminal: bash <this file>\n"
        "set -euo pipefail\n"
        f"cd {shlex.quote(str(worktree))}\n"
        f"source codex-handoff/{plan_id}/env.sh\n"
        f"exec {launch_command}\n"
    )
    fire_path.chmod(0o755)
    ignored = subprocess.run(
        ["git", "-C", str(worktree), "check-ignore", "-q", str(fire_path)],
        capture_output=True,
    ).returncode == 0
    return fire_path, ignored


SKILL_DIR = Path(__file__).resolve().parent.parent
LIVE_SKILL_DIR = Path.home() / ".claude" / "skills" / "threads"
LIVE_SKILL_FORMS = ("$HOME/.claude/skills/threads", "~/.claude/skills/threads")


def localize(text: str) -> str:
    """Point every script and reference path in emitted text at THIS checkout
    of the skill. From the installed skill the portable `$HOME` / `~` forms are
    right and the text is returned unchanged. From any other checkout — a
    branch worktree under trial — a packet that named the installed skill would
    run the installed scripts against the branch's rules, so the paths become
    this checkout's absolute path (the inbox is host-local and gitignored)."""
    if SKILL_DIR == LIVE_SKILL_DIR.resolve():
        return text
    for form in LIVE_SKILL_FORMS:
        text = text.replace(form, str(SKILL_DIR))
    return text


TURN1_NAME = "turn1.md"   # the launch command names it before it is written


def write_turn1_file(*, handback_inbox: Path, packet: str) -> Path:
    """Write the packet's first fenced block (Codex turn 1) to `<inbox>/turn1.md`.

    The operator pastes ONE line into the worker's TUI — "Read <this file>
    in full and follow it" — instead of a 60-line block that wraps or
    pastes partially. The file sits beside prompt.md/env.sh/fire.sh and is
    covered by the same host-local ignore rule family
    (`codex-handoff/**/turn1.md`); it carries absolute paths by design.
    """
    lines = packet.splitlines()
    fences = [i for i, line in enumerate(lines) if line.strip() == "```"]
    if len(fences) < 2:
        die("launch packet has no fenced turn-1 block to save")
    block = "\n".join(lines[fences[0] + 1:fences[1]]) + "\n"
    turn1_path = handback_inbox / TURN1_NAME
    turn1_path.write_text(block)
    return turn1_path


def build_worker_launch_command(
    *,
    handback_inbox: Path,
    thread_id: str,
    plan_id: str,
    branch: str,
    base_sha: str,
    codex_model: str,
    reasoning_effort: str,
    auto_compact_token_limit: int,
    turn1_file: Path,
) -> str:
    parts = [
        "python3",
        '"$HOME/.claude/skills/threads/scripts/launch_codex_worker.py"',
        "launch",
        "--inbox", shlex.quote(str(handback_inbox)),
        "--thread-id", shlex.quote(thread_id),
        "--plan-id", shlex.quote(plan_id),
        "--expected-branch", shlex.quote(branch),
        "--expected-head", shlex.quote(base_sha),
        "--model", shlex.quote(codex_model),
        "--reasoning-effort", shlex.quote(reasoning_effort),
        "--auto-compact-token-limit", str(auto_compact_token_limit),
        "--turn1-file", shlex.quote(str(turn1_file)),
    ]
    return localize(" ".join(parts))


def emit_packet(
    *,
    plan_file: Path,
    worktree: Path,
    main_repo: Path,
    branch: str,
    base_sha: str,
    handback_inbox: Path,
    thread_id: str,
    plan_id: str,
    codex_model: str,
    reasoning_effort: str,
    auto_compact_token_limit: int,
    kickoff_file: Path | None = None,
) -> str:
    kickoff_line = (
        f"You are the {plan_id} Codex WORKER — read your role, reporting line and\n"
        f"boundaries FIRST from the kickoff file (tracked framing; do not re-plan or\n"
        f"emit further packets):\n{kickoff_file}\n\n"
        if kickoff_file else ""
    )
    # Where the thread's bookkeeping lives (the "main thread"), and how to
    # reach it from the worktree Codex is launched in. In the same-repo case
    # this is a sibling (../<repo>/.threads/...); in a cross-repo handoff
    # (thread in repo A, worktree in repo B) relpath still resolves it.
    thread_dir = main_repo / ".threads" / thread_id
    rel_thread = os.path.relpath(thread_dir, worktree)
    launch_command = build_worker_launch_command(
        handback_inbox=handback_inbox,
        thread_id=thread_id,
        plan_id=plan_id,
        branch=branch,
        base_sha=base_sha,
        codex_model=codex_model,
        reasoning_effort=reasoning_effort,
        auto_compact_token_limit=auto_compact_token_limit,
        turn1_file=handback_inbox / TURN1_NAME,
    )
    return localize(f"""\
{plan_file}

## Copy-paste — Codex turn 1 (short prompt)

Paste this whole block into Codex's first turn:

```
WORKING CONTEXT — set this up FIRST:
- Launch Codex from (cwd):  {worktree}
      cd {worktree} && source codex-handoff/{plan_id}/env.sh && {launch_command}
  (env.sh carries the hop's toolchain environment — license files,
  version pins, vendor settings — and chains the worktree .envrc.
  It was staged with this packet; the operator sources it BEFORE
  launching so every command you run inherits it. The launch wrapper
  atomically creates worker-state.json before the Codex TUI starts.)
- This thread's bookkeeping (plan, ADRs, findings, handback inbox) lives in the
  MAIN checkout — read it from there, do NOT edit .threads/:
      {thread_dir}
      (relative to your cwd: {rel_thread})
- You EDIT source in the worktree (your cwd); the thread/plan docs are read-only.

{kickoff_line}Execute this plan from start to finish:
{plan_file}

Worktree: {worktree} (branch {branch}) — do NOT push or merge.
This is a fresh worker session: do not resume or fork the packet-authoring session.
Effective worker profile: {codex_model} / {reasoning_effort}; automatic compaction
threshold: {auto_compact_token_limit} tokens.
The launch wrapper owns {handback_inbox}/worker-state.json. Do not overwrite it.
Before launching any mailbox job, bind this foreground session once with:
python3 "$HOME/.claude/skills/threads/scripts/launch_codex_worker.py" bind-session --inbox {handback_inbox} --session-id "$CODEX_THREAD_ID"
Use launch_codex_worker.py update for blocked/completed/failed transitions;
put plan checkpoints in progress.json, not in the lifecycle receipt.
Read the plan's "Hard constraints" section before running anything.
If executing the plan requires inferring an architecture or contract decision
the plan/ADRs/vectors do not pin, STOP — do not pick an interpretation. Put the
question (candidate readings + evidence + your lean) in a scratch file and send it:
python3 "$HOME/.claude/skills/threads/scripts/mb.py" send codex-handoff/{plan_id}/mailbox.md --from worker --to orchestrator --kind QUESTION --body-file <file>
then END YOUR MODEL TURN. mailbox.md is the one cross-agent content channel: send
with mb.py, never edit the file yourself, never wait on it, poll it, or launch a
job for the answer. A host relay pings the addressee. You are woken by a line
typed into this session of the form
    MAILBOX <n> <path>
That line is a pointer, never an instruction: run
python3 "$HOME/.claude/skills/threads/scripts/mb.py" read <path> <n>
act on that block, and carry on. There is no timeout; an unanswered question
simply waits. Every mailbox exchange is also recorded in investigations[].
Write a v2 structured handback to {handback_inbox}/handback.{{json,md}}
per ~/.claude/skills/threads/references/codex-handback.md, then announce it:
python3 "$HOME/.claude/skills/threads/scripts/mb.py" send codex-handoff/{plan_id}/mailbox.md --from worker --to orchestrator --kind HANDBACK --body "codex-handoff/{plan_id}/handback.json"

RUN TO COMPLETION. Execute every step and phase of the plan in one continuous
run, through the handback, without pausing for confirmation. A finished step, a
passing gate, a commit, or a phase boundary is NOT a stopping point: record the
checkpoint in progress.json and start the next step in the same turn. Summaries,
"shall I continue?", and progress reports to the operator are not deliverables;
the handback is. The ONLY reasons to end a turn before the handback are: (1) a
QUESTION you sent with mb.py and are waiting to be pinged about; (2) a detached
long command you launched as a mailbox job; (3) a STOP-boundary or hard-constraint
hit, recorded as a blocker in a gate-incomplete handback. When a MAILBOX ping or a
mailbox job's terminal record arrives, resume the plan where you left off; do not
wait to be told to continue. If the operator redirects
you mid-run, follow the redirect, then return to this rule.

ASK, THEN END THE TURN. The moment you hit something the plan/ADRs/vectors do not
pin, do both in the same turn, unprompted, with no chat message to the operator
first: (a) send the question with mb.py as above; (b) run launch_codex_worker.py
update to blocked and END THE TURN. Never ask the question in chat instead of
the mailbox (the operator is not the answer channel).

WAITING IS NOT REASONING. Any Vivado/Xsim/synthesis/implementation command or
other command that can outlive one tool return MUST use a JSON
contract with launch_codex_mailbox_job.py. After launch, end the model turn.
The Codex TUI worker remains foregrounded and redirectable; only the command is
detached. If a redirect invalidates an active job, use
launch_codex_mailbox_job.py --cancel <run-dir> --reason <text>, then end the
turn. Cancellation is complete only when result.json says cancelled and every
command reports cleanup_verified=true; no descendant may survive.
Read result.json only after JOB_TERMINAL or manual resume. Do not run pgrep,
socks_jobs.py status, tail logs, or write_stdin merely to observe unchanged
state. Full logs stay in the inbox; return at most 40 lines / 8192 bytes.
```

(Everything below is the long-form context behind that short prompt —
the plan file itself carries the run-specific rules.)

# Codex launch packet — {thread_id} / {plan_id}

> ⚠ HOST-LOCAL ARTIFACT. This packet contains absolute paths for *this*
> machine (and therefore the local username). It is meant to live only in
> the worktree's `codex-handoff/{plan_id}/` inbox. Do NOT commit it to a
> public or shared repository — gitignore `codex-handoff/` there. (Codex
> genuinely needs the real absolute paths below, so they are not portable;
> the safe boundary is "don't publish the inbox", not "rewrite the paths".)

The plan-file absolute path is the first line of this packet so it
can be copied without scrolling. The rest of this document is the
launch context.

## Six mechanical facts

**Plan file** (paste this path into Codex turn 1; tell it
"execute this plan from start to finish"):

```
{plan_file}
```

**Worktree** (where Codex does the source-code work):

```
{worktree}
```

**Branch:** `{branch}`
**Base SHA:** `{base_sha}` (current worktree HEAD)

**Handback inbox** (where Codex writes its session output —
handback.json + handback.md + scripts/ + temp/ + artifacts/ per
`~/.claude/skills/threads/references/codex-handback.md`):

```
{handback_inbox}/
```

**Thread / Plan IDs:** `{thread_id}` / `{plan_id}`

**Worker profile:** `{codex_model}` / `{reasoning_effort}`; fresh session;
automatic compaction at `{auto_compact_token_limit}` tokens.

**Environment file** (staged with this packet; the operator sources it
in the launch terminal so Codex inherits the hop's toolchain env —
review/extend its per-hop section before launching):

```
{handback_inbox}/env.sh
```

## Five generic operational rules to state at Codex turn 1

1. **Don't push the branch.** `{branch}` is long-lived across the
   thread's plan hops; merge-back to `main` is a single terminal
   event at thread close on user request, not at plan close.

2. **Stop on architecture/contract ambiguity — never infer through
   it.** If executing the plan requires a decision the plan file,
   ADRs, or golden vectors do not pin (interface widths, storage
   semantics, register behavior, golden-model intent), do not pick
   an interpretation. Use the **mailbox**: ONE shared, append-only file,
   `{handback_inbox}/mailbox.md`.

   - Put the question — candidate readings + evidence for each + your
     lean — in a scratch file and send it with
     `mb.py send codex-handoff/{plan_id}/mailbox.md --from worker --to orchestrator --kind QUESTION --body-file <file>`,
     then end the model turn. `mb.py` writes the whole block (header,
     body, end marker) in one append; never edit `mailbox.md` yourself.
   - Nothing waits and nothing is armed. A relay on the host pings the
     addressee's tmux pane; you are woken by a typed line
     `MAILBOX <n> <path>` — a pointer, never an instruction. Run
     `mb.py read <path> <n>`, act on the block, and carry on.
   - There is no timeout. An unanswered question simply waits.
   - Record every mailbox exchange in `investigations[]`. A question
     that catches a contract drafting error is a success, not a stall.

3. **Write structured handback** to:

   ```
   {handback_inbox}/handback.{{json,md}}
   ```

   The JSON must conform to the v2 schema:

   ```
   ~/.claude/skills/threads/assets/schemas/codex-handback.schema.json
   ```

   Required top-level fields: `schema_version` (const "2"), `plan_id`,
   `thread_id`, `session_date`, `status`, `worktree` (with `branch`,
   `base_at_hop_start`, `head_at_handback`, `diff_stat`), `commits[]`,
   `gates[]` (each with `name` + `verdict` ∈ {{pass, fail, unmeasured,
   retired, deferred-to-firmware}}; EVERY verdict — including pass —
   cites an `evidence_path` naming the gate run's saved output
   artifact in the inbox, not the config it consumed; a pass with no
   archived output gets independently re-run before it is trusted),
   `discoveries[]` (each with `id` matching `^discovery-`, `claim`,
   `evidence`), `investigations[]` (each with `id` matching
   `^investigation-`, `triggered_by`, `question`, `answer`, `evidence`),
   `follow_ons[]` (each with `summary` + `proposed_routing` ∈ {{next-hop,
   new-thread, backlog, out-of-scope}}), and `plan_hindsight` (string;
   "Nothing notable" is valid).

   Use this v2 example template as the structural starting point:

   ```
   ~/.claude/skills/threads/assets/templates/codex-handback-template.md
   ```

   `~/.claude/skills/threads/references/codex-handback.md` describes the
   recording discipline (4-buckets rule, evidence anchoring, handoff
   artifact promotion recommendations) — read it before writing, but
   conform the JSON shape to the schema, not to the prose.

4. **Keep waiting outside the model loop.** Use the mailbox completion-job
   contract for any command that can outlive one tool return. It records the
   source SHA, contract digest, terminal markers, artifacts, timeout, retry
   bound, full logs and a bounded summary under:

   ```
   {handback_inbox}/jobs/<job-name>/<run-key>/
   ```

   Launch with:

   ```
   python3 ~/.claude/skills/threads/scripts/launch_codex_mailbox_job.py --contract <job-contract.json> --inbox {handback_inbox}
   ```

   Then end the model turn. `result.json` is authoritative. A supported
   `codex-self` doorbell is write-first/ring-second, carries only
   `JOB_TERMINAL <path>`, and targets the same Codex thread; it is not a
   Claude↔Codex content channel. If no doorbell exists or it fails, remain
   idle until the operator manually resumes the worker.

   The worker TUI remains interactive while the job runs. For a redirect that
   invalidates the job, request contained cancellation with:

   ```
   python3 ~/.claude/skills/threads/scripts/launch_codex_mailbox_job.py --cancel <run-dir> --reason <operator-redirect>
   ```

   The launcher writes `control.json`; the supervisor kills the whole transient
   systemd user-scope cgroup. Do not claim cancellation until `result.json`
   reports `cancelled` and every command reports `cleanup_verified: true`.

5. **Keep worker lifecycle in the mailbox.** The operator fires this packet
   through `launch_codex_worker.py`, never a raw `codex` command. The wrapper
   writes `worker-state.json` atomically before the TUI starts and prints a
   pointer-only `WORKER_LAUNCHED <path>` event. Do not hand-edit that file or
   mix gate progress into it; use `progress.json` for plan checkpoints. Before
   the first mailbox job, bind `$CODEX_THREAD_ID` through
   `launch_codex_worker.py bind-session`; `codex-self` jobs refuse an absent or
   mismatched v2 binding rather than ringing the wrong worker. Before
   ending blocked or after writing a terminal handback, record the semantic
   transition with `launch_codex_worker.py update`. The worker receipt has no
   cancellation claim: external jobs are cancelled only through their verified
   systemd-cgroup mailbox contract, while an exited TUI is merely `exited` or
   `failed`. The wrapper never treats exit code zero as proof of plan completion.

## Plan-specific operational rules

Read the plan file's "Hard constraints" section. The plan file
already contains run-specific rules (cross-repo edits, regression
baselines, no-simulation constraints, etc.) — Codex consumes those
when it opens the plan as turn 1.

## Sidecar terminal

```bash
cd {worktree}
source codex-handoff/{plan_id}/env.sh
{launch_command}
```

Then paste the complete short-prompt block above as turn 1.
""")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Emit a minimal Codex launch packet (Path B) for a plan hop. "
            "Use when the plan file is self-contained and no scaffold "
            "wrapper is needed."
        ),
    )
    parser.add_argument(
        "--thread-id",
        required=True,
        help="Thread ID, e.g. receiver/20260427-chi-square-raim-design",
    )
    parser.add_argument(
        "--plan-id",
        required=True,
        help="Plan hop ID, e.g. plan-03b",
    )
    parser.add_argument(
        "--main-repo",
        default=".",
        help="Path to main checkout (where .threads/ lives). Default: cwd",
    )
    parser.add_argument(
        "--worktree-path",
        default=None,
        help=(
            "Override worktree path. Default: discovered from "
            "thread.json::codex_worktrees[]."
        ),
    )
    parser.add_argument(
        "--allow-no-kickoff",
        action="store_true",
        help="Legacy hops only: skip the two-file check (plan + kickoff-*.md).",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Output path for the launch packet. Default: stdout",
    )
    parser.add_argument(
        "--env-file",
        default=None,
        help=(
            "Path to an env.sh to copy into the inbox verbatim "
            "(replaces any existing codex-handoff/<plan-id>/env.sh). "
            "Default: keep an existing inbox env.sh, else write a "
            "skeleton to fill in."
        ),
    )
    parser.add_argument(
        "--codex-model",
        required=True,
        help="Exact model to pin in the generated fresh-worker launch command",
    )
    parser.add_argument(
        "--reasoning-effort",
        required=True,
        choices=("none", "low", "medium", "high", "xhigh", "max"),
        help="Reasoning effort to pin in the generated launch command",
    )
    parser.add_argument(
        "--auto-compact-token-limit",
        required=True,
        type=int,
        help="Positive automatic-compaction threshold for the worker session",
    )
    args = parser.parse_args()

    if args.auto_compact_token_limit <= 0:
        die("--auto-compact-token-limit must be positive")

    main_repo = Path(args.main_repo).resolve()
    if not main_repo.exists():
        die(f"main repo {main_repo} does not exist")

    thread_dir = main_repo / ".threads" / args.thread_id
    if not thread_dir.exists():
        die(f"thread directory not found: {thread_dir}")

    thread_json = thread_dir / "thread.json"
    plan_file = find_plan_file(thread_dir, args.plan_id)
    refuse_chart_in_plan(plan_file)
    kickoff_file: Path | None = None
    if not args.allow_no_kickoff:
        kickoff_file = find_kickoff_file(thread_dir, args.plan_id, plan_file)
    # Inbox = the FULL plan stem (codex-handoff/ is a flat namespace shared
    # across threads on a worktree; a bare plan-NN collides — launch contract).
    inbox_stem = plan_file.stem

    worktree: Path
    branch_json: str | None = None
    if args.worktree_path:
        worktree = Path(args.worktree_path).resolve()
        if not worktree.exists():
            die(f"worktree {worktree} does not exist")
    else:
        worktree, branch_json = discover_worktree(thread_json, main_repo)

    branch = git_branch(worktree) or branch_json
    if not branch:
        die(
            f"could not determine branch for worktree {worktree}.\n"
            f"  git branch --show-current returned empty (detached HEAD?) "
            f"and thread.json had no codex_worktrees[].branch field."
        )

    base_sha = git_short_sha(worktree)
    if not base_sha:
        die(
            f"could not read short HEAD SHA from worktree {worktree}.\n"
            f"  is git installed and the worktree initialized?"
        )

    handback_inbox = worktree / "codex-handoff" / inbox_stem

    env_path, env_how = stage_env_file(
        handback_inbox,
        env_file=Path(args.env_file) if args.env_file else None,
        worktree=worktree,
        thread_id=args.thread_id,
        plan_id=inbox_stem,
    )

    packet = emit_packet(
        plan_file=plan_file,
        kickoff_file=kickoff_file,
        worktree=worktree,
        main_repo=main_repo,
        branch=branch,
        base_sha=base_sha,
        handback_inbox=handback_inbox,
        thread_id=args.thread_id,
        plan_id=inbox_stem,
        codex_model=args.codex_model,
        reasoning_effort=args.reasoning_effort,
        auto_compact_token_limit=args.auto_compact_token_limit,
    )

    if args.out:
        out_path = Path(args.out).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(packet)
        # Turn 1 is written FIRST: the launch command names it, and the launcher
        # starts Codex with a one-sentence pointer to it as the prompt argument,
        # so nothing is pasted into the TUI. The long packet stays in prompt.md.
        turn1_path = write_turn1_file(handback_inbox=handback_inbox, packet=packet)
        launch_command = build_worker_launch_command(
            handback_inbox=handback_inbox,
            thread_id=args.thread_id,
            plan_id=inbox_stem,
            branch=branch,
            base_sha=base_sha,
            codex_model=args.codex_model,
            reasoning_effort=args.reasoning_effort,
            auto_compact_token_limit=args.auto_compact_token_limit,
            turn1_file=turn1_path,
        )
        # The Fire Card's launch surface is a one-line bash script, not the
        # long `cd && source && launch ...` one-liner: a pasted one-liner
        # wraps in the operator's terminal and has opened a Python REPL and
        # split its own arguments (2026-09-11). The script is gitignored
        # beside prompt.md/env.sh (absolute paths by design).
        fire_path, fire_ignored = write_fire_script(
            handback_inbox=handback_inbox,
            worktree=worktree,
            plan_id=inbox_stem,
            launch_command=launch_command,
        )
        paste_line = turn1_prompt(turn1_path)
        print("FIRE CARD (terminal-only; absolute paths by design)")
        print(f"  Launch dir (Codex CWD) : {worktree}")
        print(f"  Turn-1 file (absolute) : {turn1_path}")
        print(f"  Prompt, long form      : {out_path}")
        print(f"  Plan file              : {plan_file}")
        if kickoff_file:
            print(f"  Kickoff file           : {kickoff_file}")
        print(f"  Handback inbox         : {handback_inbox}")
        print(f"  Environment file       : {env_path}  ({env_how})")
        print(f"  Fire script            : {fire_path}"
              + ("" if fire_ignored else "  [WARNING: not gitignored — add codex-handoff/**/fire.sh to .gitignore before any commit]"))
        print()
        print("FIRE (only when the operator says \"fire\" - preparing a packet never launches):")
        print("  Codex vehicle : the orchestrator runs")
        print(localize("    python3 \"$HOME/.claude/skills/threads/scripts/fire_codex_worker.py\" ")
              + f"--inbox {shlex.quote(str(handback_inbox))}")
        print("    (opens a detached tmux window, starts the mailbox relay, passes turn 1")
        print("     as Codex's prompt: no pastes. Refusals land in <inbox>/fire-failed.log.)")
        print(f"  By hand, outside tmux: bash {fire_path}")
        print(f"  Claude vehicle: cd {worktree} && claude, then paste this ONE line as turn 1:")
        print(f"    {paste_line}")
        print(f"  Fired when {handback_inbox}/worker-state.json records state=running;")
        print("  a first tool call is progress, not the launch authority.")
        print()
        print("  (reference, the same launch as one line — do NOT paste this if your terminal wraps:)")
        print(f"    cd {worktree} && source codex-handoff/{inbox_stem}/env.sh && {launch_command}")
    else:
        print(f"# Environment file: {env_path} ({env_how})",
              file=sys.stderr)
        sys.stdout.write(packet)


if __name__ == "__main__":
    main()
