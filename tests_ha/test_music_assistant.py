"""Conservative MA discovery, physical deduplication and MA-owned announcements."""

from __future__ import annotations

import asyncio
import itertools
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from custom_components.roomcall import core
from custom_components.roomcall.calls import CallRequest, place_call, place_recorded_call
from custom_components.roomcall.capabilities import deduplicate, default_destination, physical_key
from custom_components.roomcall.communication import execute
from custom_components.roomcall.config_flow import suggested_speakers, validate_communication
from custom_components.roomcall.onboarding import current_entry
from custom_components.roomcall.playback import related
from custom_components.roomcall.schema import vol
from homeassistant.components.media_player import MediaPlayerEntityFeature as F
from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError, ServiceNotFound
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service
from tests_ha.test_restore import _roomcall


def player(hass, area, platform="music_assistant", name="ma", mac=None, serial=None):
    entry = MockConfigEntry(domain=platform)
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(platform, name)},
        connections={("mac", mac)} if mac else set(),
        manufacturer="Example",
        model="Speaker",
        serial_number=serial,
    )
    registry = er.async_get(hass)
    entity = registry.async_get_or_create(
        "media_player",
        platform,
        name,
        device_id=device.id,
        suggested_object_id=name,
        original_name=name,
    )
    registry.async_update_entity(entity.entity_id, area_id=area)
    hass.states.async_set(
        entity.entity_id,
        "idle",
        {
            "supported_features": F.PLAY_MEDIA | F.MEDIA_ANNOUNCE,
        },
    )
    return entity.entity_id


async def setup_call(hass, user, **request):
    return await execute(hass, "setup", request, Context(user_id=user.id))


async def test_unknown_ma_player_is_visible_but_requires_explicit_selection(
    hass, house, hass_admin_user
):
    area = ar.async_get(hass).async_create("MA Room").id
    ma = player(hass, area)
    await async_setup_component(hass, "roomcall", {})
    assert not default_destination(hass, ma, {})
    assert ma not in suggested_speakers(hass)
    result = await setup_call(hass, hass_admin_user, action="discover")
    discovery = result["discovery"]
    endpoint = next(e for e in discovery["endpoints"] if e["entity_id"] == ma)
    assert endpoint["eligible"] and not endpoint["selected"]
    assert ma not in discovery["recommended"]["speakers"]
    # A person can opt in even without manufacturer/model/name-based identity guesses.
    planned = await setup_call(
        hass, hass_admin_user, action="plan", media_only=True, settings={"speakers": [ma]}
    )
    assert planned["ok"] and planned["plan"]["settings"]["speakers"] == [ma]
    registry = er.async_get(hass)
    no_device = registry.async_get_or_create("media_player", "music_assistant", "unidentified")
    assert not default_destination(hass, no_device.entity_id, {})
    assert default_destination(hass, ma, {ma: "explicit-physical-speaker"})


@pytest.mark.parametrize("identity", ["mac", "serial", "alias"])
async def test_ma_only_player_with_verified_identity_is_recommended(
    hass, house, hass_admin_user, identity
):
    area = ar.async_get(hass).async_create("MA Only").id
    ma = player(
        hass,
        area,
        mac="aa:bb:cc:dd:ee:01" if identity == "mac" else None,
        serial="PHYSICAL1234" if identity == "serial" else None,
    )
    aliases = {ma: "known-speaker"} if identity == "alias" else {}
    current = {"communication": {"aliases": aliases}}
    assert default_destination(hass, ma, aliases)
    assert ma in suggested_speakers(hass, current)
    await _roomcall(hass, [ma])
    entry = current_entry(hass)
    hass.config_entries.async_update_entry(entry, options=current)
    await hass.async_block_till_done()
    found = (await setup_call(hass, hass_admin_user, action="discover"))["discovery"]
    assert ma in found["recommended"]["speakers"]
    assert ma not in found["duplicates"]


async def test_shared_registry_device_is_evidence_even_without_a_mac(hass, house):
    area = ar.async_get(hass).async_create("Shared Device").id
    native = player(hass, area, "sonos", "native")
    ma = player(hass, area)
    registry = er.async_get(hass)
    registry.async_update_entity(ma, device_id=registry.async_get(native).device_id)
    assert default_destination(hass, ma, {})
    assert physical_key(hass, ma, {}) == physical_key(hass, native, {})
    assert deduplicate(hass, [ma, native], {})[0] == [native]
    assert ma not in suggested_speakers(hass)


