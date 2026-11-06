import asyncio
import hmac
import json
import logging
import os
import time
import uuid


class MetadataFormatter(logging.Formatter):
    def format(self, record):
        fields = {
            "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
        }
        if record.name == "inference.audit":
            for key in ("request_id", "status", "duration_ms", "error_type"):
                if hasattr(record, key):
                    fields[key] = getattr(record, key)
        else:
            # Runtime messages and tracebacks can contain request data.
            fields["line"] = record.lineno
            if record.exc_info and record.exc_info[0] is not None:
                fields["error_type"] = record.exc_info[0].__name__
        return json.dumps(fields, separators=(",", ":"))


class PrivateAPI:
    routes = frozenset(
        {
            ("GET", "/health"),
            ("GET", "/v1/models"),
            ("POST", "/v1/chat/completions"),
        }
    )

    def __init__(self, app):
        self.app = app
        token = os.environ.get("VLLM_API_KEY", "")
        if not token.strip():
            raise RuntimeError("VLLM_API_KEY must be set")
        self.token = token.encode()
        self.logger = logging.getLogger("inference.audit")

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            return await self.app(scope, receive, send)
        if scope["type"] == "websocket":
            return await send({"type": "websocket.close", "code": 1008})
        if scope["type"] != "http":
            return

        started = time.monotonic()
        status = 500
        response_started = False
        request_id = uuid.uuid4().hex

        async def audit_send(message):
            nonlocal status, response_started
            if message["type"] == "http.response.start":
                status = message["status"]
                response_started = True
            await send(message)

        async def reject(code, message):
            await audit_send(
                {
                    "type": "http.response.start",
                    "status": code,
                    "headers": [(b"content-type", b"application/json")],
                }
            )
            await audit_send(
                {
                    "type": "http.response.body",
                    "body": json.dumps({"error": message}).encode(),
                }
            )

        try:
            if (scope["method"], scope["path"]) not in self.routes:
                return await reject(404, "Not found")
            if scope["path"] != "/health":
                headers = [
                    v for k, v in scope["headers"] if k.lower() == b"authorization"
                ]
                scheme, _, token = (
                    headers[0].partition(b" ") if len(headers) == 1 else (b"", b"", b"")
                )
                if scheme.lower() != b"bearer" or not hmac.compare_digest(
                    token, self.token
                ):
                    return await reject(401, "Unauthorized")
            await self.app(scope, receive, audit_send)
        except asyncio.CancelledError:
            status = 499
            raise
        except Exception as error:  # noqa: BLE001 -- sanitize errors at the API boundary
            status = 500
            self.logger.error(
                "request_failed",
                extra={
                    "request_id": request_id,
                    "error_type": type(error).__name__,
                },
            )
            if not response_started:
                await reject(500, "Internal server error")
            else:
                raise RuntimeError("Request failed") from None
        finally:
            if scope["path"] != "/health":
                self.logger.info(
                    "request",
                    extra={
                        "request_id": request_id,
                        "status": status,
                        "duration_ms": round((time.monotonic() - started) * 1000, 2),
                    },
                )
