"""Speak one Hinglish sentence with the plugin and write it to a WAV: the smallest possible check.

VARTALAAP_API_KEY=... python examples/say.py "नमस्ते, आपका loan approve हो गया है।" out.wav
"""

import asyncio
import sys
import wave

from livekit.agents.utils import http_context
from livekit.plugins import vartalaap


async def main(text: str, path: str) -> None:
    # outside an agent job the plugin needs the shared http session opened by hand
    async with http_context.open():
        await speak(text, path)


async def speak(text: str, path: str) -> None:
    tts = vartalaap.TTS(voice="saanvi")
    try:
        with wave.open(path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(tts.sample_rate)
            # stream(): the sentence goes over the WebSocket, frames come back as decoded
            stream = tts.stream()
            stream.push_text(text)
            stream.end_input()
            async for ev in stream:
                w.writeframes(bytes(ev.frame.data))
    finally:
        await tts.aclose()
    print("wrote", path)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "out.wav"))
