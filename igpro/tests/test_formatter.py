import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot import formatter as fmt  # noqa: E402


def sample_page() -> dict:
    return {
        "kind": "page",
        "username": "nima.arish",
        "user_id": "5",
        "profile": {
            "username": "nima.arish",
            "user_id": "5",
            "full_name": "Nima <Arish> & Co",
            "biography": "bio <script>alert(1)</script>",
            "followers": 43059,
            "following": 12,
            "media_count": 309,
            "is_private": False,
            "is_business": True,
            "is_verified": False,
            "category": "Musician",
            "external_url": "https://x.com/?a=1&b=2",
        },
        "posts": [
            {
                "media_pk": "m1",
                "shortcode": "CODE1",
                "kind": "ریل (Reel)",
                "url": "https://www.instagram.com/reel/CODE1/",
                "likes": 33614,
                "likes_hidden": False,
                "comments": 8817,
                "views": 1915204,
                "duration_sec": 82.07,
                "taken_at": "2026-08-27 16:21:00",
                "caption": "کپشن <test> «نیمه»",
                "viral": 47.06,
            },
            {
                "media_pk": "m2",
                "shortcode": "CODE2",
                "kind": "عکس",
                "url": "https://www.instagram.com/p/CODE2/",
                "likes": None,
                "likes_hidden": True,
                "comments": 12,
                "views": 0,
                "duration_sec": 0,
                "taken_at": "2026-08-28 19:22:11",
                "caption": "",
                "viral": None,
            },
        ],
        "total_likes": 33614,
        "total_comments": 8829,
        "total_views": 1915204,
        "top_post_index": 1,
        "top_viral": 47.06,
        "clips_note": "",
    }


def test_page_report_html_escaping():
    text = fmt.page_report_html(sample_page())
    assert "<script>" not in text
    assert "&lt;script&gt;" in text
    assert "@nima.arish" in text
    assert "43,059" in text
    assert "47.06" in text
    assert "پنهان" in text  # لایک‌های پنهان
    assert "instagram.com/reel/CODE1" in text
    assert fmt.TELEGRAM_TEXT_LIMIT >= len(text)


def test_hashtag_report_html():
    data = {
        "kind": "hashtag",
        "tag": "مدلینگ <x>",
        "media_count": 1234567,
        "posts": [
            {
                "media_pk": "h1",
                "shortcode": "HC1",
                "kind": "ریل (Reel)",
                "url": "https://www.instagram.com/reel/HC1/",
                "likes": 500,
                "likes_hidden": False,
                "comments": 100,
                "views": 40000,
                "duration_sec": 15.0,
                "taken_at": "2026-09-01 12:00:00",
                "caption": "caption",
                "owner": {"username": "owner1", "followers": 100},
                "viral": 415.0,
            }
        ],
        "total_likes": 500,
        "total_comments": 100,
        "total_views": 40000,
        "top_post_index": 1,
        "top_viral": 415.0,
    }
    text = fmt.hashtag_report_html(data)
    assert "#مدلینگ" in text
    assert "&lt;x&gt;" in text
    assert "1,234,567" in text
    assert "owner1" in text
    assert "415.00" in text


def test_duration_labels():
    assert fmt.duration_label(None) == "دائمی"
    assert "ساعت" in fmt.duration_label(6)
    assert "روز" in fmt.duration_label(24)
    assert "روز" in fmt.duration_label(168)


def test_long_text_truncated():
    data = sample_page()
    data["profile"]["biography"] = "x" * 6000
    text = fmt.page_report_html(data)
    assert len(text) <= fmt.TELEGRAM_TEXT_LIMIT


def test_keyboards_have_valid_callbacks():
    kb = fmt.menu_keyboard()
    datas = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert "m:page" in datas and "m:tag" in datas and "h:p:0" in datas
    approve = fmt.approve_keyboard(222)
    adata = [b.callback_data for row in approve.inline_keyboard for b in row]
    assert "a:ok:222:24" in adata and "a:no:222" in adata and "a:ok:222:f" in adata


def test_history_page_and_keyboard():
    rows = [
        {"id": 3, "kind": "page", "target": "nima.arish", "status": "ok", "created_at": 1757000000},
        {"id": 2, "kind": "hashtag", "target": "مدلینگ", "status": "error", "created_at": 1756900000},
    ]
    text = fmt.history_page_html(rows, 0, 2)
    assert "@nima.arish" in text and "#مدلینگ" in text
    kb = fmt.history_keyboard(rows, 0, has_prev=False, has_next=False)
    datas = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert "h:v:3" in datas and "h:v:2" in datas and "m:menu" in datas
