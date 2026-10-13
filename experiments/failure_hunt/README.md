# Exploratory synthetic failure hunt

This archive preserves all 180 logged payment attempts, 33 logged confirmation
attempts, 360 cached Sonnet payment extraction results, and both recognizers'
cached transcripts. It was provided after an iterative input search, not sampled
randomly. `attempts.jsonl` includes the original simple parser's diagnostics;
`scored.json` contains the later repo Sonnet extractor's terms. The comparator
experiment uses the latter, never the float-based parser.

Audio was generated with ElevenLabs `eleven_flash_v2_5`, stock Roger, Charlie
and Alice voices, speeds 1.0 and 1.15. Channels include clean audio and a
simulated 8 kHz, 300–3400 Hz, mu-law phone channel with colored noise at target
SNR 10/5/0. Output is decoded back to 16 kHz WAV; target noise level uses
speech-active RMS, so it is not a measured end-to-end SNR after clipping.
Recognizer A is cached Scribe v2; B is cached local faster-whisper small,
CPU int8, English, beam size 5, VAD off. This is not the earlier Scribe
verbatim-versus-cleaned experiment. Acquisition API usage/request IDs and
original local library versions were not recorded in the supplied archive.

Replay stores explicit `recognizer_a_text/terms` and `recognizer_b_text/terms` in
evidence and records `confirmation_source=recognizer_a`. Legacy verbatim/cleaned
fields are compatibility aliases of A for this suite, not separate Scribe modes.
B is never substituted when A extracts no complete instruction. Transcription
and extraction adapters read frozen outputs; no STT/extraction API is called by
the comparison. Only the live rule proposer calls an API.

**Retention choice: keep all 705 files** in `audio/` (30,974,242 bytes): 63 TTS
MP3 sources, 213 channel WAVs, 3 generated non-speech WAVs, and 426 transcript
JSON caches. Existing channel hashes and noise seeds use absolute source paths;
ffmpeg and NumPy versions can also affect regenerated samples. Keeping exact
evaluated bytes avoids claiming portable bit-for-bit regeneration. No TTS or
STT calls are necessary to replay the experiment. The supplied `manifest.json`
links each attempt directly to its source, channel and recognizer caches; replay
uses these links, never reconstructed absolute paths. Future acquisition hashes
repo-relative paths and logs filenames explicitly (channel hash version 2).
`audio_manifest.json` records
the byte hashes used during integration.

All generation/classification keys go through `keys.py`, which reads ignored
root `.env` and `.env.secrets` files on demand, with interpolation disabled.
Imports never invoke generation. Existing transcript/extraction/confirmation
caches are preserved; scoring commands are cached-only by default:

```sh
uv run python -m experiments.failure_hunt.rescore
uv run python -m experiments.failure_hunt.confirm_score
```

`--live` explicitly permits missing Sonnet cache entries. New acquisition is an
optional experiment, separate from replay; it additionally needs NumPy, ffmpeg
and faster-whisper with its downloaded small model. From the repository root:

```sh
uv run --with numpy --with faster-whisper python -m experiments.failure_hunt.round1
uv run --with numpy --with faster-whisper python -m experiments.failure_hunt.round2
```

Those commands append new attempts and can call paid APIs when caches miss.
The current archive is frozen input to the recorded comparison. Generated audio
attribution: ElevenLabs, using stock voices; these are synthetic utterances and
simulated channels, not human caller recordings or field traffic.

## Sealed archives

Bill (`sealed_*`) and Lily (`sealed2_*`) each preserve the fixed 60-clip protocol.
Lily’s preregistration names its voice before generation; it was chosen because
errors in the original hunt clustered on non-American stock voices. Neither
sealed voice’s outcomes entered search, proposal, or gate. Phrases/channels are
shared with the original hunt, so these are unseen voices, not unseen phrases.
Generated Bill/Lily audio is also attributed to ElevenLabs `eleven_flash_v2_5`.

Importing either voice script never generates audio. Executing acquisition can
call paid APIs. Both scoring scripts read the once-scored Sonnet terms by default;
`--live` explicitly permits new extraction only if the saved file is absent.
Keys use the ignored-file-only `keys.py`. No cached terms were re-extracted for
these sealed worker comparisons.

The exact additional audio/cache archives are retained:

| Manifest | Files | Bytes |
|---|---:|---:|
| `sealed_manifest.json` (Bill) | 198 | 9,559,445 |
| `sealed2_manifest.json` (Lily) | 198 | 8,426,987 |

These are additional to the original 705-file hunt manifest. Links come from
logs, not audio-directory discovery. Lily’s manifest also pins the original
preregistration bytes. The runner copies that provenance into its export.

`sealed_eval.py` snapshots the learned rules from a completed experiment export,
records source/rule fingerprints, and replays one explicitly selected cached set
through real Atlas worker/oracle transactions. It reports empty, agreement and
frozen policies on identical inputs; it performs no proposal, gate decision or
activation. Select Bill by default, or Lily with `--set lily`:

```sh
uv run python -m experiments.failure_hunt.sealed_score
uv run python -m experiments.failure_hunt.sealed2_score
uv run python -m experiments.failure_hunt.sealed_eval --set lily --output artifacts/sealed_lily_replay.json
```

The scoring commands are offline; the last needs the ignored Atlas URI. Outputs
validate against `artifacts/sealed_experiment.schema.json`. Error annotations
separate wrong numeric destinations from garbled identifiers. In particular,
`vending 003` keeps the intended digits but needs clarification; its block is
not credited as a wrong-account catch. See root RESULTS.md for measured outcomes.
