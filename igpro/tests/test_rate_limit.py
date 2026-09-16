import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402
from aiogram import Dispatcher  # noqa: E402
from aiogram.fsm.storage.memory import MemoryStorage  # noqa: E402
from aiogram.types import Update  # noqa: E402

import igclient.client as cc  # noqa: E402
from igclient.client import IGClient, RateLimited  # noqa: E402
from igclient.config import Settings  # noqa: E402
from instagrapi.exceptions import (  # noqa: E402
    ClientThrottledError,
    ClientUnauthorizedError,
    PleaseWaitFewMinutes,
)

from bot.config import BotSettings  # noqa: E402
from bot.database import Database  # noqa: E402
from bot.engine import IgEngine  # noqa: E402
from bot.handlers import build_router  # noqa: E402
from tests.fakes import FakeBot  # noqa: E402


def _settings(tmp_path) -> BotSettings:
    return BotSettings(token="x", admin_id=None, state_dir=Path(tmp_path),
                       history_limit=10, hashtag_show=3, hashtag_scan=9)


def test_safe_call_rate_limited_waits_once_then_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("IG_STATE_DIR", str(tmp_path))
    for v in ("IG_SESSION_ID", "IG_USERNAME", "IG_PASSWORD", "IG_TOTP_SEED", "IG_2FA_CODE"):
        monkeypatch.delenv(v, raising=False)
    ig = IGClient(Settings.from_env())

    sleeps: list = []
    monkeypatch.setattr(cc.time, "sleep", lambda s: sleeps.append(s))
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        raise PleaseWaitFewMinutes(Exception("rl"))

    with pytest.raises(RateLimited):
        ig.safe_call(fn)
    # فقط یک بازتلاش، بعد از ~۶۰ ثانیه (نه ۳ تلاشِ ۵/۱۱/۲۱ ثانیه‌ای)
    assert calls["n"] == 2
    assert len(sleeps) == 1 and 60 <= sleeps[0] <= 80


def test_safe_call_429_same_behavior(tmp_path, monkeypatch):
    monkeypatch.setenv("IG_STATE_DIR", str(tmp_path))
    for v in ("IG_SESSION_ID", "IG_USERNAME", "IG_PASSWORD", "IG_TOTP_SEED", "IG_2FA_CODE"):
        monkeypatch.delenv(v, raising=False)
    ig = IGClient(Settings.from_env())

    sleeps: list = []
    monkeypatch.setattr(cc.time, "sleep", lambda s: sleeps.append(s))
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        raise ClientThrottledError(Exception("429"))

    with pytest.raises(RateLimited):
        ig.safe_call(fn)
    assert calls["n"] == 2 and len(sleeps) == 1


def test_safe_call_401_triggers_relogin(tmp_path, monkeypatch):
    """401 = سشن بی‌اعتبار ⇒ قبل از شکست، تلاش ورودِ مجدد می‌شود."""
    monkeypatch.setenv("IG_STATE_DIR", str(tmp_path))
    for v in ("IG_SESSION_ID", "IG_USERNAME", "IG_PASSWORD", "IG_TOTP_SEED", "IG_2FA_CODE"):
        monkeypatch.delenv(v, raising=False)
    ig = IGClient(Settings.from_env())
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        raise ClientUnauthorizedError(Exception("401"))

    # بدون هیچ اعتباری، ورودِ مجدد AuthenticationError می‌دهد ⇒ یعنی مسیر AUTH رفته
    with pytest.raises(cc.AuthenticationError):
        ig.safe_call(fn, attempts=2)
    assert calls["n"] == 1


def test_bot_shows_ratelimited_message(tmp_path):
    async def scenario():
        bot = FakeBot()
        db = Database(tmp_path / "bot.db")
        db.ensure_user(111, "admin", "Admin")

        class _Rat:
            client = None
            login_method = "password"

            def login(self, method=None, fresh=False):
                return "password"

            def safe_call(self, fn, *a, **k):
                raise RateLimited("test")

        engine = IgEngine(_Rat(), _settings(tmp_path))  # type: ignore[arg-type]
        router = build_router(_settings(tmp_path), db, engine)
        dp = Dispatcher(storage=MemoryStorage())
        dp.include_router(router)

        counter = [0]

        def upd(text):
            counter[0] += 1
            n = counter[0]
            return Update.model_validate(
                {
                    "update_id": n,
                    "message": {
                        "message_id": n, "date": int(time.time()),
                        "chat": {"id": 111, "type": "private"},
                        "from": {"id": 111, "is_bot": False, "first_name": "A", "username": "a"},
                        "text": text,
                    },
                },
                context={"bot": bot},
            )

        await dp.feed_update(bot=bot, update=upd("/start"))
        await dp.feed_update(bot=bot, update=upd("/page nima.arish"))
        last = bot.last_sent(111)
        assert "ریت‌لیمیت" in last["text"], last["text"]
        assert "۵ تا ۱۵ دقیقه" in last["text"]
        db.close()

    asyncio.run(scenario())


