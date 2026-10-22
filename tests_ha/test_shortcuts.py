"""Authenticated, credential-free exports and bounded opt-in signing."""

from __future__ import annotations

import asyncio
import json
import plistlib
from unittest.mock import AsyncMock, Mock, patch
from urllib.parse import parse_qs, quote, unquote, urlsplit

import aiohttp
import pytest
from custom_components.roomcall.errors import Problem
from custom_components.roomcall.shortcuts import SIGN_URL, generate, room_choices, source, workflow
from homeassistant.core import Context


def test_workflow_routes_exact_room_and_preserves_message():
    room = 'kids & "all"/room'
    prompt = 'What about <room> "hello"?'
    value = plistlib.loads(workflow(room, prompt))
    actions = value["WFWorkflowActions"]
    assert actions[0]["WFWorkflowActionParameters"]["WFAskActionPrompt"] == prompt
    assert len(actions) == 3
    assert actions[1]["WFWorkflowActionIdentifier"].endswith(".urlencode")
    assert "conditional" not in source(room, prompt)
    params = actions[-1]["WFWorkflowActionParameters"]["WFInput"]["Value"]
    for message in ["Hello", 'Café 🍽️\n"Dinner" & snacks? #1']:
        url = params["string"].replace("\ufffc", quote(message, safe=""))
        parsed = urlsplit(url)
        assert parsed.scheme == "homeassistant" and parsed.path == "/roomcall.broadcast"
        assert parse_qs(parsed.query) == {"areas": [room], "message": [message]}
    assert "Bearer" not in json.dumps(value)
    assert source(room, 'What {Clipboard} "now"?').count("{Clipboard}") == 0
    assert quote(room, safe="") in source(room, prompt)


async def test_exports_require_auth_and_use_attachment(
    hass, setup_roomcall, house, hass_client, hass_client_no_auth
):
    anon = await hass_client_no_auth()
    assert (
        await anon.post("/api/roomcall/shortcut", json={"room": house["basement"]})
    ).status == 401
    client = await hass_client()
    result = await client.post("/api/roomcall/shortcut", json={"room": house["basement"]})
    assert result.status == 200 and result.headers["Cache-Control"] == "no-store"
    assert "RoomCall Basement.cherri" in unquote(result.headers["Content-Disposition"])
    assert "areas=basement" in await result.text()
    result = await client.post(
        "/api/roomcall/shortcut", json={"room": "all", "format": "shortcut", "language": "es"}
    )
    assert result.status == 200
    value = plistlib.loads(await result.read())
    assert (
        "anunciar"
        in value["WFWorkflowActions"][0]["WFWorkflowActionParameters"]["WFAskActionPrompt"]
    )
    assert "unsigned.shortcut" in unquote(result.headers["Content-Disposition"])
    result = await client.post("/api/roomcall/shortcut", data=b"x" * 65537)
    assert (await result.json())["error"]["code"] == "invalid_request"
    result = await client.post("/api/roomcall/shortcut", data="{broken")
    assert (await result.json())["error"]["code"] == "invalid_request"
    result = await client.post("/api/roomcall/shortcut", json={"room": "missing"})
    assert (await result.json())["error"]["code"] == "no_targets"


async def test_export_validation_and_permissions(hass, setup_roomcall, hass_admin_user):
    context = Context(user_id=hass_admin_user.id)
    for values, code in [
        ({}, "invalid_request"),
        ({"room": "missing"}, "no_targets"),
        ({"room": "all", "format": "signed"}, "confirmation_required"),
        ({"room": "all", "format": "signed", "allow_remote": True}, "shortcut_key_required"),
    ]:
        with pytest.raises(Problem, match=code):
            await generate(hass, values, context)
    with (
        patch("custom_components.roomcall.shortcuts.permitted", AsyncMock(return_value=False)),
        pytest.raises(Problem, match="forbidden"),
    ):
        await generate(hass, {"room": "all"}, context)
    data = hass.data.pop("roomcall")
    try:
        with pytest.raises(Problem, match="not_ready"):
            await generate(hass, {"room": "all"}, context)
    finally:
        hass.data["roomcall"] = data


async def test_signed_export_is_opt_in_bounded_and_never_persists_key(
    hass, setup_roomcall, aioclient_mock
):
    values = {"room": "all", "format": "signed", "allow_remote": True, "signing_key": "test-secret"}
    aioclient_mock.post(SIGN_URL, content=b"AEA1signed")
    result, name = await generate(hass, values, Context())
    assert result == b"AEA1signed" and name.endswith(".shortcut")
    assert aioclient_mock.call_count == 1
    assert "test-secret" not in repr(hass.data.get("roomcall_shortcut_signing"))
    lock = hass.data["roomcall_shortcut_signing"]
    async with lock:
        with pytest.raises(Problem, match="shortcut_signing_busy"):
            await generate(hass, values, Context())
    assert not lock.locked()


