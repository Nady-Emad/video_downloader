import pytest

from core import platform_detector as pd
from utils import file_manager as fm
from utils import logger, network, validators


def test_platform_detection():
    cases = {
        "https://www.youtube.com/watch?v=abc": pd.YOUTUBE, "https://youtu.be/abc": pd.YOUTUBE,
        "https://m.facebook.com/x": pd.FACEBOOK, "https://fb.watch/x": pd.FACEBOOK,
        "https://www.tiktok.com/@a/video/1": pd.TIKTOK, "https://www.instagram.com/reel/x": pd.INSTAGRAM,
        "https://x.com/a/status/1": pd.TWITTER, "https://twitter.com/a/status/1": pd.TWITTER,
        "https://soundcloud.com/a/b": pd.SOUNDCLOUD, "https://vimeo.com/123": pd.VIMEO,
        "https://example.com/v": pd.OTHER, "https://notyoutube.com/x": pd.OTHER,
        "https://evil.com/youtube.com": pd.OTHER,
    }
    for url, expected in cases.items():
        assert pd.detect_platform(url) == expected, url


def test_playlist_url_detection():
    assert pd.is_playlist_url("https://www.youtube.com/playlist?list=PL123")
    assert pd.is_playlist_url("https://www.youtube.com/watch?v=abc&list=PL123")      # whole playlist wanted
    assert pd.playlist_target("https://youtu.be/abc?list=PL123&si=x") == "https://www.youtube.com/playlist?list=PL123"
    assert not pd.is_playlist_url("https://www.youtube.com/watch?v=abc&list=RDabc")   # endless auto-mix
    assert not pd.is_playlist_url("https://www.youtube.com/watch?v=abc")
    assert pd.youtube_video_id("https://youtu.be/N3jJCYaGR-Y?si=x") == "N3jJCYaGR-Y"
    assert pd.youtube_video_id("https://www.youtube.com/shorts/abc123") == "abc123"
    assert pd.has_playlist_param_with_video("https://www.youtube.com/watch?v=abc&list=PL123")
    assert not pd.is_playlist_url("https://vimeo.com/123")


def test_validate_url():
    assert validators.validate_url(" https://youtu.be/abc ") == "https://youtu.be/abc"
    for bad in ["", "ftp://x.com/a", "javascript:alert(1)", "https://localhost/x", "not a url", "https://a b.com"]:
        with pytest.raises(ValueError):
            validators.validate_url(bad)


def test_extract_urls_dedup():
    text = "see https://a.com/x, and https://b.com/y). also https://a.com/x"
    assert validators.extract_urls(text) == ["https://a.com/x", "https://b.com/y"]


def test_filename_template_validation():
    validators.validate_filename_template("{index} - {title}")
    for bad in ["{unknown}", "", "{title", "plain"]:
        with pytest.raises(ValueError):
            validators.validate_filename_template(bad)


def test_sanitize_filename():
    assert fm.sanitize_filename('a<b>:c"/d\\e|f?g*') == "a_b__c__d_e_f_g_"
    assert fm.sanitize_filename("CON") == "_CON"
    assert fm.sanitize_filename("nul.txt") == "_nul.txt"
    assert fm.sanitize_filename("../../etc/passwd") == "._._etc_passwd" or "/" not in fm.sanitize_filename("../../etc/passwd")
    assert ".." not in fm.sanitize_filename("../../etc/passwd")
    assert fm.sanitize_filename("  ...  ") == "untitled"
    assert fm.sanitize_filename("") == "untitled"
    assert fm.sanitize_filename("trailing. ") == "trailing"
    long = fm.sanitize_filename("é" * 500)
    assert len(long.encode()) <= 200
    assert fm.sanitize_filename("日本語のタイトル 🎬") == "日本語のタイトル 🎬"


def test_render_filename():
    assert fm.render_filename("{index} - {title}", "Intro", index=1, total=4) == "01 - Intro"
    assert fm.render_filename("{index} - {title}", "Intro", index=7, total=120) == "007 - Intro"
    assert fm.render_filename("{title}", "A/B") == "A_B"


