"""WebSocket API for live calls from the dashboard card.

The card subscribes to ``roomcall/live``, gets a binary handler id back, and streams raw
16 kHz mono PCM frames prefixed with that id (the same scheme Home Assistant's voice assistant
uses). An empty frame, or unsubscribing, ends the recording.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components import websocket_api
from homeassistant.components.ffmpeg import get_ffmpeg_manager
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.network import NoURLAvailableError

from .calls import CallError, CallRequest, place_call, prepare_call
from .const import ALL_ROOMS, API_LIVE, DOMAIN, PRIORITIES, PRIORITY_NORMAL
from .core import CallRecord
from .errors import Problem
from .live import LiveStream
from .schema import vol
from .stations import Station

if TYPE_CHECKING:
    from . import RoomCallData

_LOGGER = logging.getLogger(__name__)


@websocket_api.websocket_command(
    {
        vol.Required("type"): "roomcall/echo_pair",
        vol.Required("speaker"): str,
        vol.Optional("host"): str,
        vol.Optional("word"): vol.All(str, vol.Length(min=16, max=63)),
        vol.Optional("remove", default=False): bool,
    }
)
@websocket_api.async_response
async def ws_echo_pair(hass, connection, msg) -> None:
    """Only administrators may pair an endpoint or change its shared key."""
    data = hass.data.get(DOMAIN)
    if not connection.user or not connection.user.is_admin:
        connection.send_error(msg["id"], "forbidden", "RoomCall")
        return
    if data is None:
        connection.send_error(msg["id"], "not_ready", "RoomCall")
        return
    try:
        if msg["remove"]:
            await data.echoes.remove(msg["speaker"])
        else:
            await data.echoes.pair(msg["speaker"], msg.get("host", ""), msg.get("word", ""))
        connection.send_result(msg["id"], {"echoes": data.echoes.list()})
    except (ValueError, Problem):
        connection.send_error(msg["id"], "invalid_request", "RoomCall")


@websocket_api.websocket_command(
    {
        vol.Required("type"): "roomcall/echo_call",
        vol.Required("speaker"): str,
        vol.Optional("name", default="RoomCall"): vol.All(str, vol.Length(min=1, max=40)),
        vol.Optional("invitation"): str,
    }
)
@websocket_api.async_response
async def ws_echo_call(hass, connection, msg) -> None:
    from .communication import permitted

    data = hass.data.get(DOMAIN)
    if data is None or not connection.user:
        connection.send_error(msg["id"], "not_ready", "RoomCall")
        return
    try:
        if not await permitted(hass, msg["speaker"], connection.context(msg)):
            raise Problem("forbidden")
        call_id = data.echoes.start(
            msg["speaker"],
            connection.user.id,
            msg["name"],
            lambda event: connection.send_message(websocket_api.event_message(msg["id"], event)),
            msg.get("invitation"),
        )
        connection.subscriptions[msg["id"]] = lambda: data.echoes.end(call_id, "disconnected")
        connection.send_result(msg["id"], {"id": call_id})
    except Problem as err:
        connection.send_error(msg["id"], err.code, err.as_dict(hass)["error"]["message"])


@websocket_api.websocket_command(
    {
        vol.Required("type"): "roomcall/echo_audio",
        vol.Required("call_id"): str,
        vol.Optional("pcm"): vol.All(str, vol.Length(max=1712)),
        vol.Optional("hangup", default=False): bool,
    }
)
@websocket_api.async_response
async def ws_echo_audio(hass, connection, msg) -> None:
    data = hass.data.get(DOMAIN)
    if data is None or not connection.user:
        connection.send_error(msg["id"], "not_ready", "RoomCall")
        return
    try:
        data.echoes.owned(msg["call_id"], connection.user.id)
        if msg["hangup"]:
            data.echoes.end(msg["call_id"])
        else:
            await data.echoes.audio(msg["call_id"], connection.user.id, msg.get("pcm", ""))
        connection.send_result(msg["id"])
    except (Problem, OSError, TimeoutError) as err:
        code = err.code if isinstance(err, Problem) else "station_unavailable"
        connection.send_error(msg["id"], code, "RoomCall")


@callback
def async_register(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_job_events)
    websocket_api.async_register_command(hass, ws_live)
    websocket_api.async_register_command(hass, ws_station)
    websocket_api.async_register_command(hass, ws_session)
    websocket_api.async_register_command(hass, ws_echo_pair)
    websocket_api.async_register_command(hass, ws_echo_call)
    websocket_api.async_register_command(hass, ws_echo_audio)


@websocket_api.websocket_command(
    {
        vol.Required("type"): "roomcall/station",
        vol.Required("station_id"): vol.All(str, vol.Length(min=8, max=64)),
        vol.Required("room"): str,
        vol.Required("name"): vol.All(str, vol.Length(min=1, max=80)),
    }
)
@websocket_api.async_response
async def ws_station(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    data = hass.data.get(DOMAIN)
    if data is None or connection.user is None:
        connection.send_error(msg["id"], "not_ready", "RoomCall")
        return
    try:
        data.stations.register(
            Station(
                msg["station_id"],
                connection.user.id,
                msg["room"],
                msg["name"],
                lambda event: connection.send_message(
                    websocket_api.event_message(msg["id"], event)
                ),
            )
        )
    except Problem as err:
        connection.send_error(msg["id"], err.code, err.as_dict(hass)["error"]["message"])
        return
    connection.subscriptions[msg["id"]] = lambda: data.stations.remove(msg["station_id"])
    connection.send_result(msg["id"])


@websocket_api.websocket_command(
    {
        vol.Required("type"): "roomcall/session",
        vol.Required("station_id"): str,
        vol.Required("action"): vol.In(("call", "answer", "signal", "hangup")),
        vol.Optional("peer"): str,
        vol.Optional("session_id"): str,
        vol.Optional("signal"): dict,
    }
)
@websocket_api.async_response
async def ws_session(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    data = hass.data.get(DOMAIN)
    if data is None or connection.user is None:
        connection.send_error(msg["id"], "not_ready", "RoomCall")
        return
    try:
        if msg["action"] == "call":
            session_id = data.stations.invite(
                msg["station_id"], msg.get("peer", ""), connection.user.id
            )
        else:
            session_id = msg.get("session_id", "")
            data.stations.action(
                msg["station_id"], connection.user.id, session_id, msg["action"], msg.get("signal")
            )
        connection.send_result(msg["id"], {"id": session_id})
    except Problem as err:
        connection.send_error(msg["id"], err.code, err.as_dict(hass)["error"]["message"])


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
        deadline=asyncio.get_running_loop().time() + 120,
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
                    "id": record.call_id,
                    "ok": record.ok,
                    "evidence": record.evidence,
                    "audible": None,
                    "delivery_semantics": "request_accepted",
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
                        "ok": record.ok,
                        "evidence": record.evidence,
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


@websocket_api.websocket_command(
    {vol.Required("type"): "roomcall/job_events", vol.Required("job_id"): str}
)
@websocket_api.async_response
async def ws_job_events(hass, connection, msg):
    """Subscribe to one owned job; recheck current permissions on every update."""
    from .communication import execute

    context = connection.context(msg)
    snapshot = await execute(hass, "status", {"id": msg["job_id"]}, context)
    if not snapshot["ok"]:
        error = snapshot["error"]
        connection.send_error(msg["id"], error["code"], error["message"])
        return

    async def changed(event):
        if event.data.get("id") != msg["job_id"]:
            return
        result = await execute(hass, "status", {"id": msg["job_id"]}, context)
        if msg["id"] in connection.subscriptions:
            connection.send_message(websocket_api.event_message(msg["id"], result))

    connection.subscriptions[msg["id"]] = hass.bus.async_listen("roomcall_job_updated", changed)
    connection.send_result(msg["id"], snapshot)
