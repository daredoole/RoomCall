"""Authorization, validation and reversible setup boundaries."""

from __future__ import annotations

import asyncio
import copy
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from custom_components.roomcall.communication import execute
from custom_components.roomcall.onboarding import fingerprint, merged
from custom_components.roomcall.protocol import SETUP_KEY, json_schema
from homeassistant.auth.const import GROUP_ID_ADMIN
from homeassistant.core import Context
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component


async def call(hass, user, operation="setup", **request):
    return await execute(hass, operation, request, Context(user_id=user.id))


async def draft(hass, user, **values):
    hass.states.async_set("tts.piper", "unknown")
    result = await call(hass, user, action="plan", **values)
    assert result["ok"], result
    return result["plan"]


async def test_admin_gate_and_bootstrap_http(hass, house, hass_client):
    await async_setup_component(hass, "roomcall", {})
    for operation, values in [
        ("setup", {"action": "discover"}),
        ("doctor", {}),
        ("announcements", {"action": "schema"}),
    ]:
        result = await execute(hass, operation, values, Context())
        assert result["error"]["code"] == "admin_required"
    client = await hass_client()
    assert (await (await client.get("/api/roomcall/rooms")).json())["configured"] is False
    assert (await (await client.get("/api/roomcall/history")).json())["calls"] == []
    hass.data.pop(SETUP_KEY)
    assert (await execute(hass, "setup", {"action": "discover"}, Context()))["error"][
        "code"
    ] == "not_ready"


async def test_discovery_ambiguity_disabled_and_permissions(hass, setup_roomcall, hass_admin_user):
    registry = er.async_get(hass)
    registry.async_update_entity(
        "media_player.kitchen_sonos", disabled_by=er.RegistryEntryDisabler.USER
    )
    hass.states.async_set("tts.first", "unknown")
    hass.states.async_set("tts.second", "unknown")
    with patch("custom_components.roomcall.communication.permitted", AsyncMock(return_value=False)):
        found = await call(hass, hass_admin_user, action="discover")
    assert found["discovery"]["endpoints"] == []
    assert {q["code"] for q in found["discovery"]["questions"]} == {"no_targets", "choose_tts"}
    found = await call(hass, hass_admin_user, action="discover")
    assert len(found["discovery"]["tts_engines"]) == 2
    result = await call(
        hass, hass_admin_user, action="plan", settings={"speakers": ["media_player.kitchen_sonos"]}
    )
    assert result["error"]["code"] == "no_targets"


async def test_removed_override_and_effective_native_fallback(
    hass, setup_roomcall, hass_admin_user
):
    hass.config_entries.async_update_entry(
        setup_roomcall, options={"volume": 0.7, "tts_engine": "tts.piper"}
    )
    await hass.async_block_till_done()
    plan = await draft(
        hass, hass_admin_user, settings={"volume": None, "tts_engine": None}, media_only=True
    )
    assert plan["settings"]["volume"] == 0.4
    assert {"field": "tts_engine", "before": "tts.piper", "after": None} in plan["changes"]


@pytest.mark.parametrize("action", ["apply", "undo"])
async def test_revision_rechecked_after_journal_write(
    hass, setup_roomcall, hass_admin_user, action
):
    user = hass_admin_user
    plan = await draft(hass, user)
    if action == "undo":
        await call(hass, user, action="apply", id=plan["id"], revision=plan["revision"])
        await hass.async_block_till_done()
    manager = hass.data[SETUP_KEY]
    original = manager.save

    async def concurrent_edit():
        await original()
        hass.config_entries.async_update_entry(setup_roomcall, options={"volume": 0.8})

    with patch.object(manager, "save", side_effect=concurrent_edit):
        result = await call(
            hass, user, action=action, id=plan["id"], revision=plan["revision"], confirm=True
        )
    assert result["error"]["code"] == "configuration_conflict"
    assert setup_roomcall.options == {"volume": 0.8}


@pytest.mark.parametrize(
    "settings,code",
    [
        ({"unknown": True}, "invalid_settings"),
        ({"speakers": []}, "no_targets"),
        ({"speakers": ["media_player.missing"]}, "no_targets"),
        ({"volume": 2}, "invalid_settings"),
        ({"communication": {"aliases": {"bad": "x"}}}, "invalid_settings"),
    ],
)
async def test_plan_validation(hass, setup_roomcall, hass_admin_user, settings, code):
    result = await call(hass, hass_admin_user, action="plan", settings=settings)
    assert result["error"]["code"] == code


async def test_plan_requires_provider_or_explicit_media_only(hass, setup_roomcall, hass_admin_user):
    result = await call(hass, hass_admin_user, action="plan")
    plan = result["plan"]
    assert plan["questions"][0]["field"] == "tts_engine"
    result = await call(
        hass, hass_admin_user, action="apply", id=plan["id"], revision=plan["revision"]
    )
    assert result["error"]["code"] == "choose_tts"
    plan = (await call(hass, hass_admin_user, action="plan", media_only=True))["plan"]
    assert not plan["questions"]
    assert (
        await call(hass, hass_admin_user, action="apply", id=plan["id"], revision=plan["revision"])
    )["ok"]
    await hass.async_block_till_done()
    result = await call(hass, hass_admin_user, action="verify", id=plan["id"])
    assert result["verification"]["tts_ready"]


