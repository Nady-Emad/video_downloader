"""Logging setup: rotating file log, custom SUCCESS level and secret redaction."""
from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Callable, Optional

SUCCESS_LEVEL = 25
logging.addLevelName(SUCCESS_LEVEL, "SUCCESS")

LOGGER_NAME = "vdl"

LEVEL_MAP = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
}

# key=value / key: value pairs that must never reach a log file.
_SECRET_PATTERN = re.compile(
    r"(?i)\b(cookie|set-cookie|authorization|proxy-authorization|x-auth[\w-]*|access[_-]?token|"
    r"refresh[_-]?token|token|password|passwd|sessionid|sid|api[_-]?key|secret|signature)"
    r"(\s*[:=]\s*)([^\s&;,\"']+)"
)
_NETSCAPE_LINE = re.compile(r"(?m)^[^\s#][^\t\n]*\t(?:TRUE|FALSE)\t[^\t\n]*\t(?:TRUE|FALSE)\t\d+\t[^\t\n]+\t[^\t\n]*$")


def redact(text: str) -> str:
    """Mask credentials and cookie lines in free text."""
    text = _NETSCAPE_LINE.sub("[cookie line removed]", text)
    return _SECRET_PATTERN.sub(lambda m: f"{m.group(1)}{m.group(2)}[REDACTED]", text)


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # malformed format args; never break logging
            return True
        record.msg = redact(message)
        record.args = ()
        return True


def get_logger(name: Optional[str] = None) -> logging.Logger:
    return logging.getLogger(f"{LOGGER_NAME}.{name}" if name else LOGGER_NAME)


def setup_logging(log_file: Optional[Path] = None, level: str = "info") -> logging.Logger:
    """Configure the application logger. Safe to call more than once."""
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(LEVEL_MAP.get(level.lower(), logging.INFO))
    logger.propagate = False
    if getattr(logger, "_vdl_configured", False):
        return logger
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    if log_file is not None:
        try:
            Path(log_file).parent.mkdir(parents=True, exist_ok=True)
            handler = RotatingFileHandler(
                log_file, maxBytes=2_000_000, backupCount=5, encoding="utf-8"
            )
            handler.setFormatter(fmt)
            handler.addFilter(RedactingFilter())
            logger.addHandler(handler)
        except OSError:
            pass  # logging problems must never stop the application
    stream = logging.StreamHandler()
    stream.setFormatter(fmt)
    stream.addFilter(RedactingFilter())
    logger.addHandler(stream)
    logger._vdl_configured = True  # type: ignore[attr-defined]
    return logger


def set_level(level: str) -> None:
    logging.getLogger(LOGGER_NAME).setLevel(LEVEL_MAP.get(level.lower(), logging.INFO))


def level_enabled(level_name: str) -> bool:
    """True if a message with this console level name should be shown."""
    numeric = {"DEBUG": logging.DEBUG, "INFO": logging.INFO, "SUCCESS": SUCCESS_LEVEL,
               "WARNING": logging.WARNING, "ERROR": logging.ERROR}.get(level_name.upper(), logging.INFO)
    return logging.getLogger(LOGGER_NAME).isEnabledFor(numeric)


LogCallback = Callable[[str, str], None]  # (level_name, message)


def make_log_callback(component: str, forward: Optional[LogCallback] = None) -> LogCallback:
    """Return a callback that writes to the file log (redacted) and forwards to the GUI."""
    logger = get_logger(component)
    numeric = {"DEBUG": logging.DEBUG, "INFO": logging.INFO, "SUCCESS": SUCCESS_LEVEL,
               "WARNING": logging.WARNING, "ERROR": logging.ERROR}

    def _callback(level: str, message: str) -> None:
        safe = redact(message)
        logger.log(numeric.get(level.upper(), logging.INFO), safe)
        if forward is not None:
            forward(level.upper(), safe)

    return _callback
