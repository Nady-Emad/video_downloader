@echo off
REM Builds installer\VideoDownloader_Setup.exe from the finished one-folder app in dist\VideoDownloader.
REM Needs Inno Setup 6. If it is missing this script tries to install it with winget and FAILS LOUDLY otherwise;
REM it never reports success without a real installer file.
setlocal
cd /d "%~dp0\.."

if not exist "dist\VideoDownloader\VideoDownloader.exe" (
  echo ERROR: dist\VideoDownloader\VideoDownloader.exe not found. Build the application first ^(make_exe.bat^).
  exit /b 1
)
for /f %%v in ('python -c "import app_info; print(app_info.VERSION)"') do set APPVER=%%v

call :find_iscc
if not defined ISCC (
  echo Inno Setup 6 was not found. Trying to install it with winget...
  winget install --id JRSoftware.InnoSetup -e --silent --accept-package-agreements --accept-source-agreements
  call :find_iscc
)
if not defined ISCC (
  echo ERROR: Inno Setup 6 is not installed. Install it from https://jrsoftware.org/isdl.php and run this again.
  exit /b 1
)

if exist "installer\VideoDownloader_Setup.exe" del /q "installer\VideoDownloader_Setup.exe"
"%ISCC%" /DAppVersion=%APPVER% "installer\VideoDownloader.iss" || (echo ERROR: Inno Setup failed. & exit /b 1)

if not exist "installer\VideoDownloader_Setup.exe" (
  echo ERROR: installer\VideoDownloader_Setup.exe was not produced.
  exit /b 1
)
REM The installer contains the whole app folder, so it must be clearly bigger than the app exe alone.
for %%A in ("installer\VideoDownloader_Setup.exe") do set SETUPSIZE=%%~zA
if %SETUPSIZE% LSS 5000000 (
  echo ERROR: the installer is suspiciously small ^(%SETUPSIZE% bytes^).
  exit /b 1
)
echo.
echo Application: dist\VideoDownloader\VideoDownloader.exe   ^(opens the program directly^)
echo Installer  : installer\VideoDownloader_Setup.exe        ^(opens the installation wizard^)
exit /b 0

:find_iscc
set "ISCC="
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
exit /b 0
