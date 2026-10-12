# LinkedIn post draft

For tellr, I tested whether adding a second speech recognizer makes payment
approval safer.

I compared cached ElevenLabs Scribe v2 and local faster-whisper small transcripts,
extracted payment terms with the repo’s Sonnet 4.6 adapter, then replayed policies
through the real MongoDB worker and oracle on Atlas.

The corpus is an exploratory failure hunt: 180 selected payment clips generated
with ElevenLabs stock voices, with simulated phone channels. I kept every attempt.
Twelve requested an account absent from the vendor file and were marked for
clarification outside the commit comparison; seven had no complete A instruction.
Roger and Charlie supplied training cases; Alice was withheld from the proposer.

On the same eligible suite:

- Requiring both recognizers to agree caught 22 of 23 unsafe commits, but blocked
  42 of 138 correct payments.
- Checking the spoken account against the vendor file caught the same 22, with
  zero correct payments blocked.
- Live Opus 5.5 proposed that account check. Its replay-gated rule reached the
  same result in one iteration.

One Alice holdout still failed: “fourteen” became $40 in both recognizers. Its
available rule fields match 12 correct $40 cases, so this rule language cannot
separate them without false blocks.

A separate confirmation finding was striking: Whisper turned “No, don't send it”
into “Thank you very much.” Sonnet labeled that UNCLEAR, not authorization. That
was an observation across the saved confirmation data, not a payment rule test.

These are synthetic voices and selected inputs, not deployment error rates.
Holdouts participate in gating, and the corpus designers had already seen all
voices. “Payments” are MongoDB receipts, not transfers. All transcripts, candidate
decisions, per-set scores and audio remain saved for review.

#MongoDB #AIEvaluation #Python

---

Fact sources: [RESULTS.md](RESULTS.md), [failure-hunt experiment](artifacts/failure_hunt_experiment.json).
Generated audio: [ElevenLabs](https://elevenlabs.io), stock Roger, Charlie and Alice.
This is an unpublished draft; [tellr](https://github.com/chrislazar25/tellr) remains private.