@pytest.mark.parametrize("order", ["native_first", "ma_first"])
async def test_proven_pair_defaults_to_native_and_room_preference_overrides(
    hass, house, hass_admin_user, order
):
    area = ar.async_get(hass).async_create("Known Pair").id
    native = player(hass, area, "sonos", "native", mac="aa:bb:cc:dd:ee:02")
    ma = player(hass, area, mac="aa:bb:cc:dd:ee:02")
    members = [native, ma] if order == "native_first" else [ma, native]
    selected, skipped = deduplicate(hass, members, {})
    assert selected == [native] and skipped == {ma: f"duplicate:{native}"}
    assert deduplicate(hass, members, {}, [area])[0] == [ma]
    assert deduplicate(hass, members, {}, [house["kitchen"]])[0] == [native]
    assert native in suggested_speakers(hass) and ma not in suggested_speakers(hass)
    await _roomcall(hass, members)
    entry = current_entry(hass)
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        flow["flow_id"],
        {
            "speakers": members,
            "communication": {"prefer_music_assistant": [area]},
        },
    )
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()
    data = hass.data["roomcall"]
    assert data.prefer_music_assistant == [area]
    assert deduplicate(hass, members, {})[0] == [ma]
    assert related(hass, native) == [native]
    calls = async_mock_service(hass, "music_assistant", "play_announcement")
    native_calls = async_mock_service(hass, "media_player", "play_media")
    record = await place_call(
        hass,
        data,
        CallRequest(
            targets=[area], media_id="http://ha.local/test.mp3", kind="recording", volume=0.37
        ),
    )
    assert record.ok and record.summary()["audible"] is None
    assert len(calls) == 1 and not native_calls
    assert calls[0].data == {
        "entity_id": ma,
        "url": "http://ha.local/test.mp3",
        "use_pre_announce": False,
        "announce_volume": 37,
    }
    assert record.evidence[ma]["request_accepted"]
    # Availability still wins over provider preference; keep only one route.
    hass.states.async_set(
        ma, "unavailable", {"supported_features": F.PLAY_MEDIA | F.MEDIA_ANNOUNCE}
    )
    assert deduplicate(hass, members, {})[0] == [native]
    # The same known pair is deduplicated using a preference in a new, unapplied plan.
    hass.states.async_set(ma, "idle", {"supported_features": F.PLAY_MEDIA | F.MEDIA_ANNOUNCE})
    planned = await setup_call(
        hass,
        hass_admin_user,
        action="plan",
        media_only=True,
        settings={"speakers": members, "communication": {"prefer_music_assistant": []}},
    )
    assert planned["ok"] and planned["plan"]["settings"]["speakers"] == [native]


async def test_same_names_and_room_never_establish_identity_or_replace_other_speakers(hass, house):
    area = ar.async_get(hass).async_create("Distinct Speakers").id
    ma = player(hass, area)
    native = player(hass, area, "sonos", "native")
    for entity in [ma, native]:
        hass.states.async_set(
            entity,
            "idle",
            {
                "friendly_name": "Same speaker name",
                "supported_features": F.PLAY_MEDIA | F.MEDIA_ANNOUNCE,
            },
        )
    assert deduplicate(hass, [ma, native], {}, [area])[0] == [ma, native]
    # Explicit aliases can supply the missing identity without name inference.
    aliases = {ma: "same-physical-output", native: "same-physical-output"}
    assert deduplicate(hass, [native, ma], aliases, [area])[0] == [ma]
    other = player(hass, area, "cast", "other", mac="aa:bb:cc:dd:ee:03")
    assert deduplicate(hass, [native, ma, other], aliases, [area])[0] == [ma, other]
    with pytest.raises(vol.Invalid):
        validate_communication({"prefer_music_assistant": "not-a-list"})


@pytest.mark.parametrize("volume", [None, 0.0, 0.65])
async def test_recorded_clip_uses_ma_announcement_without_a_second_chime(
    hass, house, tts_audio, volume
):
    area = ar.async_get(hass).async_create("Recording Room").id
    ma = player(hass, area)
    await _roomcall(hass, [ma])
    data = hass.data["roomcall"]
    data.volume = volume
    calls = async_mock_service(hass, "music_assistant", "play_announcement")
    native_calls = async_mock_service(hass, "media_player", "play_media")
    record, audio, content_type = await place_recorded_call(
        hass, data, tts_audio.return_value[1], "audio/wav", targets=[area]
    )
    assert record.ok and audio and content_type == "audio/mpeg"
    assert record.summary()["audible"] is None
    assert len(calls) == 1 and not native_calls
    assert calls[0].data["url"].startswith("http://ha.local:8123/api/roomcall/clip/")
    assert calls[0].data["use_pre_announce"] is False
    if volume is None:
        assert "announce_volume" not in calls[0].data
    else:
        assert calls[0].data["announce_volume"] == round(volume * 100)


