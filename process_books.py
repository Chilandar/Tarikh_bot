# -*- coding: utf-8 -*-
"""
این اسکریپت با توکن بات (Bot API) چک می‌کنه که آیا فایل کتاب جدیدی برایش
فرستاده شده یا نه. فقط پیام‌هایی که از آیدی خودِ کاربر (OWNER_USER_ID) اومده
باشن پردازش می‌شن. برای هر فایل جدید: کپشن پردازش می‌شه، فایل با نام جدید
دانلود و ذخیره می‌شه، و یک رکورد به book_queue.json اضافه می‌شه.

اجرا: python process_books.py
پیشنهاد زمان‌بندی: ۲ تا ۴ بار در روز
"""

import os
import requests

import config
from telegram_client import load_json, save_json
from text_utils import process_book_caption, build_book_filename

API_BASE = f"https://api.telegram.org/bot{config.BOT_TOKEN}"
FILE_BASE = f"https://api.telegram.org/file/bot{config.BOT_TOKEN}"


def get_updates(offset: int):
    resp = requests.get(f"{API_BASE}/getUpdates", params={"offset": offset, "timeout": 10})
    resp.raise_for_status()
    return resp.json().get("result", [])


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
        raise RuntimeError("TG_BOT_TOKEN و TG_OWNER_USER_ID باید تنظیم شده باشن.")

    last_update = load_json(config.LAST_BOOK_MSG_ID_FILE, {"update_id": 0})
    book_queue = load_json(config.BOOK_QUEUE_FILE, [])

    updates = get_updates(offset=last_update["update_id"] + 1)

    added = 0
    max_update_id = last_update["update_id"]

    for update in updates:
        max_update_id = max(max_update_id, update["update_id"])

        msg = update.get("message")
        if not msg:
            continue

        sender_id = msg.get("from", {}).get("id")
        if sender_id != config.OWNER_USER_ID:
            continue  # فقط پیام‌های خود کاربر پردازش می‌شن

        document = msg.get("document")
        if not document:
            continue  # این پیام فایل نداره

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
        added += 1

    last_update["update_id"] = max_update_id
    save_json(config.LAST_BOOK_MSG_ID_FILE, last_update)
    save_json(config.BOOK_QUEUE_FILE, book_queue)

    print(f"{added} کتاب جدید پردازش و به صف اضافه شد. مجموع صف کتاب‌ها: {len(book_queue)}")


if __name__ == "__main__":
    main()
