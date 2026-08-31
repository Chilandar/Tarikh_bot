# -*- coding: utf-8 -*-
"""
اسکریپت اصلی گشتن تاریخچه‌ی کانال‌ها.
هر بار اجرا، حداکثر HISTORY_MESSAGES_PER_RUN پیام جدید (که سنشون بیشتر از
MIN_AGE_DAYS روزه) رو از کانال‌های SOURCE_CHANNELS می‌خونه، امتیازدهی می‌کنه
و پست‌های خوب رو به صف post_queue.json اضافه می‌کنه.

این اسکریپت هرگز پست‌های جدیدتر از یک سال پیش رو نمی‌بینه - فقط از قدیمی‌ترین
پیام هر کانال شروع می‌کنه و رو به جلو (به سمت جدیدتر) حرکت می‌کنه، تا وقتی
به مرز یک‌سال‌پیش برسه، و بعد اون کانال رو "تمام‌شده" علامت می‌زنه.

اجرا: python scan_history.py
پیشنهاد زمان‌بندی: حداکثر ۲ بار در روز (طبق تصمیم کاربر)
"""

import datetime
import config
from telegram_client import get_client, load_json, save_json
from text_utils import detect_category

import pytz


def get_cutoff_date():
    tz = pytz.timezone(config.TIMEZONE)
    now = datetime.datetime.now(tz)
    return now - datetime.timedelta(days=config.MIN_AGE_DAYS)


def update_channel_average(stats: dict, channel: str, views: int) -> float:
    """میانگین بازدید کانال رو به‌صورت تدریجی (running average) به‌روز می‌کنه."""
    entry = stats.get(channel, {"count": 0, "avg_views": 0.0})
    count = entry["count"] + 1
    avg = entry["avg_views"] + (views - entry["avg_views"]) / count
    stats[channel] = {"count": count, "avg_views": avg}
    return avg if avg > 0 else 1.0


def score_message(text: str, views: int, forwards: int, avg_views: float) -> float:
    view_ratio = (views or 0) / avg_views if avg_views else 0
    return view_ratio + (forwards or 0) * 2


def main():
    cutoff = get_cutoff_date()

    progress = load_json(config.HISTORY_PROGRESS_FILE, {})
    # progress[channel] = {"last_id": 0, "finished": False}  (last_id=0 یعنی هنوز شروع نشده)

    queue = load_json(config.POST_QUEUE_FILE, [])
    stats = load_json(config.CHANNEL_STATS_FILE, {})

    remaining_budget = config.HISTORY_MESSAGES_PER_RUN
    added_count = 0

    with get_client() as client:
        for channel in config.SOURCE_CHANNELS:
            if remaining_budget <= 0:
                break

            state = progress.get(channel, {"last_id": 0, "finished": False})
            if state.get("finished"):
                continue  # این کانال قبلاً تا مرز یک‌سال‌پیش کامل بررسی شده

            entity = client.get_entity(channel)

            # از قدیمی‌ترین پیام به بعد، رو به جلو حرکت می‌کنیم (reverse=True)
            messages = client.iter_messages(
                entity,
                reverse=True,
                offset_id=state["last_id"],
                limit=remaining_budget,
            )

            last_seen_id = state["last_id"]
            channel_finished = False

            for msg in messages:
                last_seen_id = msg.id

                # اگه به مرز یک‌سال‌پیش رسیدیم، این کانال رو تمام‌شده علامت می‌زنیم
                if msg.date >= cutoff:
                    channel_finished = True
                    break

                if not msg.text and not msg.message:
                    continue

                text = msg.message or ""
                has_photo = bool(msg.photo)

                if not (config.MIN_TEXT_LEN <= len(text) <= config.MAX_TEXT_LEN):
                    continue

                category = detect_category(text)
                if config.CATEGORY_REQUIRES_IMAGE.get(category, False) and not has_photo:
                    continue

                avg_views = update_channel_average(stats, channel, msg.views or 0)
                score = score_message(text, msg.views or 0, msg.forwards or 0, avg_views)

                queue.append({
                    "channel": channel,
                    "message_id": msg.id,
                    "text": text,
                    "has_photo": has_photo,
                    "category": category,
                    "score": round(score, 3),
                    "used": False,
                })
                added_count += 1
                remaining_budget -= 1

                if remaining_budget <= 0:
                    break

            progress[channel] = {
                "last_id": last_seen_id,
                "finished": channel_finished,
            }

    save_json(config.HISTORY_PROGRESS_FILE, progress)
    save_json(config.POST_QUEUE_FILE, queue)
    save_json(config.CHANNEL_STATS_FILE, stats)

    print(f"{added_count} پست جدید به صف اضافه شد. مجموع صف: {len(queue)}")


if __name__ == "__main__":
    main()
