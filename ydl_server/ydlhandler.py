import os
from queue import Queue
from threading import Thread
import subprocess
from collections import ChainMap
import io
import importlib
import youtube_dl
import json
import httpx
import glob
from html.parser import HTMLParser
from time import sleep
import sys
from pathlib import Path

from ydl_server.logdb import JobsDB, Job, Actions, JobType
from ydl_server import jobshandler
from ydl_server.config import app_defaults

queue = Queue()
thread = None
done = False

def start():
    thread = Thread(target=worker)
    thread.start()

def put(obj):
    queue.put(obj)

def finish():
    done = True

def worker():
    while not done:
        job = queue.get()
        job.status = Job.RUNNING
        jobshandler.put((Actions.SET_STATUS, (job.id, job.status)))
        if job.type == JobType.YDL_DOWNLOAD:
            output = io.StringIO()
            stdout_thread = Thread(target=download_log_update,
                    args=(job, output))
            stdout_thread.start()
            try:
                job.log = Job.clean_logs(download(job.url, {'format':  job.format}, output, job.id))
                job.status = Job.COMPLETED
            except Exception as e:
                job.status = Job.FAILED
                job.log += str(e)
                print("Exception during download task:\n" + str(e))
            stdout_thread.join()
        elif job.type == JobType.TWL_DOWNLOAD:
            output = io.StringIO()
            stdout_thread = Thread(target=download_log_update,
                    args=(job, output))
            stdout_thread.start()
            try:
                job.log = Job.clean_logs(twldownload(job.url, {'format':  job.format}, output, job.id))
                job.status = Job.COMPLETED
            except Exception as e:
                job.status = Job.FAILED
                job.log += str(e)
                print("Exception during ToWatchList task:\n" + str(e))
            stdout_thread.join()
        elif job.type == JobType.YDL_UPDATE:
            rc, log = update()
            job.log = Job.clean_logs(log)
            job.status = Job.COMPLETED if rc == 0 else Job.FAILED
        jobshandler.put((Actions.UPDATE, job))
        queue.task_done()

def reload_youtube_dl():
    for module in list(sys.modules.keys()):
        if 'youtube' in module:
            importlib.reload(sys.modules[module])

def update():
    command = ["pip", "install", "--no-cache-dir", "--upgrade", "youtube-dl"]
    proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    out, err = proc.communicate()
    if proc.returncode == 0:
        reload_youtube_dl()
    return proc.returncode, str(out.decode('utf-8'))

def get_ydl_options(request_options):
    request_vars = {
        'YDL_EXTRACT_AUDIO_FORMAT': None,
        'YDL_RECODE_VIDEO_FORMAT': None,
    }

    requested_format = request_options.get('format', 'bestvideo')

    if requested_format in ['aac', 'flac', 'mp3', 'm4a', 'opus', 'vorbis', 'wav']:
        request_vars['YDL_EXTRACT_AUDIO_FORMAT'] = requested_format
    elif requested_format == 'bestaudio':
        request_vars['YDL_EXTRACT_AUDIO_FORMAT'] = 'best'
    elif requested_format in ['mp4', 'flv', 'webm', 'ogg', 'mkv', 'avi']:
        request_vars['YDL_RECODE_VIDEO_FORMAT'] = requested_format

    ydl_vars = ChainMap(request_vars, os.environ, app_defaults)

    postprocessors = []

    if(ydl_vars['YDL_EXTRACT_AUDIO_FORMAT']):
        postprocessors.append({
            'key': 'FFmpegExtractAudio',
            'preferredcodec': ydl_vars['YDL_EXTRACT_AUDIO_FORMAT'],
            'preferredquality': ydl_vars['YDL_EXTRACT_AUDIO_QUALITY'],
        })

    if(ydl_vars['YDL_RECODE_VIDEO_FORMAT']):
        postprocessors.append({
            'key': 'FFmpegVideoConvertor',
            'preferedformat': ydl_vars['YDL_RECODE_VIDEO_FORMAT'],
        })

    ydl_options = {
        'format': ydl_vars['YDL_FORMAT'],
        'postprocessors': postprocessors,
        'outtmpl': ydl_vars['YDL_OUTPUT_TEMPLATE'],
        'download_archive': ydl_vars['YDL_ARCHIVE_FILE'],
        'cachedir': ydl_vars['YDL_CACHE_DIR']
    }

    ydl_options = {**ydl_vars['YDL_RAW_OPTIONS'], **ydl_options}

    if ydl_vars['YDL_SUBTITLES_LANGUAGES']:
        ydl_options['writesubtitles'] = True
        if ydl_vars['YDL_SUBTITLES_LANGUAGES'] != 'all':
            ydl_options['subtitleslangs'] = \
                    ydl_vars['YDL_SUBTITLES_LANGUAGES'].split(',')
        else:
            ydl_options['allsubtitles'] = True

    return ydl_options

