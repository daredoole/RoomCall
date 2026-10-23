"""Visible delivery stages never imply acoustic or human confirmation."""

from custom_components.roomcall.communication import execute
from custom_components.roomcall.messages import request_for
from homeassistant.core import Context
from pytest_homeassistant_custom_component.common import async_mock_service


async def test_timeline_tracks_preparation_dispatch_and_request_completion(hass, setup_roomcall):
    data = hass.data["roomcall"]
    hass.states.async_set("tts.piper", "unknown")
    async_mock_service(hass, "media_player", "play_media")
    result = await execute(hass, "send", {"areas": ["kitchen"], "message": "Dinner"}, Context())
    assert result["progress"][0]["stage"] == "queued"
    job = data.jobs.items[result["id"]]
    await job.task
    status = await execute(hass, "status", {"id": job.id}, Context())
    assert [s["stage"] for s in status["progress"]] == [
        "queued",
        "synthesizing",
        "composing",
        "waiting_speakers",
        "dispatching",
        "completed",
    ]
    assert all(s["elapsed_ms"] >= 0 for s in status["progress"])
    assert status["audible"] is None and status["result"]["audible"] is None
    assert not any("human" in evidence for evidence in status["result"]["evidence"].values())
    for _ in range(30):
        job.phase("queued")
    assert len(job.progress) == 16

    cached_stages = []
    await request_for(
        hass,
        data,
        {"areas": ["kitchen"], "message": "Dinner"},
        Context(),
        on_progress=cached_stages.append,
    )
    assert cached_stages == []  # Reused audio did not synthesize or compose again.
