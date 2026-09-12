# -*- coding: utf-8 -*-
"""
پرکردنِ خودکارِ صفِ پست (بدون نیاز به دستورِ /scan). هر بار که این اسکریپت
اجرا می‌شه (خودکار، هر ۵ دقیقه کنارِ بقیه‌ی مراحل):

  ۱. اول می‌بینه توی ۹ روزِ آینده، کدوم اسلات‌های «پستِ معمولی» (عمومی -
     ساعت ۱۰/۱۳، سخن‌بزرگان/تصاویرقدیم - ساعت ۱۶، حکایت - ساعت ۱۹) هنوز
     خالی‌ان، و صفِ فعلی (post_queue) از همون دسته چیزی براشون نداره.

  ۲. اگه هیچ‌کدوم چیزی لازم نداشتن، اصلاً به تلگرام/هوش‌مصنوعی سر نمی‌زنه و
     همون‌جا تموم می‌شه - چون گشتنِ بی‌دلیل یعنی پیداکردنِ پست‌های خوب و
     بلااستفاده‌موندنشون (دقیقاً چیزی که نباید بشه).

  ۳. وگرنه، دقیقاً مثلِ /scan (و با همون زیرساخت - ChannelScanner و
     finalize_candidate از scan_history.py)، کانال‌های منبع رو از همون‌جایی
     که مونده می‌گرده و پست‌های واجدشرایط رو دسته‌ای دسته‌بندی می‌کنه - ولی
     به‌محضِ اینکه نیازِ *همه‌ی* دسته‌ها برطرف شد (نه با یه عددِ ثابتِ از پیش)،
     متوقف می‌شه.

  ۴. هر پستِ خوبی که پیدا کنه - حتی اگه دسته‌اش الان لازم نباشه (مثلاً یه
     حکایتِ خوب وقتی فقط دنبالِ عمومی می‌گشتیم) - باز هم به post_queue اضافه
     می‌شه، نه دور ریخته می‌شه؛ برای وقتی که اسلاتِ همون دسته خالی بشه می‌مونه
     (این دقیقاً همون رفتاری‌ست که «فراموش نکردن» می‌خواستید).

از همون فایل‌های وضعیتِ دستورِ /scan (پیشرفتِ کانال‌ها، هشِ متن‌های دیده‌شده،
آمارِ بازدید) استفاده می‌کنه - این دو مکانیزم با هم تداخلی ندارن، مکملِ
همدیگه‌ان و هیچ پیامی رو دوبار پردازش نمی‌کنن.

اجرا: python auto_fill_posts.py
"""

import datetime
import pytz

import config
from telegram_client import get_client, load_json, save_json
from scan_history import ChannelScanner, finalize_candidate, dedupe_existing_queue, get_cutoff_date
from schedule_posts import get_live_hour_counts, LOOKAHEAD_DAYS
from ai_classify import classify_batch_with_ai, BATCH_SIZE


def tz_now():
    return datetime.datetime.now(pytz.timezone(config.TIMEZONE))


def _bucket_for_category(category):
    """هر دسته توی کدوم «سطلِ نیاز» قرار می‌گیره؛ دسته‌هایی که به این اسکنِ
    خودکار ربطی ندارن (مثلاً گلچینِ کتاب) None برمی‌گردونن."""
    if category in (config.CATEGORY_SOKHAN_BOZORGAN, config.CATEGORY_AKS_IRAN_QADIM):
        return "16base"
    if category == config.CATEGORY_HEKAYAT:
        return "hekayat"
    if category not in config.RESERVED_CATEGORIES:
        return "general"
    return None


