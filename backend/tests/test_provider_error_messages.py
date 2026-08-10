from __future__ import annotations

import unittest

import httpx

from app.providers.anthropic import _extract_error_message as extract_anthropic_error_message
from app.providers.openai import _extract_error_message as extract_openai_error_message


class ProviderErrorMessageTests(unittest.TestCase):
    def test_html_gateway_error_is_not_exposed(self) -> None:
        response = httpx.Response(
            status_code=502,
            headers={"content-type": "text/html; charset=UTF-8"},
            text="<!DOCTYPE html><html><title>cloudhabitatsh.com | 502: Bad gateway</title></html>",
        )

        for extract_error_message in (extract_openai_error_message, extract_anthropic_error_message):
            with self.subTest(provider=extract_error_message.__module__):
                message = extract_error_message(response)
                self.assertIn("HTTP 502", message)
                self.assertIn("HTML 错误页", message)
                self.assertNotIn("<!DOCTYPE html>", message)
                self.assertNotIn("cloudhabitatsh.com", message)

    def test_json_error_message_is_preserved(self) -> None:
        response = httpx.Response(status_code=401, json={"error": {"message": "API key is invalid"}})

        for extract_error_message in (extract_openai_error_message, extract_anthropic_error_message):
            with self.subTest(provider=extract_error_message.__module__):
                self.assertEqual(extract_error_message(response), "API key is invalid")


if __name__ == "__main__":
    unittest.main()
