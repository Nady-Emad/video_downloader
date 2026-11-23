# -*- mode: python ; coding: utf-8 -*-
# One-folder build:   pyinstaller --noconfirm --clean VideoDownloader.spec
from PyInstaller.utils.hooks import collect_all

datas, binaries, hiddenimports = [("assets", "assets")], [], [
    "yt_dlp", "pytubefix", "gallery_dl", "you_get", "gallery_dl.__main__", "you_get.__main__",
    "runpy",
]
# Download libraries load extractors dynamically, so collect every submodule and data file.
for package in ("yt_dlp", "pytubefix", "gallery_dl", "you_get"):
    d, b, h = collect_all(package)
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(
    ["main.py"], pathex=["."], binaries=binaries, datas=datas, hiddenimports=hiddenimports,
    hookspath=[], runtime_hooks=[], excludes=["tkinter", "pytest"], noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True, name="VideoDownloader", debug=False, bootloader_ignore_signals=False,
    strip=False, upx=False, console=False, icon="assets/icons/app.ico",
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="VideoDownloader")
