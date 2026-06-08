from __future__ import annotations

import unittest
from unittest.mock import patch

import httpx

from app.core.exceptions import AppError
from app.models.api_key import ApiKey
from app.providers.anthropic import _messages_payload
from app.providers.anthropic import list_anthropic_models


class AnthropicProviderTests(unittest.TestCase):
    def test_messages_payload_moves_system_to_cacheable_system_block(self) -> None:
        payload = _messages_payload(
            model="claude-sonnet-4-20250514",
            messages=[
                {"role": "system", "content": "Stable system prompt"},
                {"role": "user", "content": "Hello"},
            ],
            temperature=None,
            max_tokens=1000,
            stream=False,
        )

        self.assertEqual(payload["model"], "claude-sonnet-4-20250514")
        self.assertEqual(payload["max_tokens"], 1000)
        self.assertEqual(payload["messages"], [{"role": "user", "content": "Hello"}])
        self.assertEqual(
            payload["system"],
            [
                {
                    "type": "text",
                    "text": "Stable system prompt",
                    "cache_control": {"type": "ephemeral"},
                }
            ],
        )

    def test_messages_payload_uses_default_max_tokens(self) -> None:
        payload = _messages_payload(
            model="claude-sonnet-4-20250514",
            messages=[{"role": "user", "content": "Hello"}],
            temperature=None,
            max_tokens=None,
            stream=True,
        )

        self.assertEqual(payload["max_tokens"], 2000)
        self.assertTrue(payload["stream"])
        self.assertNotIn("system", payload)


class AnthropicModelListTests(unittest.IsolatedAsyncioTestCase):
    async def test_list_models_accepts_top_level_array(self) -> None:
        api_key = ApiKey(provider="anthropic", key_encrypted="encrypted")
        response = httpx.Response(
            status_code=200,
            json=[
                {"id": "claude-sonnet", "display_name": "Claude Sonnet"},
                {"id": "claude-haiku"},
            ],
        )

        class FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def get(self, _url, headers=None):
                return response

        with (
            patch("app.providers.anthropic.decrypt_text", return_value="secret"),
            patch("app.providers.anthropic.httpx.AsyncClient", return_value=FakeClient()),
        ):
            models = await list_anthropic_models(api_key=api_key)

        self.assertEqual(
            models,
            [
                {"id": "claude-haiku", "owned_by": "anthropic"},
                {"id": "claude-sonnet", "owned_by": "Claude Sonnet"},
            ],
        )

    async def test_list_models_surfaces_payload_shape_in_details(self) -> None:
        api_key = ApiKey(provider="anthropic", key_encrypted="encrypted")
        response = httpx.Response(status_code=200, json="unexpected")

        class FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def get(self, _url, headers=None):
                return response

        with (
            patch("app.providers.anthropic.decrypt_text", return_value="secret"),
            patch("app.providers.anthropic.httpx.AsyncClient", return_value=FakeClient()),
        ):
            with self.assertRaises(AppError) as ctx:
                await list_anthropic_models(api_key=api_key)

        self.assertEqual(ctx.exception.message, "Anthropic 模型列表响应格式不正确")
        self.assertEqual(ctx.exception.details, "顶层类型: str")


if __name__ == "__main__":
    unittest.main()
