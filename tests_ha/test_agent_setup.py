"""Agent acceptance journeys using discovered schemas and real HA configuration flows."""

from __future__ import annotations

from custom_components.roomcall.communication import execute
from homeassistant.core import Context


async def call(hass, user, operation, **request):
    return await execute(hass, operation, request, Context(user_id=user.id))


async def test_bootstrap_plan_apply_verify_undo(hass, house, hass_admin_user):
    user = hass_admin_user
    hass.states.async_set("tts.piper", "unknown")
    result = await hass.config_entries.flow.async_init("roomcall", context={"source": "user"})
    assert result["type"] == "form"
    catalog = await call(hass, user, "catalog")
    assert catalog["ok"] and catalog["api_version"] == "1.0"
    assert "settings" in catalog["operations"]["setup"]["input_schema"]["properties"]
    found = await call(hass, user, "setup", action="discover")
    assert found["ok"] and not found["discovery"]["configured"]
    draft = await call(hass, user, "setup", action="plan")
    assert draft["ok"], draft
    plan = draft["plan"]
    assert len(plan["settings"]["speakers"]) == 3
    applied = await call(
        hass, user, "setup", action="apply", id=plan["id"], revision=plan["revision"]
    )
    assert applied["ok"], applied
    await hass.async_block_till_done()
    verified = await call(hass, user, "setup", action="verify", id=plan["id"])
    assert verified["verification"]["configuration_ready"], verified
    assert not verified["verification"]["audio_confirmed"]
    undone = await call(
        hass, user, "setup", action="undo", id=plan["id"], revision=plan["revision"], confirm=True
    )
    assert undone["ok"], undone
    assert not hass.config_entries.async_entries("roomcall")


async def test_setup_preserves_nested_options_and_detects_conflict(
    hass, setup_roomcall, hass_admin_user
):
    user = hass_admin_user
    hass.states.async_set("tts.piper", "unknown")
    hass.config_entries.async_update_entry(
        setup_roomcall, options={"delivery": {"stagger_ms": 100}, "volume": 0.4}
    )
    await hass.async_block_till_done()
    draft = await call(
        hass, user, "setup", action="plan", settings={"volume": 0.3, "delivery": {"restore": False}}
    )
    assert draft["ok"], draft
    plan = draft["plan"]
    assert plan["settings"]["delivery"]["stagger_ms"] == 100
    hass.config_entries.async_update_entry(setup_roomcall, options={"volume": 0.5})
    await hass.async_block_till_done()
    result = await call(
        hass, user, "setup", action="apply", id=plan["id"], revision=plan["revision"]
    )
    assert result["error"]["code"] == "configuration_conflict"
    assert result["error"]["next_actions"][0]["operation"] == "setup"


async def test_announcements_crud_via_dedicated_tool(hass, setup_roomcall, house, hass_admin_user):
    user = hass_admin_user
    schema = await call(hass, user, "announcements", action="schema")
    assert schema["ok"], schema
    created = await call(
        hass,
        user,
        "announcements",
        action="create",
        kind="scheduled",
        values={
            "name": "Dinner",
            "time": "18:00:00",
            "days": ["mon"],
            "message": "Dinner",
            "rooms": [house["kitchen"]],
        },
        idempotency_key="dinner",
    )
    assert created["ok"], created
    await hass.async_block_till_done()
    announcement = (await call(hass, user, "announcements", action="list"))["announcements"][0]
    changed = await call(
        hass,
        user,
        "announcements",
        action="update",
        id=announcement["id"],
        revision=announcement["revision"],
        values={"message": "Dinner is ready"},
    )
    assert changed["ok"], changed
    await hass.async_block_till_done()
    announcement = (await call(hass, user, "announcements", action="list"))["announcements"][0]
    off = await call(
        hass,
        user,
        "announcements",
        action="disable",
        id=announcement["id"],
        revision=announcement["revision"],
    )
    assert off["ok"] and not off["announcement"]["enabled"], off
    deleted = await call(
        hass,
        user,
        "announcements",
        action="delete",
        id=announcement["id"],
        revision=off["announcement"]["revision"],
        confirm=True,
    )
    assert deleted["ok"], deleted
