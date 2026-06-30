# ADR conventions (canonical, machine-wide)

Single source of truth for Architecture Decision Record discipline across **all**
projects and machines. Project docs and `CLAUDE.md` files **point here** — they do
not restate this policy (restated policy drifts; that drift is what this file
exists to kill). The threads skill is the home because the ADR *lifecycle*
(promotion from a thread, provenance, cross-machine reconciliation, handback) lives
here too.

## What an ADR is / when to write one

An ADR records a **project choice among real alternatives**. Write one only when
all four hold:
1. Real alternatives were on the table.
2. Consequences ripple beyond the immediate change.
3. A future agent could reach the wrong conclusion without the recorded *why*.
4. There is evidence — `research-backed` and/or `experiment-validated`.

Bug fixes, parameter tuning, refactors, and thread-internal experiments do **not**
qualify. Promotion is main-agent review with the user at thread close, not
auto-extraction.

## Header schema

`Status` (`proposed` → `accepted` → `deprecated`) · `Date` · `Evidence`
(`research-backed` / `experiment-validated`; ≥1 required to be `accepted`) ·
`Decided in` (originating thread + plan-hop) · then `Context · Decision ·
Alternatives · Consequences · Verification`.

(`Supersedes` / `Superseded by` are **legacy** fields — present only on records
created before 2026-06-29. Do not add them to new ADRs; see Amendment below.)

## Amendment in place (policy, 2026-06-29 — unified)

A landed ADR is **amended in place** — corrected, trimmed, or rewritten,
**including when the decision itself changes** — whenever it is wrong or stale,
**provided the edit appends a provenance trailer** citing the git SHA holding the
pre-amendment version:

> `> Amended in place 2026-06-29; pre-amendment text at git <SHA>:<path-to-adr-file>`

The `rm`-with-backup principle: in-place change is safe because git *is* the
backup, one `git show` away. Successive amendments **append** trailers (each points
one hop back), never overwrite the prior — the chain walks the history. Requires
the pre-edit state already committed so the cited SHA exists.

**Supersession-by-new-number is retired.** Do not mint a new ADR number for a
changed decision — reuse the existing ADR's number and amend its body. (Existing
superseded records stay as history; the retirement is forward-looking.) This
replaces the prior "decision change → new superseding ADR" split.

## ADR number namespace

ADR numbers are an **append-only, shared namespace**: once assigned, a number is
permanent and is **never reused for a different decision**. Numbering gaps are
fine (a retired/never-landed number is simply skipped). Per-project sub-namespaces
(e.g. `ADR-PL-<BLOCK>-NNN`) follow the same rules independently.

Amendment-in-place and the number namespace are **orthogonal**: amendment governs
an ADR's *body*; the namespace governs its *number*. Amending a decision in place
does **not** free or change its number.

## Cross-machine number assignment

The namespace is shared across every clone/machine. A number assigned on one
machine but not yet pushed is invisible to the others, so two machines can grab the
same one (this happened 2026-06-29: two ADR-019s). Before assigning a new number,
on **any** machine:

1. `git fetch origin` then `grep '^## ADR-' <ledger> | tail` — the true high-water
   mark **including unmerged origin**.
2. Add the highest *in-flight* (authored-but-unpushed) number any other machine
   holds — check the shared claims ledger and/or ping the other machine.
3. Take the next number above that. A temporary gap pending another machine's push
   is expected and fine.

**Shared ADR-claims ledger** (lives in the project repo): `number | title |
owner-machine | status (authored/pushed/landed)`. An entry goes in the moment a
number is *authored*, not when it lands — so unpushed claims are visible.
Renumbering is expensive (it sweeps every citing doc), so coordinate the number
**before** landing.

## ADR stores (per project)

ADRs are **project-scoped data** and live in the project repo, deliberately
**outside** the transient `.threads/` workspace. A project may tier them (system /
cross-block / per-block); each project declares its store paths so the tooling and
the next reader know where they are:

- **Human manifest:** the project `CLAUDE.md` names the tiers + points here.
- **Machine-readable:** `<project>/.threads/adr-stores.json` (optional) — the
  threads indexer surfaces it into `threads.json::adr_stores` if present:
  ```json
  { "stores": [ { "tier": "system", "path": "docs/decision-log.md" },
                { "tier": "per-block", "path_glob": "docs/architecture/**/decisions.md" } ],
    "policy": "threads skill references/adr-conventions.md" }
  ```
