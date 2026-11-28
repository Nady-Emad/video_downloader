# -*- mode: python ; coding: utf-8 -*-
# Build notes
#  * console=False  -> GUI-only exe (no terminal). Child processes are also hidden: see utils/no_console.py.
#  * One-folder (not one-file): a one-file exe unpacks itself into %TEMP% on EVERY launch (slow start, antivirus
#    scans). The installer ships this folder, so nothing is extracted at run time.
#  * upx=False on purpose: UPX makes every start slower (decompression) and is a common antivirus false-positive
#    trigger. It only shrinks the download size.
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
    hookspath=[], runtime_hooks=[], noarchive=False,
    # Not used by the app (smaller folder, fewer files for antivirus to scan on every start). Everything the
    # download libraries need is collected above, so nothing functional is removed.
    excludes=["tkinter", "pytest", "_pytest", "IPython", "matplotlib", "numpy", "pandas", "scipy", "PIL.ImageTk",
              "PyQt6.QtWebEngineCore", "PyQt6.QtWebEngineWidgets", "PyQt6.QtQml", "PyQt6.QtQuick",
              "PyQt6.QtQuickWidgets", "PyQt6.Qt3DCore", "PyQt6.QtMultimedia", "PyQt6.QtBluetooth",
              "PyQt6.QtSensors", "PyQt6.QtSerialPort", "PyQt6.QtPdf", "PyQt6.QtDesigner", "PyQt6.QtTest"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True, name="VideoDownloader", debug=False, bootloader_ignore_signals=False,
    strip=False, upx=False, console=False, icon="assets/icons/app.ico",
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="VideoDownloader")
