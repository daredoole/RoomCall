"""Call notifications with a Reply button, and speech-to-text transcripts."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlencode

from homeassistant.components import stt
from homeassistant.core import HomeAssistant
from homeassistant.util import slugify

from .core import CallRecord

_LOGGER = logging.getLogger(__name__)


def _service_users(hass: HomeAssistant) -> dict[str, str | None]:
    """``notify`` service name -> Home Assistant user id, for mobile app devices."""
    out: dict[str, str | None] = {}
    for entry in hass.config_entries.async_entries("mobile_app"):
        name = entry.data.get("device_name")
        if name:
            out[f"mobile_app_{slugify(name)}"] = entry.data.get("user_id")
    return out


def reply_uri(dashboard_path: str, room: str | None) -> str:
    path = dashboard_path if dashboard_path.startswith("/") else f"/{dashboard_path}"
    return f"{path}?{urlencode({'roomcall': room or 'all'})}"


async def notify_call(
    hass: HomeAssistant,
    record: CallRecord,
    *,
    services: list[str],
    dashboard_path: str | None,
    caller_user_id: str | None,
    room_names: dict[str, str],
) -> list[str]:
    """Tell phones about a call; returns the services that were notified.

    The caller's own devices are skipped. When a dashboard path is set, the notification
    gets a Reply button that opens the card aimed back at the caller's room.
    """
    users = _service_users(hass)
    origin = room_names.get(record.from_room or "", record.from_room) or "someone"
    target = ", ".join(room_names.get(t.strip(), t.strip()) for t in record.target.split(","))
    title = "Urgent call" if record.priority == "urgent" else "RoomCall"
    body = f"{origin} → {target}"
    if text := record.transcript or record.message:
        body += f": “{text}”"
    data: dict[str, Any] = {"tag": "roomcall", "group": "roomcall"}
    if dashboard_path:
        uri = reply_uri(dashboard_path, record.from_room)
        data |= {"url": uri, "clickAction": uri}
        data["actions"] = [{"action": "URI", "title": "Reply", "uri": uri}]
    if record.priority == "urgent":
        data["push"] = {"interruption-level": "time-sensitive"}
    sent = []
    for name in services:
        service = name.removeprefix("notify.")
        if caller_user_id and users.get(service) == caller_user_id:
            continue
        if not hass.services.has_service("notify", service):
            _LOGGER.warning("RoomCall notify target notify.%s does not exist", service)
            continue
        await hass.services.async_call(
            "notify", service, {"title": title, "message": body, "data": data}, blocking=False
        )
        sent.append(service)
    return sent


async def transcribe(hass: HomeAssistant, engine_id: str, pcm: bytes) -> str | None:
    """Run 16 kHz mono PCM through a speech-to-text entity."""
    engine = stt.async_get_speech_to_text_entity(hass, engine_id)
    if engine is None:
        _LOGGER.warning("RoomCall speech-to-text engine %s not found", engine_id)
        return None
    language = hass.config.language
    if language not in engine.supported_languages:
        language = engine.supported_languages[0]
    metadata = stt.SpeechMetadata(
        language=language,
        format=stt.AudioFormats.WAV,
        codec=stt.AudioCodecs.PCM,
        bit_rate=stt.AudioBitRates.BITRATE_16,
        sample_rate=stt.AudioSampleRates.SAMPLERATE_16000,
        channel=stt.AudioChannels.CHANNEL_MONO,
    )

    async def stream():
        for i in range(0, len(pcm), 3200):
            yield pcm[i : i + 3200]

    result = await engine.internal_async_process_audio_stream(metadata, stream())
    if result.result != stt.SpeechResultState.SUCCESS or not result.text:
        return None
    return result.text.strip()
