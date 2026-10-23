"""Restart recovery preserves ownership and never replays uncertain playback."""

import asyncio
import time
from unittest.mock import AsyncMock

from custom_components.roomcall.communication import execute
from custom_components.roomcall.jobs import Jobs
from homeassistant.core import Context
from pytest_homeassistant_custom_component.common import async_mock_service


async def test_durable_completion_idempotency_and_restart(
    hass, setup_roomcall, hass_storage, hass_admin_user
):
    data = hass.data["roomcall"]
    hass.states.async_set("tts.piper", "unknown")
    played = async_mock_service(hass, "media_player", "play_media")
    context = Context(user_id=hass_admin_user.id)
    values = {
        "areas": ["kitchen"],
        "message": "Dinner",
        "durable": True,
        "idempotency_key": "dinner",
    }
    result = await execute(hass, "send", values, context)
    await data.jobs.items[result["id"]].task
    assert len(played) == 2
    store = hass_storage[f"roomcall.{data.entry_id}.jobs"]["data"]
    assert store["jobs"][0]["state"] == "completed"
    restored = Jobs(hass, data)
    await restored.load()
    data.jobs = restored
    again = await execute(hass, "send", values, context)
    assert again["id"] == result["id"] and again["result"] and len(played) == 2
    denied = await execute(hass, "status", {"id": result["id"]}, Context(user_id="other"))
    assert denied["error"]["code"] == "forbidden"
    missing = await execute(hass, "send", {**values, "idempotency_key": ""}, context)
    assert not missing["ok"]
    no_key = dict(values)
    no_key.pop("idempotency_key")
    assert (await execute(hass, "send", no_key, context))["error"]["code"] == "durable_key_required"


async def test_recovery_only_resumes_queued_unexpired_jobs(
    hass, setup_roomcall, hass_storage, monkeypatch
):
    data = hass.data["roomcall"]
    now = time.time()

    def row(key, state, age=0):
        return {
            "id": key,
            "owner": None,
            "values": {
                "areas": ["kitchen"],
                "message": "Dinner",
                "durable": True,
                "idempotency_key": key,
                "expires_in": 300,
            },
            "state": state,
            "created": now - age,
        }

    hass_storage[f"roomcall.{data.entry_id}.jobs"] = {
        "version": 1,
        "data": {
            "jobs": [
                row("queued", "queued"),
                row("running", "running"),
                row("ack", "waiting_ack"),
                row("expired", "queued", 500),
                row("old", "completed", 90000),
            ]
        },
    }
    restored = Jobs(hass, data)
    run = AsyncMock()
    monkeypatch.setattr(restored, "_run", run)
    await restored.load()
    await restored.items["queued"].task
    assert run.await_count == 1
    assert restored.items["running"].state == restored.items["ack"].state == "interrupted"
    assert restored.items["expired"].state == "expired" and "old" not in restored.items


async def test_shutdown_keeps_queued_but_not_started_playback(hass, setup_roomcall, monkeypatch):
    data = hass.data["roomcall"]
    jobs = data.jobs
    gate = asyncio.Event()
    monkeypatch.setattr(jobs, "_wait_for_presence", lambda job: gate.wait())
    queued = jobs.submit(
        {
            "people": ["person.alex"],
            "message": "Hello",
            "defer_until_present": True,
            "durable": True,
            "idempotency_key": "q",
        },
        Context(),
    )
    await asyncio.sleep(0.05)
    await jobs.close()
    assert queued.state == "queued"
    restarted = Jobs(hass, data)
    data.jobs = restarted
    monkeypatch.setattr(
        "custom_components.roomcall.jobs.send", AsyncMock(side_effect=asyncio.CancelledError)
    )
    job = restarted.submit(
        {"areas": ["kitchen"], "message": "Hello", "durable": True, "idempotency_key": "started"},
        Context(),
    )
    await asyncio.gather(job.task, return_exceptions=True)
    assert job.state == "interrupted"
    # A queued cancellation is persisted, and an explicit ack never resends audio.
    restarted.closing = False
    monkeypatch.setattr(restarted, "_wait_for_presence", lambda job: gate.wait())
    cancelled = restarted.submit(
        {
            "people": ["person.alex"],
            "defer_until_present": True,
            "durable": True,
            "idempotency_key": "cancel",
        },
        Context(),
    )
    await restarted.cancel(cancelled.id, Context())
    await restarted.close()


async def test_durable_acknowledgment_is_saved(hass, setup_roomcall, hass_admin_user):
    data = hass.data["roomcall"]
    async_mock_service(hass, "media_player", "play_media")
    context = Context(user_id=hass_admin_user.id)
    result = await execute(
        hass,
        "send",
        {
            "areas": ["kitchen"],
            "media_url": "http://ha.local/a.mp3",
            "durable": True,
            "idempotency_key": "ack",
            "require_ack": True,
        },
        context,
    )
    job = data.jobs.items[result["id"]]
    for _ in range(100):
        if job.state == "waiting_ack":
            break
        await asyncio.sleep(0.01)
    assert job.state == "waiting_ack"
    await data.jobs.acknowledge(job.id, context, "Heard it")
    await job.task
    restored = Jobs(hass, data)
    await restored.load()
    assert restored.items[job.id].state == "acknowledged"
    assert restored.items[job.id].responses[0]["text"] == "Heard it"


async def test_recovered_messages_wait_for_home_assistant_start(
    hass, setup_roomcall, hass_storage, monkeypatch
):
    from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
    from homeassistant.core import CoreState

    data = hass.data["roomcall"]
    hass_storage[f"roomcall.{data.entry_id}.jobs"] = {
        "version": 1,
        "data": {
            "jobs": [
                {
                    "id": "boot",
                    "owner": None,
                    "values": {"durable": True, "idempotency_key": "boot"},
                    "state": "queued",
                    "created": time.time(),
                }
            ]
        },
    }
    monkeypatch.setattr(hass, "state", CoreState.starting)
    restored = Jobs(hass, data)
    run = AsyncMock()
    monkeypatch.setattr(restored, "_run", run)
    await restored.load()
    assert restored.items["boot"].task is None and not run.await_count
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done()
    assert run.await_count == 1
    await restored.close()
