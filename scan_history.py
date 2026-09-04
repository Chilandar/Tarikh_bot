# -*- coding: utf-8 -*-
"""
اسکریپت اصلی گشتن تاریخچه‌ی کانال‌ها.
دیگه خودش مستقیم با تلگرام Bot API تماس نمی‌گیره (این کار توسط check_telegram.py
انجام می‌شه) - فقط فایل پرچم state/scan_requested.json رو چک می‌کنه. اگه خاموش
بود، بلافاصله بدون هیچ تغییری تموم می‌شه.

بودجه‌ی HISTORY_MESSAGES_PER_RUN به‌طور یک‌درمیون (round-robin) بین همه‌ی
کانال‌های فعال تقسیم می‌شه.

اجرا: python scan_history.py
"""

import datetime
import config
from telegram_client import get_client, load_json, save_json, send_bot_message
from text_utils import detect_category

import pytz


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
    """پیام‌های واجدشرایط یک کانال رو یکی‌یکی برمی‌گردونه - برای حالت round-robin."""

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
    scan_flag = load_json(config.SCAN_FLAG_FILE, {"pending": False})
    if not scan_flag.get("pending"):
        print("دستور /scan در انتظار نیست - این اجرا کاری انجام نمی‌ده.")
        return

    print("دستور /scan فعاله - شروع گشتن تاریخچه...")
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

    scan_flag["pending"] = False
    save_json(config.SCAN_FLAG_FILE, scan_flag)

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
