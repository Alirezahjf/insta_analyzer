"""تنظیمات برنامه: خواندن از متغیرهای محیطی و فایل .env (بدون نیاز به python-dotenv)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

APP_DIR = Path(__file__).resolve().parent.parent  # پوشه‌ی پروژه (igpro)
DEFAULT_SESSION = APP_DIR / "state" / "session.json"
DEFAULT_STATE_DIR = APP_DIR / "state"


def load_dotenv(path: Path = APP_DIR / ".env", override: bool = False) -> None:
    """خواندن فایل ساده‌ی KEY=VALUE؛ مقادیر موجود در محیط را (در حالت پیش‌فرض) بازنویسی نمی‌کند."""
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and (override or key not in os.environ):
            os.environ[key] = value


def env_str(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def env_int(name: str, default: int) -> int:
    try:
        return int(env_str(name, str(default)) or default)
    except ValueError:
        return default


def mask(secret: str, keep: int = 6) -> str:
    """نمایش امنِ رشته‌های حساس برای لاگ."""
    if not secret:
        return "<empty>"
    if len(secret) <= keep * 2:
        return "*" * len(secret)
    return f"{secret[:keep]}...{secret[-keep:]} (طول={len(secret)})"


@dataclass(frozen=True)
class Settings:
    session_path: Path
    state_dir: Path
    code_file: Path
    log_dir: Path

    proxy: Optional[str]
    session_id: str
    username: str
    password: str
    totp_seed: str
    verification_code: str
    phone_number: str

    request_timeout: float
    delay_min: float
    delay_max: float
    private_transport: str  # curl (HTTP/2) | requests (HTTP/1.1 — سازگار با VPN/پروکسی)
    transport_fallback: bool
    trust_env_proxy: bool   # استفاده از متغیرهای محیطیِ پروکسی ویندوز؟ (برای VPN خیر)
    watchdog_seconds: int   # اگر برنامه بیش از این مدت گیر کند، ردِ پشته را چاپ کند (۰ = غیرفعال)
    code_wait_seconds: int
    challenge_choice: str  # email | sms
    locale: str
    country: str
    country_code: int
    timezone_offset: int
    log_level: str

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        state_dir = Path(env_str("IG_STATE_DIR", str(DEFAULT_STATE_DIR))).expanduser()
        state_dir.mkdir(parents=True, exist_ok=True)
        # پیش‌فرضِ فایل سشن از state_dir ساخته می‌شود تا IG_STATE_DIR واقعاً همه‌چیز را جابه‌جا کند
        default_session = state_dir / "session.json"
        return cls(
            session_path=Path(env_str("IG_SESSION_FILE", str(default_session))).expanduser(),
            state_dir=state_dir,
            code_file=state_dir / "code.txt",
            log_dir=state_dir / "logs",
            proxy=env_str("IG_PROXY") or None,
            session_id=env_str("IG_SESSION_ID"),
            username=env_str("IG_USERNAME"),
            password=env_str("IG_PASSWORD"),
            totp_seed=env_str("IG_TOTP_SEED").replace(" ", "").upper(),
            verification_code=env_str("IG_2FA_CODE"),
            phone_number=env_str("IG_PHONE_NUMBER"),
            request_timeout=float(env_int("IG_REQUEST_TIMEOUT", 20)),
            delay_min=float(env_int("IG_DELAY_MIN", 1)),
            delay_max=float(env_int("IG_DELAY_MAX", 3)),
            # requests = HTTP/1.1 سازگار با VPN/پروکسی؛ curl = HTTP/2 (اثرانگشت بهتر ولی حساس‌تر)
            private_transport=env_str("IG_PRIVATE_TRANSPORT", "requests").lower() or "requests",
            transport_fallback=env_str("IG_TRANSPORT_FALLBACK", "1") not in ("0", "false", "no"),
            # اتصال مستقیم/VPN: متغیرهای محیطیِ پروکسی (HTTP_PROXY و ...) نادیده گرفته شوند
            trust_env_proxy=env_str("IG_TRUST_ENV_PROXY", "0") not in ("0", "false", "no"),
            watchdog_seconds=env_int("IG_WATCHDOG_SECONDS", 300),
            code_wait_seconds=env_int("IG_CODE_WAIT_SECONDS", 300),
            challenge_choice=env_str("IG_CHALLENGE_CHOICE", "email").lower(),
            locale=env_str("IG_LOCALE", "en_US"),
            country=env_str("IG_COUNTRY", "US"),
            country_code=env_int("IG_COUNTRY_CODE", 1),
            timezone_offset=env_int("IG_TIMEZONE_OFFSET", -14400),
            log_level=env_str("IG_LOG_LEVEL", "INFO").upper(),
        )
