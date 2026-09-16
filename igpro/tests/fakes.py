"""FakeBot / FakeIgClient — تست بدون توکن تلگرام و بدون شبکه.

FakeBot دقیقاً همان سطح API را پیاده می‌کند که هندلرها صدا می‌زنند:
aiogram 3.31 shortcutها (msg.answer و ...) را به‌صورت `await bot(method)` اجرا می‌کند،
پس FakeBot فقط `__call__` را پیاده می‌کند و روش‌ها را ثبت می‌کند.
"""
from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from aiogram.types import Message

from instagrapi.exceptions import UserNotFound


class FakeBot:
    """جایگزین aiogram.Bot — همه‌ی فراخوانی‌ها را ثبت می‌کند."""

    def __init__(self, bot_id: int = 100000):
        self.bot_id = bot_id
        self.id = bot_id  # feed_update برای لاگ از bot.id استفاده می‌کند
        self.sent: List[Dict[str, Any]] = []      # sendMessage / sendPhoto ...
        self.edited: List[Dict[str, Any]] = []    # editMessageText
        self.callback_answers: List[Dict[str, Any]] = []
        self.deleted: List[Dict[str, Any]] = []
        self.registered_commands: List[tuple] = []  # آخرین منوی دستوراتِ ثبت‌شده
        self._next_id = 1
        self._messages: Dict[int, Dict[str, Any]] = {}

    # ------------------------------------------------- shortcutها مثل Bot واقعی
    async def send_message(self, chat_id: int, text: str, reply_markup: Any = None, **kw: Any) -> Any:
        from aiogram.methods import SendMessage
        return await self(SendMessage(chat_id=chat_id, text=text, reply_markup=reply_markup))

    async def edit_message_text(
        self,
        chat_id: Optional[int] = None,
        message_id: Optional[int] = None,
        text: str = "",
        reply_markup: Any = None,
        **kw: Any,
    ) -> Any:
        from aiogram.methods import EditMessageText
        return await self(
            EditMessageText(
                chat_id=chat_id, message_id=message_id, text=text, reply_markup=reply_markup
            )
        )

    async def set_my_commands(self, commands: Any, **kw: Any) -> Any:
        from aiogram.methods import SetMyCommands
        self.registered_commands = [
            (c.command, c.description) for c in commands
        ]
        return await self(SetMyCommands(commands=commands))

    # ------------------------------------------------------------ API اصلی
    async def __call__(self, method: Any) -> Any:
        name = getattr(method, "__api_method__", None) or type(method).__name__
        payload = method.model_dump(exclude_none=True) if hasattr(method, "model_dump") else {}
        if name == "sendMessage":
            mid = self._next_id
            self._next_id += 1
            self.sent.append({"method": name, "message_id": mid, **payload})
            self._messages[mid] = {
                "text": payload.get("text"),
                "reply_markup": payload.get("reply_markup"),
                "chat_id": payload.get("chat_id"),
            }
            msg = Message.model_validate(
                {
                    "message_id": mid,
                    "date": int(time.time()),
                    "chat": {"id": payload.get("chat_id"), "type": "private"},
                    "text": payload.get("text"),
                },
                context={"bot": self},
            )
            return msg
        if name == "editMessageText":
            self.edited.append({"method": name, **payload})
            mid = payload.get("message_id")
            if mid in self._messages:
                self._messages[mid]["text"] = payload.get("text")
                self._messages[mid]["reply_markup"] = payload.get("reply_markup")
            return True
        if name == "answerCallbackQuery":
            self.callback_answers.append(payload)
            return True
        if name == "deleteMessage":
            self.deleted.append(payload)
            return True
        return True

    # ------------------------------------------------------------ ابزار تست
    def last_sent(self, chat_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
        for item in reversed(self.sent):
            if chat_id is None or item.get("chat_id") == chat_id:
                return item
        return None

    def sent_to(self, chat_id: int) -> List[Dict[str, Any]]:
        return [i for i in self.sent if i.get("chat_id") == chat_id]

    def message_text(self, message_id: int) -> Optional[str]:
        item = self._messages.get(message_id)
        return item["text"] if item else None

    def has_callback(self, chat_id: int, prefix: str) -> bool:
        """آیا آخرین پیامِ ارسال‌شده به chat_id دکمه‌ای با این callback دارد؟"""
        item = self.last_sent(chat_id)
        if not item:
            return False
        markup = item.get("reply_markup")
        if markup is None:
            return False
        rows = markup.get("inline_keyboard") if isinstance(markup, dict) else getattr(markup, "inline_keyboard", [])
        for row in rows or []:
            for btn in row:
                data = btn.get("callback_data") if isinstance(btn, dict) else getattr(btn, "callback_data", None)
                if data and data.startswith(prefix):
                    return True
        return False


# =====================================================================
#  کلاینتِ جعلی instagrapi
# =====================================================================
def make_user(user_id: str, username: str, followers: int = 1000, **kw: Any) -> SimpleNamespace:
    base = dict(
        pk=user_id,
        id=user_id,
        user_id=user_id,
        username=username,
        full_name=kw.get("full_name", username.title()),
        biography=kw.get("biography", "bio <script> & co"),
        follower_count=followers,
        following_count=kw.get("following_count", 42),
        media_count=kw.get("media_count", 10),
        is_private=kw.get("is_private", False),
        is_business=kw.get("is_business", False),
        is_verified=kw.get("is_verified", False),
        category_name=kw.get("category_name", "Musician"),
        external_url=kw.get("external_url", ""),
        profile_pic_url_hd="http://pic/1.jpg",
        public_email="",
    )
    base.update(kw)
    return SimpleNamespace(**base)


def make_media(
    pk: str,
    code: str,
    owner: Optional[SimpleNamespace] = None,
    media_type: int = 2,
    product_type: str = "clips",
    likes: int = 100,
    comments: int = 10,
    views: int = 5000,
    duration: float = 10.0,
    taken_at: str = "2026-09-01 12:00:00",
    caption: str = "caption text",
) -> SimpleNamespace:
    return SimpleNamespace(
        pk=pk,
        id=pk,
        code=code,
        media_type=media_type,
        product_type=product_type,
        like_count=likes,
        comment_count=comments,
        play_count=views,
        view_count=None,
        like_and_view_counts_disabled=False,
        video_duration=duration,
        taken_at=taken_at,
        caption_text=caption,
        thumbnail_url=f"http://t/{pk}.jpg",
        user=owner,
    )


class FakeIgClient:
    """کلاینتِ جعلی: داده‌ها از دیکشنریِ داده‌شده، هر روش قابل برنامه‌ریزی است."""

    def __init__(self, users: Dict[str, Any], hashtags: Optional[Dict[str, Any]] = None):
        # users: {username: {"id": str, "user": SimpleNamespace, "medias": [...], "clips": [...]}}
        self.users = users
        self.hashtags = hashtags or {}  # {tag: {"info": obj, "medias": [...]}}
        self.calls: List[str] = []
        self.sleep_sec: float = 0.0

    def _sleep(self) -> None:
        import time as _t
        if self.sleep_sec:
            _t.sleep(self.sleep_sec)

    # ------------------------------------------------------- روش‌های instagrapi
    def user_id_from_username(self, username: str) -> str:
        self.calls.append(f"user_id_from_username:{username}")
        self._sleep()
        info = self.users.get(username)
        if info is None:
            raise UserNotFound(f"user {username} not found")
        return str(info["id"])

    def user_info(self, user_id: str) -> SimpleNamespace:
        self.calls.append(f"user_info:{user_id}")
        self._sleep()
        for info in self.users.values():
            if str(info["id"]) == str(user_id):
                return info["user"]
        raise UserNotFound(f"user {user_id} not found")

    def user_info_v1(self, user_id: str) -> SimpleNamespace:
        """مسیرِ خصوصیِ 3.0.2 — در فیک همان نتیجه‌ی user_info را دارد."""
        self.calls.append(f"user_info_v1:{user_id}")
        self._sleep()
        for info in self.users.values():
            if str(info["id"]) == str(user_id):
                return info["user"]
        raise UserNotFound(f"user {user_id} not found")

    def user_info_by_username_v1(self, username: str) -> SimpleNamespace:
        """اندپوینتِ خصوصیِ ``users/{username}/usernameinfo/`` در 3.0.2."""
        self.calls.append(f"user_info_by_username_v1:{username}")
        self._sleep()
        info = self.users.get(username)
        if info is None:
            raise UserNotFound(f"user {username} not found")
        return info["user"]

    def user_medias(self, user_id: str, amount: int = 0) -> List[Any]:
        self.calls.append(f"user_medias:{user_id}:{amount}")
        self._sleep()
        for info in self.users.values():
            if str(info["id"]) == str(user_id):
                medias = list(info.get("medias") or [])
                return medias[:amount] if amount else medias
        return []

    def user_clips(self, user_id: str, amount: int = 0) -> List[Any]:
        self.calls.append(f"user_clips:{user_id}:{amount}")
        self._sleep()
        for info in self.users.values():
            if str(info["id"]) == str(user_id):
                clips = list(info.get("clips") or [])
                return clips[:amount] if amount else clips
        return []

    def media_info(self, pk: str) -> SimpleNamespace:
        self.calls.append(f"media_info:{pk}")
        self._sleep()
        for info in self.users.values():
            for media in list(info.get("medias") or []) + list(info.get("clips") or []):
                if str(media.pk) == str(pk):
                    return media
        for tag in self.hashtags.values():
            for media in tag.get("medias") or []:
                if str(media.pk) == str(pk):
                    return media
        raise UserNotFound(f"media {pk} not found")

    def hashtag_info(self, name: str) -> SimpleNamespace:
        self.calls.append(f"hashtag_info:{name}")
        self._sleep()
        info = self.hashtags.get(name)
        if info is None:
            from instagrapi.exceptions import HashtagNotFound
            raise HashtagNotFound(name=name)
        return info["info"]

    def hashtag_medias_top(self, name: str, amount: int = 9) -> List[Any]:
        self.calls.append(f"hashtag_medias_top:{name}:{amount}")
        self._sleep()
        info = self.hashtags.get(name)
        if info is None:
            return []
        medias = list(info.get("medias") or [])
        return medias[:amount] if amount else medias


class FakeIG:
    """جایگزین IGClient برای engine (safe_call بدون بازتلاش/شبکه)."""

    def __init__(self, fake: FakeIgClient, login_side_effect: Optional[Exception] = None):
        self.client = fake
        self.login_method: Optional[str] = "password"  # فرض: از قبل لاگین
        self.login_calls = 0
        self._login_side_effect = login_side_effect

    def login(self, method: Optional[str] = None, fresh: bool = False) -> str:
        self.login_calls += 1
        if self._login_side_effect:
            raise self._login_side_effect
        self.login_method = "password"
        return self.login_method

    def safe_call(self, fn, *args, **kwargs):
        return fn(*args, **kwargs)


def make_page_fixture() -> FakeIgClient:
    """یک پیجِ نمونه: ۲ فالوور، ۲ پست + ۱ ریل."""
    owner = make_user("5", "nima.arish", followers=1000)
    m1 = make_media("m1", "CODE1", owner=owner, likes=300, comments=50, views=10000,
                    taken_at="2026-09-01 12:00:00")
    m2 = make_media("m2", "CODE2", owner=owner, media_type=1, product_type="feed",
                    likes=100, comments=5, views=0, taken_at="2026-08-01 12:00:00")
    c1 = make_media("c1", "CODEC1", owner=owner, likes=500, comments=80, views=20000,
                    taken_at="2026-09-02 12:00:00")
    return FakeIgClient(
        users={
            "nima.arish": {
                "id": "5",
                "user": owner,
                "medias": [m1, m2],
                "clips": [c1],
            }
        }
    )


def make_hashtag_fixture() -> FakeIgClient:
    o1 = make_user("6", "owner1", followers=100)
    o2 = make_user("7", "owner2", followers=10000)
    o3 = make_user("8", "owner3", followers=5000)
    m1 = make_media("h1", "HC1", owner=o1, likes=500, comments=100, views=40000)
    m2 = make_media("h2", "HC2", owner=o2, likes=900, comments=200, views=30000)
    m3 = make_media("h3", "HC3", owner=o3, likes=200, comments=40, views=9000)
    return FakeIgClient(
        users={
            "owner1": {"id": "6", "user": o1},
            "owner2": {"id": "7", "user": o2},
            "owner3": {"id": "8", "user": o3},
        },
        hashtags={
            "مدلینگ": {
                "info": SimpleNamespace(id="99", name="مدلینگ", media_count=1234567,
                                        profile_pic_url=None),
                "medias": [m1, m2, m3],
            }
        },
    )
