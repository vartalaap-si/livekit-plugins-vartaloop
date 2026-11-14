"""Token server + static host for the web client.

A browser cannot hold the LiveKit API secret, so it asks this tiny server for a short-lived
room token instead. The server also serves ``web/`` so the whole demo is one URL locally.

    python token_server.py            # http://localhost:8080

Endpoints (all under ``ROUTE_PREFIX``, empty by default):

    GET /                 the web client (web/index.html)
    GET /token?room=R&identity=I
                          -> {"url": LIVEKIT_URL, "token": "<jwt>", "room": R, "identity": I}
                          ``room`` and ``identity`` are optional; random ones are made up.
    GET /healthz          -> {"ok": true}

The token lets the holder join one room, publish their microphone and subscribe to the
agent's audio, and expires after ``TOKEN_TTL_SECONDS``.

Built for a public URL as well as a laptop:

* ``ALLOWED_ORIGINS`` (comma-separated) turns on CORS for a page served from another host,
  e.g. the demo page on https://tts.vartaloop.ai asking https://api.vartaloop.ai for a token.
* ``TOKENS_PER_IP_PER_HOUR`` caps how many conversations one address can start, so a link
  passed around cannot run up the STT and LLM bills. The counter lives in memory: nothing
  here reads a file, a database or a cache on the request path.
* ``ROUTE_PREFIX`` mounts the routes under a path such as ``/integration/livekit`` when a
  load balancer forwards only that prefix to this service.

Nothing here is specific to Vartaloop: it is the standard LiveKit pattern, kept minimal.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import time
from collections import defaultdict, deque
from datetime import timedelta
from pathlib import Path

import aiohttp
from aiohttp import web
from cryptography.fernet import Fernet
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
ROUTE_PREFIX = os.getenv("ROUTE_PREFIX", "").rstrip("/")
TOKEN_TTL_SECONDS = int(os.getenv("TOKEN_TTL_SECONDS", "600"))

#: Origins allowed to call /token from a page on another host. Empty = same-origin only.
ALLOWED_ORIGINS = {o.strip() for o in os.getenv("ALLOWED_ORIGINS", "").split(",") if o.strip()}

#: Per-address budget of new conversations. 0 disables the limit (local development).
TOKENS_PER_IP_PER_HOUR = int(os.getenv("TOKENS_PER_IP_PER_HOUR", "0"))

#: Set when the service sits behind a proxy or load balancer that adds X-Forwarded-For.
TRUST_X_FORWARDED_FOR = os.getenv("TRUST_X_FORWARDED_FOR", "false").lower() == "true"

#: Static files are served only when the page is hosted from here (local run). On a public
#: deployment the page usually lives on a CDN and this service only mints tokens.
SERVE_STATIC = os.getenv("SERVE_STATIC", "true").lower() == "true"

#: Explicit agent dispatch. Empty (local default): the worker auto-joins every new room.
#: Set to the worker's ``AGENT_NAME`` on a shared LiveKit project so only rooms created by
#: this token server get the agent, and so the token can carry per-session metadata to it.
AGENT_NAME = os.getenv("AGENT_NAME", "")

#: Bring-your-own-key mode. A caller may POST ``{"key": "<Vartaloop API key>"}``; the key is
#: checked against the Vartaloop API, then handed to the agent (encrypted) so the TTS of
#: that conversation is metered against the caller's own credits. The portal does this with
#: the short-lived playground key of the signed-in user. ``AGENT_SHARED_SECRET`` is a Fernet
#: key shared with the worker (``python -c "from cryptography.fernet import Fernet;
#: print(Fernet.generate_key().decode())"``). Required for BYOK; without it the body is refused.
AGENT_SHARED_SECRET = os.getenv("AGENT_SHARED_SECRET", "")
VARTALOOP_BASE_URL = os.getenv("VARTALOOP_BASE_URL", "https://api.vartaloop.ai")
#: Whether a token may be minted without a caller key (the worker then uses its own
#: ``VARTALOOP_API_KEY``). Off on the public deployment: every conversation must be metered.
ALLOW_ANONYMOUS = os.getenv("ALLOW_ANONYMOUS", "true").lower() == "true"

_fernet = Fernet(AGENT_SHARED_SECRET.encode()) if AGENT_SHARED_SECRET else None


# ---------------------------------------------------------------------------------------
# Rate limiting: a sliding one-hour window per client address, in memory
# ---------------------------------------------------------------------------------------

_recent_tokens: dict[str, deque[float]] = defaultdict(deque)


def client_ip(request: web.Request) -> str:
    if TRUST_X_FORWARDED_FOR:
        forwarded = request.headers.get("X-Forwarded-For", "")
        if forwarded:
            # Google's load balancer appends "<client>, <lb>"; the first entry is the client.
            return forwarded.split(",")[0].strip()
    return request.remote or "unknown"


def over_limit(ip: str) -> bool:
    """Record one token for ``ip`` and say whether it exceeded the hourly budget."""
    if TOKENS_PER_IP_PER_HOUR <= 0:
        return False
    now = time.monotonic()
    window = _recent_tokens[ip]
    while window and now - window[0] > 3600:
        window.popleft()
    if len(window) >= TOKENS_PER_IP_PER_HOUR:
        return True
    window.append(now)
    return False


# ---------------------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------------------


async def vartaloop_key_is_valid(key: str) -> bool:
    """One cheap authenticated call to the Vartaloop API; 401/403/402 mean "do not proceed"."""
    try:
        async with (
            aiohttp.ClientSession() as http,
            http.get(
                f"{VARTALOOP_BASE_URL}/v1/voices",
                headers={"x-api-key": key},
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp,
        ):
            return resp.status == 200
    except aiohttp.ClientError as exc:
        logger.warning("could not verify Vartaloop key: %s", exc)
        return False


def mint_token(room: str, identity: str, user_key: str | None = None) -> str:
    """Return a signed JWT that lets ``identity`` join ``room`` as a normal participant.

    With ``AGENT_NAME`` set the token also asks LiveKit to dispatch that agent into the room.
    ``user_key`` (BYOK) travels to the agent in the dispatch metadata, encrypted with the
    shared secret, so the browser that holds this JWT cannot read it back out.
    """
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
    if AGENT_NAME:
        metadata: dict[str, str] = {}
        if user_key:
            assert _fernet is not None
            metadata["vartaloop_key"] = _fernet.encrypt(user_key.encode()).decode()
        token = token.with_room_config(
            api.RoomConfiguration(
                agents=[api.RoomAgentDispatch(agent_name=AGENT_NAME, metadata=json.dumps(metadata))]
            )
        )
    return token.to_jwt()


async def handle_token(request: web.Request) -> web.Response:
    ip = client_ip(request)
    if over_limit(ip):
        logger.warning("rate limit hit for %s", ip)
        raise web.HTTPTooManyRequests(
            text="Too many conversations from this address this hour; please try later."
        )

    # GET: query parameters. POST: a JSON body, which is also where a caller's own
    # Vartaloop key goes (never in the URL, so it never lands in access logs).
    body: dict = {}
    if request.method == "POST" and request.can_read_body:
        try:
            body = await request.json()
        except ValueError:
            raise web.HTTPBadRequest(text="body must be JSON") from None
        if not isinstance(body, dict):
            raise web.HTTPBadRequest(text="body must be a JSON object")
    params = {**request.query, **body}

    user_key = params.get("key")
    if user_key:
        if not isinstance(user_key, str) or len(user_key) > 200:
            raise web.HTTPBadRequest(text="key must be a short string")
        if _fernet is None:
            raise web.HTTPNotImplemented(text="bring-your-own-key is not enabled here")
        if not await vartaloop_key_is_valid(user_key):
            raise web.HTTPUnauthorized(text="that Vartaloop key was not accepted by the API")
    elif not ALLOW_ANONYMOUS:
        raise web.HTTPUnauthorized(text="a Vartaloop API key is required to start a conversation")

    # One room per visitor by default, so two people opening the page do not end up
    # talking to the same agent instance.
    room = params.get("room") or f"vartaloop-{secrets.token_hex(3)}"
    identity = params.get("identity") or f"user-{secrets.token_hex(3)}"

    # Keep the values the browser sent within sane bounds before they reach the JWT.
    if len(room) > 64 or len(identity) > 64:
        raise web.HTTPBadRequest(text="room and identity must be at most 64 characters")

    jwt = mint_token(room, identity, user_key)
    logger.info("token for identity=%s room=%s ip=%s byok=%s", identity, room, ip, bool(user_key))
    return web.json_response({"url": LIVEKIT_URL, "token": jwt, "room": room, "identity": identity})


async def handle_health(_: web.Request) -> web.Response:
    return web.json_response({"ok": True})


async def handle_index(_: web.Request) -> web.FileResponse:
    return web.FileResponse(WEB_DIR / "index.html")


@web.middleware
async def cors_middleware(request: web.Request, handler):  # type: ignore[no-untyped-def]
    """Answer pre-flight requests and stamp CORS headers for allowed origins only."""
    origin = request.headers.get("Origin", "")
    allowed = origin in ALLOWED_ORIGINS
    if request.method == "OPTIONS":
        response = web.Response(status=204)
    else:
        try:
            response = await handler(request)
        except web.HTTPException as exc:
            response = exc
    if allowed:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type"
        response.headers["Vary"] = "Origin"
    if isinstance(response, web.HTTPException):
        raise response
    return response


def make_app() -> web.Application:
    app = web.Application(middlewares=[cors_middleware])
    app.router.add_get(f"{ROUTE_PREFIX}/healthz", handle_health)
    app.router.add_get(f"{ROUTE_PREFIX}/token", handle_token)
    app.router.add_post(f"{ROUTE_PREFIX}/token", handle_token)
    if SERVE_STATIC:
        app.router.add_get(f"{ROUTE_PREFIX}/", handle_index)
        # Everything else under the prefix is a static file from web/ (app.js, styles.css).
        app.router.add_static(f"{ROUTE_PREFIX}/", WEB_DIR, show_index=False)
    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    if not LIVEKIT_API_KEY or not LIVEKIT_API_SECRET:
        # Fail loudly at start-up instead of handing out tokens that no server will accept.
        raise SystemExit(
            "LIVEKIT_API_KEY and LIVEKIT_API_SECRET must be set (see .env.example). "
            "For a local `livekit-server --dev` they are `devkey` and `secret`."
        )
    logger.info(
        "serving on http://%s:%d prefix=%r static=%s origins=%s limit=%s/h (LiveKit at %s)",
        HOST,
        PORT,
        ROUTE_PREFIX,
        SERVE_STATIC,
        sorted(ALLOWED_ORIGINS) or "same-origin",
        TOKENS_PER_IP_PER_HOUR or "off",
        LIVEKIT_URL,
    )
    web.run_app(make_app(), host=HOST, port=PORT, print=None)


if __name__ == "__main__":
    main()
