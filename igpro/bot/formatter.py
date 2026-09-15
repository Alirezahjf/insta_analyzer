"""سازنده‌ی پیام‌های تلگرام (HTML). همه‌ی داده‌های پویا escape می‌شوند."""
from __future__ import annotations

import html
import time
from typing import Any, Dict, List, Optional

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.viral import viral_formula_text, viral_label

TELEGRAM_TEXT_LIMIT = 4096


def esc(value: Any) -> str:
    """escape کامل برای HTML تلگرام."""
    return html.escape(str(value if value is not None else ""), quote=False)


def fmt_int(value: Any) -> str:
    try:
        return f"{int(value or 0):,}"
    except (TypeError, ValueError):
        return "—"


def fmt_date(epoch: Optional[float]) -> str:
    if not epoch:
        return "—"
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(epoch))


def clip(text: str, limit: int) -> str:
    text = str(text or "").replace("\n", " ").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def safe_text(text: str) -> str:
    """اگر پیام از سقف تلگرام بلندتر شد، کوتاهش می‌کند."""
    if len(text) <= TELEGRAM_TEXT_LIMIT:
        return text
    return text[: TELEGRAM_TEXT_LIMIT - 20] + "\n… (ادامه حذف شد)"


def media_kind_text(kind: str) -> str:
    return kind or "نامشخص"


# =====================================================================
#  کیبوردها
# =====================================================================
def menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🔍 جستجوی پیج", callback_data="m:page"),
                InlineKeyboardButton(text="🏷️ جستجوی هشتگ", callback_data="m:tag"),
            ],
            [
                InlineKeyboardButton(text="🕘 هیزتوری من", callback_data="h:p:0"),
                InlineKeyboardButton(text="ℹ️ راهنما", callback_data="m:help"),
            ],
        ]
    )


def request_access_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🙋 درخواست دسترسی", callback_data="a:req")]]
    )


def approve_keyboard(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⏱ ۱ ساعت", callback_data=f"a:ok:{user_id}:1"),
                InlineKeyboardButton(text="⏱ ۶ ساعت", callback_data=f"a:ok:{user_id}:6"),
            ],
            [
                InlineKeyboardButton(text="⏱ ۱ روز", callback_data=f"a:ok:{user_id}:24"),
                InlineKeyboardButton(text="⏱ ۷ روز", callback_data=f"a:ok:{user_id}:168"),
            ],
            [
                InlineKeyboardButton(text="♾️ دائمی", callback_data=f"a:ok:{user_id}:f"),
                InlineKeyboardButton(text="🚫 رد", callback_data=f"a:no:{user_id}"),
            ],
        ]
    )


def retry_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🔁 تلاش دوباره", callback_data="r:last"),
                InlineKeyboardButton(text="🏠 منو", callback_data="m:menu"),
            ]
        ]
    )


def result_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🔁 تکرار این گزارش", callback_data="r:last"),
                InlineKeyboardButton(text="🏠 منو", callback_data="m:menu"),
            ]
        ]
    )


def history_keyboard(
    rows: List[Dict[str, Any]], page: int, has_prev: bool, has_next: bool
) -> InlineKeyboardMarkup:
    kb_rows: List[List[InlineKeyboardButton]] = []
    for row in rows:
        icon = "📄" if row["kind"] == "page" else "🏷️"
        target = ("@" + row["target"]) if row["kind"] == "page" else ("#" + row["target"])
        kb_rows.append(
            [InlineKeyboardButton(text=f"👀 {icon} {target[:28]}", callback_data=f"h:v:{row['id']}")]
        )
    nav: List[InlineKeyboardButton] = []
    if has_prev:
        nav.append(InlineKeyboardButton(text="⬅️ قبلی", callback_data=f"h:p:{page - 1}"))
    nav.append(InlineKeyboardButton(text="🏠 منو", callback_data="m:menu"))
    if has_next:
        nav.append(InlineKeyboardButton(text="بعدی ➡️", callback_data=f"h:p:{page + 1}"))
    kb_rows.append(nav)
    return InlineKeyboardMarkup(inline_keyboard=kb_rows)


