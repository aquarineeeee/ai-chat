from __future__ import annotations

import unittest

from app.core.exceptions import AppError
from app.services.providers import _validate_provider_configuration


class CustomProviderConfigurationTests(unittest.TestCase):
    def test_custom_provider_accepts_the_three_supported_protocols(self) -> None:
        for adapter_id in (
            "openai_chat_completions",
            "openai_responses",
            "anthropic_messages",
        ):
            with self.subTest(adapter_id=adapter_id):
                self.assertEqual(
                    _validate_provider_configuration(
                        preset_id="custom",
                        adapter_id=adapter_id,
                        base_url=" https://gateway.example.com/v1/ ",
                    ),
                    "https://gateway.example.com/v1",
                )

    def test_custom_provider_requires_an_http_base_url(self) -> None:
        with self.assertRaises(AppError) as missing_url:
            _validate_provider_configuration(
                preset_id="custom",
                adapter_id="openai_chat_completions",
                base_url=None,
            )
        self.assertEqual(missing_url.exception.status_code, 422)

        with self.assertRaises(AppError) as invalid_url:
            _validate_provider_configuration(
                preset_id="custom",
                adapter_id="openai_chat_completions",
                base_url="gateway.example.com/v1",
            )
        self.assertEqual(invalid_url.exception.status_code, 422)

    def test_custom_provider_rejects_unsupported_protocols(self) -> None:
        with self.assertRaises(AppError) as error:
            _validate_provider_configuration(
                preset_id="custom",
                adapter_id="google_gemini_generate_content",
                base_url="https://gateway.example.com",
            )
        self.assertEqual(error.exception.status_code, 422)


if __name__ == "__main__":
    unittest.main()
