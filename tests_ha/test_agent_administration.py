"""Administration recovery, native subentry boundaries and conservative diagnosis."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from custom_components.roomcall.communication import execute
from custom_components.roomcall.protocol import SETUP_KEY
from homeassistant.core import Context
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.network import NoURLAvailableError
from homeassistant.setup import async_setup_component


async def call(hass, user, operation="announcements", **request):
    return await execute(hass, operation, request, Context(user_id=user.id))


async def create(hass, user, **request):
    return await call(
        hass,
        user,
        action="create",
        kind="scheduled",
        values={"name": "Dinner", "message": "Dinner", **request.pop("values", {})},
        **request,
    )


async def test_announcement_bootstrap_and_invalid_targets(hass, house, hass_admin_user):
    await async_setup_component(hass, "roomcall", {})
    assert (await call(hass, hass_admin_user, action="list"))["error"]["code"] == "not_ready"


async def test_missing_announcement_kind(hass, setup_roomcall, hass_admin_user):
    assert (await call(hass, hass_admin_user, action="create"))["error"][
        "code"
    ] == "invalid_request"


@pytest.mark.parametrize(
    "values,code",
    [
        ({"name": " "}, "invalid_settings"),
        ({"message": " "}, "invalid_settings"),
        ({"message": "x" * 4001}, "invalid_settings"),
        ({"time": "bad"}, "invalid_settings"),
        ({"message": "{{ broken"}, "invalid_settings"),
        ({"rooms": ["missing"]}, "no_targets"),
        ({"target_speakers": ["media_player.missing"]}, "no_targets"),
    ],
)
async def test_announcement_validation(hass, setup_roomcall, hass_admin_user, values, code):
    assert (await create(hass, hass_admin_user, values=values))["error"]["code"] == code


async def test_announcement_retries_revision_delete_and_permissions(
    hass, setup_roomcall, hass_admin_user
):
    user = hass_admin_user
    assert (await call(hass, user, action="create", kind="other"))["error"][
        "code"
    ] == "invalid_request"
    with patch("custom_components.roomcall.communication.permitted", AsyncMock(return_value=False)):
        result = await create(
            hass, user, values={"target_speakers": ["media_player.kitchen_sonos"]}
        )
        assert result["error"]["code"] == "forbidden"
    created = await create(hass, user, idempotency_key="dinner")
    await hass.async_block_till_done()
    item = (await call(hass, user, action="list"))["announcements"][0]
    assert created["announcement"]["id"] == item["id"]
    assert (await create(hass, user, idempotency_key="dinner"))["reused"]
    assert (await create(hass, user))["reused"]  # Recovers missing idempotency storage.
    assert (await create(hass, user, idempotency_key="dinner", values={"message": "New"}))["error"][
        "code"
    ] == "idempotency_conflict"
    assert (await call(hass, user, action="update", id="missing", revision="x"))["error"][
        "code"
    ] == "not_found"
    assert (await call(hass, user, action="delete", id=item["id"], revision="wrong"))["error"][
        "code"
    ] == "configuration_conflict"
    assert (await call(hass, user, action="delete", id=item["id"], revision=item["revision"]))[
        "error"
    ]["code"] == "confirmation_required"
    with patch(
        "homeassistant.helpers.entity_registry.EntityRegistry.async_get_entity_id",
        return_value=None,
    ):
        assert (await call(hass, user, action="enable", id=item["id"], revision=item["revision"]))[
            "error"
        ]["code"] == "not_ready"
    enabled = await call(hass, user, action="enable", id=item["id"], revision=item["revision"])
    assert enabled["announcement"]["enabled"]
    changed = await call(
        hass,
        user,
        action="update",
        id=item["id"],
        revision=enabled["announcement"]["revision"],
        values={"message": "Changed"},
    )
    assert changed["ok"]
    await hass.async_block_till_done()
    retried = await create(hass, user, idempotency_key="dinner")
    assert (
        retried["announcement"]["id"] == item["id"]
        and retried["announcement"]["values"]["message"] == "Changed"
    )
    assert len(setup_roomcall.subentries) == 1
    item = (await call(hass, user, action="list"))["announcements"][0]
    assert (
        await call(
            hass, user, action="delete", id=item["id"], revision=item["revision"], confirm=True
        )
    )["ok"]
    assert (await create(hass, user, idempotency_key="dinner"))["error"]["code"] == "not_found"


async def test_native_subentry_failures_and_bounded_keys(hass, setup_roomcall, hass_admin_user):
    user = hass_admin_user
    with patch.object(
        type(hass.config_entries.subentries),
        "async_init",
        AsyncMock(return_value={"type": FlowResultType.FORM}),
    ):
        assert (await create(hass, user))["error"]["code"] == "invalid_settings"
    manager = hass.data[SETUP_KEY]
    manager.announcement_keys = {str(i): {"id": "old", "digest": "old"} for i in range(128)}
    result = await create(hass, user)
    assert result["ok"] and len(manager.announcement_keys) == 128
    reused = await create(hass, user, idempotency_key="recovered-create")
    assert reused["reused"] and len(manager.announcement_keys) == 128
    await hass.async_block_till_done()
    item = (await call(hass, user, action="list"))["announcements"][0]
    with patch.object(
        type(hass.config_entries.subentries),
        "async_configure",
        AsyncMock(return_value={"reason": "failed"}),
    ):
        assert (
            await call(
                hass,
                user,
                action="update",
                id=item["id"],
                revision=item["revision"],
                values={"message": "New"},
            )
        )["error"]["code"] == "invalid_settings"


async def test_doctor_evidence_and_planned_physical_route(hass, setup_roomcall, hass_admin_user):
    user = hass_admin_user
    data = hass.data["roomcall"]
    options = {
        "communication": {
            "aliases": {
                "media_player.kitchen_sonos": "kitchen",
                "media_player.kitchen_hub": "kitchen",
            }
        }
    }
    hass.config_entries.async_update_entry(setup_roomcall, options=options)
    await hass.async_block_till_done()
    data = hass.data["roomcall"]
    data.dnd = True
    data.sound_tests["media_player.kitchen_hub"] = {"heard": False, "at": 0}
    hass.states.async_set("media_player.kitchen_sonos", "unavailable", {"supported_features": 512})
    evidence = {
        "media_player.kitchen_hub": {"request_accepted": True, "audio_fetched_ms": 0},
        "media_player.basement_sonos": {"request_accepted": True, "playback_reported_ms": 0},
    }
    with (
        patch.object(type(data.quiet), "active", return_value=True),
        patch.object(
            type(data.history), "records", return_value=[SimpleNamespace(evidence=evidence)]
        ),
        patch("custom_components.roomcall.http.base_url", return_value="https://ha.local"),
    ):
        result = await call(hass, user, "doctor")
    assert result["audible"] is None
    assert {f["code"] for f in result["findings"]} == {
        "endpoint_unavailable",
        "dnd_blocked",
        "quiet_hours_active",
        "sound_test_silent",
        "audibility_unconfirmed",
        "choose_tts",
        "speaker_url_tls",
    }
    assert len([f for f in result["findings"] if f["code"] == "audibility_unconfirmed"]) == 1
    assert result["alternatives"] == [
        {"from": "media_player.kitchen_sonos", "to": "media_player.kitchen_hub"}
    ]
    proposal = await call(
        hass,
        user,
        "doctor",
        action="plan_route",
        speaker="media_player.kitchen_sonos",
        alternative="media_player.kitchen_hub",
    )
    assert proposal["ok"] and proposal["plan"]["state"] == "draft"
    assert "media_player.kitchen_sonos" not in proposal["plan"]["settings"]["speakers"]
    assert dict(setup_roomcall.options) == options  # Diagnosis never applies.
    assert data.dnd
    unsafe = await call(
        hass,
        user,
        "doctor",
        action="plan_route",
        speaker="media_player.kitchen_sonos",
        alternative="media_player.basement_sonos",
    )
    assert unsafe["error"]["code"] == "unsafe_repair"
    hass.states.async_remove("media_player.kitchen_sonos")
    with patch("custom_components.roomcall.http.base_url", side_effect=NoURLAvailableError):
        result = await call(hass, user, "doctor")
    assert {"endpoint_unavailable", "speaker_url_missing"} <= {
        f["code"] for f in result["findings"]
    }


async def test_doctor_without_runtime_and_missing_provider(hass, house, hass_admin_user):
    await async_setup_component(hass, "roomcall", {})
    result = await call(hass, hass_admin_user, "doctor")
    assert result["metrics"] == {} and result["audible"] is None
