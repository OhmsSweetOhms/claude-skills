# Shared skills: Codex routing

This directory is the source of shared Claude/Codex skills. Codex discovers the
linked skills in its own skill directory; this map explains how they fit together.
Read the matching SKILL.md, then only the references needed for the current task.
Do not bulk-read the catalog or treat a skill's trigger as authorization to act.

| Task | Skill entry |
|---|---|
| Prepare a worker packet or kickoff | `kickoff/SKILL.md` → `threads/SKILL.md` → the project's launch contract |
| Execute a packet | Its worker-facing prompt and plan; `threads/SKILL.md` for mailbox/handback mechanics |
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