async def test_plan_owner_expiry_capacity_and_merge(hass, setup_roomcall, hass_admin_user):
    user = hass_admin_user
    plan = await draft(hass, user)
    manager = hass.data[SETUP_KEY]
    assert merged({"a": {"b": 1}, "c": 2}, {"a": {"b": None}, "c": None}) == {"a": {}}
    assert fingerprint({"a": 1}) == fingerprint({"a": 1})
    other = await hass.auth.async_create_user("Other", group_ids=[GROUP_ID_ADMIN])
    assert (await call(hass, other, action="get", id=plan["id"]))["error"]["code"] == "not_found"
    assert (await call(hass, other, action="list"))["plans"] == []
    manager.plans[plan["id"]]["expires"] = 0
    assert (await call(hass, user, action="get", id=plan["id"]))["error"]["code"] == "plan_expired"
    manager.plans = {
        str(i): {
            **copy.deepcopy(plan),
            "id": str(i),
            "state": "applied",
            "expires": time.time() + 1000,
        }
        for i in range(32)
    }
    assert (await call(hass, user, action="plan"))["error"]["code"] == "queue_full"
    manager.plans["0"]["state"] = "draft"
    assert (await call(hass, user, action="plan"))["ok"]
    assert "0" not in manager.plans and len(manager.plans) == 32


async def test_permission_recheck_and_revisions(hass, setup_roomcall, hass_admin_user):
    user = hass_admin_user
    with patch("custom_components.roomcall.communication.permitted", AsyncMock(return_value=False)):
        result = await call(
            hass, user, action="plan", settings={"speakers": ["media_player.kitchen_sonos"]}
        )
        assert result["error"]["code"] == "forbidden"
    plan = await draft(hass, user)
    with patch("custom_components.roomcall.communication.permitted", AsyncMock(return_value=False)):
        assert (await call(hass, user, action="apply", id=plan["id"], revision=plan["revision"]))[
            "error"
        ]["code"] == "forbidden"
    assert (await call(hass, user, action="apply", id=plan["id"], revision="wrong"))["error"][
        "code"
    ] == "configuration_conflict"
    hass.states.async_remove("tts.piper")
    assert (await call(hass, user, action="apply", id=plan["id"], revision=plan["revision"]))[
        "error"
    ]["code"] == "no_targets"


async def test_failed_native_flow_and_undo_constraints(hass, setup_roomcall, hass_admin_user):
    user = hass_admin_user
    plan = await draft(hass, user)
    with patch.object(
        type(hass.config_entries.options),
        "async_init",
        AsyncMock(return_value={"type": FlowResultType.FORM, "errors": {"base": "failed"}}),
    ):
        assert (await call(hass, user, action="apply", id=plan["id"], revision=plan["revision"]))[
            "error"
        ]["code"] == "invalid_settings"
    assert hass.data[SETUP_KEY].plans[plan["id"]]["state"] == "draft"
    assert (await call(hass, user, action="undo", id=plan["id"], revision=plan["revision"]))[
        "error"
    ]["code"] == "confirmation_required"
    assert (await call(hass, user, action="undo", id=plan["id"], revision="wrong", confirm=True))[
        "error"
    ]["code"] == "configuration_conflict"
    assert (
        await call(
            hass, user, action="undo", id=plan["id"], revision=plan["revision"], confirm=True
        )
    )["error"]["code"] == "configuration_conflict"
    await call(hass, user, action="apply", id=plan["id"], revision=plan["revision"])
    await hass.async_block_till_done()
    undone = await call(
        hass, user, action="undo", id=plan["id"], revision=plan["revision"], confirm=True
    )
    assert undone["plan"]["state"] == "rolled_back"
    assert (
        await call(
            hass, user, action="undo", id=plan["id"], revision=plan["revision"], confirm=True
        )
    )["ok"]
    assert (await call(hass, user, action="apply", id=plan["id"], revision=plan["revision"]))[
        "error"
    ]["code"] == "configuration_conflict"


async def test_unavailable_tts_never_completes_setup(hass, setup_roomcall, hass_admin_user):
    user = hass_admin_user
    plan = await draft(hass, user)
    await call(hass, user, action="apply", id=plan["id"], revision=plan["revision"])
    await hass.async_block_till_done()
    hass.states.async_set("tts.piper", "unavailable")
    found = await call(hass, user, action="discover")
    assert found["discovery"]["tts_engines"] == []
    checked = await call(hass, user, action="verify", id=plan["id"])
    assert not checked["verification"]["tts_ready"] and not checked["verification"]["completed"]
    diagnosed = await call(hass, user, "doctor")
    assert "choose_tts" in {f["code"] for f in diagnosed["findings"]}


