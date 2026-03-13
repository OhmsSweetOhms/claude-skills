#!/usr/bin/env python3
"""
Tests for the SOCKS pipeline redesign: unified STAGES, keywords, no verify loop.

Run:  python3 scripts/test_socks_redesign.py
"""

import inspect
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from socks import parse_stages, STAGES, DESIGN_LOOP, StageDef, run_stage, write_pipeline_logs

PASS = 0
FAIL = 0


def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        msg = f"  [FAIL] {name}"
        if detail:
            msg += f" -- {detail}"
        print(msg)


# -----------------------------------------------------------------------
# 1. Unified STAGES dict structure
# -----------------------------------------------------------------------
print("\n=== STAGES dict structure ===")

check("STAGES has 20 entries (0-19)",
      set(STAGES.keys()) == set(range(20)),
      f"keys={sorted(STAGES.keys())}")

check("All values are StageDef",
      all(isinstance(v, StageDef) for v in STAGES.values()))

check("Every stage has a label",
      all(v.label for v in STAGES.values()))

check("No old AUTOMATED_STAGES exists",
      "AUTOMATED_STAGES" not in dir(sys.modules["socks"]))

check("No old GUIDANCE_STAGES exists",
      "GUIDANCE_STAGES" not in dir(sys.modules["socks"]))

check("No VERIFY_LOOP constant",
      "VERIFY_LOOP" not in dir(sys.modules["socks"]))

check("No VERIFY_MAX_RETRIES constant",
      "VERIFY_MAX_RETRIES" not in dir(sys.modules["socks"]))

# -----------------------------------------------------------------------
# 2. DESIGN_LOOP is informational
# -----------------------------------------------------------------------
print("\n=== DESIGN_LOOP ===")

check("DESIGN_LOOP is [2,3,4,5,6,7,8,9]",
      DESIGN_LOOP == [2, 3, 4, 5, 6, 7, 8, 9])

# -----------------------------------------------------------------------
# 3. parse_stages: only 'automated' keyword
# -----------------------------------------------------------------------
print("\n=== parse_stages ===")

auto_stages = parse_stages("automated")
expected_auto = sorted(k for k, v in STAGES.items() if v.script)
check("'automated' returns stages with scripts",
      auto_stages == expected_auto,
      f"got {auto_stages}, expected {expected_auto}")

check("'automated' includes 0,1,3,4,5,7,8,9,10,11,13,14,15,16,17,18,19",
      auto_stages == [0, 1, 3, 4, 5, 7, 8, 9, 10, 11, 13, 14, 15, 16, 17, 18, 19],
      f"got {auto_stages}")

# BOTH stages
check("Stage 1 has both script and guidance",
      STAGES[1].script is not None and STAGES[1].guidance is not None)

check("Stage 5 has both script and guidance",
      STAGES[5].script is not None and STAGES[5].guidance is not None)

check("Stage 7 has both script and guidance",
      STAGES[7].script is not None and STAGES[7].guidance is not None)

# -----------------------------------------------------------------------
# 4. Comma-separated -- no auto-expansion
# -----------------------------------------------------------------------
print("\n=== No auto-expansion ===")

check("'5,7,8' returns exactly [5,7,8]",
      parse_stages("5,7,8") == [5, 7, 8])

check("'8' returns exactly [8] (no expansion to 5,6,7,8)",
      parse_stages("8") == [8])

check("'7' returns exactly [7]",
      parse_stages("7") == [7])

check("'0,13' returns exactly [0,13]",
      parse_stages("0,13") == [0, 13])

# -----------------------------------------------------------------------
# 5. No 'all' or 'guidance' keywords
# -----------------------------------------------------------------------
print("\n=== Removed keywords ===")

socks_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "socks.py")
with open(socks_path) as f:
    src = f.read()

check("No 'all' keyword in parse_stages",
      'keyword == "all"' not in src)

check("No 'guidance' keyword in parse_stages",
      'keyword == "guidance"' not in src)

check("Default --stages is 'automated'",
      'default="automated"' in src)

# -----------------------------------------------------------------------
# 6. Stage 7 unconditional --vcd
# -----------------------------------------------------------------------
print("\n=== Stage 7 unconditional --vcd ===")

check("No 'if 8 in stages' conditional for --vcd",
      "if 8 in stages" not in src)

in_stage7 = False
found_unconditional_vcd = False
for line in src.splitlines():
    if "elif stage == 7:" in line:
        in_stage7 = True
    elif in_stage7 and "elif stage ==" in line:
        in_stage7 = False
    elif in_stage7 and '--vcd' in line and 'if' not in line:
        found_unconditional_vcd = True

check("Stage 7 has unconditional --vcd append",
      found_unconditional_vcd)

