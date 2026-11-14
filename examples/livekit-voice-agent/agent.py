"""Vartaloop voice agent for LiveKit: a worker you can talk to from the browser.

Pipeline, in the order a turn travels through it:

    microphone ─▶ Silero VAD ─▶ Deepgram STT ─▶ LLM (Claude or OpenAI) ─▶ Vartaloop TTS ─▶ speaker

* VAD (voice activity detection) decides when the user starts and stops speaking.
* Deepgram ``nova-3`` turns the speech into text (language set by ``DEEPGRAM_LANGUAGE``).
* The LLM writes the reply: Claude by default, OpenAI with ``LLM_PROVIDER=openai``.
* Vartaloop speaks it. Sentences are streamed to the API over one pooled WebSocket as the
  LLM produces them, so the first sentence plays while the rest is still being written.

Run it:

    cp .env.example .env            # fill in the keys
    python agent.py download-files  # one-off: fetch the VAD model weights
    python agent.py dev             # connect to the LiveKit server in LIVEKIT_URL
    python agent.py console         # or: talk to it with the local mic, no room needed

In ``dev`` mode the worker registers with the LiveKit server and is dispatched to every new
room automatically. Open the web client (see ``token_server.py``) to create a room and talk.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os

from cryptography.fernet import Fernet, InvalidToken
from dotenv import load_dotenv
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    ConversationItemAddedEvent,
    EndpointingOptions,
    InterruptionOptions,
    JobContext,
    JobProcess,
    PreemptiveGenerationOptions,
    TurnHandlingOptions,
    cli,
    inference,
    llm,
)
from livekit.plugins import anthropic, deepgram, openai, silero, vartaloop

# Read ``.env`` from the current directory. Keys never appear in this file or in logs.
load_dotenv()

logger = logging.getLogger("vartaloop-voice-agent")

# ---------------------------------------------------------------------------------------
# Configuration — every value comes from the environment so the same code runs unchanged
# on a laptop, in Docker, or on LiveKit Cloud. See ``.env.example`` for the full list.
# ---------------------------------------------------------------------------------------

#: Vartaloop voice. ``sahil`` and ``saanvi`` speak Hinglish and Indian English; ``kiara``
#: and ``kevin`` are the English voices. Any id returned by ``GET /v1/voices`` works.
VOICE = os.getenv("VARTALOOP_VOICE", "sahil")

#: Speaking rate, 0.5–2.0, pitch preserved. 1.1 is the showcase setting for ``sahil``.
SPEED = float(os.getenv("VARTALOOP_SPEED", "1.1"))

#: Vartaloop API base. The public API by default; a deployment that sits next to a Vartaloop
#: server can point this at it directly (same node or same network) and skip the public hop.
VARTALOOP_BASE_URL = os.getenv("VARTALOOP_BASE_URL", "https://api.vartaloop.ai")

#: Deepgram language code. ``hi`` (default) is the Hindi model and it transcribes Hinglish
#: the way people write it ("Mumbai के बारे में दो line में बताओ"), English sentences included.
#: Measured: nova-3's ``multi`` mode dropped a plain Hindi sentence outright, so it is not the
#: default. ``en`` or ``en-IN`` for English-only callers.
DEEPGRAM_LANGUAGE = os.getenv("DEEPGRAM_LANGUAGE", "hi")

#: Which LLM writes the replies: ``anthropic`` (Claude) or ``openai``. Both plugins stream
#: tokens, so the first sentence reaches Vartaloop before the reply is finished either way.
#: Speed matters more than depth in a spoken turn: every second the LLM takes is a second
#: of silence for the caller, so pick a fast model and keep the prompt short.
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "anthropic").lower()

#: Claude model for ``LLM_PROVIDER=anthropic``. ``claude-opus-5-5`` is the default: the best
#: answers, and with preemptive generation most of its extra time-to-first-token is hidden.
#: ``claude-haiku-5-5`` cuts measured time-to-first-token from 1.7–2.2 s to 1.0–1.5 s on the
#: same question (README, "Turn detection and latency") when speed matters more than depth.
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-opus-5-5")

#: OpenAI model for ``LLM_PROVIDER=openai``.
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

#: What the agent is. Short sentences and no lists: everything it writes is spoken aloud.
SYSTEM_PROMPT = os.getenv(
    "AGENT_INSTRUCTIONS",
    "You are a friendly voice assistant built with Vartaloop text-to-speech. "
    "You are talking, not typing: answer in one to three short spoken sentences, "
    "never use lists, markdown, emojis or URLs, and read numbers out as words. "
    "If the user speaks Hindi or Hinglish, reply in natural Hinglish (Hindi in "
    "Devanagari with everyday English words). Otherwise reply in English.",
)

#: Explicit dispatch name. Empty (local default): the worker joins every new room on the
#: project. Set on a shared LiveKit project so it only joins rooms whose token asked for it
#: (the token server's ``AGENT_NAME`` must match).
AGENT_NAME = os.getenv("AGENT_NAME", "")

#: Fernet key shared with the token server. When a conversation was started with a caller's
#: own Vartaloop key (the portal's signed-in user), the key arrives encrypted in the job
#: metadata and the TTS of that conversation is metered against the caller's credits.
AGENT_SHARED_SECRET = os.getenv("AGENT_SHARED_SECRET", "")

#: Hard cap on one conversation, in seconds. 0 = unlimited (local development). On a public
#: demo URL this is what keeps a forgotten tab from running the STT and LLM meters all day.
MAX_SESSION_SECONDS = int(os.getenv("MAX_SESSION_SECONDS", "0"))

#: Spoken when the cap is reached, just before the room is closed.
GOODBYE = os.getenv(
    "AGENT_GOODBYE",
    "That is all the time I have for this demo. Thanks for talking to me, bye!",
)

#: Spoken as soon as the user joins, so they hear the voice before they say anything.
GREETING = os.getenv(
    "AGENT_GREETING",
    "Hi, I am your Vartaloop assistant. Ask me anything, in English or Hinglish.",
)


# ---------------------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------------------

server = AgentServer()


def build_llm() -> anthropic.LLM | openai.LLM:
    """Pick the LLM from ``LLM_PROVIDER``. Keys come from the environment:
    ``ANTHROPIC_API_KEY`` or ``OPENAI_API_KEY``; neither is read here, the plugins do that.
    """
    if LLM_PROVIDER == "anthropic":
        # caching="ephemeral" caches the system prompt and history prefix on Anthropic's side,
        # trimming time-to-first-token on every turn after the first. max_tokens bounds a
        # runaway reply: a spoken answer should never need more than a few sentences.
        return anthropic.LLM(model=ANTHROPIC_MODEL, max_tokens=300, caching="ephemeral")
    if LLM_PROVIDER == "openai":
        return openai.LLM(model=OPENAI_MODEL)
    raise SystemExit(f"LLM_PROVIDER must be 'anthropic' or 'openai', got {LLM_PROVIDER!r}")


def prewarm(proc: JobProcess) -> None:
    """Load the VAD model once per worker process.

    ``setup_fnc`` runs in the job process before any room is assigned, so the model load
    (a few hundred milliseconds) never lands inside a live call. The weights come from
    ``python agent.py download-files``.
    """
    # min_silence_duration is how long the VAD waits before calling "speech ended". 0.4 s is
    # a touch quicker than the default; the turn detector adds its own judgement on top.
    proc.userdata["vad"] = silero.VAD.load(min_silence_duration=0.4)


server.setup_fnc = prewarm


def caller_vartaloop_key(ctx: JobContext) -> str | None:
    """The caller's own Vartaloop key from the dispatch metadata, or None for the worker's key."""
    if not AGENT_SHARED_SECRET or not ctx.job.metadata:
        return None
    try:
        meta = json.loads(ctx.job.metadata)
        blob = meta.get("vartaloop_key")
        if not blob:
            return None
        return Fernet(AGENT_SHARED_SECRET.encode()).decrypt(blob.encode()).decode()
    except (ValueError, InvalidToken):
        logger.warning("dispatch metadata present but unreadable; using the worker's own key")
        return None


@server.rtc_session(agent_name=AGENT_NAME)
async def entrypoint(ctx: JobContext) -> None:
    """Called for every room the worker is dispatched to. One call == one conversation."""
    user_key = caller_vartaloop_key(ctx)
    logger.info(
        "joining room %s with voice=%s speed=%s byok=%s",
        ctx.room.name,
        VOICE,
        SPEED,
        bool(user_key),
    )

    tts = vartaloop.TTS(
        api_key=user_key,  # None -> the plugin falls back to $VARTALOOP_API_KEY
        base_url=VARTALOOP_BASE_URL,
        voice=VOICE,
        speed=SPEED,
        expression="neutral",  # "excited", "sad", "whisper" on voices that carry them
        digits="auto",  # Hinglish voices: how digits are read; English voices ignore it
    )
    # Open the WebSocket to api.vartaloop.ai now, so the greeting does not pay the
    # TLS + handshake cost. The pool keeps it open for the rest of the call.
    tts.prewarm()

    session = AgentSession(
        vad=ctx.proc.userdata["vad"],
        # interim_results + no_delay: Deepgram streams partial transcripts as the user speaks,
        # which is what preemptive generation below feeds on. endpointing_ms is Deepgram's own
        # silence trigger; kept short because the turn detector, not Deepgram, decides the turn.
        stt=deepgram.STT(
            model="nova-3",
            language=DEEPGRAM_LANGUAGE,
            interim_results=True,
            no_delay=True,
            endpointing_ms=25,
        ),
        llm=build_llm(),
        tts=tts,
        turn_handling=TurnHandlingOptions(
            # A small transformer reads the transcript so far and predicts whether the user
            # has finished (English and Hindi). Far fewer cut-offs mid-sentence than silence
            # alone, and a faster hand-over when the sentence is clearly complete. Hosted on
            # LiveKit Cloud; elsewhere the bundled local model (weights fetched once by
            # `download-files`, then resident in memory).
            turn_detection=inference.TurnDetector(local_fallback=True),
            # How long to wait after speech stops: min_delay when the detector is confident
            # the turn is over, up to max_delay when it thinks the user will continue.
            endpointing=EndpointingOptions(mode="dynamic", min_delay=0.3, max_delay=3.0),
            # Start the LLM on the interim transcript while the detector is still deciding.
            # A wrong guess is discarded; a right one hides most of the end-of-turn delay.
            preemptive_generation=PreemptiveGenerationOptions(enabled=True),
            # Barge-in: 0.4 s of speech interrupts the agent; a cough or "hmm" that turns out
            # not to be a turn resumes the reply instead of leaving silence.
            interruption=InterruptionOptions(
                mode="vad", min_duration=0.4, resume_false_interruption=True
            ),
        ),
    )

    # Per-turn latency. Every assistant message carries a MetricsReport: end_of_turn_delay
    # (speech stopped -> turn decided), transcription_delay, llm_node_ttft (first token),
    # tts_node_ttfb (first audio byte). These are the numbers to watch when tuning a voice
    # agent; they go to the log, nowhere else.
    @session.on("conversation_item_added")
    def _on_item(ev: ConversationItemAddedEvent) -> None:
        if isinstance(ev.item, llm.ChatMessage) and ev.item.role == "assistant" and ev.item.metrics:
            report = {
                k: round(v, 3) if isinstance(v, (int, float)) else v
                for k, v in ev.item.metrics.items()
            }
            logger.info("turn metrics %s", report)

    # ``session.start`` publishes the agent's audio track and, by default, streams both the
    # user's and the agent's transcripts to the room on the ``lk.transcription`` text-stream
    # topic. The web client listens there to draw the transcript panel.
    await session.start(
        room=ctx.room,
        agent=Agent(instructions=SYSTEM_PROMPT),
    )

    # ``say`` speaks fixed text without consulting the LLM.
    await session.say(GREETING)

    if MAX_SESSION_SECONDS > 0:
        # Say goodbye and close the room when the cap is reached. Deleting the room
        # disconnects the browser too, so the page shows "Disconnected" rather than silence.
        async def end_when_time_is_up() -> None:
            await asyncio.sleep(MAX_SESSION_SECONDS)
            logger.info("session cap of %ss reached in %s", MAX_SESSION_SECONDS, ctx.room.name)
            await session.say(GOODBYE, allow_interruptions=False)
            await ctx.delete_room()

        asyncio.create_task(end_when_time_is_up())


if __name__ == "__main__":
    # Subcommands: ``dev`` (hot reload, verbose logs), ``start`` (production),
    # ``console`` (local mic and speaker), ``download-files`` (model weights).
    cli.run_app(server)