def test_unique_path_and_find_existing(tmp_path):
    f = tmp_path / "a.mp4"
    f.write_bytes(b"1")
    assert fm.unique_path(f).name == "a (1).mp4"
    (tmp_path / "a (1).mp4").write_bytes(b"1")
    assert fm.unique_path(f).name == "a (2).mp4"
    assert fm.find_existing(tmp_path, "a") == f
    assert fm.find_existing(tmp_path, "zzz") is None
    (tmp_path / "b.mp4.part").write_bytes(b"1")
    assert fm.find_existing(tmp_path, "b") is None


def test_finalize_rename_skip_overwrite(tmp_path):
    dest = tmp_path / "out"
    work = fm.make_attempt_dir(dest)
    src = work / "media.mp4"
    src.write_bytes(b"new")
    final, skipped = fm.finalize_file(src, dest, "Video", "rename")
    assert final.name == "Video.mp4" and not skipped and not src.exists()

    src2 = work / "media.mp4"
    src2.write_bytes(b"second")
    final2, _ = fm.finalize_file(src2, dest, "Video", "rename")
    assert final2.name == "Video (1).mp4" and final.read_bytes() == b"new"

    src3 = work / "media.mp4"
    src3.write_bytes(b"third")
    kept, skipped3 = fm.finalize_file(src3, dest, "Video", "skip")
    assert skipped3 and kept == final and final.read_bytes() == b"new" and src3.exists()

    fm.finalize_file(src3, dest, "Video", "overwrite")
    assert final.read_bytes() == b"third"


def test_attempt_dir_cleanup_is_safe(tmp_path):
    dest = tmp_path / "d"
    keep = dest / "unrelated.mp4"
    dest.mkdir()
    keep.write_bytes(b"keep")
    work = fm.make_attempt_dir(dest)
    (work / "media.mp4.part").write_bytes(b"x")
    assert fm.cleanup_attempt_dir(work)
    assert not work.exists() and keep.exists()
    # refuses arbitrary folders
    other = dest / "notmine"
    other.mkdir()
    assert not fm.cleanup_attempt_dir(other) and other.exists()


def test_stale_cleanup_only_dead_owner(tmp_path):
    dest = tmp_path / "d"
    mine = fm.make_attempt_dir(dest)
    stale = fm.make_attempt_dir(dest)
    (stale / fm.LOCK_NAME).write_text("999999999")   # pid that cannot exist
    removed = fm.cleanup_stale_attempts(dest, max_age_hours=0.0)
    assert removed == 1 and mine.exists() and not stale.exists()


def test_disk_space_check(tmp_path):
    with pytest.raises(fm.FileError):
        fm.check_disk_space(tmp_path, required=10 ** 18)


def test_ensure_dir_permission(tmp_path):
    import os
    if os.geteuid() == 0:
        pytest.skip("root ignores permissions")
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    with pytest.raises(fm.FileError):
        fm.ensure_dir(locked / "sub")
    locked.chmod(0o700)


def test_classify_errors():
    assert network.classify_error(Exception("HTTP Error 404")).kind == network.DELETED
    assert network.classify_error(Exception("Sign in to confirm you're not a bot")).kind == network.AUTH
    assert network.classify_error(Exception("Video unavailable")).kind == network.DELETED
    assert network.classify_error(Exception("<urlopen error timed out>")).kind == network.TIMEOUT
    assert network.classify_error(Exception("Unsupported URL: x")).kind == network.UNSUPPORTED
    assert network.classify_error(PermissionError()).kind == network.PERMISSION
    assert network.classify_error(Exception("[Errno -2] Name or service not known")).kind == network.DNS
    assert network.classify_error(Exception("zzz")).kind == network.UNKNOWN
    assert network.classify_error(Exception("timeout")).transient
    assert not network.classify_error(Exception("Private video")).transient


def test_redaction():
    text = "Cookie: SID=abc123; token=xyz authorization: Bearer q password=hunter2"
    out = logger.redact(text)
    for secret in ("abc123", "xyz", "hunter2"):
        assert secret not in out
    line = ".example.com\tTRUE\t/\tTRUE\t1999999999\tsession\tSECRETVALUE"
    assert "SECRETVALUE" not in logger.redact(line)
