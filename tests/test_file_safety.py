import asyncio
import importlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


class FileSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config_dir = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.config_dir.cleanup)
        config_path = Path(cls.config_dir.name) / "config.yml"
        config_path.write_text(json.dumps({
            "ydl_server": {"metadata_db_path": str(Path(cls.config_dir.name) / ".metadata.db")},
            "ydl_options": {"output": str(Path(cls.config_dir.name) / "%(title)s.%(ext)s")},
        }))
        with patch.dict(os.environ, {"YDL_CONFIG_PATH": str(config_path)}):
            cls.config = importlib.import_module("ydl_server.config")
            cls.views = importlib.import_module("ydl_server.views")
            cls.ydlhandler = importlib.import_module("ydl_server.ydlhandler")

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        for module in (self.config, self.views):
            patcher = patch.object(module, "get_finished_path", return_value=str(self.root) + "/")
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.dict(self.config.app_config["ydl_server"], {
            "metadata_db_path": str(self.root / "state" / "jobs.db"),
        })
        patcher.start()
        self.addCleanup(patcher.stop)

    def queue_title(self, title):
        async def request_json():
            return {"url": "https://example.com/video", "extra_params": {"title": title}}

        manager = Mock()
        request = SimpleNamespace(
            headers={"Content-Type": "application/json"},
            json=request_json,
            app=SimpleNamespace(state=SimpleNamespace(jobshandler=manager)),
        )
        return asyncio.run(self.views.api_queue_download(request)), manager

    def test_invalid_titles_are_rejected_before_insertion(self):
        for title in ("../escape", "nested/name", "nested\\name", "/absolute", ".hidden", " ", "nul\0", "line\n", 1, [], {}):
            with self.subTest(title=title):
                response, manager = self.queue_title(title)
                self.assertEqual(response.status_code, 400)
                self.assertFalse(json.loads(response.body)["success"])
                manager.insert_and_wait.assert_not_called()

    def test_literal_titles_are_accepted(self):
        for title in ("My video", "100% complete", "%(title)s", "Café", "", None):
            with self.subTest(title=title):
                response, manager = self.queue_title(title)
                self.assertTrue(json.loads(response.body)["success"])
                manager.insert_and_wait.assert_called_once()

    def test_worker_rejects_unsafe_persisted_title_before_extraction(self):
        handler = self.ydlhandler.YdlHandler.__new__(self.ydlhandler.YdlHandler)
        handler.fetch_metadata = Mock()
        job = SimpleNamespace(extra_params={"title": "../escape"})
        with self.assertRaises(self.ydlhandler.OptionsError):
            handler.download(job, {}, io.StringIO())
        handler.fetch_metadata.assert_not_called()

    def test_custom_titles_preserve_output_directory_and_escape_templates(self):
        for template in ("%(title)s.%(ext)s", "media/%(title)s.%(ext)s", str(self.root / "%(title)s.%(ext)s")):
            with self.subTest(template=template):
                handler = self.ydlhandler.YdlHandler.__new__(self.ydlhandler.YdlHandler)
                handler.app_config = {"ydl_server": {}, "ydl_options": {"output": template}}
                handler.ydl_module_name = "yt-dlp"
                handler.jobshandler = Mock()
                handler.fetch_metadata = Mock(return_value=(0, [{"title": "original"}]))
                job = SimpleNamespace(extra_params={"title": "100% %(title)s"}, url=["https://example.com/video"], id=1)
                with (
                    patch.object(self.ydlhandler, "Popen", side_effect=RuntimeError("captured")) as spawn,
                    self.assertRaisesRegex(RuntimeError, "captured"),
                ):
                    handler.download(job, {"format": "video/best"}, io.StringIO())
                command = spawn.call_args.args[0]
                output = command[command.index("--output") + 1]
                self.assertEqual(output, os.path.join(os.path.dirname(template), "100%% %%(title)s.%(ext)s"))

    def test_root_aliases_cannot_be_deleted(self):
        (self.root / "album").mkdir()
        (self.root / "alias").symlink_to(self.root, target_is_directory=True)
        for name in (".", "./", "album/..", "alias", str(self.root), "../outside"):
            with self.subTest(name=name), patch.object(self.views.shutil, "rmtree") as remove:
                response = asyncio.run(self.views.api_delete_file(SimpleNamespace(path_params={"fname": name})))
                self.assertFalse(json.loads(response.body)["success"])
                remove.assert_not_called()
                self.assertTrue(self.root.is_dir())

    def test_hidden_files_and_database_ancestors_cannot_be_deleted(self):
        (self.root / ".cache").mkdir()
        (self.root / ".cache" / "data").write_text("cache")
        (self.root / "state").mkdir()
        (self.root / "state" / "jobs.db").write_text("database")
        (self.root / "alias.db").symlink_to(self.root / "state" / "jobs.db")
        for name in (".cache", ".cache/data", "state", "state/jobs.db", "alias.db"):
            with self.subTest(name=name):
                response = asyncio.run(self.views.api_delete_file(SimpleNamespace(path_params={"fname": name})))
                self.assertEqual(response.status_code, 400)
                self.assertFalse(json.loads(response.body)["success"])
                self.assertTrue((self.root / name).exists())

    def test_media_files_and_directories_can_be_deleted(self):
        (self.root / "song.mp3").write_text("audio")
        (self.root / "album").mkdir()
        (self.root / "album" / "song.mp3").write_text("audio")
        for name in ("song.mp3", "album"):
            with self.subTest(name=name):
                response = asyncio.run(self.views.api_delete_file(SimpleNamespace(path_params={"fname": name})))
                self.assertTrue(json.loads(response.body)["success"])
                self.assertFalse((self.root / name).exists())
