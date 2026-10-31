"""End-to-end tests for the RoomCall Home Assistant integration."""

from __future__ import annotations

import asyncio
import io
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path

import pytest
from custom_components.roomcall.agent_policy import AgentPolicies
from custom_components.roomcall.calls import CallRequest, prepare_call
from custom_components.roomcall.communication import execute
from custom_components.roomcall.core import group_names
from custom_components.roomcall.errors import Problem
from custom_components.roomcall.profiles import validate as validate_profiles
from homeassistant import config_entries
from homeassistant.core import Context, HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import floor_registry as fr
from homeassistant.helpers import label_registry as lr
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

DOMAIN = "roomcall"


def _wav() -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\x00\x00" * 800)
    return buf.getvalue()


async def test_config_flow_creates_entry(hass: HomeAssistant, house) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"speakers": ["media_player.kitchen_sonos"], "gain_db": 0}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["speakers"] == ["media_player.kitchen_sonos"]


async def test_config_flow_requires_speakers(hass: HomeAssistant, house) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"speakers": [], "gain_db": 0}
    )
    assert result["errors"] == {"speakers": "no_speakers"}


async def test_broadcast_message_to_one_room(hass: HomeAssistant, setup_roomcall, house) -> None:
    calls = async_mock_service(hass, "media_player", "play_media")
    hass.states.async_set("tts.piper", "unknown")
    response = await hass.services.async_call(
        DOMAIN,
        "broadcast",
        {"areas": [house["kitchen"]], "message": "Dinner is ready"},
        blocking=True,
        return_response=True,
    )
    played = sorted(c.data["entity_id"] for c in calls)
    assert played == ["media_player.kitchen_hub", "media_player.kitchen_sonos"]
    assert all(c.data["announce"] is True for c in calls)
    assert calls[0].data["media_content_id"].startswith("http://ha.local:8123/api/roomcall/clip/")
    assert calls[0].data["extra"] == {"volume": 40}
    assert response["ok"] is True
    state = hass.states.get("sensor.roomcall_last_call")
    assert state is not None and state.attributes["kind"] == "message"


async def test_broadcast_by_area_name_and_everywhere(hass: HomeAssistant, setup_roomcall) -> None:
    calls = async_mock_service(hass, "media_player", "play_media")
    await hass.services.async_call(
        DOMAIN, "broadcast", {"areas": ["Basement"], "media_url": "http://x/a.mp3"}, blocking=True
    )
    assert [c.data["entity_id"] for c in calls] == ["media_player.basement_sonos"]
    calls.clear()
    await hass.services.async_call(
        DOMAIN, "broadcast", {"media_url": "http://x/a.mp3"}, blocking=True
    )
    assert len(calls) == 3


async def test_rooms_view(hass: HomeAssistant, setup_roomcall, hass_client) -> None:
    client = await hass_client()
    body = await (await client.get("/api/roomcall/rooms")).json()
    assert body["speakers"] == 3
    names = {r["name"]: len(r["speakers"]) for r in body["rooms"]}
    assert names == {"Basement": 1, "Kitchen": 2}