def compute_needed(client, entity, post_queue):
    """
    {"general": n, "16base": n, "hekayat": n} - چند پستِ *بیشتر* از هر سطل
    لازمه: (اسلات‌های خالیِ ۹ روزِ آینده) منهای (چیزی که همین الان
    بدونِ‌استفاده توی post_queue از همون سطل هست).
    """
    live_counts = get_live_hour_counts(client, entity)
    now = tz_now()
    tz = pytz.timezone(config.TIMEZONE)

    open_slots = {"general": 0, "16base": 0, "hekayat": 0}
    for day_offset in range(LOOKAHEAD_DAYS + 1):
        day = (now + datetime.timedelta(days=day_offset)).date()
        for hour in config.POSTING_HOURS:
            if hour in config.RESERVED_HOURS:
                continue
            slot_dt = tz.localize(datetime.datetime.combine(day, datetime.time(hour=hour)))
            if slot_dt <= now:
                continue
            other_have = live_counts.get((day, hour), {}).get("other", 0)
            if other_have >= 1:
                continue
            if hour == 16:
                open_slots["16base"] += 1
            elif hour == 19:
                open_slots["hekayat"] += 1
            else:
                open_slots["general"] += 1

    avail = {"general": 0, "16base": 0, "hekayat": 0}
    for item in post_queue:
        if item.get("used"):
            continue
        bucket = _bucket_for_category(item.get("category"))
        if bucket:
            avail[bucket] += 1

    return {k: max(0, open_slots[k] - avail[k]) for k in open_slots}


def main():
    progress = load_json(config.HISTORY_PROGRESS_FILE, {})
    queue = load_json(config.POST_QUEUE_FILE, [])
    stats = load_json(config.CHANNEL_STATS_FILE, {})
    seen_hashes = load_json(config.SEEN_TEXT_HASHES_FILE, {})

    queue = dedupe_existing_queue(queue, seen_hashes)

    with get_client() as client:
        entity = client.get_entity(config.TARGET_CHANNEL)

        needed = compute_needed(client, entity, queue)
        if sum(needed.values()) <= 0:
            print("همه‌ی اسلات‌های پستِ ۹ روزِ آینده پرن یا صف براشون چیزی آماده داره - نیازی به گشتنِ خودکار نیست.")
            save_json(config.POST_QUEUE_FILE, queue)
            save_json(config.SEEN_TEXT_HASHES_FILE, seen_hashes)
            return

        print(f"نیازِ باقی‌مونده: {needed} - شروع گشتنِ خودکار (بدون /scan)...")

        cutoff = get_cutoff_date()
        scanners = []
        for channel in config.SOURCE_CHANNELS:
            state = progress.get(channel, {"last_id": 0, "finished": False})
            if state.get("finished"):
                continue
            progress[channel] = state
            scanners.append(ChannelScanner(client, channel, state, cutoff, seen_hashes))

        pending = []
        added_count = 0
        scanned_budget = config.AUTO_FILL_MAX_MESSAGES_PER_RUN

        def flush_pending():
            nonlocal added_count
            if not pending:
                return
            items = [{"text": c["text"], "has_media": c["media_type"] is not None} for _, c in pending]
            ai_results = classify_batch_with_ai(items) if config.AI_PROVIDER_CHAIN else [None] * len(pending)

            for (scanner, candidate), ai_result in zip(pending, ai_results):
                final_item = finalize_candidate(candidate, ai_result, stats)
                if final_item:
                    queue.append(final_item)
                    added_count += 1
                    scanner.added += 1
                    bucket = _bucket_for_category(final_item["category"])
                    if bucket and needed[bucket] > 0:
                        needed[bucket] -= 1
            pending.clear()

        while sum(needed.values()) > 0 and scanned_budget > 0 and scanners:
            still_active = []
            for scanner in scanners:
                if scanned_budget <= 0 or sum(needed.values()) <= 0:
                    still_active.append(scanner)
                    continue
                candidate = scanner.next_candidate()
                if candidate is not None:
                    pending.append((scanner, candidate))
                    scanned_budget -= 1
                    if len(pending) >= BATCH_SIZE:
                        flush_pending()
                if not scanner.done or scanner._ready:
                    still_active.append(scanner)
            scanners = still_active

        flush_pending()
        print(f"{added_count} پستِ جدید (از هر دسته‌ای که پیدا شد، نه فقط نیازِ فعلی) به صف اضافه شد. "
              f"نیازِ باقی‌مونده: {needed}")

    save_json(config.HISTORY_PROGRESS_FILE, progress)
    save_json(config.POST_QUEUE_FILE, queue)
    save_json(config.CHANNEL_STATS_FILE, stats)
    save_json(config.SEEN_TEXT_HASHES_FILE, seen_hashes)


if __name__ == "__main__":
    main()
              
