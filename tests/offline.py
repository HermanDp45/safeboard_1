import os
import secrets
import subprocess
from pathlib import Path


def main():
    state = subprocess.run(
        ["docker", "inspect", "safeboard", "--format", "{{.State.Running}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if state.stdout.strip() == "true":
        raise SystemExit("Stop Minikube first: minikube stop -p safeboard")

    root = Path(__file__).resolve().parents[1]
    settings = {}
    for line in (root / "k8s/kustomization.yaml").read_text().splitlines():
        key, _, value = line.strip().removeprefix("- ").partition("=")
        if key in ("MODEL_ID", "MODEL_REVISION"):
            settings[key] = value
    env = os.environ | settings | {"VLLM_API_KEY": secrets.token_urlsafe(32)}
    download = [
        "docker",
        "run",
        "--rm",
        "--cpus=1",
        "--memory=512m",
        "--memory-swap=512m",
        "--mount",
        "type=volume,source=safeboard-model,target=/models",
        "-e",
        "MODEL_ID",
        "-e",
        "MODEL_REVISION",
    ]
    for network in ([], ["--network=none"]):
        subprocess.run(
            [*download, *network, "safeboard-downloader:cpu"], env=env, check=True
        )

    subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--detach",
            "--name",
            "safeboard-offline",
            "--network=none",
            "--cpus=2",
            "--memory=4608m",
            "--memory-swap=4608m",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,size=1g,uid=10001,gid=10001",
            "--shm-size=256m",
            "--mount",
            "type=volume,source=safeboard-model,target=/models,readonly",
            "-e",
            "VLLM_API_KEY",
            "-e",
            "OMP_NUM_THREADS=2",
            "-e",
            "VLLM_CPU_OMP_THREADS_BIND=nobind",
            "-e",
            "VLLM_HOST_IP=127.0.0.1",
            "safeboard-vllm:cpu",
            "/models/" + settings["MODEL_REVISION"],
            "--config",
            "/etc/vllm/serve.yaml",
        ],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    try:
        result = subprocess.run(
            ["docker", "exec", "-i", "safeboard-offline", "python", "-"],
            input="""
import json, os, time, urllib.request
for attempt in range(90):
    try:
        urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).close()
        break
    except OSError:
        time.sleep(2)
else:
    raise SystemExit('Offline server did not become ready')
payload = {'model': 'qwen', 'messages': [{'role': 'user', 'content':
           'Repeat: offline_probe_972a61'}], 'max_tokens': 24, 'temperature': 0}
request = urllib.request.Request(
    'http://127.0.0.1:8000/v1/chat/completions', json.dumps(payload).encode(),
    headers={'Content-Type': 'application/json',
             'Authorization': 'Bearer ' + os.environ['VLLM_API_KEY']})
with urllib.request.urlopen(request, timeout=30) as response:
    content = json.load(response)['choices'][0]['message']['content']
    assert content, 'Empty completion'
    print(content)
""",
            capture_output=True,
            text=True,
            timeout=240,
            check=True,
        )
        logs = subprocess.run(
            ["docker", "logs", "safeboard-offline"],
            capture_output=True,
            text=True,
            check=True,
        )
        output = logs.stdout + logs.stderr
        for value in (
            "offline_probe_972a61",
            result.stdout.strip(),
            env["VLLM_API_KEY"],
        ):
            assert value not in output, "Content leaked into offline server logs"
        print("Passed: cached downloader and inference with --network=none")
    finally:
        subprocess.run(
            ["docker", "stop", "--time", "10", "safeboard-offline"],
            check=False,
            capture_output=True,
        )


if __name__ == "__main__":
    main()
