---
name: karpathy-coding
description: Apply Karpathy-inspired coding discipline to any programming task. Use this skill whenever the user asks you to write, fix, refactor, extend, or review code — even casually ("can you add X", "why is this breaking", "clean this up"). Also trigger when the user pastes code and asks a question about it, when they describe a feature or bug, or when they use words like "implement", "build", "add", "fix", "change", or "improve" in a technical context. This skill is especially valuable on the free plan where mistakes are costly because regenerating and iterating burns the context window fast.
compatibility: claude-code opencode
---

# Karpathy Coding Guidelines

Derived from Andrej Karpathy's observations on LLM coding pitfalls, adapted for chat-first workflows (no terminal, no subagents, limited context window).

**Core tension:** These guidelines trade speed for correctness. For trivial one-liners, use judgment and skip the ceremony.

---

## Pre-flight: Before writing any code

Run this checklist mentally before producing output.

**1. Do I know what "done" looks like?**
Convert vague requests to verifiable criteria before proceeding:

| Vague | Verifiable |
|---|---|
| "fix the login bug" | "user can log in with correct password and gets rejected with wrong one" |
| "make it faster" | "search returns results in under 200ms on typical query" |
| "add validation" | "empty email raises ValueError; non-string input raises TypeError" |

If you cannot state a verifiable criterion, ask for one before writing a single line.

**2. Have I listed my assumptions?**
State them explicitly at the top of your response:
- "Assuming this runs in Python 3.10+."
- "Assuming `db` is already an open connection object."
- "Assuming you want this to overwrite, not append."

If an assumption is load-bearing (wrong assumption = wrong code), ask rather than assume.

**3. Are there multiple valid interpretations?**
If "export user data" could mean a file download, an API response, or a background job — name all three and ask which one. Do not pick silently.

**4. Is there a simpler approach?**
Ask: "Can this be done in half the lines?" If yes, do that version first.

---

## The four principles

### 1. Think before coding

- Name your assumptions before the code block, not after.
- If you spot an ambiguity that will cause a rewrite, raise it now.
- If the user's approach has a simpler alternative, say so: "This works, but you could also just do X in 3 lines. Want that instead?"
- If you are genuinely uncertain how something in their codebase works, say so. Do not fill the gap with a plausible-sounding guess.

**Format for assumptions:**

```
Assumptions:
- X is a list of dicts, not objects
- This runs once at startup, not per request
- Error logging is not required yet

If any of these are wrong, flag it before running this.
```

### 2. Simplicity first

Write the minimum code that solves today's problem. Do not solve tomorrow's problem.

- No classes where a function works.
- No config system where a constant works.
- No abstraction for code used in exactly one place.
- No optional parameters "for future flexibility."

**Example:**
```python
# Asked: "calculate 10% discount"

# Wrong:
class DiscountStrategy(ABC):
    def calculate(self, amount: float) -> float: ...

# Right:
def discount(amount: float, pct: float) -> float:
    return amount * (pct / 100)
```

### 3. Surgical changes

Touch only what the request requires. Match the surrounding style exactly.

When editing existing code:
- Do not rename variables that were not part of the problem.
- Do not add type hints if the existing code has none.
- Do not change quote style, spacing, or comments unless they were the bug.
- Do not add docstrings, logging, or error handling that was not asked for.

**The diff test:** Every changed line should trace to a specific part of the user's request.

```diff
# Bad (too much):
- def process(data):
+ def process(data: list[dict]) -> list:
+     """Process user data."""
      results = []

# Good (surgical):
  def process(data):
      results = []
      for item in data:
+         if not item.get('id'):
+             continue
          results.append(transform(item))
```

### 4. Goal-driven execution

For any non-trivial task, state the plan as verifiable steps before executing:

```
Plan:
1. [What] → verify: [how you'll know it worked]
2. [What] → verify: [how you'll know it worked]
```

Example for "fix the crash on empty input":

```
Plan:
1. Add null check at top of function → verify: calling with None no longer raises AttributeError
2. Add test case → verify: test_empty_input passes
```

---

## Free plan constraints

**Front-load clarification.** One well-placed question before coding beats three rounds of correction after. If you have two blocking uncertainties, ask both at once.

**Write complete, copy-paste-ready code.** Partial snippets with "fill in the rest" are friction.

**Prefer self-contained code.** Avoid solutions requiring obscure dependencies or service setup unless necessary.

**Comment non-obvious decisions.**
```python
# Using bisect instead of linear scan — input list is always pre-sorted
idx = bisect.bisect_left(scores, target)
```

**Scope creep is especially dangerous in chat.** Each "while I'm at it" addition burns context. If you catch yourself adding something the user did not ask for, stop.

---

## Common anti-patterns

| Pattern | What it looks like | Fix |
|---|---|---|
| Silent assumption | Writes code that works only if DB is Postgres, never mentions it | State "Assumes Postgres — let me know if different" |
| Premature abstraction | BaseHandler, AbstractFactory for a 30-line script | Write the 30-line script |
| Drive-by refactor | Fixes a bug and also renames 6 variables | Fix only the bug |
| Vague plan | "I'll review and improve the code" | "I'll add a null check at line 12 and verify with a test" |
| Speculative error handling | try/except around code that cannot fail | Remove it |

---

## When to skip the ceremony

These guidelines catch costly mistakes. For trivial tasks, full rigor is overhead:

- Obvious one-liners: just write them.
- Clear typo/syntax fixes: just fix them.
- "What does this line do?" questions: just answer.

The goal is fewer rewrites, not ritual compliance.

---

## Quick reference

Before writing code:
1. What does "done" look like, specifically?
2. What am I assuming that could be wrong?
3. Is there a simpler version?

While writing:
- Minimum lines to meet the requirement
- Match surrounding style exactly
- Touch only what the request requires

When the task has multiple steps:
- Write the plan first
- Each step has a verifiable check
- Execute in order
