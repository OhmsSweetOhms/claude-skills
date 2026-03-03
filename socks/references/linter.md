# VHDL Linter

Run this after Stage 2 (VHDL authoring) to catch style, convention, and type-checking issues before synthesis.

## Setup

**Prerequisite:** Node.js >= 14.15.0 (v18+ recommended). Check:
```bash
node --version
```

If you have Node v12, upgrade:
```bash
nvm install 18 && nvm use 18
```

Install or clone the linter (e.g., vhdl-linter from GitHub):
```bash
cd <vhdl-linter-root>
npm install
npm run compile  # Generates dist/ directory
```

## Usage

Lint a folder:
```bash
node <vhdl-linter-root>/dist/lib/cli/cli.js src/
```

## Output

Three severity levels:

| Level | Meaning | Action |
|-------|---------|--------|
| **error** (0) | Code will not compile or is fundamentally wrong | Fix before proceeding |
| **warning** (3-5) | Suspicious code (unused signals/generics, risky constructs) | Fix if actionable (own code) |
| **info** (218+) | Style recommendations (std_logic vs std_ulogic, naming) | Document rationale if intentional |

Example output:
```
0 error(s), 5 warning(s), 220 info(s)
```

## Common Warnings

**Unused signal/generic:**
```
module.vhd:215: Not reading signal 'unused_signal' (unused)
```
→ Remove the signal declaration and all assignments.

**Type resolution mismatch:**
```
module.vhd:86: Port is using resolved subtype (std_logic)
              should use unresolved type std_ulogic
```
→ Acceptable in external/read-only modules. Use `std_ulogic` for new code.

## Configuration

Create `vhdl-linter.yml` in your project to customize rules:

```yaml
rules:
  coding-style: true
  naming-style: true
  type-checking: true

style:
  objectCasing: snake_case
  constantGenericCasing: CONSTANT_CASE
  labelCasing: lowercase
```

## When to Skip

- External read-only modules (symlinks): accept style warnings
- If linter has no VHDL syntax errors, proceed to Stage 4 (Audit)
- If linter flags a style issue in external code, document in CLAUDE.md

## Repository

- **GitHub:** https://github.com/vhdl-linter/vhdl-linter
- **VS Code:** https://marketplace.visualstudio.com/items?itemName=g0t00.vhdl-linter
