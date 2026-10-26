"""Native local Assist sentences for announcements and answered Echo calls."""

from __future__ import annotations

from typing import Any

from homeassistant.components.conversation.trigger import async_attach_trigger
from homeassistant.core import Context
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.script import ScriptRunResult

from .communication import execute, permitted
from .core import area_id_for
from .errors import Problem
from .localization import catalog, text


async def register(hass, entry, data) -> None:
    sentences = []
    for language in ("en", "es", "de", "fr", "nl"):
        strings = catalog(language)
        sentences.extend([strings["voice_announce_pattern"], strings["voice_call_pattern"]])

    async def handle(variables: dict[str, Any]) -> ScriptRunResult:
        trigger = variables["trigger"]
        user_input = trigger["user_input"]
        context = Context(user_id=user_input["context"].get("user_id"))
        language = user_input["language"]
        slots = trigger["slots"]
        area = area_id_for(hass, slots["area"])
        response = text(hass, "no_targets", language)
        if area:
            if "message" in slots:
                result = await execute(
                    hass,
                    "send",
                    {
                        "areas": [area],
                        "message": slots["message"],
                        "idempotency_key": "voice-" + context.id,
                    },
                    context,
                    "conversation",
                )
                response = (
                    text(hass, "voice_queued", language, room=slots["area"])
                    if result["ok"]
                    else result["error"]["message"]
                )
            else:
                try:
                    response = await call(hass, data, trigger, context, area, language)
                except Problem as err:
                    response = text(hass, err.code, language)
        return ScriptRunResult(conversation_response=response, service_response=None, variables={})

    remove = await async_attach_trigger(
        hass,
        {"platform": "conversation", "command": sentences},
        handle,
        {"domain": "roomcall", "name": "RoomCall", "trigger_data": {"id": "roomcall", "idx": "0"}},
    )
    entry.async_on_unload(remove)


async def call(hass, data, trigger, context, area: str, language: str) -> str:
    registry = er.async_get(hass)
    satellite = registry.async_get(trigger.get("satellite_id") or "")
    sources = [
        speaker
        for speaker in data.echoes.peers
        if satellite
        and (entity := registry.async_get(speaker))
        and entity.device_id == satellite.device_id
    ]
    stations = [station for station in data.stations.items.values() if station.room == area]
    if len(sources) == 1 and len(stations) == 1:
        if not await permitted(hass, sources[0], context, "conversation"):
            return text(hass, "forbidden", language)
        data.echoes.invite(sources[0], stations[0])
        return text(hass, "voice_calling", language, room=trigger["slots"]["area"])
    # A voice command outside a paired Echo cannot open a browser microphone by itself.
    return text(hass, "voice_call_help", language, room=trigger["slots"]["area"])
