# -*- coding: utf-8 -*-
"""
اسکریپت اصلی گشتن تاریخچه‌ی کانال‌ها.
فقط فایل پرچم state/scan_requested.json رو چک می‌کنه.

محافظ‌های کیفیت:
  - تشخیص مدیا هر نوع (عکس فشرده، فیلم، عکس/فیلم به‌شکل فایل) رو می‌بینه.
  - اگه یک کانال عکس رو تنها (بدون کپشن) بفرسته و کپشنش رو توی پیام بعدی
    جداگانه بفرسته، این دو پیام رو یکی می‌بینه - و برعکسش هم: اگه کپشن اول
    بیاد و عکس توی پیام بعدی، بازم جفتشون می‌کنه.
  - قبل از اضافه‌کردن هر پست، هش متنش با پست‌های قبلاً دیده‌شده مقایسه می‌شه.
  - هیچ پستی (به‌جز حکایت) بدون عکس/فیلم واقعی اضافه نمی‌شه.

درباره‌ی دسته‌بندی با Gemini: به‌جای یک درخواست به‌ازای هر پست، پست‌های
واجدشرایط (بعد از رد تکراری‌ها و پست‌های خیلی کوتاه) توی یک بافر جمع می‌شن و
هر ai_classify.BATCH_SIZE تا، با هم در یک درخواست به Gemini فرستاده می‌شن -
هم سریع‌تره هم به سقف رایگان نمی‌خوریم.

اجرا: python scan_history.py
"""

import datetime
import config
from telegram_client import get_client, load_json, save_json, send_bot_message, resolve_source_entity
from text_utils import detect_category, text_hash_for_dedupe, get_visible_content_length, MIN_VISIBLE_CONTENT_LEN
from ai_classify import classify_batch_with_gemini, BATCH_SIZE

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


def finalize_candidate(candidate: dict, ai_result: dict, stats: dict):
    """
    یک کاندید (که از پیش دسته‌بندی نشده) رو با نتیجه‌ی Gemini (یا فال‌بک
    کلیدواژه‌ای) نهایی می‌کنه. اگه رد بشه، None برمی‌گردونه.
    """
    text = candidate["text"]
    media_type = candidate["media_type"]
    has_media = media_type is not None

    if ai_result:
        if not ai_result["is_good"]:
            return None
        category = ai_result["category"]
    else:
        category = detect_category(text, has_media=has_media)

    # قانون سخت: هیچ پستی (به‌جز حکایت) بدون عکس/فیلم واقعی زمان‌بندی نمی‌شه
    if config.CATEGORY_REQUIRES_IMAGE.get(category, True) and not has_media:
        return None

    avg_views = update_channel_average(stats, candidate["channel"], candidate["views"] or 0)
    score = score_message(text, candidate["views"] or 0, candidate["forwards"] or 0, avg_views)

    return {
        "channel": candidate["channel"],
        "message_id": candidate["message_id"],
        "text": text,
        "media_type": media_type,
        "category": category,
        "score": round(score, 3),
        "used": False,
    }


