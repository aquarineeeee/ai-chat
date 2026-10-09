from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from app.canonical_transcript import (
    CanonicalTranscriptItem, ImagePart, TextPart, build_message_history_transcript,
    user_message_item, user_text_item,
)
from app.core.exceptions import AppError
from app.models.api_key import ApiKey
from app.models.message import Message, MessageRole, MessageStatus
from app.providers import anthropic, openai, openai_responses
from app.providers.registry import adapter_supports
from app.services import vision_budget
from app.services.vision_budget import (
    AttachmentMetadata, VisionBufferBudget, VisionRequestContext, vision_request,
)


def image_transcript():
    return [CanonicalTranscriptItem(kind="user_text", text="Describe", parts=(
        TextPart("Describe"), ImagePart(4, "image/png"), ImagePart(7, "image/jpeg"),
    ))]


def reader():
    result = SimpleNamespace(metadata=AsyncMock(), read=AsyncMock(return_value=b"abc"))
    result.metadata.side_effect = lambda id: AttachmentMetadata(id, "image/png" if id == 4 else "image/jpeg", 3)
    return result


class TranscriptVisionTests(unittest.IsolatedAsyncioTestCase):
    def test_parts_order_and_deleted_placeholder(self):
        message = Message(content="Question")
        attachments = [SimpleNamespace(id=4, media_type="image/png", status="ready"),
                       SimpleNamespace(id=7, media_type="image/jpeg", status="failed")]
        item = user_message_item(message, attachments)
        self.assertEqual(item.parts, (TextPart("Question"), ImagePart(4, "image/png"), TextPart("[图片附件 7 已移除，图片内容不可用]")))
        self.assertEqual(user_text_item("old").parts, (TextPart("old"),))

    async def test_batch_history_keeps_image_only_and_unavailable_only_messages(self):
        messages = [Message(id=id, role=MessageRole.USER, content="", status=MessageStatus.COMPLETED) for id in (2, 3)]
        loader = AsyncMock(return_value={
            2: [SimpleNamespace(id=4, media_type="image/png", status="ready")],
            3: [SimpleNamespace(id=7, media_type="image/jpeg", status="deleted")],
        })
        session = SimpleNamespace(scalars=AsyncMock())
        with patch("app.services.attachments.load_message_attachments", loader):
            items = await build_message_history_transcript(session=session, messages=messages)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].parts, (ImagePart(4, "image/png"),))
        self.assertIsInstance(items[1].parts[0], TextPart)
        loader.assert_awaited_once_with(session, [2, 3])
        session.scalars.assert_not_awaited()

    def test_all_three_content_mappings(self):
        transcript = image_transcript()
        images = {4: "YWJj", 7: "YWJj"}
        chat = openai._transcript_to_openai_messages(transcript, images)[0]["content"]
        self.assertEqual([p["type"] for p in chat], ["text", "image_url", "image_url"])
        self.assertEqual(chat[1]["image_url"]["url"], "data:image/png;base64,YWJj")
        responses = openai_responses._transcript_to_responses_input(transcript, images)[0]["content"]
        self.assertEqual([p["type"] for p in responses], ["input_text", "input_image", "input_image"])
        history = anthropic._transcript_to_anthropic_history(transcript, images)
        payload = anthropic._messages_payload(model="test", messages=history, temperature=None, max_tokens=20, stream=False)
        blocks = payload["messages"][0]["content"]
        self.assertEqual([p["type"] for p in blocks], ["text", "image", "image"])
        self.assertEqual(blocks[1]["source"], {"type": "base64", "media_type": "image/png", "data": "YWJj"})

    def test_vision_adapter_capability(self):
        for adapter in ("openai_chat_completions", "openai_responses", "anthropic_messages"):
            self.assertTrue(adapter_supports(adapter, "vision"))
        self.assertFalse(adapter_supports("google_gemini_generate_content", "vision"))


class VisionBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.budget = VisionBufferBudget(256 * 1024 * 1024)
        self.patcher = patch.object(vision_budget, "get_vision_buffer_budget", return_value=self.budget)
        self.patcher.start()

    async def asyncTearDown(self):
        self.patcher.stop()
        self.assertEqual(self.budget.used_bytes, 0)

    async def test_admission_precedes_any_file_read_and_fails_without_waiting(self):
        self.budget.max_bytes = 1
        attachment_reader = reader()
        context = VisionRequestContext("openai_chat_completions", attachment_reader)
        with self.assertRaises(AppError) as raised:
            await context.prepare(image_transcript())
        self.assertEqual(raised.exception.code, "VISION_CAPACITY_EXCEEDED")
        attachment_reader.read.assert_not_awaited()
        context.close()

    async def test_all_metadata_budget_checked_before_file_read(self):
        attachment_reader = reader()
        context = VisionRequestContext("openai_chat_completions", attachment_reader)
        context.request_limit = 1
        with self.assertRaises(AppError) as raised:
            await context.prepare(image_transcript())
        self.assertEqual(raised.exception.code, "VISION_REQUEST_TOO_LARGE")
        attachment_reader.read.assert_not_awaited()
        context.close()

    async def test_adapter_image_count_includes_history_and_duplicate_blocks_before_read(self):
        repeated = CanonicalTranscriptItem(kind="user_text", parts=(ImagePart(4, "image/png"),))
        for adapter, count in (("anthropic_messages", 100), ("openai_chat_completions", 500), ("openai_responses", 500)):
            with self.subTest(adapter=adapter):
                attachment_reader = reader()
                context = VisionRequestContext(adapter, attachment_reader)
                with self.assertRaises(AppError) as raised:
                    await context.prepare([repeated] * (count + 1))
                self.assertEqual(raised.exception.code, "VISION_CONTEXT_LIMIT")
                self.assertEqual(raised.exception.status_code, 413)
                attachment_reader.metadata.assert_not_awaited()
                attachment_reader.read.assert_not_awaited()
                context.close()

    async def test_anthropic_rejects_oversized_metadata_before_read_and_accepts_boundary(self):
        attachment_reader = reader()
        attachment_reader.metadata.side_effect = lambda id: AttachmentMetadata(id, "image/png", 3, width=8001, height=1)
        transcript = [CanonicalTranscriptItem(kind="user_text", parts=(ImagePart(4, "image/png"),))]
        context = VisionRequestContext("anthropic_messages", attachment_reader)
        with self.assertRaises(AppError) as raised:
            await context.prepare(transcript)
        self.assertEqual(raised.exception.code, "VISION_CONTEXT_LIMIT")
        attachment_reader.read.assert_not_awaited()
        self.assertEqual(self.budget.used_bytes, 0)
        attachment_reader.metadata.side_effect = lambda id: AttachmentMetadata(id, "image/png", 3, width=8000, height=1)
        await context.prepare(transcript)
        attachment_reader.read.assert_awaited_once_with(4)
        context.close()

    async def test_custom_adapter_count_and_dimension_limits_are_applied_before_read(self):
        settings = SimpleNamespace(openai_chat_max_images=1, openai_chat_max_image_dimension=1000)
        attachment_reader = reader()
        with patch.object(vision_budget, "get_settings", return_value=settings):
            context = VisionRequestContext("openai_chat_completions", attachment_reader)
            with self.assertRaises(AppError):
                await context.prepare(image_transcript())
            attachment_reader.read.assert_not_awaited()
            attachment_reader.metadata.side_effect = lambda id: AttachmentMetadata(id, "image/png", 3, width=1001, height=1)
            with self.assertRaises(AppError) as raised:
                await context.prepare([CanonicalTranscriptItem(kind="user_text", parts=(ImagePart(4, "image/png"),))])
        self.assertEqual(raised.exception.code, "VISION_CONTEXT_LIMIT")
        attachment_reader.read.assert_not_awaited()
        context.close()

    async def test_encoding_full_releases_resident_lease_via_wrapper(self):
        @vision_request("openai_chat_completions")
        async def reply(**kwargs):
            self.fail("Encoding admission must fail first")
        with patch.object(vision_budget, "_encoding_active", 999):
            with self.assertRaises(AppError) as raised:
                await reply(transcript=image_transcript(), attachment_reader=reader())
        self.assertEqual(raised.exception.code, "VISION_ENCODING_BUSY")

    async def test_read_failure_and_cancel_release_lease(self):
        @vision_request("openai_chat_completions")
        async def reply(**kwargs):
            return "unused"
        for error in (AppError(404, "ATTACHMENT_FILE_MISSING", "missing"), asyncio.CancelledError()):
            attachment_reader = reader()
            attachment_reader.read.side_effect = error
            with self.assertRaises(type(error)):
                await reply(transcript=image_transcript(), attachment_reader=attachment_reader)
            self.assertEqual(self.budget.used_bytes, 0)

    async def test_stream_holds_lease_until_real_cleanup_exits(self):
        cleanup_started = asyncio.Event()
        cleanup_allowed = asyncio.Event()
        @vision_request("openai_chat_completions")
        async def reply(**kwargs):
            try:
                yield {"type": "content", "content": "x"}
            finally:
                cleanup_started.set()
                await cleanup_allowed.wait()
        iterator = reply(transcript=image_transcript(), attachment_reader=reader())
        await anext(iterator)
        self.assertGreater(self.budget.used_bytes, 0)
        task = asyncio.create_task(iterator.aclose())
        await cleanup_started.wait()
        self.assertGreater(self.budget.used_bytes, 0)
        cleanup_allowed.set()
        await task

    async def test_growing_tool_result_reserves_before_request_copy(self):
        context = VisionRequestContext("openai_chat_completions", reader())
        await context.prepare(image_transcript())
        previous = self.budget.used_bytes
        self.budget.max_bytes = previous
        with self.assertRaises(AppError) as raised:
            context.check_request_data({"tool": "a" * (2 * 1024 * 1024)})
        self.assertEqual(raised.exception.code, "VISION_CAPACITY_EXCEEDED")
        self.assertEqual(self.budget.used_bytes, previous)
        context.close()

    async def test_actual_payload_counts_tool_results_and_utf8(self):
        context = VisionRequestContext("openai_chat_completions", None)
        context.request_limit = 100
        with self.assertRaises(AppError) as raised:
            context.check_payload({"messages": [{"role": "tool", "content": "图" * 40}]})
        self.assertEqual(raised.exception.code, "VISION_REQUEST_TOO_LARGE")

    async def test_total_request_deadline_returns_stable_model_timeout(self):
        with patch.object(vision_budget, "get_settings", return_value=SimpleNamespace(vision_request_timeout_seconds=0.001)):
            with self.assertRaises(AppError) as raised:
                async with vision_budget.provider_timeout():
                    await asyncio.Event().wait()
        self.assertEqual(raised.exception.code, "MODEL_TIMEOUT")
        self.assertEqual(raised.exception.status_code, 504)

    async def test_multiple_runs_compete_for_resident_not_encoding_budget(self):
        first = VisionRequestContext("openai_chat_completions", reader())
        await first.prepare(image_transcript())
        self.budget.max_bytes = self.budget.used_bytes
        second = VisionRequestContext("openai_chat_completions", reader())
        with self.assertRaises(AppError):
            await second.prepare(image_transcript())
        first.close()
        await second.prepare(image_transcript())
        second.close()

    async def test_responses_tool_round_retains_images_and_reader_only_encodes_once(self):
        attachment_reader = reader()
        outputs = [{"output": [{"type": "function_call", "call_id": "c1", "name": "lookup", "arguments": "{}"}]},
                   {"output": [{"type": "message", "content": [{"type": "output_text", "text": "done"}]}]}]
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.post.side_effect = [httpx.Response(200, json=data) for data in outputs]
        async def execute(*args):
            self.assertGreater(self.budget.used_bytes, 0)
            return "result"
        key = ApiKey(key_encrypted="secret", base_url=None)
        with patch.object(openai_responses, "decrypt_text", return_value="key"), patch.object(openai_responses.httpx, "AsyncClient", return_value=client):
            result = await openai_responses.create_openai_responses_reply(
                api_key=key, model="test", transcript=image_transcript(), temperature=None,
                max_tokens=30, tools=[{"type": "function", "function": {"name": "lookup", "parameters": {}}}],
                tool_executor=execute, attachment_reader=attachment_reader,
            )
        self.assertEqual(result, "done")
        self.assertEqual(attachment_reader.read.await_count, 2)
        second = client.post.call_args_list[1].kwargs["json"]["input"]
        self.assertEqual(second[0]["content"][1]["type"], "input_image")
        self.assertEqual(second[-1]["type"], "function_call_output")

    async def test_chat_and_anthropic_nonstream_pass_image_blocks_to_http(self):
        cases = [
            (openai, openai.create_openai_reply, {"choices": [{"message": {"content": "done"}}]}, "image_url"),
            (anthropic, anthropic.create_anthropic_reply, {"content": [{"type": "text", "text": "done"}]}, "image"),
        ]
        for module, function, response_data, image_type in cases:
            with self.subTest(provider=module.__name__):
                client = AsyncMock()
                client.__aenter__.return_value = client
                client.post.return_value = httpx.Response(200, json=response_data)
                with patch.object(module, "decrypt_text", return_value="key"), patch.object(module.httpx, "AsyncClient", return_value=client):
                    result = await function(api_key=ApiKey(key_encrypted="secret"), model="test", transcript=image_transcript(),
                                            temperature=None, max_tokens=30, attachment_reader=reader())
                self.assertEqual(result, "done")
                content = client.post.call_args.kwargs["json"]["messages"][0]["content"]
                self.assertEqual(content[1]["type"], image_type)
                self.assertEqual(self.budget.used_bytes, 0)

    async def test_streaming_all_three_providers_keep_image_history_after_tools(self):
        cases = [(openai, openai.stream_openai_reply, "_stream_completion_round", "messages", "image_url"),
                 (anthropic, anthropic.stream_anthropic_reply, "_stream_completion_round", "messages", "image"),
                 (openai_responses, openai_responses.stream_openai_responses_reply, "_stream_response_round", "input", "input_image")]
        for module, function, round_name, history_key, image_type in cases:
            with self.subTest(provider=module.__name__):
                requests = []
                async def round_stream(client, **kwargs):
                    payload = kwargs["payload"]
                    requests.append(payload)
                    self.assertEqual(payload[history_key][0]["content"][1]["type"], image_type)
                    if len(requests) == 1:
                        if module is openai:
                            yield {"type": "tool_calls", "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "lookup", "arguments": "{}"}}]}
                        elif module is anthropic:
                            blocks = [{"type": "tool_use", "id": "c1", "name": "lookup", "input": {}}]
                            yield {"type": "tool_uses", "tool_uses": blocks, "content_blocks": blocks}
                        else:
                            yield {"type": "done", "output": [{"type": "function_call", "call_id": "c1", "name": "lookup", "arguments": "{}"}]}
                    else:
                        yield {"type": "content", "content": "done"}
                        yield {"type": "done", "content": "done", "output": []}
                async def execute(*args):
                    self.assertGreater(self.budget.used_bytes, 0)
                    return "result"
                attachment_reader = reader()
                with patch.object(module, "decrypt_text", return_value="key"), patch.object(module, round_name, round_stream):
                    events = [event async for event in function(api_key=ApiKey(key_encrypted="secret"), model="test",
                              transcript=image_transcript(), temperature=None, max_tokens=30,
                              tools=[{"type": "function", "function": {"name": "lookup", "parameters": {}}}],
                              tool_executor=execute, attachment_reader=attachment_reader)]
                self.assertEqual(len(requests), 2)
                self.assertTrue(any(event.get("content") == "done" for event in events))
                self.assertEqual(attachment_reader.read.await_count, 2)
                self.assertEqual(self.budget.used_bytes, 0)


if __name__ == "__main__":
    unittest.main()