async def test_talk_uploads_plays_and_serves_clip(
    hass: HomeAssistant, setup_roomcall, house, hass_client, hass_client_no_auth
) -> None:
    calls = async_mock_service(hass, "media_player", "play_media")
    client = await hass_client()
    resp = await client.post(
        f"/api/roomcall/talk?target={house['basement']}",
        data=_wav(),
        headers={"Content-Type": "audio/wav"},
    )
    body = await resp.json()
    assert resp.status == 200, body
    assert [r["entity_id"] for r in body["results"]] == ["media_player.basement_sonos"]
    url = calls[0].data["media_content_id"]
    assert url.startswith("http://ha.local:8123/api/roomcall/clip/")
    anon = await hass_client_no_auth()
    clip = await anon.get(url.removeprefix("http://ha.local:8123"))
    assert clip.status == 200
    audio = await clip.read()
    # Chime is on by default, so the WAV is re-encoded to MP3 with the chirp prepended.
    assert audio[:3] == b"ID3" or audio[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2")
    assert body["seconds"] and body["seconds"] > 0.3


def _iphone_mp4() -> bytes:
    """AAC in MP4 with the index (moov atom) at the end, as Safari's MediaRecorder writes it."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("ffmpeg not installed")
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "rec.mp4"
        subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-t",
                "1",
                "-i",
                "sine=frequency=440:sample_rate=44100",
                "-c:a",
                "aac",
                str(out),
            ],
            check=True,
        )
        data = out.read_bytes()
    assert data.rfind(b"moov") > data.find(b"mdat")  # index after the audio
    return data


async def test_talk_accepts_iphone_mp4(
    hass: HomeAssistant, setup_roomcall, house, hass_client
) -> None:
    async_mock_service(hass, "media_player", "play_media")
    client = await hass_client()
    data = _iphone_mp4()

    async def chunks():  # a reverse proxy forwards the upload in pieces
        for i in range(0, len(data), 1024):
            yield data[i : i + 1024]
            await asyncio.sleep(0)

    resp = await client.post(
        f"/api/roomcall/talk?target={house['basement']}",
        data=chunks(),
        headers={"Content-Type": "audio/mp4"},
    )
    body = await resp.json()
    assert resp.status == 200, body
    assert body["seconds"] > 0.8


async def test_talk_requires_auth(hass: HomeAssistant, setup_roomcall, hass_client_no_auth) -> None:
    anon = await hass_client_no_auth()
    resp = await anon.post("/api/roomcall/talk?target=all", data=_wav())
    assert resp.status == 401


async def test_unknown_room_is_404(hass: HomeAssistant, setup_roomcall, hass_client) -> None:
    client = await hass_client()
    resp = await client.post("/api/roomcall/talk?target=attic", data=_wav())
    assert resp.status == 404


async def test_unload(hass: HomeAssistant, setup_roomcall) -> None:
    assert await hass.config_entries.async_unload(setup_roomcall.entry_id)
    assert DOMAIN not in hass.data


async def test_message_has_builtin_chime_even_with_chime_tts_loaded(
    hass: HomeAssistant, setup_roomcall, house
) -> None:
    hass.config.components.add("chime_tts")
    chime_calls = async_mock_service(hass, "chime_tts", "say")
    play_calls = async_mock_service(hass, "media_player", "play_media")
    hass.states.async_set("tts.piper", "unknown")
    await hass.services.async_call(
        DOMAIN, "broadcast", {"areas": [house["basement"]], "message": "Hi"}, blocking=True
    )
    assert not chime_calls
    assert (
        play_calls[0].data["media_content_id"].startswith("http://ha.local:8123/api/roomcall/clip/")
    )
    assert play_calls[0].data["announce"] is True
    assert play_calls[0].data["extra"] == {"volume": 40}


async def test_chime_can_be_disabled(hass: HomeAssistant, house) -> None:
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(
        domain=DOMAIN, data={"speakers": ["media_player.basement_sonos"], "chime": False}
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    hass.config.components.add("chime_tts")
    hass.states.async_set("tts.piper", "unknown")
    play_calls = async_mock_service(hass, "media_player", "play_media")
    await hass.services.async_call(DOMAIN, "broadcast", {"message": "Hi"}, blocking=True)
    assert play_calls[0].data["media_content_id"].startswith("media-source://tts/")


@pytest.fixture
async def floors(hass: HomeAssistant, setup_roomcall, house) -> dict[str, str]:
    """Attic joins the kitchen upstairs, so one floor holds two of three speaker rooms."""
    areas = ar.async_get(hass)
    registry = er.async_get(hass)
    upstairs = fr.async_get(hass).async_create("Upstairs", aliases={"Top floor"})
    downstairs = fr.async_get(hass).async_create("Downstairs")
    attic = areas.async_create("Attic", floor_id=upstairs.floor_id)
    areas.async_update(house["kitchen"], floor_id=upstairs.floor_id)
    areas.async_update(house["basement"], floor_id=downstairs.floor_id)
    entry = registry.async_get_or_create(
        "media_player", "test", "attic", suggested_object_id="attic"
    )
    registry.async_update_entity(entry.entity_id, area_id=attic.id)
    hass.states.async_set(entry.entity_id, "idle", {"supported_features": 1048576 | 512})
    data = hass.data[DOMAIN]
    data.speakers = [*data.speakers, entry.entity_id]
    return {"upstairs": upstairs.floor_id, "downstairs": downstairs.floor_id}


@pytest.mark.parametrize("floor", ["upstairs", "Upstairs", "top floor"])
async def test_broadcast_to_a_floor_by_id_name_or_alias(hass, floors, floor) -> None:
    calls = async_mock_service(hass, "media_player", "play_media")
    await hass.services.async_call(
        DOMAIN, "broadcast", {"floors": [floor], "media_url": "http://x/a.mp3"}, blocking=True
    )
    assert sorted(c.data["entity_id"] for c in calls) == [
        "media_player.attic",
        "media_player.kitchen_hub",
        "media_player.kitchen_sonos",
    ]


async def test_broadcast_combines_floor_with_a_room(hass, floors, house) -> None:
    calls = async_mock_service(hass, "media_player", "play_media")
    await hass.services.async_call(
        DOMAIN,
        "broadcast",
        {"floors": "downstairs", "areas": ["Attic"], "media_url": "http://x/a.mp3"},
        blocking=True,
    )
    assert sorted(c.data["entity_id"] for c in calls) == [
        "media_player.attic",
        "media_player.basement_sonos",
    ]


async def test_unknown_floor_or_label_never_means_everywhere(hass, floors) -> None:
    calls = async_mock_service(hass, "media_player", "play_media")
    for field in ("floors", "labels"):
        with pytest.raises(ServiceValidationError):
            await hass.services.async_call(
                DOMAIN, "broadcast", {field: ["nope"], "media_url": "http://x/a.mp3"}, blocking=True
            )
    assert calls == []


async def test_label_on_speaker_device_or_area(hass, floors, house) -> None:
    labels = lr.async_get(hass)
    label = labels.async_create("Bedtime").label_id
    registry = er.async_get(hass)
    entry = MockConfigEntry(domain="test")
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={("test", "hub")}
    )
    dr.async_get(hass).async_update_device(device.id, labels={label})
    registry.async_update_entity("media_player.kitchen_hub", device_id=device.id)
    registry.async_update_entity("media_player.attic", labels={label})
    ar.async_get(hass).async_update(house["basement"], labels={label})
    calls = async_mock_service(hass, "media_player", "play_media")
    await hass.services.async_call(
        DOMAIN, "broadcast", {"labels": ["Bedtime"], "media_url": "http://x/a.mp3"}, blocking=True
    )
    assert sorted(c.data["entity_id"] for c in calls) == [
        "media_player.attic",
        "media_player.basement_sonos",
        "media_player.kitchen_hub",
    ]


async def test_agent_policy_still_applies_to_every_speaker_on_a_floor(
    hass, floors, house, hass_admin_user
) -> None:
    data = hass.data[DOMAIN]
    data.agent_policies = AgentPolicies(
        {
            hass_admin_user.id: {
                "operations": ["send"],
                "areas": [house["kitchen"]],
            }
        }
    )
    with pytest.raises(Problem, match="agent_policy_denied"):
        await prepare_for(hass, data, hass_admin_user.id, "floor:upstairs")
    await prepare_for(hass, data, hass_admin_user.id, house["kitchen"])


async def prepare_for(hass, data, user_id, target):
    return prepare_call(
        hass,
        data,
        CallRequest(
            targets=[target],
            people=None,
            media_id="http://x/a.mp3",
            kind="media",
            priority="normal",
            volume=None,
            caller_user_id=user_id,
        ),
    )


async def test_communicate_preview_and_send_accept_floors(hass, floors, hass_admin_user) -> None:
    context = Context(user_id=hass_admin_user.id)
    preview = await execute(hass, "preview", {"floors": ["downstairs"]}, context)
    assert preview["endpoints"] == ["media_player.basement_sonos"]
    # An unknown floor stays a failed target; it must not fall through to every speaker.
    missing = await execute(hass, "preview", {"floors": ["nope"]}, context)
    assert missing.get("endpoints", []) == []
    assert missing["ok"] is False


async def test_restricted_account_only_discovers_floors_it_fully_controls(
    hass, floors, house, hass_admin_user
) -> None:
    data = hass.data[DOMAIN]
    owner = hass_admin_user.id
    context = Context(user_id=owner)
    everything = await execute(hass, "discover", {}, context)
    assert [f["name"] for f in everything["floors"]] == ["Upstairs"]
    # The kitchen alone is not all of Upstairs, so the floor and its speakers stay hidden.
    data.agent_policies = AgentPolicies({owner: {"areas": [house["kitchen"]]}})
    limited = await execute(hass, "discover", {}, context)
    assert limited["floors"] == []
    assert "Attic" not in str(limited["rooms"])
    # One speaker in each room is still not the whole floor: Kitchen Hub stays denied.
    data.agent_policies = AgentPolicies(
        {owner: {"entities": ["media_player.kitchen_sonos", "media_player.attic"]}}
    )
    partial = await execute(hass, "discover", {}, context)
    assert partial["floors"] == []
    data.agent_policies = AgentPolicies(
        {
            owner: {
                "entities": [
                    "media_player.kitchen_sonos",
                    "media_player.kitchen_hub",
                    "media_player.attic",
                ]
            }
        }
    )
    complete = await execute(hass, "discover", {}, context)
    assert [f["name"] for f in complete["floors"]] == ["Upstairs"]


async def test_household_profile_can_target_a_floor_and_a_room(hass, floors) -> None:
    data = hass.data[DOMAIN]
    data.household_profiles = validate_profiles(
        {
            "upstairs": {
                "name": "Upstairs",
                "defaults": {"floors": ["upstairs"], "areas": ["Basement"], "message": "Hi"},
            },
            "floor_only": {
                "name": "Floor",
                "defaults": {"floors": ["downstairs"], "message": "Hi"},
            },
        }
    )
    context = Context()
    preview = await execute(hass, "preview", {"profile": "upstairs"}, context)
    assert sorted(preview["endpoints"]) == [
        "media_player.attic",
        "media_player.basement_sonos",
        "media_player.kitchen_hub",
        "media_player.kitchen_sonos",
    ]
    # An explicit floor replaces the profile's destinations, as an explicit room does.
    override = await execute(
        hass, "preview", {"profile": "upstairs", "floors": ["downstairs"]}, context
    )
    assert override["endpoints"] == ["media_player.basement_sonos"]
    found = await execute(hass, "discover", {}, context)
    assert {p["id"] for p in found["profiles"]} == {"upstairs", "floor_only"}


async def test_rooms_view_offers_only_useful_floors(hass, floors, hass_client, house) -> None:
    client = await hass_client()
    body = await (await client.get("/api/roomcall/rooms")).json()
    # Downstairs has one speaker room, which the room chip already covers.
    assert [f["name"] for f in body["floors"]] == ["Upstairs"]
    assert set(body["floors"][0]["rooms"]) == {house["kitchen"], "attic"}
    assert body["floors"][0]["id"] == f"floor:{floors['upstairs']}"
    ar.async_get(hass).async_update(house["basement"], floor_id=floors["upstairs"])
    body = await (await client.get("/api/roomcall/rooms")).json()
    # One floor holding every room would repeat Everywhere.
    assert body["floors"] == []


async def test_group_names_for_notifications(hass, floors) -> None:
    lr.async_get(hass).async_create("Bedtime")
    names = group_names(hass)
    assert names[f"floor:{floors['upstairs']}"] == "Upstairs"
    assert names["floor:Upstairs"] == "Upstairs"
    assert names["label:Bedtime"] == "Bedtime"
