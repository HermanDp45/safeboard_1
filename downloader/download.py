import hashlib
import json
import os
import re
import sys
from pathlib import Path


def checksum(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def cached_model(path, model_id, revision):
    try:
        manifest = json.loads((path / ".ready.json").read_text())
        if manifest["model_id"] != model_id or manifest["revision"] != revision:
            return False
        files = manifest["files"]
        if not {"config.json", "tokenizer.json"} <= files.keys():
            return False
        if not any(name.endswith(".safetensors") for name in files):
            return False
        return all(
            Path(name).name == name
            and (path / name).is_file()
            and checksum(path / name) == digest
            for name, digest in files.items()
        )
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return False


def download_model(model_id, revision, root, token=None):
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("MODEL_REVISION must be a full commit hash")
    path = root / revision
    if cached_model(path, model_id, revision):
        print("model_cache_hit", flush=True)
        return path

    from huggingface_hub import snapshot_download

    path.mkdir(parents=True, exist_ok=True)
    force_download = (path / ".ready.json").exists()
    (path / ".ready.json").unlink(missing_ok=True)
    snapshot_download(
        repo_id=model_id,
        revision=revision,
        local_dir=path,
        token=token or False,
        force_download=force_download,
        allow_patterns=["*.json", "*.safetensors", "*.txt", "*.model", "LICENSE"],
    )
    files = {
        file.name: checksum(file)
        for file in sorted(path.iterdir())
        if file.is_file() and not file.name.startswith(".")
    }
    if not {"config.json", "tokenizer.json"} <= files.keys():
        raise RuntimeError("Model configuration or tokenizer is missing")
    if not any(name.endswith(".safetensors") for name in files):
        raise RuntimeError("Model weights are missing")

    # The main container must never see an interrupted download as ready.
    pending = path / ".ready.tmp"
    pending.write_text(
        json.dumps({"model_id": model_id, "revision": revision, "files": files})
    )
    pending.replace(path / ".ready.json")
    print("model_download_complete", flush=True)
    return path


if __name__ == "__main__":
    try:
        download_model(
            os.environ["MODEL_ID"],
            os.environ["MODEL_REVISION"],
            Path(os.environ.get("MODEL_ROOT", "/models")),
            os.environ.get("HF_TOKEN"),
        )
    except Exception as error:  # noqa: BLE001 -- exception messages may contain credentials
        print(f"model_download_failed error={type(error).__name__}", file=sys.stderr)
        sys.exit(1)