def download_log_update(job, stringio):
    while job.status == Job.RUNNING:
        job.log = Job.clean_logs(stringio.getvalue())
        jobshandler.put((Actions.SET_LOG, (job.id, job.log)))
        sleep(5)

def fetch_metadata(url):
    stdout = io.StringIO()
    stderr = io.StringIO()
    info = None
    with youtube_dl.YoutubeDL({'extract_flat': 'in_playlist'}) as ydl:
        ydl.params['extract_flat'] = 'in_playlist'
        return ydl.extract_info(url, download=False)

def download(url, request_options, output, job_id):
    with youtube_dl.YoutubeDL(get_ydl_options(request_options)) as ydl:
        ydl.params['extract_flat'] = 'in_playlist'
        ydl_opts = ChainMap(os.environ, app_defaults)
        info = ydl.extract_info(url, download=False)
        if 'title' in info and info['title']:
            jobshandler.put((Actions.SET_NAME, (job_id, info['title'])))
        if '_type' in info and info['_type'] == 'playlist' \
                and 'YDL_OUTPUT_TEMPLATE_PLAYLIST' in ydl_opts:
            ydl.params['outtmpl'] = ydl_opts['YDL_OUTPUT_TEMPLATE_PLAYLIST']
        ydl.params['extract_flat']= False

        # 'YDL_OUTPUT_TEMPLATE': '/youtube-dl/%(title)s [%(id)s].%(ext)s',
        # 'YDL_OUTPUT_TEMPLATE_PLAYLIST': '/youtube-dl/%(playlist_title)s/%(title)s [%(id)s].%(ext)s',

        if 'YDL_WRITE_NFO' in ydl_opts and ydl_opts['YDL_WRITE_NFO']:
            # write NFO file
            vidpath = Path(ydl.prepare_filename(info))
            nfopath = os.path.join(vidpath.parent, f"{vidpath.stem}.nfo")
            if not os.path.isfile(nfopath):
                with open(nfopath, "w") as nfoF:
                    # json.dump(info, nfoF)
                    # nfoF.write(f"{info['title']}\n")
                    # nfoF.write(f"{ydl.prepare_filename(info)}.nfo\n")

                    nfoF.write("<musicvideo>\n")

                    if 'title' in info and info['title']:
                        nfoF.write(f"  <title>{info['title']}</title>\n")
                    else:
                        nfoF.write("  <title>Unknown Title</title>\n")

                    if 'uploader_id' in info and info['uploader_id']:
                        nfoF.write(f"  <showtitle>{info['uploader']}</showtitle>\n")
                    else:
                        nfoF.write("  <showtitle>Unknown Channel</showtitle>\n")

                    if 'description' in info and info['description']:
                        nfoF.write(f"  <plot>{info['description']}</plot>\n")
                    else:
                        nfoF.write("  <plot>Unknown Plot</plot>\n")

                    nfoF.write(f"  <runtime>{round(info['duration']/60.0)}</runtime>\n")
                    nfoF.write(f"  <thumb>{info['thumbnail']}</thumb>\n")
                    nfoF.write(f"  <videourl>{url}</videourl>\n")
                    # upload date is a dumb datestring eg 20200929 = 2020-09-29
                    nfoF.write(f"  <aired>{info['upload_date'][:4]}-"
                               "{info['upload_date'][4:6]}-"
                               "{info['upload_date'][6:]}</aired>\n")

                    nfoF.write("</musicvideo>\n")

        # Swap out sys.stdout as ydl's output so we can capture it
        ydl._screen_file = output
        ydl._err_file = ydl._screen_file
        ydl.download([url])
        return ydl._screen_file.getvalue()


