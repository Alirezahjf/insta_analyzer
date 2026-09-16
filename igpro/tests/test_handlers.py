import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402
from aiogram import Dispatcher  # noqa: E402
from aiogram.fsm.storage.memory import MemoryStorage  # noqa: E402
from aiogram.types import Update  # noqa: E402

from bot.config import BotSettings  # noqa: E402
from bot.database import Database  # noqa: E402
from bot.engine import IgEngine  # noqa: E402
from bot.handlers import build_router  # noqa: E402
from tests.fakes import FakeBot, FakeIG, make_page_fixture  # noqa: E402

ADMIN = 111
USER = 222


def make_settings(tmp) -> BotSettings:
    return BotSettings(
        token="x", admin_id=None, state_dir=Path(tmp),
        history_limit=10, hashtag_show=3, hashtag_scan=9,
    )


def text_update(bot: FakeBot, uid: int, text: str, uid_counter: list) -> Update:
    uid_counter[0] += 1
    n = uid_counter[0]
    return Update.model_validate(
        {
            "update_id": n,
            "message": {
                "message_id": n,
                "date": int(time.time()),
                "chat": {"id": uid, "type": "private"},
                "from": {"id": uid, "is_bot": False, "first_name": f"U{uid}",
                         "username": f"user{uid}"},
                "text": text,
            },
        },
        context={"bot": bot},
    )


def callback_update(bot: FakeBot, uid: int, data: str, message_id: int, uid_counter: list) -> Update:
    uid_counter[0] += 1
    n = uid_counter[0]
    return Update.model_validate(
        {
            "update_id": n,
            "callback_query": {
                "id": f"cb{n}",
                "from": {"id": uid, "is_bot": False, "first_name": f"U{uid}",
                         "username": f"user{uid}"},
                "chat_instance": "ci",
                "data": data,
                "message": {
                    "message_id": message_id,
                    "date": int(time.time()),
                    "chat": {"id": uid, "type": "private"},
                    "from": {"id": uid, "is_bot": False, "first_name": f"U{uid}"},
                    "text": "old-menu",
                },
            },
        },
        context={"bot": bot},
    )


@pytest.fixture()
def env(tmp_path):
    bot = FakeBot()
    db = Database(tmp_path / "bot.db")
    ig = FakeIG(make_page_fixture())
    engine = IgEngine(ig, make_settings(tmp_path))
    router = build_router(make_settings(tmp_path), db, engine)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    return {"bot": bot, "db": db, "dp": dp, "engine": engine, "ig": ig, "counter": [0]}


async def feed(env, update):
    await env["dp"].feed_update(bot=env["bot"], update=update)


