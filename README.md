# MongoDB Hackathon

Python 3.11 backend for a fixture-driven payment approval pipeline. No UI or API
keys are required for local development. Payments are database receipts; no money
is sent by this prototype.

```sh
uv sync --frozen --python 3.11
docker compose up -d --wait
cp .env.example .env
uv run python -m db.seed
uv run python pipeline.py
uv run pytest -q
```

An empty `MONGODB_URI` uses the local single-node `rs0` replica set. Local MongoDB
is exposed only on loopback. Real credentials belong in ignored `.env` files.
The existing `.env.secrets` file is not loaded automatically.

Without uv, use Python 3.11: `python3.11 -m venv .venv`, activate it, then
`pip install -e . pytest`. Run the same seed/demo commands using `python` and tests
using `pytest`.

`python -m db.seed` creates `vendors`, `fences`, `evidence`, `intents`, `queue`,
`payments`, `rule_versions`, and `runs` with strict `$jsonSchema` validators.
It installs a unique index on `payments.idempotency_key` and seeds three fictional
vendors with version-zero fences. Re-running it preserves existing vendor accounts
and fence versions. Monetary fields accept only positive signed 64-bit integer
cents, never floats, decimal values, or booleans.

`pipeline.py` uses `fixtures/pipeline.json`: transcription (verbatim by default),
term extraction, text readback, evidence/intent storage, queue insertion, and one
worker pass. No actual audio file is needed for `demo-invoice.wav`. Use
`python pipeline.py --normalized` for the normalized transcript. Evidence, intent,
and queue insertion share one transaction. Each demo invocation creates a new
intent, so running it twice intentionally creates two receipts.

`python worker.py` claims one pending queue item using `find_one_and_update`.
After claiming, one transaction writes the vendor fence, reads the vendor/intent
and active rules, evaluates the rules, and inserts the receipt plus marks the queue
done, or marks it blocked with a reason. The receipt's idempotency key is exactly
the queue item's string ID. No active rules means allow all; the independent
destination check still requires the intent's account to match the vendor account
read in that transaction. A changed account requires reviewed instructions.

`db.vendors.update_vendor_bank_account(db, vendor_id, bank_account)` updates the
account and increments that same fence in one transaction. All account changes
must use this function. The shared write causes conflicting snapshots to abort;
PyMongo's `with_transaction` retries `TransientTransactionError` and retries commit
on `UnknownTransactionCommitResult`. See the [PyMongo transaction documentation](https://www.mongodb.com/docs/languages/python/pymongo-driver/current/crud/transactions/).

`rules.evaluate(rule, context)` is the pluggable allow-all stub. It returns `None`
to allow, or a non-empty reason string to block. Pass `evaluator=` to worker
functions to replace it. Hooks must have no external side effects because the
transaction callback may run again. A receipt stores the vendor account, fence
version, and evaluated rule-version IDs for audit.

`runs` records `{run_id, step, inputs, outputs, ts}`. Logs for transaction steps
commit or roll back with the payment; retry/error records are separate. The
`observability.trace` wrapper no-ops until `RAINDROP_WRITE_KEY` and an explicit
`configure_trace` transport are supplied. Transcription, extraction, readback,
and remote tracing remain stubs; merely setting keys does not switch them to
live services. Keys and connection URIs are never logged.

Tests use only local MongoDB and a fresh randomly named database per test, removed
afterward. They do not load `.env` or consult `MONGODB_URI`. If you use another
loopback port, set `MONGO_PORT` for Compose and `TEST_MONGODB_URI` for tests (including
`replicaSet=rs0&directConnection=true`); set `MONGODB_URI` separately for the app.
Integration tests fail clearly if the replica set is unavailable. To run just
the key-free unit tests, use `uv run pytest -m 'not integration'`.

The suite covers competing workers, real fence write conflicts in both race
orders, stale-destination blocking, empty rules, receipt uniqueness, integer cents,
seed idempotence, transaction rollback, adapters, logging, and trace gating.
GitHub Actions runs the full suite against this Compose replica set on Python 3.11.

On an unrecoverable worker error, the item stays claimed. Lease expiry and automatic
crash recovery are not implemented yet. `process_claimed(db, claimed_item)` can
resume the existing claim and returns an existing completed receipt without
duplicating it. Avoid resetting ambiguous claims to pending. Stop local MongoDB
with `docker compose down`; its named volume persists between starts.
