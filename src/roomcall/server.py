"""HTTP API and push-to-talk web app."""

from __future__ import annotations

import asyncio
import hmac
import logging
from collections.abc import Awaitable, Callable, Mapping
from importlib import resources
from typing import Any

from aiohttp import web

from . import __version__
from .broadcast import broadcast
from .clips import ClipStore
from .config import Config
from .discovery import discover, merge_speakers
from .models import Speaker, SpeakerKind
from .rooms import RoomMap
from .sinks.base import Sink
from .transcode import TranscodeError, to_playable

_LOGGER = logging.getLogger(__name__)

Discoverer = Callable[[float], Awaitable[list[Speaker]]]

MAX_UPLOAD_BYTES = 10 * 1024 * 1024


class State:
    def __init__(
        self, config: Config, sinks: Mapping[SpeakerKind, Sink], discoverer: Discoverer
    ) -> None:
        self.config = config
        self.sinks = sinks
        self.discoverer = discoverer
        self.rooms = RoomMap.from_config(config.rooms)
        self.clips = ClipStore()
        self.speakers: list[Speaker] = list(config.static_speakers)
        self._refresh_lock = asyncio.Lock()

    async def refresh(self) -> list[Speaker]:
        async with self._refresh_lock:
            found = await self.discoverer(self.config.discovery_timeout)
            self.speakers = merge_speakers([*self.config.static_speakers, *found])
            _LOGGER.info("Found %d speakers", len(self.speakers))
            return self.speakers


CONFIG_KEY = web.AppKey("config", Config)
STATE_KEY = web.AppKey("state", State)


def _speaker_json(s: Speaker) -> dict[str, Any]:
    return {
        "id": s.id,
        "name": s.name,
        "kind": s.kind.value,
        "host": s.host,
        "model": s.model,
        "room": s.room,
    }


@web.middleware
async def auth_middleware(
    request: web.Request, handler: Callable[[web.Request], Awaitable[web.StreamResponse]]
) -> web.StreamResponse:
    token = request.app[CONFIG_KEY].token
    if token and request.path.startswith("/api/"):
        supplied = request.headers.get("X-RoomCall-Token") or request.query.get("token", "")
        if not hmac.compare_digest(supplied, token):
            raise web.HTTPUnauthorized(text="missing or invalid RoomCall token")
    return await handler(request)


async def index(request: web.Request) -> web.Response:
    html = resources.files("roomcall.web").joinpath("index.html").read_text()
    return web.Response(text=html, content_type="text/html")


async def status(request: web.Request) -> web.Response:
    state = request.app[STATE_KEY]
    return web.json_response(
        {
            "version": __version__,
            "speakers": len(state.speakers),
            "base_url": state.config.base_url(),
        }
    )


async def list_speakers(request: web.Request) -> web.Response:
    state = request.app[STATE_KEY]
    return web.json_response([_speaker_json(s) for s in state.rooms.assign(state.speakers)])


async def list_rooms(request: web.Request) -> web.Response:
    state = request.app[STATE_KEY]
    assigned = state.rooms.assign(state.speakers)
    rooms = state.rooms.room_names(state.speakers)
    return web.json_response(
        [{"name": room, "speakers": [s.name for s in assigned if s.room == room]} for room in rooms]
    )


async def refresh(request: web.Request) -> web.Response:
    speakers = await request.app[STATE_KEY].refresh()
    return web.json_response({"speakers": len(speakers)})


async def talk(request: web.Request) -> web.Response:
    state = request.app[STATE_KEY]
    target = request.query.get("target", "all")
    speakers = state.rooms.resolve(target, state.speakers)
    if not speakers:
        raise web.HTTPNotFound(text=f"no speakers for target {target!r}")
    body = await request.read()
    if not body:
        raise web.HTTPBadRequest(text="empty recording")
    try:
        audio, ext = await to_playable(
            body, request.content_type or "application/octet-stream", gain_db=state.config.gain_db
        )
    except TranscodeError as err:
        raise web.HTTPUnprocessableEntity(text=str(err)) from err
    clip = state.clips.add(audio, "audio/mpeg" if ext == "mp3" else "audio/wav", ext)
    url = f"{state.config.base_url()}/clips/{clip.id}.{ext}"
    volume_q = request.query.get("volume")
    volume = float(volume_q) if volume_q else state.config.volume
    result = await broadcast(target, speakers, url, state.sinks, volume=volume)
    return web.json_response(
        {
            "target": target,
            "ok": result.ok,
            "clip": url,
            "results": [
                {
                    "speaker": r.speaker.name,
                    "ok": r.ok,
                    "elapsed_ms": round(r.elapsed_ms),
                    "error": r.error,
                }
                for r in result.results
            ],
        },
        status=200 if result.ok else 207,
    )


async def get_clip(request: web.Request) -> web.Response:
    clip_id = request.match_info["clip"].split(".", 1)[0]
    clip = request.app[STATE_KEY].clips.get(clip_id)
    if clip is None:
        raise web.HTTPNotFound()
    return web.Response(
        body=clip.data, content_type=clip.content_type, headers={"Cache-Control": "no-store"}
    )


def create_app(
    config: Config,
    sinks: Mapping[SpeakerKind, Sink],
    discoverer: Discoverer = discover,
    *,
    discover_on_start: bool = True,
) -> web.Application:
    app = web.Application(middlewares=[auth_middleware], client_max_size=MAX_UPLOAD_BYTES)
    state = State(config, sinks, discoverer)
    app[CONFIG_KEY] = config
    app[STATE_KEY] = state
    app.router.add_get("/", index)
    app.router.add_get("/api/status", status)
    app.router.add_get("/api/speakers", list_speakers)
    app.router.add_get("/api/rooms", list_rooms)
    app.router.add_post("/api/refresh", refresh)
    app.router.add_post("/api/talk", talk)
    app.router.add_get("/clips/{clip}", get_clip)

    if discover_on_start:

        async def _startup(app: web.Application) -> None:
            await state.refresh()

        app.on_startup.append(_startup)
    return app
