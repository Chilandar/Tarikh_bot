# -*- coding: utf-8 -*-
"""
اسکریپت اصلی زمان‌بندی. هر بار:
  ۱. مستقیماً از خودِ تلگرام می‌پرسه الان واقعاً چند پیام (و از چه نوعی -
     کتاب/کوییز/عادی) توی هر (روز، ساعت) زمان‌بندی شده - نه از یک فایل محلی
     که با جابه‌جایی/حذفِ دستیِ شما به‌روز نمی‌مونه.
  ۲. برای هر (روز، ساعت)، هر نوع محتوا (پست عادی، کوییز، کتاب) رو *جداگانه*
     تا ظرفیتِ خودش پر می‌کنه - نه یک عدد ترکیبی، تا هیچ‌وقت مثلاً به‌خاطرِ
     خالی‌موندنِ اسلاتِ حکایت، یک کتابِ اضافه (سوم) جایگزینش نشه.

قوانین ظرفیت هر ساعت (به‌وقت تهران):
  ۱۰:۰۰ و ۱۳:۰۰  -> ۱ پستِ عمومی
  ۱۶:۰۰          -> ۱ پست (سخن بزرگان/تصاویر قدیم) + QUIZZES_PER_16_SLOT کوییز
  ۱۹:۰۰          -> ۱ پستِ حکایت + BOOKS_PER_SLOT کتاب
  ۲۲:۰۰          -> دست‌نخورده، کاملاً متعلق به خود کاربره

نکته‌ی مهمِ کوییز: چون شمارش مستقیم از خودِ تلگرام خونده می‌شه، اگه شما دستی
یک کوییزِ زمان‌بندی‌شده رو حذف کنید، دفعه‌ی بعد خودش می‌بینه جا خالی شده و
یک کوییزِ جدید می‌سازه - کاملاً خودکار، بدون نیاز به /scan.

اجرا: python schedule_posts.py
"""

import datetime
import os
import random
import pytz

import config
from telegram_client import get_client, load_json, save_json, send_bot_message
from text_utils import clean_channel_post_text, get_visible_content_length, MIN_VISIBLE_CONTENT_LEN
from quiz_web import build_daily_quizzes
from telethon.tl.functions.messages import GetScheduledHistoryRequest, SendMediaRequest
from telethon.tl.types import Poll, PollAnswer, InputMediaPoll, TextWithEntities

LOOKAHEAD_DAYS = 9
ALTERNATOR_FILE = config.STATE_DIR + "/alternator.json"


def tz_now():
    return datetime.datetime.now(pytz.timezone(config.TIMEZONE))


