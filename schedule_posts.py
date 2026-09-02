# -*- coding: utf-8 -*-
"""
اسکریپت اصلی زمان‌بندی. کاراش:
  ۱. چک می‌کنه پست‌هایی که قبلاً زمان‌بندی کرده هنوز سرجاشون هستن یا حذف شدن
     (اگه حذف شده باشن، جایگزین می‌کنه).
  ۲. اسلات‌های خالیِ ۲ روز آینده رو با محتوای مناسب (از صف پست‌ها یا صف کتاب‌ها) پر می‌کنه.

قوانین اسلات‌ها (به‌وقت تهران):
  ۱۰:۰۰ و ۱۳:۰۰  -> عمومی (بهترین پست موجود، به‌جز دسته‌های رزروشده)
  ۱۶:۰۰          -> یکی‌درمیون: سخن بزرگان / تصاویر ایران قدیم
  ۱۹:۰۰          -> یک پست حکایت/داستان/شعر + ۲ کتاب (۳ پست پشت‌سرهم)
  ۲۲:۰۰          -> دست‌نخورده، متعلق به خود کاربره

اجرا: python schedule_posts.py
پیشنهاد زمان‌بندی: هر ۶ ساعت (هماهنگ با process_books.py)
"""

import datetime
import os
import pytz

import config
from telegram_client import get_client, load_json, save_json
from text_utils import clean_channel_post_text
from telethon.tl.functions.messages import GetScheduledHistoryRequest

LOOKAHEAD_DAYS = 2
ALTERNATOR_FILE = config.STATE_DIR + "/alternator.json"


def tz_now():
    return datetime.datetime.now(pytz.timezone(config.TIMEZONE))


def build_future_slots():
    """لیست تمام اسلات‌های آینده (۲ روز جلوتر) به‌جز ساعت‌های رزروشده رو می‌سازه."""
    now = tz_now()
    slots = []
    for day_offset in range(LOOKAHEAD_DAYS + 1):
        day = now + datetime.timedelta(days=day_offset)
        for hour in config.POSTING_HOURS:
            if hour in config.RESERVED_HOURS:
                continue
            slot_dt = day.replace(hour=hour, minute=0, second=0, microsecond=0)
            if slot_dt > now:
                slots.append(slot_dt)
    return sorted(slots)


def pop_best(queue, category=None, exclude_categories=None):
    candidates = [
        item for item in queue
        if not item.get("used")
        and (category is None or item.get("category") == category)
        and (exclude_categories is None or item.get("category") not in exclude_categories)
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda x: -x.get("score", 0))
    item = candidates[0]
    item["used"] = True
    return item


def pop_next_book(book_queue):
    for item in book_queue:
        if not item.get("used"):
            item["used"] = True
            return item
    return None


def get_16_category(alternator: dict) -> str:
    last = alternator.get("last_16_category")
    if last == config.CATEGORY_SOKHAN_BOZORGAN:
        nxt = config.CATEGORY_AKS_IRAN_QADIM
    else:
        nxt = config.CATEGORY_SOKHAN_BOZORGAN
    alternator["last_16_category"] = nxt
    return nxt


def send_post(client, entity, item: dict, schedule_dt: datetime.datetime):
    """یک آیتم از صف پست‌های کانال‌ها رو (متن + عکس اگه داشت) زمان‌بندی می‌کنه."""
    source_msg = client.get_messages(item["channel"], ids=item["message_id"])
    final_text = clean_channel_post_text(item["text"])

    if item.get("has_photo") and source_msg and source_msg.photo:
        media_path = client.download_media(source_msg.photo)
        sent = client.send_file(entity, media_path, caption=final_text, schedule=schedule_dt)
    else:
        sent = client.send_message(entity, final_text, schedule=schedule_dt)

    return sent.id


def send_book(client, entity, book_item: dict, schedule_dt: datetime.datetime):
    sent = client.send_file(
        entity,
        book_item["file_path"],
        caption=book_item["caption"],
        force_document=True,
        schedule=schedule_dt,
    )
    # بعد از زمان‌بندی موفق، فایل محلی رو پاک می‌کنیم که ریپازیتوری سنگین نشه
    try:
        os.remove(book_item["file_path"])
    except OSError:
        pass
    return sent.id


