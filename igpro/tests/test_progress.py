import asyncio
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


from bot.progress import ProgressReporter, Step  # noqa: E402
from tests.fakes import FakeBot  # noqa: E402


def test_progress_lifecycle():
    async def scenario():
        bot = FakeBot()
        rep = ProgressReporter(
            bot, chat_id=1, title="تست",
            steps=[Step("گام یک", 10.0), Step("گام دو", 10.0)],
            heartbeat=100.0, min_edit_gap=0.0,
        )
        await rep.start()
        assert bot.edited == [] and len(bot.sent) == 1
        # از یک «رشته‌ی worker» push می‌کنیم (مثل engine)
        def worker():
            rep.push({"op": "begin", "i": 0})
            time.sleep(0.05)
            rep.push({"op": "finish", "i": 0, "ok": True})
            rep.push({"op": "begin", "i": 1})
            time.sleep(0.05)
            rep.push({"op": "finish", "i": 1, "ok": True})
            rep.push({"op": "complete"})
        t = threading.Thread(target=worker)
        t.start()
        # صبر تا فاینال
        for _ in range(200):
            if bot.edited:
                break
            await asyncio.sleep(0.02)
        t.join()
        await asyncio.sleep(0.1)
        texts = [e["text"] for e in bot.edited]
        joined = "\n".join(texts)
        assert "گام یک" in texts[0] or "گام یک" in joined
        final = bot.edited[-1]["text"]
        assert "✅" in final
        assert "کامل شد" in final
        assert rep.is_aborted is False
        return bot

    asyncio.run(scenario())


def test_progress_step_timeout_aborts():
    async def scenario():
        bot = FakeBot()
        rep = ProgressReporter(
            bot, chat_id=1, title="تست timeout",
            steps=[Step("گام کند", 0.2)],
            heartbeat=100.0, min_edit_gap=0.0,
        )
        await rep.start()
        rep.push({"op": "begin", "i": 0})
        # گام هرگز تمام نمی‌شود ⇒ ددلاین ۰.۲ ثانیه باید abort کند
        await asyncio.sleep(0.8)
        assert rep.is_aborted is True
        final = bot.edited[-1]["text"]
        assert "⏰" in final or "مهلت" in final
        return bot

    asyncio.run(scenario())


def test_progress_step_failure_finalizes():
    async def scenario():
        bot = FakeBot()
        rep = ProgressReporter(
            bot, chat_id=1, title="تست fail",
            steps=[Step("گام", 10.0)],
            heartbeat=100.0, min_edit_gap=0.0,
        )
        await rep.start()
        rep.push({"op": "begin", "i": 0})
        rep.push({"op": "finish", "i": 0, "ok": False, "error": "ClientError: nope"})
        await asyncio.sleep(0.2)
        final = bot.edited[-1]["text"]
        assert "❌" in final
        assert "nope" in final
        assert rep.is_aborted is True
        return bot

    asyncio.run(scenario())


def test_heartbeat_updates_elapsed():
    async def scenario():
        bot = FakeBot()
        rep = ProgressReporter(
            bot, chat_id=1, title="heartbeat",
            steps=[Step("کند", 60.0)],
            heartbeat=0.15, min_edit_gap=0.0,
        )
        await rep.start()
        rep.push({"op": "begin", "i": 0})
        await asyncio.sleep(0.7)
        # باید چند بار elapsed به‌روز شده باشد (⏳ ...s / 60s)
        progress_edits = [
            e for e in bot.edited if "⏳" in (e.get("text") or "")
        ]
        assert len(progress_edits) >= 2, f"edits={bot.edited}"
        rep.push({"op": "finish", "i": 0, "ok": True})
        await asyncio.sleep(0.2)
        return bot

    asyncio.run(scenario())
