"""HTTP views: push-to-talk upload, room list, and clip download for speakers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from aiohttp import web
from homeassistant.components.ffmpeg import get_ffmpeg_manager
from homeassistant.components.http import KEY_HASS, HomeAssistantView
from homeassistant.helpers.network import NoURLAvailableError, get_url

from .const import (
    ALL_ROOMS,
    API_CLIP,
    API_ROOMS,
    API_TALK,
    DOMAIN,
    MAX_UPLOAD_BYTES,
)
from .core import TranscodeError, fan_out, mp3_seconds, resolve, rooms, to_playable

if TYPE_CHECKING:
    from . import RoomCallData


def _data(request: web.Request) -> RoomCallData:
    return request.app[KEY_HASS].data[DOMAIN]


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
        return self.json({"rooms": rooms(hass, data.speakers), "speakers": len(data.speakers)})


class TalkView(HomeAssistantView):
    """Receive a browser recording and broadcast it."""

    url = API_TALK
    name = "api:roomcall:talk"

    async def post(self, request: web.Request) -> web.Response:
        hass = request.app[KEY_HASS]
        data = _data(request)
        target = request.query.get("target", ALL_ROOMS)
        entity_ids = resolve(hass, data.speakers, [target])
        if not entity_ids:
            return self.json_message(f"No speakers for {target!r}", 404)
        body = await _read_body(request)
        if not body:
            return self.json_message("Empty recording", 400)
        if len(body) > MAX_UPLOAD_BYTES:
            return self.json_message("Recording too large", 413)
        try:
            audio, content_type = await to_playable(
                get_ffmpeg_manager(hass).binary,
                body,
                request.content_type or "application/octet-stream",
                data.gain_db,
                data.chime,
            )
        except TranscodeError as err:
            return self.json_message(f"Could not convert recording: {err}", 422)
        clip_id = data.clips.add(audio, content_type)
        ext = "mp3" if content_type == "audio/mpeg" else "wav"
        try:
            base = get_url(hass, allow_internal=True, allow_external=True, prefer_external=False)
        except NoURLAvailableError:
            return self.json_message("Set an internal URL under Settings > System > Network", 500)
        volume_q = request.query.get("volume")
        volume = float(volume_q) if volume_q else data.volume
        record = await fan_out(
            hass,
            entity_ids,
            f"{base}{API_CLIP.format(clip=f'{clip_id}.{ext}')}",
            volume,
            target=target,
            kind="talk",
        )
        return self.json(
            {
                "target": target,
                "ok": record.ok,
                "seconds": mp3_seconds(audio) if content_type == "audio/mpeg" else None,
                "results": [r.as_dict() for r in record.results],
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
