# MongoDB Hackathon

Python 3.11 backend with fixture/live adapters, dual transcription, baseline approval,
and fenced MongoDB transactions. No UI. Payments are database receipts; this
prototype does not send money.

```sh
uv sync --frozen --python 3.11
docker compose up -d --wait
uv run pytest -q
```

Tests always use the local `rs0` replica set and isolated databases. They never
read this project's `.env` or `.env.secrets`, and never use its Atlas URI. Each
test database is removed afterward. `TEST_MONGODB_URI` can select another loopback
port; include `replicaSet=rs0&directConnection=true`. Remote test hosts are rejected.
For unit tests only: `uv run pytest -m 'not integration'`.

The app loads settings in this order: process environment, `.env.secrets`, `.env`.
`.env.secrets` therefore wins over the blank URI in `.env.example`. An absent or
blank final `MONGODB_URI` falls back to local MongoDB. Keep real values in ignored
files, not `.env.example`. Keys and connection strings are never printed or logged.
The configured demo uses Atlas when its URI is in `.env.secrets`:

```sh
uv run python -m db.ping
uv run python -m db.seed
uv run python pipeline.py enqueue demo-invoice.wav --confirm-from verbatim
# Copy outcome.queue_id from the enqueue output.
uv run python scripts/change_vendor_account.py vendor-001 demo-account-updated
uv run python pipeline.py process --queue-id 'queue:YOUR_INTENT_ID'
```

`db.ping` prints exactly `ok` or `fail` and exits 0 or 1. `db.seed` installs eight
collections (`vendors`, `fences`, `evidence`, `intents`, `queue`, `payments`,
`rule_versions`, `runs`), strict validators, a unique payments idempotency index,
and three fictional vendors with version-zero fences. Re-running seed preserves
existing accounts and fence versions. Existing historical documents are not
migrated when validators are refreshed; unconfirmed legacy intents cannot pay.

`enqueue` never processes payments. For a combined demo or the `--hold` shortcut:

```sh
uv run python pipeline.py demo demo-invoice.wav --confirm-from verbatim
uv run python pipeline.py demo amount-correction.wav --confirm-from verbatim --hold
# Equivalent shortcut for a held demo:
uv run python pipeline.py --hold --confirm-from verbatim
```

Enqueue/demo commands transcribe the same audio twice, using `no_verbatim=False`
for verbatim and `True` for cleaned text. They independently extract terms from
each transcript and store both texts and both results in one evidence document.
A draft intent cannot be enqueued or paid. Explicit confirmation stores
`confirmed_terms`, `confirmed_at`, `confirmation_source`, `approved_bank_account`,
and `approved_fence_version`. The account/version pair is read from one snapshot
at approval. Worker evaluation can inspect that snapshot through `context['intent']`.

The naive baseline automatically approves the cleaned candidate by default,
including non-interactive CLI invocations and `run_pipeline`. Its approval
snapshot records this choice; it can preserve transcription mistakes deliberately
for oracle evaluation. `--confirm-from verbatim` overrides the baseline, while
`--confirm-from cleaned` is explicit default behavior. Use
`--confirmed-terms /path/to/approved-terms.json` for manual corrections. The file
must contain exactly `vendor_id`, `amount_cents`, `destination_account`, and an
ISO `date`. No interactive prompt is shown. `prepare_intent` remains a draft-only
API for future deliberate approval flows. Repeating confirmation with the same
terms preserves its original snapshot, and repeating enqueue does not create a
second queue item.

`STATIC_GUARD` defaults to `off`. With no active rules and the guard off, payment
uses the vendor's **current** bank account read inside the payment transaction.
Receipts retain `destination_account` from the approved instruction and record the
actual target in `paid_to` (also recorded as `vendor_account`). These may differ
after an account change. Set `STATIC_GUARD=on` to opt into blocking a requested
destination that differs from the current vendor account. Numeric destinations
represent spoken account endings: `24` matches `acct-0024`, while `42` does not.
Other destination identifiers require an exact match. Seed defaults vendor-002 to
`acct-0024`; existing seeded accounts remain untouched on reruns.

The worker atomically claims one pending queue item, then one transaction
increments the vendor fence, loads current vendor data and active rules, evaluates
them, inserts the receipt and marks done, or marks blocked with a reason.
Idempotency keys are exactly queue IDs. All monetary fields, including nested
candidate/confirmed terms, require positive signed 64-bit integer cents; floats,
decimals, and booleans are rejected. All new fields have validators.

