"""تستِ اعتبارسنجی و ثبتِ منوی دستورات (رفع خطای BOT_COMMAND_INVALID)."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aiogram.exceptions import TelegramAPIError  # noqa: E402
from aiogram.types import BotCommand  # noqa: E402

from bot.app import BOT_COMMANDS, register_bot_commands, validate_bot_commands  # noqa: E402
from tests.fakes import FakeBot  # noqa: E402


def test_default_commands_are_valid():
    valid = validate_bot_commands(BOT_COMMANDS)
    assert len(valid) == len(BOT_COMMANDS)
    assert bot_names(valid) == ["start", "page", "tag", "history", "menu", "help", "cancel"]


def bot_names(cmds):
    return [c.command for c in cmds]


def test_validate_drops_invalid_entries():
    cmds = [
        BotCommand(command="start", description="شروع"),
        BotCommand(command="Bad-Name", description="توضیح"),       # حرف بزرگ/خط‌تیره
        BotCommand(command="ok_cmd", description="ab"),             # توضیح < ۳ کاراکتر
        BotCommand(command="toolong" * 10, description="توضیح بلند"),  # نام > ۳۲ کاراکتر
        BotCommand(command=" clean ", description="  توضیح سالم  "),   # باید تمیز شود
    ]
    valid = validate_bot_commands(cmds)
    assert bot_names(valid) == ["start", "clean"]
    assert valid[1].description == "توضیح سالم"


def test_register_commands_bulk_success():
    async def scenario():
        bot = FakeBot()
        await register_bot_commands(bot, BOT_COMMANDS)
        assert len(bot.registered_commands) == len(BOT_COMMANDS)

    asyncio.run(scenario())


def test_register_commands_fallback_skips_bad_one():
    """اگر ثبتِ یکجا رد شد، دستورهای سالم یکی‌یکی ثبت می‌شوند و بدی حذف می‌شود."""

    class PickyBot(FakeBot):
        """فهرست‌هایی که دستور «بد» داشته باشند را رد می‌کند."""

        def __init__(self):
            super().__init__()
            self.attempts = []

        async def set_my_commands(self, commands, **kw):
            self.attempts.append(list(commands))
            if any(c.command == "badone" for c in commands):
                raise TelegramAPIError(method=None, message="Bad Request: BOT_COMMAND_INVALID")
            return await super().set_my_commands(commands, **kw)

    async def scenario():
        bot = PickyBot()
        cmds = [
            BotCommand(command="start", description="شروع و منو"),
            BotCommand(command="badone", description="این یکی رد می‌شود"),
            BotCommand(command="help", description="راهنما"),
        ]
        await register_bot_commands(bot, cmds)
        assert bot_names(list(cmds)) == ["start", "badone", "help"]
        # منوی نهایی = فقط سالم‌ها
        assert [c[0] for c in bot.registered_commands] == ["start", "help"]

    asyncio.run(scenario())
