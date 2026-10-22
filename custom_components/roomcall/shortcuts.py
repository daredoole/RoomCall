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
NATIVE_FORMATS = {"native", "native_signed"}
SCHEMA = vol.Schema(
    {
        vol.Required("room"): vol.All(str, vol.Length(min=1, max=128)),
        vol.Optional("format", default="cherri"): vol.In(
            ("cherri", "shortcut", "signed", "native", "native_signed")
        ),
        vol.Optional("multiple", default=False): bool,
        vol.Optional("message_mode", default="ask"): vol.In(("ask", "input")),
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
        "const message = text(entered)\n"
        "const encoded = urlEncode(message)\n"
        f'openURL("homeassistant://call_service/roomcall.broadcast?areas={quote(room, safe="")}'
        '&message={encoded}")\n'
    )


def workflow(room: str, prompt: str) -> bytes:
    """Build the same Ask → encode → HA link flow used by the verified Cherri export."""
    ask_id, encode_id = (str(uuid4()) for _ in range(2))
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
                    "urlencode",
                    {"UUID": encode_id, "WFEncodeMode": "Encode", "WFInput": token(reference)},
                ),
                action("openurl", {"WFInput": url}),
            ],
        },
        fmt=plistlib.FMT_XML,
    )


async def room_choices(hass, data, context, language=None, assistant=None) -> dict[str, str]:
    """Friendly choices include only destinations the caller can actually control."""
    allowed = {
        entity_id
        for entity_id in data.speakers
        if await permitted(hass, entity_id, context, assistant)
    }
    eligible = [
        room
        for room in rooms(hass, data.speakers)
        if set(resolve(hass, data.speakers, [room["area_id"]])) <= allowed
    ]
    everywhere = text(hass, "shortcut_everywhere", language)
    names = [room["name"] for room in eligible]
    choices = {
        (
            f"{room['name']} ({room['area_id']})"
            if names.count(room["name"]) > 1 or room["name"] == everywhere
            else room["name"]
        ): room["area_id"]
        for room in eligible
    }
    if allowed and allowed == set(data.speakers):
        choices[everywhere] = ALL_ROOMS
    return choices


