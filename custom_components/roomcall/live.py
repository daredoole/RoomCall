"""Live calls: speakers start playing while you are still talking.

Raw 16 kHz PCM arrives in small pieces (from the dashboard card over Home Assistant's
WebSocket, or from the Apple Home talk button). ffmpeg encodes it to MP3 as it arrives, with
the chime in front, and speakers fetch one growing URL that is served as the audio is made.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
import time
from collections.abc import AsyncIterator

from . import chimes
from .talkstream import SAMPLE_RATE

_LOGGER = logging.getLogger(__name__)

MAX_SECONDS = 120  # a stuck stream is cut off here
LIVE_TTL_SECONDS = 120  # finished streams stay fetchable this long


class LiveStream:
    """One live call: PCM in, a growing MP3 out."""

    def __init__(
        self,
        ffmpeg: str,
        *,
        gain_db: float = 0.0,
        chime: str | None = None,
        chime_url: str | None = None,
    ) -> None:
        self.id = secrets.token_urlsafe(16)
        self.mp3 = bytearray()
        self.pcm = bytearray()
        self.done = False
        self.created = time.monotonic()
        self.first_fetch: float | None = None
        self._ffmpeg = ffmpeg
        self._gain_db = gain_db
        self._chime = chime
        self._chime_url = chime_url
        self._changed = asyncio.Event()
        self._proc: asyncio.subprocess.Process | None = None
        self._reader: asyncio.Task[None] | None = None
        self._finished = asyncio.Event()

    def command(self) -> list[str]:
        voice = f"[0:a]aresample=44100,aformat=channel_layouts=mono,volume={self._gain_db}dB"
        if self._chime_url:
            inputs = ["-i", self._chime_url]
            graph = chimes.chime_filter(1, 1, from_file=True)
        elif self._chime in chimes.STYLES:
            inputs = chimes.tone_inputs(self._chime)
            graph = chimes.chime_filter(1, len(chimes.STYLES[self._chime]))
        else:
            inputs, graph = [], ""
        if graph:
            mix = [
                "-filter_complex",
                f"{graph};{voice}[voice];[chime][voice]concat=n=2:v=0:a=1[out]",
            ]
            mix += ["-map", "[out]"]
        else:
            mix = ["-af", f"aresample=44100,volume={self._gain_db}dB"]
        return [
            self._ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-fflags",
            "nobuffer",
            "-f",
            "s16le",
            "-ar",
            str(SAMPLE_RATE),
            "-ac",
            "1",
            "-i",
            "pipe:0",
            *inputs,
            *mix,
            "-ac",
            "1",
            "-ar",
            "44100",
            "-codec:a",
            "libmp3lame",
            "-b:a",
            "128k",
            "-flush_packets",
            "1",
            "-f",
            "mp3",
            "pipe:1",
        ]

    async def start(self) -> None:
        self._proc = await asyncio.create_subprocess_exec(
            *self.command(),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        self._reader = asyncio.ensure_future(self._read())

    async def _read(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        while chunk := await self._proc.stdout.read(4096):
            self.mp3 += chunk
            self._changed.set()
        await self._proc.wait()
        self.done = True
        self._changed.set()
        self._finished.set()

    def feed(self, pcm: bytes) -> None:
        """Add audio (16 kHz mono 16-bit). Ignored once the stream has ended."""
        if self._proc is None or self._proc.stdin is None or self._proc.stdin.is_closing():
            return
        if len(self.pcm) >= MAX_SECONDS * SAMPLE_RATE * 2:
            self.end()
            return
        self.pcm += pcm
        self._proc.stdin.write(pcm)

    def end(self) -> None:
        """No more audio: let ffmpeg finish the MP3."""
        if self._proc is not None and self._proc.stdin is not None:
            with contextlib.suppress(Exception):
                self._proc.stdin.close()

    async def wait_done(self, limit: float = MAX_SECONDS + 10) -> None:
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(limit):
                await self._finished.wait()

    async def abort(self) -> None:
        self.end()
        if self._proc is not None and self._proc.returncode is None:
            self._proc.kill()
        await self.wait_done(5)

    async def chunks(self) -> AsyncIterator[bytes]:
        """The MP3 from the start, following along while it is being made."""
        if self.first_fetch is None:
            self.first_fetch = time.monotonic()
            _LOGGER.debug(
                "RoomCall live %s: first fetch %.2f s after start",
                self.id[:6],
                self.first_fetch - self.created,
            )
        sent = 0
        while True:
            if sent < len(self.mp3):
                chunk = bytes(self.mp3[sent:])
                sent += len(chunk)
                yield chunk
                continue
            if self.done:
                return
            self._changed.clear()
            if sent < len(self.mp3) or self.done:
                continue
            await self._changed.wait()


class LiveStore:
    """Live streams by id; finished ones expire."""

    def __init__(self) -> None:
        self._streams: dict[str, LiveStream] = {}

    def add(self, stream: LiveStream) -> None:
        self.prune()
        self._streams[stream.id] = stream

    def get(self, stream_id: str) -> LiveStream | None:
        self.prune()
        return self._streams.get(stream_id)

    def prune(self) -> None:
        now = time.monotonic()
        for stream_id, stream in list(self._streams.items()):
            if now - stream.created > MAX_SECONDS + LIVE_TTL_SECONDS:
                del self._streams[stream_id]
