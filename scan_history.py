# -*- coding: utf-8 -*-
"""
اسکریپت اصلی گشتن تاریخچه‌ی کانال‌ها.
فقط وقتی کاربر دستور /scan رو به بات فرستاده باشه کاری انجام می‌ده - وگرنه
بلافاصله بدون هیچ تغییری تموم می‌شه. به کاربر هم گزارش می‌ده (شروع + نتیجه).

بودجه‌ی HISTORY_MESSAGES_PER_RUN به‌طور یک‌درمیون (round-robin) بین همه‌ی
کانال‌های فعال تقسیم می‌شه - یعنی به‌جای خالی‌کردن کل بودجه روی اولین کانال،
از هر کانال یکی برمی‌داره، دوباره از هر کانال یکی، و همین‌طور تا تموم‌شدن بودجه.

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
            json={"commands": [{"command": "scan", "description": "شروع گشتن تاریخچه‌ی کانال‌ها"}]},
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
                "سلام! برای شروع گشتن تاریخچه‌ی کانال‌ها، دکمه‌ی زیر رو بزن یا /scan رو بفرست.",
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


class ChannelScanner:
    """
    یک لایه‌ی نازک روی iter_messages که پیام‌های واجدشرایط یک کانال رو یکی‌یکی
    (به‌درخواست، نه همه‌ی ۳۰۰ تا یک‌جا) برمی‌گردونه - برای پشتیبانی از حالت round-robin.
    """

    def __init__(self, client, channel, state, cutoff, stats):
        self.channel = channel
        self.state = state
        self.cutoff = cutoff
        self.stats = stats
        self.scanned = 0
        self.added = 0
        self.done = False

        entity = client.get_entity(channel)
        self._iterator = client.iter_messages(
            entity,
            reverse=True,
            offset_id=state["last_id"],
            limit=config.HISTORY_RAW_SCAN_CAP,
        )

    def next_qualifying_item(self):
        if self.done:
            return None

        for msg in self._iterator:
            self.state["last_id"] = msg.id
            self.scanned += 1

            if msg.date >= self.cutoff:
                self.state["finished"] = True
                self.done = True
                return None

            if not msg.text and not msg.message:
                continue

            text = msg.message or ""
            has_photo = bool(msg.photo)

            if not (config.MIN_TEXT_LEN <= len(text) <= config.MAX_TEXT_LEN):
                continue

            category = detect_category(text)
            if config.CATEGORY_REQUIRES_IMAGE.get(category, False) and not has_photo:
                continue

            avg_views = update_channel_average(self.stats, self.channel, msg.views or 0)
            score = score_message(text, msg.views or 0, msg.forwards or 0, avg_views)

            self.added += 1
            return {
                "channel": self.channel,
                "message_id": msg.id,
                "text": text,
                "has_photo": has_photo,
                "category": category,
                "score": round(score, 3),
                "used": False,
            }

        self.done = True
        return None


def main():
    if not scan_command_pending():
        print(f"دستور {config.SCAN_COMMAND_TEXT} پیدا نشد - این اجرا کاری انجام نمی‌ده.")
        return

    print(f"دستور {config.SCAN_COMMAND_TEXT} پیدا شد - شروع گشتن تاریخچه...")
    send_bot_message("🔍 شروع گشتن تاریخچه‌ی کانال‌ها... (چند دقیقه طول می‌کشه)")

    cutoff = get_cutoff_date()

    progress = load_json(config.HISTORY_PROGRESS_FILE, {})
    queue = load_json(config.POST_QUEUE_FILE, [])
    stats = load_json(config.CHANNEL_STATS_FILE, {})

    remaining_budget = config.HISTORY_MESSAGES_PER_RUN
    added_count = 0

    with get_client() as client:
        scanners = []
        for channel in config.SOURCE_CHANNELS:
            state = progress.get(channel, {"last_id": 0, "finished": False})
            if state.get("finished"):
                continue
            progress[channel] = state
            scanners.append(ChannelScanner(client, channel, state, cutoff, stats))

        while remaining_budget > 0 and scanners:
            still_active = []
            for scanner in scanners:
                if remaining_budget <= 0:
                    still_active.append(scanner)
                    continue
                item = scanner.next_qualifying_item()
                if item is not None:
                    queue.append(item)
                    added_count += 1
                    remaining_budget -= 1
                if not scanner.done:
                    still_active.append(scanner)
            scanners = still_active

        per_channel_report = {
            s.channel: f"{s.added} پست اضافه شد (از {s.scanned} پیام بررسی‌شده)"
            for s in scanners
        }
        for channel in config.SOURCE_CHANNELS:
            if channel not in per_channel_report:
                if progress.get(channel, {}).get("finished"):
                    per_channel_report[channel] = "تاریخچه تمام شده"

    save_json(config.HISTORY_PROGRESS_FILE, progress)
    save_json(config.POST_QUEUE_FILE, queue)
    save_json(config.CHANNEL_STATS_FILE, stats)

    summary_lines = [f"✅ گشتن تمام شد. {added_count} پست جدید اضافه شد (مجموع صف: {len(queue)})", ""]
    for ch, report in per_channel_report.items():
        summary_lines.append(f"• {ch}: {report}")

    print("\n".join(summary_lines))
    send_bot_message("\n".join(summary_lines))


if __name__ == "__main__":
    main()
