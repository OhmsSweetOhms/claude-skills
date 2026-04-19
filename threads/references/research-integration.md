# Bidirectional linking with `/research`

The `/threads` and `/research` skills are designed to compose. A
debug thread often spawns a research session ("we don't know enough
about X; let's go research it before continuing"); occasionally a
research session uncovers a bug worth turning into a thread. Both
directions need a back-pointer so future sessions can navigate
either way.

## The two fields

### Thread side: `thread.json.linked_research[]`

Array of entries. One per linked research session.

```json
{
  "linked_research": [
    {
      "path": ".research/session-20260416-215648",
      "title": "Pseudorange ±500µs ambiguity + nav-bit lock",
      "spawned_by_this_thread": true,
      "consumed_artifacts": [
        "report.md",
        "three-pseudorange-architectures.md"
      ]
    }
  ]
}
```

- `path` — repo-relative path to the research session directory.
- `title` — copy of the session's `session-manifest.json.title`.
  Cached here so the thread README's research-linkage table doesn't
  need to load the manifest.
- `spawned_by_this_thread` — boolean. `true` means the thread
  preceded the research and asked for it; `false` means the
  research existed first and the thread consumed it.
- `consumed_artifacts[]` — optional, list of specific filenames
  inside the session directory the thread referenced. Helps a
  future reader see "the thread cared about *these* parts of the
  research, not the whole report."

### Research side: `session-manifest.json.spawning_thread`

A single string. Optional field — the `/research` schema doesn't
require it.

```json
{
  "session_id": "session-20260416-215648",
  "title": "...",
  "...": "...",
  "spawning_thread": "receiver/20260414-nav-anchor-precision"
}
```

The value is the canonical thread `id` (matches
`thread.json.id`), no leading `threads/` prefix. The path is
derived: `threads/<spawning_thread>/`.

`spawning_thread` is set when the research was spawned BY a thread.
If the research was independent (came first, then the thread
consumed it), this field is omitted — the link only goes thread →
research, not back.

## When to maintain the link

| Situation | What to do |
|-----------|-----------|
| Thread spawns research (user invokes `/research` from inside a thread context) | Add to `linked_research[]` with `spawned_by_this_thread: true`; write `spawning_thread` into the new session's manifest. |
| Existing research is consumed by a thread (thread cites a session in its plan) | Add to `linked_research[]` with `spawned_by_this_thread: false`; do NOT write `spawning_thread` (the research existed independently). |
| User retroactively connects an old thread to an old session | Ask which direction (`spawned_by_this_thread` true or false). Add to `linked_research[]`. Write `spawning_thread` only if `true`. |
| Thread has linked research; the research session gets renamed/moved | Update `linked_research[].path`. If `spawning_thread` was set, update it too (well — the new session-manifest.json carries the new id; nothing to update there if you overwrote). |
| Research session has `spawning_thread` set, but the thread's `linked_research[]` doesn't list it | Sync: add the missing entry to `linked_research[]`. Ask the user before assuming `spawned_by_this_thread`. |

## How `/research` could collaborate (future)

The `/research` skill currently doesn't know about threads. It
could grow a `--thread <id>` flag that, on session end:

1. Looks up `<repo>/threads/<id>/thread.json`.
2. Appends an entry to its `linked_research[]` with
   `spawned_by_this_thread: true`.
3. Writes `spawning_thread: <id>` into the new session's
   `session-manifest.json`.

That's a `/research` change, not a `/threads` change — but the
contract on the threads side is what this skill establishes, so a
future `/research` integration has a clear spec to target.

Until that integration ships, the user does the linking manually
via the **Link research** workflow.

## Schema-safety

The `/research` `session-manifest.json` schema (at
`~/.claude/skills/research/schemas/session-manifest.json` if the
research skill is installed) does NOT set
`additionalProperties: false`. That means adding an unknown
`spawning_thread` field is schema-safe — no validators will
reject it.

If the schema ever tightens (`additionalProperties: false`), the
field would need to be added to the schema's `properties` block
first. Coordinate with the `/research` maintainer before assuming
that's already done.

## Schema field-naming reminder

The bidirectional link uses identical field names where the
concepts overlap:
- Thread side `linked_research[].path` and research side
  `spawning_thread` both store thread/session locations as strings.
- Thread side `linked_research[].title` mirrors the research
  session's `session-manifest.json.title` — cache the same string,
  don't paraphrase.

This naming consistency mirrors the
`thread.json.promotions[].{from,to}` ↔
`threads.json.promotion_log[].{from,to}` rule: same concept,
same field name across scopes.

## What this looks like in practice

The `gps_design` repo's first thread
(`threads/receiver/20260414-nav-anchor-precision/`) shows a worked
example:

```json
// thread.json
{
  "linked_research": [
    {
      "path": ".research/session-20260416-215648",
      "title": "Pseudorange ±500µs ambiguity + nav-bit lock",
      "spawned_by_this_thread": true,
      "consumed_artifacts": [
        "three-pseudorange-architectures.md",
        "brainstorm-pseudorange-zynq-kpml.md"
      ]
    }
  ]
}
```

The research session's `session-manifest.json` would gain
`"spawning_thread": "receiver/20260414-nav-anchor-precision"` on
the research side (currently set manually; future `/research`
integration would set it automatically).
