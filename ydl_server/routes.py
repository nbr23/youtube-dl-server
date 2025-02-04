from pathlib import Path

from ydl_server import views
from ydl_server.config import get_finished_path
from starlette.responses import JSONResponse
from starlette.requests import Request

from starlette.routing import Route, Mount
from starlette.staticfiles import StaticFiles

static = StaticFiles(directory=str(Path(__file__).parent / "static"), html=True)

finished_files = StaticFiles(directory=get_finished_path())

async def downloads_endpoint(request: Request):
    params = request.query_params
    show_logs = params.get("show_logs", "false").lower() == "true"
    status = params.get("status")  # added new status parameter
    downloads = request.app.state.jobshandler.get_downloads(show_logs=show_logs, status=status)  # removed await
    return JSONResponse(downloads)

routes = [
    Route("/api/extractors", views.api_list_extractors, name="api_list_extractors"),
    Route("/api/formats", views.api_list_formats, name="api_list_formats"),
    Route("/api/info", views.api_server_info, name="api_server_info"),
    Route("/api/downloads/stats", views.api_queue_size, name="api_queue_size"),
    Route("/api/downloads", downloads_endpoint, name="api_logs", methods=["GET"]),
    Route("/api/downloads/clean", views.api_logs_clean, name="api_logs_clean"),
    Route(
        "/api/downloads",
        views.api_logs_purge,
        name="api_logs_purge",
        methods=["DELETE"],
    ),
    Route(
        "/api/downloads",
        views.api_queue_download,
        name="api_queue_dowanload",
        methods=["POST"],
    ),
    Route(
        "/api/metadata",
        views.api_metadata_fetch,
        name="api_metadata_fetch",
        methods=["POST"],
    ),
    Route("/api/finished", views.api_finished, name="api_finished", methods=["GET"]),
    Route(
        "/api/finished/{fname:path}",
        views.api_delete_file,
        name="api_delete_file",
        methods=["DELETE"],
    ),
    Route(
        "/api/jobs/{job_id:str}/stop",
        views.api_jobs_stop,
        name="api_jobs_stop",
        methods=["POST"],
    ),
    Route(
        "/api/jobs/{job_id:str}/retry",
        views.api_jobs_retry,
        name="api_jobs_retry",
        methods=["POST"],
    ),
    Route(
        "/api/jobs/{job_id:str}",
        views.api_jobs_delete,
        name="api_jobs_delete",
        methods=["DELETE"],
    ),
    Mount("/api/finished/", finished_files, name="api_finished"),
    Mount("/", static, name="static"),
]
