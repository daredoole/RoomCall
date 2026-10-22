"""Protocol-agent benchmarks: only discoverable tools/results, no documentation lookup.

This is a deterministic agent driver, not a benchmark of an external LLM model.
Speaker/provider actions are mocked; explicit human listening answers are fixtures.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

import pytest
from custom_components.roomcall.onboarding import Setup
from custom_components.roomcall.protocol import SETUP_KEY
from homeassistant.core import Context
from homeassistant.helpers import llm
from pytest_homeassistant_custom_component.common import async_mock_service


class Agent:
    def __init__(self, hass, user):
        self.hass, self.user = hass, user
        self.requests = 0

    async def open(self):
        context = llm.LLMContext(
            platform="test",
            context=Context(user_id=self.user.id),
            language="en",
            assistant=None,
            device_id=None,
        )
        self.api = await llm.async_get_api(self.hass, "roomcall", context)
        self.contract = await self.call("catalog", {})
        assert self.contract["ok"]
        json.dumps(self.contract)

    async def call(self, operation, request):
        self.requests += 1
        result = await self.api.async_call_tool(
            llm.ToolInput(tool_name=f"roomcall__{operation}", tool_args=request)
        )
        return result.data if hasattr(result, "data") else result


async def test_agent_fresh_setup_to_human_verified_completion(
    hass, house, hass_admin_user, record_property
):
    await hass.config_entries.flow.async_init("roomcall", context={"source": "user"})
    hass.states.async_set("tts.piper", "unknown")
    played = async_mock_service(hass, "media_player", "play_media")
    agent = Agent(hass, hass_admin_user)
    await agent.open()
    assert "setup" in agent.contract["operations"]
    found = await agent.call("setup", {"action": "discover"})
    assert not found["discovery"]["questions"]
    draft = await agent.call(
        "setup", {"action": "plan", "settings": found["discovery"]["recommended"]}
    )
    plan = draft["plan"]
    assert not played and not hass.config_entries.async_entries("roomcall")
    applied = await agent.call(
        "setup", {"action": "apply", "id": plan["id"], "revision": plan["revision"]}
    )
    assert applied["ok"]
    await hass.async_block_till_done()
    checked = await agent.call("setup", {"action": "verify", "id": plan["id"]})
    assert checked["verification"]["configuration_ready"]
    confirmations = 0
    for action in checked["verification"]["next_actions"]:
        tested = await agent.call(action["operation"], action["request"])
        assert tested["ok"], tested
        await hass.data["roomcall"].jobs.items[tested["id"]].task
        # This answer comes from a person in this test scenario, never from the agent.
        confirmed = await agent.call(
            "setup",
            {
                "action": "confirm",
                "id": plan["id"],
                "speaker": action["request"]["speaker"],
                "heard": True,
            },
        )
        assert confirmed["ok"]
        confirmations += 1
    verified = await agent.call("setup", {"action": "verify", "id": plan["id"]})
    assert verified["verification"]["completed"] and verified["plan"]["completed_at"]
    assert len(played) == 3
    await agent.call("setup", {"action": "verify", "id": plan["id"]})
    assert hass.data["roomcall"].metrics.counters["setup_sessions_completed"] == 1
    for key, value in {
        "agent_tool_calls": agent.requests,
        "configuration_questions": 0,
        "human_listening_confirmations": confirmations,
        "documentation_lookups": 0,
        "setup_completed": True,
    }.items():
        record_property(key, value)


async def test_agent_resumes_interrupted_apply_and_undo(
    hass, setup_roomcall, hass_admin_user, record_property
):
    hass.states.async_set("tts.piper", "unknown")
    agent = Agent(hass, hass_admin_user)
    await agent.open()
    draft = await agent.call("setup", {"action": "plan", "settings": {"volume": 0.3}})
    plan = draft["plan"]
    original = hass.config_entries.options.async_init

    async def interrupted(*args, **kwargs):
        await original(*args, **kwargs)
        raise asyncio.CancelledError

    with (
        patch.object(type(hass.config_entries.options), "async_init", side_effect=interrupted),
        pytest.raises(asyncio.CancelledError),
    ):
        await agent.call(
            "setup", {"action": "apply", "id": plan["id"], "revision": plan["revision"]}
        )
    await hass.async_block_till_done()
    restored = Setup(hass)
    await restored.load()
    hass.data[SETUP_KEY] = restored
    resumed = Agent(hass, hass_admin_user)
    await resumed.open()
    listed = await resumed.call("setup", {"action": "list"})
    assert listed["plans"][0]["state"] == "applying"
    request = {"action": "apply", "id": plan["id"], "revision": plan["revision"]}
    assert (await resumed.call("setup", request))["plan"]["state"] == "applied"
    assert (await resumed.call("setup", request))["ok"]  # Stable retry.
    original_update = hass.config_entries.async_update_entry

    def interrupt_undo(entry, **kwargs):
        result = original_update(entry, **kwargs)
        if kwargs.get("options") == {}:
            raise asyncio.CancelledError
        return result

    with (
        patch.object(type(hass.config_entries), "async_update_entry", side_effect=interrupt_undo),
        pytest.raises(asyncio.CancelledError),
    ):
        await resumed.call(
            "setup",
            {"action": "undo", "id": plan["id"], "revision": plan["revision"], "confirm": True},
        )
    await hass.async_block_till_done()
    again = Setup(hass)
    await again.load()
    hass.data[SETUP_KEY] = again
    undone = await resumed.call(
        "setup", {"action": "undo", "id": plan["id"], "revision": plan["revision"], "confirm": True}
    )
    assert undone["plan"]["state"] == "rolled_back" and dict(setup_roomcall.options) == {}
    record_property("recovered_interruptions", 2)
    record_property("documentation_lookups", 0)
