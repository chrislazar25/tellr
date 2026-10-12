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
