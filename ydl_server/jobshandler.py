import logging
import time
from concurrent.futures import Future
from queue import Empty, Queue
from threading import Event, Thread

from ydl_server.db import Actions, Job, JobsDB

logger = logging.getLogger(__name__)


class JobInsertError(Exception):
    pass


class JobInsertTimeout(TimeoutError):
    pass


class JobsHandler:
    def __init__(self, app_config):
        self.queue = Queue()
        self.thread = None
        self.scheduler_thread = None
        self.done = False
        self.scheduler_stop = Event()
        self.app_config = app_config

    def start(self, dl_queue):
        self.thread = Thread(target=self.worker, args=(dl_queue,))
        self.thread.start()
        self.scheduler_thread = Thread(target=self.scheduler_worker)
        self.scheduler_thread.start()

    def stop(self):
        self.finish()

    def put(self, obj):
        self.queue.put(obj)

    def insert_and_wait(self, job, timeout=5):
        return self.submit_and_wait(Actions.INSERT, job, timeout)

    def submit_and_wait(self, action, job, timeout=5):
        if self.done:
            raise JobInsertError("Job manager is stopped")
        future = Future()
        self.queue.put((action, job, future))
        try:
            return future.result(timeout)
        except TimeoutError as exc:
            future.cancel()
            raise JobInsertTimeout("Timed out waiting for the job queue") from exc
        except Exception as exc:
            raise JobInsertError("Could not process job action") from exc

    def stop_scheduler(self):
        self.scheduler_stop.set()
        if self.scheduler_thread is not None:
            self.scheduler_thread.join()

    def finish(self):
        self.stop_scheduler()
        self.done = True

    def worker(self, dl_queue):
        db = JobsDB(readonly=False)
        try:
            while not self.done or not self.queue.empty():
                try:
                    item = self.queue.get(timeout=1)
                except Empty:
                    continue
                action, future, error = None, None, None
                try:
                    action, job = item[:2]
                    future = item[2] if len(item) > 2 else None
                    if future is not None and not future.set_running_or_notify_cancel():
                        continue
                    result = self.handle_action(db, dl_queue, action, job)
                except Exception as exc:
                    logger.exception("Error processing job action %s", action)
                    error = exc
                finally:
                    self.queue.task_done()
                if future is not None:
                    if error is not None:
                        future.set_exception(error)
                    else:
                        future.set_result(result)
        finally:
            db.close()

    def handle_action(self, db, dl_queue, action, job):
        if action == Actions.PURGE_LOGS:
            if db.purge_jobs():
                db.vacuum()
        elif action == Actions.INSERT:
            if db.clean_old_jobs(self.app_config["ydl_server"].get("max_log_entries", 100) - 1):
                db.vacuum()
            db.insert_job(job)
            dl_queue.put(job)
            return job.id
        elif action == Actions.UPDATE:
            db.update_job(job)
        elif action == Actions.RESUME:
            if db.update_job(job):
                dl_queue.put(job)
        elif action == Actions.ABORT:
            return db.abort_job(job)
        elif action == Actions.SET_NAME:
            job_id, name = job
            db.set_job_name(job_id, name)
        elif action == Actions.SET_LOG:
            job_id, log = job
            db.set_job_log(job_id, log)
        elif action == Actions.SET_STATUS:
            job_id, status = job
            db.set_job_status(job_id, status)
        elif action == Actions.SET_PID:
            job_id, pid = job
            db.set_job_pid(job_id, pid)
        elif action == Actions.CLEAN_LOGS:
            if db.clean_old_jobs():
                db.vacuum()
        elif action == Actions.DELETE_LOG_SAFE:
            if db.delete_job_safe(job["id"]):
                db.vacuum()
        elif action == Actions.DELETE_LOG:
            if db.delete_job(job["id"]):
                db.vacuum()
        else:
            raise ValueError(f"Unknown job action: {action}")
        return None

    def scheduler_worker(self):
        """Re-queue scheduled jobs (upcoming live events) once their release time is reached."""
        db = JobsDB(readonly=True)
        interval = self.app_config["ydl_server"].get("schedule_check_interval", 60)
        elapsed = interval
        while not self.scheduler_stop.is_set():
            if elapsed < interval:
                self.scheduler_stop.wait(1)
                elapsed += 1
                continue
            elapsed = 0
            for due in db.get_due_scheduled_jobs(int(time.time())):
                print(f"Scheduled time reached for job {due['id']}")
                job = Job(
                    due["name"],
                    Job.PENDING,
                    "Scheduled time reached",
                    int(due["type"]),
                    due["format"],
                    due["urls"],
                    id=due["id"],
                    force_generic_extractor=due["force_generic_extractor"],
                    extra_params=due["extra_params"],
                )
                self.put((Actions.RESUME, job))
        db.close()

    def join(self):
        if self.scheduler_thread is not None:
            self.scheduler_thread.join()
        if self.thread is not None:
            return self.thread.join()
