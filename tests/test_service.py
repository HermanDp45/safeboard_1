import asyncio
import io
import json
import logging
import os
import unittest
from unittest.mock import patch

from service import MetadataFormatter, PrivateAPI


class PrivateAPITests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"VLLM_API_KEY": "test-secret"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.output = io.StringIO()
        self.logger = logging.getLogger("inference.audit")
        self.previous = (
            self.logger.handlers[:],
            self.logger.level,
            self.logger.propagate,
        )
        handler = logging.StreamHandler(self.output)
        handler.setFormatter(MetadataFormatter())
        self.logger.handlers = [handler]
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False
        self.addCleanup(self.restore_logger)

    def restore_logger(self):
        self.logger.handlers, self.logger.level, self.logger.propagate = self.previous

    async def request(
        self, path="/v1/chat/completions", headers=None, app=None, method="POST"
    ):
        async def respond(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"private answer"})

        messages = []

        async def send(message):
            messages.append(message)

        async def receive():
            return {"type": "http.request", "body": b"private prompt"}

        scope = {
            "type": "http",
            "method": method,
            "path": path,
            "query_string": b"private=query",
            "headers": headers or [],
        }
        await PrivateAPI(app or respond)(scope, receive, send)
        return messages

    def auth(self):
        return [(b"authorization", b"Bearer test-secret")]

    async def test_success_logs_metadata_only(self):
        messages = await self.request(headers=self.auth())
        self.assertEqual(messages[0]["status"], 200)
        event = json.loads(self.output.getvalue())
        self.assertEqual(event["status"], 200)
        self.assertEqual(len(event["request_id"]), 32)
        for secret in ("private", "test-secret", "Bearer", "query"):
            self.assertNotIn(secret, self.output.getvalue())

    async def test_missing_wrong_and_duplicate_tokens_are_rejected(self):
        for headers in ([], [(b"authorization", b"Bearer wrong")], self.auth() * 2):
            with self.subTest(headers=headers):
                messages = await self.request(headers=headers)
                self.assertEqual(messages[0]["status"], 401)

    async def test_alternative_routes_and_methods_are_closed(self):
        for path in (
            "/invocations",
            "/metrics",
            "/docs",
            "/v1/completions",
            "/v1/models/",
        ):
            with self.subTest(path=path):
                messages = await self.request(path=path, headers=self.auth())
                self.assertEqual(messages[0]["status"], 404)
        messages = await self.request(path="/health")
        self.assertEqual(messages[0]["status"], 404)

    async def test_health_does_not_require_token_or_produce_audit_noise(self):
        messages = await self.request(path="/health", method="GET")
        self.assertEqual(messages[0]["status"], 200)
        self.assertEqual(self.output.getvalue(), "")

    async def test_stream_chunks_are_forwarded_immediately(self):
        delivered = []

        async def stream(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            for index in range(3):
                await send(
                    {
                        "type": "http.response.body",
                        "body": str(index).encode(),
                        "more_body": index < 2,
                    }
                )
                self.assertEqual(len(delivered), index + 2)

        async def send(message):
            delivered.append(message)

        await PrivateAPI(stream)(
            {
                "type": "http",
                "method": "POST",
                "path": "/v1/chat/completions",
                "headers": self.auth(),
            },
            None,
            send,
        )
        self.assertEqual([m["body"] for m in delivered[1:]], [b"0", b"1", b"2"])

    async def test_exception_message_is_not_logged_or_returned(self):
        async def fail(scope, receive, send):
            raise ValueError("private prompt and test-secret")

        messages = await self.request(headers=self.auth(), app=fail)
        self.assertEqual(messages[0]["status"], 500)
        self.assertNotIn("private", str(messages) + self.output.getvalue())
        self.assertIn("ValueError", self.output.getvalue())

    async def test_cancellation_is_logged_without_content(self):
        async def cancel(scope, receive, send):
            raise asyncio.CancelledError("private")

        with self.assertRaises(asyncio.CancelledError):
            await self.request(headers=self.auth(), app=cancel)
        self.assertEqual(json.loads(self.output.getvalue())["status"], 499)
        self.assertNotIn("private", self.output.getvalue())

    def test_empty_token_prevents_startup(self):
        with (
            patch.dict(os.environ, {"VLLM_API_KEY": ""}),
            self.assertRaises(RuntimeError),
        ):
            PrivateAPI(None)

    def test_runtime_formatter_discards_message_arguments_and_traceback(self):
        try:
            raise ValueError("private exception")
        except ValueError:
            import sys

            record = logging.LogRecord(
                "vllm.engine",
                logging.ERROR,
                "engine.py",
                42,
                "private prompt %s",
                ("private answer",),
                sys.exc_info(),
            )
        result = MetadataFormatter().format(record)
        self.assertNotIn("private", result)
        self.assertEqual(json.loads(result)["error_type"], "ValueError")


if __name__ == "__main__":
    unittest.main()
