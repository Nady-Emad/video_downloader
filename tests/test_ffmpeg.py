import shutil
import subprocess

import pytest

from core.ffmpeg_manager import FfmpegManager
from engines.base_engine import CancelToken, DownloadCancelled, EngineError

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


def make_media(path, video=True, audio=True, seconds=1):
    cmd = ["ffmpeg", "-y", "-loglevel", "error"]
    if video:
        cmd += ["-f", "lavfi", "-i", f"testsrc=duration={seconds}:size=64x64:rate=5"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}"]
    if video:
        cmd += ["-pix_fmt", "yuv420p"]
    cmd += [str(path)]
    subprocess.run(cmd, check=True)
    return path


def test_detect_and_status():
    m = FfmpegManager()
    assert m.available and "ffmpeg" in m.version.lower()
    assert m.limitations() == []


def test_custom_path_and_missing(tmp_path):
    m = FfmpegManager(shutil.which("ffmpeg"))
    assert m.available
    missing = FfmpegManager(str(tmp_path / "nope"))
    assert missing.available                       # falls back to PATH
    import core.ffmpeg_manager as mod
    orig = mod.shutil.which
    mod.shutil.which = lambda *_: None
    try:
        none = FfmpegManager(str(tmp_path / "nope"))
        assert not none.available and none.location is None and none.limitations()
        with pytest.raises(EngineError):
            none.convert_to_mp3(tmp_path / "a.wav", tmp_path / "a.mp3")
        ok, _ = none.validate_media(tmp_path / "x")
        assert not ok
    finally:
        mod.shutil.which = orig


def test_merge_and_mp3(tmp_path):
    m = FfmpegManager()
    v = make_media(tmp_path / "v.mp4", audio=False)
    a = make_media(tmp_path / "a.m4a", video=False)
    out = m.merge(v, a, tmp_path / "out.mp4")
    ok, msg = m.validate_media(out)
    assert ok, msg
    mkv = m.merge(v, a, tmp_path / "out.mkv")
    assert m.validate_media(mkv)[0]
    mp3 = m.convert_to_mp3(a, tmp_path / "a.mp3", 128)
    assert mp3.stat().st_size > 0 and m.validate_media(mp3)[0]
    best = m.convert_to_mp3(a, tmp_path / "best.mp3", 0)
    assert best.exists()


def test_conversion_failure_reported(tmp_path):
    m = FfmpegManager()
    bad = tmp_path / "bad.m4a"
    bad.write_bytes(b"not audio")
    with pytest.raises(EngineError) as err:
        m.convert_to_mp3(bad, tmp_path / "bad.mp3", 128)
    assert err.value.kind == "ffmpeg"


def test_validate_rejects_corrupt_and_empty(tmp_path):
    m = FfmpegManager()
    empty = tmp_path / "e.mp4"
    empty.write_bytes(b"")
    assert not m.validate_media(empty)[0]
    junk = tmp_path / "j.mp4"
    junk.write_bytes(b"garbage" * 100)
    assert not m.validate_media(junk)[0]


def test_cancel_during_ffmpeg(tmp_path):
    m = FfmpegManager()
    a = make_media(tmp_path / "a.m4a", video=False, seconds=1)
    token = CancelToken()
    token.cancel()
    with pytest.raises(DownloadCancelled):
        m.convert_to_mp3(a, tmp_path / "c.mp3", 128, token)
