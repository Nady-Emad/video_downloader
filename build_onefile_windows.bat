@echo off
REM One-file Windows build. Slower to start (unpacks to a temp folder on every launch).
python -m pip install -r requirements-dev.txt || exit /b 1
pyinstaller --noconfirm --clean --onefile --windowed --name VideoDownloader --icon assets\icons\app.ico ^
  --collect-all yt_dlp --collect-all pytubefix --collect-all gallery_dl --collect-all you_get ^
  --hidden-import gallery_dl.__main__ --hidden-import you_get.__main__ --hidden-import runpy ^
  --add-data "assets;assets" --exclude-module tkinter main.py || exit /b 1
echo Build finished: dist\VideoDownloader.exe
