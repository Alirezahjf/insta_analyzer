"""فرمول Viral Score — برجسته‌کردن پست‌هایی که نسبت به فالوورهای پیج عالی‌اند.

فرمول (طبق درخواست کاربر):
    Viral Score = (Views + Likes×2 + Comments×5) / Page Followers
"""
from __future__ import annotations

from typing import Optional, Tuple


def viral_score(
    views: Optional[float],
    likes: Optional[float],
    comments: Optional[float],
    followers: Optional[float],
) -> Optional[float]:
    """محاسبه‌ی Viral Score.

    در صورتی None برمی‌گرداند که:
      - فالوورهای پیج صفر/نامشخص باشد (تقسیم بر صفر)،
      - مقادیر ورودی نامعتبر باشند.
    مقادیر None در views/likes/comments به‌عنوان ۰ حساب می‌شوند
    (مثلاً پستِ عکس بی‌بازدید یا لایک‌های پنهان).
    """
    try:
        f = float(followers or 0)
        if f <= 0:
            return None
        v = float(views or 0)
        l = float(likes or 0)
        c = float(comments or 0)
        return (v + l * 2.0 + c * 5.0) / f
    except (TypeError, ValueError):
        return None


# آستانه‌ها: (حداقل امتیاز، برچسب) — از بالا به پایین بررسی می‌شوند
TIERS: Tuple[Tuple[float, str], ...] = (
    (10.0, "🚀 فوق‌العاده ویروسی"),
    (5.0, "🔥 بسیار ویروسی"),
    (1.0, "📈 ویروسی"),
    (0.3, "🙂 خوب"),
    (0.0, "😐 متوسط/پایین"),
)


def viral_label(score: Optional[float]) -> str:
    """نمایش خواناِ امتیاز با برچسب؛ برای امتیاز نامشخص «—»."""
    if score is None:
        return "— (فالوور پیج معلوم نیست)"
    text = f"{score:.2f}"
    for threshold, label in TIERS:
        if score >= threshold:
            return f"{text} ({label})"
    return f"{text} (کم)"


def viral_formula_text() -> str:
    return "Viral = (بازدید + لایک×۲ + کامنت×۵) ÷ فالوورهای پیج"