def native_workflow(
    room: str, prompt: str, multiple: bool, message_mode: str, labels: dict
) -> bytes:
    """Use the on-device exported App Intent shape without any user's server ID."""
    actions: list[dict] = []
    questions: list[dict] = []

    def token(reference):
        return {
            "WFSerializationType": "WFTextTokenString",
            "Value": {"string": "\ufffc", "attachmentsByRange": {"{0, 1}": reference}},
        }

    def add(name, parameters):
        identifier = str(uuid4())
        parameters["UUID"] = identifier
        actions.append(
            {"WFWorkflowActionIdentifier": name, "WFWorkflowActionParameters": parameters}
        )
        return token({"OutputUUID": identifier, "Type": "ActionOutput"})

    def builtin(name, parameters):
        return add("is.workflow.actions." + name, parameters)

    def call(service, payload):
        index = len(actions)
        result = add(
            "io.robbie.HomeAssistant.CallServiceIntent",
            {
                "payload": payload,
                "AppIntentDescriptor": {
                    "TeamIdentifier": "QMQYCKL255",
                    "BundleIdentifier": "io.robbie.HomeAssistant",
                    "Name": "Home Assistant",
                    "AppIntentIdentifier": "CallServiceIntent",
                },
            },
        )
        for key, description in [("server", labels["shortcut_import_server"]), ("action", service)]:
            questions.append(
                {
                    "Category": "Parameter",
                    "ActionIndex": index,
                    "ParameterKey": key,
                    "Text": description,
                    "DefaultValue": "",
                }
            )
        return result

    if room == "ask":
        found = call("roomcall.communicate", '{"operation":"discover","request":{}}')
        decoded = builtin("detect.dictionary", {"WFInput": found})
        choices = builtin(
            "getvalueforkey",
            {
                "WFInput": decoded,
                "WFDictionaryKey": "shortcut_choices",
                "WFGetDictionaryValueType": "Value",
            },
        )
        target = builtin(
            "choosefromlist",
            {
                "WFInput": choices,
                "WFChooseFromListActionPrompt": labels["shortcut_choose_destination"],
                "WFChooseFromListActionSelectMultiple": multiple,
            },
        )
    else:
        target = room
    message = (
        builtin("ask", {"WFAskActionPrompt": prompt, "WFInputType": "Text"})
        if message_mode == "ask"
        else token({"Type": "ExtensionInput"})
    )
    message_dict = builtin(
        "dictionary",
        {
            "WFItems": {
                "WFSerializationType": "WFDictionaryFieldValue",
                "Value": {
                    "WFDictionaryFieldValueItems": [
                        {
                            "WFItemType": 0,
                            "WFKey": {
                                "WFSerializationType": "WFTextTokenString",
                                "Value": {"string": "message"},
                            },
                            "WFValue": message,
                        }
                    ]
                },
            }
        },
    )
    payload = builtin(
        "setvalueforkey",
        {"WFDictionary": message_dict, "WFDictionaryKey": "areas", "WFDictionaryValue": target},
    )
    if room == "ask":
        # Structured send rejects empty targets rather than defaulting to Everywhere.
        operation = builtin(
            "dictionary",
            {
                "WFItems": {
                    "WFSerializationType": "WFDictionaryFieldValue",
                    "Value": {
                        "WFDictionaryFieldValueItems": [
                            {
                                "WFItemType": 0,
                                "WFKey": {
                                    "WFSerializationType": "WFTextTokenString",
                                    "Value": {"string": "operation"},
                                },
                                "WFValue": {
                                    "WFSerializationType": "WFTextTokenString",
                                    "Value": {"string": "send"},
                                },
                            }
                        ]
                    },
                }
            },
        )
        payload = builtin(
            "setvalueforkey",
            {"WFDictionary": operation, "WFDictionaryKey": "request", "WFDictionaryValue": payload},
        )
    call("roomcall.communicate" if room == "ask" else "roomcall.broadcast", payload)
    return plistlib.dumps(
        {
            "WFWorkflowClientVersion": "4033.0.4.3",
            "WFWorkflowMinimumClientVersion": 900,
            "WFWorkflowIcon": {
                "WFWorkflowIconGlyphNumber": 61473,
                "WFWorkflowIconStartColor": 463140863,
            },
            "WFWorkflowImportQuestions": questions,
            "WFWorkflowActions": actions,
            "WFWorkflowInputContentItemClasses": ["WFStringContentItem"],
            "WFWorkflowHasShortcutInputVariables": message_mode == "input",
            "WFWorkflowTypes": ["ActionExtension"] if message_mode == "input" else [],
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
    native = values["format"] in NATIVE_FORMATS
    if not native and (room == "ask" or values["multiple"] or values["message_mode"] != "ask"):
        raise Problem("invalid_request")
    known = {item["area_id"]: item["name"] for item in rooms(hass, data.speakers)}
    known[ALL_ROOMS] = text(hass, "shortcut_everywhere", values["language"])
    if native and room == "ask":
        if not await room_choices(hass, data, context, values["language"]):
            raise Problem("no_targets")
        known["ask"] = text(hass, "shortcut_ask_each_time", values["language"])
        targets = []
    elif room not in known or not (targets := resolve(hass, data.speakers, [room])):
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
    payload = (
        native_workflow(
            room,
            text(hass, "shortcut_message_prompt", values["language"]) if room == "ask" else prompt,
            values["multiple"],
            values["message_mode"],
            {
                key: text(hass, key, values["language"])
                for key in ["shortcut_import_server", "shortcut_choose_destination"]
            },
        )
        if native
        else workflow(room, prompt)
    )
    if values["format"] in {"shortcut", "native"}:
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
