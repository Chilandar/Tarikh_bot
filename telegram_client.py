# -*- coding: utf-8 -*-
"""
اتصال به تلگرام (با اکانت کاربری، از طریق Telethon) و توابع کمکی خواندن/نوشتن state.
از اکانت کاربری استفاده می‌کنیم (نه بات معمولی) چون قابلیت‌هایی مثل خواندن
تاریخچه‌ی کانال‌های دیگر و زمان‌بندی پست (schedule) فقط با اکانت کاربری ممکنه.
"""

import json
import os
import requests
from telethon.sync import TelegramClient
from telethon.sessions import StringSession

import config


def send_bot_message(text: str, reply_markup: dict = None) -> None:
    """یک پیام از طرف بات برای خود کاربر (OWNER_USER_ID) می‌فرسته - برای گزارش/تأیید."""
    if not config.BOT_TOKEN or not config.OWNER_USER_ID:
        return
    payload = {"chat_id": config.OWNER_USER_ID, "text": text}
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    try:
        requests.post(f"https://api.telegram.org/bot{config.BOT_TOKEN}/sendMessage", data=payload, timeout=10)
    except requests.RequestException:
        pass  # اگه گزارش نرسید، مهم نیست کل اجرا خراب بشه


def resolve_source_entity(client, channel_ref: str):
    """
    یک کانال منبع رو resolve می‌کنه - چه یوزرنیم معمولی (مثل "World5History")
    باشه، چه لینک دعوت خصوصی (مثل ".../joinchat/HASH" یا ".../+HASH").
    برای لینک دعوت، اگه قبلاً عضو نشده باشیم، اول عضو می‌شه.
    """
    if "joinchat/" in channel_ref or "/+" in channel_ref:
        from telethon.tl.functions.messages import ImportChatInviteRequest, CheckChatInviteRequest
        from telethon.tl.types import ChatInviteAlready

        if "joinchat/" in channel_ref:
            invite_hash = channel_ref.split("joinchat/")[-1]
        else:
            invite_hash = channel_ref.split("/+")[-1]
        invite_hash = invite_hash.strip("/")

        try:
            result = client(CheckChatInviteRequest(invite_hash))
            if isinstance(result, ChatInviteAlready):
                return result.chat
        except Exception:
            pass

        updates = client(ImportChatInviteRequest(invite_hash))
        return updates.chats[0]

    return client.get_entity(channel_ref)


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
