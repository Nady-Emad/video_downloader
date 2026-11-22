import threading

import pytest

from core.cookie_manager import CookieConfig
from core.fallback_manager import DownloadFailed, FallbackManager
from core.platform_detector import FACEBOOK, YOUTUBE
from core.quality_manager import build_spec
from engines.base_engine import CancelToken, DownloadCancelled, EngineError
from engines.gallery_dl_engine import GalleryDlEngine
from engines.pytubefix_engine import PytubefixEngine
from engines.youget_engine import YouGetEngine
from tests.helpers import FakeEngine, FakeFfmpeg, fail_with

SPEC = build_spec("720", "mp4", 192)
COOKIES = CookieConfig()


def run(manager, tmp_path, platform=YOUTUBE, title_hint="", cancel=None, name_for=None):
    logs, engines_seen = [], []
    result = manager.run(
        "https://example.com/v", platform, SPEC, COOKIES, tmp_path, name_for or (lambda t: t),
        lambda p: None, lambda lvl, msg: logs.append((lvl, msg)), cancel or CancelToken(),
        engines_seen.append, title_hint=title_hint,
    )
    return result, logs, engines_seen


def mgr(engines, **kw):
    kw.setdefault("retry_delay", 0)
    return FallbackManager(engines, FakeFfmpeg(), **kw)


def test_primary_success_no_fallback(tmp_path):
    a, b = FakeEngine("yt-dlp"), FakeEngine("pytubefix")
    result, logs, seen = run(mgr([a, b]), tmp_path)
    assert result.file_path.name == "Fake Title.mp4" and result.file_path.parent == tmp_path
    assert seen == ["yt-dlp"] and not b.calls
    assert ("INFO", "Attempting primary engine: yt-dlp.") in logs
    assert ("SUCCESS", "Download completed: Fake Title.mp4") in logs
    assert not (tmp_path / ".vdl_tmp").exists()          # temp folder removed


def test_fallback_order_and_logging(tmp_path):
    a = FakeEngine("yt-dlp", fail_with("Connection timeout"))
    b = FakeEngine("pytubefix")
    c = FakeEngine("gallery-dl")
    result, logs, seen = run(mgr([a, b, c], max_retries=0), tmp_path)
    assert result.engine == "pytubefix" and seen == ["yt-dlp", "pytubefix"] and not c.calls
    texts = [m for _, m in logs]
    assert any("yt-dlp failed" in t for t in texts)
    assert "Switching to pytubefix." in texts
    assert texts.index("Switching to pytubefix.") > [i for i, t in enumerate(texts) if "yt-dlp failed" in t][0]
    # partial output from the failed engine must be gone, completed file preserved
    assert sorted(p.name for p in tmp_path.iterdir()) == ["Fake Title.mp4"]


def test_incompatible_fallbacks_are_rejected_and_reported(tmp_path):
    spec = build_spec("720", "mp4", 192)
    for engine, platform in ((PytubefixEngine(), FACEBOOK),):
        ok, reason = engine.supports(platform, spec, COOKIES)
        assert not ok and "YouTube" in reason
    assert not GalleryDlEngine().supports(YOUTUBE, spec, COOKIES)[0]
    assert not YouGetEngine().supports("Other", spec, COOKIES)[0]
    assert not YouGetEngine().supports(YOUTUBE, spec, COOKIES)[0]          # max resolution cannot be enforced
    assert not GalleryDlEngine().supports("TikTok", build_spec("audio", "mp4", 0), COOKIES)[0]

    ytdlp = FakeEngine("yt-dlp", fail_with("boom"))
    only_yt = FakeEngine("pytubefix", platforms={YOUTUBE})
    manager = mgr([ytdlp, only_yt], max_retries=0)
    with pytest.raises(DownloadFailed):
        run(manager, tmp_path, platform=FACEBOOK)
    assert not only_yt.calls                        # never attempted
    logs = []
    try:
        manager.run("https://x.com/a", FACEBOOK, SPEC, COOKIES, tmp_path, lambda t: t, lambda p: None,
                    lambda l, m: logs.append(m), CancelToken())
    except DownloadFailed:
        pass
    assert any(m.startswith("Skipping pytubefix") for m in logs)


