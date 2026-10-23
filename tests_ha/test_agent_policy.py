"""Agent account restrictions are enforced before provider or speaker work."""

import time
from datetime import datetime
from unittest.mock import patch

import pytest
from custom_components.roomcall.agent_policy import AgentPolicies, validate
from custom_components.roomcall.communication import execute
from custom_components.roomcall.config_flow import validate_communication
from custom_components.roomcall.errors import Problem
from custom_components.roomcall.schema import vol
from homeassistant.core import Context
from pytest_homeassistant_custom_component.common import async_mock_service


async def test_scoped_account_send_preview_rate_and_revocation(
    hass, setup_roomcall, hass_admin_user, tts_audio
):
    data = hass.data["roomcall"]
    owner = hass_admin_user.id
    context = Context(user_id=owner)
    data.agent_policies = AgentPolicies(
        {owner: {"areas": ["kitchen"], "per_minute": 1, "max_volume": 0.2}}
    )
    validate_communication({"agent_policies": {owner: {"areas": ["kitchen"]}}})
    hass.states.async_set("tts.piper", "unknown")
    played = async_mock_service(hass, "media_player", "play_media")
    assert (await execute(hass, "policy", {}, context))["limits"]["max_volume"] == 0.2
    assert (await execute(hass, "catalog", {}, context))["ok"]
    from custom_components.roomcall.llm import async_get_tools
    from homeassistant.helpers import llm

    tools = async_get_tools(
        hass,
        llm.LLMContext(
            platform="test", context=context, language="en", assistant=None, device_id=None
        ),
        "roomcall",
    )
    assert {tool.name for tool in tools.tools} == {
        "roomcall__catalog",
        "roomcall__policy",
        "roomcall__discover",
        "roomcall__preview",
        "roomcall__send",
        "roomcall__status",
    }
    denied = await execute(hass, "setup", {"action": "discover"}, context)
    assert denied["error"]["code"] == "agent_policy_denied"
    found = await execute(hass, "discover", {}, context)
    assert all(e["area_id"] == "kitchen" for e in found["endpoints"])
    for fields in [
        {"areas": ["basement"]},
        {"areas": ["kitchen"], "priority": "urgent"},
        {"areas": ["kitchen"], "volume": 0.9},
    ]:
        assert not (await execute(hass, "send", {**fields, "message": "No"}, context))["ok"]
    assert not tts_audio.await_count and not played
    preview = await execute(hass, "preview", {"areas": ["kitchen"]}, context)
    assert set(preview["volumes"].values()) == {0.2}
    for i in range(2):
        result = await execute(
            hass,
            "send",
            {"areas": ["kitchen"], "message": "Hello", "idempotency_key": str(i)},
            context,
        )
        job = data.jobs.items[result["id"]]
        await job.task
    assert job.error["code"] == "agent_rate_limited" and len(played) == 2
    data.agent_policies.recent[owner][0] = time.monotonic() - 61
    data.agent_policies.consume(owner)
    data.agent_policies.grants[owner]["areas"] = []
    assert not (await execute(hass, "preview", {"areas": ["kitchen"]}, context))["ok"]
    assert not data.agent_policies.summary("unrestricted")["restricted"]


@pytest.mark.parametrize(
    "start,end,hour,allowed",
    [
        ("08:00", "20:00", 10, True),
        ("08:00", "20:00", 23, False),
        ("22:00", "06:00", 23, True),
        ("22:00", "06:00", 12, False),
    ],
)
def test_policy_hours(start, end, hour, allowed):
    policy = AgentPolicies(
        {"agent": {"entities": ["media_player.office"], "start": start, "end": end}}
    )
    with patch(
        "custom_components.roomcall.agent_policy.dt_util.now",
        return_value=datetime(2026, 10, 23, hour),
    ):
        if allowed:
            assert policy.playback(None, "agent", ["media_player.office"], "normal", 0.1) == 0.4
        else:
            with pytest.raises(Problem, match="agent_policy_denied"):
                policy.playback(None, "agent", ["media_player.office"], "normal", 0.1)


@pytest.mark.parametrize(
    "policies",
    [{str(i): {} for i in range(33)}, {"a": {"start": "08:00"}}, {"a": {"per_minute": 0}}],
)
def test_invalid_policies(policies):
    with pytest.raises(vol.Invalid):
        validate(policies)


async def test_owned_job_subscription_rechecks_permissions(
    hass, setup_roomcall, hass_ws_client, hass_admin_user
):
    data = hass.data["roomcall"]
    client = await hass_ws_client(hass)
    context = Context(user_id=hass_admin_user.id)
    job = data.jobs.submit({"areas": ["kitchen"], "media_url": "http://ha.local/a.mp3"}, context)
    await job.task
    await client.send_json({"id": 1, "type": "roomcall/job_events", "job_id": job.id})
    assert (await client.receive_json())["success"]
    hass.bus.async_fire("roomcall_job_updated", {"id": "other"})
    hass.bus.async_fire("roomcall_job_updated", {"id": job.id})
    event = await client.receive_json()
    assert event["event"]["id"] == job.id
    data.agent_policies = AgentPolicies(
        {hass_admin_user.id: {"operations": ["status"], "areas": []}}
    )
    hass.bus.async_fire("roomcall_job_updated", {"id": job.id})
    assert not (await client.receive_json())["event"]["ok"]
    data.agent_policies = AgentPolicies({hass_admin_user.id: {"operations": []}})
    hass.bus.async_fire("roomcall_job_updated", {"id": job.id})
    assert not (await client.receive_json())["event"]["ok"]
    await client.send_json({"id": 2, "type": "roomcall/job_events", "job_id": "missing"})
    assert not (await client.receive_json())["success"]
    await client.send_json({"id": 3, "type": "unsubscribe_events", "subscription": 1})
    assert (await client.receive_json())["success"]
