"""Vartaloop voice agent for LiveKit: a worker you can talk to from the browser.

Pipeline, in the order a turn travels through it:

    microphone ─▶ Silero VAD ─▶ Deepgram STT ─▶ OpenAI LLM ─▶ Vartaloop TTS ─▶ speaker

* VAD (voice activity detection) decides when the user starts and stops speaking.
* Deepgram ``nova-3`` turns the speech into text (language set by ``DEEPGRAM_LANGUAGE``).
* The LLM writes the reply.
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

import logging
import os

from dotenv import load_dotenv
from livekit.agents import Agent, AgentServer, AgentSession, JobContext, JobProcess, cli
from livekit.plugins import deepgram, openai, silero, vartaloop

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

#: Deepgram language code. ``en`` for English; for Hinglish callers use ``hi`` (Hindi,
#: also catches the English words) or ``multi`` (nova-3 code-switching). The agent's
#: system prompt below should match: an English prompt with a ``hi`` STT reads oddly.
DEEPGRAM_LANGUAGE = os.getenv("DEEPGRAM_LANGUAGE", "en")

#: OpenAI chat model. ``gpt-4o-mini`` is fast and cheap, which matters more than depth in
#: a spoken turn: every second the LLM takes is a second of silence for the caller.
LLM_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

#: What the agent is. Short sentences and no lists: everything it writes is spoken aloud.
SYSTEM_PROMPT = os.getenv(
    "AGENT_INSTRUCTIONS",
    "You are a friendly voice assistant built with Vartaloop text-to-speech. "
    "You are talking, not typing: answer in one to three short spoken sentences, "
    "never use lists, markdown, emojis or URLs, and read numbers out as words. "
    "If the user speaks Hindi or Hinglish, reply in natural Hinglish (Hindi in "
    "Devanagari with everyday English words). Otherwise reply in English.",
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


def prewarm(proc: JobProcess) -> None:
    """Load the VAD model once per worker process.

    ``setup_fnc`` runs in the job process before any room is assigned, so the model load
    (a few hundred milliseconds) never lands inside a live call. The weights come from
    ``python agent.py download-files``.
    """
    proc.userdata["vad"] = silero.VAD.load()


server.setup_fnc = prewarm


@server.rtc_session()
async def entrypoint(ctx: JobContext) -> None:
    """Called for every room the worker is dispatched to. One call == one conversation."""
    logger.info("joining room %s with voice=%s speed=%s", ctx.room.name, VOICE, SPEED)

    tts = vartaloop.TTS(
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
        stt=deepgram.STT(model="nova-3", language=DEEPGRAM_LANGUAGE),
        llm=openai.LLM(model=LLM_MODEL),
        tts=tts,
    )

    # ``session.start`` publishes the agent's audio track and, by default, streams both the
    # user's and the agent's transcripts to the room on the ``lk.transcription`` text-stream
    # topic. The web client listens there to draw the transcript panel.
    await session.start(
        room=ctx.room,
        agent=Agent(instructions=SYSTEM_PROMPT),
    )

    # ``say`` speaks fixed text without consulting the LLM.
    await session.say(GREETING)


if __name__ == "__main__":
    # Subcommands: ``dev`` (hot reload, verbose logs), ``start`` (production),
    # ``console`` (local mic and speaker), ``download-files`` (model weights).
    cli.run_app(server)
