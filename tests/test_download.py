import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from downloader.download import cached_model, download_model


class DownloadTests(unittest.TestCase):
    revision = "a" * 40

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.snapshot = Mock(side_effect=self.write_model)
        self.hub = patch.dict(
            "sys.modules",
            {
                "huggingface_hub": SimpleNamespace(snapshot_download=self.snapshot),
            },
        )
        self.hub.start()
        self.addCleanup(self.hub.stop)

    def write_model(self, **kwargs):
        path = kwargs["local_dir"]
        for name in ("config.json", "tokenizer.json", "model.safetensors"):
            (path / name).write_bytes(b"model data")

    def download(self, revision=None):
        return download_model("Qwen/test", revision or self.revision, self.root)

    def test_warm_cache_does_not_contact_hub(self):
        path = self.download()
        self.snapshot.reset_mock()
        self.snapshot.side_effect = AssertionError("Network is unavailable")
        self.assertEqual(self.download(), path)
        self.snapshot.assert_not_called()

    def test_corrupt_file_forces_download(self):
        path = self.download()
        (path / "model.safetensors").write_bytes(b"other data")
        self.download()
        self.assertTrue(self.snapshot.call_args.kwargs["force_download"])
        self.assertTrue(cached_model(path, "Qwen/test", self.revision))

    def test_interrupted_download_has_no_ready_marker(self):
        def interrupt(**kwargs):
            (kwargs["local_dir"] / "config.json").write_text("partial")
            raise TimeoutError("Interrupted")

        self.snapshot.side_effect = interrupt
        with self.assertRaises(TimeoutError):
            self.download()
        self.assertFalse((self.root / self.revision / ".ready.json").exists())
        self.snapshot.side_effect = self.write_model
        path = self.download()
        self.assertFalse(self.snapshot.call_args.kwargs["force_download"])
        self.assertTrue(cached_model(path, "Qwen/test", self.revision))

    def test_missing_weights_do_not_mark_model_ready(self):
        self.snapshot.side_effect = lambda **kwargs: (
            kwargs["local_dir"] / "config.json"
        ).write_text("{}")
        with self.assertRaises(RuntimeError):
            self.download()
        self.assertFalse((self.root / self.revision / ".ready.json").exists())

    def test_revision_changes_use_a_separate_directory(self):
        first = self.download()
        second = self.download("b" * 40)
        self.assertNotEqual(first, second)
        self.assertTrue(first.is_dir())
        self.assertEqual(self.snapshot.call_count, 2)

    def test_invalid_revision_is_rejected_before_download(self):
        with self.assertRaises(ValueError):
            self.download("../model")
        self.snapshot.assert_not_called()

    def test_invalid_manifest_is_not_a_cache_hit(self):
        path = self.download()
        manifest = json.loads((path / ".ready.json").read_text())
        manifest["files"] = {"../outside": "hash"}
        (path / ".ready.json").write_text(json.dumps(manifest))
        self.assertFalse(cached_model(path, "Qwen/test", self.revision))


if __name__ == "__main__":
    unittest.main()
