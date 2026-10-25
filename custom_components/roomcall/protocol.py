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
READ_ONLY = {"policy", "catalog", "discover", "diagnose", "preview", "status"}
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
        "playback_prepare_failed": ("doctor", {}),
        "playback_controller_ambiguous": ("setup", {"action": "discover"}),
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
        "playback_delays": {
            "settings_path": ["communication", "playback_delays"],
            "max_entries": 32,
            "key_pattern": r"^media_player\.[a-z0-9_]+$",
            "value_schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    field: {"type": "number", "minimum": 0, "maximum": 60}
                    for field in ("startup_delay", "restore_delay")
                },
            },
            "denonavr_defaults": {"startup_after_wake_or_source_change": 10, "restore_delay": 2},
        },
        "playback_routes": {
            "settings_path": ["communication", "playback_routes"],
            "max_entries": 32,
            "key_pattern": r"^media_player\.[a-z0-9_]+$",
            "value_schema": {
                "type": "object",
                "required": ["controller", "source"],
                "additionalProperties": False,
                "properties": {
                    "controller": {"type": "string", "pattern": r"^media_player\.[a-z0-9_]+$"},
                    "source": {"type": "string", "minLength": 1, "maxLength": 100},
                },
            },
            "identity_evidence": [
                "registry_device",
                "mac",
                "manufacturer_model_serial",
                "explicit_alias",
            ],
            "room_name_is_identity": False,
            "resume_semantics": "best_effort_not_exact_session",
        },
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
            "formats": ["native", "native_signed", "cherri", "shortcut", "signed"],
            "native_picker": {
                "room": "ask",
                "multiple": True,
                "message_mode": ["ask", "input"],
                "discovery": "shortcut_choices",
                "setup": "select_server_and_action_on_device",
            },
            "signed_requires": ["allow_remote", "signing_key"],
            "signing_key_persisted": False,
            "export_transport": "format_specific",
            "export_requires_confirmation_each_run": None,
            "format_transports": {
                name: {
                    "transport": "perform_action"
                    if name.startswith("native")
                    else "homeassistant_url",
                    "url_confirmation_each_run": not name.startswith("native"),
                }
                for name in ["native", "native_signed", "cherri", "shortcut", "signed"]
            },
        },
        "audible": None,
    }
