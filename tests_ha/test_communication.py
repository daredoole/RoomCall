"""Human and agent journeys through the real control surface, queue and evidence store."""

from __future__ import annotations

import asyncio
import time
from datetime import timedelta
from unittest.mock import patch

import pytest
from custom_components.roomcall.calls import CallRequest, place_call
from custom_components.roomcall.capabilities import deduplicate, physical_key
from custom_components.roomcall.communication import execute
from custom_components.roomcall.config_flow import validate_communication
from custom_components.roomcall.errors import Problem
from custom_components.roomcall.llm import async_get_tools
from custom_components.roomcall.localization import catalog
from custom_components.roomcall.messages import request_for
from custom_components.roomcall.policy import person_area
from custom_components.roomcall.voicemail import Mailbox
from homeassistant.core import Context, SupportsResponse
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import intent, llm
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_mock_service


async def finished(hass, job_id):
    job = hass.data["roomcall"].jobs.items[job_id]
    await asyncio.wait_for(job.task, 3)
    return job


@pytest.mark.parametrize(
    "operation,values,code",
    [
        ("unknown", {}, "invalid_request"),
        ("preview", {}, "target_required"),
        ("preview", {"areas": ["missing"]}, "no_targets"),
        ("send", {"areas": ["kitchen"]}, "content_required"),
        ("send", {"areas": ["kitchen"], "message": "x", "media_url": "x"}, "content_required"),
        ("send", {"areas": ["kitchen"], "message": "x", "voicemail": True}, "voicemail_disabled"),
        ("send", {"defer_until_present": True, "message": "x"}, "target_required"),
        ("status", {"id": "missing"}, "not_found"),
        (
            "ask",
            {"satellites": ["assist_satellite.missing"], "message": "x"},
            "satellite_unavailable",
        ),
        ("adapter", {"adapter": "missing", "action": "call"}, "adapter_unavailable"),
        ("voicemail", {}, "voicemail_disabled"),
        ("confirm_test", {"id": "missing", "heard": True}, "not_found"),
        ("sound_test", {"speaker": "media_player.missing"}, "no_targets"),
        ("send", {"volume": 2}, "invalid_request"),
    ],
)
async def test_structured_errors(hass, setup_roomcall, operation, values, code):
    result = await execute(hass, operation, values, Context())
    assert not result["ok"]
    assert result["error"]["code"] == code
    assert result["error"]["message"] != code


async def test_agent_discover_preview_send_status_and_recovery(hass, setup_roomcall, house):
    hass.states.async_set("tts.piper", "unknown")
    played = async_mock_service(hass, "media_player", "play_media")
    context = llm.LLMContext(
        platform="test", context=Context(), language="en", assistant=None, device_id=None
    )
    api = await llm.async_get_api(hass, "roomcall", context)
    assert "roomcall__send" in {tool.name for tool in api.tools}
    assert async_get_tools(hass, context, "unrelated") is None

    async def call(name, values):
        result = await api.async_call_tool(
            llm.ToolInput(tool_name=f"roomcall__{name}", tool_args=values)
        )
        return result.data if hasattr(result, "data") else result

    found = await call("discover", {})
    assert len(found["endpoints"]) == 3
    preview = await call("preview", {"areas": [house["basement"]]})
    assert preview["endpoints"] == ["media_player.basement_sonos"]
    assert not played  # A preview never invokes audio/TTS.
    data = hass.data["roomcall"]
    data.dnd = True
    blocked = await call("send", {"areas": [house["basement"]], "message": "Dinner"})
    assert blocked["error"]["code"] == "dnd_blocked"
    diag = await call("diagnose", {})
    assert diag["policy"]["dnd"] and not played
    data.dnd = False
    queued = await call(
        "send", {"areas": [house["basement"]], "message": "Dinner", "idempotency_key": "dinner"}
    )
    await finished(hass, queued["id"])
    result = await call("status", {"id": queued["id"]})
    assert result["state"] == "completed"
    assert result["result"]["evidence"]["media_player.basement_sonos"]["request_accepted"]
    assert result["audible"] is None
    repeated = await call(
        "send", {"areas": [house["basement"]], "message": "Dinner", "idempotency_key": "dinner"}
    )
    assert repeated["id"] == queued["id"] and len(played) == 1
    conflict = await call(
        "send", {"areas": [house["basement"]], "message": "Changed", "idempotency_key": "dinner"}
    )
    assert conflict["error"]["code"] == "idempotency_conflict"
    assert data.metrics.summary()["counters"]["duplicates_suppressed"] == 1


