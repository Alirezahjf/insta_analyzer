"""مونتاژ اپلیکیشنِ ربات: Bot + Dispatcher + روتر + لاگین پس‌زمینه."""
from __future__ import annotations

import asyncio
import logging
import re

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand
from aiogram.types.error_event import ErrorEvent

from bot.config import BotSettings
from bot.database import Database
from bot.engine import IgEngine
from bot.handlers import build_router
from igclient.client import IGClient
from igclient.config import Settings

logger = logging.getLogger("igpro.bot.app")

BG_LOGIN_TIMEOUT = 300.0

# قوانین تلگرام برای منوی دستورات (سرور کلِ فهرست را با یک خطا رد می‌کند):
#   نامِ دستور: فقط a-z 0-9 _ و ۱ تا ۳۲ کاراکتر
#   توضیح:      ۳ تا ۲۵۶ کاراکتر
BOT_COMMAND_RE = re.compile(r"^[a-z0-9_]{1,32}$")

# فهرستِ دستورات — قبل از ارسال اعتبارسنجی می‌شود (نگهبانِ بازگشتِ خطا به نسخه‌های قدیمی)
BOT_COMMANDS = [
    BotCommand(command="start", description="شروع و منو"),
    BotCommand(command="page", description="تحلیل پیج: /page username"),
    BotCommand(command="tag", description="تحلیل هشتگ: /tag #hashtag"),
    BotCommand(command="history", description="هیزتوری من"),
    BotCommand(command="menu", description="نمایش منو"),
    BotCommand(command="help", description="راهنما"),
    BotCommand(command="cancel", description="لغوِ واردکردن"),
]


def validate_bot_commands(commands: list[BotCommand]) -> list[BotCommand]:
    """فیلترِ محلی: دستورهای نامعتبر را قبل از ارسال حذف و لاگ می‌کند."""
    valid: list[BotCommand] = []
    for cmd in commands:
        name = (cmd.command or "").strip()
        desc = (cmd.description or "").strip()
        if not BOT_COMMAND_RE.match(name):
            logger.warning("دستور %r نامعتبر است (فقط a-z/0-9/_ تا ۳۲ کاراکتر) — حذف شد.", name)
            continue
        if not (3 <= len(desc) <= 256):
            logger.warning("توضیح دستور /%s نامعتبر است (۳ تا ۲۵۶ کاراکتر) — حذف شد.", name)
            continue
        if name != cmd.command or desc != cmd.description:
            cmd = BotCommand(command=name, description=desc)
        valid.append(cmd)
    return valid


async def register_bot_commands(bot: Bot, commands: list[BotCommand]) -> None:
    """ثبت منوی دستورات با تحملِ خطا.

    اگر ثبتِ یکجا شکست خورد (خطای 400 تلگرام مثل BOT_COMMAND_INVALID)، دستورات
    یکی‌یکی اضافه می‌شوند تا دستورِ مشکل‌دار شناسایی و حذف شود و بقیه ثبت بمانند.
    """
    commands = validate_bot_commands(commands)
    if not commands:
        logger.warning("هیچ دستور معتبری برای ثبت وجود ندارد.")
        return
    try:
        await bot.set_my_commands(commands)
        logger.info("منوی دستورات ثبت شد (%d دستور).", len(commands))
        return
    except TelegramAPIError as exc:
        logger.warning("ثبتِ یکجای منوی دستورات ناموفق بود (%s) — ثبتِ تک‌به‌تک.", exc)

    accepted: list[BotCommand] = []
    for cmd in commands:
        trial = accepted + [cmd]
        try:
            await bot.set_my_commands(trial)
            accepted = trial
        except TelegramAPIError as exc:
            logger.warning("دستور /%s توسط تلگرام رد شد (%s) — حذف شد.", cmd.command, exc)
    if accepted:
        logger.info("منوی دستورات ثبت شد (%d از %d دستور).", len(accepted), len(commands))
    else:
        logger.warning("ثبت منوی دستورات به‌طور کامل ناموفق بود — ربات بدون منو ادامه می‌دهد.")


async def _background_login(ig: IGClient) -> None:
    """لاگین اولیه در پس‌زمینه — شکستش ربات را خاموش نمی‌کند؛
    اولین درخواست دوباره تلاش می‌کند (زنجیره‌ی session→sessionid→password)."""
    try:
        await asyncio.wait_for(asyncio.to_thread(ig.login), timeout=BG_LOGIN_TIMEOUT)
        logger.info("لاگین پس‌زمینه موفق شد (%s)", ig.login_method.value if ig.login_method else "?")
    except asyncio.TimeoutError:
        logger.error("لاگین پس‌زمینه در مهلت %d ثانیه تمام نشد", int(BG_LOGIN_TIMEOUT))
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "لاگین پس‌زمینه ناموفق بود: %s — در اولین درخواست دوباره تلاش می‌شود.",
            exc, exc_info=True,
        )


def build_application(bot_settings: BotSettings, ig_settings: Settings) -> tuple[Bot, Dispatcher]:
    if not bot_settings.token:
        raise RuntimeError("BOT_TOKEN در .env تنظیم نشده است.")

    db = Database(bot_settings.state_dir / "bot.db")
    ig = IGClient(ig_settings)
    engine = IgEngine(ig, bot_settings)

    bot = Bot(
        token=bot_settings.token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())

    router = build_router(bot_settings, db, engine)
    dp.include_router(router)

    @dp.startup()
    async def _on_startup() -> None:
        if not bot_settings.startup_login:
            logger.info(
                "حالتِ اقتصادی: در شروعِ ربات درخواستی به اینستاگرام نمی‌رود؛ "
                "سشن در اولین درخواست بررسی/بازیابی می‌شود (گامِ اولِ پیامِ گام‌به‌قدم). "
                "برای لاگینِ فوری در شروع: BOT_STARTUP_LOGIN=1"
            )
            do_bg_login = False
        else:
            logger.info("ربات آماده است؛ در حال لاگینِ پس‌زمینه به اینستاگرام ...")
            do_bg_login = True
        try:
            await register_bot_commands(bot, BOT_COMMANDS)
        except Exception as exc:  # noqa: BLE001 — تزئینی است؛ ربات بدون منو هم کار می‌کند
            logger.warning("ثبت منوی دستورات ناموفق: %s", exc)
        if do_bg_login:
            asyncio.create_task(_background_login(ig))

    @dp.shutdown()
    async def _on_shutdown() -> None:
        db.close()
        await bot.session.close()

    @dp.errors()
    async def _on_error(event: ErrorEvent) -> bool:
        """خطای غیرمنتظره در هر هندلر — لاگ کامل + اطلاع‌رسانی امن به کاربر."""
        logger.exception("خطای غیرمنتظره در هندلر: %s", event.exception)
        update = event.update
        target_chat_id = None
        if update is not None:
            for attr in ("message", "callback_query"):
                obj = getattr(update, attr, None)
                msg = getattr(obj, "message", None) or obj
                chat = getattr(msg, "chat", None)
                if chat is not None:
                    target_chat_id = chat.id
                    break
        if target_chat_id is not None:
            try:
                await bot.send_message(
                    chat_id=target_chat_id,
                    text=(
                        "⚠️ خطای غیرمنتظره‌ای رخ داد و در لاگ ثبت شد.\n"
                        "فایل: <code>state/logs/igpro.log</code>"
                    ),
                )
            except Exception:  # noqa: BLE001
                pass
        return True

    return bot, dp