# -----------------------------------------------------------------------
# 7. No verify loop mechanics in source
# -----------------------------------------------------------------------
print("\n=== No verify loop mechanics ===")

check("No 'verify_retries' in source",
      "verify_retries" not in src)

check("No 'VERIFY_LOOP' in source",
      "VERIFY_LOOP" not in src)

check("No 'VERIFY_MAX' in source",
      "VERIFY_MAX" not in src)

check("No '[RETRY' annotation in source",
      "[RETRY" not in src)

check("No 'VERIFY LOOP RESTART' in source",
      "VERIFY LOOP RESTART" not in src)

check("No 'rewind' logic (stages.index(5))",
      "stages.index(5)" not in src)

check("No 'exhausted' retries message",
      "exhausted" not in src.lower())

# -----------------------------------------------------------------------
# 8. Simple failure handling -- just stop
# -----------------------------------------------------------------------
print("\n=== Simple failure handling ===")

check("Main loop uses 'for stage in stages' (not while)",
      "for stage in stages:" in src)

check("No 'while idx < len(stages)' loop",
      "while idx < len(stages)" not in src)

check("Failure stops with 'stopping pipeline'",
      "stopping pipeline" in src)

# -----------------------------------------------------------------------
# 9. Label lookups use STAGES[stage].label
# -----------------------------------------------------------------------
print("\n=== Label lookups ===")

check("No AUTOMATED_STAGES.get() calls",
      "AUTOMATED_STAGES.get(" not in src)

check("No GUIDANCE_STAGES.get() calls",
      "GUIDANCE_STAGES.get(" not in src)

# -----------------------------------------------------------------------
# 10. run_stage has no mode parameter
# -----------------------------------------------------------------------
print("\n=== run_stage signature ===")

sig = inspect.signature(run_stage)
check("run_stage has no 'mode' parameter",
      "mode" not in sig.parameters,
      f"params={list(sig.parameters.keys())}")

# -----------------------------------------------------------------------
# 11. write_pipeline_logs signature (no verify_retries)
# -----------------------------------------------------------------------
print("\n=== write_pipeline_logs signature ===")

sig = inspect.signature(write_pipeline_logs)
check("write_pipeline_logs has no verify_retries param",
      "verify_retries" not in sig.parameters,
      f"params={list(sig.parameters.keys())}")

# -----------------------------------------------------------------------
# 12. SKILL.md content checks
# -----------------------------------------------------------------------
print("\n=== SKILL.md content ===")

skill_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
skill_md = os.path.join(skill_dir, "SKILL.md")
with open(skill_md) as f:
    skill_content = f.read()

check("SKILL.md has 'Design Loop' section",
      "Design Loop" in skill_content)

check("SKILL.md no longer has 'Verification Loop' heading",
      "## Verification Loop" not in skill_content)

check("SKILL.md documents --stages automated",
      "--stages automated" in skill_content)

check("SKILL.md does not document --stages all as keyword",
      "`--stages all`" not in skill_content)

check("SKILL.md does not document --stages guidance as keyword",
      "`--stages guidance`" not in skill_content)

check("SKILL.md has circular logic detection",
      "circular logic" in skill_content.lower())

check("SKILL.md Stage 2 is 'Write/Modify RTL'",
      "Write/Modify RTL" in skill_content)

check("SKILL.md mentions plan mode approval in Stage 1",
      "plan mode approval" in skill_content.lower())

check("SKILL.md no 'auto-expand' or 'restarts from stage 5'",
      "auto-expand" not in skill_content.lower() and
      "restarts from stage 5" not in skill_content)

check("SKILL.md no 'up to 2 retries'",
      "up to 2 retries" not in skill_content)

# -----------------------------------------------------------------------
# 13. build.py uses 'automated' not 'all'
# -----------------------------------------------------------------------
print("\n=== build.py ===")

build_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "build.py")
with open(build_path) as f:
    build_src = f.read()

check("build.py uses stages = \"automated\"",
      'stages = "automated"' in build_src)

check("build.py does not use stages = \"all\"",
      'stages = "all"' not in build_src)

# -----------------------------------------------------------------------
# 14. Self-audit still passes
# -----------------------------------------------------------------------
print("\n=== Self-audit ===")

result = subprocess.run(
    [sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "self_audit.py")],
    capture_output=True, text=True
)
check("self_audit.py exits 0",
      result.returncode == 0,
      f"rc={result.returncode}")

# -----------------------------------------------------------------------
# Summary
# -----------------------------------------------------------------------
print(f"\n{'='*60}")
total = PASS + FAIL
print(f"  {PASS}/{total} passed, {FAIL} failed")
if FAIL:
    print(f"  RESULT: FAIL")
else:
    print(f"  RESULT: PASS")
print(f"{'='*60}")

sys.exit(1 if FAIL else 0)
