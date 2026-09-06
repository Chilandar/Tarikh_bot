# -*- coding: utf-8 -*-
"""
اسکریپت اصلی گشتن تاریخچه‌ی کانال‌ها.
فقط فایل پرچم state/scan_requested.json رو چک می‌کنه.

محافظ‌های کیفیت:
  - تشخیص مدیا هر نوع (عکس فشرده، فیلم، عکس/فیلم به‌شکل فایل) رو می‌بینه.
  - اگه یک کانال عکس رو تنها (بدون کپشن) بفرسته و کپشنش رو توی پیام بعدی
    جداگانه بفرسته، این دو پیام رو به‌عنوان یک پست واحد (عکس + متن) می‌بینه.
  - قبل از اضافه‌کردن هر پست، هش متنش با پست‌های قبلاً دیده‌شده مقایسه می‌شه
    تا محتوای تکراری (که چند کانال از هم کپی می‌کنن) دوباره اضافه نشه.
  - صف فعلی هم یک‌بار از تکراری‌های قدیمی (قبل از فعال‌شدن این قابلیت) پاک می‌شه.

اجرا: python scan_history.py
"""

import datetime
import config
from telegram_client import get_client, load_json, save_json, send_bot_message, resolve_source_entity
from text_utils import detect_category, text_hash_for_dedupe
from ai_classify import classify_with_gemini

import pytz

PAIRING_MAX_SECONDS = 300
BARE_MEDIA_TEXT_LIMIT = 3


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


def detect_media_type(msg):
    if msg.photo:
        return "photo"
    if msg.video:
        return "video"
    if msg.document:
        mime = (msg.document.mime_type or "")
        if mime.startswith("image/"):
            return "photo"
        if mime.startswith("video/"):
            return "video"
    return None


def dedupe_existing_queue(queue: list, seen_hashes: dict) -> list:
    deduped = []
    for item in queue:
        h = text_hash_for_dedupe(item.get("text", ""))
        if h in seen_hashes:
            continue
        seen_hashes[h] = True
        deduped.append(item)
    return deduped


class ChannelScanner:
    def __init__(self, client, channel, state, cutoff, stats, seen_hashes):
        self.channel = channel
        self.state = state
        self.cutoff = cutoff
        self.stats = stats
        self.seen_hashes = seen_hashes
        self.scanned = 0
        self.added = 0
        self.duplicates_skipped = 0
        self.done = False
        self.pending_photo = None

        entity = resolve_source_entity(client, channel)
        self._iterator = client.iter_messages(
            entity,
            reverse=True,
            offset_id=state["last_id"],
            limit=config.HISTORY_RAW_SCAN_CAP,
        )

    def _build_item(self, message_id, text, media_type, views=0, forwards=0):
        text_hash = text_hash_for_dedupe(text)
        if text_hash in self.seen_hashes:
            self.duplicates_skipped += 1
            return None

        has_media = media_type is not None
        category = detect_category(text, has_media=has_media)
        if config.CATEGORY_REQUIRES_IMAGE.get(category, False) and not has_media:
            return None

        avg_views = update_channel_average(self.stats, self.channel, views or 0)
        score = score_message(text, views or 0, forwards or 0, avg_views)

        self.seen_hashes[text_hash] = True
        self.added += 1
        return {
            "channel": self.channel,
            "message_id": message_id,
            "text": text,
            "media_type": media_type,
            "category": category,
            "score": round(score, 3),
            "used": False,
        }

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

            text = msg.message or ""
            media_type = detect_media_type(msg)

            if media_type:
                if config.MIN_TEXT_LEN <= len(text) <= config.MAX_TEXT_LEN:
                    self.pending_photo = None
                    item = self._build_item(msg.id, text, media_type, msg.views, msg.forwards)
                    if item:
                        return item
                    continue
                if len(text) <= BARE_MEDIA_TEXT_LIMIT:
                    self.pending_photo = {"message_id": msg.id, "media_type": media_type, "date": msg.date}
                    continue
                self.pending_photo = None
                continue

            if self.pending_photo and (msg.date - self.pending_photo["date"]).total_seconds() <= PAIRING_MAX_SECONDS:
                pending = self.pending_photo
                self.pending_photo = None
                if config.MIN_TEXT_LEN <= len(text) <= config.MAX_TEXT_LEN:
                    item = self._build_item(pending["message_id"], text, pending["media_type"], msg.views, msg.forwards)
                    if item:
                        return item
                continue

            self.pending_photo = None
            if not (config.MIN_TEXT_LEN <= len(text) <= config.MAX_TEXT_LEN):
                continue
            item = self._build_item(msg.id, text, None, msg.views, msg.forwards)
            if item:
                return item

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
    seen_hashes = load_json(config.SEEN_TEXT_HASHES_FILE, {})

    before_count = len(queue)
    queue = dedupe_existing_queue(queue, seen_hashes)
    removed_old_duplicates = before_count - len(queue)

    remaining_budget = config.HISTORY_MESSAGES_PER_RUN
    added_count = 0
    total_duplicates = 0

    with get_client() as client:
        scanners = []
        for channel in config.SOURCE_CHANNELS:
            state = progress.get(channel, {"last_id": 0, "finished": False})
            if state.get("finished"):
                continue
            progress[channel] = state
            scanners.append(ChannelScanner(client, channel, state, cutoff, stats, seen_hashes))

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

        per_channel_report = {}
        for s in scanners:
            per_channel_report[s.channel] = f"{s.added} پست اضافه شد (از {s.scanned} پیام، {s.duplicates_skipped} تکراری رد شد)"
            total_duplicates += s.duplicates_skipped
        for channel in config.SOURCE_CHANNELS:
            if channel not in per_channel_report:
                if progress.get(channel, {}).get("finished"):
                    per_channel_report[channel] = "تاریخچه تمام شده"

    scan_flag["pending"] = False
    save_json(config.SCAN_FLAG_FILE, scan_flag)

    save_json(config.HISTORY_PROGRESS_FILE, progress)
    save_json(config.POST_QUEUE_FILE, queue)
    save_json(config.CHANNEL_STATS_FILE, stats)
    save_json(config.SEEN_TEXT_HASHES_FILE, seen_hashes)

    summary_lines = [
        f"✅ گشتن تمام شد. {added_count} پست جدید اضافه شد (مجموع صف: {len(queue)})",
        f"🧹 {removed_old_duplicates} تکراری قدیمی از صف حذف شد، {total_duplicates} تکراری جدید رد شد",
        "",
    ]
    for ch, report in per_channel_report.items():
        summary_lines.append(f"• {ch}: {report}")

    print("\n".join(summary_lines))
    send_bot_message("\n".join(summary_lines))


if __name__ == "__main__":
    main()
