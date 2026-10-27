YOUTUBE VIEWER - WINDOWS (64-BIT)

1. Extract the ENTIRE ZIP to a normal writable folder, such as Documents.
   Do not run the launcher directly from inside the ZIP.
2. Double-click "Start YouTube Viewer.bat".
3. On the first run, wait while Chromium is downloaded. This may take several
   minutes and requires internet access. The download includes Python and
   Node.js, so you do not need to install them separately.
4. Your default browser opens the dashboard automatically. Leave the launcher
   window open while using it. Press Enter in that window, or close it, to stop
   the backend, dashboard and their browser sessions.

The dashboard is also available at http://127.0.0.1:3000/ while running.
If a port is in use, close the other application or an earlier launcher first.
If setup fails, check your internet connection, antivirus/firewall and the
error shown in the launcher window. Logs are stored in:
%LOCALAPPDATA%\YouTubeViewer\logs

First test with one session, no proxy list and a public YouTube video. Playback
requires access to YouTube; opening a player does not guarantee counted views.
The official live viewer count additionally needs a YouTube Data API key in the
YOUTUBE_API_KEY environment variable. YouTube chat is not included.
