"""Token server + static host for the web client.

A browser cannot hold the LiveKit API secret, so it asks this tiny server for a short-lived
room token instead. The server also serves ``web/`` so the whole demo is one URL.

    python token_server.py            # http://localhost:8080

Endpoints:

    GET /                 the web client (web/index.html)
    GET /token?room=R&identity=I
                          -> {"url": LIVEKIT_URL, "token": "<jwt>", "room": R, "identity": I}
                          ``room`` and ``identity`` are optional; random ones are made up.
    GET /healthz          -> {"ok": true}

The token lets the holder join one room, publish their microphone and subscribe to the
agent's audio, and expires after ``TOKEN_TTL_SECONDS``. Nothing here is specific to
Vartaloop: it is the standard LiveKit pattern, kept minimal and readable.
"""

from __future__ import annotations

import logging
import os
import secrets
from datetime import timedelta
from pathlib import Path

from aiohttp import web
from dotenv import load_dotenv
from livekit import api

load_dotenv()

logger = logging.getLogger("token-server")

WEB_DIR = Path(__file__).parent / "web"

#: Where the browser connects. For ``livekit-server --dev`` this is ``ws://localhost:7880``.
#: The browser needs a URL it can reach from its own machine, which is why it is handed out
#: by the token endpoint rather than hard-coded in the page.
LIVEKIT_URL = os.getenv("LIVEKIT_URL", "ws://localhost:7880")
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET")

HOST = os.getenv("TOKEN_SERVER_HOST", "127.0.0.1")
PORT = int(os.getenv("TOKEN_SERVER_PORT", "8080"))
TOKEN_TTL_SECONDS = int(os.getenv("TOKEN_TTL_SECONDS", "3600"))


def mint_token(room: str, identity: str) -> str:
    """Return a signed JWT that lets ``identity`` join ``room`` as a normal participant."""
    grants = api.VideoGrants(
        room_join=True,
        room=room,
        can_publish=True,  # the user's microphone
        can_subscribe=True,  # the agent's voice
        can_publish_data=True,  # typed messages on the ``lk.chat`` topic
    )
    token = (
        api.AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
        .with_identity(identity)
        .with_name(identity)
        .with_grants(grants)
        .with_ttl(timedelta(seconds=TOKEN_TTL_SECONDS))
    )
    return token.to_jwt()


async def handle_token(request: web.Request) -> web.Response:
    # One room per visitor by default, so two people opening the page do not end up
    # talking to the same agent instance.
    room = request.query.get("room") or f"vartaloop-{secrets.token_hex(3)}"
    identity = request.query.get("identity") or f"user-{secrets.token_hex(3)}"

    # Keep the values the browser sent within sane bounds before they reach the JWT.
    if len(room) > 64 or len(identity) > 64:
        raise web.HTTPBadRequest(text="room and identity must be at most 64 characters")

    jwt = mint_token(room, identity)
    logger.info("token for identity=%s room=%s", identity, room)
    return web.json_response({"url": LIVEKIT_URL, "token": jwt, "room": room, "identity": identity})


async def handle_health(_: web.Request) -> web.Response:
    return web.json_response({"ok": True})


async def handle_index(_: web.Request) -> web.FileResponse:
    return web.FileResponse(WEB_DIR / "index.html")


def make_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/", handle_index)
    app.router.add_get("/healthz", handle_health)
    app.router.add_get("/token", handle_token)
    # Everything else under / is a static file from web/ (app.js, styles.css).
    app.router.add_static("/", WEB_DIR, show_index=False)
    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    if not LIVEKIT_API_KEY or not LIVEKIT_API_SECRET:
        # Fail loudly at start-up instead of handing out tokens that no server will accept.
        raise SystemExit(
            "LIVEKIT_API_KEY and LIVEKIT_API_SECRET must be set (see .env.example). "
            "For a local `livekit-server --dev` they are `devkey` and `secret`."
        )
    logger.info("serving %s on http://%s:%d (LiveKit at %s)", WEB_DIR, HOST, PORT, LIVEKIT_URL)
    web.run_app(make_app(), host=HOST, port=PORT, print=None)


if __name__ == "__main__":
    main()
