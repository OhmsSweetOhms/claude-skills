---
name: socks-tracker
description: "Track gaps, lessons learned, and improvement items discovered while using the SOCKS FPGA/SoC skill during real projects. Use this skill whenever the user types /socks-tracker, or when they mention tracking SOCKS issues, logging skill gaps, exporting update notes, or wanting to improve the SOCKS skill based on project experience. Also trigger when the user says things like 'note this for SOCKS', 'SOCKS needs to handle this', 'add this to the update tracker', or 'what did we learn for SOCKS'."
---

# SOCKS Tracker

Track what works, what breaks, and what's missing in the SOCKS skill during
real FPGA/SoC projects. The tracker persists across conversations so nothing
gets lost between sessions.

## Why This Exists

Every SOCKS project teaches something -- a Vivado quirk, a missing workflow
path, a script that should exist but doesn't. Without a tracker, these
lessons evaporate between conversations. This skill captures them in a
structured format that can later be fed to `/skill-creator` to actually
update SOCKS.

## Invocation

```
/socks-tracker                     Show status + help
/socks-tracker --log "description" Log a new item
/socks-tracker --review            Show all tracked items
/socks-tracker --export            Generate socks_update.md
/socks-tracker --apply             Enter plan mode to update SOCKS
```

## How It Works

### Storage

The tracker file lives at `docs/socks_tracker.json` in the current project
directory. If it doesn't exist, create it on first `--log`. The JSON
structure:

```json
{
  "project": "project-name (from CLAUDE.md or directory name)",
  "created": "2026-03-18",
  "items": [
    {
      "id": 1,
      "timestamp": "2026-03-18T10:30:00",
      "category": "skill_md",
      "group": "block_design_flow",
      "summary": "Short description",
      "detail": "What happened, what we did, what SOCKS needs",
      "code_example": "optional VHDL/TCL/Python snippet illustrating the pattern",
      "manual_vs_generic": "manual | generic_script | script_fix | template | guidance",
      "socks_files_affected": ["SKILL.md", "references/hil.md"],
      "project_files": ["build/synth/create_bd.tcl"],
      "related_items": [2, 3],
      "priority": "high | medium | low",
      "status": "open | exported | applied"
    }
  ]
}
```

**Optional fields:** `code_example`, `group`, `related_items`, and `priority`
may be omitted. Claude fills them in when the context is clear.

### Categories

When logging an item, classify it into one of these categories:

| Category | What it covers | Example |
|----------|---------------|---------|
| `skill_md` | SKILL.md workflow changes, missing paths, skip logic | "Block design projects skip stages 2-9" |
| `new_script` | Scripts that should be added to SOCKS | "gen_probe_wrapper.py for ILA shim" |
| `script_fix` | Bugs or improvements in existing SOCKS scripts | "bash_audit.py false positives on Vivado files" |
| `reference_doc` | Updates to reference files (hil.md, etc.) | "hil.md needs block_design project_type" |
| `tool_workaround` | Vivado/Vitis/XSCT quirks discovered | "Inline impl required for ILA builds" |
| `board_info` | Board-specific knowledge (pins, power, presets) | "MicroZed is CLG400 not CLG484" |
| `schema_change` | hil.json or other schema additions | "Add internal_loopback to wiring" |

### `manual_vs_generic` Field

This is the key field for knowing what to do with each item when updating SOCKS:

- **`manual`** -- Project-specific, Claude-authored each time (e.g., `create_bd.tcl`, firmware). No script needed; just SKILL.md guidance for Claude.
- **`generic_script`** -- Same logic every project, can be a new Python/TCL script in SOCKS (e.g., `hil_uart.py`, `hil_program.tcl`).
- **`script_fix`** -- Bug fix or improvement to an existing SOCKS script (e.g., bash_audit.py exclude patterns). Not a new script.
- **`template`** -- Partially generic, needs project-specific parameters (e.g., probe wrapper VHDL generated from `hil.json`).
- **`guidance`** -- Documentation/reference change only, no code (e.g., "add MicroZed board info to references/boards/").

---

## Flag Details

### `--log "description"`

Add a new tracker item. Claude should:

1. Parse the description for what happened
2. Ask clarifying questions if the category or affected files are unclear
3. Determine `manual_vs_generic` classification
4. Write the item to `docs/socks_tracker.json`
5. Confirm with a one-line summary

If the user provides a bare description, Claude fills in the structured
fields from context. If the conversation contains obvious SOCKS gaps (e.g.,
a workaround was needed, a stage was skipped, a script didn't exist),
Claude can suggest logging them proactively.

### `--review`

Display all tracked items grouped by category. Show:
- Item count per category
- Open vs exported vs applied counts
- For each item: ID, summary, manual_vs_generic, status

### `--export`

Generate `docs/socks_tracker_export.md` from the tracker JSON. The user
can override the path: `--export path/to/file.md`.

The export format
follows the structure established in the microzed-spi-pmod project:

For each item (grouped by theme, not raw category):
1. **The gap** -- what SOCKS doesn't handle
2. **What we did** -- the manual workaround in this project
3. **What SOCKS needs** -- specific changes, tagged as:
   - SKILL.md changes (guidance only)
   - New scripts (with input/output spec)
   - Reference doc updates
   - Schema changes
4. **Manual vs generic** -- whether this becomes a script, template, or guidance

Mark all exported items as `status: "exported"`.

### `--apply`

This is the bridge to actually updating SOCKS:

1. Read `docs/socks_update.md` (must exist -- run `--export` first if not)
2. Read the current SOCKS `SKILL.md` for context
3. Present a summary of proposed changes to the user
4. On approval, use `/skill-creator` in plan mode to apply the changes
5. Mark applied items as `status: "applied"`

### Bare `/socks-tracker` (no flags)

Show:
- Current project name
- Tracker file status (exists / doesn't exist / N items)
- Quick counts by category
- Suggest next action ("You have 5 open items. Run `--review` to see them
  or `--export` to generate socks_update.md.")

---

## Proactive Logging

When this skill is loaded and Claude notices a SOCKS gap during normal work
(not just when `/socks-tracker --log` is explicitly called), Claude should
mention it:

> "This looks like a SOCKS gap -- the HIL flow doesn't handle block design
> projects without VCD. Want me to log it? `/socks-tracker --log`"

Don't auto-log without asking. The user decides what's worth tracking.

---

## Integration with SOCKS Workflows

The tracker is designed to run alongside SOCKS, not replace it. Typical flow:

1. User runs `/socks --design` or `/socks --hil`
2. During the workflow, Claude hits a gap or workaround
3. Claude notes it: "This is a SOCKS gap. Want me to track it?"
4. User says yes -> `/socks-tracker --log "description"`
5. At project end, user runs `/socks-tracker --export`
6. Later, user runs `/socks-tracker --apply` to update SOCKS via `/skill-creator`

The tracker JSON persists in the project's `docs/` directory, so it survives
across conversations and can be reviewed by future Claude sessions.
