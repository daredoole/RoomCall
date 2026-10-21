"""Queue admission, authorization, failure recovery and delayed presence delivery."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from custom_components.roomcall.calls import CallError
from custom_components.roomcall.communication import SCHEMAS, execute
from custom_components.roomcall.core import CallRecord, Delivery
from custom_components.roomcall.errors import Problem
from custom_components.roomcall.jobs import Job
from homeassistant.core import Context
from homeassistant.helpers import llm
from pytest_homeassistant_custom_component.common import async_mock_service


def make_job(values=None, *, owner=None, state="queued"):
    return Job(
        "job",
        owner,
        values or {"areas": ["kitchen"], "media_url": "http://clip"},
        Context(user_id=owner),
        asyncio.get_running_loop().time() + 10,
        state=state,
    )


async def test_queue_capacity_evicts_only_terminal_jobs(hass, setup_roomcall):
    jobs = hass.data["roomcall"].jobs
    for index in range(128):
        jobs.items[str(index)] = make_job()
    with pytest.raises(Problem, match="queue_full"):
        jobs.submit({"media_url": "http://clip", "areas": ["kitchen"]}, Context())
    oldest = jobs.items["0"]
    oldest.id, oldest.state = "0", "completed"
    jobs.keys[(None, "old")] = ("0", "fingerprint")
    submitted = jobs.submit({"media_url": "http://clip", "areas": ["kitchen"]}, Context())
    assert "0" not in jobs.items and (None, "old") not in jobs.keys
    await jobs.cancel(submitted.id, Context())
    await asyncio.gather(submitted.task, return_exceptions=True)


async def test_recipient_authorization_and_ack_state(hass, setup_roomcall, hass_admin_user):
    jobs = hass.data["roomcall"].jobs
    job = make_job(owner="sender")
    jobs.items[job.id] = job
    recipient = Context(user_id=hass_admin_user.id)
    with pytest.raises(Problem, match="forbidden"):
        await jobs.get(job.id, Context(user_id="unknown"), recipient=True)
    job.record = CallRecord(
        "Kitchen", "message", results=[Delivery("media_player.kitchen_sonos", "Kitchen", True, 0)]
    )
    with (
        patch("custom_components.roomcall.communication.permitted", AsyncMock(return_value=False)),
        pytest.raises(Problem, match="forbidden"),
    ):
        await jobs.get(job.id, recipient, recipient=True)
    assert await jobs.get(job.id, recipient, recipient=True) is job
    with pytest.raises(Problem, match="not_waiting"):
        await jobs.acknowledge(job.id, recipient, "yes")
    job.owner, job.state = None, "waiting_ack"
    with pytest.raises(Problem, match="forbidden"):
        await jobs.acknowledge(job.id, Context(), "yes")
    job.state = "running"
    with pytest.raises(Problem, match="already_started"):
        await jobs.cancel(job.id, Context())


@pytest.mark.parametrize(
    "failure,expected",
    [
        ("partial", "partial"),
        ("expired", "expired"),
        ("timeout", "expired"),
        ("call", "failed"),
        ("problem", "failed"),
        ("unexpected", "failed"),
    ],
)
async def test_job_failure_states(hass, setup_roomcall, failure, expected):
    data = hass.data["roomcall"]
    job = make_job()
    result = CallRecord(
        "Kitchen",
        "media",
        results=[
            Delivery("media_player.kitchen_sonos", "Kitchen", False, 0, "expired", 1, "expired")
        ],
    )
    if failure == "partial":
        result.results.append(Delivery("media_player.kitchen_hub", "Hub", True, 0))
        result.results[0].error_code = "playback_failed"
    error = {
        "timeout": TimeoutError(),
        "call": CallError("blocked", code="dnd_blocked"),
        "problem": Problem("forbidden"),
        "unexpected": RuntimeError("provider offline"),
    }.get(failure)
    with patch(
        "custom_components.roomcall.jobs.send", AsyncMock(return_value=result, side_effect=error)
    ):
        await data.jobs._run(job)
    assert job.state == expected and job.error
    assert not job.record or not job.record.requires_ack


async def test_deferred_presence_waits_for_a_fresh_state_event(hass, setup_roomcall):
    data = hass.data["roomcall"]
    hass.states.async_set("person.alex", "unknown")
    job = data.jobs.submit(
        {"people": ["person.alex"], "defer_until_present": True, "media_url": "http://clip"},
        Context(),
    )
    await hass.async_block_till_done()
    assert job.state == "queued"
    played = async_mock_service(hass, "media_player", "play_media")
    hass.states.async_set("person.alex", "Kitchen")
    await asyncio.wait_for(job.task, 3)
    assert job.state == "completed" and len(played) == 2
    await data.jobs._fallback(make_job())  # Fallback remains opt-in.


async def test_ask_provider_failure_is_expired(hass, setup_roomcall):
    data = hass.data["roomcall"]
    job = make_job({"satellites": ["assist_satellite.kitchen"], "message": "Ready?"})
    with patch.object(
        type(hass.services), "async_call", AsyncMock(side_effect=RuntimeError("offline"))
    ):
        await data.jobs._run(job)
    assert job.state == "expired" and job.responses[0]["error"] == "provider_failed"


@pytest.mark.parametrize(
    "operation,values",
    [
        ("send", {"people": ["person.alex"], "defer_until_present": True, "message": "x"}),
        ("ask", {"satellites": ["assist_satellite.kitchen"], "message": "x"}),
        ("sound_test", {"speaker": "media_player.kitchen_sonos"}),
        ("adapter", {"adapter": "door", "action": "call"}),
    ],
)
async def test_permission_checks_cover_all_command_routes(hass, setup_roomcall, operation, values):
    data = hass.data["roomcall"]
    data.adapters = {"door": {"script": "script.door", "kind": "sip"}}
    hass.states.async_set("assist_satellite.kitchen", "idle", {"supported_features": 2})
    async_mock_service(hass, "assist_satellite", "ask_question")
    with patch("custom_components.roomcall.communication.permitted", AsyncMock(return_value=False)):
        result = await execute(hass, operation, values, Context())
    assert result["error"]["code"] == "forbidden"


async def test_pending_status_rechecks_exposure_and_command_cancel(hass, setup_roomcall):
    data = hass.data["roomcall"]
    job = make_job()
    data.jobs.items[job.id] = job
    with patch("custom_components.roomcall.communication.permitted", AsyncMock(return_value=False)):
        result = await execute(hass, "status", {"id": job.id}, Context(), assistant="conversation")
        assert result["error"]["code"] == "forbidden"
    result = await execute(hass, "cancel", {"id": job.id}, Context())
    assert result["state"] == "cancelled"


async def test_sound_test_confirmation_waits_and_records_heard(hass, setup_roomcall):
    data = hass.data["roomcall"]
    async_mock_service(hass, "media_player", "play_media")
    data.tests = {str(i): ("media_player.kitchen_sonos", 0, None) for i in range(128)}
    result = await execute(hass, "sound_test", {"speaker": "media_player.kitchen_sonos"}, Context())
    assert len(data.tests) == 128 and "0" not in data.tests
    early = await execute(hass, "confirm_test", {"id": result["id"], "heard": True}, Context())
    assert early["error"]["code"] == "not_ready"
    await data.jobs.items[result["id"]].task
    confirmed = await execute(hass, "confirm_test", {"id": result["id"], "heard": True}, Context())
    assert (
        confirmed["heard"] and data.metrics.summary()["latency"]["setup_confirmation"]["count"] == 1
    )


async def test_mailbox_commands_audio_replay_and_ownership(hass, setup_roomcall):
    data = hass.data["roomcall"]
    data.voicemail_enabled, data.mailbox.enabled = True, True
    async_mock_service(hass, "media_player", "play_media")
    message = await data.mailbox.add(None, "message", "kitchen", b"mp3")
    listed = await execute(hass, "voicemail", {}, Context())
    assert listed["messages"][0]["id"] == message
    result = await execute(hass, "voicemail", {"action": "replay", "id": message}, Context())
    await data.jobs.items[result["id"]].task
    assert "media_url" in data.jobs.items[result["id"]].values
    assert (await execute(hass, "voicemail", {"action": "delete", "id": "missing"}, Context()))[
        "error"
    ]["code"] == "not_found"


async def test_schema_dispatch_invariant(hass, setup_roomcall):
    # Guard against a newly registered schema without a corresponding handler.
    from custom_components.roomcall.schema import vol

    with patch.dict(SCHEMAS, {"unimplemented": vol.Schema({})}):
        result = await execute(hass, "unimplemented", {}, Context())
    assert result["error"]["code"] == "invalid_request"


async def test_legacy_tool_result_contract(hass, setup_roomcall):
    from custom_components.roomcall.llm import CommunicationTool

    context = llm.LLMContext(
        platform="test", context=Context(), language="en", assistant=None, device_id=None
    )
    with patch.object(llm, "ToolResult", None, create=True):
        result = await CommunicationTool(hass, "discover", "en").async_call(
            hass, llm.ToolInput(tool_name="roomcall__discover", tool_args={}), context
        )
    assert result["ok"] and len(result["endpoints"]) == 3


async def test_shutdown_cancels_pending_presence_job(hass, setup_roomcall):
    data = hass.data["roomcall"]
    hass.states.async_set("person.alex", "unknown")
    job = data.jobs.submit(
        {"people": ["person.alex"], "defer_until_present": True, "media_url": "http://clip"},
        Context(),
    )
    await hass.async_block_till_done()
    await data.jobs.close()
    assert job.task.cancelled() and job.state == "cancelled"


async def test_modern_tool_annotations_and_result_contract(hass, setup_roomcall):
    from custom_components.roomcall.llm import CommunicationTool

    context = llm.LLMContext(
        platform="test", context=Context(), language="en", assistant=None, device_id=None
    )
    with (
        patch.object(llm, "ToolAnnotations", SimpleNamespace, create=True),
        patch.object(llm, "ToolResult", SimpleNamespace, create=True),
    ):
        tool = CommunicationTool(hass, "discover", "en")
        assert tool.annotations.read_only and tool.annotations.idempotent
        result = await tool.async_call(
            hass, llm.ToolInput(tool_name=tool.name, tool_args={}), context
        )
    assert result.data["ok"] and not result.error
