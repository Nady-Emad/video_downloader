@echo off
REM One-folder Windows build (recommended). Output: dist\VideoDownloader\VideoDownloader.exe
python -m pip install -r requirements-dev.txt || exit /b 1
python -m PyInstaller --noconfirm --clean VideoDownloader.spec || exit /b 1
echo.
echo Build finished: dist\VideoDownloader\VideoDownloader.exe
echo FFmpeg is NOT bundled. Place ffmpeg.exe next to the application or install it (see README).