async def test_assist_reports_partial_failure_and_preserves_context(hass, setup_roomcall):
    hass.data["roomcall"].retry = False
    hass.states.async_set("tts.piper", "unknown")
    calls = []

    async def play(call):
        calls.append(call)
        if call.data["entity_id"] == "media_player.kitchen_sonos":
            raise RuntimeError("offline")

    hass.services.async_register("media_player", "play_media", play)
    response = await intent.async_handle(
        hass,
        "test",
        "RoomCallAnnounce",
        {"message": {"value": "Hello"}, "area": {"value": "kitchen"}},
        context=Context(),
    )
    speech = response.speech["plain"]["speech"]
    assert "Failed:" in speech and "Kitchen_Sonos" in speech
    assert "1 playback" in speech


async def test_permissions_and_exposure_prevent_audio(hass, setup_roomcall, hass_admin_user):
    context = Context(user_id=hass_admin_user.id)
    with patch("custom_components.roomcall.communication.async_should_expose", return_value=False):
        result = await execute(hass, "discover", {}, context, assistant="conversation")
        assert result["endpoints"] == [] and result["rooms"] == []
        result = await execute(
            hass, "send", {"areas": ["kitchen"], "message": "x"}, context, assistant="conversation"
        )
        assert result["error"]["code"] == "forbidden"
    result = await execute(hass, "preview", {"areas": ["kitchen"]}, Context(user_id="unknown"))
    assert result["error"]["code"] == "forbidden"
    async_mock_service(hass, "media_player", "play_media")
    sent = await execute(hass, "send", {"areas": ["kitchen"], "media_url": "x"}, context)
    await finished(hass, sent["id"])
    with patch("custom_components.roomcall.communication.async_should_expose", return_value=False):
        status = await execute(
            hass, "status", {"id": sent["id"]}, context, assistant="conversation"
        )
        assert status["error"]["code"] == "forbidden"


async def test_device_identity_dedup_does_not_merge_matching_models(hass, setup_roomcall):
    registry = er.async_get(hass)
    devices = dr.async_get(hass)
    first = devices.async_get_or_create(
        config_entry_id=setup_roomcall.entry_id,
        identifiers={("test", "a")},
        manufacturer="Acme",
        model="Speaker",
    )
    second = devices.async_get_or_create(
        config_entry_id=setup_roomcall.entry_id,
        identifiers={("test", "b")},
        manufacturer="Acme",
        model="Speaker",
    )
    registry.async_update_entity("media_player.kitchen_sonos", device_id=first.id)
    registry.async_update_entity("media_player.kitchen_hub", device_id=second.id)
    ids = ["media_player.kitchen_sonos", "media_player.kitchen_hub"]
    assert deduplicate(hass, ids, {})[0] == ids
    registry.async_update_entity(ids[1], device_id=first.id)
    assert len(deduplicate(hass, ids, {})[0]) == 1
    assert physical_key(hass, ids[0], {ids[0]: "one"}) == "alias:one"
    registry.async_update_entity(ids[1], device_id=second.id)
    assert len(deduplicate(hass, ids, dict.fromkeys(ids, "one"))[0]) == 1
    hass.states.async_set(ids[0], "unavailable")
    assert deduplicate(hass, ids, dict.fromkeys(ids, "one"))[0] == [ids[1]]


