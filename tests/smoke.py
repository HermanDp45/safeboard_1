import argparse
import json
import subprocess
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--container")
    args = parser.parse_args()
    env_file = Path(__file__).resolve().parents[1] / "k8s" / ".env"
    values = dict(
        line.split("=", 1)
        for line in env_file.read_text().splitlines()
        if line and not line.startswith("#")
    )
    token = values["VLLM_API_KEY"]
    markers = []

    def request(path, data=None, auth=token):
        headers = {"Content-Type": "application/json"}
        if auth is not None:
            headers["Authorization"] = f"Bearer {auth}"
        body = json.dumps(data).encode() if data is not None else None
        try:
            return urlopen(Request(args.url + path, body, headers), timeout=60)
        except HTTPError as error:
            return error

    with request("/health", auth=None) as response:
        assert response.status == 200, "Health check failed"
    for auth in (None, "invalid-token"):
        with request("/v1/models", auth=auth) as response:
            assert response.status == 401, "API accepted an invalid token"
    for path in ("/invocations", "/metrics", "/docs", "/v1/completions"):
        with request(path) as response:
            assert response.status == 404, f"Unexpected open route: {path}"
    with request("/v1/models") as response:
        assert response.status == 200
        model = json.load(response)["data"][0]["id"]

    marker = "privacy_probe_" + uuid.uuid4().hex
    markers.extend([marker, token])
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "Repeat this string: " + marker}],
        "max_tokens": 32,
        "temperature": 0,
    }
    with request("/v1/chat/completions", payload) as response:
        assert response.status == 200, "Completion failed"
        content = json.load(response)["choices"][0]["message"]["content"]
        assert content, "Empty completion"
        markers.append(content)

    payload.update(stream=True, max_tokens=16)
    chunks = []
    with request("/v1/chat/completions", payload) as response:
        assert response.status == 200, "Streaming failed"
        for raw in response:
            if not raw.startswith(b"data: "):
                continue
            data = raw[6:].strip()
            if data == b"[DONE]":
                break
            delta = json.loads(data)["choices"][0]["delta"].get("content")
            if delta:
                chunks.append(delta)
        else:
            raise AssertionError("Stream did not finish")
    assert chunks, "Empty stream"
    markers.append("".join(chunks))

    with request(
        "/v1/chat/completions", {"model": model, "messages": marker}
    ) as response:
        assert response.status in (400, 422), "Invalid request was accepted"
    payload["max_tokens"] = 64
    with request("/v1/chat/completions", payload) as response:
        for raw in response:
            if raw.startswith(b"data: ") and b'"content"' in raw:
                break
    time.sleep(1)

    command = (
        ["docker", "logs", args.container]
        if args.container
        else [
            "kubectl",
            "--context",
            "safeboard",
            "-n",
            "vllm-cpu",
            "logs",
            "deployment/vllm",
            "--all-containers=true",
        ]
    )
    result = subprocess.run(command, capture_output=True, text=True, check=True)
    logs = result.stdout + result.stderr
    for value in markers:
        assert value not in logs, "Request, response or token leaked into logs"
    events = [json.loads(line) for line in logs.splitlines() if line.startswith("{")]
    assert any(event.get("status") == 200 for event in events), "Missing request audit"
    print(
        "Passed: authorization, routes, completion, streaming, validation, log privacy"
    )


if __name__ == "__main__":
    main()
