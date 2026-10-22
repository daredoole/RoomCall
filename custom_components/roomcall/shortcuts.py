"""Portable Siri shortcut generation; signing requires the caller's own account."""

from __future__ import annotations

import asyncio
import json
import plistlib
from urllib.parse import quote
from uuid import uuid4

import aiohttp
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .communication import permitted
from .const import ALL_ROOMS, DOMAIN
from .core import resolve, rooms
from .errors import Problem
from .localization import text
from .schema import vol

SIGN_URL = "https://routinehub.co/api/v1/sign-shortcut"
MAX_SIGNED_BYTES = 2 * 1024 * 1024
SCHEMA = vol.Schema(
    {
        vol.Required("room"): vol.All(str, vol.Length(min=1, max=128)),
        vol.Optional("format", default="cherri"): vol.In(("cherri", "shortcut", "signed")),
        vol.Optional("language", default="en"): vol.In(("en", "es", "de", "fr", "nl")),
        vol.Optional("allow_remote", default=False): bool,
        vol.Optional("signing_key", default=""): vol.All(str, vol.Length(max=512)),
    }
)


def source(room: str, prompt: str) -> str:
    """Use only public built-in actions, with no HA URL or credentials in the file."""
    quoted_prompt = json.dumps(prompt.replace("{", "(").replace("}", ")"), ensure_ascii=False)
    return (
        "#include 'actions/web'\n#define glyph speaker\n#define color blue\n\n"
        f"const entered = prompt({quoted_prompt})\n"
        'const message = text(entered)\nif message == "" {\n    stop()\n}\n'
        "const encoded = urlEncode(message)\n"
        f'openURL("homeassistant://call_service/roomcall.broadcast?areas={quote(room, safe="")}'
        '&message={encoded}")\n'
    )


def workflow(room: str, prompt: str) -> bytes:
    """Build the same Ask → encode → HA link flow used by the verified Cherri export."""
    ask_id, encode_id, group_id = (str(uuid4()) for _ in range(3))
    reference = {"OutputUUID": ask_id, "Type": "ActionOutput", "OutputName": "Message"}

    def token(value: dict) -> dict:
        return {
            "WFSerializationType": "WFTextTokenString",
            "Value": {"string": "\ufffc", "attachmentsByRange": {"{0, 1}": value}},
        }

    prefix = (
        f"homeassistant://call_service/roomcall.broadcast?areas={quote(room, safe='')}&message="
    )
    url = token({"OutputUUID": encode_id, "Type": "ActionOutput", "OutputName": "Encoded"})
    url["Value"]["string"] = prefix + "\ufffc"
    url["Value"]["attachmentsByRange"] = {
        f"{{{len(prefix)}, 1}}": {"OutputUUID": encode_id, "Type": "ActionOutput"}
    }

    def action(name: str, parameters: dict) -> dict:
        return {
            "WFWorkflowActionIdentifier": f"is.workflow.actions.{name}",
            "WFWorkflowActionParameters": parameters,
        }

    return plistlib.dumps(
        {
            "WFWorkflowClientVersion": "4033.0.4.3",
            "WFWorkflowMinimumClientVersion": 900,
            "WFWorkflowIcon": {
                "WFWorkflowIconGlyphNumber": 61473,
                "WFWorkflowIconStartColor": 463140863,
            },
            "WFWorkflowImportQuestions": [],
            "WFWorkflowActions": [
                action("ask", {"UUID": ask_id, "WFAskActionPrompt": prompt, "WFInputType": "Text"}),
                action(
                    "conditional",
                    {
                        "GroupingIdentifier": group_id,
                        "WFControlFlowMode": 0,
                        "WFCondition": 4,
                        "WFConditionalActionString": "",
                        "WFInput": {
                            "Type": "Variable",
                            "Variable": {
                                "WFSerializationType": "WFTextTokenAttachment",
                                "Value": reference,
                            },
                        },
                    },
                ),
                action("exit", {}),
                action("conditional", {"GroupingIdentifier": group_id, "WFControlFlowMode": 2}),
                action(
                    "urlencode",
                    {"UUID": encode_id, "WFEncodeMode": "Encode", "WFInput": token(reference)},
                ),
                action("openurl", {"WFInput": url}),
            ],
        },
        fmt=plistlib.FMT_XML,
    )


async def generate(hass: HomeAssistant, values: dict, context: Context) -> tuple[bytes, str]:
    try:
        values = SCHEMA(values)
    except vol.Invalid as err:
        raise Problem("invalid_request") from err
    data = hass.data.get(DOMAIN)
    if data is None:
        raise Problem("not_ready")
    room = values["room"]
    known = {item["area_id"]: item["name"] for item in rooms(hass, data.speakers)}
    known[ALL_ROOMS] = text(hass, "shortcut_everywhere", values["language"])
    if room not in known or not (targets := resolve(hass, data.speakers, [room])):
        raise Problem("no_targets")
    for entity_id in targets:
        if not await permitted(hass, entity_id, context):
            raise Problem("forbidden")
    prompt = text(hass, "shortcut_prompt", values["language"], room=known[room])
    filename = (
        "RoomCall " + "".join(c if c.isalnum() or c in " -_" else "_" for c in known[room])[:80]
    )
    if values["format"] == "cherri":
        return source(room, prompt).encode(), filename + ".cherri"
    payload = workflow(room, prompt)
    if values["format"] == "shortcut":
        return payload, filename + " unsigned.shortcut"
    if not values["allow_remote"]:
        raise Problem("confirmation_required")
    if not values["signing_key"].strip():
        raise Problem("shortcut_key_required")
    lock = hass.data.setdefault("roomcall_shortcut_signing", asyncio.Lock())
    if lock.locked():
        raise Problem("shortcut_signing_busy", retryable=True)
    async with lock:
        form = aiohttp.FormData()
        form.add_field("shortcut_name", filename)
        form.add_field(
            "shortcut_file",
            payload,
            filename="unsigned.shortcut",
            content_type="application/x-plist",
        )
        form.add_field("api_key", values["signing_key"])
        try:
            async with async_get_clientsession(hass).post(
                SIGN_URL,
                data=form,
                allow_redirects=False,
                timeout=aiohttp.ClientTimeout(total=45),
            ) as response:
                if response.status != 200:
                    raise ValueError("signing rejected")
                signed = bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    signed.extend(chunk)
                    if len(signed) > MAX_SIGNED_BYTES:
                        raise ValueError("oversized signing response")
                if not signed.startswith(b"AEA1"):
                    raise ValueError("unsigned signing response")
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            raise Problem("shortcut_signing_failed", retryable=True) from err
    return bytes(signed), filename + ".shortcut"
