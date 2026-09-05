# Codex mailbox doorbell

Use this for an explicitly authorized Codex orchestrator/worker exchange. It
supplements existing question files and the atomic answer writer; it does not
replace the host job runner or authorize worker launches. No hook is required.

The packet producer records `codex-mailbox-route.json` in the inbox with exactly:

```json
{
  "orchestrator": "<actual orchestrator session UUID>",
  "worker": "<actual worker session UUID>"
}
```

Bind actual session UUIDs, never guessed names or the packet author's session.
Record each session's identity from its own `CODEX_THREAD_ID`. Both sessions must
know the inbox. Do not retarget a live exchange; a successor requires the owner's
or operator's explicit transfer and a new route. This is routing, not a security
boundary against agents already authorized to edit the same files.

On this installation, invoke the helper with host execution (`require_escalated`)
from the first call: `codex queue` needs writable access to its state database and
daemon, unavailable inside the workspace sandbox. Obtain normal tool approval where
required. This is scoped to the doorbell; it does not disable sandboxing globally
or authorize unrelated host work. Do not probe inside the sandbox first.

Write the question or handback first. Then, from the bound worker session:

```bash
python3 "$HOME/.claude/skills/threads/scripts/ring_codex_mailbox.py" \
  --inbox "$PACKET_INBOX" --event OPEN_QUESTION --file questions/q-01.md
```

The recipient receives only `OPEN_QUESTION <absolute question path>`. It reads
the file and applies the existing answer/escalation contract. An authorized answer
uses `answer_question.py`; only after its atomic write, the orchestrator rings:

```bash
python3 "$HOME/.claude/skills/threads/scripts/ring_codex_mailbox.py" \
  --inbox "$PACKET_INBOX" --event ANSWER_READY --file questions/q-01.md
```

The worker reads the resolution and proceeds. For completion it may send
`HANDBACK` with `--file handback.json`. That notification is not handback acceptance;
the orchestrator still validates the artifact and evidence.

The helper checks sender binding, event direction, file containment and question
state before calling `codex queue`. It records delivery under `doorbells/`;
the producer must use an ignored/local inbox or explicitly ignore these receipts
(the helper creates `doorbells/.gitignore` containing `*` before any receipt).
Never force-add receipts; ignore rules do not untrack previously committed files.
Audit any existing tracked receipts before adoption. It suppresses repeated identical events and retains failed/uncertain
attempts. Queue success means accepted by the queue, not consumed by the recipient.
Recipients treat repeated notifications idempotently and inspect current file state.
After a queue timeout or ambiguous delivery, inspect the receipt and recipient before
any deliberate recovery; the helper refuses an automatic duplicate send.

For a `failed` receipt, first inspect the error and confirm that the message was
not delivered; nonzero exit alone is not proof. With explicit operator approval,
retry once on the host using `--retry-failed-reason "<evidence and authorization>"`.
The receipt retains the complete original attempt and recovery reason before the
retry runs. Another failure exhausts recovery; `sending` and `uncertain` receipts
cannot be retried through this flag. Never delete receipts to force a resend.
Do not disable sandboxing or hook trust.
Before touching the inbox, the helper checks writable access to Codex home and
refuses with `HOST_EXECUTION_REQUIRED` if unavailable. No receipt is created, so
the next host invocation remains the first delivery attempt. This preflight catches
the observed read-only sandbox boundary; it is not a guarantee against later
database, daemon or network failures.
The producer verifies delivery on its actual runtime before relying on the exchange.
After ringing, end the turn and remain redirectable. No model-visible polling.
For long external commands use the existing mailbox-job contract and its bounded
wait/cancellation rules. Its `codex-self` completion doorbell remains unchanged.
