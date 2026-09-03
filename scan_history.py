cat > /home/claude/tarikhi-bot/scan_history.py << 'PYEOF'
# -*- coding: utf-8 -*-
"""
اسکریپت اصلی گشتن تاریخچهی کانالها.
فقط وقتی کاربر دستور /scan رو به بات فرستاده باشه کاری انجام میده.

نکتهی مهم دربارهی توزیع بین کانالها: بهجای اینکه کل سهمیه رو از یک کانال
بردارد و بعد برود سراغ بعدی، بهصورت "نوبتی" (round-robin) عمل میکند - از هر
کانال فعال یک پست واجدشرایط برمیدارد، بعد میرود سراغ کانال بعدی، و همینطور
دوباره از اول - تا وقتی سهمیه تمام شود یا همهی کانالها تمام شده باشند.

اجرا: python scan_history.py
"""

import datetime
import requests
import config
from telegram_client import get_client, load_json, save_json, send_bot_message
from text_utils import detect_category

import pytz

API_BASE = f"https://api.telegram.org/bot{config.BOT_TOKEN}"

SCAN_KEYBOARD = {
    "keyboard": [[{"text": config.SCAN_COMMAND_TEXT}]],
    "resize_keyboard": True,
    "is_persistent": True,
}


def ensure_bot_menu():
    try:
        requests.post(
            f"{API_BASE}/setMyCommands",
            json={"commands": [{"command": "scan", "description": "شروع گشتن تاریخچهی کانالها"}]},
            timeout=10,
        )
    except requests.RequestException:
        pass


def scan_command_pending() -> bool:
    if not config.BOT_TOKEN or not config.OWNER_USER_ID:
        return False

    ensure_bot_menu()

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

        if text == "/start":
            send_bot_message(
                "سلام! برای شروع گشتن تاریخچهی کانالها، دکمهی زیر رو بزن یا /scan رو بفرست.",
                reply_markup=SCAN_KEYBOARD,
            )
        elif text == config.SCAN_COMMAND_TEXT:
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


def qualifies(msg, has_photo, text):
    if not (config.MIN_TEXT_LEN <= len(text) <= config.MAX_TEXT_LEN):
        return False, None
    category = detect_category(text)
    if config.CATEGORY_REQUIRES_IMAGE.get(category, False) and not has_photo:
        return False, None
    return True, category


def main():
    if not scan_command_pending():
        print(f"دستور {config.SCAN_COMMAND_TEXT} پیدا نشد - این اجرا کاری انجام نمیده.")
        return

    print(f"دستور {config.SCAN_COMMAND_TEXT} پیدا شد - شروع گشتن تاریخچه...")
    send_bot_message("🔍 شروع گشتن تاریخچهی کانالها... (چند دقیقه طول میکشه)")

    cutoff = get_cutoff_date()

    progress = load_json(config.HISTORY_PROGRESS_FILE, {})
    queue = load_json(config.POST_QUEUE_FILE, [])
    stats = load_json(config.CHANNEL_STATS_FILE, {})

    remaining_budget = config.HISTORY_MESSAGES_PER_RUN
    added_count = 0

    with get_client() as client:
        # برای هر کانالِ هنوز تمامنشده، یک generator جدا میسازیم تا بتونیم
        # نوبتی (round-robin) بینشون جابهجا بشیم
        channels_state = {}
        for channel in config.SOURCE_CHANNELS:
            saved = progress.get(channel, {"last_id": 0, "finished": False})
            if saved.get("finished"):
                continue
            entity = client.get_entity(channel)
            gen = client.iter_messages(
                entity,
                reverse=True,
                offset_id=saved["last_id"],
                limit=config.HISTORY_RAW_SCAN_CAP,
            )
            channels_state[channel] = {
                "gen": gen,
                "last_seen_id": saved["last_id"],
                "finished": False,
                "scanned": 0,
                "added": 0,
            }

        # حلقهی نوبتی: هر دور، از هر کانال فعال یک پست واجدشرایط برمیداریم
        while remaining_budget > 0 and any(not s["finished"] for s in channels_state.values()):
            for channel, st in channels_state.items():
                if remaining_budget <= 0:
                    break
                if st["finished"]:
                    continue

                found_one = False
                while True:
                    try:
                        msg = next(st["gen"])
                    except StopIteration:
                        st["finished"] = True  # به سقف HISTORY_RAW_SCAN_CAP رسیدیم
                        break

                    st["scanned"] += 1
                    st["last_seen_id"] = msg.id

                    if msg.date >= cutoff:
                        st["finished"] = True  # به مرز یکسالپیش رسیدیم
                        break

                    if not msg.text and not msg.message:
                        continue

                    text = msg.message or ""
                    has_photo = bool(msg.photo)
                    ok, category = qualifies(msg, has_photo, text)
                    if not ok:
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
                    st["added"] += 1
                    remaining_budget -= 1
                    found_one = True
                    break  # یک پست از این کانال کافیه، برو سراغ کانال بعدی

                if not found_one and st["finished"]:
                    continue

        for channel, st in channels_state.items():
            progress[channel] = {"last_id": st["last_seen_id"], "finished": st["finished"]}

    save_json(config.HISTORY_PROGRESS_FILE, progress)
    save_json(config.POST_QUEUE_FILE, queue)
    save_json(config.CHANNEL_STATS_FILE, stats)

    summary_lines = [f"✅ گشتن تمام شد. {added_count} پست جدید اضافه شد (مجموع صف: {len(queue)})", ""]
    for channel, st in channels_state.items():
        summary_lines.append(f"• {channel}: {st['added']} پست اضافه شد (از {st['scanned']} پیام بررسیشده)")

    print("\n".join(summary_lines))
    send_bot_message("\n".join(summary_lines))


if __name__ == "__main__":
    main()
PYEOF
echo done
