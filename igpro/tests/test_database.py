import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from bot.database import Database  # noqa: E402


@pytest.fixture()
def db(tmp_path):
    d = Database(tmp_path / "test.db")
    yield d
    d.close()


def test_first_user_becomes_admin(db):
    row = db.ensure_user(111, "owner", "Owner")
    assert row["is_admin"] == 1
    assert db.admin_id() == 111
    row2 = db.ensure_user(222, "other", "Other")
    assert row2["is_admin"] == 0
    assert db.is_authorized(111)
    assert not db.is_authorized(222)


def test_forced_admin_id_wins(db):
    # BOT_ADMIN_ID ست شده: کاربرِ اول دیگر مدیر نیست
    row = db.ensure_user(333, "rando", "R", forced_admin_id=999)
    assert row["is_admin"] == 0
    row2 = db.ensure_user(999, "owner", "O", forced_admin_id=999)
    assert row2["is_admin"] == 1
    assert db.admin_id() == 999


def test_grant_and_expire(db):
    db.ensure_user(111, "a", "A")
    db.ensure_user(222, "b", "B")
    assert not db.is_authorized(222)
    until = db.grant_access(222, 1)  # ۱ ساعت
    assert db.is_authorized(222)
    # شبیه‌سازی گذشتِ زمان: تا بعد از انقضا
    db._conn.execute(
        "UPDATE users SET access_until = ? WHERE telegram_id = 222",
        (time.time() - 10,),
    )
    db._conn.commit()
    assert not db.is_authorized(222)
    # لغو
    db.grant_access(222, 1)
    assert db.is_authorized(222)
    db.revoke_access(222)
    assert not db.is_authorized(222)
    assert until > time.time()


def test_grant_forever(db):
    db.ensure_user(222, "b", "B")
    db.grant_access(222, None)
    assert db.is_authorized(222)


def test_access_requests(db):
    db.ensure_user(222, "b", "B")
    rid = db.add_access_request(222)
    assert db.has_pending_request(222)
    # درخواست تکراری همان شناسه را می‌دهد
    assert db.add_access_request(222) == rid
    db.resolve_access_request(222, "granted")
    assert not db.has_pending_request(222)


def test_history_crud(db):
    db.ensure_user(222, "b", "B")
    hid = db.add_history(222, "page", "nima.arish", "ok", "<b>html</b>", {"posts": 2})
    assert hid == 1
    item = db.get_history(hid)
    assert item["target"] == "nima.arish"
    assert "html" in item["result_html"]
    rows = db.list_history(222)
    assert len(rows) == 1
    assert db.count_history(222) == 1
    # کاربر دیگر چیزی نمی‌بیند
    db.add_history(333, "page", "x", "ok", "y", {})
    assert db.count_history(222) == 1
    assert db.list_history(333, 0, 10)[0]["target"] == "x"
    # صفحه‌بندی
    for i in range(15):
        db.add_history(222, "tag", f"t{i}", "ok", "x", {})
    page0 = db.list_history(222, 0, 10)
    page1 = db.list_history(222, 10, 10)
    assert len(page0) == 10 and len(page1) == 6
    assert page0[0]["id"] > page1[0]["id"]  # جدیدترین اول