async def test_queue_keeps_independent_rooms_free_and_expires_waiting_message(
    hass,
    setup_roomcall,
    monkeypatch,
):
    gate = asyncio.Event()

    async def playback(hass, entity_id, seconds, until):
        if "basement" in entity_id:
            await gate.wait()

    monkeypatch.setattr("custom_components.roomcall.scheduler.wait_for_playback", playback)
    played = async_mock_service(hass, "media_player", "play_media")
    values = {"areas": ["basement"], "media_url": "http://x/a.mp3"}
    one = await execute(hass, "send", values, Context())
    await finished(hass, one["id"])
    two = hass.data["roomcall"].jobs.submit({**values, "expires_in": 0.02}, Context())
    other = await execute(hass, "send", {**values, "areas": ["kitchen"]}, Context())
    await finished(hass, other["id"])
    await finished(hass, two.id)
    assert two.state == "expired"
    assert sum(c.data["entity_id"] == "media_player.basement_sonos" for c in played) == 1
    gate.set()


async def test_queued_job_cancellation_and_owner_checks(hass, setup_roomcall, hass_admin_user):
    jobs = hass.data["roomcall"].jobs
    context = Context(user_id=hass_admin_user.id)
    lock = await hass.data["roomcall"].scheduler.acquire("media_player.basement_sonos", None)
    job = jobs.submit({"areas": ["basement"], "media_url": "x"}, context)
    denied = await execute(hass, "status", {"id": job.id}, Context(user_id="other"))
    assert denied["error"]["code"] == "forbidden"
    result = await execute(hass, "cancel", {"id": job.id}, context)
    assert result["state"] == "cancelled"
    await asyncio.gather(job.task, return_exceptions=True)
    again = await execute(hass, "cancel", {"id": job.id}, context)
    assert again["error"]["code"] == "already_started"
    lock.release()


async def test_acknowledgment_is_explicit(hass, setup_roomcall, hass_admin_user):
    context = Context(user_id=hass_admin_user.id)
    async_mock_service(hass, "media_player", "play_media")
    result = await execute(
        hass, "send", {"areas": ["basement"], "media_url": "x", "require_ack": True}, context
    )
    for _ in range(30):
        await hass.async_block_till_done()
        job = hass.data["roomcall"].jobs.items[result["id"]]
        if job.state == "waiting_ack":
            break
    assert job.state == "waiting_ack" and job.record.requires_ack
    response = await execute(hass, "acknowledge", {"id": job.id, "response": "Coming"}, context)
    assert response["state"] == "acknowledged"
    assert response["responses"][0]["user_id"] == hass_admin_user.id
    assert not job.record.requires_ack
    await finished(hass, job.id)


async def test_ack_timeout_retains_opted_in_mail_and_falls_back(hass, setup_roomcall):
    data = hass.data["roomcall"]
    data.voicemail_enabled = data.mailbox.enabled = True
    data.notify = ["notify.mobile_app_phone"]
    notified = async_mock_service(hass, "notify", "mobile_app_phone")
    async_mock_service(hass, "media_player", "play_media")
    hass.states.async_set("tts.piper", "unknown")
    job = data.jobs.submit(
        {
            "areas": ["basement"],
            "media_url": "x",
            "message": "Come here",
            "require_ack": True,
            "ack_timeout": 0.01,
            "voicemail": True,
            "fallback_notify": True,
        },
        Context(),
    )
    hass.states.async_set("tts.piper", "unknown")
    await finished(hass, job.id)
    assert job.state == "expired" and job.error["code"] == "ack_timeout", job.summary()
    assert len(data.mailbox.items) == 1
    assert notified[-1].data["message"] == "Come here"


@pytest.mark.parametrize("heard", [True, False])
async def test_guided_sound_test_records_only_human_confirmation(hass, setup_roomcall, heard):
    async_mock_service(hass, "media_player", "play_media")
    job = await execute(hass, "sound_test", {"speaker": "media_player.basement_sonos"}, Context())
    await finished(hass, job["id"])
    assert not hass.data["roomcall"].sound_tests
    confirmed = await execute(hass, "confirm_test", {"id": job["id"], "heard": heard}, Context())
    assert confirmed["heard"] is heard
    assert hass.data["roomcall"].sound_tests["media_player.basement_sonos"]["heard"] is heard


