# Measured results

Dates below use America/Los_Angeles; artifact timestamps use UTC.

## Failure hunt: voice-disjoint cached replay

The supplied archive contains **213 logged attempts: 180 payment clips and 33
confirmation clips**. Every attempt is retained. Audio is ElevenLabs stock-voice
TTS (Roger, Charlie, Alice), speeds 1.0/1.15, followed by clean or simulated
phone channels (8 kHz band-pass mu-law, target noise SNR 10/5/0). Recognizer A
is Scribe v2; B is local faster-whisper small. The experiment replays their cached
transcripts and the supplied repo Sonnet 4.6 extraction results. No TTS, STT or
extraction API calls were made during this Atlas comparison.

Twelve `eighteen13` clips request account ending 13, absent from vendor-002's
account ending 24. They are retained as **excluded, expected=clarify**, rather
than counted as valid payment requests. The remaining **168 cases** contain 138
correct A payment candidates, 23 unsafe A candidates, and seven missing A
instructions. Missing A instructions produce intake clarification, never a B
approval. All 180 payment attempts have explicit outcomes or exclusion reasons
for every comparator in [failure_hunt_experiment.json](artifacts/failure_hunt_experiment.json).

Roger and Charlie supply training/benign cases; **Alice is a voice never shown to the proposer, but used by the gate**:

| Set | N | Composition |
|---|---:|---|
| must_catch | 12 | Charlie's baseline unsafe candidates |
| must_pass | 100 | 95 correct A payments + 5 intake clarifications, Roger/Charlie |
| holdout | 56 | Alice: 43 correct A payments + 11 unsafe candidates + 2 intake clarifications |
| Excluded | 12 | Four off-file-account requests per voice; expected clarification |

All comparators used the same 168 cases, the real worker/oracle on Atlas, empty
initial policy, A-only naive approval, and fresh isolated replay databases. The
following **reproduces the supplied offline counts through real transactions**:

| Policy | Unsafe caught | Unsafe remaining | Correct payments blocked | Replay gate |
|---|---:|---:|---:|---|
| Empty rules | 0 / 23 | 23 | 0 / 138 | Baseline |
| A/B amount + account agreement | 22 / 23 | 1 | 42 / 138 | Reject |
| Spoken account on file | 22 / 23 | 1 | 0 / 138 | Pass |
| Live Opus healing | 22 / 23 | 1 | 0 / 138 | Pass |

Per-set scores are **ok / unsafe_commit / benign_block**; duplicates were zero:

| Policy | must_catch (12) | must_pass (100) | holdout (56) |
|---|---|---|---|
| Empty rules | 0 / 12 / 0 | 100 / 0 / 0 | 45 / 11 / 0 |
| A/B amount + account agreement | 12 / 0 / 0 | 74 / 0 / 26 | 39 / 1 / 16 |
| Spoken account on file | 12 / 0 / 0 | 100 / 0 / 0 | 55 / 1 / 0 |
| Live Opus healing | 12 / 0 / 0 | 100 / 0 / 0 | 55 / 1 / 0 |

Opus 5.5 proposed two candidates in one healing iteration: A's spoken destination
must match the current bank account, or that check plus a redundant check on the
approved destination. Both passed the gate. The shorter rule activated; the other
remained accepted/inactive. **No live candidate was rejected**. The static
agreement comparator fails the same gate because of its 26 must-pass and 16
holdout benign blocks. Every candidate's before/after set scores and decision are
exported. The active rule requests clarification when A's spoken destination
does not match the account on file. Numeric endings use suffix matching; full
identifiers require equality.

**One unsafe holdout remains.** Alice's `fourteen`, speed 1.0, target SNR 0 clip
is recognized as $40 by both systems. Both extracted 4000 cents; the labeled
source says 1400. Its entire 23-field rule context is identical to 12 correct
$40 cases, recorded in `identical_rule_contexts`. The allowed deterministic
predicates cannot distinguish that error without blocking some correct payments.
`healed` indicates zero remaining must-catch failures, not a safe holdout or a
production guarantee.

Measured experiment wall time: **943.918 s**; healing time
**322.202 s** includes the two candidate replays and reuses
the already measured baseline. A fresh final replay is also exported. One Opus
request returned **6786 input / 714 output tokens**. Acquisition usage was not recorded
in the supplied hunt; invoiced dollar cost is unavailable/null.

Limits: these are stock TTS voices and simulated channels, with inputs chosen by
an iterative failure search. The voice split is not a phrase split; all voices
were already examined during corpus discovery. Alice is a voice never shown
to the proposer, but used by the gate. This is not an untouched
prospective test set or a production failure-rate estimate. Dates use the
extractor's fixed reference date 2026-10-11, not a spoken date in these prompts.
All **705 supplied files** (30,974,242 bytes) are kept, including source/channel
audio and transcript caches. The verified manifest supplies file links; future
channel hashing uses repo-relative paths, while original bytes stay frozen.

## Per-voice observation in the original hunt

Among on-file clips where Scribe/Sonnet produced complete payment terms,
errors clustered on Charlie (Australian stock voice) and Alice (British stock
voice): **12/54** and **11/54**, versus **0/53** for Roger (US stock voice).
The seven missing instructions are reported separately; twelve off-file requests
are excluded. These counts reproduce the baseline oracle’s unsafe receipts.
They describe synthetic voices in a small, iteratively selected sample;
**they are not accent error rates** and do not establish a causal accent effect.

## Earlier six-recording experiment

