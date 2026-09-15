#!/usr/bin/env python3
"""نقطه‌ی ورود ربات تلگرام.

اجرا:
    cd igpro
    py bot\\run.py          (ویندوز)
    python bot/run.py      (لینوکس/ماک)

پیش‌نیازها:
    pip install -r requirements.txt
    کپی .env.example به .env و پرکردن BOT_TOKEN + یک روشِ ورود اینستاگرام
"""
from __future__ import annotations

import sys
from pathlib import Path

# igpro را به sys.path اضافه می‌کنیم تا مستقل از cwd اجرا شود
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from igclient.config import Settings, load_dotenv  # noqa: E402
from igclient.logging_setup import setup_logging  # noqa: E402
from bot.app import build_application  # noqa: E402
from bot.config import BotSettings  # noqa: E402


def main() -> int:
    load_dotenv()
    bot_settings = BotSettings.from_env()
    if not bot_settings.token:
        print(
            "❌ BOT_TOKEN تنظیم نشده است.\n"
            "   فایل .env را بسازید (کپی .env.example) و این خط را پر کنید:\n"
            "       BOT_TOKEN=123456:ABC...  (از @BotFather)\n"
            "   همچنین یکی از روش‌های ورود اینستاگرام (IG_SESSION_ID یا IG_USERNAME+IG_PASSWORD).",
            file=sys.stderr,
        )
        return 2

    settings = Settings.from_env()
    logger = setup_logging(settings.log_level, settings.log_dir)
    logger.info("=== igpro Telegram Bot in start ===")
    logger.info("سشن: %s | دیتابیس: %s/bot.db", settings.session_path, bot_settings.state_dir)

    try:
        bot, dp = build_application(bot_settings, settings)
    except Exception as exc:  # noqa: BLE001
        logger.exception("خطا در ساخت اپلیکیشن: %s", exc)
        return 1

    try:
        dp.run_polling(bot)
    except (KeyboardInterrupt, SystemExit):
        logger.info("=== bot stopped ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
