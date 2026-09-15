"""رابط خط فرمان (CLI) پروژه."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from .client import AuthenticationError, IGClient, LoginMethod
from .config import Settings, mask
from . import profile as prof

EXIT_OK, EXIT_ERROR, EXIT_AUTH = 0, 1, 2


def _print(data: Any, as_json: bool, title: Optional[str] = None) -> None:
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2, default=str))
        return
    if title:
        print(f"\n--- {title} ---")
    if isinstance(data, dict):
        for key, value in data.items():
            print(f"{key}: {value}")
    elif isinstance(data, list):
        for item in data:
            print(json.dumps(item, ensure_ascii=False, default=str))
    else:
        print(data)


def _print_profile(info: Dict[str, Any]) -> None:
    yes = lambda b: "بله" if b else "خیر"
    print("\n--- اطلاعات پیج ---")
    print(f"نام کاربری:          @{info['username']}  (id: {info['user_id']})")
    print(f"نام کامل:            {info['full_name']}")
    print(f"تعداد فالوورها:      {info['followers']:,}")
    print(f"تعداد فالوینگ‌ها:     {info['following']:,}")
    print(f"تعداد کل پست‌ها:      {info['media_count']:,}")
    print(f"بیوگرافی:            {info['biography']}")
    print(f"خصوصی؟               {yes(info['is_private'])}")
    print(f"بیزینسی؟             {yes(info['is_business'])}")
    print(f"تیک آبی؟             {yes(info['is_verified'])}")
    if info["category"]:
        print(f"دسته‌بندی:            {info['category']}")
    if info["external_url"]:
        print(f"لینک بیو:            {info['external_url']}")


def _print_posts(data: Dict[str, Any]) -> None:
    owner = data.get("owner") or {}
    print("\n--- آخرین پست‌ها ---")
    if owner.get("username"):
        print(f"پیج: @{owner['username']}  (فالوورها: {owner.get('followers', 0):,} | "
              f"تعداد پست‌ها: {owner.get('media_count', 0):,})")
    for index, post in enumerate(data.get("posts", []), start=1):
        likes = "پنهان" if post["likes_hidden"] else f"{post['likes']:,}"
        views_text = f"{post['views']:,}" if post["views"] else "—"
        print(f"\n{index}) نوع: {post['kind']}")
        print(f"   لینک پست:   {post['url']}")
        print(f"   لایک: {likes} | کامنت: {post['comments']:,} | بازدید: {views_text}")
        if post["duration_sec"]:
            print(f"   مدت: {post['duration_sec']} ثانیه")
        print(f"   تاریخ: {post['taken_at']}")
        if post["caption"]:
            snippet = post["caption"].replace("\n", " ")
            print(f"   کپشن: {snippet[:120]}{'...' if len(snippet) > 120 else ''}")
    print(f"\nجمع کل این {data['count']} پست →  "
          f"لایک: {data['total_likes']:,} | کامنت: {data['total_comments']:,} | "
          f"بازدید: {data['total_views']:,}")


def build_parser() -> argparse.ArgumentParser:
    # پرچم‌های سراسری را هم قبل و هم بعد از زیردستور می‌پذیریم
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--session", help="مسیر فایل سشن (پیش‌فرض: state/session.json)")
    common.add_argument("--log-level", default=None, help="DEBUG/INFO/WARNING/ERROR")
    common.add_argument("--json", action="store_true", help="خروجی ماشین‌خوان (JSON)")
    common.add_argument("--fresh", action="store_true", help="نادیده گرفتن فایل سشن و ورود دوباره")
    common.add_argument("--method", choices=[m.value for m in LoginMethod],
                        help="اجبارِ روش ورود: session | sessionid | password")
    common.add_argument("--trace", action="store_true", help="نمایش کاملِ ردِ خطا (traceback)")

    parser = argparse.ArgumentParser(
        prog="igpro",
        parents=[common],
        description="ابزار حرفه‌ای و ایمنِ کار با اینستاگرام بر پایه‌ی instagrapi (فقط‌خواندنی)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("login", parents=[common], help="ورود و ذخیره‌ی سشن")
    sub.add_parser("whoami", parents=[common], help="نمایش مشخصات پیجِ خودتان")
    p_info = sub.add_parser("profile", parents=[common], help="مشخصات یک پیج")
    p_info.add_argument("username", help="نام کاربری (بدون @)")
    for name, help_text in (("followers", "لیست فالوورها"), ("following", "لیست فالوینگ‌ها")):
        p = sub.add_parser(name, parents=[common], help=help_text)
        p.add_argument("--user", help="نام کاربریِ مقصد (پیش‌فرض: خودتان)")
        p.add_argument("--amount", type=int, default=0, help="تعداد (۰ یعنی همه)")
    p_media = sub.add_parser("medias", parents=[common], help="آخرین پست‌ها")
    p_media.add_argument("username")
    p_media.add_argument("--amount", type=int, default=12)
    p_posts = sub.add_parser("posts", parents=[common], help="آخرین پست‌ها + لایک/کامنت/بازدید + لینک")
    p_posts.add_argument("target", help="نام کاربری یا لینک پروفایل یا لینک پست")
    p_posts.add_argument("--amount", type=int, default=2, help="تعداد پست (پیش‌فرض ۲)")
    p_posts.add_argument("--no-detail", action="store_true",
                         help="بدون درخواست اضافه برای بازدید دقیق (سریع‌تر)")
    p_posts.add_argument("--no-reels", action="store_true", help="ریل‌ها را لحاظ نکند")
    sub.add_parser("security", parents=[common], help="وضعیت امنیت حساب (ایمیل/شماره/۲FA)")
    p_mail = sub.add_parser("verify-email", parents=[common], help="ارسال/تایید کد ایمیل")
    p_mail.add_argument("email")
    p_mail.add_argument("--code", help="کد ۶ رقمی رسیده به ایمیل (اگر ندهید فقط ارسال می‌شود)")
    p_phone = sub.add_parser("verify-phone", parents=[common], help="ارسال/تایید کد پیامکی")
    p_phone.add_argument("phone")
    p_phone.add_argument("--code")
    sub.add_parser("session-info", parents=[common], help="نمایش وضعیت فایل سشن")
    sub.add_parser("export-sessionid", parents=[common], help="نمایش sessionid فعلی (محرمانه)")
    return parser


def main(argv: Optional[list] = None) -> int:
    import faulthandler
    import traceback

    args = build_parser().parse_args(argv)

    # نگهبانِ ضد‌هنگ: اگر برنامه بیش از این مدت گیر کند، ردِ پشته را چاپ و خارج می‌شود
    # (در غیر این صورت روی ویندوز فقط «هیچ خروجی‌ای» نمی‌بینید)
    watchdog = getattr(args, "watchdog", None) or Settings.from_env().watchdog_seconds
    if watchdog and watchdog > 0:
        faulthandler.dump_traceback_later(watchdog, exit=True)
    settings = Settings.from_env()
    if args.session:
        settings = Settings(**{**settings.__dict__, "session_path": Path(args.session).expanduser()})
    if args.log_level:
        settings = Settings(**{**settings.__dict__, "log_level": args.log_level.upper()})

    from .logging_setup import setup_logging
    logger = setup_logging(settings.log_level, settings.log_dir)
    logger.debug("تنظیمات barگذاری شد.")

    if args.command == "session-info":
        store_meta = IGClient(settings).store.metadata()
        return EXIT_OK if _print(store_meta or {"session": "نداریم"}, args.json, "وضعیت سشن") or store_meta else EXIT_ERROR

    ig = IGClient(settings)
    try:
        method = ig.login(
            method=LoginMethod(args.method) if args.method else None,
            fresh=args.fresh,
        )
    except AuthenticationError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return EXIT_AUTH
    except Exception as exc:
        print(f"❌ خطای غیرمنتظره در ورود: {type(exc).__name__}: {exc}", file=sys.stderr)
        _network_hint(exc)
        return EXIT_ERROR

    logger.info("ورود موفق از طریق %s", method.value)
    if args.command == "login":
        _print({"status": "ok", "method": method.value, "username": ig.username,
                "user_id": ig.user_id, "session_file": str(settings.session_path)},
               args.json, "ورود")
        return EXIT_OK

    try:
        if args.command == "whoami":
            info = ig.safe_call(prof.own_profile, ig.client)
            _print_profile(info) if not args.json else _print(info, True)
        elif args.command == "profile":
            info = ig.safe_call(prof.profile_by_username, ig.client, args.username)
            _print_profile(info) if not args.json else _print(info, True)
        elif args.command in ("followers", "following"):
            user_id = _target_user_id(ig, args.user)
            fn = prof.followers if args.command == "followers" else prof.following
            data = ig.safe_call(fn, ig.client, user_id, args.amount)
            _print(data, args.json, f"{args.command}: {len(data)} مورد")
        elif args.command == "posts":
            data = ig.safe_call(
                prof.last_posts, ig.client, args.target,
                amount=args.amount, detail=not args.no_detail, include_reels=not args.no_reels,
            )
            if args.json:
                _print(data, True)
            else:
                _print_posts(data)
        elif args.command == "medias":
            user_id = _target_user_id(ig, args.username)
            data = ig.safe_call(prof.medias, ig.client, user_id, args.amount)
            _print(data, args.json, f"پست‌ها: {len(data)} مورد")
        elif args.command == "security":
            _print(ig.safe_call(prof.security_info, ig.client), args.json, "امنیت حساب")
        elif args.command == "verify-email":
            if args.code:
                _print(ig.safe_call(prof.confirm_email_code, ig.client, args.email, args.code),
                       args.json, "تایید ایمیل")
            else:
                _print(ig.safe_call(prof.send_email_code, ig.client, args.email),
                       args.json, "ارسال کد ایمیل")
        elif args.command == "verify-phone":
            if args.code:
                _print(ig.safe_call(prof.confirm_phone_code, ig.client, args.phone, args.code),
                       args.json, "تایید شماره")
            else:
                _print(ig.safe_call(prof.send_phone_code, ig.client, args.phone),
                       args.json, "ارسال کد پیامکی")
        elif args.command == "export-sessionid":
            sid = ig.sessionid()
            print(mask(sid or "", keep=8) if not args.json else json.dumps({"sessionid": sid}, ensure_ascii=False))
            if sid and not args.json:
                print("برای نمایش کامل از --json استفاده کنید (مراقب افشای آن باشید).")
    except Exception as exc:
        print(f"❌ {type(exc).__name__}: {exc}", file=sys.stderr)
        if getattr(args, "trace", False) or str(getattr(args, "log_level", "")).upper() == "DEBUG":
            traceback.print_exc()
        else:
            print("   (برای جزئیات کامل: --trace)", file=sys.stderr)
        logger.debug("خطا در اجرای دستور", exc_info=True)
        return EXIT_ERROR
    finally:
        try:
            faulthandler.cancel_dump_traceback_later()
        except Exception:
            pass
    return EXIT_OK


def _network_hint(exc: BaseException) -> None:
    """راهنماییِ دقیق برای خطاهای شبکه/پروکسی (رایج‌ترین مشکل روی ویندوز و پشت VPN)."""
    text = f"{type(exc).__name__}: {exc}"
    if "ProxyError" in text or "Connection" in text or "Timeout" in text:
        print(
            "\n💡 راهنما: ترنسپورتِ پیش‌فرض (curl/HTTP2) پشتِ بیشتر پروکسی‌ها و VPNها شکست می‌خورد.\n"
            "   در فایل .env این دو خط را بگذارید و دوباره اجرا کنید:\n"
            "       IG_PRIVATE_TRANSPORT=requests\n"
            "       IG_PROXY=                      (اگر پروکسی ندارید خالی بگذارید)\n"
            "   اگر پروکسی/VPN دارید، آن را کامل وارد کنید:\n"
            "       IG_PROXY=http://user:pass@127.0.0.1:8080\n",
            file=sys.stderr,
        )


def _target_user_id(ig: IGClient, username: Optional[str]) -> str:
    if not username:
        if not ig.user_id:
            raise RuntimeError("شناسه‌ی کاربر در دسترس نیست.")
        return str(ig.user_id)
    return str(ig.safe_call(ig.client.user_id_from_username, username.lstrip("@").lower()))