All bank changes go through `db.vendors.update_vendor_bank_account` (used by the
change-account script): it increments the same vendor fence and changes the bank
account in one transaction. Real write conflicts cause callback retries; uncertain
commit results retry only the commit. See the [PyMongo transaction documentation](https://www.mongodb.com/docs/languages/python/pymongo-driver/current/crud/transactions/).
`rules.evaluate(rule, context)` remains an allow-all stub; injected evaluators
return `None` or a blocking reason. They must be free of external side effects.

The six fictional fixtures have independent, hand-labeled `true_terms`:

| Case | Audio fixture | Correct instruction |
| --- | --- | --- |
| 500, no, 50 | `amount-correction.wav` | 5,000 cents |
| 500, no wait, 500 is right | `amount-reaffirmation.wav` | 50,000 cents |
| Fifteen vs fifty | `fifteen-vs-fifty.wav` | 1,500 cents |
| Account ending 42, sorry, 24 | `account-correction.wav` | Vendor-002 `acct-0024` |
| Filler only | `filler-only.wav` | No terms; evidence only |
| Clean | `demo-invoice.wav` | 12,550 cents |

No actual audio files are needed for stubs. Cleaned candidates intentionally
simulate lost corrections in three cases; these are not measured Scribe results.
Ground-truth labels are used only by tests, never to extract or approve terms.

`ADAPTERS=fixture` is the default even when keys exist. Set `ADAPTERS=live`
for real audio files and configure `ELEVENLABS_API_KEY`, `ANTHROPIC_API_KEY`, and
optionally `CLAUDE_MODEL` (default `claude-sonnet-5-5`). Missing keys or API errors
fail visibly; live mode never silently substitutes fixture data. Readback stays
text-only. Inject an `interfaces.Adapters` bundle for isolated/offline tests.

```sh
ADAPTERS=live uv run python pipeline.py enqueue /path/to/audio.wav --confirm-from cleaned
```

The live adapter uses [Scribe v2](https://elevenlabs.io/docs/api-reference/speech-to-text/convert)
with `no_verbatim=False` and `True`, using identical audio bytes read once.
SHA-256 cache records live in `evidence` (`kind: cache`), distinct from complete
per-run evidence. Successful transcripts and extractions persist immediately,
so sequential reruns, renamed files, partial failures, and null terms reuse prior
calls. Cache hits are logged. Extraction caches include the model, prompt, tool
schema, and vendor catalog; changing these re-extracts but reuses Scribe transcripts.
Changed audio bytes invalidate both caches. Concurrent first-time submissions can
still make duplicate API calls; the cache does not implement distributed leases.

Claude calls `messages.create` with `EXTRACT_TERMS_TOOL`, `strict=True`, and a
forced named `tool_choice` with `disable_parallel_tool_use=True`. These parameters
were verified against the installed SDKs (ElevenLabs 2.71.0, Anthropic 0.125.0) and
[Claude strict tool-use docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/strict-tool-use).
The SDK's `transform_schema` removes constraints unsupported by the wire schema;
`validate_terms` still enforces positive 64-bit integer cents and real ISO dates
locally. Refusals, truncated or multiple tool calls, and invalid payloads fail.
Null means no complete payment instruction. The model sees the vendor catalog
and transcript, never fixture `true_terms`.

`runs` logs `{run_id, step, inputs, outputs, ts}`, including each transcription and
extraction variant, confirmation, queueing, and worker outcome. Transaction logs
commit or roll back with their writes; retry/error records are separate. Remote
tracing no-ops until both `RAINDROP_WRITE_KEY` and an explicit `configure_trace`
transport exist. No network calls run inside retryable payment callbacks.

GitHub Actions runs Python 3.11 tests and the split CLI demo against local Compose.
Compose binds MongoDB to loopback and sets its open-file limit to 64,000, following
[MongoDB's recommendation](https://www.mongodb.com/docs/manual/reference/ulimit/),
so repeated creation of isolated test databases cannot hit Docker's low default.
Stop it with `docker compose down`; the named data volume persists.

On an unrecoverable worker error, the item stays claimed. Lease expiry and
automatic crash recovery remain future work. `process_claimed(db, claimed_item)`
can resume a known claim without duplicating a completed receipt. Avoid resetting
ambiguous claims to pending. Without uv, install with Python 3.11 using
`pip install -e . pytest` inside a virtual environment.