def verify_and_clean_scheduled(client, entity, scheduled: list) -> list:
    """
    پست‌هایی که کاربر از بخش «پیام‌های زمان‌بندی‌شده» حذف کرده رو از لیست پاک می‌کنه.
    توجه: پیام‌های زمان‌بندی‌شده (که هنوز منتشر نشدن) با get_messages معمولی دیده
    نمی‌شن؛ باید از GetScheduledHistoryRequest استفاده کرد.
    """
    if not scheduled:
        return scheduled

    result = client(GetScheduledHistoryRequest(peer=entity, hash=0))
    scheduled_ids_on_telegram = {m.id for m in result.messages}

    now = tz_now()
    still_valid = []
    for s in scheduled:
        # فقط اونایی که هنوز باید در آینده منتشر بشن رو چک می‌کنیم؛
        # پست‌هایی که زمانشون گذشته یعنی احتمالاً قبلاً منتشر شدن (دیگه لازم نیست چک بشن)
        if s["message_id"] in scheduled_ids_on_telegram:
            still_valid.append(s)
        # اگه پیدا نشد و برای آینده بوده، یعنی حذف شده -> از لیست حذف می‌شه
        # (در دور بعدیِ حلقه‌ی پرکردن اسلات‌ها، خودکار جایگزین می‌شه)

    return still_valid


def main():
    now = tz_now()

    post_queue = load_json(config.POST_QUEUE_FILE, [])
    book_queue = load_json(config.BOOK_QUEUE_FILE, [])
    scheduled = load_json(config.SCHEDULED_FILE, [])
    alternator = load_json(ALTERNATOR_FILE, {})

    with get_client() as client:
        entity = client.get_entity(config.TARGET_CHANNEL)
      client.parse_mode = "html"

        # ۱. حذف پست‌هایی که کاربر دستی پاکشون کرده از لیست وضعیت
        scheduled = verify_and_clean_scheduled(client, entity, scheduled)
        occupied_slots = {s["slot_key"] for s in scheduled}

        # ۲. پر کردن اسلات‌های خالی
        future_slots = build_future_slots()

        for slot_dt in future_slots:
            hour = slot_dt.hour
            slot_key = slot_dt.isoformat()

            if hour == 16:
                base_key = f"16-{slot_dt.date()}"
                if base_key in occupied_slots:
                    continue
                category = get_16_category(alternator)
                item = pop_best(post_queue, category=category)
                if not item:
                    continue
                msg_id = send_post(client, entity, item, slot_dt)
                scheduled.append({"slot_key": base_key, "message_id": msg_id, "type": "post"})
                occupied_slots.add(base_key)

            elif hour == 19:
                base_key = f"19-{slot_dt.date()}"
                if base_key in occupied_slots:
                    continue

                # پست حکایت/داستان/شعر
                hekayat_item = pop_best(post_queue, category=config.CATEGORY_HEKAYAT)
                if hekayat_item:
                    msg_id = send_post(client, entity, hekayat_item, slot_dt)
                    scheduled.append({"slot_key": base_key, "message_id": msg_id, "type": "post"})
                    occupied_slots.add(base_key)

                # دو کتاب پشت‌سرهم (۲ دقیقه فاصله تا با هم تداخل نکنن)
                for i in range(config.BOOKS_PER_SLOT):
                    book_item = pop_next_book(book_queue)
                    if not book_item:
                        break
                    book_slot_dt = slot_dt + datetime.timedelta(minutes=2 * (i + 1))
                    book_slot_key = f"19-book-{slot_dt.date()}-{i}"
                    if book_slot_key in occupied_slots:
                        continue
                    msg_id = send_book(client, entity, book_item, book_slot_dt)
                    scheduled.append({"slot_key": book_slot_key, "message_id": msg_id, "type": "book"})
                    occupied_slots.add(book_slot_key)

            else:  # ۱۰ و ۱۳ -> عمومی، به‌جز دسته‌های رزروشده
                if slot_key in occupied_slots:
                    continue
                item = pop_best(post_queue, exclude_categories=config.RESERVED_CATEGORIES)
                if not item:
                    item = pop_best(post_queue)  # اگه چیز عمومی نبود، هرچی بهترینه
                if not item:
                    continue
                msg_id = send_post(client, entity, item, slot_dt)
                scheduled.append({"slot_key": slot_key, "message_id": msg_id, "type": "post"})
                occupied_slots.add(slot_key)

    save_json(config.POST_QUEUE_FILE, post_queue)
    save_json(config.BOOK_QUEUE_FILE, book_queue)
    save_json(config.SCHEDULED_FILE, scheduled)
    save_json(ALTERNATOR_FILE, alternator)

    print(f"وضعیت زمان‌بندی به‌روزرسانی شد. تعداد پست‌های زمان‌بندی‌شده‌ی فعال: {len(scheduled)}")


if __name__ == "__main__":
    main()
