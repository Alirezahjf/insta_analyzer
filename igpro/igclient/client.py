"""هسته‌ی احراز هویت: ساخت کلاینت، زنجیره‌ی لاگین، مدیریت چالش/۲FA و بازتلاش.

همه‌ی فراخوانی‌ها بر اساس سورس واقعی instagrapi 3.0.2 نوشته شده‌اند.
"""
from __future__ import annotations

import logging
import random
import time
from enum import Enum
from typing import Any, Callable, Optional, TypeVar

from instagrapi import Client
from instagrapi.exceptions import (
    BadPassword,
    ChallengeRequired,
    ClientConnectionError,
    ClientError,
    ClientLoginRequired,
    ClientRequestTimeout,
    LoginRequired,
    PleaseWaitFewMinutes,
    PrivateError,
    RateLimitError,
    ReloginAttemptExceeded,
    TwoFactorRequired,
    UserNotFound,
)

from .config import Settings, mask
from .handlers import CodeProvider, build_change_password_handler, totp_code
from .session_store import SessionStore

logger = logging.getLogger("igpro.client")
T = TypeVar("T")

# خطاهایی که یعنی سشن واقعاً مرده است → حذف فایل و تلاش از روش بعدی
AUTH_ERRORS = (LoginRequired, ClientLoginRequired, ChallengeRequired, BadPassword)
# خطاهای گذرا → فایل سشن را دست‌نخورده نگه می‌داریم و فقط صبر/تلاشِ مجدد می‌کنیم
TRANSIENT_ERRORS = (
    ClientConnectionError,
    ClientRequestTimeout,
    RateLimitError,
    PleaseWaitFewMinutes,
)


class LoginMethod(str, Enum):
    SESSION = "session"        # فایل سشنِ ذخیره‌شده (ارزان‌ترین و امن‌ترین)
    SESSIONID = "sessionid"    # sessionid خام از مرورگر
    PASSWORD = "password"      # یوزر/پسورد (+ کد ۲FA/TOTP/چالش)


class AuthenticationError(RuntimeError):
    """هیچ روشی برای ورود موفق نبود."""


