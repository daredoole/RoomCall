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
from custom_components.roomcall.shortcuts import SIGN_URL, generate, source, workflow
from homeassistant.core import Context


def test_workflow_routes_exact_room_and_preserves_message():
    room = 'kids & "all"/room'
    prompt = 'What about <room> "hello"?'
    value = plistlib.loads(workflow(room, prompt))
    actions = value["WFWorkflowActions"]
    assert actions[0]["WFWorkflowActionParameters"]["WFAskActionPrompt"] == prompt
    assert actions[1]["WFWorkflowActionParameters"]["WFConditionalActionString"] == ""
    assert actions[2]["WFWorkflowActionIdentifier"].endswith(".exit")
    assert actions[4]["WFWorkflowActionIdentifier"].endswith(".urlencode")
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
