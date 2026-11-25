# Video Downloader

```bash
pip install -r requirements.txt
```

Then start it with `python main.py`.

A desktop video / audio / playlist downloader for Windows, Linux and macOS (PyQt6 interface, yt-dlp as the main
engine with pytubefix, gallery-dl and you-get as fallbacks, FFmpeg for merging and validation).

- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation) · [FFmpeg](#ffmpeg) · [Deno / JavaScript runtime](#deno--javascript-runtime-youtube) · [aria2c](#aria2c-optional)
- [Using the application](#using-the-application)
- [Settings reference](#settings-reference)
- [How the important parts work](#how-the-important-parts-work)
- [Troubleshooting](#troubleshooting)
- [Building a Windows .exe with an icon](#building-a-windows-exe-with-an-icon)
- [Tests](#tests) · [Project structure](#project-structure) · [Legal notes](#legal-notes)
- [ملخص سريع بالعربية](#ملخص-سريع-بالعربية)

---

## Features

**Downloading**
- Paste a link and the application analyses it automatically (no button): title, thumbnail, duration, platform,
  available resolutions and estimated size appear as soon as they are known. For YouTube the title and thumbnail
  show up almost instantly. Change the link and the new one is analysed straight away; results of the old link are ignored.
- Quality: Best, 8K down to 144p, Audio only (MP3), or Custom (maximum resolution, container, audio format, bitrate,
  quality-vs-size priority). Resolutions that do not exist for the video are disabled.
- Fast transfers: parallel fragments (1–16 connections), 10 MB range requests (avoids YouTube's per-connection
  throttling) and optional aria2c.
- Four engines with an ordered fallback chain: yt-dlp → pytubefix (YouTube only) → gallery-dl (not YouTube) → you-get.
- Playlists: a whole playlist is downloaded into a folder named after it (`001 - Title.mp4`). A checkable list with
  thumbnails lets you choose which items to download (all are selected by default). A link such as
  `watch?v=…&list=…` means the whole playlist. YouTube auto-generated "Mix" lists (`list=RD…`) are endless, so only the
  video is downloaded.

**Control and reliability**
- Pause / Resume / Cancel for each download. Pause keeps the partial file and Resume continues from it.
  Cancel asks whether to keep or delete the partial files; completed downloads are never deleted.
- Network recovery: exponential-backoff retries (default 5 retries, 3 s → 6 s → 12 s …, capped at 120 s), then the
  task becomes *Interrupted*, keeps its partial data and resumes automatically when the connection returns.
- Queue with thumbnails, priorities, pause/resume/cancel/retry/remove, duplicate prevention, and recovery of
  unfinished tasks after a restart (optionally automatic).
- History (SQLite) with thumbnails, search, filters, sorting, open file/folder, retry failed, remove records
  (files are only deleted if you explicitly choose that).

**Playback safety (no more "audio but black video")**
- MP4 downloads prefer H.264 video + AAC audio. Every file from every engine is checked with ffprobe: a video
  download without a video stream is rejected (and the next engine is tried), never reported as success.
- *Compatibility mode* (default on) converts only the streams that are not H.264 (`yuv420p`) / AAC, so the file plays
  in QQPlayer, TVs, phones and browsers.

**Files and thumbnails**
- Private temp folder per attempt, atomic move into place, no silent overwrite, unrelated files are never touched.
- Optional `.jpg` thumbnail saved beside each download and cover art embedded in MP3 files.

**Interface**
- Sidebar navigation: **Dashboard, Downloader, Download Queue, Media Library, Download History, Diagnostics, Settings**.
- Dark and light themes; English and Arabic with right-to-left layout (language applies after a restart).
- Shortcuts: `Ctrl+1…7` pages, `Ctrl+Enter` start, `Ctrl+L` focus the link box, `Ctrl+,` settings, `Ctrl+J` console.
- The live console is hidden by default and opens by itself when an error happens.
- Drag a link (or text containing links) onto the window to open it in the Downloader.

**Desktop integration**
- System tray icon with Show/Pause queue/Quit, optional *close to tray* and *minimise to tray*; downloads keep running.
- Desktop notifications for completed and failed downloads (tray balloon).
- Optional clipboard monitor (off by default): a link you copy is put into the Downloader when the box is empty.
- First-run setup wizard: download folder, FFmpeg, Deno, aria2c (required / recommended / optional, all skippable),
  language, theme, default quality.
- Optional update check against a release address you configure (GitHub-releases style JSON, https only).

## Requirements

- Python 3.11 or newer (3.13 works)
- Packages in `requirements.txt`: PyQt6, yt-dlp (with the `default` extras), pytubefix, gallery-dl, you-get
- Strongly recommended: **FFmpeg** (includes `ffprobe`) and, for YouTube, **Deno**
- Optional: aria2c

A missing engine package is not fatal: that engine is skipped and the console says so.

## Installation

### Windows

```bat
:: 1. Install Python 3.11+ from https://www.python.org (tick "Add python.exe to PATH")
cd video_downloader
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

:: 2. Tools used by the app
winget install --id Gyan.FFmpeg
winget install DenoLand.Deno

:: 3. Run
python main.py
```

### Linux / macOS

```bash
cd video_downloader
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
sudo apt install ffmpeg        # Fedora: dnf install ffmpeg · Arch: pacman -S ffmpeg · macOS: brew install ffmpeg
curl -fsSL https://deno.land/install.sh | sh      # or: brew install deno
python main.py
```

Keep the download libraries current — sites change often:
`pip install -U yt-dlp pytubefix gallery-dl you-get`

### FFmpeg

FFmpeg is needed to merge separate video/audio streams (most YouTube qualities above 360p/720p), convert to MP3,
choose audio bitrate, make MKV, run the ffprobe validation and convert incompatible codecs.
After installing it either make sure `ffmpeg` is on `PATH` or choose `ffmpeg.exe` in *Settings → FFmpeg path*.
Without FFmpeg the application keeps working with single-file streams and tells you what is limited.

### Deno / JavaScript runtime (YouTube)

YouTube needs a JavaScript runtime to solve its challenges. The application looks for **Deno, Node or Bun** on
`PATH` and hands the one it finds to yt-dlp. Without any of them you will see
`No supported JavaScript runtime could be found` and some formats may be missing.
Install Deno (`winget install DenoLand.Deno`) and restart the application.

### aria2c (optional)

For the most "download manager"-like speed install aria2 (`winget install aria2.aria2`, `sudo apt install aria2`)
and tick *Settings → Use aria2c*. Pause/Cancel respond more slowly with aria2c because yt-dlp hands the whole
transfer to it, which is why it is off by default.

## Using the application

### Dashboard
Totals (downloads, downloaded size, active downloads), current speed, recent downloads, storage usage of the
download folder, and the status of required / recommended / optional tools (FFmpeg, Deno/Node, aria2c).

### Downloader
1. Paste a link, drop it on the window, or press **Paste URL**. Analysis starts by itself; the preview card shows the
   thumbnail, title, platform, duration, resolutions and estimated size. Only formats that exist are selectable.
2. Pick a **preset**: Best Quality, Balanced (up to 1080p, H.264), Small File Size (up to 480p), Compatible with Most
   Players (MP4 + H.264 + AAC) or Custom (manual 144p – 8K, container, codec, audio bitrate, size priority).
3. For a playlist the list shows every item with thumbnail, duration and size when the source states them, plus a
   running status (Downloading / Done / Failed / Skipped). Untick items to skip them; the counter shows the selection and
   the known total size.
4. **Start Download** queues the link and starts the queue; **Add to Queue** only queues it. The Progress card has
   **Pause**, **Resume** and **Cancel**.

### Download Queue
Thumbnail, title, progress, speed, ETA, quality, status and engine for each task. Pause, Resume, Cancel (asks keep/delete
partials), Retry, Remove, priority ▲▼, Clear Completed, Start/Pause Queue. Statuses: Pending, Extracting Information,
Downloading, Paused, **Interrupted**, Completed, Failed, Cancelled. The queue is stored in SQLite and restored after a
restart or a crash.

### Media Library
Everything in the download folder as a **grid** or **list** with thumbnails (made by FFmpeg when available), search,
type filter (video / audio), sort (newest, name, size), *Open*, *Open Folder*, *Rename* (never overwrites) and *Delete*
(asks for confirmation; only files inside the download folder can be touched), plus storage statistics.

### Download History
Thumbnail, title, platform, resolution, format, size, date, status and location. Search, platform and status
filters, date sorting, *Open File*, *Open Folder*, *Retry Failed*, *Remove Record* (the file is kept unless you choose to
delete it too).

### Diagnostics (error centre)
Every failure is listed with a plain-language explanation and a category (network, authentication, FFmpeg, storage,
permission, missing dependency …). *Show technical details*, *Copy details*, *Export diagnostic report* (versions,
dependency status, settings **without** cookies/credentials, recent errors, redacted log tail), the application log
viewer and a dependency checklist.

### Settings
Organised in categories (General, Downloads, Queue and speed, Tools and sign-in, Behaviour, Updates …) with
descriptions, validation, **Save settings**, **Revert** and **Reset to defaults**.

## Settings reference

| Setting | Default | Meaning |
|---|---|---|
| Download folder | `~/Downloads/VideoDownloader` | Where files are saved |
| Default video quality / format / audio bitrate | Best / MP4 / 192 | Pre-selected on the Downloader tab |
| Preferred codec | Auto | Auto, H.264, VP9 or AV1 (ignored while Compatibility mode is on for MP4) |
| Compatibility mode | On | MP4 + H.264 + AAC; re-encodes only streams that need it |
| Save thumbnail beside the download | Off | Writes `<title>.jpg` (never overwrites an existing file) |
| Embed thumbnail in MP3 | On | Cover art inside MP3 files |
| Download connections | 8 | Parallel fragments / range requests (1–16) |
| Use aria2c | Off | Multi-connection transfers through aria2c if installed |
| If a file already exists | Keep both | Keep both (add a number), skip, or overwrite |
| Filename templates | `{title}` / `{index} - {title}` | Fields `{title} {id} {uploader} {date}` (+ `{index} {playlist}`) |
| FFmpeg path | empty | Empty = use `PATH` |
| Cookies | none | Browser cookies or a `cookies.txt` file |
| Maximum retries | 5 | Retries for network problems before the task becomes Interrupted |
| Initial retry delay | 3 s | Doubles after every failed attempt (max 120 s) |
| Concurrent downloads | 1 | Number of tasks downloaded at the same time |
| Start queued downloads automatically | On | New tasks start without pressing Start Queue |
| Resume automatically when the network is back | On | Interrupted tasks continue by themselves |
| Keep partial downloads | On | Needed for Pause/Resume and recovery |
| Resume unfinished downloads after a restart | Off | Recovered tasks start automatically instead of waiting |
| Quality preset | Best Quality | Best, Balanced, Small, Compatible or Custom |
| Speed limit | Unlimited | KB/s per download (yt-dlp rate limit) |
| Download schedule | Off | Start queued downloads only between two times (a window may cross midnight) |
| Language / Theme | English / Dark | English or Arabic (RTL, restart needed); dark or light |
| Clipboard monitor | Off | Put a copied link into the Downloader |
| System tray, close/minimise to tray | On / Off / Off | Keep running in the tray |
| Notifications | On | Tray balloon when a download completes or fails |
| Check for updates, release address | Off / empty | Needs a GitHub-releases style https address |
| Log verbosity | Info | Debug shows technical details |

Settings are stored as JSON (`%APPDATA%\VideoDownloader` in the packaged app, otherwise the project folder).

## How the important parts work

### Why a video could play only audio (and the fix)
The old sort order ranked codecs AV1 > VP9 > H.264 while the default container was MP4, so players such as QQPlayer
received AV1/VP9 video (plus Opus audio) inside `.mp4`, decoded the sound and showed a black picture.
Now MP4 prefers H.264 + AAC, every output is inspected with ffprobe (video stream present, audio present when expected,
valid codec/dimensions/duration), and Compatibility mode converts what is not H.264/AAC. If an engine produces an
audio-only "video" the result is rejected and the next engine runs; if every engine does, the task fails with the
reason instead of claiming success.

### Pause and resume
Pause raises a stop request that the engine honours at its next progress callback. The partial file stays in
`<download folder>/.vdl_tmp/<task id>` together with a small state file (URL + quality signature). Resume reuses that
folder and yt-dlp continues from the partial file (`continuedl`); a partial written for a different URL/quality, or
an empty one, is discarded. The task stays in the queue the whole time and its state is saved in SQLite.
Resume continues "from the previous position" only where the server supports HTTP range requests (YouTube does).
Partial folders older than 7 days are cleaned.

### Network recovery
Connection errors (timeouts, DNS, resets) and temporary file locks are retried with exponential backoff
(`delay = initial × 2^(attempt-1)`, capped at 120 s). When the retries run out the task becomes **Interrupted**, the
partial data is kept and a lightweight probe checks the network every 15 s; when it succeeds the task resumes by
itself (or press Resume). Completed files and finished playlist items are never downloaded again.

### Cancel
Cancel stops the transfer, marks the task *Cancelled* and asks: *Keep partial files* (a later Retry continues from
them) or *Delete partial files*. Finished downloads are never touched.

### Partial-file protection
Each attempt writes into its own folder, only one engine can claim a target at a time, a stopped attempt's folder is
removed when it cannot be resumed, finished files are validated and moved with an atomic rename without
overwriting, and only folders created by this application are ever deleted.

### Fallback order
1. The platform is detected from the URL. 2. Engines that cannot handle it are skipped with a logged reason.
3. Each remaining engine runs in a private temp folder. 4. Transient errors are retried; permanent errors (invalid
URL, private/deleted video, disk full, permission denied) stop the chain. 5. Output is validated before it counts.

## Troubleshooting

| Problem | What to do |
|---|---|
| `Unexpected response from webpage request` (TikTok, etc.) | The site changed. Diagnostics > Dependencies > **Update download engines**, then restart. The app also tries the other engines and uses browser impersonation (`curl-cffi`) for non-YouTube sites. In the exe build, rerun `make_exe.bat`. |
| Downloads suddenly fail on one site | `pip install -U yt-dlp pytubefix gallery-dl you-get` |
| `No supported JavaScript runtime could be found` | Install Deno (`winget install DenoLand.Deno`), restart the app |
| "Requested format is not available" | Same as above, and install FFmpeg; update `yt-dlp[default]` |
| "Sign in to confirm you're not a bot" / age-restricted | Configure cookies in Settings |
| Only low resolutions / no MP3 | Install FFmpeg or set its path |
| Video plays audio only | Keep *Compatibility mode* on; run `ffprobe file.mp4` to see the codecs |
| `WinError 32` while resuming | Antivirus/indexer briefly locking the file; the app retries automatically |
| Slow downloads | Raise *Download connections*, install aria2c, check the Deno/Node runtime is found |
| Playlist shows fewer items than expected | Private/deleted items are listed as "(unavailable)" and skipped |
| Need details | Console button (Ctrl+J) or `logs/application.log`; set *Log verbosity* to Debug |

## Building a Windows .exe with an icon

Run these steps **on Windows** (PyInstaller builds for the OS it runs on).

### Quickest way: one click
Double-click **`make_exe.bat`** in the project folder. It creates a virtual environment, installs everything, builds
the application with your icon and writes `dist\VideoDownloader\VideoDownloader.exe` plus
`dist\VideoDownloader_portable.zip`. To ship FFmpeg with it, put `ffmpeg.exe` and `ffprobe.exe` in `tools\bin` first.
No Windows PC at hand? Push the project to GitHub and run the *Build Windows exe* workflow (`.github/workflows`).

The manual steps below do the same thing.

### 1. Prepare
```bat
cd video_downloader
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements-dev.txt
```

### 2. The icon
The project already contains `assets\icons\app.ico` (16–256 px) and `app.png`. To use your own icon, replace
`app.ico` (a real multi-size `.ico`, ideally containing 16, 32, 48, 64, 128 and 256 px) and keep the name, or
regenerate the default one:
```bat
pip install pillow
python tools\make_icon.py
```
The same file is used for the `.exe` file icon (PyInstaller `--icon`), the window icon and the taskbar.

### 3. Build (recommended: one-folder)
```bat
pyinstaller --noconfirm --clean VideoDownloader.spec
```
or simply run `build_windows.bat`. Result: `dist\VideoDownloader\VideoDownloader.exe` (the whole `dist\VideoDownloader`
folder is the application; copy or zip the entire folder).

Equivalent command line without the spec file:
```bat
pyinstaller --noconfirm --clean --windowed --name VideoDownloader --icon assets\icons\app.ico ^
  --collect-all yt_dlp --collect-all pytubefix --collect-all gallery_dl --collect-all you_get ^
  --hidden-import gallery_dl.__main__ --hidden-import you_get.__main__ --hidden-import runpy ^
  --add-data "assets;assets" --exclude-module tkinter main.py
```

### 4. Single-file build (optional)
`build_onefile_windows.bat` produces `dist\VideoDownloader.exe`. It starts slower (it unpacks to a temp folder on every
launch) and antivirus programs flag one-file builds more often, so prefer the one-folder build.

### 5. Add the external tools
- **FFmpeg is not bundled** (size and licence). Either install it on the target PC, or copy `ffmpeg.exe` **and**
  `ffprobe.exe` next to `VideoDownloader.exe` — they are detected automatically.
- Deno/Node is also not bundled; install it on the target PC for YouTube, or copy `deno.exe` next to the exe and add that
  folder to `PATH`.
- Distribute FFmpeg only in line with its licence.

### 6. Test the build on a clean machine (a VM without Python)
1. Copy `dist\VideoDownloader`, run the exe: the window opens with your icon and the taskbar shows it.
2. Download a short public video at 360p; then install/point to FFmpeg and try 1080p and MP3.
3. Try a playlist, Pause → Resume, Cancel, then close and reopen the app and check queue/history recovery.

### 7. Installer (Inno Setup)
The project contains `installer\VideoDownloader.iss` (English + Arabic, start-menu and optional desktop shortcut,
per-user install without admin rights, uninstaller, your icon). Install Inno Setup 6
(`winget install JRSoftware.InnoSetup`) and run:
```bat
tools\build_installer.bat
```
It builds the one-folder app and then `dist\VideoDownloader_Setup_<version>.exe`. The version comes from `app_info.py`
(single source of truth: also shown in the sidebar and the diagnostic report). FFmpeg and Deno are not bundled.

### Build notes
- If the build fails with `ModuleNotFoundError`, add the module with `--hidden-import <name>`.
- If the icon does not change in Explorer after rebuilding, Windows is showing a cached icon: rename the exe or run
  `ie4uinit.exe -show`.
- Build size is large (~150–300 MB) because Qt and four download libraries are included.
- When frozen, gallery-dl and you-get run as `VideoDownloader.exe --vdl-run-module <module>` (handled in `main.py`).
- Settings, history and logs live in `%APPDATA%\VideoDownloader` in the packaged app.

## Tests

```bash
pip install pytest
python -m pytest -q tests
```

The suite covers quality/format selection, ffprobe validation and compatibility conversion (using real media generated
with ffmpeg), file safety, history and queue persistence, settings, cookies, the fallback chain, retries/backoff,
pause/resume/cancel/interruption with mock engines (through a small Qt stub, so it runs without PyQt6), playlist
selection and thumbnails. **Not covered by automated tests:** real downloads from real sites, yt-dlp's real HTTP
resume, playback in a particular player, and the visual GUI — try those by hand.

## Project structure

```
main.py                    entry point (first-run wizard, theme, language/RTL, icon; embedded gallery-dl/you-get when frozen)
app_info.py                name, version, version comparison
gui/                       main_window (sidebar shell), sidebar, dashboard_page, library_page, diagnostics_page, wizard,
                           tray, i18n (English/Arabic), styles (dark/light QSS), widgets, thumbnails, queue_widget,
                           history_widget, settings_dialog (the Settings page)
core/                      download_manager (queue <-> workers), download_worker (QThreads), task_runner, download_queue,
                           download_history, fallback_manager, quality_manager, ffmpeg_manager (probe/validate/convert),
                           thumbnail_tools, media_library, scheduler, diagnostics (error centre, report), updater,
                           cookie_manager, playlist_manager, platform_detector
engines/                   base_engine, ytdlp_engine, pytubefix_engine, gallery_dl_engine, youget_engine
utils/                     file_manager, validators, logger, network (error classification, image fetch), system_info
config/settings.py         JSON settings with validation
assets/icons/              app.ico, app.png      tools/make_icon.py   regenerates them
VideoDownloader.spec       PyInstaller spec      build_windows.bat / build_onefile_windows.bat
installer/VideoDownloader.iss + tools/build_installer.bat   Windows installer
tests/                     pytest suite
```

Threads: workers (`QThread`) never touch widgets; they emit signals which `DownloadManager` (GUI thread) turns into
queue updates. Tables `history` and `queue_tasks` live in `database/download_history.db`.

## Legal notes

Download only content you have the right to download and respect each site's terms of service and copyright law.
This project is a front end for the open-source libraries it uses and does not circumvent DRM.

---

## ملخص سريع بالعربية

**التشغيل:**
```
pip install -r requirements.txt
python main.py
```
ثبّت أيضاً FFmpeg وDeno على ويندوز: `winget install Gyan.FFmpeg` ثم `winget install DenoLand.Deno`.

**الاستخدام:** الصق الرابط وسيُحلَّل تلقائياً. للبلاي لست تظهر قائمة بالفيديوهات مع صورها لتختار منها.
أزرار Pause / Resume / Cancel في بطاقة التقدم وفي تبويب Queue. عند انقطاع النت تصبح المهمة Interrupted وتُستكمل
تلقائياً عند عودة الاتصال.

**تحويل البرنامج إلى EXE بأيقونة (على ويندوز):**
```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements-dev.txt
pyinstaller --noconfirm --clean VideoDownloader.spec
```
الناتج: `dist\VideoDownloader\VideoDownloader.exe`، والأيقونة من `assets\icons\app.ico` (استبدل الملف بأيقونتك وأعد
البناء). انسخ `ffmpeg.exe` و`ffprobe.exe` بجانب الـ exe أو ثبّتهما على الجهاز.