def test_all_engines_fail_reports_reason_and_cleans(tmp_path):
    manager = mgr([FakeEngine("yt-dlp", fail_with("Unsupported URL: x")),
                   FakeEngine("gallery-dl", fail_with("boom"))], max_retries=0)
    with pytest.raises(DownloadFailed) as err:
        run(manager, tmp_path)
    assert len(err.value.failures) == 2
    assert list(tmp_path.iterdir()) == []


def test_permanent_error_stops_chain(tmp_path):
    a = FakeEngine("yt-dlp", fail_with("Private video"))
    b = FakeEngine("pytubefix")
    with pytest.raises(DownloadFailed) as err:
        run(mgr([a, b]), tmp_path)
    assert err.value.info.kind == "private" and not b.calls
    assert len(a.calls) == 1                         # not retried


def test_transient_error_retried_then_succeeds(tmp_path):
    def flaky(request, attempt, cancel):
        if attempt < 3:
            (request.work_dir / "media.mp4.part").write_bytes(b"p")
            raise EngineError("Connection timed out")
    a = FakeEngine("yt-dlp", flaky)
    result, logs, _ = run(mgr([a], max_retries=3), tmp_path)
    assert len(a.calls) == 3 and result.engine == "yt-dlp"
    assert len({str(d) for d in a.work_dirs}) == 3   # each attempt used its own temp folder
    assert sum("Retrying" in m for _, m in logs) == 2


def test_retry_limit_respected(tmp_path):
    a = FakeEngine("yt-dlp", fail_with("Connection reset by peer"))
    with pytest.raises(DownloadFailed):
        run(mgr([a], max_retries=2), tmp_path)
    assert len(a.calls) == 3


def test_empty_output_is_rejected(tmp_path):
    def empty(request, attempt, cancel):
        (request.work_dir / "media.mp4").write_bytes(b"")
    a = FakeEngine("yt-dlp", empty)
    b = FakeEngine("pytubefix")
    result, _, _ = run(mgr([a, b], max_retries=0), tmp_path)
    assert result.engine == "pytubefix"


def test_existing_file_not_overwritten_rename_policy(tmp_path):
    (tmp_path / "Fake Title.mp4").write_bytes(b"original")
    result, _, _ = run(mgr([FakeEngine("yt-dlp")]), tmp_path)
    assert result.file_path.name == "Fake Title (1).mp4"
    assert (tmp_path / "Fake Title.mp4").read_bytes() == b"original"


def test_skip_policy_skips_before_download(tmp_path):
    (tmp_path / "Fake Title.mp4").write_bytes(b"original")
    a = FakeEngine("yt-dlp")
    result, logs, _ = run(mgr([a], conflict_policy="skip"), tmp_path, title_hint="Fake Title")
    assert result.skipped_existing and not a.calls
    assert any("already exists" in m for _, m in logs)


def test_cancellation_cleans_up_and_preserves_completed(tmp_path):
    (tmp_path / "done.mp4").write_bytes(b"completed earlier")
    token = CancelToken()

    def cancelling(request, attempt, cancel):
        (request.work_dir / "media.mp4.part").write_bytes(b"p")
        token.cancel()
        cancel.raise_if_cancelled()
    with pytest.raises(DownloadCancelled):
        run(mgr([FakeEngine("yt-dlp", cancelling), FakeEngine("pytubefix")]), tmp_path, cancel=token)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["done.mp4"]


def test_no_engine_installed(tmp_path):
    with pytest.raises(DownloadFailed) as err:
        run(mgr([FakeEngine("yt-dlp", installed=False)]), tmp_path)
    assert err.value.info.kind == "missing_dependency"


def test_duplicate_concurrent_downloads_blocked(tmp_path):
    started, release = threading.Event(), threading.Event()

    def slow(request, attempt, cancel):
        started.set()
        release.wait(5)
    a = FakeEngine("yt-dlp", slow)
    results = {}

    def first():
        results["first"], _, _ = run(mgr([a]), tmp_path, title_hint="Same")

    t = threading.Thread(target=first)
    t.start()
    assert started.wait(5)
    with pytest.raises(DownloadFailed) as err:
        run(mgr([FakeEngine("yt-dlp")]), tmp_path, title_hint="Same")
    assert err.value.info.kind == "duplicate"
    release.set()
    t.join()
    assert results["first"].file_path.exists()
