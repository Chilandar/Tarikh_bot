# -*- coding: utf-8 -*-
"""
این اسکریپت رو فقط یک‌بار، روی سیستم شخصی خودت (نه روی GitHub) اجرا کن.
ازت شماره تلفن و کد تأیید تلگرام رو می‌پرسه، و در انتها یک "session string"
تولید می‌کنه که باید توی GitHub Secrets به اسم TG_SESSION_STRING ذخیره‌ش کنی.

اجرا: python get_session.py
"""

from telethon.sync import TelegramClient
from telethon.sessions import StringSession

API_ID = int(input("API ID (از my.telegram.org): "))
API_HASH = input("API HASH (از my.telegram.org): ").strip()

with TelegramClient(StringSession(), API_ID, API_HASH) as client:
    session_string = client.session.save()
    print("\n\n=== این رشته رو کپی کن و توی GitHub Secrets با نام TG_SESSION_STRING ذخیره کن ===\n")
    print(session_string)
    print("\n=====================================================================\n")

    me = client.get_me()
    print(f"وارد شدی به‌عنوان: {me.first_name} (@{me.username})")
    print("این آیدی عددی رو هم برای پیدا کردن chat_id لازم داری:", me.id)
