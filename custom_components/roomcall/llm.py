"""Explicit current-HA LLM tools and an API usable on older supported HA versions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.core import Context, HomeAssistant, callback
from homeassistant.helpers import llm

from .communication import SCHEMAS, execute
from .const import DOMAIN
from .localization import text

try:
    from homeassistant.components.llm import LLMTools
except ImportError:  # HA before the LLM platform: the dedicated API still works.

    @dataclass
    class LLMTools:  # type: ignore[no-redef]
        tools: list[llm.Tool]
        prompt: str | None = None


class CommunicationTool(llm.Tool):
    def __init__(self, hass: HomeAssistant, operation: str, language: str | None) -> None:
        self.name = f"{DOMAIN}__{operation}"
        self.integration = DOMAIN
        self.operation = operation
        self.description = text(hass, f"tool_{operation}", language)
        self.parameters = SCHEMAS[operation]
        if annotations := getattr(llm, "ToolAnnotations", None):
            self.annotations = annotations(
                read_only=operation in {"discover", "preview", "diagnose", "status"},
                idempotent=operation in {"discover", "preview", "diagnose", "status"},
            )

    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: llm.ToolInput,
        llm_context: llm.LLMContext,
    ) -> Any:
        result = await execute(
            hass,
            self.operation,
            tool_input.tool_args,
            llm_context.context or Context(),
            assistant=llm_context.assistant,
        )
        if wrapper := getattr(llm, "ToolResult", None):
            return wrapper(data=result, error=not result["ok"])
        return result


@callback
def async_get_tools(
    hass: HomeAssistant, llm_context: llm.LLMContext, api_id: str
) -> LLMTools | None:
    if api_id not in {"assist", "homeassistant", DOMAIN} or DOMAIN not in hass.data:
        return None
    return LLMTools(
        tools=[CommunicationTool(hass, name, llm_context.language) for name in SCHEMAS],
        prompt=text(hass, "agent_prompt", llm_context.language),
    )


class RoomCallAPI(llm.API):
    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__(hass=hass, id=DOMAIN, name="RoomCall")

    async def async_get_api_instance(self, llm_context: llm.LLMContext) -> llm.APIInstance:
        offered = async_get_tools(self.hass, llm_context, DOMAIN)
        return llm.APIInstance(
            api=self,
            api_prompt=offered.prompt if offered else "",
            llm_context=llm_context,
            tools=offered.tools if offered else [],
            custom_serializer=getattr(llm, "selector_serializer", None),
        )
