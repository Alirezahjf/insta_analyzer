"""تأمین کدهای امنیتی: چالش (ایمیل/پیامک)، کد ۲FA و TOTP.

نکته‌ی حیاتی از سورس instagrapi (mixins/challenge.py::challenge_code_or_raised):
    هندلر باید *مسدودکننده* باشد. اگر در اولین فراخوانی مقدار خالی برگرداند،
    کتابخانه بلافاصله ChallengeRequired پرتاب می‌کند و چالش از بین می‌رود.
    بنابراین هندلر فایلی درون خودش تا رسیدن کد صبر (polling) می‌کند.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import sys
import time
from pathlib import Path
from typing import Callable, Optional

from instagrapi.mixins.challenge import ChallengeChoice

logger = logging.getLogger("igpro.handlers")


# --------------------------------------------------------------------------- TOTP
def totp_code(seed_b32: str, digits: int = 6, period: int = 30) -> str:
    """تولید کد TOTP مطابق RFC 6238 (بدون وابستگی خارجی).

    همان الگوریتم کلاس TOTP در instagrapi.mixins.totp است، اینجا مستقل پیاده شده
    تا به ماژول داخلی کتابخانه وابسته نباشیم.
    """
    seed = (seed_b32 or "").strip().replace(" ", "").upper()
    if not seed:
        raise ValueError("IG_TOTP_SEED خالی است")
    padding = "=" * (-len(seed) % 8)
    key = base64.b32decode(seed + padding, casefold=True)
    counter = int(time.time()) // period
    msg = counter.to_bytes(8, "big")
    digest = hmac.new(key, msg, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = (
        (digest[offset] & 0x7F) << 24
        | (digest[offset + 1] & 0xFF) << 16
        | (digest[offset + 2] & 0xFF) << 8
        | (digest[offset + 3] & 0xFF)
    )
    return str(code % (10**digits)).zfill(digits)


# ------------------------------------------------------------------ کدِ چالش/۲FA
class CodeProvider:
    """ترکیب چند منبع کد: فایل (برای اجرای غیرتعاملی/سرور) و سپس ورودی دستی."""

    def __init__(self, code_file: Path, wait_seconds: int = 300, poll_interval: float = 3.0):
        self.code_file = Path(code_file)
        self.wait_seconds = wait_seconds
        self.poll_interval = poll_interval

    def _read_file(self) -> Optional[str]:
        if not self.code_file.exists():
            return None
        raw = self.code_file.read_text(encoding="utf-8").strip()
        if not raw:
            return None
        code = "".join(ch for ch in raw if ch.isdigit())
        if len(code) < 6:
            return None
        self.code_file.unlink(missing_ok=True)  # مصرف شد
        return code[:6]  # کدهای اینستاگرام ۶ رقمی‌اند؛ رقمِ اضافه = ورودیِ اشتباه

    def _wait_for_file(self, choice: ChallengeChoice) -> Optional[str]:
        if self.wait_seconds <= 0:
            return None
        deadline = time.time() + self.wait_seconds
        hint = "ایمیل" if choice == ChallengeChoice.EMAIL else "پیامک"
        logger.warning(
            "کد تایید (%s) را در فایل بنویسید: %s  — حداکثر %d ثانیه صبر می‌کنم",
            hint, self.code_file, self.wait_seconds,
        )
        while time.time() < deadline:
            code = self._read_file()
            if code:
                logger.info("کد از فایل خوانده شد.")
                return code
            time.sleep(self.poll_interval)
        return None

    def _ask_interactive(self, choice: ChallengeChoice) -> Optional[str]:
        if not sys.stdin or not sys.stdin.isatty():
            return None
        hint = "ایمیل" if choice == ChallengeChoice.EMAIL else "پیامک"
        try:
            return input(f"کد ۶ رقمی ارسال‌شده به {hint} را وارد کنید: ").strip()
        except (EOFError, KeyboardInterrupt):
            return None

    def __call__(self, username: str, choice: ChallengeChoice) -> str:
        """هندلرِ قابل‌اتصال به client.challenge_code_handler (مسدودکننده)."""
        code = self._read_file() or self._wait_for_file(choice) or self._ask_interactive(choice)
        if not code:
            logger.error(
                "کدی دریافت نشد. یا فایل %s را پر کنید، یا برنامه را به‌صورت تعاملی اجرا کنید.",
                self.code_file,
            )
            return ""  # آگاهانه: باعث می‌شود instagrapi چالش را با پیام روشن رها کند
        return code


def build_change_password_handler() -> Callable[[str], str]:
    """اگر اینستاگرام در چالش، تغییر رمز خواست (PASSWORD_RESET)."""
    def handler(username: str) -> str:
        if sys.stdin and sys.stdin.isatty():
            return input(f"رمز عبور جدید برای {username}: ").strip()
        logger.error("تغییر رمز لازم است اما ورودی تعاملی در دسترس نیست.")
        return ""
    return handler
