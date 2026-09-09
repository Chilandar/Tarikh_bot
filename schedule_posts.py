# -*- coding: utf-8 -*-
"""
اسکریپت اصلی زمان‌بندی. هر بار:
  ۱. مستقیماً از خودِ تلگرام می‌پرسه الان واقعاً چند پیام توی هر (روز، ساعت)
     زمان‌بندی شده - نه از یک فایل محلی که با جابه‌جایی دستیِ شما به‌روز نمی‌مونه.
  ۲. برای هر (روز، ساعت)ی که هنوز به ظرفیتش نرسیده، محتوای مناسب اضافه می‌کنه.

قوانین ظرفیت هر ساعت (به‌وقت تهران):
  ۱۰:۰۰ و ۱۳:۰۰  -> ظرفیت ۱ (عمومی)
  ۱۶:۰۰          -> ظرفیت ۱ (سخن بزرگان/تصاویر قدیم، یکی‌درمیون) + QUIZZES_PER_16_SLOT کوییز
                    (کوییزها از رویدادهای واقعیِ «امروز در تاریخ» ویکی‌پدیا ساخته می‌شن - quiz_web.py)
  ۱۹:۰۰          -> ظرفیت ۱ + BOOKS_PER_SLOT (حکایت + کتاب)
  ۲۲:۰۰          -> دست‌نخورده، کاملاً متعلق به خود کاربره

اجرا: python schedule_posts.py
"""

import datetime
import os
import random
import pytz

import config
from telegram_client import get_client, load_json, save_json, send_bot_message
from text_utils import clean_channel_post_text, get_visible_content_length, MIN_VISIBLE_CONTENT_LEN
import quiz_web
from telethon.tl.functions.messages import GetScheduledHistoryRequest, SendMediaRequest
from telethon.tl.types import InputMediaPoll, Poll, PollAnswer

LOOKAHEAD_DAYS = 14
ALTERNATOR_FILE = config.STATE_DIR + "/alternator.json"


def tz_now():
    return datetime.datetime.now(pytz.timezone(config.TIMEZONE))


def hour_capacity(hour: int) -> int:
    if hour == 16:
        return 1 + config.QUIZZES_PER_16_SLOT
    if hour == 19:
        return 1 + config.BOOKS_PER_SLOT
    return 1  # اسلات‌های عمومی (۱۰، ۱۳)


def get_live_hour_counts(client, entity):
    """
    تنها منبع حقیقتِ «این اسلات پره یا نه»: می‌شمره الان واقعاً چند پیام توی
    هر (روز، ساعت) روی تلگرام زمان‌بندی شده - چه ربات گذاشته باشتش چه خودِ
    کاربر دستی جابه‌جا/اضافه کرده باشه.
    """
    result = client(GetScheduledHistoryRequest(peer=entity, hash=0))
    tz = pytz.timezone(config.TIMEZONE)
    now = tz_now()
    counts = {}
    for m in result.messages:
        dt = m.date.astimezone(tz)
        if dt <= now:
            continue
        key = (dt.date(), dt.hour)
        counts[key] = counts.get(key, 0) + 1
    return counts


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


def has_media(item: dict) -> bool:
    return bool(item.get("media_type")) or bool(item.get("has_photo"))


def send_post(client, entity, item: dict, schedule_dt: datetime.datetime):
    final_text = clean_channel_post_text(item["text"])

    if get_visible_content_length(item["text"]) < MIN_VISIBLE_CONTENT_LEN:
        return None  # محافظ نهایی: اگه عملاً محتوایی نمونده، پست نمی‌شه

    if has_media(item):
        source_msg = client.get_messages(item["channel"], ids=item["message_id"])
        if not source_msg or not source_msg.media:
            return None
        media_path = client.download_media(source_msg)
        if not media_path:
            return None
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
    try:
        os.remove(book_item["file_path"])
    except OSError:
        pass
    return sent.id


def send_quiz_poll(client, entity, quiz: dict, schedule_dt: datetime.datetime):
    """
    یک Poll تعاملی از نوع quiz می‌سازه؛ با همون حساب کاربری‌ای که ربات بهش
    وصله (نه Bot API). فقط «ترتیب تصادفی گزینه‌ها» (که قبل از این تابع انجام
    شده) و «تنظیم پاسخ درست» (quiz=True) فعاله - چندجوابی و نمایش عمومی
    رأی‌دهنده‌ها خاموشه. توضیحاتِ کوییز همیشه دقیقاً امضای کاناله.
    """
    answers = [
        PollAnswer(text=opt, option=bytes([i]))
        for i, opt in enumerate(quiz["options"])
    ]
    poll = Poll(
        id=random.randint(1, 2**31 - 1),
        question=quiz["question"],
        answers=answers,
        quiz=True,
        multiple_choice=False,
        public_voters=False,
    )
    media = InputMediaPoll(
        poll=poll,
        correct_answers=[bytes([quiz["correct_index"]])],
        solution=config.SIGNATURE,
        solution_entities=[],
    )
    result = client(SendMediaRequest(peer=entity, media=media, message="", schedule_date=schedule_dt))
    for upd in result.updates:
        if hasattr(upd, "message") and hasattr(upd.message, "id"):
            return upd.message.id
    return None


def try_send_with_retries(pop_func, send_func, client, entity, slot_dt, max_attempts=3):
    """
    یک آیتم رو از صف برمی‌داره و ارسالش می‌کنه؛ اگه ارسال شکست خورد، آیتم
    رو به حالت "استفاده‌نشده" برمی‌گردونه (تا برای دفعه‌ی بعد از دست نره) و
    آیتم بعدی رو امتحان می‌کنه - تا max_attempts بار.
    """
    for _ in range(max_attempts):
        item = pop_func()
        if not item:
            return None, None
        msg_id = send_func(item, slot_dt)
        if msg_id:
            return item, msg_id
        item["used"] = False  # ارسال شکست خورد - این آیتم رو از دست ندیم
    return None, None