@pytest.mark.parametrize("failure", ["status", "html", "oversize", "network", "timeout"])
async def test_signing_failures_do_not_become_importable_files(
    hass, setup_roomcall, aioclient_mock, failure
):
    values = {"room": "all", "format": "signed", "allow_remote": True, "signing_key": "test-secret"}
    if failure == "status":
        aioclient_mock.post(SIGN_URL, status=403)
    elif failure == "html":
        aioclient_mock.post(SIGN_URL, content=b"<html>sign in</html>")
    elif failure == "oversize":
        aioclient_mock.post(SIGN_URL, content=b"AEA1" + b"x" * (2 * 1024 * 1024))
    else:
        aioclient_mock.post(
            SIGN_URL, exc=TimeoutError() if failure == "timeout" else aiohttp.ClientError()
        )
    with pytest.raises(Problem, match="shortcut_signing_failed"):
        await generate(hass, values, Context())
    assert not hass.data["roomcall_shortcut_signing"].locked()


@pytest.mark.parametrize(
    "room,multiple,mode",
    [("ask", False, "ask"), ("ask", True, "input"), ("basement", False, "ask")],
)
async def test_native_export_has_device_setup_and_typed_target(
    hass, setup_roomcall, room, multiple, mode
):
    payload, filename = await generate(
        hass,
        {"room": room, "format": "native", "multiple": multiple, "message_mode": mode},
        Context(),
    )
    value = plistlib.loads(payload)
    assert filename.endswith("unsigned.shortcut")
    assert "homeassistant://" not in payload.decode()
    actions = value["WFWorkflowActions"]
    calls = [
        (i, a)
        for i, a in enumerate(actions)
        if a["WFWorkflowActionIdentifier"] == "io.robbie.HomeAssistant.CallServiceIntent"
    ]
    assert len(calls) == (2 if room == "ask" else 1)
    assert value["WFWorkflowHasShortcutInputVariables"] == (mode == "input")
    for index, call in calls:
        assert "server" not in call["WFWorkflowActionParameters"]
        assert "action" not in call["WFWorkflowActionParameters"]
        questions = [q for q in value["WFWorkflowImportQuestions"] if q["ActionIndex"] == index]
        assert {q["ParameterKey"] for q in questions} == {"server", "action"}
    assert "::roomcall." not in payload.decode()
    assert not any(a["WFWorkflowActionIdentifier"].endswith(".conditional") for a in actions)
    if room == "ask":
        chooser = next(
            a["WFWorkflowActionParameters"]
            for a in actions
            if a["WFWorkflowActionIdentifier"].endswith(".choosefromlist")
        )
        assert chooser["WFChooseFromListActionSelectMultiple"] is multiple
        assert any(
            a["WFWorkflowActionParameters"].get("WFDictionaryKey") == "shortcut_choices"
            for a in actions
        )
        assert all(
            q["Text"] == "roomcall.communicate"
            for q in value["WFWorkflowImportQuestions"]
            if q["ParameterKey"] == "action"
        )
    else:
        assert any(
            a["WFWorkflowActionParameters"].get("WFDictionaryValue") == room for a in actions
        )


async def test_live_choices_obey_permissions_and_disambiguate(hass, setup_roomcall):
    data = hass.data["roomcall"]
    with (
        patch(
            "custom_components.roomcall.shortcuts.rooms",
            return_value=[
                {"name": "Same", "area_id": "first"},
                {"name": "Same", "area_id": "second"},
                {"name": "Everywhere", "area_id": "third"},
                {"name": "Same (first)", "area_id": "fourth"},
            ],
        ),
        patch("custom_components.roomcall.shortcuts.resolve", return_value=[data.speakers[0]]),
    ):
        choices = await room_choices(hass, data, Context())
        assert choices["Same (first)"] == "first" and choices["Same (second)"] == "second"
        assert choices["Everywhere (third)"] == "third" and choices["Everywhere"] == "all"
        assert choices["Same (first) (fourth)"] == "fourth"
    with patch("custom_components.roomcall.shortcuts.permitted", AsyncMock(return_value=False)):
        assert await room_choices(hass, data, Context()) == {}
        with pytest.raises(Problem, match="no_targets"):
            await generate(hass, {"room": "ask", "format": "native"}, Context())
    for options in [
        {"room": "ask"},
        {"room": "all", "multiple": True},
        {"room": "all", "message_mode": "input"},
    ]:
        with pytest.raises(Problem, match="invalid_request"):
            await generate(hass, options, Context())