# =====================================================================
#  پیام‌های عمومی
# =====================================================================
def start_text(is_admin: bool, is_first: bool) -> str:
    if is_admin:
        who = (
            "👑 شما <b>مدیر</b> ربات هستید (اولین نفری که استارت زد)."
            if is_first
            else "👑 شما مدیر ربات هستید."
        )
        return (
            f"📊 <b>ربات تحلیل اینستاگرام</b>\n\n{who}\n"
            "بقیه‌ی کاربران بدون مجوزِ شما نمی‌توانند از ربات استفاده کنند.\n"
            "دسترسی‌ها را از همین پیام‌ها تایید/رد می‌کنید و با /users می‌بینید.\n\n"
            "از منوی زیر شروع کنید 👇"
        )
    return (
        "📊 <b>ربات تحلیل اینستاگرام</b>\n\n"
        "می‌توانید اطلاعات یک پیج یا پست‌های یک هشتگ را بگیرید.\n"
        "از منوی زیر شروع کنید 👇"
    )


def help_text() -> str:
    return (
        "ℹ️ <b>راهنما</b>\n\n"
        "<b>دستورات:</b>\n"
        "/page <i>username</i> — تحلیل کامل یک پیج (۲ پست آخر + آمار + Viral Score)\n"
        "/tag <i>هشتگ</i> — پست‌های برتر یک هشتگ با آمار و Viral Score\n"
        "/history — هیزتوری جستجوهای من\n"
        "/menu — نمایش منو\n"
        "/cancel — لغو واردکردن نام\n\n"
        "<b>یا از دکمه‌های منو استفاده کنید</b> — مثلاً «🔍 جستجوی پیج» را بزنید"
        " و نام کاربری را بفرستید.\n\n"
        f"🧬 {viral_formula_text()}"
    )


def denied_text() -> str:
    return (
        "🚫 <b>شما به این ربات دسترسی ندارید.</b>\n"
        "برای دریافت دسترسی، دکمه‌ی زیر را بزنید تا درخواست برای مدیر ارسال شود.\n"
        "مدیر می‌تواند دسترسی را برای یک مدت مشخص (یا دائمی) بدهد."
    )


def busy_text(is_own: bool) -> str:
    if is_own:
        return (
            "⏳ <b>درخواست قبلیِ شما هنوز در حال انجام است.</b>\n"
            "لطفاً صبر کنید تا کامل شود (پیام گام‌به‌گام را ببینید) و بعد درخواست جدید بدهید."
        )
    return (
        "⏳ <b>ربات مشغول است</b> — در حال انجام درخواست یک کاربر دیگر.\n"
        "تا تکمیل آن، درخواست جدیدی پذیرفته نمی‌شود؛ چند ثانیه بعد دوباره امتحان کنید."
    )


def page_prompt_text() -> str:
    return (
        "🔍 <b>نام کاربری پیج</b> را بفرستید (بدون @)\n"
        "مثال: <code>nima.arish</code>\n\n"
        "برای لغو: /cancel"
    )


def tag_prompt_text() -> str:
    return (
        "🏷️ <b>هشتگ</b> را بفرستید (با # یا بدون)\n"
        "مثال: <code>#مدلینگ</code> یا <code>modling</code>\n\n"
        "برای لغو: /cancel"
    )


def invalid_username_text() -> str:
    return (
        "⚠️ این یک نام کاربریِ معتبر نیست.\n"
        "فقط حروف انگلیسی، عدد، نقطه و آندرلاین (حداکثر ۳۰ کاراکتر).\n"
        "مثال: <code>nima.arish</code>"
    )


