"""ذخیره‌سازی امن و اتمیکِ سشن (کوکی‌ها + اثرانگشت دستگاه).

نکته‌ی مهم برگرفته از سورس instagrapi:
    load_settings() مقدار client.username را بازیابی نمی‌کند (فقط در login/login_by_sessionid
    ست می‌شود)، بنابراین هرگز به cl.username بعد از بارگذاری فایل تکیه نکنید.
    این ماژول متادیتا (username/user_id) را خودش ذخیره می‌کند تا آن را دور بزنیم.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Optional

from instagrapi import Client


class SessionStore:
    """نگهداری سشن در یک فایل JSON شامل متادیتا + تنظیماتِ کلاینت."""

    SCHEMA_VERSION = 1

    def __init__(self, path: Path, logger=None):
        self.path = Path(path)
        self.logger = logger

    # ---------- عملیات فایل ----------
    def exists(self) -> bool:
        return self.path.exists()

    def _chmod_private(self) -> None:
        try:
            os.chmod(self.path, 0o600)  # فقط مالک: جلوگیری از نشت sessionid
        except OSError:  # ویندوز از chmod کامل پشتیبانی نمی‌کند
            pass

    def read(self) -> Optional[Dict[str, Any]]:
        if not self.path.exists():
            return None
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            if self.logger:
                self.logger.warning("فایل سشن خراب است (%s)؛ حذف می‌شود.", exc)
            self.delete()
            return None
        if not isinstance(data, dict) or "settings" not in data:
            if self.logger:
                self.logger.warning("قالب فایل سشن نامعتبر است؛ حذف می‌شود.")
            self.delete()
            return None
        return data

    def write(self, client: Client, login_method: str) -> None:
        """ذخیره‌ی اتمیک: ابتدا فایل موقت، سپس جایگزینی (در صورت crash فایل سالم می‌ماند)."""
        payload = {
            "schema_version": self.SCHEMA_VERSION,
            "saved_at": time.time(),
            "login_method": login_method,
            "user_id": str(client.user_id or ""),
            "username": getattr(client, "username", None) or "",
            "device": client.device,  # فقط ۴ کلیدِ خلاصه: manufacturer/model/android
            "settings": client.get_settings(),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=str(self.path.parent), prefix=".sess-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fp:
                json.dump(payload, fp, ensure_ascii=False, indent=2)
                fp.flush()
                os.fsync(fp.fileno())
            os.replace(tmp_name, self.path)  # اتمیک
        finally:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
        self._chmod_private()

    def delete(self) -> None:
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            pass

    # ---------- بارگذاری در کلاینت ----------
    def apply_to(self, client: Client, override_app_version: bool = True) -> Optional[Dict[str, Any]]:
        """تزریق تنظیمات ذخیره‌شده به کلاینت؛ در صورت نبود، None برمی‌گرداند."""
        data = self.read()
        if not data:
            return None
        # override_app_version باعث می‌شود پروفایل اپلیکیشنِ پشتیبانی‌شده اعمال شود
        # (برای جلوگیری از خطای «CAA login requires bloks_versioning_id»)
        client.override_app_version = override_app_version
        client.set_settings(data["settings"])
        if data.get("username"):
            # دور زدن محدودیت load_settings که username را ست نمی‌کند
            client.username = data["username"]
        return data

    def metadata(self) -> Optional[Dict[str, Any]]:
        data = self.read()
        if not data:
            return None
        meta = {k: v for k, v in data.items() if k != "settings"}
        meta["age_hours"] = round((time.time() - data.get("saved_at", 0)) / 3600, 2)
        meta["path"] = str(self.path)
        meta["size_bytes"] = self.path.stat().st_size if self.path.exists() else 0
        return meta
