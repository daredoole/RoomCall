"""Speakers that can't announce are put back the way they were after a call."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.components.media_player import MediaPlayerEntityFeature as F
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

DOMAIN = "roomcall"
NO_WAIT = patch("custom_components.roomcall.restore.wait_until_done", AsyncMock())


def _player(
    hass: HomeAssistant,
    platform: str,
    object_id: str,
    area_id: str,
    maker: str,
    model: str,
    state: str,
    **attrs,
) -> str:
    entry = MockConfigEntry(domain=platform)
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(platform, object_id)},
        manufacturer=maker,
        model=model,
    )
    registry = er.async_get(hass)
    ent = registry.async_get_or_create(
        "media_player",
        platform,
        object_id,
        device_id=device.id,
        suggested_object_id=object_id,
        original_name=object_id,
    )
    registry.async_update_entity(ent.entity_id, area_id=area_id)
    hass.states.async_set(ent.entity_id, state, attrs)
    return ent.entity_id


async def _roomcall(hass: HomeAssistant, speakers: list[str]) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data={"speakers": speakers})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def _record_services(hass: HomeAssistant, on_play=None) -> list[ServiceCall]:
    calls: list[ServiceCall] = []

    async def handler(call: ServiceCall) -> None:
        calls.append(call)
        if call.service == "play_media" and on_play:
            on_play(call)

    for service in ("play_media", "select_source", "volume_set", "turn_off"):
        hass.services.async_register("media_player", service, handler)
    return calls


async def _broadcast(hass: HomeAssistant, area: str) -> None:
    with NO_WAIT:
        await hass.services.async_call(
            DOMAIN, "broadcast", {"areas": [area], "media_url": "http://x/a.mp3"}, blocking=True
        )
        await hass.async_block_till_done(wait_background_tasks=True)


async def test_denon_goes_back_to_the_tv_input(hass: HomeAssistant, house):
    living = ar.async_get(hass).async_create("Living Room").id
    heos = _player(
        hass,
        "heos",
        "denon_heos",
        living,
        "Denon",
        "AVR-X1800H",
        "idle",
        supported_features=F.PLAY_MEDIA | F.VOLUME_SET,
        volume_level=0.61,
    )
    avr = _player(
        hass,
        "denonavr",
        "denon_avr",
        living,
        "Denon",
        "Denon AVR-X1800H",
        "on",
        supported_features=F.SELECT_SOURCE | F.VOLUME_SET | F.TURN_OFF,
        source="Living Room TV",
        source_list=["HEOS Music", "Living Room TV"],
        volume_level=0.61,
    )
    _player(
        hass,
        "music_assistant",
        "denon_ma",
        living,
        "Denon",
        "AVR-X1800H",
        "idle",
        supported_features=F.PLAY_MEDIA | F.SELECT_SOURCE,
        source="Queue",
        source_list=["Queue"],
    )
    await _roomcall(hass, [heos])

    def play(call: ServiceCall) -> None:  # HEOS switches the receiver to its network input
        hass.states.async_set(
            avr, "on", {**hass.states.get(avr).attributes, "source": "HEOS Music"}
        )

    calls = _record_services(hass, play)
    await _broadcast(hass, living)
    restores = [(c.service, c.data["entity_id"], c.data.get("source")) for c in calls[1:]]
    assert restores == [("select_source", avr, "Living Room TV")]


async def test_receiver_that_was_off_is_turned_back_off(hass: HomeAssistant, house):
    living = ar.async_get(hass).async_create("Living Room").id
    heos = _player(
        hass,
        "heos",
        "denon_heos",
        living,
        "Denon",
        "AVR-X1800H",
        "idle",
        supported_features=F.PLAY_MEDIA | F.VOLUME_SET,
    )
    avr = _player(
        hass,
        "denonavr",
        "denon_avr",
        living,
        "Denon",
        "Denon AVR-X1800H",
        "off",
        supported_features=F.SELECT_SOURCE | F.VOLUME_SET | F.TURN_OFF,
    )
    await _roomcall(hass, [heos])
    calls = _record_services(
        hass, lambda call: hass.states.async_set(avr, "on", hass.states.get(avr).attributes)
    )
    await _broadcast(hass, living)
    assert [(c.service, c.data["entity_id"]) for c in calls[1:]] == [("turn_off", avr)]


async def test_onn_box_reopens_the_app_that_was_playing(hass: HomeAssistant, house):
    bedroom = ar.async_get(hass).async_create("Master Bedroom").id
    cast = _player(
        hass,
        "cast",
        "bedroom_cast",
        bedroom,
        "onn",
        "onn. 4K Streaming Box",
        "playing",
        supported_features=F.PLAY_MEDIA | F.VOLUME_SET | F.TURN_OFF,
        app_name="Netflix",
    )
    atv = _player(
        hass,
        "androidtv_remote",
        "bedroom_atv",
        bedroom,
        "onn",
        "onn. 4K Streaming Box",
        "on",
        supported_features=F.PLAY_MEDIA | F.TURN_OFF,
        app_id="com.netflix.ninja",
    )
    await _roomcall(hass, [cast])

    def play(call: ServiceCall) -> None:
        if call.data["entity_id"] == cast:
            hass.states.async_set(
                atv, "on", {**hass.states.get(atv).attributes, "app_id": "com.google.cast.receiver"}
            )
            hass.states.async_set(
                cast, "idle", {**hass.states.get(cast).attributes, "app_name": "Default"}
            )

    calls = _record_services(hass, play)
    await _broadcast(hass, bedroom)
    reopen = calls[-1]
    assert reopen.service == "play_media"
    assert reopen.data["entity_id"] == atv
    assert reopen.data["media_content_type"] == "app"
    assert reopen.data["media_content_id"] == "com.netflix.ninja"


async def test_announce_speakers_are_left_alone(hass: HomeAssistant, setup_roomcall, house):
    calls = _record_services(hass)
    await _broadcast(hass, house["basement"])
    assert [c.service for c in calls] == ["play_media"]


async def test_overlapping_calls_restore_once_to_the_original_state(hass: HomeAssistant, house):
    from custom_components.roomcall.restore import Guard

    living = ar.async_get(hass).async_create("Living Room").id
    avr = _player(
        hass,
        "denonavr",
        "denon_avr",
        living,
        "Denon",
        "AVR-X1800H",
        "on",
        supported_features=F.SELECT_SOURCE,
        source="Living Room TV",
        source_list=["HEOS Music", "Living Room TV"],
    )
    calls = _record_services(hass)
    first, second = Guard(hass, avr), Guard(hass, avr)
    first.start()
    hass.states.async_set(avr, "on", {**hass.states.get(avr).attributes, "source": "HEOS Music"})
    second.start()  # must reuse the first snapshot, not the interrupted state
    with NO_WAIT:
        await first.finish(1)
        assert not calls
        await second.finish(1)
    assert [(c.service, c.data.get("source")) for c in calls] == [
        ("select_source", "Living Room TV")
    ]


async def test_live_call_restores_only_after_the_call_ends(hass: HomeAssistant, house):
    """A live call has no known length; restoring early cut Denon calls off."""
    import asyncio

    from custom_components.roomcall.restore import Guard

    living = ar.async_get(hass).async_create("Living Room").id
    avr = _player(
        hass, "denonavr", "denon_avr", living, "Denon", "AVR-X1800H", "on",
        supported_features=F.SELECT_SOURCE, source="Living Room TV",
        source_list=["HEOS Music", "Living Room TV"],
    )  # fmt: skip
    calls = _record_services(hass)
    ended = asyncio.Event()
    guard = Guard(hass, avr)
    guard.start()
    hass.states.async_set(avr, "on", {**hass.states.get(avr).attributes, "source": "HEOS Music"})
    with (
        patch("custom_components.roomcall.restore.LIVE_TAIL_SECONDS", 0),
        patch("custom_components.roomcall.restore.POLL_SECONDS", 0.01),
    ):
        task = hass.async_create_task(guard.finish(None, ended.wait))
        await asyncio.sleep(0.3)  # the caller is still talking
        assert not calls
        ended.set()
        await asyncio.wait_for(task, 15)
    assert [(c.service, c.data.get("source")) for c in calls] == [
        ("select_source", "Living Room TV")
    ]


async def test_unknown_length_waits_when_player_never_reports_playing(hass: HomeAssistant):
    from custom_components.roomcall import restore as r

    hass.states.async_set("media_player.heos", "idle")
    with (
        patch.object(r, "POLL_SECONDS", 0.01),
        patch.object(r, "UNSEEN_PLAYING_SECONDS", 0.3),
    ):
        start = hass.loop.time()
        await r.wait_until_done(hass, "media_player.heos", None)
        assert hass.loop.time() - start >= 0.3  # waited the (patched) safe time

        hass.states.async_set("media_player.heos", "playing")

        async def stop_soon():
            import asyncio

            await asyncio.sleep(0.05)
            hass.states.async_set("media_player.heos", "idle")

        hass.async_create_task(stop_soon())
        start = hass.loop.time()
        with patch.object(r, "UNKNOWN_LENGTH_SECONDS", 5):
            await r.wait_until_done(hass, "media_player.heos", None)
        # Seen playing, then stopped: returns at the 2 s minimum, not after the safe wait.
        assert hass.loop.time() - start < 2.5
