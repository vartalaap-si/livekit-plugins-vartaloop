# Changelog

## 1.8.13 — 2026-11-21

- In step with the plugin in livekit/agents#8449 and livekit/agents-js#2958, now on `livekit-agents` 1.8.13 (Node: `@livekit/agents` 1.10.0).
- A server error that ends only its utterance fails the stream but keeps the pooled WebSocket, so the retry does not pay a new handshake; the socket is dropped when another sentence was already sent on it.

## 1.8.11 — 2026-11-07

- First release: `TTS` with `synthesize()` over `POST /v1/tts` and `stream()` over a pooled `WS /v1/tts/stream`, one utterance per sentence, `prewarm()`, `update_options()` for every server parameter.
