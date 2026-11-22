"""Checks that need no Qt: translation coverage, settings the wizard writes, and GUI/manager signal wiring."""
import ast
import re
from pathlib import Path

from config.settings import DEFAULTS, Settings
from gui import i18n

ROOT = Path(__file__).resolve().parent.parent


def _tr_literals():
    found = set()
    for folder in ("gui", "core"):
        for path in (ROOT / folder).glob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if (isinstance(node, ast.Call) and getattr(node.func, "id", "") == "tr" and node.args
                        and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
                    found.add(node.args[0].value)
    return found


def test_every_translatable_string_has_arabic():
    missing = sorted(t for t in _tr_literals() if t not in i18n.AR)
    assert not missing, missing


def test_translation_switching_and_rtl():
    try:
        i18n.set_language("ar")
        assert i18n.is_rtl() and i18n.tr("Settings") == "الإعدادات" and i18n.tr("Unknown text") == "Unknown text"
        i18n.set_language("en")
        assert not i18n.is_rtl() and i18n.tr("Settings") == "Settings"
        i18n.set_language("xx")
        assert i18n.language() == "en"
    finally:
        i18n.set_language("en")


def test_wizard_style_values_pass_validation(tmp_path):
    s = Settings(tmp_path / "s.json")
    values = {"download_dir": str(tmp_path / "dl"), "language": "ar", "theme": "light", "quality_preset": "compatible",
              "default_video_quality": "1080", "compat_mode": True, "first_run_done": True}
    assert s.update(values) == []
    assert Settings(tmp_path / "s.json").get("theme") == "light"
    assert s.update({"theme": "purple"})          # invalid values are rejected, nothing applied
    assert s.get("theme") == "light"


def test_new_setting_defaults_exist():
    for key in ("language", "theme", "clipboard_monitor", "tray_enabled", "close_to_tray", "minimize_to_tray",
                "notifications", "check_updates", "update_url", "first_run_done", "speed_limit_kbps",
                "schedule_enabled", "quality_preset"):
        assert key in DEFAULTS


def test_gui_only_uses_manager_members_that_exist():
    manager = (ROOT / "core" / "download_manager.py").read_text(encoding="utf-8")
    for path in (ROOT / "gui").glob("*.py"):
        for name in set(re.findall(r"self\.manager\.(\w+)", path.read_text(encoding="utf-8"))):
            if name in ("queue", "history", "settings", "ffmpeg", "errors", "paused", "active_count"):
                continue
            assert re.search(rf"(def {name}\b|\b{name}\s*=|\b{name}\s*:)", manager), f"{path.name}: manager.{name}"


def test_main_window_pages_match_sidebar():
    src = (ROOT / "gui" / "main_window.py").read_text(encoding="utf-8")
    labels = re.search(r"PAGES = \[(.*?)\]", src, re.S).group(1)
    assert len(re.findall(r'"[^"]+"', labels)) == 7
    assert "range(7)" in src
