# -*- coding: utf-8 -*-
"""
اتصال به تلگرام (با اکانت کاربری، از طریق Telethon) و توابع کمکی خواندن/نوشتن state.
از اکانت کاربری استفاده می‌کنیم (نه بات معمولی) چون قابلیت‌هایی مثل خواندن
تاریخچه‌ی کانال‌های دیگر و زمان‌بندی پست (schedule) فقط با اکانت کاربری ممکنه.
"""

import json
import os
from telethon.sync import TelegramClient
from telethon.sessions import StringSession

import config


def get_client() -> TelegramClient:
    if not (config.API_ID and config.API_HASH and config.SESSION_STRING):
        raise RuntimeError(
            "TG_API_ID / TG_API_HASH / TG_SESSION_STRING تنظیم نشدن. "
            "اول باید get_session.py رو یک‌بار روی سیستم خودت اجرا کنی."
        )
    return TelegramClient(StringSession(config.SESSION_STRING), config.API_ID, config.API_HASH)


# ---------------------------------------------------------------------------
# خواندن/نوشتن فایل‌های وضعیت (JSON)
# ---------------------------------------------------------------------------

def load_json(path: str, default):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        content = f.read().strip()
        if not content:
            return default
        return json.loads(content)


def save_json(path: str, data) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