def invalid_tag_text() -> str:
    return "⚠️ هشتگ نامعتبر است. متنِ هشتگ (با یا بدون #) را بفرستید."


def cancelled_text() -> str:
    return "✅ لغو شد. برای شروع دوباره از منو استفاده کنید."


def error_text(title: str, reason: str) -> str:
    return (
        f"❌ <b>{esc(title)}</b>\n\n"
        f"🔎 {esc(reason)}\n\n"
        "اگر مشکل تکرار شد، فایل لاگ <code>state/logs/igpro.log</code> را بررسی کنید."
    )


def not_authorized_admin_text() -> str:
    return "🚫 این دستور فقط برای <b>مدیر</b> ربات است."


# =====================================================================
#  گزارش پیج
# =====================================================================
def _post_block(index: int, post: Dict[str, Any], show_owner: bool = False) -> str:
    lines = [f"<b>{index}) {esc(media_kind_text(post.get('kind')))}</b>"]
    if show_owner:
        owner = post.get("owner") or {}
        owner_name = esc(owner.get("username") or "نامشخص")
        owner_followers = owner.get("followers")
        owner_followers_txt = fmt_int(owner_followers) if owner_followers is not None else "؟"
        lines.append(f"👤 صاحب: @{owner_name} (فالوورها: {owner_followers_txt})")
    url = post.get("url") or ""
    if url:
        safe_url = html.escape(url, quote=True)
        shown = url.replace("https://www.instagram.com/", "")
        lines.append(f'🔗 <a href="{safe_url}">{esc(shown)}</a>')
    likes_txt = "پنهان" if post.get("likes_hidden") else fmt_int(post.get("likes"))
    lines.append(
        f"❤️ {likes_txt} | 💬 {fmt_int(post.get('comments'))} | 👁 {fmt_int(post.get('views'))}"
    )
    meta = []
    if post.get("duration_sec"):
        meta.append(f"⏱ {post['duration_sec']} ثانیه")
    if post.get("taken_at"):
        taken = str(post["taken_at"])
        meta.append(f"🗓 {esc(taken[:16])}")
    if meta:
        lines.append(" | ".join(meta))
    lines.append(f"🧬 Viral Score: <b>{esc(viral_label(post.get('viral')))}</b>")
    caption = post.get("caption")
    if caption:
        lines.append(f"📝 کپشن: {esc(clip(caption, 120))}")
    return "\n".join(lines)


def page_report_html(data: Dict[str, Any]) -> str:
    p = data.get("profile") or {}
    yes_no = lambda b: "بله" if b else "خیر"  # noqa: E731
    lines = [
        f"📊 <b>تحلیل پیج</b> — @{esc(p.get('username') or data.get('username'))}",
        "",
        f"👤 نام کامل: {esc(p.get('full_name') or '—')}",
        f"🆔 شناسه: {esc(p.get('user_id'))}",
    ]
    if p.get("biography"):
        lines.append(f"📝 بیو: {esc(clip(p['biography'], 200))}")
    lines += [
        f"👥 فالوورها: <b>{fmt_int(p.get('followers'))}</b>",
        f"🔗 فالوینگ: {fmt_int(p.get('following'))}",
        f"🖼️ تعداد پست‌ها: {fmt_int(p.get('media_count'))}",
        f"🔒 خصوصی: {yes_no(p.get('is_private'))}",
        f"💼 بیزینسی: {yes_no(p.get('is_business'))}",
        f"✔️ تیک آبی: {yes_no(p.get('is_verified'))}",
    ]
    if p.get("category"):
        lines.append(f"🏷️ دسته‌بندی: {esc(p['category'])}")
    if p.get("external_url"):
        lines.append(f"🌐 لینک بیو: {esc(clip(p['external_url'], 80))}")

    lines += ["", "━" * 18, "📈 <b>آخرین پست‌ها + آمار</b>"]
    if data.get("clips_note"):
        lines.append(f"(یادداشت: {esc(data['clips_note'])})")
    for i, post in enumerate(data.get("posts") or [], start=1):
        lines.append("")
        lines.append(_post_block(i, post, show_owner=False))

    lines.append("")
    lines.append("━" * 18)
    lines.append(
        f"Σ جمع این {len(data.get('posts') or [])} پست →  "
        f"❤️ {fmt_int(data.get('total_likes'))} | 💬 {fmt_int(data.get('total_comments'))} | "
        f"👁 {fmt_int(data.get('total_views'))}"
    )
    if data.get("top_post_index"):
        lines.append(
            f"🏆 پستِ پرمایه‌تر: پست {data['top_post_index']} — "
            f"Viral <b>{esc(viral_label(data.get('top_viral')))}</b>"
        )
    lines.append(f"\n🧮 {esc(viral_formula_text())}")
    return safe_text("\n".join(lines))


