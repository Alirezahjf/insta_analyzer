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
