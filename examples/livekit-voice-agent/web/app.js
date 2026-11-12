/* Vartaloop voice agent — browser client.
 *
 * Plain JavaScript on top of the LiveKit JS SDK (global `LivekitClient`, loaded from a
 * pinned CDN URL in index.html). No bundler, no framework.
 *
 * What happens when the user presses "Connect & talk":
 *   1. GET /token            -> { url, token }   (token_server.py signs it; the secret stays there)
 *   2. room.connect(url, token)
 *   3. publish the microphone
 *   4. the agent worker is dispatched into the room by the LiveKit server, greets the user,
 *      and its audio track is attached to a hidden <audio> element here
 *   5. transcripts arrive as text streams on the `lk.transcription` topic and fill the panel
 */

(() => {
  "use strict";

  const { Room, RoomEvent, Track, ConnectionState } = LivekitClient;

  // -------------------------------------------------------------------------------------
  // DOM
  // -------------------------------------------------------------------------------------
  const $ = (id) => document.getElementById(id);
  const el = {
    connect: $("connect"),
    mute: $("mute"),
    disconnect: $("disconnect"),
    statusDot: $("status-dot"),
    statusText: $("status-text"),
    transcript: $("transcript"),
    textForm: $("text-form"),
    textInput: $("text-input"),
    textSend: $("text-send"),
    unmuteAudio: $("unmute-audio"),
  };

  /** @type {import("livekit-client").Room | null} */
  let room = null;
  let micMuted = false;

  // -------------------------------------------------------------------------------------
  // UI helpers
  // -------------------------------------------------------------------------------------
  function setStatus(kind, text) {
    el.statusDot.className = `dot dot-${kind}`; // off | busy | on | error
    el.statusText.textContent = text;
  }

  function setConnectedUi(connected) {
    el.connect.disabled = connected;
    el.mute.disabled = !connected;
    el.disconnect.disabled = !connected;
    el.textInput.disabled = !connected;
    el.textSend.disabled = !connected;
    if (!connected) {
      el.mute.textContent = "Mute mic";
      micMuted = false;
      el.unmuteAudio.classList.add("hidden");
    }
  }

  function clearTranscript() {
    el.transcript.innerHTML = "";
  }

  /**
   * Render (or update) one turn. Segments arrive incrementally: the agent writes partial
   * text while it speaks, and Deepgram sends interim user transcripts before the final
   * one. Both carry a stable `lk.segment_id`, so we key bubbles on it and update in place.
   */
  function upsertTurn(segmentId, who, text, isFinal) {
    const hint = el.transcript.querySelector(".hint");
    if (hint) hint.remove();

    let node = el.transcript.querySelector(`[data-segment="${segmentId}"]`);
    if (!node) {
      node = document.createElement("div");
      node.dataset.segment = segmentId;
      node.className = `turn turn-${who}`;
      const label = document.createElement("span");
      label.className = "who";
      label.textContent = who === "user" ? "You" : "Agent";
      node.appendChild(label);
      node.appendChild(document.createElement("span"));
      el.transcript.appendChild(node);
    }
    node.lastChild.textContent = text;
    node.classList.toggle("turn-interim", !isFinal);
    el.transcript.scrollTop = el.transcript.scrollHeight;
  }

  // -------------------------------------------------------------------------------------
  // Transcripts
  // -------------------------------------------------------------------------------------
  /**
   * Handler for the `lk.transcription` text-stream topic.
   *
   * The agent worker publishes both sides of the conversation. It writes the user's
   * recognised speech *on behalf of the user* (the stream's sender is our own identity)
   * and its own words as itself, so the sender identity tells the two apart.
   * `lk.transcription_final` tells us whether the segment may still change.
   */
  async function onTranscription(reader, participantInfo) {
    const attrs = reader.info.attributes || {};
    const segmentId = attrs["lk.segment_id"] || reader.info.id;
    const fromUser = room && participantInfo.identity === room.localParticipant.identity;
    const who = fromUser ? "user" : "agent";

    // Stream the text in as it arrives so the agent's bubble grows while it speaks.
    let text = "";
    for await (const chunk of reader) {
      text += chunk;
      upsertTurn(segmentId, who, text, false);
    }
    const isFinal = attrs["lk.transcription_final"] !== "false";
    upsertTurn(segmentId, who, text, isFinal);
  }

  // -------------------------------------------------------------------------------------
  // Room lifecycle
  // -------------------------------------------------------------------------------------
  async function fetchToken() {
    const res = await fetch("/token");
    if (!res.ok) throw new Error(`token server replied ${res.status}`);
    return res.json(); // { url, token, room, identity }
  }

  async function connect() {
    setConnectedUi(true);
    setStatus("busy", "Requesting token…");
    clearTranscript();

    try {
      const { url, token, room: roomName } = await fetchToken();

      room = new Room({
        adaptiveStream: true,
        dynacast: true,
        // Echo cancellation + noise suppression matter a lot for a voice agent: without
        // them the agent hears itself through the speakers and interrupts its own replies.
        audioCaptureDefaults: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });

      room
        .on(RoomEvent.ConnectionStateChanged, (state) => {
          if (state === ConnectionState.Connected) setStatus("on", `Connected · room ${roomName}`);
          else if (state === ConnectionState.Reconnecting) setStatus("busy", "Reconnecting…");
          else if (state === ConnectionState.Disconnected) setStatus("off", "Not connected");
        })
        .on(RoomEvent.ParticipantConnected, (p) => {
          if (p.isAgent) setStatus("on", `Agent joined · room ${roomName}`);
        })
        .on(RoomEvent.TrackSubscribed, (track) => {
          // The agent's voice. attach() creates an <audio> element and starts playback.
          if (track.kind === Track.Kind.Audio) {
            const audio = track.attach();
            audio.dataset.agentAudio = "1";
            document.body.appendChild(audio);
          }
        })
        .on(RoomEvent.TrackUnsubscribed, (track) => {
          track.detach().forEach((node) => node.remove());
        })
        .on(RoomEvent.AudioPlaybackStatusChanged, () => {
          // Browsers block audio that starts without a user gesture. LiveKit tells us when
          // that happens; show a button whose click counts as the gesture.
          el.unmuteAudio.classList.toggle("hidden", room.canPlaybackAudio);
        })
        .on(RoomEvent.Disconnected, () => {
          setStatus("off", "Disconnected");
          setConnectedUi(false);
          room = null;
        });

      room.registerTextStreamHandler("lk.transcription", onTranscription);

      setStatus("busy", "Connecting…");
      await room.connect(url, token);

      setStatus("busy", "Enabling microphone…");
      try {
        await room.localParticipant.setMicrophoneEnabled(true);
        setStatus("on", `Connected · room ${roomName} · waiting for the agent`);
      } catch (micErr) {
        // No microphone or permission denied: stay connected, the typed box still works
        // and the agent's audio still plays.
        console.warn("microphone unavailable:", micErr);
        el.mute.disabled = true;
        setStatus("on", `Connected · room ${roomName} · no microphone, type below`);
      }
      el.textInput.focus();
    } catch (err) {
      console.error(err);
      setStatus("error", `Failed: ${err.message || err}`);
      if (room) {
        await room.disconnect();
        room = null;
      }
      setConnectedUi(false);
    }
  }

  async function disconnect() {
    if (!room) return;
    await room.disconnect();
    room = null;
    setStatus("off", "Not connected");
    setConnectedUi(false);
  }

  async function toggleMute() {
    if (!room) return;
    micMuted = !micMuted;
    await room.localParticipant.setMicrophoneEnabled(!micMuted);
    el.mute.textContent = micMuted ? "Unmute mic" : "Mute mic";
  }

  /** Typed input goes to the agent on the `lk.chat` topic, same pipeline minus STT. */
  async function sendText(event) {
    event.preventDefault();
    const text = el.textInput.value.trim();
    if (!text || !room) return;
    el.textInput.value = "";
    upsertTurn(`typed-${Date.now()}`, "user", text, true);
    await room.localParticipant.sendText(text, { topic: "lk.chat" });
  }

  // -------------------------------------------------------------------------------------
  // Wire up
  // -------------------------------------------------------------------------------------
  el.connect.addEventListener("click", connect);
  el.disconnect.addEventListener("click", disconnect);
  el.mute.addEventListener("click", toggleMute);
  el.textForm.addEventListener("submit", sendText);
  el.unmuteAudio.addEventListener("click", async () => {
    if (room) await room.startAudio(); // resumes playback after the user gesture
  });
  window.addEventListener("beforeunload", () => room && room.disconnect());
})();
