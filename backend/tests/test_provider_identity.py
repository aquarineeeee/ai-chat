from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.services.conversations import _provider_name_for_instance
from app.services.messages import _adapter_id_for_generation


class ProviderIdentityTests(unittest.TestCase):
    def test_instance_display_name_is_persisted_as_provider_identity(self) -> None:
        instance = SimpleNamespace(preset_id="custom", display_name="aether", default_adapter_id="openai_chat_completions")

        self.assertEqual(_provider_name_for_instance(instance), "aether")

    def test_custom_provider_keeps_its_openai_compatible_adapter(self) -> None:
        instance = SimpleNamespace(preset_id="custom", display_name="aether", default_adapter_id="openai_chat_completions")

        self.assertEqual(
            _adapter_id_for_generation(provider="aether", instance=instance),
            "openai_chat_completions",
        )
