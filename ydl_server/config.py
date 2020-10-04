app_defaults = {
    'YDL_FORMAT': 'bestvideo+bestaudio/best',
    'YDL_EXTRACT_AUDIO_FORMAT': None,
    'YDL_EXTRACT_AUDIO_QUALITY': '192',
    'YDL_RECODE_VIDEO_FORMAT': None,
    'YDL_OUTPUT_TEMPLATE': '/youtube-dl/%(title)s [%(id)s].%(ext)s',
    'YDL_OUTPUT_TEMPLATE_PLAYLIST': '/youtube-dl/%(playlist_title)s/%(title)s [%(id)s].%(ext)s',
    'YDL_ARCHIVE_FILE': None,
    'YDL_SERVER_HOST': '0.0.0.0',
    'YDL_SERVER_PORT': 8080,
    'YDL_CACHE_DIR': '/youtube-dl/.cache',
    'YDL_DB_PATH': '/youtube-dl/.ydl-metadata.db',
    'YDL_SUBTITLES_LANGUAGES': None,
    'YDL_DEBUG': False,
    'YDL_RAW_OPTIONS': {
        'ignoreerrors': True
        },
    # should we write NFO files adacent to the downloaded movies, used to pass Metadata to Kodi etc
    'YDL_WRITE_NFO': False,
    # relative English string will be parsed into a timedelata from now by PHP on the server side
    'TWL_LOOKBACK_TIME_STRING': '-3days',
}
