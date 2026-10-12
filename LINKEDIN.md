# LinkedIn post draft

I built a payment-approval prototype around one question: when a worker commits,
does the payment still match what the caller authorized?

First, I tested the transcription hypothesis. I ran six recordings through
ElevenLabs Scribe’s verbatim and cleaned modes, then extracted payment terms
independently with Claude Sonnet 4.6. Both modes retained the corrections:
“500, no, 50,” “fifteen, not fifty,” and “42, sorry, 24.” I observed zero
money-term divergences in those six clips. This experiment did not reproduce
cleanup dropping a payment correction.

The failure I did measure was downstream: change a vendor’s bank account after
approval but before payment, and the empty-rules baseline pays the new account.
A MongoDB transaction fence handles concurrent writes; an approval snapshot is
also needed to check account identity.

Claude Opus 5.5 proposed that check. After replay gating, the account-change case
was blocked for reapproval, while all eight recording-based must-pass cases
still passed. Two synthetic transcription holdouts remained unsafe; the live
training data had no transcription failure to teach that rule.

In a separate, explicitly synthetic stress suite, live model proposals reduced
unsafe commits from 7/14 to 0/14 in two iterations, with no benign blocks.
Holdout scenarios stayed outside the proposal prompt. Every candidate and gate
decision is recorded.

These are small evaluations, and “payments” are MongoDB receipts, not transfers.
The project started at the MongoDB x Cerebral Valley harness hackathon; I’m
developing it as a portfolio project with saved transcripts, measured results,
and reproducible replay artifacts.

#MongoDB #AIEvaluation #Python

---

Fact sources: [RESULTS.md](RESULTS.md), [measured experiment](artifacts/experiment.json),
[synthetic stress experiment](artifacts/fixture_stress.json). This is an unpublished
draft. The repository remains private.
