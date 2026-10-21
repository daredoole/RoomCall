"""HTTP views: push-to-talk upload, rooms, clips for speakers, chimes and call history."""

from __future__ import annotations

from typing import TYPE_CHECKING

from aiohttp import web
from homeassistant.components.ffmpeg import get_ffmpeg_manager
from homeassistant.components.http import KEY_HASS, HomeAssistantView
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.network import get_url
from homeassistant.util import dt as dt_util

from . import chimes
from .calls import CallError, place_recorded_call
from .const import (
    ALL_ROOMS,
    API_CHIME,
    API_CLIP,
    API_HISTORY,
    API_HISTORY_CLIP,
    API_LIVE,
    API_ROOMS,
    API_TALK,
    DOMAIN,
    MAX_UPLOAD_BYTES,
    PRIORITIES,
    PRIORITY_NORMAL,
)
from .core import friendly, mp3_seconds, render_chime, rooms

if TYPE_CHECKING:
    from . import RoomCallData


def _data(request: web.Request) -> RoomCallData:
    return request.app[KEY_HASS].data[DOMAIN]


def base_url(hass: HomeAssistant) -> str:
    """Address speakers use to fetch clips (Home Assistant's internal URL)."""
    return get_url(hass, allow_internal=True, allow_external=True, prefer_external=False)


async def _read_body(request: web.Request) -> bytes:
    """Read the whole upload, stopping just past the size limit.

    ``StreamReader.read(n)`` returns as soon as any data is buffered, so behind a reverse
    proxy (which forwards the body in pieces) a single read only gets the first chunk.
    """
    body = bytearray()
    while len(body) <= MAX_UPLOAD_BYTES:
        chunk = await request.content.read(MAX_UPLOAD_BYTES + 1 - len(body))
        if not chunk:
            break
        body += chunk
    return bytes(body)


class RoomsView(HomeAssistantView):
    url = API_ROOMS
    name = "api:roomcall:rooms"

    async def get(self, request: web.Request) -> web.Response:
        hass = request.app[KEY_HASS]
        data = _data(request)
        return self.json(
            {
                "rooms": rooms(hass, data.speakers),
                "speakers": len(data.speakers),
                "people": [
                    {"entity_id": p, "name": friendly(hass, p)}
                    for p in sorted(hass.states.async_entity_ids("person"))
                ],
                "dnd": data.dnd,
                "dnd_entity": er.async_get(hass).async_get_entity_id(
                    "switch", DOMAIN, f"{data.entry_id}_dnd"
                ),
                "chimes": [*chimes.STYLES],
                "quiet": data.quiet.active(dt_util.now()),
            }
        )


class TalkView(HomeAssistantView):
    """Receive a browser recording and broadcast it."""

    url = API_TALK
    name = "api:roomcall:talk"

    async def post(self, request: web.Request) -> web.Response:
        hass = request.app[KEY_HASS]
        data = _data(request)
        query = request.query
        target = query.get("target", ALL_ROOMS)
        priority = query.get("priority", PRIORITY_NORMAL)
        if priority not in PRIORITIES:
            return self.json_message(f"Unknown priority {priority!r}", 400)
        body = await _read_body(request)
        if not body:
            return self.json_message("Empty recording", 400)
        if len(body) > MAX_UPLOAD_BYTES:
            return self.json_message("Recording too large", 413)
        volume_q = query.get("volume")
        user = request.get("hass_user")
        person = query.get("person")
        try:
            record, audio, content_type = await place_recorded_call(
                hass,
                data,
                body,
                request.content_type or "application/octet-stream",
                targets=[] if person else [target],
                people=[person] if person else None,
                priority=priority,
                from_label=query.get("from") or None,
                volume=float(volume_q) if volume_q else None,
                caller_user_id=user.id if user else None,
            )
        except CallError as err:
            return self.json_message(str(err), err.status)
        return self.json(
            {
                "id": record.call_id,
                "target": record.target,
                "ok": record.ok,
                "seconds": mp3_seconds(audio) if content_type == "audio/mpeg" else None,
                "results": [r.as_dict() for r in record.results],
                "skipped": record.skipped,
                "transcript": record.transcript,
            },
            status_code=200 if record.ok else 207,
        )


class ClipView(HomeAssistantView):
    """Speakers cannot authenticate, so clips use unguessable, short-lived ids."""

    url = API_CLIP
    name = "api:roomcall:clip"
    requires_auth = False

    async def get(self, request: web.Request, clip: str) -> web.Response:
        found = _data(request).clips.get(clip.split(".", 1)[0])
        if found is None:
            return web.Response(status=404)
        return web.Response(
            body=found.data,
            content_type=found.content_type,
            headers={"Cache-Control": "no-store"},
        )


class LiveView(HomeAssistantView):
    """A live call's MP3, served while it is still being recorded.

    Like a clip, it has an unguessable short-lived id because speakers cannot sign in.
    """

    url = API_LIVE
    name = "api:roomcall:live"
    requires_auth = False

    async def head(self, request: web.Request, stream: str) -> web.Response:
        if _data(request).live.get(stream.removesuffix(".mp3")) is None:
            return web.Response(status=404)
        return web.Response(content_type="audio/mpeg", headers={"Cache-Control": "no-store"})

    async def get(self, request: web.Request, stream: str) -> web.StreamResponse:
        live = _data(request).live.get(stream.removesuffix(".mp3"))
        if live is None:
            return web.Response(status=404)
        response = web.StreamResponse(
            headers={"Content-Type": "audio/mpeg", "Cache-Control": "no-store"}
        )
        response.enable_chunked_encoding()
        await response.prepare(request)
        async for chunk in live.chunks():
            await response.write(chunk)
        await response.write_eof()
        return response


class ChimeView(HomeAssistantView):
    """Built-in chime tones as MP3 (public: they are just tones, used by Chime TTS)."""

    url = API_CHIME
    name = "api:roomcall:chime"
    requires_auth = False

    async def get(self, request: web.Request, style: str) -> web.Response:
        hass = request.app[KEY_HASS]
        style = style.removesuffix(".mp3")
        if style not in chimes.STYLES:
            return web.Response(status=404)
        cache = _data(request).chime_cache
        if style not in cache:
            cache[style] = await render_chime(get_ffmpeg_manager(hass).binary, style)
        return web.Response(
            body=cache[style],
            content_type="audio/mpeg",
            headers={"Cache-Control": "public, max-age=86400"},
        )


class HistoryView(HomeAssistantView):
    url = API_HISTORY
    name = "api:roomcall:history"

    async def get(self, request: web.Request) -> web.Response:
        return self.json({"calls": [r.summary() for r in _data(request).history.records()]})


class HistoryClipView(HomeAssistantView):
    """Replay a recent call's audio (signed-in users only)."""

    url = API_HISTORY_CLIP
    name = "api:roomcall:history_clip"

    async def get(self, request: web.Request, call_id: str) -> web.Response:
        found = _data(request).history.audio(call_id.removesuffix(".mp3"))
        if found is None:
            return web.Response(status=404)
        return web.Response(
            body=found.data,
            content_type=found.content_type,
            headers={"Cache-Control": "private, max-age=3600"},
        )
