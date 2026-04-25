# Lessons learned

Six cautionary cases that shaped this skill. Each describes a
failure mode the synthesis step should respect.

These are written as principles with examples, not as project-
specific findings. The principles generalize; the examples are
illustrative.

---

## 1. Naming drift hides duplicates from grep

**Principle.** When you ask "is this function duplicated?", a
symbol-name grep is necessary but not sufficient. Two implementations
can do the same math under different names — the grep returns
"single canonical owner," and the duplicate stays hidden.

**Practical guidance.** In the duplicate-helper lens AND the
naming-drift lens, always cross-check by:
- Math signature (which constants and operations).
- Parameter shape (count, types, return shape).
- Docstring summary if present.

**Example pattern.** A geometry function called
`_correct_X` in module A and `_rotate_Y` in module B, both performing
the same rotation by the same angle of the same input. Symbol
search finds neither when you search for the other. The math
signature finds both.

**Synthesis-step implication.** When the duplicate-helper lens
reports "single canonical owner," cross-check the same finding
against the naming-drift lens. If naming-drift flags the same
file/area, *promote* the finding to high confidence with a
"naming drift hides duplicate" annotation.

---

## 2. Length is not monolith

**Principle.** A 900-line file can be cohesive. The signal isn't
length; it's whether the file does multiple unrelated jobs. The
biggest false positive in file-level scans is "this file is long,
therefore split it."

**Practical guidance.** Bias every file-level lens toward KEEP. The
verdict SPLIT requires demonstrating multi-concern. The verdict
KEEP only needs to show the file has one concern, expressed at length.

**Example patterns that warrant KEEP despite length.**
- Orchestrators that compose N sub-systems. Their job is the
  composition.
- State machines that explicitly represent N states. Splitting
  per-state methods obscures the transitions.
- Constructors that wire many parameters. Pure init code can run
  long without doing multiple jobs.

**Synthesis-step implication.** When a file appears in the
file-monolith lens with a SPLIT verdict but shows up nowhere else,
treat it as medium confidence. When the same file shows up in
function-level long-method as well (multiple long methods AND
multi-concern at file level), confidence rises.

---

## 3. Physics floor matters

**Principle.** Not every numerical residual is a bug. Many fixed-
point iterations converge quadratically and reach sub-floor accuracy
in a small number of passes. Calling these "precision bugs" wastes
user attention and erodes trust.

**Practical guidance.** The project config's `physics_floor` is the
filter. Before flagging anything as a "P1 precision bug," ask: is
the residual above the floor? If not, demote to the filtered-out
appendix with reason "below physics floor."

**Example pattern.** Two implementations of the same iterative
solver use different iteration counts (3 vs 6). The 3-pass version
converges to sub-mm; the 6-pass version converges to ULP. Both are
below any "centimeter matters" floor for physical contributions.
Not a precision bug. Possibly a duplicate to consolidate (lens 3),
possibly a naming-drift issue (lens 4) — but not P1.

**Synthesis-step implication.** Apply the physics-floor filter
*after* gathering findings, *before* assigning P1. A finding that
fails the filter doesn't get promoted to P1 even if its lens
verdict suggests "bug."

---

## 4. Same code, different role ≠ duplicate

**Principle.** Two implementations of the same math can exist
intentionally because they live in incompatible contexts. The most
common case: one runs in a sandboxed/firmware/embedded environment
that cannot import the other.

**Practical guidance.** In the duplicate-helper lens, before
recommending merge, ask: does either implementation have a
deployment constraint that prevents it from importing the other?
If yes, the duplication is intentional. The right action is
*documentation* (a one-line comment explaining the duplication),
not deduplication.

**Example pattern.** A simulation module and a firmware module
both compute the same coordinate transform. The simulation module
uses scipy; the firmware module uses only stdlib because it gets
ported to bare-metal C. Merging would force the firmware to import
scipy. Don't merge; document.

**Synthesis-step implication.** When a duplicate-helper finding
spans a project's `boundaries[]` rules, mark it as
`INTENTIONAL` with a recommendation to *document* rather than
merge. The skill's job is to make the duplication legible, not to
eliminate it.

---

## 5. Threads vs sprint: ask the partition question early

**Principle.** Multi-day, multi-hop, hypothesis-driven refactor work
benefits from a /threads container. Quick cleanups don't. Asking
"is this thread-worthy?" *after* generating the recommendation list
forces the user to relitigate the entire work plan; asking *during*
synthesis lets the partition shape the deliverable.

**Practical guidance.** The synthesis step has a thread-worthy
check (step 7) that runs against the project config's
`thread_worthy_threshold`. If the check passes, the wrap-up MD
ends with a "next: propose-thread" pointer. If not, it ends with
"commit these in this order."

**Example.** A scan finds 9 items: 3 P2 dedupes (each ~30 minutes),
3 P3 refactors (each ~1 hour), 2 P4 cleanups (each ~10 minutes), 1
P1 bug (1 hour). Total ~6 hours, includes one P1, three P2s with
verification needs. → thread-worthy. Synthesize accordingly.

**Synthesis-step implication.** Don't wait for the user to ask
"should this be a thread?" The synthesis already has the data; surface
the verdict.

---

## 6. Don't propose new files for 2-occurrence duplications

**Principle.** Two-file duplicates rarely justify a new shared
module. The cost of a new module (import-graph complication, naming
debate, ownership ambiguity) usually exceeds the benefit at N=2.
Hoist to one of the existing files instead.

**Practical guidance.** In the duplicate-helper lens, recommend a
new shared module only when the duplicate appears in 3+ files. For
2-file duplicates, recommend hoisting to whichever file is the
canonical owner — usually the runtime path over a legacy/test
path, or the module deeper in the call graph.

**Example.** Bit-twiddling helpers `_bits_to_uint` and
`_check_word` appear identically in two files. The runtime decoder
is the canonical owner; the legacy decoder imports from runtime.
No new module needed. If a third decoder appeared (say, a hardware
emulator), the calculus changes — at 3+ occurrences, a shared
module pays for itself.

**Synthesis-step implication.** When the duplicate-helper lens
recommends a shared module for a 2-file duplicate, downgrade the
recommendation to "hoist to <canonical-owner>." Surface the
canonical-owner choice with a one-line rationale.

---

## How these lessons compose

Together, these six principles produce a synthesis step that:

- Doesn't over-flag length (lesson 2).
- Doesn't under-flag duplicates hidden by naming (lesson 1).
- Doesn't escalate sub-floor numerical noise to P1 (lesson 3).
- Distinguishes intentional from accidental duplication (lesson 4).
- Surfaces the thread/sprint partition early (lesson 5).
- Avoids module sprawl from premature shared-module proposals
  (lesson 6).

Each one is a guardrail against a specific way the multi-lens scan
can mislead a user. Read them as constraints on the synthesis step,
not as additional features.
