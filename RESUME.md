# Portfolio phase completed

The restart checkpoint has been completed. Acquisition was saved on 2026-10-11;
the Atlas experiments were run on 2026-10-12 (America/Los_Angeles).

- `artifacts/live_clips.json`: six real recordings, dual Scribe transcripts,
  independent Sonnet terms, user-confirmed truth, audio hashes and API usage.
  No money divergence or truth mismatch was observed. Corrections survived.
- `artifacts/experiment.json`: independent Atlas audit database, empty-rules
  baseline and one real Opus healing iteration. must_catch 1/1 ok, must_pass
  8/8 ok; synthetic holdout 3/5 ok with two unsafe transcription cases left.
- `artifacts/fixture_stress.json`: separately synthetic transcription stress
  inputs, real Atlas worker/oracle/gate measurements, real Opus proposals.
  Two iterations, final 4/4 must_catch, 5/5 must_pass, 5/5 holdout ok.
- Every candidate and gate result is exported; all five measured candidates
  passed their gates, three activated across two independent experiments.
  No rejected candidate was invented. Holdouts were not proposal inputs.
- `RESULTS.md`, rewritten `README.md`, and unpublished `LINKEDIN.md` use measured
  facts and disclose small-set limits. Dollar API cost is unavailable/null.
- `artifacts/experiment.schema.json` validates both complete experiment exports.
- `artifacts/secret_scan.json` records full-history scan scope and findings.
  `.gitleaks.toml` retains default detection with a narrow exemption for
  documented SHA-256 cache fingerprints. No credential values are in reports.
- MIT license added; generated egg-info removed and ignored. Repository remains
  private and retains the placeholder name. No post was published.
- Full tests: 220 passed on local MongoDB rs0. Tests never use Atlas/API keys.

Inspect saved scoreboards without any services:

```sh
uv run python scoreboard.py --artifact artifacts/experiment.json
uv run python scoreboard.py --artifact artifacts/fixture_stress.json --json
```

Future live acquisition can reuse persisted evidence caches. Do not relabel
recordings to obtain a preferred result. "Today" uses recording date 2026-10-11;
spoken destinations are suffix 001 or 24. All actual credentials remain in
ignored `.env.secrets`; never print or stage them. Continue atomic commits with
explicit paths, no `.env*` or incidental lockfile changes.