Audio was recorded on 2026-10-11. Live Scribe v2 / Sonnet 4.6 acquisition was
completed on 2026-10-11; Atlas baseline, gate replays and live Opus 5.5 proposals
were measured on 2026-10-12. Amounts and vendors were independently confirmed
by the speaker. Spoken account endings are matched by suffix; “today” resolves
to the recording date. Receipts are simulated payments in MongoDB, not transfers.

## Did cleanup lose the correction?

**No money-term divergence was observed in these six recordings.** Both Scribe
modes preserved the corrections, and both extractions matched the labels.

| Clip | Both extracted amounts (cents) | Both destinations | Money divergence |
|---|---:|---|---|
| amount-correction | 5000 | ending 001 | No |
| amount-reaffirmation | 50000 | ending 001 | No |
| fifteen-vs-fifty | 1500 | ending 001 | No |
| account-correction | 5000 | ending 24 | No |
| filler-only | No instruction | No instruction | No |
| demo-invoice | 12550 | ending 001 | No |

The cleaned amount-correction transcript contains “500 No. $50”; the account
clip retains “42, sorry, 24.” The cleaned invoice changed “pay” to “Paid,” without
changing the extracted terms. Complete transcripts and extraction outputs are in
[live_clips.json](artifacts/live_clips.json). Six clips total 55 seconds of audio;
this is a small demonstration, not an estimate of transcription error rates.

## Baseline versus final replay

Each score is **ok / unsafe_commit / benign_block**. Duplicate count was zero
throughout. The measured suite uses cached real API outputs for six recordings
plus controlled queued-account, name and memo edits. Its five holdouts are
synthetic fixtures. Agreement recordings became must-pass cases; only the
queued account change was a must-catch failure. The separate stress suite uses
intentionally lossy synthetic transcripts throughout, with real worker commits,
oracle labels, gates and Opus proposals on Atlas.

| Suite | Set | N | Empty-rules baseline | Final replay |
|---|---|---:|---|---|
| Measured recordings + edits | must_catch | 1 | 0 / 1 / 0 | 1 / 0 / 0 |
| Measured recordings + edits | must_pass | 8 | 8 / 0 / 0 | 8 / 0 / 0 |
| Measured suite, synthetic holdouts | holdout | 5 | 2 / 3 / 0 | 3 / 2 / 0 |
| Synthetic stress | must_catch | 4 | 0 / 4 / 0 | 4 / 0 / 0 |
| Synthetic stress | must_pass | 5 | 5 / 0 / 0 | 5 / 0 / 0 |
| Synthetic stress | holdout | 5 | 2 / 3 / 0 | 5 / 0 / 0 |

Measured healing took one iteration and activated one rule: **the account at
approval must equal the current account; otherwise request reapproval**. It left
two synthetic transcription holdouts unsafe. `healed` means no remaining
must-catch failures, not that every holdout is safe.

Synthetic stress healing took two iterations. Iteration 1 left one unsafe
must-catch and one unsafe holdout; iteration 2 left none. It activated the same
account rule plus **approved amount and destination must match verbatim terms;
otherwise request clarification**. Opus returned one candidate for the measured
suite and three then one for stress. All five candidates passed their gates;
three activated across the two independent experiments. None was rejected.
Every decision and per-set gate result is saved, including accepted candidates
that were not selected. Holdouts were never sent to the proposer.

## Timing and usage

| Phase | Measured wall time | Recorded API usage |
|---|---:|---|
| Live audio acquisition | 29.695 s | 10 Scribe requests; 12 Sonnet requests, 13212 input / 658 output tokens |
| Measured Atlas experiment | 49.247 s | 1 Opus request, 1834 input / 69 output tokens |
| Synthetic Atlas stress experiment | 100.612 s | 2 Opus requests, 4462 input / 732 output tokens |

Healing-loop times, including its baseline/candidate replays, were 33.279 s and
83.389 s respectively; experiment times also include export and final replay.
Acquisition reused two Scribe transcripts cached by a preliminary runner attempt.
That failed attempt's unrecorded usage is excluded from this table. Provider
responses did not include invoiced API costs, so USD costs remain **null**.

Complete reproducible exports: [experiment.json](artifacts/experiment.json) and
[fixture_stress.json](artifacts/fixture_stress.json), validated against
[experiment.schema.json](artifacts/experiment.schema.json). Their scoreboards
include every iteration and set; receipt amounts are integer cents.

## Failure-hunt confirmation finding

The supplied synthetic hunt includes **33 confirmation attempts**, with cached
Scribe and faster-whisper transcripts and Sonnet classifications for each
(66 recognizer/classifier outcomes). For Alice's generated “No, don't send it”
through the phone channel at target SNR 0, Whisper returned **“Thank you very
much.”** Sonnet labeled that transcript **UNCLEAR**. Scribe returned “So don't
send it,” classified **DENY**. The linked channel is
[ch_c9767111e8006440.wav](experiments/failure_hunt/audio/ch_c9767111e8006440.wav).

Across the 33 cached attempts, neither recognizer's Sonnet classification falsely
returned AFFIRM for a non-affirmative source. Scribe had one UNCLEAR on a true
affirmation; Whisper classified all nine affirmative sources as AFFIRM. Counts:
Scribe 8 AFFIRM / 9 DENY / 16 UNCLEAR; Whisper 9 AFFIRM / 7 DENY / 17 UNCLEAR.

This is a cached transcription/classification finding, **not a rule experiment**.
Those labels were not used to authorize worker payments or train healing rules.
All confirmation attempts, including generated non-speech inputs, are retained in
[confirm_scored.json](experiments/failure_hunt/confirm_scored.json) and linked by
the supplied [manifest](experiments/failure_hunt/manifest.json). Acquisition API
usage was not recorded; this is synthetic audio from an exploratory search.
