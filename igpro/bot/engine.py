"""موتور گزارش‌های اینستاگرام (page / hashtag) با گام‌های پویا.

نکات کلیدی:
  - کلِ هر گزارش در یک رشته‌ی worker اجرا می‌شود (کال‌های instagrapi همگام‌اند)
    و فقط **یک** گزارش در هر لحظه (قفل سراسری) — چون یک سشنِ اینستاگرام مشترک
    داریم و درخواست‌های هم‌زمان همان 429/چالش قبلی را برمی‌گرداند.
  - هر گام مهلتِ خودش را دارد (ProgressReporter)؛ engine بین گام‌ها is_aborted
    را چک می‌کند و در صورت سررسید، بلافاصله متوقف می‌شود.
  - همه‌ی کال‌های API از safe_call می‌روند (بازتلاش + ورود مجدد خودکار).
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable, Dict, List, Optional

from instagrapi.exceptions import UserNotFound

from igclient.client import IGClient
from igclient import profile as prof
from igclient.profile import media_stats
from bot.config import BotSettings
from bot.progress import ProgressReporter, Step
from bot.viral import viral_score

logger = logging.getLogger("igpro.bot.engine")

# مهلت‌ها (ثانیه) — بر اساس زمان‌های واقعیِ مشاهده‌شده (هر درخواستِ خصوصی ~۲۰ ثانیه)
DL_SESSION = 300.0   # ممکن است لاگین کامل + کد ۲FA/چالش شامل شود
DL_ID = 60.0         # دریافت شناسه / اطلاعات پیج
DL_MEDIA = 120.0     # گرفتن لیست پست‌ها
DL_DETAIL = 45.0     # جزئیاتِ تک‌پست (یک media_info)
DL_OWNER = 30.0      # یک user_info برای صاحبِ پست
DL_INSTANT = 10.0    # محاسبات


class ReportError(RuntimeError):
    """خطای کسب‌وکارِ گزارش (پیج/هشتگ پیدا نشد و...)."""


class ReportAborted(RuntimeError):
    """درخواست به‌دلیل سررسید/لغو متوقف شد."""


def _check_abort(reporter: ProgressReporter) -> None:
    if reporter.is_aborted:
        raise ReportAborted()


class IgEngine:
    def __init__(self, ig: IGClient, bot_settings: BotSettings):
        self.ig = ig
        self.cfg = bot_settings
        self._lock = asyncio.Lock()
        self._busy_owner: Optional[int] = None

    # ---------------------------------------------------------- قفل سراسری
    @property
    def locked(self) -> bool:
        return self._lock.locked()

    @property
    def busy_owner(self) -> Optional[int]:
        return self._busy_owner

    async def acquire(self, user_id: int) -> bool:
        if self._lock.locked():
            return False
        await self._lock.acquire()
        self._busy_owner = user_id
        return True

    def release(self) -> None:
        self._busy_owner = None
        if self._lock.locked():
            self._lock.release()

    # ------------------------------------------------------------ ابزار گام‌ها
    def _span(self, reporter: ProgressReporter, index: int, fn: Callable[[], Any]) -> Any:
        """اجرای یک گام (با هر محتوایی) و گزارشِ begin/finish به پیامِ پویا.

        ⚠️ `push(finish)` باید OUTSIDE_try باشد؛ `return` در try باعث می‌شود else اجرا نشود.
        """
        _check_abort(reporter)
        reporter.push({"op": "begin", "i": index})
        try:
            result = fn()
        except ReportAborted:
            reporter.push({"op": "finish", "i": index, "ok": False, "error": "لغوشده"})
            raise
        except Exception as exc:  # noqa: BLE001 — به reporter گزارش می‌شود
            logger.debug("گام %d خطا داد: %s", index, exc, exc_info=True)
            reporter.push(
                {"op": "finish", "i": index, "ok": False,
                 "error": f"{type(exc).__name__}: {str(exc)[:120]}"}
            )
            raise
        reporter.push({"op": "finish", "i": index, "ok": True})
        return result

    def _run_step(
        self,
        reporter: ProgressReporter,
        index: int,
        fn: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """یک گام = یک کالِ API از safe_call؛ حالت‌ها را به reporter می‌فرستد."""
        return self._span(
            reporter, index, lambda: self.ig.safe_call(fn, *args, **kwargs)
        )

    def _ensure_session(self, reporter: ProgressReporter, index: int) -> None:
        def _login() -> None:
            if self.ig.login_method is None:
                self.ig.login()
        self._run_step(reporter, index, _login)

    @staticmethod
    def _merge_posts(medias: List[Any], clips: List[Any], amount: int) -> List[Any]:
        """ادغام پست+ریل، حذف تکراری، مرتب‌سازی از جدید به قدیم (بدون درخواست جدید)."""
        seen: set = set()
        unique: List[Any] = []
        for media in list(medias or []) + list(clips or []):
            pk = str(prof._safe(media, "pk", "id", default="") or "")
            if pk and pk not in seen:
                seen.add(pk)
                unique.append(media)
        unique.sort(key=lambda m: str(prof._safe(m, "taken_at", default="") or ""), reverse=True)
        return unique[: max(1, amount)]

    # ---------------------------------------------------------------- گزارش پیج
    def page_report_steps(self) -> List[Step]:
        return [
            Step("بررسی/بازسازی سشن اینستاگرام", DL_SESSION),
            Step("دریافت شناسه‌ی پیج", DL_ID),
            Step("دریافت اطلاعات پیج", DL_ID),
            Step("دریافت ۲ پستِ آخر", DL_MEDIA),
            Step("دریافت ۲ ریلِ آخر", DL_MEDIA),
            Step("جزئیات دقیق پست‌ها (لایک/کامنت/بازدید)", DL_DETAIL * 2),
            Step("محاسبه‌ی Viral Score", DL_INSTANT),
        ]

    def page_report(self, username: str, reporter: ProgressReporter) -> Dict[str, Any]:
        """در رشته‌ی worker اجرا می‌شود. نتیجه: دیکشنریِ کامل گزارش."""
        cl = self.ig.client
        username = username.strip().lstrip("@").lower()
        self._ensure_session(reporter, 0)

        try:
            user_id = str(
                self._run_step(reporter, 1, self.ig.safe_call, cl.user_id_from_username, username)
            )
        except UserNotFound as exc:
            raise ReportError(
                f"پیج @{username} پیدا نشد (نام کاربری اشتباه یا پیج حذف‌شده است)."
            ) from exc

        user_raw = self._run_step(reporter, 2, self.ig.safe_call, cl.user_info, user_id)
        profile_data = prof.normalize_user(user_raw)

        medias = self._run_step(reporter, 3, self.ig.safe_call, cl.user_medias, user_id, 2)
        clips: List[Any] = []
        clips_note = ""
        reporter.push({"op": "begin", "i": 4})
        try:
            clips = list(self.ig.safe_call(cl.user_clips, user_id, 2) or [])
            reporter.push({"op": "finish", "i": 4, "ok": True})
        except Exception as exc:  # noqa: BLE001 — ریل ممکن است نباشد
            clips_note = f"دریافت ریل‌ها ناموفق بود ({type(exc).__name__})"
            reporter.push({"op": "finish", "i": 4, "ok": True, "note": clips_note})
            logger.debug("clips: %s", exc)

        posts = self._merge_posts(list(medias or []), clips, amount=2)

        # جزئیات دقیق هر پست (بازدید ویدیو/ریل در فید گاهی خالی است)
        def _details() -> List[Any]:
            detailed: List[Any] = []
            for media in posts:
                _check_abort(reporter)
                pk = str(prof._safe(media, "pk", "id", default="") or "")
                try:
                    rich = self.ig.safe_call(cl.media_info, pk)
                except Exception as exc:  # noqa: BLE001 — داده‌ی فید جایگزین می‌شود
                    logger.debug("media_info(%s) ناموفق: %s", pk, exc)
                    rich = media
                detailed.append(rich)
            return detailed

        detailed = self._span(reporter, 5, _details)

        followers = int(profile_data.get("followers") or 0)

        def _scores() -> List[Dict[str, Any]]:
            results = []
            for media in detailed:
                _check_abort(reporter)
                stats = media_stats(media)
                stats["viral"] = viral_score(
                    stats.get("views"), stats.get("likes"),
                    stats.get("comments"), followers,
                )
                results.append(stats)
            return results

        results = self._span(reporter, 6, _scores)
        total_likes = sum(p["likes"] or 0 for p in results)
        total_comments = sum(p["comments"] or 0 for p in results)
        total_views = sum(p["views"] or 0 for p in results)
        top = max(results, key=lambda p: (p["viral"] is not None, p["viral"] or 0), default=None)

        return {
            "kind": "page",
            "username": profile_data.get("username") or username,
            "user_id": str(profile_data.get("user_id") or user_id),
            "profile": profile_data,
            "posts": results,
            "clips_note": clips_note,
            "total_likes": total_likes,
            "total_comments": total_comments,
            "total_views": total_views,
            "top_post_index": (results.index(top) + 1) if (top and results) else None,
            "top_viral": (top or {}).get("viral"),
            "done_at": time.time(),
        }

    # -------------------------------------------------------------- گزارش هشتگ
    def hashtag_report_steps(self, tag: str) -> List[Step]:
        show = self.cfg.hashtag_show
        return [
            Step("بررسی/بازسازی سشن اینستاگرام", DL_SESSION),
            Step(f"دریافت اطلاعات # {tag}", DL_ID),
            Step(f"دریافت {self.cfg.hashtag_scan} پستِ برتر", DL_MEDIA),
            Step(f"شناسایی صاحبان {show} پست (برای Viral Score)", DL_OWNER * show),
            Step(f"جزئیات دقیق {show} پست + Viral Score", DL_DETAIL * show),
        ]

    def hashtag_report(self, tag: str, reporter: ProgressReporter) -> Dict[str, Any]:
        """در رشته‌ی worker اجرا می‌شود."""
        cl = self.ig.client
        show = self.cfg.hashtag_show
        scan = self.cfg.hashtag_scan
        self._ensure_session(reporter, 0)

        info = self._run_step(reporter, 1, self.ig.safe_call, cl.hashtag_info, tag)
        media_count = int(prof._safe(info, "media_count", default=0) or 0)
        tag_name = str(prof._safe(info, "name", default=tag) or tag)

        medias = self._run_step(reporter, 2, self.ig.safe_call, cl.hashtag_medias_top, tag, scan)
        medias = list(medias or [])
        if not medias:
            raise ReportError(
                f"هیچ پستی برای # {tag_name} پیدا نشد (هشتگ جدید/کم‌بازدید یا موقتاً در دسترس نیست)."
            )
        # حذف تکراری
        seen: set = set()
        unique: List[Any] = []
        for media in medias:
            pk = str(prof._safe(media, "pk", "id", default="") or "")
            if pk and pk not in seen:
                seen.add(pk)
                unique.append(media)
        chosen = unique[:show]

        # شناسایی صاحبان (برای فالوورهای پیج ⇒ Viral Score)
        def _owners() -> List[Dict[str, Any]]:
            owners: List[Dict[str, Any]] = []
            for media in chosen:
                _check_abort(reporter)
                owner_raw = prof._safe(media, "user", default=None)
                owner_pk = str(prof._safe(owner_raw, "pk", "user_id", "id", default="") or "")
                owner_user = prof._safe(owner_raw, "username", default="") or ""
                followers = prof._safe(owner_raw, "follower_count", default=None)
                full = None
                if owner_pk:
                    try:
                        full = self.ig.safe_call(cl.user_info, str(owner_pk))
                    except Exception as exc:  # noqa: BLE001 — فالوور نامشخص ⇒ ویروسی «—»
                        logger.debug("user_info(%s) ناموفق: %s", owner_pk, exc)
                    if full is not None:
                        followers = prof._safe(full, "follower_count", default=followers)
                owners.append(
                    {
                        "user_id": owner_pk,
                        "username": owner_user or (
                            str(prof._safe(full, "username", default="") or "") if full else ""
                        ),
                        "followers": int(followers) if followers is not None else None,
                    }
                )
            return owners

        owners = self._span(reporter, 3, _owners)

        # جزئیات دقیق (بازدید ویدیو/ریل) + Viral Score
        def _details_and_scores() -> List[Dict[str, Any]]:
            results = []
            for index, media in enumerate(chosen, start=1):
                _check_abort(reporter)
                pk = str(prof._safe(media, "pk", "id", default="") or "")
                try:
                    rich = self.ig.safe_call(cl.media_info, pk)
                except Exception as exc:  # noqa: BLE001 — داده‌ی فید
                    logger.debug("media_info(%s) ناموفق: %s", pk, exc)
                    rich = media
                owner = owners[index - 1]
                stats = media_stats(rich)
                stats["owner"] = owner
                stats["viral"] = viral_score(
                    stats.get("views"), stats.get("likes"),
                    stats.get("comments"), owner.get("followers"),
                )
                results.append(stats)
            return results

        results = self._span(reporter, 4, _details_and_scores)
        total_likes = sum(p["likes"] or 0 for p in results)
        total_comments = sum(p["comments"] or 0 for p in results)
        total_views = sum(p["views"] or 0 for p in results)
        top = max(results, key=lambda p: (p["viral"] is not None, p["viral"] or 0), default=None)

        return {
            "kind": "hashtag",
            "tag": tag_name,
            "media_count": media_count,
            "posts": results,
            "total_likes": total_likes,
            "total_comments": total_comments,
            "total_views": total_views,
            "top_post_index": (results.index(top) + 1) if (top and results) else None,
            "top_viral": (top or {}).get("viral"),
            "done_at": time.time(),
        }