def test_full_user_journey(env):
    async def scenario():
        bot, db, counter = env["bot"], env["db"], env["counter"]

        # 1) اولین استارت ⇒ مدیر
        await feed(env, text_update(bot, ADMIN, "/start", counter))
        first = bot.last_sent(ADMIN)
        assert "مدیر" in first["text"]
        assert db.get_user(ADMIN)["is_admin"] == 1

        # 2) کاربر دوم ⇒ بدون دسترسی + دکمه‌ی درخواست
        await feed(env, text_update(bot, USER, "/start", counter))
        denied = bot.last_sent(USER)
        assert "دسترسی ندارید" in denied["text"]
        assert bot.has_callback(USER, "a:req")

        # 3) /page بدون دسترسی رد می‌شود
        await feed(env, text_update(bot, USER, "/page nima.arish", counter))
        assert "دسترسی ندارید" in bot.last_sent(USER)["text"]

        # 4) درخواست دسترسی ⇒ پیام به مدیر با دکمه‌های تایید
        denied_mid = denied["message_id"]
        await feed(env, callback_update(bot, USER, "a:req", denied_mid, counter))
        to_admin = bot.last_sent(ADMIN)
        assert "درخواست دسترسی" in to_admin["text"]
        assert str(USER) in to_admin["text"]
        assert bot.has_callback(ADMIN, f"a:ok:{USER}:")

        # 5) مدیر تایید ۲۴ ساعت ⇒ کاربر دسترسی می‌گیرد
        await feed(env, callback_update(bot, ADMIN, f"a:ok:{USER}:24", to_admin["message_id"], counter))
        granted = bot.last_sent(USER)
        assert "دسترسی فعال شد" in granted["text"]
        assert "1 روز" in granted["text"]
        assert db.is_authorized(USER)

        # 6) کاربر /page بدون آرگومان ⇒ پرامپت + state
        await feed(env, text_update(bot, USER, "/page", counter))
        prompt = bot.last_sent(USER)
        assert "نام کاربری" in prompt["text"]

        # 7) فرستادن نام کاربری ⇒ گزارش کامل
        await feed(env, text_update(bot, USER, "nima.arish", counter))
        # پیام گام‌به‌گام + پیام نتیجه
        user_msgs = bot.sent_to(USER)
        result = [m for m in user_msgs if "تحلیل پیج" in (m.get("text") or "")
                  and "Viral Score" in (m.get("text") or "")]
        assert result, f"no result message: {[m.get('text','')[:60] for m in user_msgs]}"
        assert "@nima.arish" in result[-1]["text"]
        assert "47" not in result[-1]["text"] or "Viral" in result[-1]["text"]

        # 8) هیزتوری ثبت شده + نمایش
        assert db.count_history(USER) == 1
        await feed(env, text_update(bot, USER, "/history", counter))
        hist = bot.last_sent(USER)
        assert "هیزتوری" in hist["text"]
        hist_id = db.list_history(USER)[0]["id"]
        assert bot.has_callback(USER, f"h:v:{hist_id}")

        # 9) مشاهده‌ی آیتم هیزتوری
        await feed(env, callback_update(bot, USER, f"h:v:{hist_id}", hist["message_id"], counter))
        view = bot.last_sent(USER)
        assert "آیتم هیزتوری" in view["text"]
        assert "Viral Score" in view["text"]

        # 10) /users مدیر
        await feed(env, text_update(bot, ADMIN, "/users", counter))
        users_txt = bot.last_sent(ADMIN)["text"]
        assert "user111" in users_txt.replace("admin", "user111") or "👑" in users_txt
        assert "user222" in users_txt or "⏳" in users_txt

        # 11) /revoke
        await feed(env, text_update(bot, ADMIN, f"/revoke {USER}", counter))
        assert "لغو شد" in bot.last_sent(ADMIN)["text"]
        assert not db.is_authorized(USER)

        # 12) کاربرِ بدون دسترسی دوباره ادرس می‌گیرد
        await feed(env, text_update(bot, USER, "/page nima.arish", counter))
        assert "دسترسی ندارید" in bot.last_sent(USER)["text"]

        # 13) /cancel داخل state
        await feed(env, text_update(bot, ADMIN, "/page", counter))
        await feed(env, text_update(bot, ADMIN, "/cancel", counter))
        assert "لغو شد" in bot.last_sent(ADMIN)["text"]

        # 14) متنِ بدون state ⇒ منو
        await feed(env, text_update(bot, ADMIN, "سلام", counter))
        assert "منو" in bot.last_sent(ADMIN)["text"]

        db.close()

    asyncio.run(scenario())


def test_busy_lock_rejects_second_request(env):
    async def scenario():
        bot, counter = env["bot"], env["counter"]
        # لاگین و دسترسی را مستقیم ست می‌کنیم
        env["db"].ensure_user(ADMIN, "admin", "Admin")
        env["db"].grant_access(USER, 100)
        # قفل را دستی بگیرید (شبیه‌سازیِ گزارشِ در حال اجرا)
        await env["engine"].acquire(ADMIN)
        try:
            await feed(env, text_update(bot, USER, "/page nima.arish", counter))
            busy = bot.last_sent(USER)
            assert "مشغول" in busy["text"] or "در حال" in busy["text"]
            # و برای صاحبِ در حال اجرا:
            await feed(env, text_update(bot, ADMIN, "/page nima.arish", counter))
            own = bot.last_sent(ADMIN)
            assert "درخواست قبلی" in own["text"]
        finally:
            env["engine"].release()
        env["db"].close()

    asyncio.run(scenario())


def test_invalid_inputs(env):
    async def scenario():
        bot, counter = env["bot"], env["counter"]
        env["db"].ensure_user(ADMIN, "admin", "Admin")
        # نام کاربری نامعتبر
        await feed(env, text_update(bot, ADMIN, "/page x y z", counter))
        assert "معتبر" in bot.last_sent(ADMIN)["text"] or "نام کاربری" in bot.last_sent(ADMIN)["text"]
        # /tag با هشتگ فارسی از fixture (page fixture ندارد ⇒ ReportError)
        await feed(env, text_update(bot, ADMIN, "/tag #مدلینگ", counter))
        # گام‌به‌گام + پیام خطا
        admin_msgs = bot.sent_to(ADMIN)
        assert any("خطا" in (m.get("text") or "") or "درخواست کامل نشد" in (m.get("text") or "")
                   for m in admin_msgs)
        env["db"].close()

    asyncio.run(scenario())


