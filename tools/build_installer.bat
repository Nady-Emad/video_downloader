@echo off
REM Builds the one-folder app, then the installer (needs Inno Setup 6: winget install JRSoftware.InnoSetup)
setlocal
for /f %%v in ('python -c "import app_info; print(app_info.VERSION)"') do set APPVER=%%v
call build_windows.bat || exit /b 1
set ISCC="%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist %ISCC% set ISCC="%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
%ISCC% /DAppVersion=%APPVER% installer\VideoDownloader.iss || exit /b 1
echo Installer: dist\VideoDownloader_Setup_%APPVER%.exe
