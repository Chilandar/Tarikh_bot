# -*- coding: utf-8 -*-
"""
تنها جایی که با تلگرام Bot API تماس می‌گیره تا پیام‌های جدید رو بخونه.

نکته‌ی مهم درباره‌ی فایل‌های بزرگ: بات‌های تلگرام فقط می‌تونن فایل‌های تا ۲۰
مگابایت رو با getFile دانلود کنن (این محدودیت خودِ تلگرامه، نه ما). برای
فایل‌های بزرگ‌تر (کتاب‌های حجیم)، از همون اکانت کاربری (Telethon) که برای
زمان‌بندی استفاده می‌شه کمک می‌گیریم، چون اکانت کاربری این محدودیت رو نداره.

مهم‌تر از همه: هر خطایی هم پیش بیاد (فایل بزرگ، شبکه، هرچی)، این اسکریپت
کرش نمی‌کنه - چون اگه کرش کنه، مرحله‌های بعدیِ workflow (گشتن تاریخچه،
زمان‌بندی) هم اصلاً اجرا نمی‌شن.

اجرا: python check_telegram.py
"""

import os
import requests

import config
from telegram_client import get_client, load_json, save_json, send_bot_message
from text_utils import process_book_caption, build_book_filename
from book_excerpts import generate_excerpt_items

API_BASE = f"https://api.telegram.org/bot{config.BOT_TOKEN}"
FILE_BASE = f"https://api.telegram.org/file/bot{config.BOT_TOKEN}"

SCAN_KEYBOARD = {
    "keyboard": [[{"text": config.SCAN_COMMAND_TEXT}]],
    "resize_keyboard": True,
    "is_persistent": True,
}


def ensure_bot_menu():
    try:
        requests.post(
            f"{API_BASE}/setMyCommands",
            json={"commands": [{"command": "scan", "description": "شروع گشتن تاریخچه‌ی کانال‌ها"}]},
            timeout=10,
        )
    except requests.RequestException:
        pass


def try_bot_api_download(file_id: str, dest_path: str) -> bool:
    """دانلود از طریق Bot API. اگه فایل بزرگ‌تر از ۲۰ مگابایت باشه، شکست می‌خوره."""
    try:
        resp = requests.get(f"{API_BASE}/getFile", params={"file_id": file_id}, timeout=30)
        resp.raise_for_status()
        file_path = resp.json()["result"]["file_path"]

        file_resp = requests.get(f"{FILE_BASE}/{file_path}", timeout=120)
        file_resp.raise_for_status()

        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        with open(dest_path, "wb") as f:
            f.write(file_resp.content)
        return True
    except requests.RequestException as e:
        print(f"⚠️ دانلود با Bot API شکست خورد ({e}) - فایل احتمالاً بزرگ‌تر از ۲۰ مگابایته.")
        return False


def try_telethon_download(document: dict, dest_path: str) -> bool:
    """
    فال‌بک برای فایل‌های بزرگ: از اکانت کاربری (که خودش پیام رو فرستاده به
    بات) توی همون گفتگوی خصوصی با بات دنبال فایل مشابه (بر اساس اسم و حجم)
    می‌گرده و دانلودش می‌کنه.
    """
    try:
        bot_user_id = int(config.BOT_TOKEN.split(":")[0])
    except (ValueError, IndexError):
        print("⚠️ نتونستیم آیدی بات رو از توکن استخراج کنیم.")
        return False

    target_name = document.get("file_name")
    target_size = document.get("file_size")

    try:
        with get_client() as client:
            for msg in client.iter_messages(bot_user_id, limit=50):
                if not msg.document:
                    continue
                msg_name = None
                for attr in msg.document.attributes:
                    if hasattr(attr, "file_name"):
                        msg_name = attr.file_name
                        break
                if target_name and msg_name != target_name:
                    continue
                if target_size and msg.document.size != target_size:
                    continue
                os.makedirs(os.path.dirname(dest_path), exist_ok=True)
                client.download_media(msg, file=dest_path)
                return True
    except Exception as e:
        print(f"⚠️ دانلود جایگزین با اکانت کاربری هم شکست خورد: {e}")
        return False

    print("⚠️ فایل مشابه توی گفتگوی خصوصی با بات پیدا نشد.")
    return False


