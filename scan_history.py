# -*- coding: utf-8 -*-
"""
اسکریپت اصلی گشتن تاریخچه‌ی کانال‌ها.
فقط وقتی کاربر دستور /scan رو به بات فرستاده باشه کاری انجام می‌ده - وگرنه
بلافاصله بدون هیچ تغییری تموم می‌شه.

اجرا: python scan_history.py
"""

import datetime
import requests
import config
from telegram_client import get_client, load_json, save_json
from text_utils import detect_category

import pytz

API_BASE = f"https://api.telegram.org/bot{config.BOT_TOKEN}"


def scan_command_pending() -> bool:
    """چک می‌کنه آیا کاربر دستور /scan رو فرستاده یا نه (سبک، فقط یک HTTP request)."""
    if not config.BOT_TOKEN or not config.OWNER_USER_ID:
        return False

    last = load_json(config.LAST_COMMAND_UPDATE_ID_FILE, {"update_id": 0})
    resp = requests.get(f"{API_BASE}/getUpdates", params={"offset": last["update_id"] + 1, "timeout": 10})
    resp.raise_for_status()
    updates = resp.json().get("result", [])

    found = False
    max_update_id = last["update_id"]

    for update in updates:
        max_update_id = max(max_update_id, update["update_id"])
        msg = update.get("message", {})
        if msg.get("from", {}).get("id") != config.OWNER_USER_ID:
            continue
        text = (msg.get("text") or "").strip().lower()
        if text == config.SCAN_COMMAND_TEXT:
            found = True

    last["update_id"] = max_update_id
    save_json(config.LAST_COMMAND_UPDATE_ID_FILE, last)
    return found


def get_cutoff_date():
    tz = pytz.timezone(config.TIMEZONE)
    now = datetime.datetime.now(tz)
    return now - datetime.timedelta(days=config.MIN_AGE_DAYS)


def update_channel_average(stats: dict, channel: str, views: int) -> float:
    entry = stats.get(channel, {"count": 0, "avg_views": 0.0})
    count = entry["count"] + 1
    avg = entry["avg_views"] + (views - entry["avg_views"]) / count
    stats[channel] = {"count": count, "avg_views": avg}
    return avg if avg > 0 else 1.0


def score_message(text: str, views: int, forwards: int, avg_views: float) -> float:
    view_ratio = (views or 0) / avg_views if avg_views else 0
    return view_ratio + (forwards or 0) * 2


def main():
    if not scan_command_pending():
        print(f"دستور {config.SCAN_COMMAND_TEXT} پیدا نشد - این اجرا کاری انجام نمی‌ده.")
        return

    print(f"دستور {config.SCAN_COMMAND_TEXT} پیدا شد - شروع گشتن تاریخچه...")
    cutoff = get_cutoff_date()

    progress = load_json(config.HISTORY_PROGRESS_FILE, {})
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
                continue

            entity = client.get_entity(channel)

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
