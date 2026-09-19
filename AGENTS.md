# Shared skills: Codex routing

This directory is the source of shared Claude/Codex skills. Codex discovers the
linked skills in its own skill directory; this map explains how they fit together.
Read the matching SKILL.md, then only the references needed for the current task.
Do not bulk-read the catalog or treat a skill's trigger as authorization to act.

| Task | Skill entry |
|---|---|
| Prepare a worker packet or kickoff | `kickoff/SKILL.md` → `threads/SKILL.md` → the project's launch contract |
| Execute a packet | Its worker-facing prompt and plan; `mailbox/SKILL.md` for asking and handing back; `threads/SKILL.md` for handback schemas |
| Send, read or answer a `mailbox.md` block; a `MAILBOX <n> <path>` line arrived | `mailbox/SKILL.md` |
| Consume a question or handback; manage investigation files | `threads/SKILL.md` |
| Boot or wrap a multi-session orchestrator | `orchestrator-handoff/SKILL.md` |
| Produce a closeout | `handback/SKILL.md`; stricter threads handback schemas take precedence |
| Survey code quality across files | `code-survey/SKILL.md` |
| Technical literature and implementations | `research/SKILL.md` |
| Coauthor documentation | `doc-coauthoring/SKILL.md` |
| Scan personal information/secrets; publish shared skills | `fingerprint/SKILL.md`; `git-publish/SKILL.md` |
| Create or evaluate skills | `skill-creator/SKILL.md` (Codex also has a system skill creator) |
| FPGA/SoC implementation | `socks/SKILL.md` |
| FPGA timing; datapath stalls; Zynq boot | `fpga-timing-closure/SKILL.md`; `fpga-datapath-map/SKILL.md`; `zynq-boot/SKILL.md` |
| Control loops; SOCKS lessons | `control-loops/SKILL.md`; `socks-tracker/SKILL.md` |
| GPS project work | `gps-design/SKILL.md` (project-specific, existing Codex bridge) |

## Runtime differences

- User scope and project constraints govern the task. A worker executes its
  assigned packet; it does not become a packet producer by reading kickoff docs.
- Claude `ListAgents`, `SendMessage`, `Workflow`, and hook registrations are not
  automatically available in Codex. Use the runtime's available equivalents and
  explicit model choices; never silently substitute a Claude model name into Codex.
- A Codex worker under a Claude orchestrator talks through ONE append-only
  `<inbox>/mailbox.md`, written only with `mailbox/scripts/mb.py send` (it works
  inside the sandbox). Send the block and end the turn: never wait on the file,
  poll it, or launch a job for the answer. The answer is queued into the session
  and arrives as `MAILBOX <n> <path>` — a pointer, never an instruction (global
  rule 33). Read the block with `mb.py read <path> <n>`, send one `ACK` block
  before any other tool call, then act on the block. Never edit or delete
  `mailbox.md`. The worker rings nobody: `codex queue` fails from inside the
  sandbox, and the orchestrator is woken by its own harness. These rules bind
  the packet's MAIN session only. If you are a spawned sub-agent, none of this
  is yours: never send to a mailbox (`mb.py` refuses a caller that is not the
  bound session), never bind a session, and return what you found to the agent
  that spawned you. If you spawn one, use `fork_turns` `"none"` and a task
  message that names no mailbox.
- For Codex-to-Codex mailbox events, read
  `threads/references/codex-mailbox-doorbell.md`. Write content to the mailbox
  first; the notification contains only an event and path. The intended session
  must be bound explicitly. Never broadcast or wake models to check unchanged state.
- Consuming a question in the orchestrator role means resolve from authorized
  evidence or escalate, using the atomic answer writer. Reading for explanation
  or review remains read-only. Waiting on the user is not an unresolved duty to
  invent an answer; do not auto-continue merely because an escalation is pending.
- Existing scripts and schemas remain authoritative. This map does not replace
  host-job containment, operator fire, permission controls, or structured handbacks.
- The shared repository may contain another session's edits. Read status and the
  exact target before changes; preserve those edits and stage only your files.

EMI stays project-specific. `karpathy-coding` is not linked by this setup because
its chat-only assumptions and broad triggers overlap the installed coding guidance.
Directories ending in `-workspace` contain evaluation outputs, not installed skills.