def get_live_hour_counts(client, entity):
    """
    برای هر (روز، ساعت)، تعداد پیام‌های *هر نوع* رو جدا می‌شمره - نه یک عددِ
    کلی. "document" یعنی کتاب (فایل)، "poll" یعنی کوییز، "other" یعنی هر
    پستِ معمولی (متن/عکس/فیلم - یعنی سخن‌بزرگان/تصاویرقدیم/حکایت/عمومی).
    این تفکیک لازمه تا مثلاً خالی‌موندنِ جای حکایت، اشتباهی با یک کتابِ
    اضافه پر نشه.
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
        entry = counts.setdefault(key, {"document": 0, "poll": 0, "other": 0})
        if getattr(m, "document", None):
            entry["document"] += 1
        elif getattr(m, "poll", None):
            entry["poll"] += 1
        else:
            entry["other"] += 1
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
        return None

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


def send_quiz(client, entity, quiz: dict, schedule_dt: datetime.datetime):
    answers = [
        PollAnswer(text=TextWithEntities(text=opt, entities=[]), option=bytes([i]))
        for i, opt in enumerate(quiz["options"])
    ]
    poll = Poll(
        id=random.getrandbits(63),
        question=TextWithEntities(text=quiz["question"], entities=[]),
        answers=answers,
        quiz=True,
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


def fill_hour(client, entity, day, hour, counts, post_queue, book_queue, alternator, counters):
    tz = pytz.timezone(config.TIMEZONE)
    slot_dt = tz.localize(datetime.datetime.combine(day, datetime.time(hour=hour)))

    other_have = counts.get("other", 0)
    document_have = counts.get("document", 0)
    poll_have = counts.get("poll", 0)

    if hour == 16:
        if other_have < 1:
            category = get_16_category(alternator)
            item = pop_best(post_queue, category=category)
            if item:
                msg_id = send_post(client, entity, item, slot_dt)
                if msg_id:
                    counters["posts"] += 1
                else:
                    item["used"] = False
                    print(f"⚠️ ساعت {hour} روز {day}: ارسال پست دسته {category} شکست خورد.")
            else:
                print(f"⚠️ ساعت {hour} روز {day}: پستی از دسته‌ی {category} توی صف نبود.")

        need_quizzes = config.QUIZZES_PER_16_SLOT - poll_have
        if need_quizzes > 0:
            quizzes = build_daily_quizzes(day, count=need_quizzes)
            if len(quizzes) < need_quizzes:
                print(f"⚠️ ساعت {hour} روز {day}: فقط {len(quizzes)} از {need_quizzes} کوییزِ لازم ساخته شد.")
            for j, quiz in enumerate(quizzes):
                q_dt = slot_dt + datetime.timedelta(minutes=2 * (poll_have + j + 1))
                msg_id = send_quiz(client, entity, quiz, q_dt)
                if msg_id:
                    counters["quizzes"] += 1

    elif hour == 19:
        if other_have < 1:
            hekayat_item = pop_best(post_queue, category=config.CATEGORY_HEKAYAT)
            if hekayat_item:
                msg_id = send_post(client, entity, hekayat_item, slot_dt)
                if msg_id:
                    counters["posts"] += 1
                else:
                    hekayat_item["used"] = False
                    print(f"⚠️ ساعت {hour} روز {day}: ارسال پست حکایت شکست خورد.")
            else:
                print(f"⚠️ ساعت {hour} روز {day}: پست حکایتی توی صف نبود.")

        need_books = config.BOOKS_PER_SLOT - document_have
        for j in range(max(need_books, 0)):
            book_item = pop_next_book(book_queue)
            if not book_item:
                print(f"⚠️ ساعت {hour} روز {day}: کتابی توی صف نبود.")
                break
            b_dt = slot_dt + datetime.timedelta(minutes=2 * (document_have + j))
            msg_id = send_book(client, entity, book_item, b_dt)
            if msg_id:
                counters["books"] += 1
            else:
                book_item["used"] = False
                print(f"⚠️ ساعت {hour} روز {day}: ارسال کتاب شکست خورد.")

    else:  # اسلات‌های عمومی (۱۰، ۱۳) - ظرفیت ۱
        if other_have < 1:
            item = pop_best(post_queue, exclude_categories=config.RESERVED_CATEGORIES)
            if item:
                msg_id = send_post(client, entity, item, slot_dt)
                if msg_id:
                    counters["posts"] += 1
                else:
                    item["used"] = False
                    print(f"⚠️ ساعت {hour} روز {day}: ارسال پست عمومی شکست خورد.")
            else:
                print(f"⚠️ ساعت {hour} روز {day}: هیچ پستِ عمومی‌ای توی صف نبود.")


def needs_anything(hour: int, counts: dict) -> bool:
    other_have = counts.get("other", 0)
    document_have = counts.get("document", 0)
    poll_have = counts.get("poll", 0)
    if hour == 16:
        return other_have < 1 or poll_have < config.QUIZZES_PER_16_SLOT
    if hour == 19:
        return other_have < 1 or document_have < config.BOOKS_PER_SLOT
    return other_have < 1


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

                counts = live_counts.get((day, hour), {"document": 0, "poll": 0, "other": 0})
                if not needs_anything(hour, counts):
                    continue

                fill_hour(client, entity, day, hour, counts, post_queue, book_queue, alternator, counters)

    save_json(config.POST_QUEUE_FILE, post_queue)
    save_json(config.BOOK_QUEUE_FILE, book_queue)
    save_json(ALTERNATOR_FILE, alternator)

    summary = (
        f"وضعیت زمان‌بندی به‌روزرسانی شد. {counters['posts']} پست، {counters['books']} کتاب، "
        f"و {counters['quizzes']} کوییز جدید زمان‌بندی شد (بر اساس شمارش زنده‌ی تلگرام)."
    )
    print(summary)
    if any(counters.values()):
        send_bot_message(f"📅 {summary}")


if __name__ == "__main__":
    main()
