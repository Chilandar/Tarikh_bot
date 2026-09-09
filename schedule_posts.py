# -*- coding: utf-8 -*-
"""
اسکریپت اصلی زمان‌بندی. هر بار:
  ۱. اگه صفِ کوییز کم‌موجودی بود، خودکار (بدون نیاز به /scan) چندتا کوییزِ
     جدید از quiz_web.build_daily_quizzes می‌سازه و به صف اضافه می‌کنه.
  ۲. مستقیماً از خودِ تلگرام می‌پرسه الان واقعاً چند پیام توی هر (روز، ساعت)
     زمان‌بندی شده - نه از یک فایل محلی که با جابه‌جایی دستیِ شما به‌روز نمی‌مونه.
  ۳. برای هر (روز، ساعت)ی که هنوز به ظرفیتش نرسیده، محتوای مناسب اضافه می‌کنه.

قوانین ظرفیت هر ساعت (به‌وقت تهران):
  ۱۰:۰۰ و ۱۳:۰۰  -> ظرفیت ۱ (عمومی)
  ۱۶:۰۰          -> ظرفیت ۱ (سخن بزرگان/تصاویر قدیم) + QUIZZES_PER_16_SLOT کوییز
  ۱۹:۰۰          -> ظرفیت ۱ + BOOKS_PER_SLOT (حکایت + کتاب)
  ۲۲:۰۰          -> دست‌نخورده، کاملاً متعلق به خود کاربره

اجرا: python schedule_posts.py
"""

import datetime
import os
import random
import pytz

import config
import quiz_web
from telegram_client import get_client, load_json, save_json, send_bot_message
from text_utils import clean_channel_post_text, get_visible_content_length, MIN_VISIBLE_CONTENT_LEN
from telethon.tl.functions.messages import GetScheduledHistoryRequest, SendMediaRequest
from telethon.tl.types import InputMediaPoll, Poll, PollAnswer

LOOKAHEAD_DAYS = 14
ALTERNATOR_FILE = config.STATE_DIR + "/alternator.json"

# اگه تعداد کوییزهای استفاده‌نشده‌ی توی صف از این کمتر بود، خودکار بیشتر می‌سازیم
QUIZ_TOPUP_THRESHOLD = 6
QUIZ_TOPUP_BATCH = 6


def tz_now():
    return datetime.datetime.now(pytz.timezone(config.TIMEZONE))


def hour_capacity(hour: int) -> int:
    if hour == 19:
        return 1 + config.BOOKS_PER_SLOT
    if hour == 16:
        return 1 + config.QUIZZES_PER_16_SLOT
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


def ensure_quiz_supply(quiz_queue: list) -> list:
    """
    اگه صفِ کوییز کم‌موجودی بود (کمتر از QUIZ_TOPUP_THRESHOLD تای استفاده‌نشده)،
    خودکار یک دستهٔ جدید می‌سازه - این کاملاً مستقل از /scan شماست.
    """
    unused_count = sum(1 for q in quiz_queue if not q.get("used"))
    if unused_count >= QUIZ_TOPUP_THRESHOLD:
        return quiz_queue

    print(f"🧩 صفِ کوییز کم‌موجودی داره ({unused_count} تا) - ساخت دستهٔ جدید...")
    try:
        new_quizzes = quiz_web.build_daily_quizzes(day=None, count=QUIZ_TOPUP_BATCH)
    except Exception as e:
        print(f"⚠️ ساخت کوییزهای جدید شکست خورد: {e}")
        return quiz_queue

    for q in new_quizzes:
        q.setdefault("used", False)
    quiz_queue.extend(new_quizzes)
    print(f"🧩 {len(new_quizzes)} کوییز جدید به صف اضافه شد.")
    return quiz_queue


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


def pop_next_quiz(quiz_queue):
    for item in quiz_queue:
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


def send_quiz(client, entity, quiz_item: dict, schedule_dt: datetime.datetime):
    """
    یک کوییز واقعی تلگرام می‌سازه. نکته‌ی مهم: امضای کانال (🏛️ @Tarikhgan)
    توی متنِ سوال نمیاد (اونجا فقط خودِ سوال باید باشه) - توی فیلد
    «توضیحاتِ اختیاری زیر سوال» (solution/explanation) گذاشته می‌شه، که
    تلگرام بعد از جواب‌دادن کاربر نشونش می‌ده.
    """
    options = list(quiz_item["options"])
    correct_index = quiz_item.get("correct_index", 0)

    indices = list(range(len(options)))
    random.shuffle(indices)
    shuffled_options = [options[i] for i in indices]
    new_correct_index = indices.index(correct_index)

    answers = [
        PollAnswer(text=opt, option=bytes([i]))
        for i, opt in enumerate(shuffled_options)
    ]
    poll = Poll(
        id=random.getrandbits(63),
        question=quiz_item["question"],
        answers=answers,
        quiz=True,
        public_voters=False,
        multiple_choice=False,
    )

    explanation = (quiz_item.get("explanation") or "").strip()
    solution_text = f"{explanation}\n\n{config.SIGNATURE}" if explanation else config.SIGNATURE
    solution_text = solution_text[:200]  # محدودیت تلگرام برای این فیلد

    media = InputMediaPoll(
        poll=poll,
        correct_answers=[bytes([new_correct_index])],
        solution=solution_text,
    )

    result = client(SendMediaRequest(
        peer=entity,
        media=media,
        message="",
        schedule_date=schedule_dt,
    ))
    for update in result.updates:
        if hasattr(update, "message") and hasattr(update.message, "id"):
            return update.message.id
    return None


