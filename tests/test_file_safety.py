import asyncio
import importlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from queue import Queue
from threading import Event, Thread
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
            cls.jobshandler = importlib.import_module("ydl_server.jobshandler")
            cls.db = importlib.import_module("ydl_server.db")

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

    def make_request(self, data, manager=None, content_type="application/json"):
        async def read_data():
            return data

        return SimpleNamespace(
            headers={"Content-Type": content_type},
            json=read_data,
            form=read_data,
            app=SimpleNamespace(state=SimpleNamespace(jobshandler=manager or Mock(), ydlhandler=Mock())),
        )

    def start_manager(self, manager=None):
        (self.root / "state").mkdir(exist_ok=True)
        self.db.JobsDB.init()
        manager = manager or self.jobshandler.JobsHandler(self.config.app_config)
        downloads = Queue()
        manager.thread = Thread(target=manager.worker, args=(downloads,))
        manager.thread.start()

        def cleanup():
            manager.finish()
            manager.thread.join(timeout=3)
            self.assertFalse(manager.thread.is_alive())

        self.addCleanup(cleanup)
        return manager, downloads

    def make_job(self, force_generic=False):
        return self.db.Job(
            "video", self.db.Job.PENDING, "", self.db.JobType.YDL_DOWNLOAD,
            "video/best", ["https://example.com/video"], force_generic_extractor=force_generic,
        )

    def prepare_recovery(self):
        (self.root / "state").mkdir()
        self.db.JobsDB.init()
        database = self.db.JobsDB(readonly=False)
        self.addCleanup(database.close)
        handler = self.ydlhandler.YdlHandler.__new__(self.ydlhandler.YdlHandler)
        handler.app_config = self.config.app_config
        handler.jobshandler = Mock()
        return database, handler

    def test_restart_recovers_all_jobs_above_history_limit(self):
        database, handler = self.prepare_recovery()
        expected_ids = []
        for _ in range(105):
            job = self.make_job()
            database.insert_job(job)
            expected_ids.append(job.id)
        with patch.dict(self.config.app_config["ydl_server"], {"max_log_entries": 100}):
            handler.resume_pending()
        resumed = [call.args[0] for call in handler.jobshandler.put.call_args_list]
        self.assertEqual([job.id for action, job in resumed], expected_ids)
        self.assertTrue(all(action == self.db.Actions.RESUME for action, job in resumed))

    def test_restart_recovers_old_unfinished_jobs_among_newer_history(self):
        database, handler = self.prepare_recovery()
        unfinished_ids = []
        for status in (self.db.Job.PENDING, self.db.Job.RUNNING):
            job = self.make_job()
            job.status = status
            database.insert_job(job)
            unfinished_ids.append(job.id)
        database.conn.execute("UPDATE jobs SET last_update = '2000-01-01 00:00:00'")
        database.conn.commit()
        for status in (self.db.Job.COMPLETED, self.db.Job.FAILED, self.db.Job.ABORTED, self.db.Job.SCHEDULED):
            job = self.make_job()
            job.status = status
            database.insert_job(job)
        with patch.dict(self.config.app_config["ydl_server"], {"max_log_entries": 2}):
            handler.resume_pending()
        resumed = [call.args[0][1] for call in handler.jobshandler.put.call_args_list]
        self.assertEqual([job.id for job in resumed], unfinished_ids)
        self.assertTrue(all(job.status == self.db.Job.PENDING for job in resumed))

    def test_restart_preserves_download_and_cut_job_parameters(self):
        database, handler = self.prepare_recovery()
        download = self.make_job(force_generic=True)
        download.name = "Custom download"
        download.format = "profile/podcast,alias/thumbnails"
        download.url = ["https://example.com/first", "https://example.com/second"]
        download.extra_params = {"title": "Custom title", "schedule_attempts": 2}
        cut = self.db.Job("Cut video", self.db.Job.RUNNING, "old log", self.db.JobType.FFMPEG_CUT,
                          None, ["video.mp4"], pid=12345,
                          extra_params={"start": "10", "end": "20", "mode": "fast", "output": "clip.mp4"})
        for job in (download, cut):
            database.insert_job(job)
        handler.resume_pending()
        resumed = [call.args[0][1] for call in handler.jobshandler.put.call_args_list]
        self.assertEqual(len(resumed), 2)
        for original, recovered in zip((download, cut), resumed, strict=True):
            self.assertEqual(recovered.id, original.id)
            self.assertEqual(recovered.name, original.name)
            self.assertEqual(recovered.type, original.type)
            self.assertEqual(recovered.format, original.format)
            self.assertEqual(recovered.url, original.url)
            self.assertEqual(recovered.extra_params, original.extra_params)
            self.assertIs(recovered.force_generic_extractor, original.force_generic_extractor)
            self.assertEqual(recovered.status, self.db.Job.PENDING)
            self.assertEqual(recovered.pid, 0)

    def test_malformed_download_requests_are_rejected(self):
        invalid = [None, [], {}, {"url": ""}, {"url": 1}, {"urls": "url"}, {"urls": [1]}, {"urls": [""]}]
        for key, value in (
            ("force_generic_extractor", "false"), ("force_generic_extractor", "true"),
            ("force_generic_extractor", 0), ("force_generic_extractor", None),
            ("aliases", [1]), ("aliases", {}), ("profile", []), ("format", {}),
            ("audio_format", False), ("extra_params", []), ("extra_params", None),
        ):
            invalid.append({"url": "https://example.com/video", key: value})
        for data in invalid:
            with self.subTest(data=data):
                request = self.make_request(data)
                response = asyncio.run(self.views.api_queue_download(request))
                self.assertEqual(response.status_code, 400)
                request.app.state.jobshandler.insert_and_wait.assert_not_called()

    def test_invalid_json_is_rejected(self):
        async def invalid_json():
            raise json.JSONDecodeError("invalid", "{", 0)

        request = self.make_request({})
        request.json = invalid_json
        response = asyncio.run(self.views.api_queue_download(request))
        self.assertEqual(response.status_code, 400)
        request.app.state.jobshandler.insert_and_wait.assert_not_called()

    def test_json_and_form_booleans_are_normalized(self):
        for content_type, supplied, expected in (
            ("application/json", False, False), ("application/json", True, True),
            ("application/x-www-form-urlencoded; charset=UTF-8", "false", False),
            ("application/x-www-form-urlencoded", "true", True),
        ):
            with self.subTest(content_type=content_type, supplied=supplied):
                request = self.make_request({"url": "https://example.com/video", "force_generic_extractor": supplied},
                                            content_type=content_type)
                response = asyncio.run(self.views.api_queue_download(request))
                self.assertTrue(json.loads(response.body)["success"])
                job = request.app.state.jobshandler.insert_and_wait.call_args.args[0]
                self.assertIs(job.force_generic_extractor, expected)

    def test_metadata_rejects_invalid_boolean(self):
        request = self.make_request({"url": "https://example.com/video", "force_generic_extractor": "false"})
        response = asyncio.run(self.views.api_metadata_fetch(request))
        self.assertEqual(response.status_code, 400)
        request.app.state.ydlhandler.fetch_metadata.assert_not_called()

    def test_download_insertion_failures_and_timeouts_are_reported(self):
        for error in (self.jobshandler.JobInsertError("private database details"), self.jobshandler.JobInsertTimeout()):
            with self.subTest(error=type(error).__name__):
                manager = Mock()
                manager.insert_and_wait.side_effect = error
                response = asyncio.run(self.views.api_queue_download(
                    self.make_request({"url": "https://example.com/video"}, manager)))
                self.assertEqual(response.status_code, 503)
                body = json.loads(response.body)
                self.assertFalse(body["success"])
                self.assertNotIn("job_id", body)
                self.assertNotIn("private database details", response.body.decode())

    def test_api_returns_a_committed_job_id(self):
        manager, downloads = self.start_manager()
        request = self.make_request({"url": "https://example.com/video", "force_generic_extractor": True}, manager)
        response = asyncio.run(self.views.api_queue_download(request))
        body = json.loads(response.body)
        self.assertTrue(body["success"])
        self.assertGreater(body["job_id"], 0)
        database = self.db.JobsDB()
        try:
            self.assertIs(database.get_job_by_id(body["job_id"])["force_generic_extractor"], True)
        finally:
            database.close()
        self.assertEqual(downloads.get_nowait().id, body["job_id"])

    def test_waiting_for_insertion_does_not_block_the_event_loop(self):
        entered, release = Event(), Event()
        self.addCleanup(release.set)

        def blocked_insert(job):
            entered.set()
            release.wait(timeout=2)
            job.id = 42

        manager = Mock()
        manager.insert_and_wait.side_effect = blocked_insert
        request = self.make_request({"url": "https://example.com/video"}, manager)

        async def check():
            task = asyncio.create_task(self.views.api_queue_download(request))
            try:
                self.assertTrue(await asyncio.wait_for(asyncio.to_thread(entered.wait, 1), timeout=1.5))
                self.assertFalse(task.done())
            finally:
                release.set()
            response = await task
            self.assertEqual(json.loads(response.body)["job_id"], 42)

        asyncio.run(check())

    def test_cut_insertion_failure_is_reported(self):
        (self.root / "video.mp4").write_text("video")
        manager = Mock()
        manager.insert_and_wait.side_effect = self.jobshandler.JobInsertError()
        request = self.make_request({"output": "cut.mp4"}, manager)
        request.path_params = {"fname": "video.mp4"}
        response = asyncio.run(self.views.api_cut_file(request))
        self.assertEqual(response.status_code, 503)

    def test_retry_keeps_original_job_when_insertion_fails(self):
        manager = Mock()
        manager.insert_and_wait.side_effect = self.jobshandler.JobInsertError()
        request = self.make_request({}, manager)
        request.path_params = {"job_id": "1"}
        with patch.object(self.views, "JobsDB") as database:
            database.return_value.get_job_by_id.return_value = {
                "name": "video", "type": self.db.JobType.YDL_DOWNLOAD, "format": "video/best",
                "urls": ["https://example.com/video"], "extra_params": {},
            }
            response = asyncio.run(self.views.api_jobs_retry(request))
        self.assertEqual(response.status_code, 503)
        manager.put.assert_not_called()

    def test_manager_survives_bad_insertion_and_processes_following_actions(self):
        manager, downloads = self.start_manager()
        with self.assertLogs(level="ERROR"), self.assertRaises(self.jobshandler.JobInsertError) as error:
            manager.insert_and_wait(self.make_job("false"), timeout=1)
        self.assertIsInstance(error.exception.__cause__, ValueError)
        job = self.make_job()
        self.assertEqual(manager.insert_and_wait(job, timeout=1), job.id)
        self.assertGreater(job.id, 0)
        manager.put((self.db.Actions.SET_STATUS, (job.id, self.db.Job.COMPLETED)))
        manager.insert_and_wait(self.make_job(), timeout=1)
        database = self.db.JobsDB()
        try:
            self.assertEqual(database.get_job_by_id(job.id)["status"], "Completed")
        finally:
            database.close()
        self.assertEqual(downloads.qsize(), 2)
        self.assertEqual(manager.queue.unfinished_tasks, 0)
        self.assertTrue(manager.thread.is_alive())

    def test_manager_survives_failure_without_an_insertion_waiter(self):
        manager, downloads = self.start_manager()
        with self.assertLogs(level="ERROR"):
            manager.put((self.db.Actions.SET_STATUS, (1, object())))
            job = self.make_job()
            manager.insert_and_wait(job, timeout=1)
        self.assertEqual(downloads.qsize(), 1)
        self.assertEqual(manager.queue.unfinished_tasks, 0)
        self.assertTrue(manager.thread.is_alive())

    def test_timed_out_queued_insertion_is_not_processed_later(self):
        manager = self.jobshandler.JobsHandler(self.config.app_config)
        expired = self.make_job()
        with self.assertRaises(self.jobshandler.JobInsertTimeout):
            manager.insert_and_wait(expired, timeout=0)
        manager, downloads = self.start_manager(manager)
        job = self.make_job()
        manager.insert_and_wait(job, timeout=1)
        self.assertEqual(expired.id, -1)
        self.assertEqual(downloads.qsize(), 1)
        self.assertEqual(manager.queue.unfinished_tasks, 0)

    def test_failed_database_action_rolls_back_partial_changes(self):
        (self.root / "state").mkdir()
        self.db.JobsDB.init()
        database = self.db.JobsDB(readonly=False)
        self.addCleanup(database.close)
        job = self.make_job()
        database.insert_job(job)

        @self.db.with_cursor
        def failing_update(database, cursor):
            cursor.execute("UPDATE jobs SET name = ? WHERE id = ?", ("partial", job.id))
            raise ValueError("failed")

        with self.assertRaises(ValueError):
            failing_update(database)
        self.assertEqual(database.get_job_by_id(job.id)["name"], "video")
        database.set_job_name(job.id, "successful")
        self.assertEqual(database.get_job_by_id(job.id)["name"], "successful")

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
