"""مونتاژ اپلیکیشنِ ربات: Bot + Dispatcher + روتر + لاگین پس‌زمینه."""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
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
        logger.info("ربات آماده است؛ در حال لاگینِ پس‌زمینه به اینستاگرام ...")
        try:
            await bot.set_my_commands(
                [
                    BotCommand(command="start", description="شروع و منو"),
                    BotCommand(command="page <username>", description="تحلیل یک پیج"),
                    BotCommand(command="tag <hashtag>", description="تحلیل یک هشتگ"),
                    BotCommand(command="history", description="هیزتوری من"),
                    BotCommand(command="menu", description="نمایش منو"),
                    BotCommand(command="help", description="راهنما"),
                    BotCommand(command="cancel", description="لغوی واردکردن"),
                ]
            )
        except Exception as exc:  # noqa: BLE001 — تزئینی است
            logger.warning("ثبت منوی دستورات ناموفق: %s", exc)
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