async def test_external_confirmation_does_not_verify_a_new_plan(
    hass, setup_roomcall, hass_admin_user
):
    user = hass_admin_user
    plan = await draft(hass, user)
    await call(hass, user, action="apply", id=plan["id"], revision=plan["revision"])
    await hass.async_block_till_done()
    # A legacy test may have played before apply but been confirmed afterward.
    data = hass.data["roomcall"]
    data.sound_tests = {entity: {"heard": True, "at": time.time() + 10} for entity in data.speakers}
    checked = await call(hass, user, action="verify", id=plan["id"])
    assert checked["verification"]["configuration_ready"]
    assert not checked["verification"]["audio_confirmed"]
    assert not checked["verification"]["completed"]


def test_schema_converter_contracts():
    from custom_components.roomcall import protocol

    expected = {"type": "object", "properties": {}}
    converter = Mock(return_value=expected)
    with patch.object(protocol, "vol", SimpleNamespace(to_openapi=converter)):
        assert json_schema(object()) == expected
    with (
        patch.object(protocol, "vol", SimpleNamespace()),
        patch.dict("sys.modules", {"voluptuous_openapi": SimpleNamespace(convert=converter)}),
    ):
        assert json_schema(object()) == expected
    assert converter.call_count == 2


async def test_setup_test_validation_and_stale_readiness(hass, setup_roomcall, hass_admin_user):
    user = hass_admin_user
    plan = await draft(hass, user)
    assert (await call(hass, user, action="get", id=plan["id"]))["plan"]["state"] == "draft"
    assert (
        await call(hass, user, action="test", id=plan["id"], speaker="media_player.kitchen_sonos")
    )["error"]["code"] == "configuration_conflict"
    await call(hass, user, action="apply", id=plan["id"], revision=plan["revision"])
    await hass.async_block_till_done()
    assert (await call(hass, user, action="test", id=plan["id"], speaker="media_player.other"))[
        "error"
    ]["code"] == "no_targets"
    assert (
        await call(
            hass,
            user,
            action="confirm",
            id=plan["id"],
            speaker="media_player.kitchen_sonos",
            heard=True,
        )
    )["error"]["code"] == "not_found"
    hass.states.async_remove("tts.piper")
    hass.states.async_remove("media_player.kitchen_sonos")
    checked = await call(hass, user, action="verify", id=plan["id"])
    assert not checked["verification"]["configuration_ready"]
    assert not checked["verification"]["tts_ready"]
    assert checked["verification"]["next_actions"] == [
        {"operation": "setup", "request": {"action": "discover"}}
    ]


async def test_interrupted_apply_before_native_commit_is_retryable(
    hass, setup_roomcall, hass_admin_user
):
    user = hass_admin_user
    plan = await draft(hass, user)
    with (
        patch.object(
            type(hass.config_entries.options),
            "async_init",
            AsyncMock(side_effect=asyncio.CancelledError),
        ),
        pytest.raises(asyncio.CancelledError),
    ):
        await call(hass, user, action="apply", id=plan["id"], revision=plan["revision"])
    assert (await call(hass, user, action="apply", id=plan["id"], revision=plan["revision"]))["ok"]


async def test_new_entry_undo_preserves_announcements(hass, house, hass_admin_user):
    from custom_components.roomcall.onboarding import Setup

    await async_setup_component(hass, "roomcall", {})
    user = hass_admin_user
    plan = await draft(hass, user)
    original = hass.config_entries.flow.async_init

    async def interrupt(*args, **kwargs):
        await original(*args, **kwargs)
        raise asyncio.CancelledError

    with (
        patch.object(type(hass.config_entries.flow), "async_init", side_effect=interrupt),
        pytest.raises(asyncio.CancelledError),
    ):
        await call(hass, user, action="apply", id=plan["id"], revision=plan["revision"])
    await hass.async_block_till_done()
    assert (await call(hass, user, action="apply", id=plan["id"], revision=plan["revision"]))["ok"]
    created = await call(
        hass,
        user,
        "announcements",
        action="create",
        kind="scheduled",
        values={"name": "Keep", "message": "Keep"},
    )
    assert created["ok"]
    await hass.async_block_till_done()
    assert (
        await call(
            hass, user, action="undo", id=plan["id"], revision=plan["revision"], confirm=True
        )
    )["error"]["code"] == "configuration_conflict"
    item = (await call(hass, user, "announcements", action="list"))["announcements"][0]
    await call(
        hass,
        user,
        "announcements",
        action="delete",
        id=item["id"],
        revision=item["revision"],
        confirm=True,
    )
    await hass.async_block_till_done()
    original_remove = hass.config_entries.async_remove

    async def interrupt_remove(*args):
        await original_remove(*args)
        raise asyncio.CancelledError

    with (
        patch.object(type(hass.config_entries), "async_remove", side_effect=interrupt_remove),
        pytest.raises(asyncio.CancelledError),
    ):
        await call(
            hass, user, action="undo", id=plan["id"], revision=plan["revision"], confirm=True
        )
    resumed = Setup(hass)
    await resumed.load()
    hass.data[SETUP_KEY] = resumed
    assert (
        await call(
            hass, user, action="undo", id=plan["id"], revision=plan["revision"], confirm=True
        )
    )["plan"]["state"] == "rolled_back"