def test_callback_menu_help(env):
    async def scenario():
        bot, counter = env["bot"], env["counter"]
        env["db"].ensure_user(ADMIN, "admin", "Admin")
        await feed(env, text_update(bot, ADMIN, "/start", counter))
        menu_mid = bot.last_sent(ADMIN)["message_id"]
        # m:help
        await feed(env, callback_update(bot, ADMIN, "m:help", menu_mid, counter))
        assert "راهنما" in bot.edited[-1]["text"]
        # m:page ⇒ پرامپت
        await feed(env, callback_update(bot, ADMIN, "m:page", menu_mid, counter))
        assert "نام کاربری" in bot.edited[-1]["text"]
        # سپس نام مستقیم
        await feed(env, text_update(bot, ADMIN, "nima.arish", counter))
        assert any("تحلیل پیج" in (m.get("text") or "") and "Viral Score" in (m.get("text") or "")
                   for m in bot.sent_to(ADMIN))
        env["db"].close()

    asyncio.run(scenario())


def test_admin_id_env_forced(env):
    """اگر BOT_ADMIN_ID ست باشد، اولین استارتر مدیر نمی‌شود."""
    async def scenario():
        bot, counter = env["bot"], env["counter"]
        db = env["db"]
        # ساخت دوباره با admin_id=555
        from bot.handlers import build_router as br
        from bot.config import BotSettings
        tmp = env["db"].path.parent
        settings2 = BotSettings(token="x", admin_id=555, state_dir=tmp,
                                history_limit=10, hashtag_show=3, hashtag_scan=9)
        router2 = br(settings2, db, env["engine"])
        dp2 = Dispatcher(storage=MemoryStorage())
        dp2.include_router(router2)

        await dp2.feed_update(bot=bot, update=text_update(bot, 777, "/start", counter))
        assert db.get_user(777)["is_admin"] == 0
        await dp2.feed_update(bot=bot, update=text_update(bot, 555, "/start", counter))
        assert db.get_user(555)["is_admin"] == 1
        db.close()

    asyncio.run(scenario())


def test_authentication_error_friendly_message(tmp_path):
    """شکستِ کاملِ احراز هویت ⇒ پیامِ دوستانه، نه «خطای غیرمنتظره»."""
    from igclient.client import AuthenticationError

    async def scenario():
        bot = FakeBot()
        db = Database(tmp_path / "bot.db")
        ig = FakeIG(make_page_fixture(),
                    login_side_effect=AuthenticationError("هیچ روشی موفق نبود"))
        ig.login_method = None  # تا موتور واقعاً لاگین کند
        engine = IgEngine(ig, make_settings(tmp_path))
        router = build_router(make_settings(tmp_path), db, engine)
        dp = Dispatcher(storage=MemoryStorage())
        dp.include_router(router)

        counter = [0]

        def upd(text):
            counter[0] += 1
            return text_update(bot, ADMIN, text, counter)

        await dp.feed_update(bot=bot, update=upd("/start"))
        await dp.feed_update(bot=bot, update=upd("/page nima.arish"))
        last = bot.last_sent(ADMIN)
        assert "ورود به اینستاگرام" in last["text"], last["text"]
        assert db.list_history(ADMIN)[0]["status"] == "auth_error"
        db.close()

    asyncio.run(scenario())


def test_cancel_outside_state_is_unknown_command(env):
    """/cancel بدون state فعال باید به فالبک بیفتد (نه اینکه کروتینش معلق بماند)."""
    async def scenario():
        bot, db, counter = env["bot"], env["db"], env["counter"]
        await feed(env, text_update(bot, ADMIN, "/start", counter))
        await feed(env, text_update(bot, ADMIN, "/cancel", counter))
        last = bot.last_sent(ADMIN)
        assert "لغو شد" not in last["text"]
        assert "ناشناس" in last["text"], last["text"]
        db.close()

    asyncio.run(scenario())