class ChannelScanner:
    """
    کاندیدهای واجدشرایط یک کانال رو یکی‌یکی برمی‌گردونه (هنوز دسته‌بندی
    نشدن - این کار دسته‌ای و بیرون از این کلاس انجام می‌شه). یک "نگه‌دارنده"
    (held) برای جفت‌کردنِ عکس/کپشنی که توی دو پیام جدا (به هر ترتیبی) اومدن داره.
    """

    def __init__(self, client, channel, state, cutoff, seen_hashes):
        self.channel = channel
        self.state = state
        self.cutoff = cutoff
        self.seen_hashes = seen_hashes
        self.scanned = 0
        self.added = 0  # بعداً از بیرون (بعد از دسته‌بندی) افزایش داده می‌شه
        self.duplicates_skipped = 0
        self.done = False
        self._held = None      # {"kind": "photo"|"text", ...}
        self._ready = []       # کاندیدهای آماده‌ی برگشت

        entity = resolve_source_entity(client, channel)
        self._iterator = client.iter_messages(
            entity,
            reverse=True,
            offset_id=state["last_id"],
            limit=config.HISTORY_RAW_SCAN_CAP,
        )

    def _prepare_candidate(self, message_id, text, media_type, views=0, forwards=0):
        text_hash = text_hash_for_dedupe(text)
        if text_hash in self.seen_hashes:
            self.duplicates_skipped += 1
            return None

        if get_visible_content_length(text) < MIN_VISIBLE_CONTENT_LEN:
            return None  # بعد از حذف امضا/آیدی، عملاً چیزی برای گفتن نمونده

        self.seen_hashes[text_hash] = True
        return {
            "channel": self.channel,
            "message_id": message_id,
            "text": text,
            "media_type": media_type,
            "views": views,
            "forwards": forwards,
        }

    def _flush_held_as_standalone(self):
        """اگه چیزی نگه‌داشته شده بود و جفتش پیدا نشد، به‌عنوان کاندید مستقل امتحانش کن."""
        held = self._held
        self._held = None
        if held and held["kind"] == "text":
            candidate = self._prepare_candidate(held["message_id"], held["text"], None, held["views"], held["forwards"])
            if candidate:
                self._ready.append(candidate)
        # اگه held از نوع "photo" بود و جفتش پیدا نشد، اصلاً نگهش نمی‌داریم -
        # چون عکس بدون کپشن (به‌جز حکایت که اصلاً عکس لازم نداره) قابل‌قبول نیست

    def next_candidate(self):
        if self._ready:
            return self._ready.pop(0)
        if self.done:
            return None

        for msg in self._iterator:
            self.state["last_id"] = msg.id
            self.scanned += 1

            if msg.date >= self.cutoff:
                self._flush_held_as_standalone()
                self.state["finished"] = True
                self.done = True
                return self._ready.pop(0) if self._ready else None

            text = msg.message or ""
            media_type = detect_media_type(msg)

            has_full = media_type and (config.MIN_TEXT_LEN <= len(text) <= config.MAX_TEXT_LEN)
            is_bare_photo = media_type and len(text) <= BARE_MEDIA_TEXT_LIMIT
            is_bare_text = (not media_type) and (config.MIN_TEXT_LEN <= len(text) <= config.MAX_TEXT_LEN)

            within_window = (
                self._held is not None
                and (msg.date - self._held["date"]).total_seconds() <= PAIRING_MAX_SECONDS
            )

            if has_full:
                self._flush_held_as_standalone()
                candidate = self._prepare_candidate(msg.id, text, media_type, msg.views, msg.forwards)
                if candidate:
                    self._ready.append(candidate)
                if self._ready:
                    return self._ready.pop(0)
                continue

            if is_bare_photo:
                if self._held and self._held["kind"] == "text" and within_window:
                    pending = self._held
                    self._held = None
                    candidate = self._prepare_candidate(msg.id, pending["text"], media_type, msg.views, msg.forwards)
                    if candidate:
                        self._ready.append(candidate)
                    if self._ready:
                        return self._ready.pop(0)
                    continue
                self._flush_held_as_standalone()
                self._held = {"kind": "photo", "message_id": msg.id, "media_type": media_type, "date": msg.date}
                continue

            if is_bare_text:
                if self._held and self._held["kind"] == "photo" and within_window:
                    pending = self._held
                    self._held = None
                    candidate = self._prepare_candidate(pending["message_id"], text, pending["media_type"], msg.views, msg.forwards)
                    if candidate:
                        self._ready.append(candidate)
                    if self._ready:
                        return self._ready.pop(0)
                    continue
                self._flush_held_as_standalone()
                self._held = {
                    "kind": "text", "text": text, "message_id": msg.id, "date": msg.date,
                    "views": msg.views, "forwards": msg.forwards,
                }
                continue

            # نه کامل، نه عکس‌تنها، نه متنِ‌تنهای واجدشرایط - رد می‌شه
            self._flush_held_as_standalone()

        self._flush_held_as_standalone()
        self.done = True
        return self._ready.pop(0) if self._ready else None


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
            scanners.append(ChannelScanner(client, channel, state, cutoff, seen_hashes))

        pending = []  # [(scanner, candidate), ...] در انتظار دسته‌بندیِ دسته‌ای

        def flush_pending():
            nonlocal added_count
            if not pending:
                return
            items = [{"text": c["text"], "has_media": c["media_type"] is not None} for _, c in pending]
            if config.GEMINI_API_KEY:
                ai_results = classify_batch_with_gemini(config.GEMINI_API_KEY, items)
            else:
                ai_results = [None] * len(pending)

            for (scanner, candidate), ai_result in zip(pending, ai_results):
                final_item = finalize_candidate(candidate, ai_result, stats)
                if final_item:
                    queue.append(final_item)
                    added_count += 1
                    scanner.added += 1
            pending.clear()

        while remaining_budget > 0 and scanners:
            still_active = []
            for scanner in scanners:
                if remaining_budget <= 0:
                    still_active.append(scanner)
                    continue
                candidate = scanner.next_candidate()
                if candidate is not None:
                    pending.append((scanner, candidate))
                    remaining_budget -= 1
                    if len(pending) >= BATCH_SIZE:
                        flush_pending()
                if not scanner.done or scanner._ready:
                    still_active.append(scanner)
            scanners = still_active

        flush_pending()  # هرچی از دسته‌ی آخر باقی مونده بود

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
