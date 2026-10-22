"""Versioned, discoverable contracts and actionable recovery instructions."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import section
from homeassistant.helpers import llm

from .localization import text
from .schema import vol

API_VERSION = "1.0"
SETUP_KEY = "roomcall_setup"
READ_ONLY = {"catalog", "discover", "diagnose", "preview", "status"}
OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["ok", "api_version"],
    "properties": {
        "ok": {"type": "boolean"},
        "api_version": {"const": API_VERSION},
        "error": {
            "type": "object",
            "required": ["code", "message", "retryable", "details", "next_actions"],
        },
    },
    "additionalProperties": True,
}


def json_schema(schema) -> dict[str, Any]:
    def serializer(value):
        if isinstance(value, section):
            return json_schema(value.schema)
        return llm.selector_serializer(value)

    if converter := getattr(vol, "to_openapi", None):
        return converter(schema, custom_serializer=serializer, openapi_version="3.1.0")
    from voluptuous_openapi import convert

    return convert(schema, custom_serializer=serializer)


def next_actions(code: str) -> list[dict[str, Any]]:
    operation, request = {
        "not_ready": ("setup", {"action": "discover"}),
        "tts_unavailable": ("setup", {"action": "discover"}),
        "choose_tts": ("setup", {"action": "discover"}),
        "target_required": ("discover", {}),
        "no_targets": ("setup", {"action": "discover"}),
        "configuration_conflict": ("setup", {"action": "plan"}),
        "plan_expired": ("setup", {"action": "plan"}),
        "dnd_blocked": ("diagnose", {}),
        "quiet_hours_blocked": ("diagnose", {}),
        "playback_failed": ("doctor", {}),
        "provider_failed": ("doctor", {}),
        "tts_preparation_failed": ("diagnose", {}),
    }.get(code, ("catalog", {}))
    return [{"operation": operation, "request": request}]


def catalog(hass: HomeAssistant) -> dict[str, Any]:
    from .communication import SCHEMAS

    return {
        "ok": True,
        "api_version": API_VERSION,
        "bootstrap": {
            "kind": "home_assistant_config_flow",
            "domain": "roomcall",
            "source": "user",
            "then": {"operation": "setup", "request": {"action": "discover"}},
        },
        "operations": {
            name: {
                "description": text(hass, f"tool_{name}"),
                "input_schema": json_schema(schema),
                "output_schema": OUTPUT_SCHEMA,
                "read_only": name in READ_ONLY,
                "admin_required": name in {"setup", "announcements", "doctor"},
            }
            for name, schema in SCHEMAS.items()
        },
        "setup_sequence": ["discover", "plan", "apply", "test", "confirm", "verify"],
        "shortcut_exports": {
            "recommended": {
                "app": "Home Assistant",
                "action": "Perform Action",
                "service": "roomcall.broadcast",
                "server_selection": "on_device",
                "data": {"areas": "room_id", "message": "Ask for Input magic variable"},
            },
            "method": "POST",
            "path": "/api/roomcall/shortcut",
            "formats": ["cherri", "shortcut", "signed"],
            "signed_requires": ["allow_remote", "signing_key"],
            "signing_key_persisted": False,
            "export_transport": "homeassistant_url",
            "export_requires_confirmation_each_run": True,
        },
        "audible": None,
    }
