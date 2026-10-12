# tellr — completed failure-hunt integration

GitHub repository: `chrislazar25/tellr`, renamed at the user's explicit request.
Visibility remains private. The local directory retains its existing path.

Latest result: `artifacts/failure_hunt_experiment.json`, validated against its
schema. All 180 payment attempts and 33 confirmations are retained, including
explicit outcomes for 12 excluded off-file-account requests. Eligible set:
12 must_catch, 100 must_pass, 56 Alice holdouts. Roger/Charlie supply training.
Seven absent A instructions are intake clarification outcomes, never B approvals.

Actual Atlas comparator metrics:
- Empty rules: 23 unsafe, 138 correct payments, seven intake clarifications.
- A/B agreement: catches 22/23 unsafe; blocks 42/138 correct payments; gate rejects.
- Spoken account on file: catches 22/23; blocks 0/138; gate accepts.
- Live Opus healing: same 22/23 and 0/138; one iteration, two accepted candidates,
  shorter activated, other accepted/inactive. One Alice fourteen-to-$40 holdout
  remains; its allowed rule context equals 12 correct $40 cases.

All 705 supplied audio/source/cache files remain committed. Use the supplied
`experiments/failure_hunt/manifest.json`, never recompute old absolute-path hashes.
Future acquisition hashes repo-relative paths and logs explicit file links.
All hunt keys use `experiments/failure_hunt/keys.py` and ignored root env files.
Cached scorer imports and default replays make no STT/extraction calls.

The refusal-to-"Thank you very much" confirmation finding is documented separately:
Sonnet classified it UNCLEAR; no payment rule or authorization used that label.
README, RESULTS and unpublished LinkedIn draft disclose TTS/channel/search limits.
The voice split withholds Alice from the proposer, not phrases or prior discovery.

Full local rs0 tests: 241 passed. All pipeline/worker/oracle/gate comparisons used
Atlas; scratch databases were removed and candidate audits remain persisted.
Earlier six-recording and synthetic-stress exports remain available unchanged.
Actual API billing is unavailable/null; the live proposal token usage is saved.

```sh
uv run python scoreboard.py --artifact artifacts/failure_hunt_experiment.json
uv run python scoreboard.py --artifact artifacts/failure_hunt_experiment.json --json
```

Preserve independent labeled truth. Do not silently relabel or discard failed
attempts. Do not claim synthetic selected-input counts are deployment error rates.
Secrets remain in ignored `.env.secrets`; never print or stage them. Commit one
logical change at a time with explicit paths; do not stage `.env*` or lockfile churn.
The LinkedIn post has not been published. Repository visibility must stay private.