# =====================================================================
#  گزارش هشتگ
# =====================================================================
def hashtag_report_html(data: Dict[str, Any]) -> str:
    lines = [
        f"🏷️ <b>تحلیل هشتگ</b> — #{esc(data.get('tag'))}",
        "",
        f"📝 تعداد کل پست‌های هشتگ: <b>{fmt_int(data.get('media_count'))}</b>",
        "",
        "━" * 18,
        f"📈 <b>پست‌های برتر</b> ({len(data.get('posts') or [])} نمونه از رتبه‌بندی اینستاگرام):",
    ]
    for i, post in enumerate(data.get("posts") or [], start=1):
        lines.append("")
        lines.append(_post_block(i, post, show_owner=True))
    lines.append("")
    lines.append("━" * 18)
    lines.append(
        f"Σ جمع این {len(data.get('posts') or [])} پست →  "
        f"❤️ {fmt_int(data.get('total_likes'))} | 💬 {fmt_int(data.get('total_comments'))} | "
        f"👁 {fmt_int(data.get('total_views'))}"
    )
    if data.get("top_post_index"):
        lines.append(
            f"🏆 پستِ پرمایه‌تر: پست {data['top_post_index']} — "
            f"Viral <b>{esc(viral_label(data.get('top_viral')))}</b>"
        )
    lines.append(f"\n🧮 {esc(viral_formula_text())}")
    return safe_text("\n".join(lines))


def empty_result_text(kind: str, target: str) -> str:
    if kind == "page":
        return f"⚠️ هیچ پستی برای @{esc(target)} پیدا نشد (پیج خصوصی یا بدون پست؟)."
    return f"⚠️ هیچ پستی برای #{esc(target)} پیدا نشد."


# =====================================================================
#  هیزتوری
# =====================================================================
def history_page_html(rows: List[Dict[str, Any]], page: int, total: int) -> str:
    if not rows:
        return (
            "🕘 <b>هیزتوری من</b>\n\n"
            "هنوز کاری انجام نداده‌اید. اولین جستجو را انجام دهید تا اینجا ثبت شود."
        )
    lines = [
        f"🕘 <b>هیزتوری من</b> — صفحه {page + 1} (کل: {total})",
        "",
        "روی دکمه‌ی 👀 زیر بزنید تا نتیجه‌ی همان جستجو دوباره نمایش داده شود:",
        "",
    ]
    for row in rows:
        icon = "📄" if row["kind"] == "page" else "🏷️"
        target = ("@" + row["target"]) if row["kind"] == "page" else ("#" + row["target"])
        status = "✅" if row["status"] == "ok" else "❌"
        lines.append(f"{status} {icon} <b>{esc(target)}</b>")
        lines.append(f"   🗓 {esc(fmt_date(row['created_at']))}")
        lines.append("")
    return safe_text("\n".join(lines))


