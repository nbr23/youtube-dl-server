import json
import os
import re
import shutil
from pathlib import Path

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from ydl_server.config import (
    app_config,
    get_finished_path,
    get_ui_aliases,
    get_ydl_formats,
    is_valid_download_title,
    resolve_finished_file,
)
from ydl_server.db import Actions, Job, JobsDB, JobType
from ydl_server.jobshandler import JobInsertError, JobInsertTimeout
from ydl_server.ydlhandler import MetadataBusy, MetadataError, MetadataTimeout

TIMESTAMP_RE = re.compile(r"^(\d+(\.\d+)?|(\d+:)?[0-5]?\d:[0-5]?\d(\.\d+)?)$")


def parse_timestamp(ts):
    seconds = 0.0
    for part in ts.split(":"):
        seconds = seconds * 60 + float(part)
    return seconds


def prefix_format(prefix, value):
    """Namespace a format segment, tolerating callers that already prefixed it."""
    return value if value.startswith(prefix + "/") else f"{prefix}/{value}"


MAX_TREE_DEPTH = 32


async def parse_download_request(request):
    is_form = request.headers.get("Content-Type", "").partition(";")[0].strip().lower() == "application/x-www-form-urlencoded"
    if is_form:
        data = dict(await request.form())
    else:
        try:
            data = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError("Invalid JSON request body") from exc
    if not isinstance(data, dict):
        raise TypeError("Request body must be an object")
    for key in ("url", "profile", "audio_format", "format"):
        if data.get(key) is not None and not isinstance(data[key], str):
            raise TypeError(f"{key} must be a string")
    urls = data.get("urls", [])
    if not isinstance(urls, list) or any(not isinstance(url, str) or not url.strip() for url in urls):
        raise TypeError("urls must be an array of non-empty strings")
    urls = list(urls)
    if data.get("url") is not None:
        if not data["url"].strip():
            raise ValueError("url must be a non-empty string")
        urls.append(data["url"])
    if not urls:
        raise ValueError("'url' and 'urls' query parameters omitted")
    aliases = data.get("aliases", [])
    if isinstance(aliases, str):
        aliases = [alias.strip() for alias in aliases.split(",") if alias.strip()]
    if not isinstance(aliases, list) or any(not isinstance(alias, str) or not alias.strip() for alias in aliases):
        raise TypeError("aliases must be an array of non-empty strings or a comma-separated string")
    force_generic = data.get("force_generic_extractor", False)
    if is_form and force_generic in ("true", "false"):
        force_generic = force_generic == "true"
    if not isinstance(force_generic, bool):
        raise TypeError("force_generic_extractor must be a boolean")
    extra_params = data.get("extra_params", {})
    if not isinstance(extra_params, dict):
        raise TypeError("extra_params must be an object")
    title = extra_params.get("title")
    if title is not None and title != "" and not is_valid_download_title(title):
        raise ValueError("Invalid download title")
    return {**data, "urls": urls, "aliases": aliases, "force_generic_extractor": force_generic, "extra_params": extra_params}


async def insert_job(request, job):
    try:
        await run_in_threadpool(request.app.state.jobshandler.insert_and_wait, job)
    except JobInsertTimeout:
        return JSONResponse({"success": False, "error": "Timed out waiting for the job queue"}, status_code=503)
    except JobInsertError:
        return JSONResponse({"success": False, "error": "Could not add job to the queue"}, status_code=503)
    return None


def build_finished_tree(root_dir, seen=None, depth=0):
    try:
        entries = list(os.scandir(root_dir))
    except OSError as e:
        print(f"Error scanning {root_dir} - {e}")
        return []
    if seen is None:
        seen = set()
    files = []
    for entry in entries:
        if entry.name.startswith("."):
            continue
        stat, is_dir = None, False
        try:
            stat = entry.stat()
            is_dir = entry.is_dir()
        except OSError as e:
            print(f"Error accessing {entry.path} - {e}")
        children = None
        if is_dir:
            children = []
            key = (stat.st_dev, stat.st_ino) if stat else None
            if (
                depth < MAX_TREE_DEPTH
                and key not in seen
                and resolve_finished_file(entry.path) is not None
            ):
                seen.add(key)
                children = build_finished_tree(entry.path, seen, depth + 1)
        file_info = {
            "name": entry.name,
            "modified": stat.st_mtime if stat else None,
            "created": stat.st_ctime if stat else None,
            "size": stat.st_size if stat and not is_dir else None,
            "directory": is_dir,
            "children": children,
        }
        files.append(file_info)
    return files


