@echo off
REM Copies this project to C:\vd\video_downloader (plain ASCII path: Arabic letters/brackets in a folder name can
REM break batch files, PyInstaller and Inno Setup), builds BOTH the app and the installer there, and keeps the
REM complete output in C:\vd\build_log.txt. Double-click this file and wait for the final message.
setlocal
set "TARGET=C:\vd\video_downloader"
if not exist "C:\vd" mkdir "C:\vd"
robocopy "%~dp0." "%TARGET%" /E /XD .venv dist build installer\Output __pycache__ .pytest_cache /XF *.pyc /NFL /NDL /NJH /NJS >nul
cd /d "%TARGET%" || (echo Could not enter %TARGET% & pause & exit /b 1)
set NOPAUSE=1
echo Building in %TARGET% ... this takes several minutes. Full log: C:\vd\build_log.txt
call make_exe.bat > "C:\vd\build_log.txt" 2>&1
set RESULT=%ERRORLEVEL%
echo.
if exist "%TARGET%\dist\VideoDownloader\VideoDownloader.exe" (echo [OK]   %TARGET%\dist\VideoDownloader\VideoDownloader.exe) else (echo [MISSING] application exe)
if exist "%TARGET%\installer\VideoDownloader_Setup.exe"      (echo [OK]   %TARGET%\installer\VideoDownloader_Setup.exe)      else (echo [MISSING] installer exe)
if not "%RESULT%"=="0" (echo BUILD FAILED. Send the LAST 30 lines of C:\vd\build_log.txt.)
pause
exit /b %RESULT%