@pytest.mark.parametrize("sentence,state", [("Pizza", "answered"), ("", "expired")])
async def test_satellite_question_returns_actual_response(hass, setup_roomcall, sentence, state):
    hass.states.async_set("assist_satellite.kitchen", "idle", {"supported_features": 2})

    async def ask(call):
        return {"id": "pizza", "sentence": sentence, "slots": {}}

    hass.services.async_register(
        "assist_satellite", "ask_question", ask, supports_response=SupportsResponse.ONLY
    )
    result = await execute(
        hass,
        "ask",
        {"satellites": ["assist_satellite.kitchen"], "message": "What would you like?"},
        Context(),
    )
    job = await finished(hass, result["id"])
    assert job.state == state and job.responses[0]["sentence"] == sentence


async def test_translation_and_language_preferences_are_explicit(hass, setup_roomcall, tts_audio):
    data = hass.data["roomcall"]
    hass.states.async_set("tts.piper", "unknown")
    data.preferences = {"person.alex": {"language": "es", "room": "kitchen"}}
    request = await request_for(
        hass, data, {"people": ["person.alex"], "message": "Hello"}, Context()
    )
    assert request.language == "es" and "language=es" in tts_audio.await_args.args[1]
    with pytest.raises(Problem, match="translation_unavailable"):
        await request_for(hass, data, {"message": "Hello", "translate": True}, Context())
    data.translation_entity = "ai_task.local"

    async def translate(call):
        assert call.data["entity_id"] == "ai_task.local"
        return {"data": "Hola"}

    hass.services.async_register(
        "ai_task", "generate_data", translate, supports_response=SupportsResponse.ONLY
    )
    request = await request_for(
        hass, data, {"message": "Hello", "translate": True, "language": "es"}, Context()
    )
    assert request.message == "Hola" and "language=es" in tts_audio.await_args.args[1]
    data.preferences["person.sam"] = {"language": "nl"}
    with pytest.raises(Problem, match="mixed_languages"):
        await request_for(
            hass, data, {"people": ["person.alex", "person.sam"], "message": "Hi"}, Context()
        )


async def test_presence_confidence_and_age(hass, setup_roomcall):
    hass.states.async_set("person.alex", "kitchen", {"confidence": 0.2})
    assert person_area(hass, "person.alex", min_confidence=0.5) is None
    hass.states.async_set("person.alex", "kitchen", {"confidence": "invalid"})
    assert person_area(hass, "person.alex") is None
    hass.states.async_set("person.alex", "kitchen", {"confidence": 0.9})
    assert person_area(hass, "person.alex", min_confidence=0.5) == "kitchen"
    with patch(
        "homeassistant.util.dt.utcnow", return_value=dt_util.utcnow() + timedelta(seconds=100)
    ):
        assert person_area(hass, "person.alex", max_age=10) is None


async def test_opt_in_voicemail_survives_reload_and_respects_owner_expiry(hass, setup_roomcall):
    mailbox = Mailbox(hass, setup_roomcall.entry_id, True, 300)
    await mailbox.load()
    key = await mailbox.add("owner", "Private", "basement", b"audio")
    reloaded = Mailbox(hass, setup_roomcall.entry_id, True, 300)
    await reloaded.load()
    assert reloaded.list("owner")[0]["message"] == "Private"
    assert reloaded.list("other") == []
    assert "audio" not in reloaded.list("owner")[0]
    reloaded.items[key]["expires"] = time.time() - 1
    assert reloaded.list("owner") == []
    await reloaded.save()
    assert not reloaded.items
    await mailbox.delete(key)
    disabled = Mailbox(hass, setup_roomcall.entry_id, False, 300)
    await disabled.load()
    with pytest.raises(Problem, match="voicemail_disabled"):
        await disabled.add("owner", "x", "basement")