async def api_finished(request):
    return JSONResponse(build_finished_tree(Path(get_finished_path())))


async def api_delete_file(request):
    fname = request.path_params["fname"]
    if not fname:
        return JSONResponse({"success": False, "message": "No filename specified"})
    fname = resolve_finished_file(fname)
    if fname is None:
        return JSONResponse({"success": False, "message": "Invalid filename"})
    fname = Path(fname)
    root = Path(os.path.realpath(get_finished_path()))
    if any(part.startswith(".") for part in fname.relative_to(root).parts):
        return JSONResponse({"success": False, "message": "Invalid filename"}, status_code=400)
    metadata_db_path = app_config["ydl_server"].get("metadata_db_path")
    if metadata_db_path:
        metadata_db_path = Path(os.path.realpath(metadata_db_path))
        if fname == metadata_db_path or fname in metadata_db_path.parents:
            return JSONResponse({"success": False, "message": "Invalid filename"}, status_code=400)
    try:
        if fname.is_dir():
            shutil.rmtree(fname)
        else:
            fname.unlink()
    except OSError as e:
        print(e)
        return JSONResponse(
            {"success": False, "message": f"Could not delete the specified file (Err {e.errno or 'unknown'})"}
        )

    return JSONResponse({"success": True, "message": "File deleted"})


async def api_cut_file(request):
    fname = request.path_params["fname"]
    data = await request.json()
    start = str(data.get("start") or "0")
    end = data.get("end") or None
    mode = data.get("mode", "fast")
    output = (data.get("output") or "").strip()

    src = resolve_finished_file(fname)
    if src is None:
        return JSONResponse({"success": False, "message": "Invalid filename"})
    if not os.path.isfile(src):
        return JSONResponse({"success": False, "message": "File not found"})

    if not output or "/" in output or output.startswith("."):
        return JSONResponse({"success": False, "message": "Invalid output filename"})
    dst = os.path.join(os.path.dirname(src), output)
    if os.path.exists(dst):
        return JSONResponse({"success": False, "message": "Output file already exists"})

    if not TIMESTAMP_RE.match(start) or (end and not TIMESTAMP_RE.match(str(end))):
        return JSONResponse({"success": False, "message": "Invalid timestamp"})
    if end and parse_timestamp(str(end)) <= parse_timestamp(start):
        return JSONResponse({"success": False, "message": "End time must be after start time"})
    if mode not in ("fast", "precise"):
        return JSONResponse({"success": False, "message": "Invalid mode"})

    job = Job(
        "Cut {} [{} - {}]".format(fname, start, end or "end"),
        Job.PENDING,
        "",
        JobType.FFMPEG_CUT,
        None,
        [fname],
        extra_params={"start": start, "end": end, "mode": mode, "output": output},
    )
    error = await insert_job(request, job)
    if error is not None:
        return error

    return JSONResponse({"success": True, "output": output})


async def api_list_extractors(request):
    return JSONResponse(request.app.state.ydlhandler.ydl_extractors)


async def api_server_info(request):
    return JSONResponse(
        {
            "ydl_module_name": request.app.state.ydlhandler.ydl_module_name,
            "ydl_module_version": request.app.state.ydlhandler.ydl_version,
            "ydl_module_website": request.app.state.ydlhandler.ydl_website,
            "ydls_version": request.app.state.ydlhandler.ydls_version,
            "ydls_release_date": request.app.state.ydlhandler.ydls_release_date,
            "download_workers_count": request.app.state.ydlhandler.download_workers_count,
        }
    )


async def api_list_formats(request):
    return JSONResponse(
        {
            "ydl_formats": get_ydl_formats(app_config),
            "ydl_aliases": get_ui_aliases(app_config),
            "ydl_default_format": app_config["ydl_server"].get(
                "default_format", "video/best"
            ),
        }
    )


async def api_queue_size(request):
    db = JobsDB(readonly=True)
    counts = db.get_job_counts()
    db.close()
    return JSONResponse(
        {
            "success": True,
            "stats": {
                "queue": request.app.state.ydlhandler.queue.qsize(),
                **counts,
            },
        }
    )


