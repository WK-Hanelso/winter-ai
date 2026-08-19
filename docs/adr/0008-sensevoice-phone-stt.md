# ADR-0008 — Use SenseVoiceSmall GGUF for phone STT

- Status: accepted for limited phone operation
- Date: 2026-08-17

## Context

The working phone path used Whisper small multilingual through whisper.cpp on
the CPU. Three real phone turns measured a median 4.12 seconds from button
release to STT response; server inference itself was about 3.79 seconds. GPU
offload is not safe while Chatterbox and Seed-VC occupy most of the 6 GB GPU.

whisper.cpp's microphone stream example repeatedly transcribes sampled windows,
but its HTTP server still accepts a completed file. Adding chunk transport
would therefore add complexity while retaining the dominant CPU inference cost.

## Decision

Use the official SenseVoiceSmall Q8 GGUF artifact and the official v0.1.9 AVX2
runtime as the phone Web STT backend. Keep Whisper as an explicit selectable
fallback; never switch to it silently after a SenseVoice failure.

The runtime executes in an Ubuntu 24.04 container because the official binary
requires a newer glibc than the Host provides. A minimal localhost-only HTTP
wrapper invokes the binary, deletes each temporary upload at request end, and
does not log audio or transcript content. `SpeechToText` remains the Core port.

## Evidence and activation boundary

- 2.36–2.56 second synthetic Korean utterances: 0.12–0.13 seconds model time;
  all preserved the intended sentence, with spacing variation in one take.
- Zeroth-Korean 10.4535 second held-out fixture: 0.49 seconds and an exact text
  match to the published reference.
- Full Web `/listen` path including ffmpeg and HTTP: 0.347 seconds server STT for
  a 2.36 second WAV, exact intended text.
- Offline adapter/server contract tests pass. The service binds only to
  `127.0.0.1:8095` and has an explicit restart policy.

This is enough for limited phone operation, not a general Korean accuracy
claim. The new latency database starts separately and still requires 20 real
phone turns. Names, English technical terms, noise, earbuds, long speech and
spontaneous disfluency remain held-out gaps.

## Provenance

- Runtime release: `runtime-llamacpp-v0.1.9`
- Official AVX2 archive SHA-256:
  `51f33822a5191f7963d8ceedba2dd76fe7d810a4388b931b25b8be4f1a8e320d`
- Local extracted binary SHA-256:
  `b49550a0ede0fe91512b45d9564248f3609d1dae01dd9ea65814920444538b56`
- Q8 GGUF SHA-256:
  `4ae45c94422de949b387e2e0fb10d7e14e4c42c69db30c3444ecc7d4b844b7c5`
- Model repository declares Apache-2.0. The original FunASR model family also
  publishes its own model license, so redistribution must re-check the exact
  selected artifact's terms; local private use is the present scope.

## Consequences

Input-side streaming is no longer the first latency task: after release the
entire STT path is below half a second on the measured short utterance. At this
decision point the remaining first-audio delay was dominated by Chatterbox and
Seed-VC; [ADR-0009](0009-direct-reference-voice-path.md) subsequently removed
the serial Seed-VC stage for limited operation. Streaming, VAD and barge-in
remain required for natural conversation after the output path is fast enough.