def _make_client(tmp_path, monkeypatch) -> IGClient:
    monkeypatch.setenv("IG_STATE_DIR", str(tmp_path))
    for v in ("IG_SESSION_ID", "IG_USERNAME", "IG_PASSWORD", "IG_TOTP_SEED", "IG_2FA_CODE"):
        monkeypatch.delenv(v, raising=False)
    return IGClient(Settings.from_env())


def test_safe_call_fast_fails_during_cooldown(tmp_path, monkeypatch):
    """در پنجره‌ی خنک‌شدن، حتی یک درخواست هم به سرور نمی‌رود."""
    ig = _make_client(tmp_path, monkeypatch)
    ig._mark_throttled(60)
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        return "ok"

    with pytest.raises(RateLimited):
        ig.safe_call(fn)
    assert calls["n"] == 0


def test_login_refuses_when_throttled(tmp_path, monkeypatch):
    """بعد از ریت‌لیمیت، زنجیره‌ی لاگین به‌جای پسوردزدن، سریع شکست می‌خورد."""
    ig = _make_client(tmp_path, monkeypatch)
    ig._mark_throttled(60)
    with pytest.raises(RateLimited):
        ig.login()


def test_ratelimit_sets_cooldown(tmp_path, monkeypatch):
    """بعد از شکستِ ریت‌لیمیت، پنجره‌ی خنک‌شدن فعال می‌شود."""
    ig = _make_client(tmp_path, monkeypatch)
    monkeypatch.setattr(cc.time, "sleep", lambda s: None)

    def fn():
        raise PleaseWaitFewMinutes(Exception("rl"))

    assert not ig.throttled
    with pytest.raises(RateLimited):
        ig.safe_call(fn)
    assert ig.throttled
    assert ig.throttle_remaining() > 0


def test_sessionid_login_swallows_network_errors(tmp_path, monkeypatch):
    """خطاهای خامِ شبکه (مثل TooManyRedirects در لاگینِ 3.0.2) نباید بالا بزنند؛
    زنجیره باید به روشِ بعدی برود."""
    import requests as _requests
    ig = _make_client(tmp_path, monkeypatch)
    monkeypatch.setenv("IG_SESSION_ID", "25037423982" + "a" * 60)
    ig = IGClient(Settings.from_env())

    def boom(sessionid):
        raise _requests.exceptions.TooManyRedirects("Exceeded 30 redirects.")

    monkeypatch.setattr(ig.client, "login_by_sessionid", boom)
    assert ig._login_with_sessionid() is False  # بدون استثنا، زنجیره ادامه دارد


def test_sessionid_login_throttle_does_not_burn_password(tmp_path, monkeypatch):
    """ریت‌لیمیت در لاگینِ sessionid ⇒ پنجره‌ی خنک‌شدن؛ ادامه‌ی زنجیره = شکستِ سریع."""
    ig = _make_client(tmp_path, monkeypatch)
    monkeypatch.setenv("IG_SESSION_ID", "25037423982" + "a" * 60)
    monkeypatch.setenv("IG_USERNAME", "u")
    monkeypatch.setenv("IG_PASSWORD", "p")
    ig = IGClient(Settings.from_env())

    def throttled(sessionid):
        raise ClientThrottledError(Exception("429"))

    password_calls = {"n": 0}

    def fake_password_login(*a, **k):
        password_calls["n"] += 1
        return True

    monkeypatch.setattr(ig.client, "login_by_sessionid", throttled)
    monkeypatch.setattr(ig.client, "login", fake_password_login)
    with pytest.raises(RateLimited):
        ig.login()
    assert password_calls["n"] == 0  # هیچ تلاش پسوردی زیرِ ریت‌لیمیت انجام نشد
    assert ig.throttled