async def api_logs(request):
    db = JobsDB(readonly=True)
    limit = app_config["ydl_server"].get("max_log_entries", 100)
    status = request.query_params.get("status", None)
    if request.query_params.get("show_logs", "1") in ["1", "true"]:
        result = db.get_jobs_with_logs(limit, status)
    else:
        result = db.get_jobs(limit, status)
    db.close()
    return JSONResponse(result)


async def api_logs_purge(request):
    request.app.state.jobshandler.put((Actions.PURGE_LOGS, None))
    return JSONResponse({"success": True})


async def api_logs_clean(request):
    request.app.state.jobshandler.put((Actions.CLEAN_LOGS, None))
    return JSONResponse({"success": True})


async def api_jobs_stop(request):
    db = JobsDB(readonly=True)
    job_id = request.path_params["job_id"]
    job = db.get_job_by_id(job_id)
    db.close()

    if not job:
        return JSONResponse({"success": False}, status_code=404)
    try:
        aborted = await run_in_threadpool(request.app.state.jobshandler.submit_and_wait, Actions.ABORT, job["id"])
    except (JobInsertError, JobInsertTimeout):
        return JSONResponse({"success": False, "error": "Could not stop job"}, status_code=503)
    if aborted:
        request.app.state.ydlhandler.cancel(job["id"])
    return JSONResponse({"success": aborted})


async def api_jobs_retry(request):
    db = JobsDB(readonly=True)
    job_id = request.path_params["job_id"]
    job = db.get_job_by_id(job_id)
    db.close()
    if not job:
        return JSONResponse({"success": False}, status_code=404)

    extra_params = job.get("extra_params", {})
    # An explicit retry gets a fresh scheduling budget
    extra_params.pop("schedule_attempts", None)

    new_job = Job(
        job["name"], Job.PENDING, "", int(job["type"]), job["format"], job["urls"], extra_params=extra_params
    )
    new_job.force_generic_extractor = job.get("force_generic_extractor", False)

    error = await insert_job(request, new_job)
    if error is not None:
        return error
    request.app.state.jobshandler.put((Actions.DELETE_LOG_SAFE, job))

    return JSONResponse({"success": True})

async def api_jobs_delete(request):
    job_id = request.path_params["job_id"]
    if job_id is not None:
        request.app.state.jobshandler.put((Actions.DELETE_LOG, {'id': job_id}))
        return JSONResponse({"success": True})
    return JSONResponse({"success": False})

async def api_queue_download(request):
    try:
        data = await parse_download_request(request)
    except (TypeError, ValueError) as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)
    urls = data["urls"]
    profile = data.get("profile")
    aliases = data["aliases"]
    audio_format = data.get("audio_format")
    format_str = data.get("format")
    force_generic_extractor = data["force_generic_extractor"]

    if profile:
        format_str = ','.join(filter(None, [format_str, prefix_format("profile", profile)]))
    if aliases:
        format_str = ','.join(filter(None, [format_str] + [prefix_format("alias", a) for a in aliases]))
    if audio_format:
        format_str = ','.join(filter(None, [format_str, prefix_format("audio", audio_format)]))
    if not format_str:
        format_str = app_config["ydl_server"].get("default_format", "video/best")
    options = {"format": format_str, "force_generic_extractor": force_generic_extractor}

    extra_params = data["extra_params"]

    job = Job(
        ", ".join(urls), Job.PENDING, "", JobType.YDL_DOWNLOAD, format_str, urls, extra_params=extra_params
    )
    job.force_generic_extractor = force_generic_extractor
    error = await insert_job(request, job)
    if error is not None:
        return error

    print("Added url " + ",".join(urls) + " to the download queue")
    return JSONResponse({"success": True, "urls": urls, "options": options, "job_id": job.id})


async def api_metadata_fetch(request):
    try:
        data = await parse_download_request(request)
    except (TypeError, ValueError) as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)
    try:
        rc, stdout = await run_in_threadpool(
            request.app.state.ydlhandler.fetch_metadata,
            data["urls"],
            force_generic_extractor=data["force_generic_extractor"],
            wait=False,
        )
    except MetadataBusy:
        return JSONResponse({"success": False, "error": "Metadata extraction is busy"}, status_code=503)
    except MetadataTimeout:
        return JSONResponse({"success": False, "error": "Metadata extraction timed out"}, status_code=504)
    except (MetadataError, OSError):
        return JSONResponse({"success": False, "error": "Could not fetch metadata"}, status_code=502)
    if rc == 0:
        return JSONResponse(stdout)
    return JSONResponse({"success": False}, status_code=404)
