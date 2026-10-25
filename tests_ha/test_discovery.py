"""Setup lists real audio routes and only retains genuinely recent availability."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

from custom_components.roomcall import discovery
from custom_components.roomcall.communication import execute
from custom_components.roomcall.config_flow import _main_fields, suggested_speakers
from homeassistant.core import Context, State
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util


def player(hass, name, state="idle", platform="cast", **attributes):
    ent = er.async_get(hass).async_get_or_create(
        "media_player", platform, name, suggested_object_id=name
    )
    hass.states.async_set(ent.entity_id, state, {"supported_features": 512, **attributes})
    return ent.entity_id


async def test_live_standby_recent_stale_disabled_and_restored_choices(hass, freezer):
    now = dt_util.utcnow()
    live = player(hass, "living_tv")
    standby = player(hass, "standby", "off")
    stale = player(hass, "old_tv", "unavailable")
    recent = player(hass, "recent_tv", "unavailable")
    ghost = player(hass, "restored_tv", "idle", restored=True)
    remote = player(hass, "tv_remote", platform="androidtv_remote")
    avr = player(hass, "avr_control", platform="denonavr")
    tracker = discovery.players(hass)
    tracker.seen[recent] = {"at": (now - timedelta(hours=23)).timestamp(), "features": 512}
    tracker.seen[stale] = {"at": (now - timedelta(hours=25)).timestamp(), "features": 512}
    await discovery.async_refresh(hass)
    assert discovery.speaker_choices(hass, []) == sorted([live, standby, recent])
    assert remote not in suggested_speakers(hass) and avr not in suggested_speakers(hass)
    assert set(suggested_speakers(hass)) == {live, standby, recent}
    assert not tracker.recent("media_player.missing")
    assert ghost not in tracker.seen and stale not in tracker.seen
    er.async_get(hass).async_update_entity(live, disabled_by=er.RegistryEntryDisabler.USER)
    assert not tracker.recent(live)
    # Existing selections are retained for explicit removal; no settings disappear on open.
    schema = _main_fields(hass, {"speakers": [stale]})
    field = next(value for key, value in schema.items() if str(key) == "speakers")
    assert stale in field.config["include_entities"]
    assert ghost in field.config["exclude_entities"]
    # Exactly 24 hours is included; it expires without requiring a HA state change.
    freezer.move_to(now + timedelta(hours=1))
    assert tracker.recent(recent)
    freezer.tick(1)
    assert not tracker.recent(recent)


async def test_observed_transitions_survive_restart_without_refreshing_unavailable(hass, freezer):
    tv = player(hass, "observed")
    tracker = discovery.players(hass)
    await tracker.refresh()
    old = tracker.seen[tv]["at"]
    freezer.tick(60)
    hass.states.async_set(tv, "unavailable")
    hass.states.async_set("sensor.unrelated", "on")
    await hass.async_block_till_done()
    seen = tracker.seen[tv]["at"]
    assert seen > old
    await tracker.store.async_save(tracker.seen)
    resumed = discovery.RecentPlayers(hass)
    await resumed.refresh()
    assert resumed.recent(tv) and resumed.seen[tv]["at"] == seen
    freezer.tick(60)
    hass.states.async_set(tv, "unavailable", {"supported_features": 512, "new_report": True})
    await hass.async_block_till_done()
    await resumed.refresh()
    assert resumed.seen[tv]["at"] == seen
    player(hass, "new_live")
    await hass.async_block_till_done()
    assert "media_player.new_live" in resumed.seen
    hass.states.async_remove("media_player.new_live")
    await hass.async_block_till_done()
    assert not resumed.recent("media_player.new_live")


async def test_recorder_bootstrap_uses_real_history_and_remembers_audio_capability(hass, freezer):
    tv = player(hass, "history_tv", "unavailable", supported_features=0)
    ancient = player(hass, "ancient_tv", "unavailable")
    ghost = player(hass, "history_restored", "unavailable")
    now = dt_util.utcnow()
    was_online = State(
        tv, "playing", {"supported_features": 512}, last_updated=now - timedelta(hours=2)
    )
    went_offline = State(tv, "unavailable", last_updated=now - timedelta(hours=1))
    restored = State(
        ghost,
        "idle",
        {"restored": True, "supported_features": 512},
        last_updated=now - timedelta(minutes=3),
    )
    executor = AsyncMock(return_value={tv: [was_online, went_offline], ghost: [restored]})
    hass.data["recorder_instance"] = SimpleNamespace(async_add_executor_job=executor)
    tracker = discovery.players(hass)
    await tracker.refresh()
    assert tracker.recent(tv) and tracker.audio_capable(tv)
    assert tracker.seen[tv]["at"] == went_offline.last_updated.timestamp()
    assert not tracker.recent(ancient) and not tracker.recent(ghost)
    query = executor.call_args.args[0]
    assert set(query.args[3]) == {tv, ancient, ghost}
    assert query.args[1] == now - timedelta(hours=24)
    await tracker.refresh()
    executor.assert_awaited_once()


async def test_history_failure_does_not_invent_availability_and_can_retry(hass):
    tv = player(hass, "history_error", "unavailable")
    executor = AsyncMock(side_effect=[RuntimeError("Database busy"), {}])
    hass.data["recorder_instance"] = SimpleNamespace(async_add_executor_job=executor)
    tracker = discovery.players(hass)
    await tracker.refresh()
    assert not tracker.recent(tv) and tv not in tracker.checked
    await tracker.refresh()
    assert not tracker.recent(tv) and tv in tracker.checked
    assert executor.await_count == 2


async def test_guided_setup_explains_control_only_and_retains_recent_cast(
    hass, house, hass_admin_user
):
    cast = player(hass, "google_tv")
    remote = player(hass, "google_remote", platform="androidtv_remote")
    old = player(hass, "retired_tv", "unavailable")
    tracker = discovery.players(hass)
    await tracker.refresh()
    hass.states.async_set(cast, "unavailable")
    await hass.async_block_till_done()
    assert await async_setup_component(hass, "roomcall", {})
    result = await execute(
        hass, "setup", {"action": "discover"}, Context(user_id=hass_admin_user.id)
    )
    assert result["ok"], result
    found = result["discovery"]
    endpoints = {e["entity_id"]: e for e in found["endpoints"]}
    assert old not in endpoints
    assert endpoints[cast]["eligible"] and endpoints[cast]["recently_available"]
    assert endpoints[cast]["available"] is False
    assert endpoints[remote]["control_only"] and not endpoints[remote]["eligible"]
    assert remote not in found["recommended"]["speakers"]
    assert cast in found["recommended"]["speakers"]
