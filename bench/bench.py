"""Latency and throughput of Vartaloop TTS: raw API vs the LiveKit plugin, from this machine.

    VARTALOOP_API_KEY=... python bench/bench.py [--base-url https://api.vartaloop.ai] [--iters 8]
                                                [--concurrency 8,16] [--seconds 20] [--json out.json]

Measures, with the same Hinglish texts for every path:
  * TTFA (time to first audio byte) and E2E (to the last byte) of a 1-sentence and a 3-sentence text
    over raw REST, raw WebSocket, the plugin's ChunkedStream (REST) and SynthesizeStream (WebSocket).
    Paths are interleaved per iteration so network drift hits all of them alike; the plugin
    overhead is the median difference against the raw path of the same transport.
  * Sustained throughput with N concurrent streams (raw WebSocket and the plugin), each stream
    speaking the 3-sentence text back to back for --seconds: audio seconds produced per wall
    second, per-stream RTF and TTFA p50/p95 under load.
Audio is pcm16 at 24 kHz: 48000 bytes per second of speech.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from dataclasses import dataclass, field

import aiohttp

ONE = "नमस्ते, आपका loan approve हो गया है।"
THREE = (
    "नमस्ते, आपका loan approve हो गया है। "
    "पहली EMI 5 नवंबर को 12,450 रुपये की होगी। "
    "कोई सवाल हो तो हमें 1800 123 4567 पर call करें।"
)
BYTES_PER_S = 24000 * 2


@dataclass
class Sample:
    ttfa_ms: float
    e2e_ms: float
    audio_s: float


@dataclass
class Series:
    name: str
    samples: list[Sample] = field(default_factory=list)

    def p(self, attr: str, q: float) -> float:
        xs = sorted(getattr(s, attr) for s in self.samples)
        if not xs:
            return float("nan")
        k = min(len(xs) - 1, max(0, round(q * (len(xs) - 1))))
        return xs[k]

    def median(self, attr: str) -> float:
        return statistics.median(getattr(s, attr) for s in self.samples)


def _headers(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


# ---------------------------------------------------------------- raw paths


async def raw_rest(session: aiohttp.ClientSession, base: str, key: str, text: str) -> Sample:
    body = {"text": text, "voice": "saanvi", "format": "pcm16", "digits": "auto"}
    t0 = time.perf_counter()
    first = None
    n = 0
    async with session.post(f"{base}/v1/tts", json=body, headers=_headers(key)) as resp:
        if resp.status != 200:
            raise RuntimeError(f"REST {resp.status}: {await resp.text()}")
        async for chunk, _ in resp.content.iter_chunks():
            if first is None and chunk:
                first = time.perf_counter()
            n += len(chunk)
    t1 = time.perf_counter()
    assert first is not None
    return Sample((first - t0) * 1e3, (t1 - t0) * 1e3, n / BYTES_PER_S)


class RawWS:
    """One WebSocket, utterances sent one at a time (what a plugin does per sentence)."""

    def __init__(self, session: aiohttp.ClientSession, base: str, key: str) -> None:
        self._session, self._base, self._key = session, base, key
        self._ws: aiohttp.ClientWebSocketResponse | None = None

    async def connect(self) -> None:
        url = self._base.replace("http", "ws", 1) + "/v1/tts/stream"
        self._ws = await self._session.ws_connect(url, headers=_headers(self._key))

    async def close(self) -> None:
        if self._ws:
            await self._ws.close()

    async def speak(self, text: str, *, sentences: list[str] | None = None) -> Sample:
        """Send the text as one utterance, or ``sentences`` back to back, and drain the audio."""
        assert self._ws is not None
        msgs = sentences or [text]
        t0 = time.perf_counter()
        for i, s in enumerate(msgs):
            await self._ws.send_str(
                json.dumps({"text": s, "voice": "saanvi", "format": "pcm16", "digits": "auto", "request_id": f"b{i}"})
            )
        first = None
        n = 0
        ended = 0
        while ended < len(msgs):
            msg = await self._ws.receive(timeout=30)
            if msg.type == aiohttp.WSMsgType.BINARY:
                if first is None:
                    first = time.perf_counter()
                n += len(msg.data)
            elif msg.type == aiohttp.WSMsgType.TEXT:
                ev = json.loads(msg.data)
                if ev.get("type") == "end":
                    ended += 1
                elif ev.get("type") == "error":
                    raise RuntimeError(f"WS error: {ev}")
            else:
                raise RuntimeError(f"WS closed: {msg.type}")
        t1 = time.perf_counter()
        assert first is not None
        return Sample((first - t0) * 1e3, (t1 - t0) * 1e3, n / BYTES_PER_S)


# ---------------------------------------------------------------- plugin paths


def _plugin():
    from livekit.plugins import vartaloop

    return vartaloop


async def plugin_chunked(tts, text: str) -> Sample:
    t0 = time.perf_counter()
    first = None
    n = 0
    async for ev in tts.synthesize(text):
        if first is None:
            first = time.perf_counter()
        n += len(ev.frame.data) * 2
    t1 = time.perf_counter()
    assert first is not None
    return Sample((first - t0) * 1e3, (t1 - t0) * 1e3, n / BYTES_PER_S)


async def plugin_stream(tts, text: str) -> Sample:
    t0 = time.perf_counter()
    stream = tts.stream()
    stream.push_text(text)
    stream.end_input()
    first = None
    n = 0
    async for ev in stream:
        if first is None:
            first = time.perf_counter()
        n += len(ev.frame.data) * 2
    t1 = time.perf_counter()
    assert first is not None
    return Sample((first - t0) * 1e3, (t1 - t0) * 1e3, n / BYTES_PER_S)


# ---------------------------------------------------------------- runs


async def latency(base: str, key: str, iters: int) -> dict[str, Series]:
    vartaloop = _plugin()
    series = {
        k: Series(k)
        for k in (
            "rest/raw/1s", "rest/plugin/1s", "ws/raw/1s", "ws/plugin/1s",
            "rest/raw/3s", "rest/plugin/3s", "ws/raw/3s", "ws/plugin/3s",
        )
    }
    async with aiohttp.ClientSession() as session:
        tts = vartaloop.TTS(api_key=key, base_url=base, http_session=session)
        raw = RawWS(session, base, key)
        await raw.connect()
        tts.prewarm()
        # warm: first request pays the TLS handshake and the prefill cache miss on both sides
        await raw_rest(session, base, key, ONE)
        await plugin_chunked(tts, ONE)
        await raw.speak(ONE)
        await plugin_stream(tts, ONE)
        three_sents = [s.strip() + "।" for s in THREE.split("।") if s.strip()]
        for _ in range(iters):
            series["rest/raw/1s"].samples.append(await raw_rest(session, base, key, ONE))
            series["rest/plugin/1s"].samples.append(await plugin_chunked(tts, ONE))
            series["ws/raw/1s"].samples.append(await raw.speak(ONE))
            series["ws/plugin/1s"].samples.append(await plugin_stream(tts, ONE))
            series["rest/raw/3s"].samples.append(await raw_rest(session, base, key, THREE))
            series["rest/plugin/3s"].samples.append(await plugin_chunked(tts, THREE))
            series["ws/raw/3s"].samples.append(await raw.speak(THREE, sentences=three_sents))
            series["ws/plugin/3s"].samples.append(await plugin_stream(tts, THREE))
        await raw.close()
        await tts.aclose()
    return series


async def throughput(base: str, key: str, n: int, seconds: float, use_plugin: bool) -> dict:
    vartaloop = _plugin()
    three_sents = [s.strip() + "।" for s in THREE.split("।") if s.strip()]
    samples: list[Sample] = []
    stop = time.perf_counter() + seconds

    async with aiohttp.ClientSession() as session:
        tts = vartaloop.TTS(api_key=key, base_url=base, http_session=session) if use_plugin else None

        async def worker(i: int) -> None:
            raw = None
            if tts is None:
                raw = RawWS(session, base, key)
                await raw.connect()
            try:
                while time.perf_counter() < stop:
                    if tts is not None:
                        samples.append(await plugin_stream(tts, THREE))
                    else:
                        assert raw is not None
                        samples.append(await raw.speak(THREE, sentences=three_sents))
            finally:
                if raw is not None:
                    await raw.close()

        if tts is not None:
            # warm: the pool opens connections one at a time under its lock, so N streams
            # starting together would pay serialized handshakes; a plugin in production has
            # its connections open before the burst (prewarm + reuse). One untimed round.
            await asyncio.gather(*(plugin_stream(tts, ONE) for _ in range(n)))
        t0 = time.perf_counter()
        await asyncio.gather(*(worker(i) for i in range(n)))
        wall = time.perf_counter() - t0
        if tts is not None:
            await tts.aclose()

    s = Series(f"{'plugin' if use_plugin else 'raw'}/{n}")
    s.samples = samples
    audio = sum(x.audio_s for x in samples)
    return {
        "streams": n,
        "path": "ws/plugin" if use_plugin else "ws/raw",
        "utterances": len(samples),
        "audio_s_per_wall_s": audio / wall,
        "rtf_per_stream_median": statistics.median(x.e2e_ms / 1e3 / x.audio_s for x in samples),
        "ttfa_p50_ms": s.p("ttfa_ms", 0.5),
        "ttfa_p95_ms": s.p("ttfa_ms", 0.95),
        "e2e_p50_ms": s.p("e2e_ms", 0.5),
        "wall_s": wall,
    }


def fmt_latency(series: dict[str, Series]) -> str:
    lines = ["| path | text | TTFA p50 ms | TTFA p95 ms | E2E p50 ms | audio s | plugin − raw TTFA p50 ms |", "|---|---|---|---|---|---|---|"]
    for transport in ("rest", "ws"):
        for text in ("1s", "3s"):
            raw = series[f"{transport}/raw/{text}"]
            plg = series[f"{transport}/plugin/{text}"]
            for s, label in ((raw, "raw"), (plg, "plugin")):
                delta = f"{plg.median('ttfa_ms') - raw.median('ttfa_ms'):+.1f}" if label == "plugin" else ""
                lines.append(
                    f"| {transport}/{label} | {text} | {s.p('ttfa_ms', 0.5):.1f} | {s.p('ttfa_ms', 0.95):.1f} | "
                    f"{s.p('e2e_ms', 0.5):.0f} | {s.median('audio_s'):.2f} | {delta} |"
                )
    return "\n".join(lines)


def fmt_throughput(rows: list[dict]) -> str:
    lines = ["| path | streams | utterances | audio s / wall s | RTF per stream | TTFA p50 ms | TTFA p95 ms |", "|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(
            f"| {r['path']} | {r['streams']} | {r['utterances']} | {r['audio_s_per_wall_s']:.1f} | "
            f"{r['rtf_per_stream_median']:.3f} | {r['ttfa_p50_ms']:.0f} | {r['ttfa_p95_ms']:.0f} |"
        )
    return "\n".join(lines)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default=os.environ.get("VARTALOOP_BASE_URL", "https://api.vartaloop.ai"))
    ap.add_argument("--iters", type=int, default=8)
    ap.add_argument("--concurrency", default="8,16")
    ap.add_argument("--seconds", type=float, default=20.0)
    ap.add_argument("--json", default=None)
    ap.add_argument("--skip-throughput", action="store_true")
    ap.add_argument("--only-throughput", action="store_true")
    args = ap.parse_args()
    key = os.environ.get("VARTALOOP_API_KEY")
    if not key:
        sys.exit("set VARTALOOP_API_KEY")

    print(f"# Vartaloop TTS bench — LiveKit plugin — {time.strftime('%Y-%m-%d %H:%M')} — {args.base_url}\n")
    out: dict = {}
    if not args.only_throughput:
        series = await latency(args.base_url.rstrip("/"), key, args.iters)
        print(f"## Latency ({args.iters} interleaved iterations, warm)\n")
        print(fmt_latency(series))
        out["latency"] = {k: [s.__dict__ for s in v.samples] for k, v in series.items()}
    if not args.skip_throughput:
        rows = []
        for n in (int(x) for x in args.concurrency.split(",")):
            for use_plugin in (False, True):
                rows.append(await throughput(args.base_url.rstrip("/"), key, n, args.seconds, use_plugin))
        print(f"\n## Sustained throughput ({args.seconds:.0f} s per cell, 3-sentence text, pool warmed)\n")
        print(fmt_throughput(rows))
        out["throughput"] = rows
    if args.json:
        with open(args.json, "w") as f:
            json.dump(out, f, indent=1, ensure_ascii=False)


if __name__ == "__main__":
    asyncio.run(main())
