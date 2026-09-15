"""دریافت اطلاعات پروفایل، فالوورها، پست‌ها و تنظیمات امنیتی/تایید ایمیل.

نکته (مستند در types.py):
    user_info()  → مدل User     : شامل follower_count / following_count / media_count
    account_info() → مدل Account: شمارنده‌ها را ندارد!
    به همین دلیل خروجی را با getattr و به‌صورت تدافعی می‌خوانیم تا با هر نسخه/مدلی سازگار باشد.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List

from instagrapi import Client

logger = logging.getLogger("igpro.profile")


def _safe(obj: Any, *names: str, default: Any = None) -> Any:
    """خواندن تدافعیِ ویژگی‌ها: با مدل‌های مختلف (User/Account/UserShort) سازگار است."""
    for name in names:
        if obj is None:
            continue
        if isinstance(obj, dict):
            if name in obj and obj[name] is not None:
                return obj[name]
            continue
        value = getattr(obj, name, None)
        if value is not None:
            return value
    return default


def normalize_user(obj: Any) -> Dict[str, Any]:
    """تبدیل مدلِ کاربر به دیکشنریِ امن برای JSON."""
    return {
        "user_id": str(_safe(obj, "pk", "user_id", "id", default="") or ""),
        "username": _safe(obj, "username", default="") or "",
        "full_name": _safe(obj, "full_name", default="") or "",
        "biography": _safe(obj, "biography", "bio", default="") or "",
        "followers": int(_safe(obj, "follower_count", "followers", default=0) or 0),
        "following": int(_safe(obj, "following_count", "following", default=0) or 0),
        "media_count": int(_safe(obj, "media_count", "medias_count", default=0) or 0),
        "is_private": bool(_safe(obj, "is_private", default=False)),
        "is_business": bool(_safe(obj, "is_business", default=False)),
        "is_verified": bool(_safe(obj, "is_verified", default=False)),
        "category": _safe(obj, "category_name", "business_category_name", "category", default="") or "",
        "external_url": str(_safe(obj, "external_url", default="") or ""),
        "profile_pic_url": str(_safe(obj, "profile_pic_url_hd", "profile_pic_url", default="") or ""),
        "public_email": _safe(obj, "public_email", default="") or "",
    }


def own_profile(cl: Client) -> Dict[str, Any]:
    """اطلاعات کامل پیجِ خودی در یک درخواست (بدون تکیه به cl.username)."""
    user_id = cl.user_id
    if not user_id:
        raise RuntimeError("شناسه‌ی کاربر در دسترس نیست؛ دوباره لاگین کنید (--fresh).")
    return normalize_user(cl.user_info(user_id))


def profile_by_username(cl: Client, username: str) -> Dict[str, Any]:
    username = (username or "").strip().lstrip("@").lower()
    if not username:
        raise ValueError("نام کاربری خالی است (این همان باگی است که 'none' جستجو می‌کرد).")
    user_id = cl.user_id_from_username(username)
    return normalize_user(cl.user_info(user_id))


def followers(cl: Client, user_id: str, amount: int = 0) -> List[Dict[str, Any]]:
    return [normalize_user(u) for u in cl.user_followers(user_id, amount=amount).values()]


def following(cl: Client, user_id: str, amount: int = 0) -> List[Dict[str, Any]]:
    return [normalize_user(u) for u in cl.user_following(user_id, amount=amount).values()]


def medias(cl: Client, user_id: str, amount: int = 12) -> List[Dict[str, Any]]:
    """آخرین پست‌های کاربر (user_medias در mixins/media.py)."""
    out = []
    for media in cl.user_medias(user_id, amount=amount):
        out.append({
            "id": str(_safe(media, "id", "pk", default="") or ""),
            "code": _safe(media, "code", default="") or "",
            "taken_at": str(_safe(media, "taken_at", default="") or ""),
            "media_type": int(_safe(media, "media_type", default=0) or 0),
            "caption": (_safe(media, "caption_text", "caption", default="") or "")[:300],
            "like_count": int(_safe(media, "like_count", default=0) or 0),
            "comment_count": int(_safe(media, "comment_count", default=0) or 0),
            "play_count": _safe(media, "play_count", default=None),
            "thumbnail_url": str(_safe(media, "thumbnail_url", default="") or ""),
        })
    return out


def security_info(cl: Client) -> Dict[str, Any]:
    """وضعیت امنیت حساب: ایمیل/شماره تایید شده، ۲FA، دستگاه‌های مورد اعتماد."""
    return cl.account_security_info()


# =====================================================================
# آخرین پست‌ها + آمار (لایک / کامنت / بازدید) + لینک پست
# =====================================================================
MEDIA_TYPE_LABELS = {0: "نامشخص", 1: "عکس", 2: "ویدیو", 8: "آلبوم (چند رسانه‌ای)"}
MAX_SCAN = 100  # سقفِ ایمنی: هرگز بیش از این تعداد رسانه بررسی نمی‌شود

_SINGLE_MEDIA_RE = re.compile(r"instagram\.com/(?:p|reel|reels|tv)/([A-Za-z0-9_\-]+)", re.I)
_USERNAME_RE = re.compile(r"instagram\.com/([A-Za-z0-9_.]+)", re.I)
_IGNORED_SEGMENTS = {
    "p", "reel", "reels", "tv", "stories", "explore", "accounts", "direct",
    "about", "privacy", "terms", "help", "developer", "api", "legal",
}


def parse_target(target: str) -> tuple:
    """تشخیص نوع ورودی: ('media', shortcode) | ('username', name)"""
    target = (target or "").strip()
    if not target:
        raise ValueError("ورودی خالی است.")
    single = _SINGLE_MEDIA_RE.search(target)
    if single:
        return "media", single.group(1)
    if "instagram.com" in target.lower():
        match = _USERNAME_RE.search(target)
        if match:
            name = match.group(1).strip("/.")
            if name.lower() not in _IGNORED_SEGMENTS:
                return "username", name
        if "/stories/" in target.lower():
            raise ValueError("لینک استوری پشتیبانی نمی‌شود؛ لینک پروفایل یا یک پست/ریل را بدهید.")
        raise ValueError(f"لینک قابل تشخیص نیست: {target}")
    return "username", target.strip().lstrip("@").lower()


def media_permalink(media: Any) -> str:
    code = _safe(media, "code", default="") or ""
    if not code:
        return ""
    product_type = (_safe(media, "product_type", default="") or "").lower()
    if product_type == "clips":
        return f"https://www.instagram.com/reel/{code}/"
    if product_type == "igtv":
        return f"https://www.instagram.com/tv/{code}/"
    return f"https://www.instagram.com/p/{code}/"


def media_kind(media: Any) -> str:
    media_type = int(_safe(media, "media_type", default=0) or 0)
    product_type = (_safe(media, "product_type", default="") or "").lower()
    if product_type == "clips":
        return "ریل (Reel)"
    if product_type == "igtv":
        return "IGTV"
    return MEDIA_TYPE_LABELS.get(media_type, "نامشخص")


def media_stats(media: Any) -> Dict[str, Any]:
    """استخراج آمار یک پست؛ بازدید از play_count و در نبودش view_count خوانده می‌شود."""
    likes_hidden = bool(_safe(media, "like_and_view_counts_disabled", default=False))
    likes = _safe(media, "like_count", default=None)
    views = _safe(media, "play_count", default=None)
    if views in (None, 0):
        views = _safe(media, "view_count", default=0)
    comments = _safe(media, "comment_count", default=0)
    return {
        "media_pk": str(_safe(media, "pk", "id", default="") or ""),
        "shortcode": _safe(media, "code", default="") or "",
        "kind": media_kind(media),
        "url": media_permalink(media),
        "likes": None if likes_hidden else int(likes or 0),
        "likes_hidden": likes_hidden,
        "comments": int(comments or 0),
        "views": int(views or 0),
        "duration_sec": round(float(_safe(media, "video_duration", default=0) or 0), 2),
        "taken_at": str(_safe(media, "taken_at", default="") or ""),
        "caption": (_safe(media, "caption_text", default="") or "")[:500],
        "thumbnail": str(_safe(media, "thumbnail_url", default="") or ""),
    }


def _collect_media(cl: Client, user_id: str, amount: int, include_reels: bool) -> list:
    """پست‌های عادی + (در صورت نیاز) ریل‌ها؛ حذف تکراری و مرتب‌سازی بر اساس زمان.

    ⚠️ نکته‌ی حیاتی: در instagrapi مقدار amount=0 یعنی «همه‌ی پست‌ها» و باعث می‌شود
    برنامه صدها درخواست بزند و عملاً هنگ کند. همیشه مقدار محدود می‌فرستیم.
    برای گرفتنِ جدیدترین N پست، گرفتنِ N مورد از هر منبع کافی است (جدیدترینِ کل،
    قطعاً داخلِ اجتماعِ «N تایِ اول هر منبع» است).
    """
    # مقدار منفی/صفر را به ۱ محدود می‌کنیم (اسلایسِ منفی در انتها پرتخرج‌ساز است)
    wanted = max(1, min(int(amount) if amount else 1, MAX_SCAN))

    logger.info("دریافت %d پستِ آخر ...", wanted)
    media_list = list(cl.user_medias(user_id, amount=wanted) or [])

    if include_reels:
        logger.info("دریافت %d ریلِ آخر ...", wanted)
        try:
            media_list += list(cl.user_clips(user_id, amount=wanted) or [])
        except Exception as exc:
            logger.debug("دریافت ریل‌ها ناموفق بود: %s", exc)
    seen, unique = set(), []
    for media in media_list:
        pk = str(_safe(media, "pk", "id", default="") or "")
        if pk and pk not in seen:
            seen.add(pk)
            unique.append(media)
    unique.sort(key=lambda m: str(_safe(m, "taken_at", default="") or ""), reverse=True)
    return unique[:wanted]


def last_posts(cl: Client, target: str, amount: int = 2, detail: bool = True,
               include_reels: bool = True) -> Dict[str, Any]:
    """آخرین پست‌های یک پیج همراه لایک/کامنت/بازدید و لینک.

    detail=True برای هر پست یک درخواستِ media_info() می‌زند تا بازدیدِ ویدیو/ریل دقیق باشد
    (در خروجیِ فید، play_count گاهی خالی است).
    """
    kind, value = parse_target(target)
    owner: Dict[str, Any] = {}

    if kind == "media":
        logger.info("دریافت اطلاعات پست ...")
        media = cl.media_info(cl.media_pk_from_code(value))
        posts = [media]
        owner = normalize_user(_safe(media, "user", default=None))
    else:
        logger.info("دریافت شناسه‌ی کاربر %s ...", value)
        user_id = str(cl.user_id_from_username(value))
        logger.info("دریافت مشخصات پیج ...")
        owner = normalize_user(cl.user_info(user_id))
        posts = _collect_media(cl, user_id, amount, include_reels)
        if not posts:
            logger.warning("هیچ پستی برای %s پیدا نشد (پیج خصوصی؟ یا فقط ریل دارد؟)", value)

    results = []
    for index, media in enumerate(posts, start=1):
        rich = media
        if detail:
            logger.info("دریافت آمار دقیقِ پست %d از %d ...", index, len(posts))
            try:
                rich = cl.media_info(str(_safe(media, "pk", "id", default="")))
            except Exception as exc:
                logger.debug("جزئیات پست در دسترس نبود (%s)؛ از داده‌ی فید استفاده می‌شود.", exc)
                rich = media
        results.append(media_stats(rich))

    return {
        "requested": target,
        "owner": owner,
        "count": len(results),
        "total_likes": sum(p["likes"] or 0 for p in results),
        "total_comments": sum(p["comments"] for p in results),
        "total_views": sum(p["views"] for p in results),
        "posts": results,
    }


def send_email_code(cl: Client, email: str) -> Dict[str, Any]:
    return cl.send_confirm_email(email)


def confirm_email_code(cl: Client, email: str, code: str) -> Dict[str, Any]:
    return cl.confirm_email(email, code)


def send_phone_code(cl: Client, phone_number: str) -> Dict[str, Any]:
    return cl.send_confirm_phone_number(phone_number)


def confirm_phone_code(cl: Client, phone_number: str, code: str) -> Dict[str, Any]:
    return cl.confirm_phone_number(phone_number, code)
