"""پیام بروزرسانی‌شده‌ی قدم‌به‌قدم با ددلاین (مهلت) برای هر گام.

طراحی:
  - هر گام یک مهلت (deadline) دارد؛ اگر در مهلت انجام نشد، گام «⏰ سررسید» می‌شود
    و کل درخواست با پیام روشن متوقف می‌شود (engine با is_aborted بررسی می‌کند).
  - مهلتِ کلِ درخواست (overall_deadline) هم جداگانه نگه‌داری می‌شود.
  - engine در یک رشته‌ی جدا کار می‌کند؛ push() از آن رشته پیام را به لوبِ اصلی
    می‌فرستد (run_coroutine_threadsafe) و نیازی به await ندارد.
  - ویرایش‌ها throttle شده‌اند (حداکثر هر ۴ ثانیه) تا به محدودیت تلگرام نخوریم.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardMarkup

PENDING = "pending"
RUNNING = "running"
OK = "ok"
FAIL = "fail"
TIMEOUT = "timeout"

_NUM_EMOJI = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]


def num_emoji(i: int) -> str:
    return _NUM_EMOJI[i] if 0 <= i < len(_NUM_EMOJI) else f"{i + 1}."


@dataclass(frozen=True)
class Step:
    title: str
    deadline: float = 60.0


def _status_icon(state: str) -> str:
    return {PENDING: "⬜", RUNNING: "⏳", OK: "✅", FAIL: "❌", TIMEOUT: "⏰"}.get(state, "⬜")


class ProgressReporter:
    def __init__(
        self,
        bot: Bot,
        chat_id: int,
        title: str,
        steps: List[Step],
        heartbeat: float = 8.0,
        overall_deadline: float = 900.0,
        min_edit_gap: float = 4.0,
    ):
        self.bot = bot
        self.chat_id = chat_id
        self.title = title
        self.steps = steps
        self.heartbeat = heartbeat
        self.overall_deadline = overall_deadline
        self.min_edit_gap = min_edit_gap

        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.message_id: Optional[int] = None
        self._states: List[str] = [PENDING] * len(steps)
        self._durations: List[Optional[float]] = [None] * len(steps)
        self._started: List[Optional[float]] = [None] * len(steps)
        self._errors: List[Optional[str]] = [None] * len(steps)
        self._current = -1
        self._t0 = 0.0
        self._last_edit = 0.0
        self._final_text = ""
        self._finalize_error: Optional[str] = None
        self._aborted = False
        self._edit_lock = asyncio.Lock()
        self._tasks: List[asyncio.Task] = []

    # ---------------------------------------------------------- ایجاد و شروع
    async def start(self, reply_markup: Optional[InlineKeyboardMarkup] = None) -> int:
        """پیام اولیه را می‌فرستد و نگهبان‌های مهلت را روشن می‌کند."""
        self.loop = asyncio.get_running_loop()
        self._t0 = time.monotonic()
        msg = await self.bot.send_message(
            chat_id=self.chat_id, text=self._render(), reply_markup=reply_markup
        )
        self.message_id = msg.message_id
        self._last_edit = self.loop.time()
        self._tasks.append(self.loop.create_task(self._heartbeat_loop()))
        self._tasks.append(
            self.loop.create_task(self._overall_guard())
        )
        return self.message_id

    # ------------------------------------------------- ورودیِ امن از رشته‌ها
    def push(self, event: Dict[str, Any]) -> None:
        """از رشته‌ی worker فراخوانی می‌شود (بدون await).

        event: {"op": "begin"|"finish"|"complete"|"fail", "i": int?,
                "ok": bool?, "error": str?, "markup": ?}
        """
        if self.loop is None:
            return
        asyncio.run_coroutine_threadsafe(self._handle(event), self.loop)

    @property
    def is_aborted(self) -> bool:
        return self._aborted

    def elapsed(self) -> float:
        return time.monotonic() - self._t0 if self._t0 else 0.0

    # ------------------------------------------------------------ لوب داخلی
    async def _handle(self, event: Dict[str, Any]) -> None:
        op = event.get("op")
        if op == "begin":
            i = int(event["i"])
            if 0 <= i < len(self.steps) and self._states[i] == PENDING:
                self._states[i] = RUNNING
                self._started[i] = time.monotonic()
                self._current = i
                await self._render_update(force=True)
                self._tasks.append(self.loop.create_task(self._step_guard(i)))
        elif op == "finish":
            i = int(event["i"])
            if not (0 <= i < len(self.steps)):
                return
            ok = bool(event.get("ok", True))
            self._states[i] = OK if ok else FAIL
            dur = time.monotonic() - (self._started[i] or time.monotonic())
            self._durations[i] = dur
            if not ok:
                self._errors[i] = str(event.get("error") or "خطا")
                self._finalize_error = (
                    f"گام {i + 1} ({self.steps[i].title}) شکست خورد: {self._errors[i]}"
                )
                self._aborted = True
                self._current = -1
                await self._finalize(event.get("markup"))
            else:
                if self._current == i:
                    self._current = i + 1 if i + 1 < len(self.steps) else -1
                await self._render_update(force=True)
        elif op == "complete":
            self._current = -1
            await self._finalize(event.get("markup"))
        elif op == "fail":
            self._finalize_error = str(event.get("error") or "خطای ناشناخته")
            self._current = -1
            await self._finalize(event.get("markup"))

    async def _step_guard(self, i: int) -> None:
        """اگر گام i در مهلتش تمام نشد، کل درخواست را متوقف می‌کند."""
        try:
            await asyncio.sleep(self.steps[i].deadline)
        except asyncio.CancelledError:
            return
        if self._states[i] == RUNNING:
            self._states[i] = TIMEOUT
            self._durations[i] = time.monotonic() - (self._started[i] or time.monotonic())
            self._errors[i] = f"مهلت {self.steps[i].deadline:.0f} ثانیه تمام شد"
            self._finalize_error = (
                f"⏰ گام {i + 1} ({self.steps[i].title}) در مهلتِ {self.steps[i].deadline:.0f} ثانیه"
                " انجام نشد؛ درخواست لغو شد."
            )
            self._aborted = True
            if self._current == i:
                self._current = -1
            await self._finalize()

    async def _overall_guard(self) -> None:
        try:
            await asyncio.sleep(self.overall_deadline)
        except asyncio.CancelledError:
            return
        if not self._aborted and self._current != -1:
            self._finalize_error = (
                f"⏰ مهلت کلِ درخواست ({self.overall_deadline:.0f} ثانیه) تمام شد."
            )
            self._aborted = True
            await self._finalize()

    async def _heartbeat_loop(self) -> None:
        """هر heartbeat ثانیه، زمانِ سپری‌شده‌ی گامِ جاری را به‌روز می‌کند."""
        try:
            while True:
                await asyncio.sleep(self.heartbeat)
                if self._aborted or self._current == -1:
                    return
                await self._render_update(force=False)
        except asyncio.CancelledError:
            return

    # ---------------------------------------------------------------- رندر
    def _render(self) -> str:
        lines = [f"🧭 {self.title}", ""]
        for i, step in enumerate(self.steps):
            state = self._states[i]
            icon = _status_icon(state)
            num = num_emoji(i)
            if state == RUNNING:
                elapsed = time.monotonic() - (self._started[i] or time.monotonic())
                lines.append(f"{num} {step.title} — {icon} {elapsed:.0f}s / {step.deadline:.0f}s")
            elif state == OK:
                lines.append(f"{num} {step.title} — {icon} {self._durations[i]:.1f}s")
            elif state == TIMEOUT:
                lines.append(f"{num} {step.title} — {icon} مهلت تمام شد")
            elif state == FAIL:
                err = (self._errors[i] or "")[:80]
                lines.append(f"{num} {step.title} — {icon} {err}")
            else:
                lines.append(f"{num} {step.title} — {icon}")
        lines.append("─" * 24)
        lines.append(f"⏱ کلِ سپری‌شده: {self.elapsed():.0f}s")
        return "\n".join(lines)

    async def _finalize(self, markup: Optional[InlineKeyboardMarkup] = None) -> None:
        if self.message_id is None:
            return
        if self._finalize_error is None:
            note = f"✅ همه‌ی گام‌ها در {self.elapsed():.0f} ثانیه کامل شد — نتیجه را پیام بعدی ببینید 👇"
        else:
            note = f"❌ {self._finalize_error}\n⏱ کلِ سپری‌شده: {self.elapsed():.0f}s"
        self._final_text = self._render() + "\n\n" + note
        try:
            async with self._edit_lock:
                await self.bot.edit_message_text(
                    chat_id=self.chat_id,
                    message_id=self.message_id,
                    text=self._final_text,
                    reply_markup=markup,
                )
        except TelegramAPIError as exc:
            if "not modified" not in str(exc).lower():
                # شکستِ ویرایش آخرین پیام نباید کل را خراب کند؛ فقط لاگ
                pass
        for task in self._tasks:
            if not task.done():
                task.cancel()

    async def _render_update(self, force: bool) -> None:
        if self.message_id is None:
            return
        now = self.loop.time()
        if not force and (now - self._last_edit) < self.min_edit_gap:
            return
        text = self._render()
        try:
            async with self._edit_lock:
                await self.bot.edit_message_text(
                    chat_id=self.chat_id, message_id=self.message_id, text=text
                )
                self._last_edit = now
        except TelegramAPIError as exc:
            if "not modified" in str(exc).lower():
                self._last_edit = now
            # سایر خطاها (مثلاً پیام حذف‌شده) را بی‌صبرانه می‌گذاریم