def history_view_html(row: Dict[str, Any]) -> str:
    saved = row.get("result_html") or ""
    kind_icon = "📄" if row["kind"] == "page" else "🏷️"
    target = ("@" + row["target"]) if row["kind"] == "page" else ("#" + row["target"])
    header = (
        f"📋 <b>آیتم هیزتوری</b> — {kind_icon} {esc(target)}\n"
        f"🗓 ثبت‌شده در: {esc(fmt_date(row['created_at']))}\n"
        "─" * 18
    )
    if not saved:
        return header + "\n\n(این نتیجه خطا بوده و محتوایی ذخیره نشده.)"
    return safe_text(header + "\n" + saved)


def history_view_keyboard(history_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🔁 اجرای دوباره", callback_data=f"r:h:{history_id}"),
                InlineKeyboardButton(text="🕘 فهرست هیزتوری", callback_data="h:p:0"),
            ]
        ]
    )


# =====================================================================
#  مدیریت دسترسی
# =====================================================================
def access_request_text(user_id: int, username: Optional[str], first_name: Optional[str]) -> str:
    who = esc(username or first_name or "بدون نام")
    return (
        f"🙋 <b>درخواست دسترسی جدید</b>\n\n"
        f"کاربر <b>{who}</b> (آیدی: <code>{user_id}</code>) درخواست استفاده از ربات را داده است.\n"
        "مدت دسترسی دلخواه را انتخاب کنید:"
    )


def duration_label(hours: Optional[float]) -> str:
    if hours is None:
        return "دائمی"
    if hours < 24:
        return f"{int(hours)} ساعت"
    days = hours / 24
    return f"{int(days)} روز" if days == int(days) else f"{days:.1f} روز"


def access_granted_text(duration: str, until: float) -> str:
    return (
        "✅ <b>دسترسی فعال شد!</b>\n"
        f"مدت: {esc(duration)} (تا {esc(fmt_date(until))})\n\n"
        "از منو استفاده کنید 👇"
    )


def access_denied_text() -> str:
    return "❌ درخواست شما توسط مدیر رد شد."


def access_expired_text() -> str:
    return (
        "⌛ <b>دسترسی شما تمام شده است.</b>\n"
        "دوباره درخواست بدهید تا مدیر تصمیم بگیرد."
    )


def request_pending_text() -> str:
    return "📨 درخواست قبلیِ شما هنوز در انتظار تصمیم مدیر است؛ صبر کنید."


def users_list_html(rows: List[Dict[str, Any]]) -> str:
    if not rows:
        return "👥 هیچ کاربری ثبت نشده است."
    lines = ["👥 <b>کاربران ربات</b>", ""]
    now = time.time()
    for row in rows:
        name = esc(row.get("username") or row.get("first_name") or "بدون نام")
        if row["is_admin"]:
            lines.append(f"👑 <b>{name}</b> (<code>{row['telegram_id']}</code>) — مدیر")
            continue
        until = row.get("access_until")
        if until and until > now:
            remain_h = (until - now) / 3600
            remain = (
                f"{int(remain_h // 24)} روز و {int(remain_h % 24)} ساعت"
                if remain_h >= 24
                else f"{remain_h:.1f} ساعت"
            )
            lines.append(
                f"⏳ <b>{name}</b> (<code>{row['telegram_id']}</code>) — "
                f"تا {esc(fmt_date(until))} ({esc(remain)} مانده)"
            )
        else:
            lines.append(f"🚫 <b>{name}</b> (<code>{row['telegram_id']}</code>) — بدون دسترسی")
    lines.append("")
    lines.append("مدیریت: /grant <code>id</code> <code>24h|7d|forever</code>  ·  /revoke <code>id</code>")
    return safe_text("\n".join(lines))


def admin_action_text(action: str, target_user: Dict[str, Any], extra: str = "") -> str:
    name = esc(target_user.get("username") or target_user.get("first_name") or target_user.get("telegram_id"))
    return f"✅ {action} — کاربر <b>{name}</b> (<code>{target_user['telegram_id']}</code>){esc(extra)}"
