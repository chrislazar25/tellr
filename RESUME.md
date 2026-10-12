# Portfolio experiment — paused for computer restart

The user requested a pause on 2026-10-11. Resume the current portfolio phase;
do not restart the project or repeat live API calls unnecessarily.

## Saved

- The six supplied WAV files are in `audio/`.
- `artifacts/live_clips.json` contains the completed live measurement: both
  Scribe v2 modes, both Sonnet 4.6 term extractions, user-confirmed true terms,
  audio hashes, cache provenance, measured wall time, and returned API usage.
- All six clips had equal extracted money terms between modes. Scribe retained
  the amount correction, reaffirmation, fifteen/not-fifty distinction, and
  account 42/sorry24 correction in both modes. No cleanup-induced money
  divergence was observed in these recordings. The clean clip's cleaned text
  says "Paid" rather than "pay"; this did not alter its extracted money terms.
- Recordings say "today": extraction receives reference date `2026-10-11`.
  Vendor-001 destinations are spoken suffix `001`; vendor-002 is suffix `24`.
  Amounts/vendors were explicitly confirmed by the user. Do not silently
  relabel any additional discrepancy with these labels.
- Initial measurement-runner unpacking error was fixed and regression tested.
  That preliminary attempt had already cached the first clip's transcripts;
  the completed artifact records those cache hits. Its API usage/cost excludes
  the failed preliminary attempt. No invoiced API cost was returned; costs are
  null, not invented or substituted with estimates.
- Code changes are committed: measurement runner, explicit reference-case
  oracle/replay support, independent extraction replay, reference-date prompt
  and cache invalidation. Relevant tests passed on the real local `rs0`.
- No payments or approvals were made by the live audio measurement command.
  The existing Atlas active rules and healing history remain untouched.

## Remaining work, in order

1. Check extracted terms against user-confirmed truth (including account suffix
   matching and date); flag discrepancies. Save a clean complete experiment
   JSON/schema suitable for a future video. Current file is audio acquisition
   data, not the completed baseline/healing artifact.
2. Baseline empty-rules, cleaned approval on measured cases, including queued
   destination change and benign memo/name twins. Use an owned Atlas experiment
   database for fresh policy history; seed first, never erase the prior demo.
   Derive measured expectations from observed divergence, clearly separate
   live recordings from intentionally lossy synthetic fixture/holdout cases.
   Existing `replay_suite(..., cases=...)` supports measured cases. Its receipts,
   approval snapshots and labels are returned before owned scratch DB cleanup.
3. Use real Opus 5.5 through `heal.run_heal` with a measured-case replayer.
   Capture every candidate/decision/gate reason, each per-set scoreboard,
   final replay, English rules, wall time and returned usage. Keep holdouts
   entirely out of proposal context. Max 3 iterations, 8 active rules, 5 proposals.
4. Write measured-only `RESULTS.md`, rewrite README with problem, Mermaid flow,
   oracle/gate method, measured results, fixture/live run instructions and small
   eval-set limitations. User says it started at MongoDB x Cerebral Valley
   harness hackathon; make no submission or award claim.
5. Full-history secret scan (gitleaks if available; otherwise key/password
   pattern scan without printing secret values), report findings. Check/remove
   generated egg-info (already ignored; none tracked), add MIT license. Suggest
   three repository names but do not rename. No UI/video rendering requested.
6. Full local-rs0 test suite, atomic explicit-path commits, push private repo,
   report actual divergence, before/after scoreboard and secret-scan result.

## Commands and constraints

```sh
uv sync --frozen --python 3.11
docker compose up -d --wait
uv run pytest -q
# Only if acquisition needs resuming; evidence cache prevents repeated calls:
ADAPTERS=live uv run python -m scripts.measure_audio --reference-date 2026-10-11
```

Tests are local-only; demos/experiment use configured Atlas. `.env.secrets`
holds credentials, is ignored, and must never be printed or staged. Stage explicit
paths only; one logical change per commit, commit as soon as relevant tests pass.
Never stage `.env*`, lockfile churn or other task files. No repository rename.

Private remote: `chrislazar25/mongodb-hackathon-placeholder`.
