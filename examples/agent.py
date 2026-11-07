"""A LiveKit room agent that speaks Hinglish with Vartalaap.

    export VARTALAAP_API_KEY=...   OPENAI_API_KEY=...   DEEPGRAM_API_KEY=...
    export LIVEKIT_URL=... LIVEKIT_API_KEY=... LIVEKIT_API_SECRET=...
    python examples/agent.py console      # local mic + speaker, no room needed
    python examples/agent.py dev          # connect to your LiveKit project

The TTS is `vartalaap.TTS`: sentences stream to the API over one pooled WebSocket as the
LLM produces them, and the audio of each one plays as it arrives.
"""

import logging

from dotenv import load_dotenv

from livekit.agents import Agent, AgentServer, AgentSession, JobContext, cli
from livekit.plugins import deepgram, openai, silero, vartalaap

load_dotenv()
logger = logging.getLogger("vartalaap-agent")

server = AgentServer()


def prewarm(proc):
    proc.userdata["vad"] = silero.VAD.load()


server.setup_fnc = prewarm


@server.rtc_session()
async def entrypoint(ctx: JobContext):
    tts = vartalaap.TTS(voice="saanvi", expression="neutral", digits="auto")
    tts.prewarm()  # open the WebSocket now so the greeting does not pay the handshake

    session = AgentSession(
        vad=ctx.proc.userdata["vad"],
        stt=deepgram.STT(model="nova-3", language="hi"),
        llm=openai.LLM(model="gpt-4.1-mini"),
        tts=tts,
    )

    await session.start(
        room=ctx.room,
        agent=Agent(
            instructions=(
                "You are a friendly assistant speaking with a Hindi speaker. Reply in natural "
                "Hinglish: Hindi in Devanagari with everyday English words, short spoken "
                "sentences, no lists or emojis."
            )
        ),
    )

    await session.say("नमस्ते! मैं आपकी Vartalaap assistant हूँ। आज मैं आपकी कैसे help कर सकती हूँ?")


if __name__ == "__main__":
    cli.run_app(server)