def fill_hour(client, entity, day, hour, already, need, post_queue, book_queue, alternator, counters):
    tz = pytz.timezone(config.TIMEZONE)
    slot_dt = tz.localize(datetime.datetime.combine(day, datetime.time(hour=hour)))
    filled_here = 0

    if hour == 16:
        filled_here = 0
        if already == 0:
            category = get_16_category(alternator)
            item, msg_id = try_send_with_retries(
                lambda: pop_best(post_queue, category=category),
                lambda it, dt: send_post(client, entity, it, dt),
                client, entity, slot_dt,
            )
            if msg_id:
                counters["posts"] += 1
                filled_here += 1
            else:
                print(f"⚠️ ساعت {hour} روز {day}: پستی از دسته‌ی {category} توی صف پیدا/ارسال نشد.")
        else:
            print(f"ℹ️ ساعت {hour} روز {day}: پایه از قبل پر بود، فقط کوییزها چک می‌شن.")

        quiz_need = need - filled_here
        if quiz_need > 0:
            quizzes = quiz_web.build_daily_quizzes(day, count=quiz_need)
            for j, quiz in enumerate(quizzes):
                idx = already + filled_here + j
                q_dt = slot_dt + datetime.timedelta(minutes=2 * idx)
                msg_id = send_quiz_poll(client, entity, quiz, q_dt)
                if msg_id:
                    counters["quizzes"] += 1
                else:
                    print(f"⚠️ ساعت {hour} روز {day}: ارسال یک کوییز شکست خورد.")
            if len(quizzes) < quiz_need:
                print(f"⚠️ ساعت {hour} روز {day}: فقط {len(quizzes)} از {quiz_need} کوییز ساخته شد.")

    elif hour == 19:
        if already == 0:
            item, msg_id = try_send_with_retries(
                lambda: pop_best(post_queue, category=config.CATEGORY_HEKAYAT),
                lambda it, dt: send_post(client, entity, it, dt),
                client, entity, slot_dt,
            )
            if msg_id:
                counters["posts"] += 1
                filled_here += 1
            else:
                print(f"⚠️ ساعت {hour} روز {day}: پست حکایتی توی صف پیدا/ارسال نشد.")
        remaining = need - filled_here
        for j in range(max(remaining, 0)):
            idx = already + filled_here + j
            b_dt = slot_dt + datetime.timedelta(minutes=2 * idx)
            book_item = pop_next_book(book_queue)
            if not book_item:
                print(f"⚠️ ساعت {hour} روز {day}: کتابی توی صف نبود.")
                break
            msg_id = send_book(client, entity, book_item, b_dt)
            if msg_id:
                counters["books"] += 1
            else:
                book_item["used"] = False
                print(f"⚠️ ساعت {hour} روز {day}: ارسال کتاب شکست خورد.")

    else:  # اسلات‌های عمومی (۱۰، ۱۳)
        for j in range(need):
            idx = already + j
            g_dt = slot_dt + datetime.timedelta(minutes=2 * idx) if idx else slot_dt
            item, msg_id = try_send_with_retries(
                lambda: pop_best(post_queue, exclude_categories=config.RESERVED_CATEGORIES),
                lambda it, dt: send_post(client, entity, it, dt),
                client, entity, g_dt,
            )
            if msg_id:
                counters["posts"] += 1
            elif item is None:
                print(f"⚠️ ساعت {hour} روز {day}: هیچ پستِ عمومی‌ای توی صف نبود.")
                break
            else:
                print(f"⚠️ ساعت {hour} روز {day}: یک پست عمومی پیدا شد ولی ارسالش شکست خورد.")


def main():
    post_queue = load_json(config.POST_QUEUE_FILE, [])
    book_queue = load_json(config.BOOK_QUEUE_FILE, [])
    alternator = load_json(ALTERNATOR_FILE, {})

    counters = {"posts": 0, "books": 0, "quizzes": 0}

    with get_client() as client:
        entity = client.get_entity(config.TARGET_CHANNEL)
        client.parse_mode = "html"

        live_counts = get_live_hour_counts(client, entity)
        now = tz_now()

        for day_offset in range(LOOKAHEAD_DAYS + 1):
            day = (now + datetime.timedelta(days=day_offset)).date()
            for hour in config.POSTING_HOURS:
                if hour in config.RESERVED_HOURS:
                    continue  # ساعت ۲۲ - کاملاً دست‌نخورده، متعلق به خود کاربره

                tz = pytz.timezone(config.TIMEZONE)
                slot_dt = tz.localize(datetime.datetime.combine(day, datetime.time(hour=hour)))
                if slot_dt <= now:
                    continue

                already = live_counts.get((day, hour), 0)
                capacity = hour_capacity(hour)
                need = capacity - already
                if need <= 0:
                    continue

                fill_hour(client, entity, day, hour, already, need,
                          post_queue, book_queue, alternator, counters)

    save_json(config.POST_QUEUE_FILE, post_queue)
    save_json(config.BOOK_QUEUE_FILE, book_queue)
    save_json(ALTERNATOR_FILE, alternator)

    summary = (
        f"وضعیت زمان‌بندی به‌روزرسانی شد. {counters['posts']} پست، {counters['books']} کتاب و "
        f"{counters['quizzes']} کوییز جدید زمان‌بندی شد (بر اساس شمارش زنده‌ی تلگرام)."
    )
    print(summary)
    if any(counters.values()):
        send_bot_message(f"📅 {summary}")


if __name__ == "__main__":
    main()
