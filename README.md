# YouTube Viewer Bot

A local dashboard for opening and monitoring YouTube playback sessions. This version replaces the original Kick-specific viewer protocol with isolated, headless Chromium contexts using Playwright. It supports YouTube live-stream/video links and channel handles, optional proxy lists, automatic reconnection, and live playback status.

> **Important:** YouTube decides which activity counts as a view or concurrent viewer and may filter automated traffic. Opening playback sessions **does not guarantee views or an increase in the counter**. Automating or manipulating engagement can violate YouTube's terms. Use responsibly and only where permitted.

## Requirements

- Python 3.9 or newer and Node.js 18.18 or newer
- A supported desktop OS and internet access to YouTube
- Playwright's Chromium browser (installed below). Linux may also need Playwright's system dependencies; see [Playwright installation](https://playwright.dev/python/docs/intro#installing-playwright).
- Resources and bandwidth for each browser session; start with one or two sessions.

## Install

From the project directory:

```sh
python -m venv .venv
# macOS/Linux:
source .venv/bin/activate
# Windows PowerShell instead: .venv\Scripts\Activate.ps1

pip install -r requirements.txt
python -m playwright install chromium

cd frontend
npm ci
cd ..
```

On Linux, if Chromium reports missing system libraries, install the required packages with `python -m playwright install-deps chromium` (this may require administrator privileges). If you already have a compatible Chromium, `CHROMIUM_PATH` can optionally point to its executable instead of the downloaded browser.

## Run

Use two terminals, keeping both processes running:

**Backend:**

```sh
# Activate the virtual environment first
python backend/main.py
```

**Frontend:**

```sh
cd frontend
npm run dev
```

Open <http://localhost:3000>. Paste a YouTube watch/live/share URL, a case-sensitive 11-character video ID, or a channel handle such as `@YouTube`; select the number of sessions and press **Start sessions**. An 11-character bare input is treated as a video ID, so prefix a channel handle with `@`. Channel targets point to the channel's `/live` page; a video link is generally more reliable.

The status panel reports startup, playback, retries, and errors. **Playing sessions** counts browser players that are playing; **Playback attempts** counts attempts, not verified YouTube views. Stop also works during startup and retrying. Private, age-restricted, offline, unavailable or sign-in-only streams may not play. No attempt is made to bypass those restrictions.

The frontend proxies `/socket.io/` and `/api/youtube/` to `http://127.0.0.1:8765` on the server. To use a different backend port, start the backend with `--port PORT` and set `BACKEND_URL=http://127.0.0.1:PORT` **before starting the frontend**. The backend binds to localhost by default. It has no authentication, so do not expose it to untrusted networks. For development behind a trusted preview proxy, the backend can be started with `--host 0.0.0.0`.

## Optional: YouTube live viewer count

YouTube's official Data API v3 is used only for the **Live viewers** card. Create an API key with the YouTube Data API v3 enabled in Google Cloud and set `YOUTUBE_API_KEY` in the backend's environment before starting it:

```sh
# macOS/Linux
export YOUTUBE_API_KEY="your-api-key"
python backend/main.py
```

In Windows PowerShell, use `$env:YOUTUBE_API_KEY="your-api-key"` before launching the backend. The key stays on the backend and is not sent to the browser. Without a key, when quota/access fails, or when YouTube does not publish a concurrent count, the card displays `—` and explains why; it does not invent a zero. A video link is needed for the count; for a channel handle, the app uses the video ID once the player resolves it. The dashboard polls no more often than once per minute per selected target.

## Proxies

The proxy file is optional. Without one, all sessions connect directly. Upload a UTF-8 `.txt` file with one proxy per line, for example:

```text
127.0.0.1:8080
http://user:password@proxy.example:8080
socks4://proxy.example:1080
socks5://proxy.example:1080
```

Explicit schemes override the selected default; `Auto / HTTP` assumes HTTP for entries without a scheme. HTTP proxy authentication is supported; Chromium does not support authenticated SOCKS proxies. Empty lines and `#` comments are ignored. Invalid entries are rejected with their line number. Proxies are assigned round-robin; if you provide fewer proxies than sessions they are reused. A failed proxy is **not** silently replaced by a direct connection. The uploaded text stays in backend memory and is not written to disk; only use a backend and proxies you trust. The file size limit is 1 MB.

## Testing

```sh
pip install pytest
python -m pytest
cd frontend
npm run lint
npm exec -- tsc --noEmit
npm run build
```

The unit tests mock YouTube and browser calls, so they run without a YouTube connection. An optional real-browser smoke test uses a local synthetic video: set `TEST_CHROMIUM_PATH` to a compatible Chromium executable before running `python -m pytest tests/test_playwright_local.py`. Actual YouTube playback depends on availability, regional restrictions, Chromium, and the network.

## Migration notes

The old Kick chat bot, Kick account/Patreon service, stability-mode claims, Kick executable builds, and Kick update feed are **not** YouTube features and are not used by the new dashboard. Some old files remain for reference; previous Kick releases are not YouTube builds. There is currently no YouTube chat integration or packaged executable; run from source using the steps above.

## License

MIT; see [LICENSE.txt](LICENSE.txt).