async def test_adapter_uses_only_configured_script(hass, setup_roomcall):
    hass.data["roomcall"].adapters = {"front_door": {"kind": "doorbell", "script": "script.answer"}}
    called = async_mock_service(hass, "script", "turn_on")
    result = await execute(
        hass, "adapter", {"adapter": "front_door", "action": "answer"}, Context()
    )
    assert result["state"] == "request_accepted"
    assert called[0].data["entity_id"] == "script.answer"
    assert called[0].data["variables"]["roomcall_action"] == "answer"


async def test_command_http_authentication_locale_and_validation(
    hass, setup_roomcall, hass_client, hass_client_no_auth
):
    anon = await hass_client_no_auth()
    assert (await anon.post("/api/roomcall/command", json={"operation": "discover"})).status == 401
    client = await hass_client()
    result = await (
        await client.post("/api/roomcall/command", json={"operation": "discover"})
    ).json()
    assert result["schema_version"] == 1
    bad = await client.post("/api/roomcall/command", data="[]")
    assert bad.status == 400
    locale = await (await client.get("/api/roomcall/locale?language=es")).json()
    assert locale["send"] == "Enviar"
    assert set(catalog("fr")) == set(catalog("unknown"))
    assert catalog("../../../../etc/passwd") == catalog("en")
    assert catalog("es/../../etc/passwd") == catalog("en")
    assert catalog("\x00") == catalog("en")


async def test_delivery_tokens_do_not_claim_audibility(hass, setup_roomcall, hass_client_no_auth):
    async_mock_service(hass, "media_player", "play_media")
    data = hass.data["roomcall"]
    clip = data.clips.add(b"audio", "audio/mpeg")
    record = await place_call(
        hass,
        data,
        CallRequest(
            targets=["basement"],
            media_id=f"http://ha.local:8123/api/roomcall/clip/{clip}.mp3",
            kind="talk",
        ),
    )
    token = next(iter(data.evidence.tokens))
    assert "audio_fetched_ms" not in record.evidence["media_player.basement_sonos"]
    data.evidence.fetched(token, "/wrong")
    anon = await hass_client_no_auth()
    response = await anon.get(f"/api/roomcall/clip/{clip}.mp3?roomcall_token={token}")
    assert response.status == 200
    assert record.evidence["media_player.basement_sonos"]["audio_fetched_ms"] >= 0
    assert record.summary()["audible"] is None


def test_advanced_configuration_rejects_arbitrary_services():
    validate_communication(
        {
            "aliases": {"media_player.a": "speaker"},
            "preferences": {"person.alex": {"language": "es"}},
            "adapters": {"door": {"kind": "sip", "script": "script.answer"}},
        }
    )


async def test_current_ha_discovers_tools_through_its_real_assist_platform(hass, setup_roomcall):
    try:
        import probatio
        from homeassistant.components.llm import async_get_tools as get_platform_tools
    except ImportError:
        pytest.skip("Older HA uses the dedicated RoomCall API")
    from homeassistant.setup import async_setup_component

    assert await async_setup_component(hass, "llm", {})
    context = llm.LLMContext(
        platform="test", context=Context(), language="en", assistant=None, device_id=None
    )
    offered = await get_platform_tools(hass, context, "assist")
    tools = [tool for tool in offered.tools if tool.name.startswith("roomcall__")]
    assert len(tools) == 16
    for tool in tools:
        schema = probatio.to_openapi(tool.parameters, custom_serializer=llm.selector_serializer)
        assert "properties" in schema  # Exactly the schema path used by current HA MCP.
        assert tool.integration == "roomcall" and tool.description


async def test_reported_playback_is_observed_only_for_the_matching_content(hass, setup_roomcall):
    async_mock_service(hass, "media_player", "play_media")
    data = hass.data["roomcall"]
    record = await place_call(
        hass, data, CallRequest(targets=["basement"], media_id="http://x/call.mp3", kind="talk")
    )
    hass.states.async_set("media_player.basement_sonos", "playing", {"media_content_id": "other"})
    await hass.async_block_till_done()
    assert "playback_reported_ms" not in record.evidence["media_player.basement_sonos"]
    hass.states.async_set(
        "media_player.basement_sonos", "playing", {"media_content_id": "http://x/call.mp3"}
    )
    await hass.async_block_till_done()
    assert record.evidence["media_player.basement_sonos"]["playback_reported_ms"] >= 0
    assert "playback_reported:test" in data.metrics.summary()["latency"]


