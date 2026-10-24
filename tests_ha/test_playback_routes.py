"""An accepted URL is insufficient: prepare the audible path and restore supported state."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from custom_components.roomcall import communication, playback, restore
from custom_components.roomcall.agent_policy import AgentPolicies
from custom_components.roomcall.capabilities import deduplicate, physical_key
from custom_components.roomcall.config_flow import validate_communication
from custom_components.roomcall.errors import Problem
from custom_components.roomcall.schema import vol
from homeassistant.components.media_player import MediaPlayerEntityFeature as F
from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from tests_ha.test_restore import NO_WAIT, _broadcast, _player, _record_services, _roomcall


def pair(hass, *, state="on", source="TV", muted=False, features=None):
    area = ar.async_get(hass).async_create("AV Room").id
    heos = _player(
        hass,
        "heos",
        "heos",
        area,
        "Denon",
        "AVR-X1000",
        "idle",
        serial_number="TEST001",
        supported_features=F.PLAY_MEDIA,
    )
    native = _player(
        hass,
        "denonavr",
        "native",
        area,
        "Denon",
        "Denon AVR-X1000",
        state,
        serial_number=None,
        supported_features=features
        if features is not None
        else F.TURN_ON | F.TURN_OFF | F.SELECT_SOURCE | F.VOLUME_SET | F.VOLUME_MUTE,
        source=source,
        source_list=["TV", "HEOS Music"],
        volume_level=0.3,
        is_volume_muted=muted,
    )
    device = er.async_get(hass).async_get(native).device_id
    dr.async_get(hass).async_update_device(
        device, new_identifiers={("denonavr", "Denon AVR-X1000-TEST001")}
    )
    return area, heos, native


async def test_verified_heos_prepares_wake_source_volume_mute_and_restores(hass, house):
    area, heos, native = pair(hass, state="off", muted=True)
    await _roomcall(hass, [heos])
    data = hass.data["roomcall"]
    data.volume = 0.4
    calls = _record_services(hass)
    await _broadcast(hass, area)
    services = [c.service for c in calls]
    assert services[:5] == ["turn_on", "select_source", "volume_set", "volume_mute", "play_media"]
    assert calls[4].data["entity_id"] == heos
    assert services[-1] == "turn_off"
    state = hass.states.get(native)
    assert state.state == "off" and state.attributes["source"] == "TV"
    assert state.attributes["volume_level"] == 0.3 and state.attributes["is_volume_muted"]
    record = data.history.records()[0]
    assert record.ok and record.evidence[heos]["interruption"]["restore"] == "requests_completed"
    assert record.summary()["audible"] is None
    assert not data.scheduler.pending and not hass.data["roomcall_restore_active"]


async def test_same_model_is_not_identity_and_zones_are_not_overridden(hass, house):
    area, heos, native = pair(hass)
    other = _player(
        hass,
        "denonavr",
        "other",
        area,
        "Denon",
        "Denon AVR-X1000",
        "on",
        serial_number="OTHER",
        supported_features=F.SELECT_SOURCE,
    )
    zone = _player(
        hass,
        "denonavr",
        "receiver-Zone2",
        area,
        "Denon",
        "Denon AVR-X1000",
        "on",
        serial_number="TEST001",
    )
    await _roomcall(hass, [heos])
    assert playback.controlled_entities(hass, heos) == [heos, native]
    assert other not in playback.related(hass, heos) and zone not in playback.related(hass, heos)
    assert playback.route(hass, heos)["controller"] == native
    # Separate matching main controllers are ambiguous; no source is guessed.
    duplicate = _player(
        hass,
        "denonavr",
        "duplicate",
        area,
        "Denon",
        "Denon AVR-X1000",
        "on",
        serial_number="TEST001",
        supported_features=F.SELECT_SOURCE,
    )
    assert playback.route(hass, heos) == {"error": "playback_controller_ambiguous"}
    preview = await communication.execute(hass, "preview", {"areas": [area]}, Context())
    assert preview["error"]["code"] == "playback_controller_ambiguous"
    calls = _record_services(hass)
    await _broadcast(hass, area)
    assert not calls and not hass.data["roomcall"].history.records()[0].ok
    er.async_get(hass).async_update_entity(duplicate, disabled_by=er.RegistryEntryDisabler.USER)
    assert playback.route(hass, heos)["controller"] == native


async def test_explicit_hdmi_route_deduplicates_and_serializes_shared_output(hass, house):
    area, heos, native = pair(hass)
    cast = _player(
        hass, "cast", "tv", area, "Example", "TV", "idle", supported_features=F.PLAY_MEDIA
    )
    await _roomcall(hass, [heos, cast])
    data = hass.data["roomcall"]
    data.playback_routes = {cast: {"controller": native, "source": "TV"}}
    assert physical_key(hass, heos, {}) == physical_key(hass, cast, {})
    selected, skipped = deduplicate(hass, [cast, heos], {})
    assert selected == [cast] and skipped[heos] == f"duplicate:{cast}"
    lock = await data.scheduler.acquire(cast, None)
    waiting = asyncio.create_task(data.scheduler.acquire(heos, None))
    await asyncio.sleep(0)
    assert not waiting.done()
    lock.release()
    assert await waiting is lock
    lock.release()
    preview = await communication.execute(hass, "preview", {"areas": [area]}, Context())
    assert preview["ok"] and preview["interruptions"][heos]["controller"] == native
    data.aliases[native] = "output"
    assert physical_key(hass, cast, data.aliases) == "alias:output"
    assert playback.identities(hass, native) == {"alias:output"}


@pytest.mark.parametrize(
    "failure", ["unavailable", "no_wake", "missing_source", "exception", "unconfirmed"]
)
async def test_preparation_failure_never_dispatches_and_releases_lease(hass, house, failure):
    area, heos, native = pair(
        hass,
        state="off" if failure == "no_wake" else "on",
        features=F.SELECT_SOURCE if failure == "no_wake" else None,
    )
    await _roomcall(hass, [heos])
    state = hass.states.get(native)
    if failure == "unavailable":
        hass.states.async_set(native, "unavailable", state.attributes)
    if failure == "missing_source":
        hass.states.async_set(native, "on", {**state.attributes, "source_list": ["TV"]})
    calls = []

    async def handler(call):
        calls.append(call)
        if failure == "exception":
            raise RuntimeError("offline")

    for service in ("play_media", "select_source"):
        hass.services.async_register("media_player", service, handler)
    with patch.object(restore, "CONTROL_TIMEOUT", 0.01):
        await _broadcast(hass, area)
    record = hass.data["roomcall"].history.records()[0]
    assert not record.ok and record.results[0].error_code == "playback_prepare_failed"
    assert not any(c.service == "play_media" for c in calls)
    assert not hass.data["roomcall_restore_active"]
    assert not any(lock.locked() for lock in hass.data["roomcall"].scheduler.locks.values())


async def test_companion_permissions_exposure_and_account_scope_are_enforced(hass, house):
    area, heos, native = pair(hass)
    await _roomcall(hass, [heos])

    async def allowed(hass, entity_id, *args, **kwargs):
        return entity_id != native

    with patch.object(communication, "permitted", allowed):
        preview = await communication.execute(hass, "preview", {"areas": [area]}, Context())
        assert preview["error"]["code"] == "forbidden"
        discovery = await communication.execute(hass, "discover", {}, Context())
        assert discovery["endpoints"][0]["interruption"] == {"error": "forbidden"}
        calls = _record_services(hass)
        with pytest.raises(HomeAssistantError):
            await _broadcast(hass, area)
    assert not calls
    assert not hass.data["roomcall"].history.records()


@pytest.mark.parametrize("previous", ["playing", "paused"])
async def test_android_app_reopens_then_requests_prior_transport(hass, house, previous):
    area = ar.async_get(hass).async_create("TV Room").id
    cast = _player(
        hass, "cast", "cast", area, "Example", "Box", previous, supported_features=F.PLAY_MEDIA
    )
    android = _player(
        hass,
        "androidtv_remote",
        "remote",
        area,
        "Example",
        "Box",
        "on",
        app_id="example.video",
        supported_features=F.PLAY_MEDIA | F.PLAY | F.PAUSE,
    )
    await _roomcall(hass, [cast])

    def on_play(call):
        if call.data["entity_id"] == cast:
            hass.states.async_set(
                android,
                "on",
                {
                    **hass.states.get(android).attributes,
                    "app_id": "com.google.android.apps.mediashell",
                },
            )

    calls = _record_services(hass, on_play)
    await _broadcast(hass, area)
    assert calls[-2].data["media_content_id"] == "example.video"
    assert calls[-1].service == ("media_play" if previous == "playing" else "media_pause")
    assert calls[-1].data["entity_id"] == android


@pytest.mark.parametrize("previous", ["playing", "paused"])
async def test_cast_media_url_resumes_and_seeks_where_supported(hass, house, previous):
    area = ar.async_get(hass).async_create("Cast Room").id
    cast = _player(
        hass,
        "cast",
        "cast",
        area,
        "Example",
        "TV",
        previous,
        supported_features=F.PLAY_MEDIA | F.PLAY | F.PAUSE | F.SEEK,
        media_content_id="https://example.test/movie.mp4",
        media_content_type="video",
        media_position=123,
    )
    await _roomcall(hass, [cast])
    calls = _record_services(hass)
    await _broadcast(hass, area)
    assert calls[-2].data["media_content_id"] == "https://example.test/movie.mp4"
    assert calls[-2].data["extra"] == {"current_time": 123, "autoplay": previous == "playing"}
    assert calls[-1].service == ("media_play" if previous == "playing" else "media_pause")


async def test_user_changed_cast_session_is_not_overwritten(hass, house):
    area = ar.async_get(hass).async_create("Cast Room").id
    cast = _player(
        hass,
        "cast",
        "cast",
        area,
        "Example",
        "TV",
        "playing",
        supported_features=F.PLAY_MEDIA,
        media_content_id="https://example.test/original",
    )
    await _roomcall(hass, [cast])
    calls = _record_services(hass)
    guard = restore.Guard(hass, cast)
    guard.start()
    await guard.prepare("https://example.test/call", None)
    hass.states.async_set(
        cast,
        "playing",
        {**hass.states.get(cast).attributes, "media_content_id": "https://example.test/user"},
    )
    with NO_WAIT:
        await guard.finish(0)
    assert not calls
    assert guard.evidence["interruption"]["restore"] == "skipped_external_change_or_permission"


async def test_source_acknowledgement_waits_for_state_change_and_preserves_context(
    hass, house, hass_admin_user
):
    _area, heos, native = pair(hass)
    await _roomcall(hass, [heos])
    calls = _record_services(hass)
    guard = restore.Guard(hass, heos, owner=hass_admin_user.id)
    guard.start()

    def updated(source):
        hass.states.async_set(
            native, "on", {**hass.states.get(native).attributes, "source": source}
        )

    async def delayed(call):
        calls.append(call)
        hass.loop.call_later(0.01, updated, call.data["source"])

    hass.services.async_register("media_player", "select_source", delayed)
    await guard.prepare("https://example.test/call", None)
    assert calls[0].context.user_id == hass_admin_user.id
    await guard.abort()
    await asyncio.sleep(0.02)


def test_route_configuration_is_bounded_and_validated():
    validate_communication(
        {
            "playback_routes": {
                "media_player.tv": {"controller": "media_player.avr", "source": "HDMI 1"}
            }
        }
    )
    for invalid in [
        {"light.tv": {"controller": "media_player.avr", "source": "HDMI"}},
        {"media_player.tv": {"controller": "script.avr", "source": "HDMI"}},
        {"media_player.tv": {"controller": "media_player.avr", "source": ""}},
    ]:
        with pytest.raises(vol.Invalid):
            validate_communication({"playback_routes": invalid})


@pytest.mark.parametrize("change", ["source", "power", "removed"])
async def test_external_receiver_changes_are_respected(hass, house, change):
    _area, heos, native = pair(hass)
    await _roomcall(hass, [heos])
    calls = _record_services(hass)
    guard = restore.Guard(hass, heos)
    guard.start()
    await guard.prepare("http://example.test/call", None)
    calls.clear()
    if change == "removed":
        hass.states.async_remove(native)
    else:
        attrs = dict(hass.states.get(native).attributes)
        if change == "source":
            attrs["source"] = "Game"
        hass.states.async_set(native, "off" if change == "power" else "on", attrs)
    with NO_WAIT:
        await guard.finish(0)
    assert not calls
    assert guard.evidence["interruption"]["restore"] == "skipped_external_change_or_permission"


@pytest.mark.parametrize(
    "url",
    [
        "http://example.test/api/roomcall/live/previous.mp3",
        "http://example.test/api/tts_proxy/previous.mp3",
        "media-source://tts/provider/message",
    ],
)
async def test_previous_announcements_are_not_replayed(hass, house, url):
    area = ar.async_get(hass).async_create("Cast Room").id
    cast = _player(
        hass,
        "cast",
        "cast",
        area,
        "Example",
        "TV",
        "playing",
        supported_features=F.PLAY_MEDIA | F.PLAY,
        media_content_id=url,
        media_content_type="music",
    )
    await _roomcall(hass, [cast])
    calls = _record_services(hass)
    await _broadcast(hass, area)
    assert len(calls) == 1 and calls[0].data["media_content_id"] != url


async def test_resume_failure_is_separate_from_acceptance_and_no_exact_session_claim(hass, house):
    area = ar.async_get(hass).async_create("Cast Room").id
    cast = _player(
        hass,
        "cast",
        "cast",
        area,
        "Example",
        "TV",
        "paused",
        supported_features=F.PLAY_MEDIA,
        media_content_id="https://example.test/old.mp4",
        media_content_type="video",
    )
    await _roomcall(hass, [cast])
    calls = []

    async def handler(call):
        calls.append(call)
        if call.data["media_content_id"] == "https://example.test/old.mp4":
            raise RuntimeError("Original stream no longer available")

    hass.services.async_register("media_player", "play_media", handler)
    await _broadcast(hass, area)
    record = hass.data["roomcall"].history.records()[0]
    assert record.ok and record.summary()["audible"] is None
    assert record.evidence[cast]["interruption"]["restore"] == "failed"


async def test_restore_disabled_and_missing_state_are_bounded(hass, house):
    _area, heos, native = pair(hass)
    await _roomcall(hass, [heos])
    calls = _record_services(hass)
    guard = restore.Guard(hass, heos, restore_enabled=False)
    guard.start()
    await guard.prepare("http://example.test/call", None)
    calls.clear()
    with NO_WAIT:
        await guard.finish(0)
    assert not calls and hass.states.get(native).attributes["source"] == "HEOS Music"
    assert guard.evidence["interruption"]["restore"] == "disabled"
    missing = restore.Guard(hass, "media_player.missing")
    missing.start()
    await missing.prepare("http://example.test/call", None)
    await missing.abort()
    assert not hass.data["roomcall_restore_active"]
    failed = await restore.restore(
        hass, [restore.Snapshot("media_player.missing", None, "on", None, None, None)]
    )
    assert failed == ["media_player.missing"]


async def test_permission_revocation_during_preparation_stops_further_commands(hass, house):
    _area, heos, _native = pair(hass)
    await _roomcall(hass, [heos])
    guard = restore.Guard(hass, heos)
    guard.start()
    calls = _record_services(hass)
    with patch.object(guard, "_permitted", side_effect=[True, False, False]):
        with pytest.raises(Problem, match="forbidden"):
            await guard.prepare("http://example.test/call", None)
        await guard.abort()
    assert (
        not calls
        and guard.evidence["interruption"]["restore"] == "skipped_external_change_or_permission"
    )


async def test_real_agent_policy_and_assistant_exposure_cover_companions(
    hass, house, hass_admin_user
):
    area, heos, native = pair(hass)
    await _roomcall(hass, [heos])
    data = hass.data["roomcall"]
    context = Context(user_id=hass_admin_user.id)
    data.agent_policies = AgentPolicies({context.user_id: {"entities": [heos]}})
    denied = await communication.execute(hass, "preview", {"areas": [area]}, context)
    assert denied["error"]["code"] == "forbidden"
    data.agent_policies = AgentPolicies({context.user_id: {"entities": [heos, native]}})
    assert (await communication.execute(hass, "preview", {"areas": [area]}, context))["ok"]
    with patch.object(
        communication, "async_should_expose", side_effect=lambda h, a, e: e != native
    ):
        denied = await communication.execute(
            hass, "preview", {"areas": [area]}, context, assistant="conversation"
        )
    assert denied["error"]["code"] == "forbidden"


async def test_attributes_hidden_in_standby_are_saved_before_override(hass, house):
    _area, heos, native = pair(hass, state="off", source=None)
    await _roomcall(hass, [heos])
    hass.states.async_set(
        native,
        "off",
        {"supported_features": F.TURN_ON | F.TURN_OFF | F.SELECT_SOURCE | F.VOLUME_SET},
    )
    calls = _record_services(hass)

    async def wake(call):
        calls.append(call)
        hass.states.async_set(
            native,
            "on",
            {
                "source": "TV",
                "source_list": ["TV", "HEOS Music"],
                "volume_level": 0.25,
                "supported_features": F.TURN_OFF | F.SELECT_SOURCE | F.VOLUME_SET,
            },
        )

    hass.services.async_register("media_player", "turn_on", wake)
    guard = restore.Guard(hass, heos)
    guard.start()
    await guard.prepare("http://example.test/call", 0.4)
    await guard.abort()
    state = hass.states.get(native)
    assert state.state == "off" and state.attributes["volume_level"] == 0.25
    assert state.attributes["source"] == "TV"


async def test_restoration_observation_is_saved_for_durable_jobs(hass, house):
    area, heos, _native = pair(hass)
    await _roomcall(hass, [heos])
    _record_services(hass)
    with NO_WAIT:
        response = await communication.execute(
            hass,
            "send",
            {
                "areas": [area],
                "durable": True,
                "idempotency_key": "restore-durable",
                "media_url": "https://example.test/call.mp3",
            },
            Context(),
        )
        await hass.async_block_till_done(wait_background_tasks=True)
    data = hass.data["roomcall"]
    saved = await data.jobs.store.async_load()
    retained = next(item for item in saved["jobs"] if item["id"] == response["id"])
    assert retained["result"]["evidence"][heos]["interruption"]["restore"] == "requests_completed"


async def test_placeholder_serial_does_not_pair_unrelated_receivers(hass, house):
    _area, heos, native = pair(hass)
    devices = dr.async_get(hass)
    registry = er.async_get(hass)
    devices.async_update_device(registry.async_get(heos).device_id, serial_number="unknown")
    devices.async_update_device(
        registry.async_get(native).device_id,
        new_identifiers={("denonavr", "Denon AVR-X1000-unknown")},
    )
    await _roomcall(hass, [heos])
    assert playback.related(hass, heos) == [heos]
    assert playback.route(hass, heos) == {}


async def test_controller_resolution_is_frozen_before_asynchronous_preparation(hass, house):
    _area, heos, native = pair(hass)
    await _roomcall(hass, [heos])
    guard = restore.Guard(hass, heos)
    guard.start()
    hass.data["roomcall"].playback_routes[heos] = {
        "controller": "media_player.other",
        "source": "Other",
    }
    calls = _record_services(hass)
    await guard.prepare("http://example.test/call", None)
    assert calls[0].data["entity_id"] == native
    await guard.abort()
    assert all(c.data["entity_id"] != "media_player.other" for c in calls)


async def test_controller_created_after_snapshot_is_not_mutated(hass, house):
    _area, heos, _native = pair(hass)
    await _roomcall(hass, [heos])
    data = hass.data["roomcall"]
    data.playback_routes[heos] = {"controller": "media_player.late", "source": "HDMI"}
    guard = restore.Guard(hass, heos)
    guard.start()
    hass.states.async_set(
        "media_player.late", "on", {"source_list": ["HDMI"], "supported_features": F.SELECT_SOURCE}
    )
    calls = _record_services(hass)
    with pytest.raises(Problem, match="playback_prepare_failed"):
        await guard.prepare("http://example.test/call", None)
    await guard.abort()
    assert not calls


@pytest.mark.parametrize("expire_during_prepare", [False, True])
async def test_expired_work_does_not_dispatch_or_leave_a_busy_lease(
    hass, house, expire_during_prepare
):
    from custom_components.roomcall.core import fan_out

    _area, heos, native = pair(hass)
    await _roomcall(hass, [heos])
    calls = _record_services(hass)
    original = restore.Guard.prepare

    async def slow_prepare(self, *args):
        await original(self, *args)
        await asyncio.sleep(0.02)

    deadline = hass.loop.time() + (0.01 if expire_during_prepare else -1)
    with patch.object(restore.Guard, "prepare", slow_prepare):
        record = await fan_out(
            hass,
            [heos],
            "http://example.test/call",
            None,
            target="all",
            kind="media",
            restore=True,
            deadline=deadline,
        )
    assert not record.ok and record.results[0].error_code == "expired"
    assert not any(c.service == "play_media" for c in calls)
    assert not any(lock.locked() for lock in hass.data["roomcall"].scheduler.locks.values())
    assert hass.states.get(native).attributes["source"] == "TV"


@pytest.mark.parametrize("moment", ["before", "after_input_acknowledgement"])
async def test_permission_is_rechecked_before_any_control_and_before_playback(hass, house, moment):
    _area, heos, native = pair(hass)
    await _roomcall(hass, [heos])
    calls = _record_services(hass)
    guard = restore.Guard(hass, heos)
    guard.start()

    async def permitted(h, e, *args, **kwargs):
        return moment != "before" and h.states.get(native).attributes["source"] == "TV"

    with patch.object(communication, "permitted", permitted):
        with pytest.raises(Problem, match="forbidden"):
            await guard.prepare("http://example.test/call", None)
        await guard.abort()
    assert not any(c.service == "play_media" for c in calls)
    if moment == "before":
        assert not calls


@pytest.mark.parametrize("media_id", [None, "protected-session:opaque"])
async def test_unrecoverable_provider_sessions_are_not_invented(hass, house, media_id):
    area = ar.async_get(hass).async_create("Cast Room").id
    cast = _player(
        hass,
        "cast",
        "cast",
        area,
        "Example",
        "TV",
        "playing",
        supported_features=F.PLAY_MEDIA,
        media_content_id=media_id,
    )
    await _roomcall(hass, [cast])
    calls = _record_services(hass)
    await _broadcast(hass, area)
    assert len(calls) == 1


async def test_android_wakes_before_cast_and_returns_to_standby(hass, house):
    from custom_components.roomcall.capabilities import can_play

    area = ar.async_get(hass).async_create("TV Room").id
    cast = _player(
        hass, "cast", "cast", area, "Example", "Box", "idle", supported_features=F.PLAY_MEDIA
    )
    android = _player(
        hass,
        "androidtv_remote",
        "remote",
        area,
        "Example",
        "Box",
        "off",
        supported_features=F.PLAY_MEDIA | F.TURN_ON | F.TURN_OFF,
    )
    await _roomcall(hass, [cast])
    calls = _record_services(hass)
    assert can_play(hass, cast) and not can_play(hass, android)
    assert deduplicate(hass, [android, cast], {})[0] == [cast]
    await _broadcast(hass, area)
    assert [(c.service, c.data["entity_id"]) for c in calls] == [
        ("turn_on", android),
        ("play_media", cast),
        ("turn_off", android),
    ]


async def test_manual_android_app_change_is_not_overwritten(hass, house):
    area = ar.async_get(hass).async_create("TV Room").id
    cast = _player(
        hass, "cast", "cast", area, "Example", "Box", "playing", supported_features=F.PLAY_MEDIA
    )
    android = _player(
        hass,
        "androidtv_remote",
        "remote",
        area,
        "Example",
        "Box",
        "on",
        app_id="example.original",
        supported_features=F.PLAY_MEDIA | F.PLAY,
    )
    await _roomcall(hass, [cast])
    calls = _record_services(hass)
    guard = restore.Guard(hass, cast)
    guard.start()
    await guard.prepare("http://example.test/call", None)
    hass.states.async_set(
        android, "on", {**hass.states.get(android).attributes, "app_id": "example.new"}
    )
    with NO_WAIT:
        await guard.finish(1)
    assert not calls
    assert guard.evidence["interruption"]["restore"] == "skipped_external_change_or_permission"


async def test_cast_receiver_shell_is_not_relaunched_as_an_app(hass, house):
    area = ar.async_get(hass).async_create("TV Room").id
    cast = _player(
        hass, "cast", "cast", area, "Example", "Box", "playing", supported_features=F.PLAY_MEDIA
    )
    _player(
        hass,
        "androidtv_remote",
        "remote",
        area,
        "Example",
        "Box",
        "on",
        app_id="com.google.android.apps.mediashell",
        supported_features=F.PLAY_MEDIA | F.PLAY,
    )
    await _roomcall(hass, [cast])
    calls = _record_services(hass)
    await _broadcast(hass, area)
    assert len(calls) == 1 and calls[0].data["entity_id"] == cast


async def test_each_restore_command_rechecks_current_permissions(hass, house):
    _area, heos, native = pair(hass)
    await _roomcall(hass, [heos])
    calls = _record_services(hass)
    with (
        patch.object(communication, "permitted", AsyncMock(return_value=False)),
        pytest.raises(Problem, match="forbidden"),
    ):
        await restore._call(hass, "turn_off", native, context=Context())
    assert not calls


@pytest.mark.parametrize("with_guard", [False, True])
async def test_live_lease_uses_final_real_pcm_duration_after_capture(
    hass, house, monkeypatch, with_guard
):
    import shutil

    from custom_components.roomcall import scheduler as scheduler_module
    from custom_components.roomcall.calls import CallRequest, place_call
    from custom_components.roomcall.live import LiveStream
    from tests_ha.test_live import _tone_frames

    area, heos, _native = pair(hass)
    await _roomcall(hass, [heos])
    data = hass.data["roomcall"]
    data.restore = with_guard
    if not with_guard:
        # An unpaired Cast endpoint has no external input controls.
        heos = _player(
            hass,
            "cast",
            "unpaired",
            area,
            "Other",
            "Speaker",
            "idle",
            supported_features=F.PLAY_MEDIA | F.MEDIA_ANNOUNCE,
            serial_number="OTHER",
        )
        data.speakers = [heos]
    calls = _record_services(hass)
    stream = LiveStream(shutil.which("ffmpeg"))
    await stream.start()
    delivered = asyncio.Event()
    waiter = AsyncMock()
    # Undo the suite's generic native-duration mock to exercise the actual scheduler.
    import importlib

    original_wait = importlib.reload(scheduler_module).wait_for_playback
    monkeypatch.setattr(scheduler_module, "wait_for_playback", original_wait)
    with (
        patch.object(restore, "LIVE_TAIL_SECONDS", 0),
        patch.object(restore, "wait_until_done", waiter),
    ):
        task = asyncio.create_task(
            place_call(
                hass,
                data,
                CallRequest(
                    targets=[area],
                    media_id="http://example.test/live",
                    kind="talk",
                    live=stream,
                    on_delivered=lambda record: delivered.set(),
                ),
            )
        )
        await asyncio.wait_for(delivered.wait(), 5)
        assert any(lock.locked() for lock in data.scheduler.locks.values())
        assert not waiter.await_count
        for frame in _tone_frames(1.2):
            stream.feed(frame)
        stream.end()
        await task
        await hass.async_block_till_done(wait_background_tasks=True)
    assert stream.mp3 and len(stream.pcm) / 32000 == 1.2
    waiter.assert_awaited_once_with(hass, heos, 1.2)
    assert calls and not any(lock.locked() for lock in data.scheduler.locks.values())


@pytest.mark.parametrize("deny", [False, True])
async def test_permission_revoked_or_expiration_during_endpoint_check_releases_lease(
    hass, house, deny
):
    from custom_components.roomcall.core import fan_out

    _area, heos, _native = pair(hass)
    await _roomcall(hass, [heos])
    calls = _record_services(hass)

    async def permitted(*args, **kwargs):
        if not deny:
            await asyncio.sleep(0.02)
        return not deny

    with patch.object(communication, "permitted", permitted):
        result = await fan_out(
            hass,
            [heos],
            "http://example.test/call",
            None,
            target="all",
            kind="media",
            deadline=hass.loop.time() + 0.01,
        )
    assert not result.ok and result.results[0].error_code == ("forbidden" if deny else "expired")
    assert not calls and not any(
        lock.locked() for lock in hass.data["roomcall"].scheduler.locks.values()
    )
