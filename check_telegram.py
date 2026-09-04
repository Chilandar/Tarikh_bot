# -*- coding: utf-8 -*-
"""
تنها جایی که با تلگرام Bot API تماس می‌گیره تا پیام‌های جدید رو بخونه.

نکته‌ی مهم: تلگرام برای هر بات فقط یک صف پیام مشترک داره - نمی‌شه دو اسکریپت
جدا (با دو ردیاب offset جدا) هردو مستقل getUpdates صدا بزنن، چون هرکدوم که
زودتر/بیشتر صدا بزنه، پیام‌ها رو از صف تلگرام "تأیید‌شده" حساب می‌کنه و اون‌یکی
اسکریپت دیگه اصلاً نمی‌بینتشون. برای همین این تنها جاییه که getUpdates صدا زده
می‌شه، و کارهای دیگه (چک /scan، چک /start، پردازش کتاب) همه از همینجا انجام
می‌شن، با یک ردیاب مشترک (SHARED_UPDATE_ID_FILE).

اجرا: python check_telegram.py
"""

import os
import requests

import config
from telegram_client import load_json, save_json, send_bot_message
from text_utils import process_book_caption, build_book_filename

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


def download_telegram_file(file_id: str, dest_path: str):
    resp = requests.get(f"{API_BASE}/getFile", params={"file_id": file_id})
    resp.raise_for_status()
    file_path = resp.json()["result"]["file_path"]

    file_resp = requests.get(f"{FILE_BASE}/{file_path}")
    file_resp.raise_for_status()

    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    with open(dest_path, "wb") as f:
        f.write(file_resp.content)


def main():
    if not config.BOT_TOKEN or not config.OWNER_USER_ID:
        print("TG_BOT_TOKEN یا TG_OWNER_USER_ID تنظیم نشده - رد شد.")
        return

    ensure_bot_menu()

    last = load_json(config.SHARED_UPDATE_ID_FILE, {"update_id": 0})
    resp = requests.get(f"{API_BASE}/getUpdates", params={"offset": last["update_id"] + 1, "timeout": 10})
    resp.raise_for_status()
    updates = resp.json().get("result", [])

    scan_flag = load_json(config.SCAN_FLAG_FILE, {"pending": False})
    book_queue = load_json(config.BOOK_QUEUE_FILE, [])

    max_update_id = last["update_id"]
    books_added = 0

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
        if document:
            caption = msg.get("caption", "")
            original_filename = document.get("file_name", "book.pdf")
            ext = original_filename.split(".")[-1] if "." in original_filename else "pdf"

            parsed = process_book_caption(caption)
            new_filename = build_book_filename(parsed["book_title"], parsed["volume_number"], ext)
            dest_path = os.path.join(config.BOOKS_DIR, new_filename)

            download_telegram_file(document["file_id"], dest_path)

            book_queue.append({
                "file_path": dest_path,
                "filename": new_filename,
                "caption": parsed["clean_caption"],
                "used": False,
            })
            books_added += 1

    last["update_id"] = max_update_id
    save_json(config.SHARED_UPDATE_ID_FILE, last)
    save_json(config.SCAN_FLAG_FILE, scan_flag)
    save_json(config.BOOK_QUEUE_FILE, book_queue)

    print(f"{len(updates)} پیام بررسی شد. {books_added} کتاب جدید اضافه شد. دستور /scan در انتظار: {scan_flag['pending']}")
    if books_added:
        send_bot_message(f"📚 {books_added} کتاب جدید پردازش و به صف اضافه شد.")


if __name__ == "__main__":
    main()
