import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.config import BotSettings  # noqa: E402
from bot.engine import IgEngine, ReportAborted, ReportError  # noqa: E402
from bot.progress import ProgressReporter  # noqa: E402
from tests.fakes import FakeIG, FakeBot, make_hashtag_fixture, make_page_fixture  # noqa: E402


def _settings(tmp) -> BotSettings:
    return BotSettings(
        token="x", admin_id=None, state_dir=Path(tmp),
        history_limit=10, hashtag_show=3, hashtag_scan=9,
    )


def test_page_report_full_flow(tmp_path):
    async def scenario():
        bot = FakeBot()
        fake = make_page_fixture()
        ig = FakeIG(fake)
        engine = IgEngine(ig, _settings(tmp_path))
        rep = ProgressReporter(bot, 1, "تست", engine.page_report_steps(),
                               heartbeat=100.0, min_edit_gap=0.0)
        await rep.start()
        result = await asyncio.to_thread(engine.page_report, "nima.arish", rep)
        rep.push({"op": "complete"})
        await asyncio.sleep(0.05)

        assert result["kind"] == "page"
        assert result["username"] == "nima.arish"
        assert result["profile"]["followers"] == 1000
        # ۲ پست: جدیدترین‌ها از اجتماعِ medias+clips
        assert len(result["posts"]) == 2
        first = result["posts"][0]
        # جدیدترین c1 (2026-09-02) باید اول باشد
        assert first["shortcode"] == "CODEC1"
        # viral score پست اول: (20000 + 500*2 + 80*5) / 1000 = 21400/1000 = 21.4
        assert abs(first["viral"] - 21.4) < 1e-9
        assert result["total_views"] == 20000 + 10000
        assert result["top_post_index"] == 1
        # همه‌ی گام‌ها سبز شده‌اند
        final = bot.edited[-1]["text"]
        assert "کامل شد" in final
        n_steps = len(engine.page_report_steps())
        assert final.count("✅") >= n_steps, f"steps={n_steps} text={final}"
        assert "⬜" not in final and "❌" not in final and "⏰" not in final
        assert rep.is_aborted is False
        return result

    asyncio.run(scenario())


def test_page_report_user_not_found(tmp_path):
    async def scenario():
        bot = FakeBot()
        ig = FakeIG(make_page_fixture())
        engine = IgEngine(ig, _settings(tmp_path))
        rep = ProgressReporter(bot, 1, "تست", engine.page_report_steps(),
                               heartbeat=100.0, min_edit_gap=0.0)
        await rep.start()
        with __import__("pytest").raises(ReportError):
            await asyncio.to_thread(engine.page_report, "ghost_user", rep)
        assert rep.is_aborted is True
        final = bot.edited[-1]["text"]
        assert "پیدا نشد" in final or "❌" in final

    asyncio.run(scenario())


def test_hashtag_report_full_flow(tmp_path):
    async def scenario():
        bot = FakeBot()
        ig = FakeIG(make_hashtag_fixture())
        engine = IgEngine(ig, _settings(tmp_path))
        rep = ProgressReporter(bot, 1, "تست", engine.hashtag_report_steps("مدلینگ"),
                               heartbeat=100.0, min_edit_gap=0.0)
        await rep.start()
        result = await asyncio.to_thread(engine.hashtag_report, "مدلینگ", rep)
        rep.push({"op": "complete"})
        await asyncio.sleep(0.05)

        assert result["kind"] == "hashtag"
        assert result["tag"] == "مدلینگ"
        assert result["media_count"] == 1234567
        assert len(result["posts"]) == 3
        # پست h1: (40000 + 500*2 + 100*5) / 100 = 41500/100 = 415.0
        assert abs(result["posts"][0]["viral"] - 415.0) < 1e-9
        # owner درج شده
        assert result["posts"][0]["owner"]["username"] == "owner1"
        assert result["posts"][0]["owner"]["followers"] == 100
        assert result["total_views"] == 40000 + 30000 + 9000
        # همه‌ی گام‌ها سبز
        final = bot.edited[-1]["text"]
        n_steps = len(engine.hashtag_report_steps("مدلینگ"))
        assert final.count("✅") >= n_steps, f"steps={n_steps} text={final}"
        assert "⬜" not in final and "❌" not in final and "⏰" not in final
        return result

    asyncio.run(scenario())


def test_hashtag_report_unknown_tag(tmp_path):
    async def scenario():
        bot = FakeBot()
        ig = FakeIG(make_page_fixture())  # هیچ هشتگی ندارد
        engine = IgEngine(ig, _settings(tmp_path))
        rep = ProgressReporter(bot, 1, "تست", engine.hashtag_report_steps("xyz"),
                               heartbeat=100.0, min_edit_gap=0.0)
        await rep.start()
        try:
            await asyncio.to_thread(engine.hashtag_report, "xyz", rep)
            raised = None
        except ReportError as exc:
            raised = exc
        except Exception as exc:  # noqa: BLE001
            raised = exc
        assert raised is not None
        assert rep.is_aborted is True
        return raised

    asyncio.run(scenario())


def test_engine_abort_between_steps(tmp_path):
    async def scenario():
        bot = FakeBot()
        fake = make_page_fixture()
        fake.sleep_sec = 0.3  # هر کال ~۰.۳ ثانیه
        ig = FakeIG(fake)
        engine = IgEngine(ig, _settings(tmp_path))
        # ددلاینِ گامِ ۲ را ۰.۱ ثانیه می‌کنیم تا abort رخ دهد
        steps = engine.page_report_steps()
        from bot.progress import Step
        steps[1] = Step("دریافت شناسه‌ی پیج (کوتاه)", 0.1)
        rep = ProgressReporter(bot, 1, "تست", steps, heartbeat=100.0, min_edit_gap=0.0)
        await rep.start()
        t0 = time.monotonic()
        with __import__("pytest").raises(ReportAborted):
            await asyncio.to_thread(engine.page_report, "nima.arish", rep)
        dt = time.monotonic() - t0
        # باید سریع متوقف شود (نه صبرِ کامل روی گام‌های بعدی)
        assert dt < 2.0, f"took {dt:.1f}s"
        assert rep.is_aborted is True

    asyncio.run(scenario())


def test_engine_global_lock(tmp_path):
    async def scenario():
        ig = FakeIG(make_page_fixture())
        engine = IgEngine(ig, _settings(tmp_path))
        assert await engine.acquire(111)
        assert engine.locked
        assert not await engine.acquire(222)  # شلوغ
        assert engine.busy_owner == 111
        engine.release()
        assert not engine.locked
        assert await engine.acquire(222)
        engine.release()

    asyncio.run(scenario())
