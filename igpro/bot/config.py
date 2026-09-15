"""تنظیمات ربات تلگرام (خوانده‌شده از env/.env)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from igclient.config import DEFAULT_STATE_DIR, env_int, env_str


@dataclass(frozen=True)
class BotSettings:
    token: str
    admin_id: Optional[int]      # BOT_ADMIN_ID — اگر ست باشد، فقط همین آیدی مدیر است
    state_dir: Path
    history_limit: int           # تعداد آیتم در هر صفحه‌ی هیزتوری
    hashtag_show: int            # تعداد پستِ نمایش‌داده‌شده در بخش هشتگ
    hashtag_scan: int            # تعداد پستِ اسکن‌شده از هشتگ (برای انتخاب بهینه)

    @classmethod
    def from_env(cls) -> "BotSettings":
        state_dir = Path(env_str("IG_STATE_DIR", str(DEFAULT_STATE_DIR))).expanduser()
        admin_raw = env_str("BOT_ADMIN_ID")
        admin_id: Optional[int] = None
        if admin_raw:
            try:
                admin_id = int(admin_raw)
            except ValueError:
                admin_id = None
        return cls(
            token=env_str("BOT_TOKEN"),
            admin_id=admin_id,
            state_dir=state_dir,
            history_limit=max(3, min(env_int("BOT_HISTORY_LIMIT", 10), 50)),
            hashtag_show=max(1, min(env_int("BOT_HASHTAG_SHOW", 3), 10)),
            hashtag_scan=max(3, min(env_int("BOT_HASHTAG_SCAN", 9), 50)),
        )
