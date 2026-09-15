"""پیکربندی لاگ: خروجی کنسول + فایل چرخشی، با تارِ خودکارِ اطلاعات حساس."""
from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

# sessionid معمولاً با یک user-id شروع می‌شود؛ در لاگ‌ها تار می‌شود
_SECRET_PATTERNS = [
    re.compile(r"\b\d{5,}%3A[A-Za-z0-9%_\-]{10,}%3A\d+%3A[A-Za-z0-9_\-]{10,}"),  # sessionid
    re.compile(r"\b[A-Z0-9]{32,}\b"),  # توکن‌های بلند
]


class SecretRedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            return True
        redacted = message
        for pattern in _SECRET_PATTERNS:
            redacted = pattern.sub("***REDACTED***", redacted)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


def setup_logging(level: str = "INFO", log_dir: Path | None = None, name: str = "igpro") -> logging.Logger:
    log_dir = log_dir or (Path.cwd() / "logs")
    log_dir.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.propagate = False
    for handler in list(logger.handlers):
        logger.removeHandler(handler)

    fmt = logging.Formatter("%(asctime)s | %(levelname)-8s | %(name)-12s | %(message)s", "%Y-%m-%d %H:%M:%S")
    redactor = SecretRedactingFilter()

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    console.addFilter(redactor)
    logger.addHandler(console)

    file_handler = RotatingFileHandler(log_dir / "igpro.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    file_handler.setFormatter(fmt)
    file_handler.addFilter(redactor)
    logger.addHandler(file_handler)

    # کتابخانه‌ی instagrapi بسیار پُرلاگ است؛ فقط خطاهایش را نمایش می‌دهیم
    logging.getLogger("instagrapi").setLevel(logging.WARNING)
    return logger