def main():
    if not config.BOT_TOKEN or not config.OWNER_USER_ID:
        print("TG_BOT_TOKEN یا TG_OWNER_USER_ID تنظیم نشده - رد شد.")
        return

    ensure_bot_menu()

    last = load_json(config.SHARED_UPDATE_ID_FILE, {"update_id": 0})

    try:
        resp = requests.get(f"{API_BASE}/getUpdates", params={"offset": last["update_id"] + 1, "timeout": 10})
        resp.raise_for_status()
        updates = resp.json().get("result", [])
    except requests.RequestException as e:
        print(f"⚠️ getUpdates شکست خورد: {e} - این اجرا رد می‌شه.")
        return

    scan_flag = load_json(config.SCAN_FLAG_FILE, {"pending": False})
    book_queue = load_json(config.BOOK_QUEUE_FILE, [])
    post_queue = load_json(config.POST_QUEUE_FILE, [])

    max_update_id = last["update_id"]
    books_added = 0
    books_failed = 0

    for update in updates:
        max_update_id = max(max_update_id, update["update_id"])

        msg = update.get("message")
        if not msg:
            continue
        if msg.get("from", {}).get("id") != config.OWNER_USER_ID:
            continue

        text = (msg.get("text") or "").strip().lower()

        if text == "/start":
            send_bot_message(
                "سلام! برای شروع گشتن تاریخچه‌ی کانال‌ها، دکمه‌ی زیر رو بزن یا /scan رو بفرست.",
                reply_markup=SCAN_KEYBOARD,
            )
            continue

        if text == config.SCAN_COMMAND_TEXT:
            scan_flag["pending"] = True
            continue

        document = msg.get("document")
        if not document:
            continue

        # این خط مهمه: هر خطایی هم توی پردازش این یک فایل پیش بیاد، فقط همین
        # فایل رد می‌شه - کل اجرا کرش نمی‌کنه و بقیه‌ی پیام‌ها هم پردازش می‌شن.
        try:
            caption = msg.get("caption", "")
            original_filename = document.get("file_name", "book.pdf")
            ext = original_filename.split(".")[-1] if "." in original_filename else "pdf"

            parsed = process_book_caption(caption)
            new_filename = build_book_filename(parsed["book_title"], parsed["volume_number"], ext)
            dest_path = os.path.join(config.BOOKS_DIR, new_filename)

            downloaded = try_bot_api_download(document["file_id"], dest_path)
            if not downloaded:
                downloaded = try_telethon_download(document, dest_path)

            if not downloaded:
                books_failed += 1
                send_bot_message(f"❌ دانلود «{new_filename}» شکست خورد (فایل خیلی بزرگه یا مشکل دیگه‌ای پیش اومد).")
                continue

            book_queue.append({
                "file_path": dest_path,
                "filename": new_filename,
                "caption": parsed["clean_caption"],
                "used": False,
            })
            books_added += 1

            try:
                excerpt_items = generate_excerpt_items(config.GEMINI_API_KEY, dest_path, {
                    "book_title": parsed["book_title"],
                    "author_line": parsed.get("author_line"),
                    "translator_line": parsed.get("translator_line"),
                })
                if excerpt_items:
                    post_queue.extend(excerpt_items)
                    send_bot_message(f"📖 {len(excerpt_items)} گلچین از «{new_filename}» استخراج شد.")
            except Exception as e:
                print(f"⚠️ استخراج گلچین از کتاب با خطا مواجه شد: {e}")
        except Exception as e:
            books_failed += 1
            print(f"⚠️ پردازش یک فایل کتاب با خطا مواجه شد: {e}")
            send_bot_message(f"❌ پردازش یک کتاب با خطا مواجه شد: {e}")
            continue

    last["update_id"] = max_update_id
    save_json(config.SHARED_UPDATE_ID_FILE, last)
    save_json(config.SCAN_FLAG_FILE, scan_flag)
    save_json(config.BOOK_QUEUE_FILE, book_queue)
    save_json(config.POST_QUEUE_FILE, post_queue)

    print(f"{len(updates)} پیام بررسی شد. {books_added} کتاب اضافه شد، {books_failed} کتاب شکست خورد. /scan در انتظار: {scan_flag['pending']}")
    if books_added:
        send_bot_message(f"📚 {books_added} کتاب جدید پردازش و به صف اضافه شد.")


if __name__ == "__main__":
    main()
