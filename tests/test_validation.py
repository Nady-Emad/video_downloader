"""Stream validation and compatibility mode, using real media generated with ffmpeg."""
import shutil
import subprocess

import pytest

from core import quality_manager as q
from core.cookie_manager import CookieConfig
from core.fallback_manager import DownloadFailed, FallbackManager
from core.ffmpeg_manager import FfmpegManager
from engines.base_engine import CancelToken
from tests.helpers import FakeEngine

pytestmark = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
                                reason="ffmpeg/ffprobe not installed")


def ff(*args):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args], check=True)


V = ["-f", "lavfi", "-i", "testsrc=duration=1:size=128x72:rate=10"]
A = ["-f", "lavfi", "-i", "sine=frequency=440:duration=1"]


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    d = tmp_path_factory.mktemp("media")
    files = {
        "h264_aac": d / "h264_aac.mp4", "vp9_aac": d / "vp9_aac.mp4", "vp9_opus": d / "vp9_opus.mp4",
        "audio_as_mp4": d / "audio_as_mp4.mp4", "video_only": d / "video_only.mp4",
        "mp3": d / "a.mp3", "cover": d / "cover.png", "mp3_cover": d / "mp3_cover.mp3",
        "h264_10bit": d / "h264_10bit.mp4",
    }
    ff(*V, *A, "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(files["h264_aac"]))
    ff(*V, *A, "-c:v", "libvpx-vp9", "-deadline", "realtime", "-c:a", "aac", "-shortest", str(files["vp9_aac"]))
    opus = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
    if "libopus" in opus:
        ff(*V, *A, "-c:v", "libvpx-vp9", "-deadline", "realtime", "-c:a", "libopus", "-shortest",
           str(files["vp9_opus"]))
    ff(*A, "-c:a", "aac", "-f", "mp4", str(files["audio_as_mp4"]))                    # AUDIO ONLY inside .mp4
    ff(*V, "-c:v", "libx264", "-pix_fmt", "yuv420p", str(files["video_only"]))
    ff(*A, "-c:a", "libmp3lame", str(files["mp3"]))
    ff("-f", "lavfi", "-i", "color=c=red:s=64x64", "-frames:v", "1", str(files["cover"]))
    ff("-i", str(files["mp3"]), "-i", str(files["cover"]), "-map", "0:a", "-map", "1:v", "-c", "copy",
       str(files["mp3_cover"]))
    ff(*V, *A, "-c:v", "libx264", "-pix_fmt", "yuv444p", "-c:a", "aac", "-shortest", str(files["h264_10bit"]))
    return files


@pytest.fixture(scope="module")
def fm():
    return FfmpegManager()


def test_valid_h264_aac(media, fm):
    r = fm.validate_output(media["h264_aac"], expect_video=True)
    assert r.ok and r.streams.video_codec == "h264" and r.streams.audio_codec == "aac"
    assert r.streams.width == 128 and r.streams.duration > 0 and not r.warnings
    assert fm.needs_compat_transcode(r.streams) == (False, False)


def test_audio_only_file_is_rejected_as_video(media, fm):
    """The reported symptom: a 'video' download that is really just audio must never count as success."""
    r = fm.validate_output(media["audio_as_mp4"], expect_video=True)
    assert not r.ok and "audio only" in r.message


def test_audio_only_accepted_when_audio_expected(media, fm):
    assert fm.validate_output(media["mp3"], expect_video=False).ok
    assert fm.validate_output(media["audio_as_mp4"], expect_video=False).ok


def test_cover_art_is_not_counted_as_video(media, fm):
    assert not fm.validate_output(media["mp3_cover"], expect_video=True).ok
    r = fm.validate_output(media["mp3_cover"], expect_video=False)
    assert r.ok and not r.streams.has_video


def test_silent_video_is_accepted_with_warning(media, fm):
    r = fm.validate_output(media["video_only"], expect_video=True)
    assert r.ok and r.warnings and "no audio" in r.warnings[0]


def test_corrupt_and_empty_files(tmp_path, fm):
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"garbage" * 500)
    assert not fm.validate_output(bad, expect_video=True).ok
    empty = tmp_path / "e.mp4"
    empty.write_bytes(b"")
    assert not fm.validate_output(empty, expect_video=True).ok
    assert not fm.validate_output(tmp_path / "missing.mp4", expect_video=True).ok


def test_without_ffprobe_audio_extension_still_rejected(tmp_path):
    m = FfmpegManager()
    m.ffprobe_path = None
    f = tmp_path / "x.m4a"
    f.write_bytes(b"data")
    assert not m.validate_output(f, expect_video=True).ok
    g = tmp_path / "x.mp4"
    g.write_bytes(b"data")
    r = m.validate_output(g, expect_video=True)
    assert r.ok and r.warnings                      # cannot verify, but says so


def test_compat_transcode_vp9_to_h264(media, fm, tmp_path):
    streams = fm.inspect(media["vp9_aac"])
    assert streams.video_codec == "vp9" and fm.needs_compat_transcode(streams) == (True, False)
    out = fm.make_compatible(media["vp9_aac"], tmp_path / "out.mp4", streams)
    r = fm.validate_output(out, expect_video=True)
    assert r.ok and r.streams.video_codec == "h264" and r.streams.audio_codec == "aac"
    assert r.streams.pix_fmt == "yuv420p"


def test_compat_transcodes_non_420_pixel_format(media, fm, tmp_path):
    streams = fm.inspect(media["h264_10bit"])
    assert fm.needs_compat_transcode(streams)[0]
    out = fm.make_compatible(media["h264_10bit"], tmp_path / "o.mp4", streams)
    assert fm.inspect(out).pix_fmt == "yuv420p"