def fill_hour(client, entity, day, hour, already, need, post_queue, book_queue, quiz_queue, alternator, counters):
    tz = pytz.timezone(config.TIMEZONE)
    slot_dt = tz.localize(datetime.datetime.combine(day, datetime.time(hour=hour)))
    filled_here = 0

    if hour == 16:
        if already == 0:
            category = get_16_category(alternator)
            item = pop_best(post_queue, category=category)
            if item:
                msg_id = send_post(client, entity, item, slot_dt)
                if msg_id:
                    counters["posts"] += 1
                    filled_here += 1
                else:
                    item["used"] = False
                    print(f"⚠️ ساعت {hour} روز {day}: ارسال پست دسته {category} شکست خورد.")
            else:
                print(f"⚠️ ساعت {hour} روز {day}: پستی از دسته‌ی {category} توی صف نبود.")
        remaining = need - filled_here
        for j in range(max(remaining, 0)):
            idx = already + filled_here + j
            q_dt = slot_dt + datetime.timedelta(minutes=2 * (idx + 1))
            quiz_item = pop_next_quiz(quiz_queue)
            if not quiz_item:
                print(f"⚠️ ساعت {hour} روز {day}: کوییزی توی صف نبود.")
                break
            msg_id = send_quiz(client, entity, quiz_item, q_dt)
            if msg_id:
                counters["quizzes"] += 1
            else:
                quiz_item["used"] = False
                print(f"⚠️ ساعت {hour} روز {day}: ارسال کوییز شکست خورد.")

    elif hour == 19:
        if already == 0:
            hekayat_item = pop_best(post_queue, category=config.CATEGORY_HEKAYAT)
            if hekayat_item:
                msg_id = send_post(client, entity, hekayat_item, slot_dt)
                if msg_id:
                    counters["posts"] += 1
                    filled_here += 1
                else:
                    hekayat_item["used"] = False
                    print(f"⚠️ ساعت {hour} روز {day}: ارسال پست حکایت شکست خورد.")
            else:
                print(f"⚠️ ساعت {hour} روز {day}: پست حکایتی توی صف نبود.")
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
            item = pop_best(post_queue, exclude_categories=config.RESERVED_CATEGORIES)
            if not item:
                print(f"⚠️ ساعت {hour} روز {day}: هیچ پستِ عمومی‌ای توی صف نبود.")
                break
            idx = already + j
            g_dt = slot_dt + datetime.timedelta(minutes=2 * idx) if idx else slot_dt
            msg_id = send_post(client, entity, item, g_dt)
            if msg_id:
                counters["posts"] += 1
            else:
                item["used"] = False
                print(f"⚠️ ساعت {hour} روز {day}: ارسال پست عمومی شکست خورد.")


def main():
    post_queue = load_json(config.POST_QUEUE_FILE, [])
    book_queue = load_json(config.BOOK_QUEUE_FILE, [])
    quiz_queue = load_json(config.QUIZ_QUEUE_FILE, [])
    alternator = load_json(ALTERNATOR_FILE, {})

    quiz_queue = ensure_quiz_supply(quiz_queue)

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
                          post_queue, book_queue, quiz_queue, alternator, counters)

    save_json(config.POST_QUEUE_FILE, post_queue)
    save_json(config.BOOK_QUEUE_FILE, book_queue)
    save_json(config.QUIZ_QUEUE_FILE, quiz_queue)
    save_json(ALTERNATOR_FILE, alternator)

    summary = (
        f"وضعیت زمان‌بندی به‌روزرسانی شد. {counters['posts']} پست، {counters['books']} کتاب، "
        f"{counters['quizzes']} کوییز جدید زمان‌بندی شد (بر اساس شمارش زنده‌ی تلگرام)."
    )
    print(summary)
    if any(counters.values()):
        send_bot_message(f"📅 {summary}")


if __name__ == "__main__":
    main()