async def test_speech_processing_uses_real_ffmpeg_and_limits_peaks():
    import array
    import io
    import math
    import shutil
    import wave

    from custom_components.roomcall.core import to_playable, to_speech_pcm

    binary = shutil.which("ffmpeg")
    if not binary:
        pytest.skip("ffmpeg required")
    pcm = array.array(
        "h", (int(25000 * math.sin(2 * math.pi * 300 * i / 16000)) for i in range(32000))
    ).tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(pcm)
    audio, content_type = await to_playable(
        binary, buf.getvalue(), "audio/wav", 15, processing="speech"
    )
    samples = array.array("h", await to_speech_pcm(binary, audio))
    assert content_type == "audio/mpeg" and len(samples) >= 30000
    assert 1000 < max(abs(value) for value in samples) < 32000


async def test_queued_message_rechecks_do_not_disturb(hass, setup_roomcall):
    data = hass.data["roomcall"]
    played = async_mock_service(hass, "media_player", "play_media")
    lock = await data.scheduler.acquire("media_player.basement_sonos", None)
    result = await execute(hass, "send", {"areas": ["basement"], "media_url": "x"}, Context())
    await hass.async_block_till_done()
    data.dnd = True
    lock.release()
    job = await finished(hass, result["id"])
    assert job.state == "failed" and not played
    assert job.record.results[0].error_code == "dnd_blocked"


async def test_volume_ceiling_also_applies_without_a_fixed_announcement_volume(
    hass, setup_roomcall
):
    data = hass.data["roomcall"]
    data.volume, data.max_volume = None, 0.3
    preview = await execute(hass, "preview", {"areas": ["basement"]}, Context())
    assert preview["volumes"]["media_player.basement_sonos"] == 0.3


async def test_phone_action_acknowledges_the_right_job(hass, setup_roomcall, hass_admin_user):
    data = hass.data["roomcall"]
    context = Context(user_id=hass_admin_user.id)
    data.notify = ["notify.mobile_app_phone"]
    notified = async_mock_service(hass, "notify", "mobile_app_phone")
    async_mock_service(hass, "media_player", "play_media")
    result = await execute(
        hass, "send", {"areas": ["basement"], "media_url": "x", "require_ack": True}, context
    )
    for _ in range(30):
        await hass.async_block_till_done()
        if data.jobs.items[result["id"]].state == "waiting_ack":
            break
    action = notified[0].data["data"]["actions"][0]["action"]
    assert action == f"ROOMCALL_ACK_{result['id']}"
    hass.bus.async_fire("mobile_app_notification_action", {"action": action}, context=context)
    await hass.async_block_till_done()
    job = await finished(hass, result["id"])
    assert job.state == "acknowledged" and job.responses[0]["user_id"] == hass_admin_user.id


async def test_voicemail_api_owner_delete_and_replay(hass, setup_roomcall):
    data = hass.data["roomcall"]
    data.voicemail_enabled = data.mailbox.enabled = True
    hass.states.async_set("tts.piper", "unknown")
    async_mock_service(hass, "media_player", "play_media")
    key = await data.mailbox.add(None, "Dinner", "basement")
    result = await execute(hass, "voicemail", {}, Context())
    assert result["messages"][0]["id"] == key
    replay = await execute(hass, "voicemail", {"action": "replay", "id": key}, Context())
    assert (await finished(hass, replay["id"])).state == "completed"
    deleted = await execute(hass, "voicemail", {"action": "delete", "id": key}, Context())
    assert deleted["ok"] and not data.mailbox.items
    from custom_components.roomcall.schema import vol

    with pytest.raises(vol.Invalid):
        validate_communication({"adapters": {"door": {"kind": "sip", "script": "lock.unlock"}}})