def test_compat_opus_audio_converted_to_aac(media, fm, tmp_path):
    if not media["vp9_opus"].exists():
        pytest.skip("libopus encoder not available")
    streams = fm.inspect(media["vp9_opus"])
    assert fm.needs_compat_transcode(streams) == (True, True)
    out = fm.make_compatible(media["vp9_opus"], tmp_path / "o.mp4", streams)
    r = fm.inspect(out)
    assert r.video_codec == "h264" and r.audio_codec == "aac"


def test_cover_embedding_and_jpeg_conversion(media, fm, tmp_path):
    out = fm.embed_cover(media["mp3"], media["cover"], tmp_path / "with_cover.mp3")
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0",
                            str(out)], capture_output=True, text=True).stdout.split()
    assert "video" in probe and "audio" in probe                       # attached picture present
    jpg = fm.to_jpeg(media["cover"], tmp_path / "c.jpg")
    assert jpg.read_bytes()[:2] == b"\xff\xd8"


# ----------------------------------------------------------------------------- through the fallback manager
def engine_writing(name, source):
    def behavior(request, attempt, cancel):
        target = request.work_dir / ("media" + source.suffix)
        shutil.copy(source, target)
        return target
    return FakeEngine(name, behavior)


def run(engines, dest, spec, ffmpeg):
    logs = []
    manager = FallbackManager(engines, ffmpeg, max_retries=0, retry_delay=0)
    result = manager.run("https://example.com/v", "YouTube", spec, CookieConfig(), dest, lambda t: t,
                         lambda p: None, lambda l, m: logs.append((l, m)), CancelToken(), title_hint="")
    return result, logs


def test_audio_only_result_from_primary_engine_triggers_fallback(media, fm, tmp_path):
    bad = engine_writing("yt-dlp", media["audio_as_mp4"])
    good = engine_writing("pytubefix", media["h264_aac"])
    result, logs = run([bad, good], tmp_path, q.build_spec("720", "mp4", 0), fm)
    assert result.engine == "pytubefix"
    assert any("audio only" in m for _, m in logs)                      # failure reason is reported
    assert fm.inspect(result.file_path).has_video
    assert [p.name for p in tmp_path.iterdir()] == [result.file_path.name]


def test_audio_only_from_every_engine_fails_instead_of_succeeding(media, fm, tmp_path):
    engines = [engine_writing("yt-dlp", media["audio_as_mp4"]), engine_writing("pytubefix", media["audio_as_mp4"])]
    with pytest.raises(DownloadFailed) as err:
        run(engines, tmp_path, q.build_spec("720", "mp4", 0), fm)
    assert "audio only" in err.value.info.message
    assert list(tmp_path.iterdir()) == []                               # nothing left behind


def test_compat_mode_converts_vp9_mp4(media, fm, tmp_path):
    spec = q.build_spec("720", "mp4", 0)
    assert spec.compat
    result, logs = run([engine_writing("yt-dlp", media["vp9_aac"])], tmp_path, spec, fm)
    info = fm.inspect(result.file_path)
    assert info.video_codec == "h264" and result.file_path.suffix == ".mp4"
    assert any("Compatibility mode" in m for _, m in logs)


def test_compat_mode_off_keeps_original_codec(media, fm, tmp_path):
    spec = q.build_spec("720", "mp4", 0)
    spec.compat = False
    result, logs = run([engine_writing("yt-dlp", media["vp9_aac"])], tmp_path, spec, fm)
    assert fm.inspect(result.file_path).video_codec == "vp9"
    assert not any("Compatibility mode" in m for _, m in logs)


def test_compatible_file_is_not_re_encoded(media, fm, tmp_path):
    result, logs = run([engine_writing("yt-dlp", media["h264_aac"])], tmp_path, q.build_spec("720", "mp4", 0), fm)
    assert not any("Compatibility mode" in m for _, m in logs)
    assert result.file_path.read_bytes() == media["h264_aac"].read_bytes()      # copied, untouched


def test_mp3_download_validates_as_audio(media, fm, tmp_path):
    result, _ = run([engine_writing("yt-dlp", media["mp3"])], tmp_path, q.build_spec("audio", "mp4", 192), fm)
    assert result.file_path.suffix == ".mp3" and fm.inspect(result.file_path).has_audio


# ----------------------------------------------------------------------------- selector regression
def test_sort_never_prefers_av1_for_mp4():
    for spec in (q.build_spec("1080", "mp4", 0), q.build_spec("best", "mp4", 0)):
        sort = q.ytdlp_sort(spec)
        assert "codec" not in sort                                     # the old bug: av01 > vp9 > h264
        assert "vcodec:h264" in sort and "acodec:m4a" in sort
        assert sort.index("vcodec:h264") < sort.index("fps")           # codec outranks frame rate


def test_sort_codec_rules():
    webm = q.build_spec("best", "webm", 0)
    assert "vcodec:vp9" in q.ytdlp_sort(webm) and "acodec:opus" in q.ytdlp_sort(webm)
    mkv = q.build_spec("best", "mkv", 0)
    mkv.compat = False
    assert not any(s.startswith("vcodec") for s in q.ytdlp_sort(mkv))
    mp4_av1 = q.build_spec("best", "mp4", 0)
    mp4_av1.codec, mp4_av1.compat = "av1", False
    assert "vcodec:av01" in q.ytdlp_sort(mp4_av1)
    mp4_av1.compat = True                                              # compatibility overrides
    assert "vcodec:h264" in q.ytdlp_sort(mp4_av1)
    size = q.build_spec("custom", "mp4", 0, {"max_height": 720, "priority": "size"})
    assert "+size" in q.ytdlp_sort(size) and "vcodec:h264" in q.ytdlp_sort(size)
