from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class AdapterDescriptor:
    id: str
    display_name: str
    capabilities: frozenset[str] = frozenset({"text", "stream", "tools", "usage"})


class ProviderAdapter(Protocol):
    descriptor: AdapterDescriptor


_REGISTRY: dict[str, ProviderAdapter] = {}


def register_adapter(adapter: ProviderAdapter) -> ProviderAdapter:
    _REGISTRY[adapter.descriptor.id] = adapter
    return adapter


def get_adapter(adapter_id: str) -> ProviderAdapter:
    try:
        return _REGISTRY[adapter_id]
    except KeyError as exc:
        raise ValueError(f"unknown provider adapter: {adapter_id}") from exc


def list_adapters() -> list[AdapterDescriptor]:
    return [adapter.descriptor for adapter in _REGISTRY.values()]


class _DescriptorOnlyAdapter:
    def __init__(self, descriptor: AdapterDescriptor) -> None:
        self.descriptor = descriptor


for _descriptor in (
    AdapterDescriptor("openai_chat_completions", "OpenAI Chat Completions"),
    AdapterDescriptor("openai_responses", "OpenAI Responses"),
    AdapterDescriptor("anthropic_messages", "Anthropic Messages"),
    AdapterDescriptor("google_gemini_generate_content", "Google Gemini GenerateContent"),
):
    register_adapter(_DescriptorOnlyAdapter(_descriptor))


def adapter_supports(adapter_id: str, capability: str) -> bool:
    try:
        return capability in get_adapter(adapter_id).descriptor.capabilities
    except ValueError:
        return False
