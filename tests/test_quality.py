from core import quality_manager as q
from engines.base_engine import FormatInfo, MediaInfo


def fmts():
    return [
        FormatInfo("137", "mp4", 1080, 30, "avc1.640028", "none", 50_000_000),
        FormatInfo("136", "mp4", 720, 30, "avc1.4d401f", "none", 25_000_000),
        FormatInfo("22", "mp4", 720, 30, "avc1.64001F", "mp4a.40.2", 30_000_000),
        FormatInfo("18", "mp4", 360, 30, "avc1.42001E", "mp4a.40.2", 9_000_000),
        FormatInfo("140", "m4a", None, None, "none", "mp4a.40.2", 3_000_000, abr=128),
    ]


def test_build_spec_variants():
    assert q.build_spec("best", "mp4", 192).max_height is None
    assert q.build_spec("1080", "mkv", 192).max_height == 1080
    audio = q.build_spec("audio", "mp4", 320)
    assert audio.is_audio and audio.audio_bitrate == 320
    custom = q.build_spec("custom", "mp4", 128, {"max_height": 540, "container": "webm", "audio_format": "best",
                                                  "audio_bitrate": 0, "priority": "size"})
    assert custom.max_height == 540 and custom.container == "webm" and custom.priority == "size"


def test_spec_roundtrip():
    spec = q.build_spec("720", "mkv", 256)
    assert q.QualitySpec.from_dict(spec.to_dict()) == spec


def test_pick_height_never_exceeds_max():
    assert q.pick_height([1080, 720, 360], 1080) == 1080
    assert q.pick_height([1080, 720, 360], 900) == 720
    assert q.pick_height([1080, 720], 240) is None
    assert q.pick_height([1080, 720], None) == 1080
    assert q.pick_height([], 720) is None


def test_enabled_keys_only_available_resolutions():
    keys = q.enabled_video_keys(fmts())
    assert {"best", "audio", "custom", "1080", "720", "360"} <= keys
    assert "2160" not in keys and "480" not in keys


def test_ytdlp_format_with_and_without_ffmpeg():
    spec = q.build_spec("720", "mp4", 192)
    assert q.ytdlp_format(spec, True) == "bv*+ba/b"
    no_ff = q.ytdlp_format(spec, False)
    assert "+" not in no_ff            # never requires merging
    assert q.ytdlp_format(q.build_spec("audio", "mp4", 192), True) == "ba/b"
    assert "webm" in q.ytdlp_format(q.build_spec("best", "webm", 0), True)


def test_ytdlp_sort_respects_max_and_priority():
    assert q.ytdlp_sort(q.build_spec("720", "mp4", 0))[0] == "res:720"
    assert "+size" in q.ytdlp_sort(q.build_spec("custom", "mp4", 0, {"max_height": 720, "priority": "size"}))


def test_summary_exact_and_closest():
    info = MediaInfo(url="u", formats=fmts())
    exact = q.summarize_selection(info, q.build_spec("1080", "mp4", 0), True)
    assert exact.selected_resolution == 1080 and exact.needs_ffmpeg and exact.estimated_size == 53_000_000
    closest = q.summarize_selection(info, q.build_spec("custom", "mp4", 0, {"max_height": 900}), True)
    assert closest.selected_resolution == 720 and any("closest" in n for n in closest.notes)


def test_summary_missing_resolution_not_possible():
    info = MediaInfo(url="u", formats=[FormatInfo("1", "mp4", 720, 30, "avc1", "mp4a", 1)])
    s = q.summarize_selection(info, q.build_spec("240", "mp4", 0), True)
    assert not s.possible and s.selected_resolution is None


def test_summary_without_ffmpeg_uses_muxed_stream():
    info = MediaInfo(url="u", formats=fmts())
    s = q.summarize_selection(info, q.build_spec("1080", "mp4", 0), False)
    assert s.selected_resolution == 720 and not s.needs_ffmpeg
    assert any("FFmpeg missing" in n for n in s.notes)


def test_summary_audio_mp3_without_ffmpeg_keeps_original():
    info = MediaInfo(url="u", formats=fmts())
    s = q.summarize_selection(info, q.build_spec("audio", "mp4", 192), False)
    assert s.container == "m4a"
