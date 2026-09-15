"""هندلرهای ربات: دستورات، FSM ورودِ نام، دسترسی‌ها، هیزتوری و گزارش‌ها.

معماری:
  - قفل سراسری: فقط یک گزارش اینستاگرامی در هر لحظه (سشنِ مشترک ⇒ بدون 429).
  - اگر درخواستِ خودِ آن کاربر در حال اجرا باشد → «درخواست قبلی شما...»
  - وگرنه → «ربات مشغول است».
  - وابستگی‌ها (settings/db/engine) از طریق closure می‌آیند.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Dict, Optional, Tuple

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from bot import formatter as fmt
from bot.config import BotSettings
from bot.database import Database
from bot.engine import IgEngine, ReportAborted, ReportError
from bot.progress import ProgressReporter
from igclient.client import RateLimited

logger = logging.getLogger("igpro.bot.handlers")

USERNAME_RE = re.compile(r"^[A-Za-z0-9._]{1,30}$")
DURATION_RE = re.compile(r"^(\d+)\s*([hHdDwW])$")
DURATION_UNITS = {"h": 1, "d": 24, "w": 168}
OVERALL_DEADLINE = 900.0  # ۱۵ دقیقه برای کلِ هر گزارش


class PageFlow(StatesGroup):
    waiting_username = State()


class TagFlow(StatesGroup):
    waiting_tag = State()


class HasState:
    """فیلتر: فقط وقتی کاربر در یک state فعال باشد (aiogram 3.31: raw_state از kwargs می‌آید)."""

    async def __call__(self, obj, raw_state: Optional[str] = None) -> bool:
        return raw_state is not None


def _parse_duration(text: str) -> Tuple[Optional[float], Optional[str]]:
    """'24h'→24 ساعت، '7d'→168 ساعت، 'forever'→None (دائمی)."""
    t = (text or "").strip().lower()
    if t in ("forever", "f", "∞", "daimi"):
        return None, None
    m = DURATION_RE.match(t)
    if m:
        return float(int(m.group(1)) * DURATION_UNITS[m.group(2).lower()]), None
    return None, "فرمت مدت نامعتبر است (مثال: 24h یا 7d یا forever)"


def build_router(settings: BotSettings, db: Database, engine: IgEngine) -> Router:
    router = Router(name="igpro-bot")
    last_targets: Dict[int, Tuple[str, str]] = {}

    # ------------------------------------------------------------- ابزارها
    async def _gate(msg: Message) -> Tuple[Dict, bool, bool]:
        """ثبت/به‌روزرسانی کاربر + بررسی دسترسی. بازگشت: (ردیف، مجاز؟، اولین ثبت؟)"""
        user = msg.from_user
        assert user is not None
        first = db.get_user(user.id) is None
        db.ensure_user(user.id, user.username, user.first_name,
                       forced_admin_id=settings.admin_id)
        return db.get_user(user.id), db.is_authorized(user.id), first

    async def _gate_cq(cq: CallbackQuery) -> Tuple[Dict, bool, bool]:
        user = cq.from_user
        assert user is not None
        first = db.get_user(user.id) is None
        db.ensure_user(user.id, user.username, user.first_name,
                       forced_admin_id=settings.admin_id)
        return db.get_user(user.id), db.is_authorized(user.id), first

    async def _deny_message(msg: Message) -> None:
        await msg.answer(fmt.denied_text(), reply_markup=fmt.request_access_keyboard())

    async def _edit_denied(cq: CallbackQuery) -> None:
        if cq.message is None:
            return
        try:
            await cq.message.edit_text(
                fmt.denied_text(), reply_markup=fmt.request_access_keyboard()
            )
        except TelegramAPIError:
            pass

    # ---------------------------------------------------- اجرای گزارش‌ها
    async def _run_report(msg: Message, kind: str, target: str, user_id: int) -> None:
        if engine.locked:
            is_own = engine.busy_owner == user_id
            await msg.answer(fmt.busy_text(is_own))
            return
        await engine.acquire(user_id)

        if kind == "page":
            steps = engine.page_report_steps()
            title = f"🔎 تحلیل پیج: @{target}"
        else:
            steps = engine.hashtag_report_steps(target)
            title = f"🔎 تحلیل هشتگ: #{target}"

        bot = msg.bot
        reporter = ProgressReporter(
            bot, msg.chat.id, title, steps, overall_deadline=OVERALL_DEADLINE
        )
        try:
            await reporter.start()
        except TelegramAPIError as exc:
            logger.error("ارسال پیام گام‌به‌گام ناموفق: %s", exc)
            engine.release()
            return

        meta: Dict = {"kind": kind, "target": target}
        try:
            if kind == "page":
                result = await asyncio.to_thread(engine.page_report, target, reporter)
            else:
                result = await asyncio.to_thread(engine.hashtag_report, target, reporter)
        except ReportAborted:
            logger.warning("گزارش %s:%s لغو/سررسید شد", kind, target)
            db.add_history(user_id, kind, target, "aborted", None, meta)
            await msg.answer(
                "⏰ گزارش به‌دلیل پایانِ مهلت متوقف شد. دوباره امتحان کنید.",
                reply_markup=fmt.retry_menu_keyboard(),
            )
        except RateLimited:
            logger.warning("گزارش %s:%s ریت‌لیمیت شد", kind, target)
            db.add_history(user_id, kind, target, "ratelimited", None, meta)
            await msg.answer(
                "⏳ <b>اینستاگرام هم‌اکنون این اکانت را ریت‌لیمیت کرده است</b> "
                "(Please wait a few minutes).\n\n"
                "این یک محدودیتِ سمتِ سرور است، نه خطای برنامه.\n"
                "• حدود ۵ تا ۱۵ دقیقه صبر کنید و دوباره امتحان کنید.\n"
                "• اگر همین اکانت روی دستگاه دیگری هم اجرا می‌شود، آن را ببندید — "
                "اجرای هم‌زمان یک اکانت از دو IP مهم‌ترین عامل ریت‌لیمیت است.\n"
                "• حجمِ استفاده را کم نگه دارید (چندین گزارش در ساعت کافی است)."
            )
        except ReportError as exc:
            db.add_history(user_id, kind, target, "error", None, {**meta, "error": str(exc)})
            await msg.answer(
                fmt.error_text("درخواست کامل نشد", str(exc)),
                reply_markup=fmt.retry_menu_keyboard(),
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("خطای غیرمنتظره در گزارش %s:%s", kind, target)
            reporter.push({"op": "fail", "error": f"{type(exc).__name__}: {str(exc)[:160]}"})
            db.add_history(user_id, kind, target, "error", None, {**meta, "error": str(exc)})
            await msg.answer(
                fmt.error_text("خطای غیرمنتظره", f"{type(exc).__name__}: {exc}"),
                reply_markup=fmt.retry_menu_keyboard(),
            )
        else:
            reporter.push({"op": "complete"})
            text = (
                fmt.page_report_html(result)
                if kind == "page"
                else fmt.hashtag_report_html(result)
            )
            meta.update(
                {
                    "posts": len(result.get("posts") or []),
                    "top_viral": result.get("top_viral"),
                    "username": result.get("username") or result.get("tag"),
                }
            )
            db.add_history(user_id, kind, target, "ok", text, meta)
            last_targets[user_id] = (kind, target)
            await msg.answer(text, reply_markup=fmt.result_keyboard())
        finally:
            engine.release()

    # ------------------------------------------------------------- /start
    @router.message(CommandStart())
    async def cmd_start(msg: Message) -> None:
        row, ok, first = await _gate(msg)
        if ok:
            text = fmt.start_text(bool(row["is_admin"]), first and bool(row["is_admin"]))
            await msg.answer(text, reply_markup=fmt.menu_keyboard())
        else:
            await _deny_message(msg)

    # --------------------------------------------------------------- /help
    @router.message(Command("help"))
    async def cmd_help(msg: Message) -> None:
        row, ok, _ = await _gate(msg)
        if not ok:
            await _deny_message(msg)
            return
        await msg.answer(fmt.help_text(), reply_markup=fmt.menu_keyboard())

    # --------------------------------------------------------------- /menu
    @router.message(Command("menu"))
    async def cmd_menu(msg: Message) -> None:
        row, ok, _ = await _gate(msg)
        if not ok:
            await _deny_message(msg)
            return
        await msg.answer(fmt.start_text(bool(row["is_admin"]), False),
                         reply_markup=fmt.menu_keyboard())

    # -------------------------------------------------------------- /page
    async def _start_page(msg: Message, target: str) -> None:
        target = (target or "").strip().lstrip("@")
        if not USERNAME_RE.match(target):
            await msg.answer(fmt.invalid_username_text())
            return
        await _run_report(msg, "page", target.lower(), msg.from_user.id)  # type: ignore[union-attr]

    @router.message(Command("page"))
    async def cmd_page(msg: Message, state: FSMContext) -> None:
        row, ok, _ = await _gate(msg)
        if not ok:
            await _deny_message(msg)
            return
        args = (msg.text or "").split(maxsplit=1)
        if len(args) < 2 or not args[1].strip():
            await msg.answer(fmt.page_prompt_text())
            await state.set_state(PageFlow.waiting_username)
            return
        await _start_page(msg, args[1].strip())

    # --------------------------------------------------------------- /tag
    def _normalize_tag(raw: str) -> Optional[str]:
        tag = (raw or "").strip().lstrip("#").strip()
        if not tag or len(tag) > 100:
            return None
        return tag

    @router.message(Command("tag"))
    async def cmd_tag(msg: Message, state: FSMContext) -> None:
        row, ok, _ = await _gate(msg)
        if not ok:
            await _deny_message(msg)
            return
        args = (msg.text or "").split(maxsplit=1)
        tag = _normalize_tag(args[1] if len(args) > 1 else "")
        if not tag:
            await msg.answer(fmt.tag_prompt_text())
            await state.set_state(TagFlow.waiting_tag)
            return
        await _run_report(msg, "hashtag", tag, msg.from_user.id)  # type: ignore[union-attr]

    # ------------------------------------------------------------ /history
    def _history_payload(uid: int, page: int) -> Tuple[str, object, int]:
        limit = settings.history_limit
        page = max(0, page)
        rows = db.list_history(uid, offset=page * limit, limit=limit)
        total = db.count_history(uid)
        if not rows and page != 0:
            page = 0
            rows = db.list_history(uid, offset=0, limit=limit)
        has_prev = page > 0
        has_next = (page + 1) * limit < total
        text = fmt.history_page_html(rows, page, total)
        keyboard = (
            fmt.menu_keyboard()
            if not rows
            else fmt.history_keyboard(rows, page, has_prev, has_next)
        )
        return text, keyboard, total

    @router.message(Command("history"))
    async def cmd_history(msg: Message) -> None:
        row, ok, _ = await _gate(msg)
        if not ok:
            await _deny_message(msg)
            return
        text, keyboard, _ = _history_payload(msg.from_user.id, 0)  # type: ignore[union-attr]
        await msg.answer(text, reply_markup=keyboard)

    # ------------------------------------------------------------ /cancel
    @router.message(Command("cancel"), HasState())
    async def cmd_cancel(msg: Message, state: FSMContext) -> None:
        await state.clear()
        await msg.answer(fmt.cancelled_text(), reply_markup=fmt.menu_keyboard())

    # ------------------------------------------------- FSM: نام کاربری پیج
    @router.message(StateFilter(PageFlow.waiting_username), F.text)
    async def page_flow_username(msg: Message, state: FSMContext) -> None:
        text = (msg.text or "").strip()
        if text.startswith("/"):
            await state.clear()
            await msg.answer("⚠️ در این مرحله نام کاربری (بدون /) بفرستید، یا /cancel برای لغو.")
            return
        await state.clear()
        await _start_page(msg, text)

    # --------------------------------------------------- FSM: هشتگ
    @router.message(StateFilter(TagFlow.waiting_tag), F.text)
    async def tag_flow_tag(msg: Message, state: FSMContext) -> None:
        text = (msg.text or "").strip()
        if text.startswith("/"):
            await state.clear()
            await msg.answer("⚠️ در این مرحله هشتگ بفرستید، یا /cancel برای لغو.")
            return
        await state.clear()
        tag = _normalize_tag(text)
        if not tag:
            await msg.answer(fmt.invalid_tag_text())
            return
        await _run_report(msg, "hashtag", tag, msg.from_user.id)  # type: ignore[union-attr]

    # --------------------------------------------------- دستورات مدیر (قبل از fallback)
    @router.message(Command("users"))
    async def cmd_users(msg: Message) -> None:
        row, ok, _ = await _gate(msg)
        if not ok or not row["is_admin"]:
            await msg.answer(fmt.not_authorized_admin_text())
            return
        await msg.answer(fmt.users_list_html(db.list_users()))

    @router.message(Command("grant"))
    async def cmd_grant(msg: Message) -> None:
        row, ok, _ = await _gate(msg)
        if not ok or not row["is_admin"]:
            await msg.answer(fmt.not_authorized_admin_text())
            return
        args = (msg.text or "").split()
        if len(args) < 3:
            await msg.answer("فرمت: /grant <id> <24h|7d|forever>")
            return
        try:
            target_id = int(args[1])
        except ValueError:
            await msg.answer("آیدی باید عددی باشد.")
            return
        hours, err = _parse_duration(args[2])
        if err:
            await msg.answer(err)
            return
        target = db.get_user(target_id)
        if not target:
            await msg.answer("کاربر در دیتابیس نیست (هنوز /start نزده).")
            return
        until = db.grant_access(target_id, hours)
        db.resolve_access_request(target_id, "granted")
        try:
            await msg.bot.send_message(  # type: ignore[union-attr]
                chat_id=target_id,
                text=fmt.access_granted_text(fmt.duration_label(hours), until),
                reply_markup=fmt.menu_keyboard(),
            )
        except TelegramAPIError as exc:
            logger.warning("اطلاع‌رسانی grant ناموفق: %s", exc)
        await msg.answer(
            fmt.admin_action_text("دسترسی داده شد", target,
                                  f" — مدت: {fmt.duration_label(hours)}")
        )

    @router.message(Command("revoke"))
    async def cmd_revoke(msg: Message) -> None:
        row, ok, _ = await _gate(msg)
        if not ok or not row["is_admin"]:
            await msg.answer(fmt.not_authorized_admin_text())
            return
        args = (msg.text or "").split()
        if len(args) < 2:
            await msg.answer("فرمت: /revoke <id>")
            return
        try:
            target_id = int(args[1])
        except ValueError:
            await msg.answer("آیدی باید عددی باشد.")
            return
        target = db.get_user(target_id)
        if not target:
            await msg.answer("کاربر در دیتابیس نیست.")
            return
        db.revoke_access(target_id)
        db.resolve_access_request(target_id, "revoked")
        try:
            await msg.bot.send_message(  # type: ignore[union-attr]
                chat_id=target_id, text="⛔ دسترسی شما توسط مدیر لغو شد."
            )
        except TelegramAPIError as exc:
            logger.warning("اطلاع‌رسانی revoke ناموفق: %s", exc)
        await msg.answer(fmt.admin_action_text("دسترسی لغو شد", target))

    # ---------------------------------------------------------- متن عمومی
    @router.message(F.text)
    async def text_fallback(msg: Message) -> None:
        row, ok, _ = await _gate(msg)
        if not ok:
            await _deny_message(msg)
            return
        if (msg.text or "").startswith("/"):
            await msg.answer(
                "⚠️ دستور ناشناس. دستورات: /page /tag /history /menu /help /cancel"
                + (" /users /grant /revoke" if row["is_admin"] else ""),
                reply_markup=fmt.menu_keyboard(),
            )
        else:
            await msg.answer("👇 برای شروع از منو استفاده کنید (یا /page و /tag):",
                             reply_markup=fmt.menu_keyboard())

    # -------------------------------------------------- محتوای غیرمتنی (عکس و ...)
    @router.message()
    async def other_content_fallback(msg: Message) -> None:
        row, ok, _ = await _gate(msg)
        if not ok:
            await _deny_message(msg)
            return
        await msg.answer("😅 فقط پیام <b>متنی</b> بفرستید (نام کاربری یا هشتگ)، "
                         "یا از منو استفاده کنید:", reply_markup=fmt.menu_keyboard())

    # ---------------------------------------------------------- کال‌بک‌ها
    @router.callback_query(F.data)
    async def on_callback(cq: CallbackQuery, state: FSMContext) -> None:
        payload = cq.data or ""
        try:
            if payload.startswith("m:page"):
                row, ok, _ = await _gate_cq(cq)
                if not ok:
                    await cq.answer("دسترسی ندارید", show_alert=False)
                    await _edit_denied(cq)
                    return
                await state.set_state(PageFlow.waiting_username)
                if cq.message:
                    await cq.message.edit_text(fmt.page_prompt_text())
                await cq.answer()
            elif payload.startswith("m:tag"):
                row, ok, _ = await _gate_cq(cq)
                if not ok:
                    await cq.answer("دسترسی ندارید", show_alert=False)
                    await _edit_denied(cq)
                    return
                await state.set_state(TagFlow.waiting_tag)
                if cq.message:
                    await cq.message.edit_text(fmt.tag_prompt_text())
                await cq.answer()
            elif payload.startswith("m:help"):
                row, ok, _ = await _gate_cq(cq)
                if not ok:
                    await cq.answer("دسترسی ندارید", show_alert=False)
                    await _edit_denied(cq)
                    return
                if cq.message:
                    await cq.message.edit_text(fmt.help_text(), reply_markup=fmt.menu_keyboard())
                await cq.answer()
            elif payload.startswith("m:menu"):
                row, ok, _ = await _gate_cq(cq)
                if not ok:
                    await cq.answer("دسترسی ندارید", show_alert=False)
                    await _edit_denied(cq)
                    return
                await state.clear()
                if cq.message:
                    await cq.message.edit_text(
                        fmt.start_text(bool(row["is_admin"]), False),
                        reply_markup=fmt.menu_keyboard(),
                    )
                await cq.answer()
            elif payload.startswith("h:p:"):
                await _handle_history_page(cq, payload)
            elif payload.startswith("h:v:"):
                await _handle_history_view(cq, payload)
            elif payload.startswith("a:req"):
                await _handle_access_request(cq)
            elif payload.startswith("a:ok:"):
                await _handle_approve(cq, payload)
            elif payload.startswith("a:no:"):
                await _handle_deny(cq, payload)
            elif payload.startswith("r:last"):
                await _handle_retry(cq)
            elif payload.startswith("r:h:"):
                await _handle_retry_history(cq, payload)
            else:
                await cq.answer("دکمه‌ی نامعتبر", show_alert=False)
        except TelegramAPIError as exc:
            logger.error("خطا در کال‌بک %s: %s", payload, exc)
            try:
                await cq.answer("خطا؛ دوباره امتحان کنید", show_alert=False)
            except TelegramAPIError:
                pass

    async def _handle_history_page(cq: CallbackQuery, payload: str) -> None:
        row, ok, _ = await _gate_cq(cq)
        if not ok:
            await cq.answer("دسترسی ندارید", show_alert=False)
            await _edit_denied(cq)
            return
        page = int(payload.split(":")[2] or 0)
        text, keyboard, _ = _history_payload(cq.from_user.id, page)  # type: ignore[union-attr]
        if cq.message:
            try:
                await cq.message.edit_text(text, reply_markup=keyboard)
            except TelegramAPIError:
                await cq.message.answer(text, reply_markup=keyboard)
        await cq.answer()

    async def _handle_history_view(cq: CallbackQuery, payload: str) -> None:
        row, ok, _ = await _gate_cq(cq)
        if not ok:
            await cq.answer("دسترسی ندارید", show_alert=False)
            return
        history_id = int(payload.split(":")[2] or 0)
        item = db.get_history(history_id)
        if not item:
            await cq.answer("آیتم پیدا نشد", show_alert=True)
            return
        uid = cq.from_user.id  # type: ignore[union-attr]
        if item["user_id"] != uid and not row["is_admin"]:
            await cq.answer("دسترسی ندارید", show_alert=True)
            return
        if cq.message:
            await cq.message.answer(
                fmt.history_view_html(item), reply_markup=fmt.history_view_keyboard(history_id)
            )
        await cq.answer()

    async def _handle_access_request(cq: CallbackQuery) -> None:
        row, ok, _ = await _gate_cq(cq)
        if ok:
            await cq.answer("شما قبلاً دسترسی دارید", show_alert=False)
            if cq.message:
                try:
                    await cq.message.edit_text(
                        fmt.start_text(bool(row["is_admin"]), False),
                        reply_markup=fmt.menu_keyboard(),
                    )
                except TelegramAPIError:
                    pass
            return
        if db.has_pending_request(row["telegram_id"]):
            await cq.answer("درخواست شما در انتظار است", show_alert=False)
            if cq.message:
                try:
                    await cq.message.edit_text(fmt.request_pending_text())
                except TelegramAPIError:
                    pass
            return
        db.add_access_request(row["telegram_id"])
        admin_id = db.admin_id() or settings.admin_id
        if not admin_id:
            logger.error("درخواست دسترسی ثبت شد ولی مدیری در دیتابیس نیست")
            await cq.answer("ثبت شد", show_alert=False)
            if cq.message:
                try:
                    await cq.message.edit_text(
                        "✅ درخواست شما ثبت شد (مدیر پیدا نشد؛ با مدیر هماهنگ کنید)."
                    )
                except TelegramAPIError:
                    pass
            return
        try:
            await cq.bot.send_message(  # type: ignore[union-attr]
                chat_id=admin_id,
                text=fmt.access_request_text(row["telegram_id"], row["username"], row["first_name"]),
                reply_markup=fmt.approve_keyboard(row["telegram_id"]),
            )
        except TelegramAPIError as exc:
            logger.error("ارسال درخواست به مدیر ناموفق: %s", exc)
            await cq.answer("خطا؛ بعداً دوباره امتحان کنید", show_alert=True)
            return
        await cq.answer("درخواست ارسال شد ✅", show_alert=False)
        if cq.message:
            try:
                await cq.message.edit_text(
                    "✅ <b>درخواست شما برای مدیر ارسال شد.</b>\n"
                    "به‌محض تایید، به شما اطلاع داده می‌شود."
                )
            except TelegramAPIError:
                pass

    async def _handle_approve(cq: CallbackQuery, payload: str) -> None:
        row, ok, _ = await _gate_cq(cq)
        if not row["is_admin"]:
            await cq.answer("فقط مدیر", show_alert=True)
            return
        parts = payload.split(":")
        target_id = int(parts[2])
        dur_raw = parts[3] if len(parts) > 3 else "24"
        hours: Optional[float] = None if dur_raw == "f" else float(dur_raw)
        target = db.get_user(target_id)
        if not target:
            await cq.answer("کاربر پیدا نشد", show_alert=True)
            return
        until = db.grant_access(target_id, hours)
        db.resolve_access_request(target_id, "granted")
        label = fmt.duration_label(hours)
        try:
            await cq.bot.send_message(  # type: ignore[union-attr]
                chat_id=target_id,
                text=fmt.access_granted_text(label, until),
                reply_markup=fmt.menu_keyboard(),
            )
        except TelegramAPIError as exc:
            logger.warning("اطلاع‌رسانی به کاربر مجاز‌شده ناموفق: %s", exc)
        if cq.message:
            try:
                await cq.message.edit_text(
                    fmt.admin_action_text("دسترسی داده شد", target, f" — مدت: {label}")
                )
            except TelegramAPIError:
                pass
        await cq.answer("تایید شد ✅")

    async def _handle_deny(cq: CallbackQuery, payload: str) -> None:
        row, ok, _ = await _gate_cq(cq)
        if not row["is_admin"]:
            await cq.answer("فقط مدیر", show_alert=True)
            return
        target_id = int(payload.split(":")[2])
        target = db.get_user(target_id)
        if not target:
            await cq.answer("کاربر پیدا نشد", show_alert=True)
            return
        db.revoke_access(target_id)
        db.resolve_access_request(target_id, "denied")
        try:
            await cq.bot.send_message(chat_id=target_id, text=fmt.access_denied_text())  # type: ignore[union-attr]
        except TelegramAPIError as exc:
            logger.warning("اطلاع‌رسانی ردشدن به کاربر ناموفق: %s", exc)
        if cq.message:
            try:
                await cq.message.edit_text(fmt.admin_action_text("درخواست رد شد", target))
            except TelegramAPIError:
                pass
        await cq.answer("رد شد")

    async def _handle_retry(cq: CallbackQuery) -> None:
        row, ok, _ = await _gate_cq(cq)
        if not ok:
            await cq.answer("دسترسی ندارید", show_alert=False)
            return
        if cq.message is None:
            await cq.answer("پیام اصلی پیدا نشد؛ دوباره از منو شروع کنید", show_alert=True)
            return
        stored = last_targets.get(cq.from_user.id)  # type: ignore[union-attr]
        if not stored:
            await cq.answer("چیزی برای تکرار نیست؛ از منو شروع کنید", show_alert=False)
            return
        kind, target = stored
        try:
            await cq.message.edit_text(
                f"↻ در حال تکرار: {('@' if kind == 'page' else '#') + target} ..."
            )
        except TelegramAPIError:
            pass
        await cq.answer()
        await _run_report(cq.message, kind, target, cq.from_user.id)  # type: ignore[union-attr]

    async def _handle_retry_history(cq: CallbackQuery, payload: str) -> None:
        row, ok, _ = await _gate_cq(cq)
        if not ok:
            await cq.answer("دسترسی ندارید", show_alert=False)
            return
        if cq.message is None:
            await cq.answer("پیام اصلی پیدا نشد؛ دوباره از منو شروع کنید", show_alert=True)
            return
        history_id = int(payload.split(":")[2] or 0)
        item = db.get_history(history_id)
        if not item or (item["user_id"] != cq.from_user.id and not row["is_admin"]):  # type: ignore[union-attr]
            await cq.answer("پیدا نشد", show_alert=True)
            return
        kind = "page" if item["kind"] == "page" else "hashtag"
        await cq.answer()
        await _run_report(cq.message, kind, item["target"], cq.from_user.id)  # type: ignore[union-attr]

    return router
