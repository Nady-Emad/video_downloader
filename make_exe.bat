@echo off
REM One-click Windows build: double-click this file (or run it from a terminal opened in this folder).
REM Result: dist\VideoDownloader\VideoDownloader.exe        = the application (opens directly)
REM         installer\VideoDownloader_Setup.exe             = the Windows installation wizard
REM         dist\VideoDownloader_portable.zip               = portable copy
setlocal
cd /d "%~dp0"

where py >nul 2>nul && (set "PY=py -3") || (set "PY=python")
%PY% --version >nul 2>nul || (
  echo Python 3.11 or newer was not found. Install it from https://www.python.org and tick "Add python.exe to PATH".
  goto fail
)

if not exist ".venv\Scripts\python.exe" (
  echo [1/6] Creating virtual environment...
  %PY% -m venv .venv || goto fail
)
call ".venv\Scripts\activate.bat" || goto fail

echo [2/6] Installing build requirements (a few minutes the first time)...
python -m pip install --upgrade pip >nul
python -m pip install -r requirements-dev.txt || goto fail

echo [3/6] Building the application with PyInstaller...
python -m PyInstaller --noconfirm --clean VideoDownloader.spec || goto fail
if not exist "dist\VideoDownloader\VideoDownloader.exe" goto fail

echo [4/6] Adding FFmpeg if you placed it in tools\bin (optional)...
if exist "tools\bin\ffmpeg.exe"  copy /y "tools\bin\ffmpeg.exe"  "dist\VideoDownloader\" >nul
if exist "tools\bin\ffprobe.exe" copy /y "tools\bin\ffprobe.exe" "dist\VideoDownloader\" >nul

echo [5/6] Building the Windows installer (Inno Setup)...
call tools\build_installer.bat || goto fail

echo [6/6] Creating the portable zip...
powershell -NoProfile -Command "Compress-Archive -Path 'dist\VideoDownloader' -DestinationPath 'dist\VideoDownloader_portable.zip' -Force"

echo.
echo ==============================================================
echo  App      : dist\VideoDownloader\VideoDownloader.exe
echo  Installer: installer\VideoDownloader_Setup.exe  ^<-- give THIS file to users
echo  Zip :  dist\VideoDownloader_portable.zip
echo  FFmpeg is not included unless tools\bin\ffmpeg.exe and ffprobe.exe existed.
echo  Otherwise install it: winget install Gyan.FFmpeg
echo ==============================================================
if not defined NOPAUSE explorer "installer"
if not defined NOPAUSE pause
exit /b 0

:fail
echo.
echo BUILD FAILED. Scroll up for the first error message and send it to get help.
if not defined NOPAUSE pause
exit /b 1