async def test_missing_ma_service_is_a_failed_request_without_native_fallback(hass, house):
    area = ar.async_get(hass).async_create("Missing MA").id
    ma = player(hass, area)
    await _roomcall(hass, [ma])
    data = hass.data["roomcall"]
    data.retry = False
    native_calls = async_mock_service(hass, "media_player", "play_media")
    record = await place_call(
        hass,
        data,
        CallRequest(targets=[area], media_id="http://ha.local/test.mp3", kind="recording"),
    )
    assert not record.ok
    assert record.summary()["failed"] == {
        "ma": str(ServiceNotFound("music_assistant", "play_announcement"))
    }
    assert record.results[0].error_code == "playback_failed"
    assert record.evidence[ma]["request_accepted"] is False
    assert record.summary()["audible"] is None and not native_calls


async def test_ma_preference_does_not_change_hdmi_receiver_deduplication(hass, house):
    from tests_ha.test_playback_routes import pair
    from tests_ha.test_restore import _player

    _area, heos, controller = pair(hass)
    cast = _player(
        hass,
        "cast",
        "tv",
        house["kitchen"],
        "Example",
        "TV",
        "idle",
        supported_features=F.PLAY_MEDIA,
    )
    await _roomcall(hass, [cast, heos])
    data = hass.data["roomcall"]
    data.playback_routes = {cast: {"controller": controller, "source": "TV"}}
    data.prefer_music_assistant = [house["kitchen"]]
    assert physical_key(hass, cast, {}) == physical_key(hass, heos, {})
    assert deduplicate(hass, [cast, heos], {})[0] == [cast]
    assert deduplicate(hass, [heos, cast], {})[0] == [heos]


@pytest.mark.parametrize(
    "native_room,ma_room", [("a", "b"), ("a", None), (None, "a"), (None, None)]
)
async def test_pair_preference_uses_one_canonical_room(hass, house, native_room, ma_room):
    areas = {"a": house["kitchen"], "b": house["basement"], None: None}
    native = player(hass, house["kitchen"], "sonos", "native")
    ma = player(hass, house["basement"])
    registry = er.async_get(hass)
    registry.async_update_entity(native, area_id=areas[native_room])
    registry.async_update_entity(ma, area_id=areas[ma_room])
    aliases = {native: "speaker", ma: "speaker"}
    for members in ([native, ma], [ma, native]):
        expected = ma if native_room == "a" or (native_room is None and ma_room == "a") else native
        assert deduplicate(hass, members, aliases, [house["kitchen"]])[0] == [expected]
        if native_room == "a" and ma_room == "b":
            assert deduplicate(hass, members, aliases, [house["basement"]])[0] == [native]


async def test_preference_is_resolved_once_for_all_routes_of_a_physical_speaker(hass, house):
    first = player(hass, house["kitchen"], "sonos", "a_native")
    second = player(hass, house["basement"], "cast", "z_native")
    ma = player(hass, house["basement"])
    aliases = dict.fromkeys([first, second, ma], "one-output")
    for members in itertools.permutations([first, second, ma]):
        assert deduplicate(hass, list(members), aliases, [house["kitchen"]])[0] == [ma]
    other_ma = player(hass, house["kitchen"], name="ma_other")
    aliases[other_ma] = "one-output"
    # Provider preference does not reorder MA-only copies of a known device.
    assert deduplicate(hass, [other_ma, ma], aliases, [house["basement"]])[0] == [other_ma]