async def test_native_signed_export_and_http_discovery(
    hass, setup_roomcall, aioclient_mock, hass_client
):
    aioclient_mock.post(SIGN_URL, content=b"AEA1signed")
    payload, name = await generate(
        hass,
        {
            "room": "ask",
            "format": "native_signed",
            "allow_remote": True,
            "signing_key": "test-secret",
        },
        Context(),
    )
    assert payload == b"AEA1signed" and name.endswith(".shortcut")
    client = await hass_client()
    response = await client.post("/api/roomcall/shortcut", json={"room": "ask", "format": "native"})
    assert response.status == 200
    assert plistlib.loads(await response.read())["WFWorkflowImportQuestions"]
    response = await client.post(
        "/api/roomcall/command", json={"operation": "discover", "request": {}}
    )
    found = await response.json()
    assert found["shortcut_choices"]["Basement"] == "basement"


@pytest.mark.parametrize("selected", [["Basement"], ["Basement", "Kitchen"], ["Everywhere"], []])
async def test_native_picker_variable_flow_reaches_validated_send(hass, setup_roomcall, selected):
    """Exercise exported references and JSON boundaries against actual HA discovery/validation.

    This small action evaluator is not an iOS simulator; device import/runtime QA remains required.
    """
    from custom_components.roomcall.communication import execute

    raw, _ = await generate(hass, {"room": "ask", "format": "native", "multiple": True}, Context())
    workflow = plistlib.loads(raw)
    outputs = {}
    message = 'Dinner "now" 🍽️\nBring plates & cups!'

    def value(raw):
        if isinstance(raw, str):
            return raw
        item = raw["Value"]
        if "attachmentsByRange" in item:
            reference = item["attachmentsByRange"]["{0, 1}"]
            return outputs[reference["OutputUUID"]]
        return item["string"]

    for action in workflow["WFWorkflowActions"]:
        kind = action["WFWorkflowActionIdentifier"]
        p = action["WFWorkflowActionParameters"]
        if kind == "io.robbie.HomeAssistant.CallServiceIntent":
            payload = value(p["payload"])
            if isinstance(payload, str):
                payload = json.loads(payload)
            result = await execute(hass, payload["operation"], payload["request"], Context())
            outputs[p["UUID"]] = json.dumps(result)
            if payload["operation"] == "send":
                assert payload["request"]["message"] == message
                if selected:
                    assert result["ok"]
                    job = hass.data["roomcall"].jobs.items[result["id"]]
                    await job.task
                    assert payload["request"]["areas"] == [
                        {"Basement": "basement", "Kitchen": "kitchen", "Everywhere": "all"}[name]
                        for name in selected
                    ]
                else:
                    assert result["error"]["code"] == "target_required"
                    assert not hass.data["roomcall"].jobs.items
        elif kind == "is.workflow.actions.detect.dictionary":
            outputs[p["UUID"]] = json.loads(value(p["WFInput"]))
        elif kind == "is.workflow.actions.getvalueforkey":
            assert p["WFGetDictionaryValueType"] == "Value"
            outputs[p["UUID"]] = value(p["WFInput"])[p["WFDictionaryKey"]]
        elif kind == "is.workflow.actions.choosefromlist":
            choices = value(p["WFInput"])
            outputs[p["UUID"]] = [choices[name] for name in selected]
        elif kind == "is.workflow.actions.ask":
            outputs[p["UUID"]] = message
        elif kind == "is.workflow.actions.dictionary":
            outputs[p["UUID"]] = {
                value(item["WFKey"]): value(item["WFValue"])
                for item in p["WFItems"]["Value"]["WFDictionaryFieldValueItems"]
            }
        else:
            assert kind == "is.workflow.actions.setvalueforkey"
            outputs[p["UUID"]] = {
                **value(p["WFDictionary"]),
                p["WFDictionaryKey"]: value(p["WFDictionaryValue"]),
            }


async def test_live_choices_refresh_and_exclude_partially_permitted_rooms(hass, setup_roomcall):
    data = hass.data["roomcall"]

    async def allowed(hass, entity_id, context, assistant=None):
        return entity_id != "media_player.kitchen_hub"

    with patch("custom_components.roomcall.shortcuts.permitted", allowed):
        assert await room_choices(hass, data, Context()) == {"Basement": "basement"}
    previous = data.speakers
    try:
        data.speakers = ["media_player.basement_sonos"]
        assert await room_choices(hass, data, Context()) == {
            "Basement": "basement",
            "Everywhere": "all",
        }
    finally:
        data.speakers = previous


async def test_cancelling_signing_propagates(hass, setup_roomcall):
    session = Mock()
    session.post.side_effect = asyncio.CancelledError()
    with (
        patch("custom_components.roomcall.shortcuts.async_get_clientsession", return_value=session),
        pytest.raises(asyncio.CancelledError),
    ):
        await generate(
            hass,
            {
                "room": "all",
                "format": "signed",
                "allow_remote": True,
                "signing_key": "test-secret",
            },
            Context(),
        )
    assert not hass.data["roomcall_shortcut_signing"].locked()
