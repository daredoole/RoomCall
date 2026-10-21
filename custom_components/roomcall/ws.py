"""WebSocket API for live calls from the dashboard card.

The card subscribes to ``roomcall/live``, gets a binary handler id back, and streams raw
16 kHz mono PCM frames prefixed with that id (the same scheme Home Assistant's voice assistant
uses). An empty frame, or unsubscribing, ends the recording.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.components.ffmpeg import get_ffmpeg_manager
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.network import NoURLAvailableError

from .calls import CallError, CallRequest, place_call, prepare_call
from .const import ALL_ROOMS, API_LIVE, DOMAIN, PRIORITIES, PRIORITY_NORMAL
from .core import CallRecord
from .live import LiveStream

if TYPE_CHECKING:
    from . import RoomCallData

_LOGGER = logging.getLogger(__name__)


@callback
def async_register(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_live)


@websocket_api.websocket_command(
    {
        vol.Required("type"): "roomcall/live",
        vol.Optional("target", default=ALL_ROOMS): str,
        vol.Optional("person"): str,
        vol.Optional("priority", default=PRIORITY_NORMAL): vol.In(PRIORITIES),
        vol.Optional("from"): str,
        vol.Optional("volume"): vol.All(vol.Coerce(float), vol.Range(min=0, max=1)),
    }
)
@websocket_api.async_response
async def ws_live(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    from .http import base_url

    data: RoomCallData | None = hass.data.get(DOMAIN)
    msg_id = msg["id"]
    if data is None:
        connection.send_error(msg_id, "not_ready", "RoomCall is not set up")
        return
    person = msg.get("person")
    request = CallRequest(
        targets=[] if person else [msg["target"]],
        people=[person] if person else None,
        media_id="",
        kind="talk",
        priority=msg["priority"],
        from_room=msg.get("from"),
        volume=msg.get("volume"),
        caller_user_id=connection.user.id if connection.user else None,
    )
    try:
        prepared = prepare_call(hass, data, request)
        base = base_url(hass)
    except CallError as err:
        connection.send_error(msg_id, "call_refused", str(err))
        return
    except NoURLAvailableError:
        connection.send_error(
            msg_id, "no_url", "Set an internal URL under Settings > System > Network"
        )
        return

    stream = LiveStream(
        get_ffmpeg_manager(hass).binary,
        gain_db=data.gain_db,
        chime=data.chime_style,
        chime_url=data.chime_url,
    )
    await stream.start()
    data.live.add(stream)
    request.media_id = f"{base}{API_LIVE.format(stream=f'{stream.id}.mp3')}"
    request.live = stream

    @callback
    def delivered(record: CallRecord) -> None:
        connection.send_message(
            websocket_api.event_message(
                msg_id,
                {
                    "type": "delivered",
                    "ok": record.ok,
                    "results": [r.as_dict() for r in record.results],
                    "skipped": record.skipped,
                },
            )
        )

    request.on_delivered = delivered

    @callback
    def on_audio(_hass: HomeAssistant, _conn: websocket_api.ActiveConnection, pcm: bytes) -> None:
        if pcm:
            stream.feed(pcm)
        else:
            stream.end()

    handler_id, unregister = connection.async_register_binary_handler(on_audio)

    async def run() -> None:
        try:
            record = await place_call(hass, data, request, prepared)
        except CallError as err:
            stream.end()
            connection.send_message(
                websocket_api.event_message(msg_id, {"type": "error", "message": str(err)})
            )
        except Exception as err:
            _LOGGER.exception("RoomCall live call failed")
            await stream.abort()
            connection.send_message(
                websocket_api.event_message(msg_id, {"type": "error", "message": str(err)})
            )
        else:
            connection.send_message(
                websocket_api.event_message(
                    msg_id,
                    {
                        "type": "done",
                        "id": record.call_id,
                        "seconds": round(len(stream.pcm) / 32000, 1),
                        "transcript": record.transcript,
                    },
                )
            )
        finally:
            unregister()

    hass.async_create_background_task(run(), f"roomcall live {stream.id[:6]}")
    # Unsubscribing (or the browser going away) ends the recording; what was said still plays.
    connection.subscriptions[msg_id] = stream.end
    connection.send_result(msg_id)
    connection.send_message(
        websocket_api.event_message(msg_id, {"type": "ready", "handler_id": handler_id})
    )