class MLStripper(HTMLParser):
    def __init__(self):
        self.reset()
        self.fed = []

    def handle_data(self, d):
        self.fed.append(d)

    def get_data(self):
        return ''.join(self.fed)


def strip_tags(html):
    s = MLStripper()
    s.feed(html)
    return s.get_data()


def twldownload(url, request_options, output, job_id):
    TWL_API_TOKEN = os.getenv("TWL_API_TOKEN", default="unset").strip()
    assert TWL_API_TOKEN != "unset", "ERROR: TWL_API_TOKEN should be set in env (and is not)"

    ydl_opts = ChainMap(os.environ, app_defaults)
    lookbackStr = ydl_opts['TWL_LOOKBACK_TIME_STRING']
    if request_options and 'format' in request_options and request_options['format'] is not None:
        # use 'format' as 'TWL_LOOKBACK_TIME_STRING' here
        lookbackStr = request_options['format']

    r = httpx.get(f"https://towatchlist.com/api/v1/marks?since={lookbackStr}&uid={TWL_API_TOKEN}")
    r.raise_for_status()
    myMarks = r.json()['marks']

    output_dir = Path(ydl_opts['YDL_OUTPUT_TEMPLATE']).parent
    with open(os.path.join(output_dir, '.twl.json'), 'w') as filehandle:
        json.dump(myMarks, filehandle)

    downloadQueueAdd = 0
    removedFiles = 0
    for i in range(len(myMarks)):
        # set some values we'll use below
        mmeta = {}  # mark metadata dict
        mmeta['videoURL'] = myMarks[i]['Mark']['source_url']
        mmeta['title'] = myMarks[i]['Mark']['title']
        mmeta['video_id'] = myMarks[i]['Mark']['video_id']
        mmeta['channel_title'] = myMarks[i]['Mark']['channel_title']
        mmeta['duration'] = int(myMarks[i]['Mark']['duration']) / 60.0
        mmeta['created'] = myMarks[i]['Mark']['created']
        try:
            mmeta['description'] = strip_tags(myMarks[i]['Mark']['comment'])
        except:
            mmeta['description'] = '-Failed to parse-'

        if (myMarks[i]['Mark']['watched']) or (myMarks[i]['Mark']['delflag']):
            # it's been marked as watched, delete the local copy
            for filename in glob.glob(os.path.join(output_dir, f"*{mmeta['video_id']}*")):
                # TODO we could remove more intellegently/selectively here ^
                os.remove(filename)
                removedFiles += 1
            continue

        downloadQueueAdd += 1
        job = Job(mmeta['title'],
                  Job.PENDING,
                  "",
                  JobType.YDL_DOWNLOAD,
                  ydl_opts['YDL_FORMAT'],
                  mmeta['videoURL'])
        jobshandler.put((Actions.INSERT, job))

    if removedFiles > 0:
        # TODO: clean Kodi library
        pass

    return f"Processed {len(myMarks)} Marks, Queued {downloadQueueAdd}, Removed {removedFiles} vids/nfos"


def resume_pending():
    db = JobsDB(readonly=False)
    jobs = db.get_all()
    not_endeds = [job for job in jobs if job['status'] == "Pending" or job['status'] == 'Running']
    for pending in not_endeds:
        if int(pending["type"]) == JobType.YDL_UPDATE:
            jobshandler.put((Actions.SET_STATUS, (pending["id"], Job.FAILED)))
        else:
            job = Job(pending["name"], Job.PENDING, "Queue stopped",
                    int(pending["type"]), pending["format"], pending["url"])
            job.id = pending["id"]
            jobshandler.put((Actions.RESUME, job))

def join():
    if thread is not None:
        return thread.join()

def get_ydl_version():
    return youtube_dl.version.__version__