@pytest.mark.parametrize("failure", [TimeoutError, HomeAssistantError])
async def test_ambiguous_ma_error_is_not_automatically_replayed(hass, house, failure):
    area = ar.async_get(hass).async_create("MA Timeout").id
    ma = player(hass, area)
    await _roomcall(hass, [ma])
    data = hass.data["roomcall"]
    assert data.retry  # The global native-speaker retry setting stays on.
    calls = []

    async def handler(call):
        calls.append(call)
        raise failure("Response lost after the announcement request")

    hass.services.async_register("music_assistant", "play_announcement", handler)
    native_calls = async_mock_service(hass, "media_player", "play_media")
    record = await place_call(
        hass,
        data,
        CallRequest(
            targets=[area], media_id="http://ha.local/long.mp3", kind="recording", seconds=90
        ),
    )
    assert len(calls) == 1 and not native_calls
    assert not record.ok and record.results[0].attempts == 1
    assert record.summary()["audible"] is None
    assert record.evidence[ma]["request_accepted"] is False
    if failure is TimeoutError:
        assert "Playback may have started" in record.results[0].error
        assert "did not retry" in record.results[0].error


@pytest.mark.parametrize("seconds", [None, 90, 450])
async def test_ma_response_budget_covers_long_recordings(hass, house, monkeypatch, seconds):
    area = ar.async_get(hass).async_create("Long MA Clip").id
    ma = player(hass, area)
    await _roomcall(hass, [ma])
    calls = async_mock_service(hass, "music_assistant", "play_announcement")
    original_timeout = asyncio.timeout
    budgets = []

    def observe_timeout(delay):
        budgets.append(delay)
        return original_timeout(delay)

    monkeypatch.setattr(core.asyncio, "timeout", observe_timeout)
    record = await place_call(
        hass,
        hass.data["roomcall"],
        CallRequest(
            targets=[area], media_id="http://ha.local/long.mp3", kind="recording", seconds=seconds
        ),
    )
    assert record.ok and len(calls) == 1
    # This is the budget passed to the actual service await, through the call pipeline.
    assert budgets[0] >= 300
    if seconds:
        assert budgets[0] > seconds


def test_ma_library_is_not_a_roomcall_setup_requirement():
    root = Path(__file__).parents[1] / "custom_components/roomcall"
    manifest = json.loads((root / "manifest.json").read_text())
    assert "music_assistant" not in [*manifest["dependencies"], *manifest["after_dependencies"]]
    assert not any("music-assistant" in requirement for requirement in manifest["requirements"])


async def test_completed_ma_request_releases_its_lease_without_a_second_clip_wait(
    hass, house, monkeypatch
):
    area = ar.async_get(hass).async_create("MA Lease").id
    ma = player(hass, area)
    await _roomcall(hass, [ma])
    data = hass.data["roomcall"]
    extra_wait = AsyncMock()
    monkeypatch.setattr("custom_components.roomcall.scheduler.wait_for_playback", extra_wait)
    started, completed = asyncio.Event(), asyncio.Event()

    async def handler(call):
        started.set()
        await completed.wait()

    hass.services.async_register("music_assistant", "play_announcement", handler)
    first = asyncio.create_task(
        place_call(
            hass,
            data,
            CallRequest(
                targets=[area], media_id="http://ha.local/long.mp3", kind="recording", seconds=90
            ),
        )
    )
    await started.wait()
    key = physical_key(hass, ma, {})
    assert data.scheduler.locks[key].locked()
    waiting = asyncio.create_task(data.scheduler.acquire(ma, None))
    await asyncio.sleep(0)
    assert not waiting.done()
    completed.set()
    record = await first
    assert record.ok and record.summary()["audible"] is None
    lease = await asyncio.wait_for(waiting, 0.5)
    lease.release()
    await asyncio.sleep(0)
    extra_wait.assert_not_awaited()


async def test_failed_ma_response_retains_the_conservative_lease_wait(hass, house, monkeypatch):
    area = ar.async_get(hass).async_create("Uncertain MA Lease").id
    ma = player(hass, area)
    await _roomcall(hass, [ma])
    data = hass.data["roomcall"]
    extra_wait = AsyncMock()
    monkeypatch.setattr("custom_components.roomcall.scheduler.wait_for_playback", extra_wait)

    async def handler(call):
        raise TimeoutError("Response lost")

    hass.services.async_register("music_assistant", "play_announcement", handler)
    record = await place_call(
        hass,
        data,
        CallRequest(
            targets=[area], media_id="http://ha.local/long.mp3", kind="recording", seconds=90
        ),
    )
    assert not record.ok and record.results[0].attempts == 1
    await asyncio.gather(*data.scheduler.tasks)
    extra_wait.assert_awaited_once_with(hass, ma, 90, None)
    assert record.summary()["audible"] is None