class IGClient:
    """لایه‌ی نازک و ایمن روی instagrapi.Client."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.store = SessionStore(settings.session_path, logger=logger)
        self.client: Client = self._build_client()
        self.login_method: Optional[LoginMethod] = None

    # ------------------------------------------------------------------ ساخت
    def _build_client(self) -> Client:
        s = self.settings
        cl = Client(
            proxy=s.proxy,
            delay_range=[s.delay_min, s.delay_max],
            request_timeout=s.request_timeout,
            # override_app_version پروفایل اپلیکیشنِ پشتیبانی‌شده را اعمال می‌کند؛
            # بدون آن ممکن است لاگین CAA با خطای bloks_versioning_id شکست بخورد
            override_app_version=True,
            # curl (=پیش‌فرض) از HTTP/2 prior-knowledge استفاده می‌کند و پشت اکثر
            # پروکسی‌ها/وی‌پی‌ان‌ها با ProxyError شکست می‌خورد؛ requests روی HTTP/1.1 است.
            private_transport=s.private_transport if s.private_transport in ("curl", "requests") else "curl",
            # تلاشِ مجددِ کمتر ⇒ خطا زودتر دیده می‌شود (اتصال‌های بد را سریع رها می‌کنیم)
            session_retry_total=2,
            session_retry_backoff_factor=1,
        )
        logger.info("ترنسپورت درخواست‌ها: %s", cl.private_transport)
        self._enforce_timeouts(cl, s.request_timeout)

        # اتصال مستقیم (VPN روی کل سیستم): متغیرهای محیطیِ پروکسی را نادیده بگیر
        # تا مبادا یک HTTP_PROXY باقی‌مانده در ویندوز باعث ProxyError شود.
        if not s.trust_env_proxy:
            for name in ("private", "public", "graphql"):
                session = getattr(cl, name, None)
                if session is not None and hasattr(session, "trust_env"):
                    session.trust_env = False
            logger.info("پروکسی: غیرفعال (اتصال مستقیم / VPN) — IG_PROXY=%s",
                        s.proxy or "ست نشده")
        elif s.proxy:
            logger.info("پروکسی: %s", s.proxy)
        cl.set_locale(s.locale)
        cl.set_country(s.country)
        cl.set_country_code(s.country_code)
        cl.set_timezone_offset(s.timezone_offset)

        # هندلرهای چالش: فایل → تعاملی (هندلر باید مسدودکننده باشد)
        code_provider = CodeProvider(s.code_file, wait_seconds=s.code_wait_seconds)
        cl.challenge_code_handler = code_provider
        cl.change_password_handler = build_change_password_handler()
        if s.phone_number:
            cl.phone_number = s.phone_number  # برای گام submit_phone در چالش
        return cl

    # ------------------------------------------------------------- ابزار احراز
    @property
    def user_id(self) -> Optional[str]:
        return self.client.user_id or None

    @property
    def username(self) -> Optional[str]:
        return getattr(self.client, "username", None) or None

    def _ensure_username(self) -> None:
        """بعد از load_settings مقدار username خالی است؛ اینجا از سرور می‌گیریم."""
        if self.username:
            return
        uid = self.user_id
        if not uid:
            return
        try:
            self.client.username = self.client.username_from_user_id(uid)
            logger.info("نام کاربری از سرور دریافت شد: %s", self.client.username)
        except Exception as exc:  # اطلاعاتِ زاید است؛ شکست آن بی‌اهمیت
            logger.debug("دریافت نام کاربری ناموفق بود: %s", exc)

    def _is_alive(self) -> bool:
        """بررسی سبکِ زنده بودن سشن: یک درخواست به users/{id}/info/"""
        uid = self.user_id
        if not uid:
            return False
        logger.info("در حال بررسی اعتبار سشن ...")
        try:
            self.with_transport_fallback(self.client.user_info, uid)
            logger.info("سشن معتبر است.")
            return True
        except AUTH_ERRORS as exc:
            logger.info("سشن مرده است: %s", type(exc).__name__)
            return False
        except TRANSIENT_ERRORS as exc:
            logger.warning("بررسی سشن با خطای گذرا مواجه شد (%s)؛ فرض می‌کنیم سالم است.", type(exc).__name__)
            return True
        except Exception as exc:
            logger.warning("بررسی سشن ناموفق بود (%s)", type(exc).__name__)
            return False

    # ------------------------------------------------------------- روش‌های ورود
    def _login_with_session(self) -> bool:
        if not self.store.exists():
            logger.info("فایل سشن وجود ندارد؛ می‌رویم سراغ روش بعدی.")
            return False
        data = self.store.apply_to(self.client)
        if not data:
            return False
        logger.info("سشن ذخیره‌شده بارگذاری شد (method=%s, age=%s)",
                    data.get("login_method"), _age_str(data.get("saved_at")))
        if self._is_alive():
            self.login_method = LoginMethod.SESSION
            self._ensure_username()
            return True
        self.store.delete()  # فقط در صورت اطمینان از مرگِ سشن حذف می‌کنیم
        return False

    def _login_with_sessionid(self) -> bool:
        sid = self.settings.session_id
        if not sid:
            logger.info("IG_SESSION_ID تنظیم نشده؛ می‌رویم سراغ روش بعدی.")
            return False
        logger.info("ورود با sessionid %s ...", mask(sid))
        try:
            self.with_transport_fallback(self.client.login_by_sessionid, sid.replace(" ", "").strip())
            self.login_method = LoginMethod.SESSIONID
            return True
        except AssertionError:
            logger.error("قالب sessionid اشتباه است (باید با عدد user-id شروع شود و طولش > ۳۰ باشد).")
        except TRANSIENT_ERRORS as exc:
            logger.error("سرور موقتاً پاسخ نداد: %s", type(exc).__name__)
            raise
        except (ClientError, PrivateError) as exc:
            logger.error("sessionid پذیرفته نشد: %s", type(exc).__name__)
        return False

    def _resolve_2fa_code(self) -> str:
        """کدِ ۲FA: از env، یا TOTP، یا فایل/تعاملی."""
        s = self.settings
        if s.verification_code:
            return s.verification_code.strip()
        if s.totp_seed:
            code = totp_code(s.totp_seed)
            logger.info("کد ۲FA از روی TOTP ساخته شد.")
            return code
        provider = CodeProvider(s.code_file, wait_seconds=s.code_wait_seconds)
        from instagrapi.mixins.challenge import ChallengeChoice
        return provider(self.username or "account", ChallengeChoice.SMS)

    def _login_with_password(self) -> bool:
        s = self.settings
        if not (s.username and s.password):
            logger.info("IG_USERNAME/IG_PASSWORD تنظیم نشده؛ راهی برای ورود نمانده.")
            return False

        verification_code = s.verification_code or (totp_code(s.totp_seed) if s.totp_seed else "")
        logger.info("ورود با نام کاربری/رمز عبور برای %s ...", s.username)
        try:
            # تلاش اول: مسیر مدرن (CAA/Bloks) — مستند در mixins/auth.py::login
            self.with_transport_fallback(self.client.login, s.username, s.password, verification_code=verification_code)
            self.login_method = LoginMethod.PASSWORD
            return True
        except TwoFactorRequired:
            logger.warning("کد دوعاملی لازم است؛ در حال تهیه‌ی کد ...")
            code = self._resolve_2fa_code()
            if not code:
                logger.error("کد دوعاملی در دسترس نیست (IG_2FA_CODE / IG_TOTP_SEED / فایل کد).")
                return False
            return self._retry_password_login(code)
        except ReloginAttemptExceeded:
            logger.error("تعداد تلاش‌های ورود مجدد بیش از حد مجاز است؛ کمی صبر کنید.")
            return False
        except (ClientError, PrivateError) as exc:
            logger.warning("مسیر CAA شکست خورد (%s)؛ تلاش با مسیر legacy ...", type(exc).__name__)
            try:
                self.client.login_legacy(s.username, s.password, verification_code=verification_code)
                self.login_method = LoginMethod.PASSWORD
                return True
            except TwoFactorRequired:
                code = self._resolve_2fa_code()
                if not code:
                    return False
                try:
                    self.client.login_legacy(s.username, s.password, verification_code=code)
                    self.login_method = LoginMethod.PASSWORD
                    return True
                except (ClientError, PrivateError) as inner:
                    logger.error("ورود legacy با ۲FA هم شکست خورد: %s", type(inner).__name__)
                    return False
            except (ClientError, PrivateError) as inner:
                logger.error("ورود legacy شکست خورد: %s", type(inner).__name__)
                return False

    def _retry_password_login(self, code: str) -> bool:
        s = self.settings
        for method, fn in (("CAA", self.client.login), ("legacy", self.client.login_legacy)):
            try:
                fn(s.username, s.password, verification_code=code)
                self.login_method = LoginMethod.PASSWORD
                return True
            except (TwoFactorRequired, ReloginAttemptExceeded) as exc:
                logger.error("کد پذیرفته نشد (%s: %s)", method, type(exc).__name__)
                return False
            except (ClientError, PrivateError) as exc:
                logger.warning("تلاشِ %s با کد شکست خورد (%s)", method, type(exc).__name__)
        return False

    # ------------------------------------------------------- ترنسپورت و شبکه
    @staticmethod
    def _enforce_timeouts(cl: Client, timeout: float) -> None:
        """تزریق timeout به همه‌ی سشن‌ها.

        ⚠️ باگِ واقعی در instagrapi: mixins/private.py هیچ timeoutـی به درخواست‌های
        خصوصی نمی‌دهد (فقط public_request از self.request_timeout استفاده می‌کند).
        نتیجه: روی یک اتصالِ نیمه‌باز، برنامه تا ابد منتظر می‌ماند و بی‌سر و صدا هنگ می‌کند.
        اینجا متد request را می‌پوشانیم تا همیشه timeout داشته باشد (بدون دستکاری کتابخانه).
        """
        for name in ("private", "public", "graphql"):
            session = getattr(cl, name, None)
            if session is None or getattr(session, "_ig_timeout_patched", False):
                continue
            original = session.request

            def request(method, url, _original=original, **kwargs):
                if not kwargs.get("timeout"):  # None یا حذف‌شده ⇒ مقدار پیش‌فرض ما
                    kwargs["timeout"] = timeout
                return _original(method, url, **kwargs)

            session.request = request          # type: ignore[method-assign]
            session._ig_timeout_patched = True  # type: ignore[attr-defined]
        logger.info("سقفِ زمانی هر درخواست: %.0f ثانیه", timeout)

    def switch_transport(self, name: str) -> None:
        """تغییر ترنسپورتِ شبکه در زمان اجرا (مثلاً curl → requests برای عبور از پروکسی).

        هر دو مقدار در سورس instagrapi پشتیبانی می‌شوند (private_transport: requests|curl).
        """
        if name not in ("curl", "requests"):
            raise ValueError("private_transport باید curl یا requests باشد")
        if self.client.private_transport == name:
            return
        self.client.set_retry_config(private_transport=name)
        if self.settings.proxy:  # پس از ساختِ سشنِ جدید، پروکسی را دوباره اعمال کن
            self.client.set_proxy(self.settings.proxy)
        logger.warning("ترنسپورت به '%s' تغییر یافت.", name)

    def with_transport_fallback(self, fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """اجرای تابع؛ در صورت خطای شبکه با curl، یک بار با requests امتحان می‌کند."""
        try:
            return fn(*args, **kwargs)
        except ClientConnectionError as exc:
            if not self.settings.transport_fallback or self.client.private_transport != "curl":
                raise
            logger.warning(
                "خطای شبکه با ترنسپورت curl (%s)؛ یک بار با requests امتحان می‌کنیم. "
                "برای همیشه: در .env بگذارید IG_PRIVATE_TRANSPORT=requests", exc,
            )
            self.switch_transport("requests")
            return fn(*args, **kwargs)

    # ------------------------------------------------------------------ API عمومی
    def login(self, method: Optional[LoginMethod] = None, fresh: bool = False) -> LoginMethod:
        """زنجیره‌ی ورود: session → sessionid → password (مگر اینکه روش خاصی خواسته شود)."""
        if fresh:
            self.store.delete()
            logger.info("--fresh: فایل سشن حذف شد.")

        order = [method] if method else [LoginMethod.SESSION, LoginMethod.SESSIONID, LoginMethod.PASSWORD]
        for candidate in order:
            if candidate == LoginMethod.SESSION and self._login_with_session():
                return LoginMethod.SESSION
            if candidate == LoginMethod.SESSIONID and self._login_with_sessionid():
                self.save_session()
                return LoginMethod.SESSIONID
            if candidate == LoginMethod.PASSWORD and self._login_with_password():
                self.save_session()
                return LoginMethod.PASSWORD

        raise AuthenticationError(
            "ورود با هیچ روشی موفق نبود.\n"
            "  • اگر sessionid داری:  export IG_SESSION_ID=...\n"
            "  • در غیر این صورت:      export IG_USERNAME=... IG_PASSWORD=...  (و در صورت نیاز IG_2FA_CODE یا IG_TOTP_SEED)\n"
            "  • کدهای چالش را در فایل زیر بنویس:  " + str(self.settings.code_file)
        )

    def save_session(self) -> None:
        self.store.write(self.client, self.login_method.value if self.login_method else "unknown")
        logger.info("سشن ذخیره شد: %s", self.settings.session_path)

    def ensure_login(self, method: Optional[LoginMethod] = None, fresh: bool = False) -> None:
        if not fresh and self.login_method:  # همین لحظه لاگین کرده‌ایم
            return
        self.login(method=method, fresh=fresh)

    def sessionid(self) -> Optional[str]:
        """استخراج sessionid فعلی (برای استفاده در مرورگر/جای دیگر) — محرمانه!"""
        return self.client.sessionid or None

    # ------------------------------------------------------- فراخوانی امنِ API
    def safe_call(self, fn: Callable[..., T], *args: Any, attempts: int = 3, **kwargs: Any) -> T:
        """اجرا با بازتلاش برای خطاهای گذرا (backoff نمایی + jitter) و ورودِ مجدد خودکار."""
        last_exc: Optional[BaseException] = None
        for attempt in range(1, attempts + 1):
            try:
                return self.with_transport_fallback(fn, *args, **kwargs)
            except AUTH_ERRORS as exc:
                if attempt == attempts or not self.store.exists():
                    raise
                logger.warning("خطای احراز هویت (%s)؛ تلاش برای ورود مجدد ...", type(exc).__name__)
                self.client.authorization_data = {}
                self.login(fresh=False)
            except TRANSIENT_ERRORS as exc:
                last_exc = exc
                wait = min(60, 5 * (2 ** (attempt - 1))) + random.uniform(0, 2)
                logger.warning("خطای گذرا (%s)؛ صبر %.1f ثانیه (تلاش %d/%d)",
                               type(exc).__name__, wait, attempt, attempts)
                if attempt == attempts:
                    break
                time.sleep(wait)
            except UserNotFound:
                raise
        assert last_exc is not None
        raise last_exc


def _age_str(saved_at: Optional[float]) -> str:
    if not saved_at:
        return "?"
    hours = (time.time() - saved_at) / 3600
    return f"{hours:.1f} ساعت پیش" if hours < 48 else f"{hours / 24:.1f} روز پیش"
