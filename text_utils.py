# -*- coding: utf-8 -*-
"""
توابع پردازش متن: پاک‌سازی کپشن پست‌های کانال‌ها، پردازش کپشن کتاب‌ها،
تشخیص دسته‌بندی محتوا، و تشخیص/تبدیل شماره‌ی جلد.
"""

import re
import html
import hashlib
import config

# میانگین تعداد کاراکتر در هر "خط" نمایش‌داده‌شده توی تلگرام (تخمینی، برای
# متن فارسی روی موبایل) - برای تخمین تعداد خط واقعی، نه فقط شمردن \n
CHARS_PER_TELEGRAM_LINE = 40

# الگوی سال (شمسی/قمری/میلادی) - چهار رقم پشت‌سرهم، یا کلمه‌ی "سال" قبلش
YEAR_PATTERN = re.compile(r"(سال\s+)?\b1[0-9]{3}\b")

# کلماتی که معمولاً همراه با اشاره به مکان (شهر/کشور/موزه) میان
PLACE_KEYWORDS = ["موزه", "شهر", "کشور", "خیابان", "میدان", "استان", "روستا"]


def normalize_text_for_dedupe(text: str) -> str:
    text = text or ""
    text = re.sub(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]", "", text)
    text = re.sub(r"[^\w\s]", "", text)
    # حروف عربی/فارسی که کانال‌های مختلف قاطی استفاده می‌کنن (ي/ی، ك/ک) یکی بشن
    text = text.replace("ي", "ی").replace("ك", "ک").replace("ة", "ه")
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text


def text_hash_for_dedupe(text: str) -> str:
    normalized = normalize_text_for_dedupe(text)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# پاک‌سازی پست‌های استخراج‌شده از کانال‌های دیگر
# ---------------------------------------------------------------------------

USERNAME_RE = re.compile(r"@\w+")
TME_LINK_RE = re.compile(r"(https?://)?t\.me/\S+")

# نشانه‌های رایج خط‌های امضا/تبلیغ (کانال، اینستاگرام، هشتگ اسم کانال و...)
SIGNATURE_MARKER_SYMBOLS = (
    "▪️", "▫️", "🔻", "🔺", "🆔", "🔹", "🔸", "➖", "➡️", "👉", "●", "○", "■", "□", "•",
)


def _looks_like_signature_line(line: str, treat_hashtag_as_signature: bool = True) -> bool:
    """
    آیا این خط شبیه یک خط امضا/تبلیغ (آیدی کانال، اینستاگرام، هشتگ اسم
    کانال، یا برچسب‌هایی مثل «کانال تلگرام ...») هست؟

    treat_hashtag_as_signature=False وقتی استفاده می‌شه که می‌خوایم تصمیم
    درباره‌ی هشتگ‌ها رو به هوش مصنوعی بسپاریم (چون بعضی هشتگ‌ها در واقع
    اسمِ نویسنده‌ن و نباید همینجا کورکورانه حذف بشن).
    """
    s = line.strip()
    if not s:
        return True  # خط خالی - در فرآیند حذفِ انتهای پیام، ردش می‌کنیم
    lower = s.lower()
    if "@" in s:
        return True
    if "instagram" in lower or "t.me/" in lower:
        return True
    if treat_hashtag_as_signature and s.startswith("#"):
        return True
    for sym in SIGNATURE_MARKER_SYMBOLS:
        if s.startswith(sym) and len(s) <= 60:
            return True
    return False


def strip_trailing_signature_block(text: str, treat_hashtag_as_signature: bool = True) -> str:
    """
    از انتهای متن، خط‌به‌خط جلو میره و هر خطی که شبیه امضا/تبلیغ (آیدی کانال،
    اینستاگرام، هشتگ، خط‌های برچسب‌مانند) یا خالی باشه رو حذف می‌کنه، تا به
    اولین خط واقعیِ محتوا برسه.
    """
    lines = text.split("\n")
    while lines and not lines[-1].strip():
        lines.pop()
    while lines and _looks_like_signature_line(lines[-1], treat_hashtag_as_signature):
        lines.pop()
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


def get_visible_content_length(original_text: str) -> int:
    """
    طول متنی که بعد از حذف امضا/آیدی/لینک واقعاً باقی می‌مونه رو برمی‌گردونه -
    برای اینکه بفهمیم آیا پست واقعاً محتوا داره یا فقط امضا/تبلیغ بوده.
    """
    text = strip_trailing_signature_block(original_text or "")
    text = USERNAME_RE.sub("", text)
    text = TME_LINK_RE.sub("", text)
    return len(text.strip())


MIN_VISIBLE_CONTENT_LEN = 20  # زیر این مقدار یعنی عملاً چیزی برای گفتن نمونده

def clean_channel_post_text(original_text: str) -> str:
    """
    آیدی/یوزرنیم و لینک‌های t.me رو از متن پست حذف می‌کنه، متن اصلی رو بولد
    می‌کنه، و امضای خودمون رو (بدون بولد) با یک خط فاصله‌ی کامل در انتها اضافه می‌کنه.

    توجه: این تابع دیگه مستقیماً توی مسیر اصلیِ پست‌گذاری استفاده نمی‌شه (چون
    هشتگ‌های تازه‌ای که خودمون در انتهای متن اضافه می‌کنیم رو هم پاک می‌کرد -
    strip_trailing_signature_block هر خط با # رو امضا حساب می‌کنه). به‌جاش
    clean_and_assemble_post_text + finalize_clean_text استفاده می‌شه. این تابع
    فقط برای سازگاری با کدهای قدیمی/دیگه نگه داشته شده.
    """
    text = original_text or ""
    text = strip_trailing_signature_block(text)
    text = USERNAME_RE.sub("", text)
    text = TME_LINK_RE.sub("", text)
    text = text.rstrip()
    bold_text = f"<b>{html.escape(text)}</b>"
    return f"{bold_text}\n\n{config.SIGNATURE}"


# ---------------------------------------------------------------------------
# حذفِ مکانیکیِ استیکر/ایموجی (بدون هوش مصنوعی، صد در صد قابل‌پیش‌بینی)
# ---------------------------------------------------------------------------

# تمام رنج‌های ایموجی/نمادهای تزئینی رایج (سعی شده جامع باشه ولی حروف/اعداد
# فارسی و علائم نگارشیِ معمولی مثل ! ? … « » : رو دست‌نخورده بذاره)
EMOJI_RE = re.compile(
    "["
    "\U0001F000-\U0001F0FF"   # مهجونگ، کارت‌های بازی
    "\U0001F100-\U0001F1FF"   # حروف/اعداد توی‌مربع (مثل 🆔 🆕) + پرچم‌ها
    "\U0001F200-\U0001F2FF"   # نمادهای ایدئوگرافیک توی‌دایره/مربع
    "\U0001F300-\U0001F5FF"   # نمادها و تصاویر متفرقه
    "\U0001F600-\U0001F64F"   # ایموجی‌های صورتک
    "\U0001F680-\U0001F6FF"   # حمل‌ونقل و نقشه
    "\U0001F700-\U0001F77F"   # نمادهای کیمیاگری
    "\U0001F780-\U0001F7FF"   # اشکال هندسیِ گسترش‌یافته
    "\U0001F800-\U0001F8FF"   # فلش‌های تکمیلی
    "\U0001F900-\U0001F9FF"   # نمادهای تکمیلیِ متفرقه
    "\U0001FA00-\U0001FA6F"   # نمادهای شطرنج و غیره
    "\U0001FA70-\U0001FAFF"   # نمادهای تکمیلیِ گسترش‌یافته (بخش A)
    "\U00002300-\U000023FF"   # نمادهای فنیِ متفرقه (⌚ ⏰ ...)
    "\U000025A0-\U000025FF"   # اشکال هندسی (● ■ ○ ...)
    "\U00002600-\U000026FF"   # نمادهای متفرقه
    "\U00002700-\U000027BF"   # dingbats
    "\U00002190-\U000021FF"   # فلش‌ها
    "\U00002B00-\U00002BFF"   # نمادها/فلش‌های متفرقه
    "\U0000FE0F"              # variation selector (رنگی‌کننده‌ی ایموجی)
    "\U0000200D"              # zero-width joiner
    "]+",
    flags=re.UNICODE,
)


def _drop_lines_and_collapse(lines: list, should_drop: list) -> list:
    """
    خط‌هایی که should_drop[i]=True دارن رو کامل از لیست حذف می‌کنه، و اگه
    خط خالیِ دیگه‌ای درست قبل یا بعدش بود (نه محتوای واقعی) اونم حذف می‌کنه -
    که یه فاصله‌ی یتیمِ اضافه از قلم‌افتاده‌ی چیزی که پاک شده باقی نمونه.
    """
    result = []
    i, n = 0, len(lines)
    while i < n:
        if should_drop[i]:
            if result and result[-1] == "":
                result.pop()
            i += 1
            while i < n and not should_drop[i] and lines[i].strip() == "":
                i += 1
            continue
        result.append(lines[i])
        i += 1
    return result


def strip_sticker_lines(text: str) -> str:
    """
    همه‌ی استیکر/ایموجی‌ها رو از کل متن حذف می‌کنه. اگه یک خط، بعد از حذف
    استیکر، کاملاً خالی بشه (یعنی محتوای اون خط فقط استیکر + فاصله بوده)،
    کل اون خط حذف می‌شه - و اگه خط خالیِ دیگه‌ای درست قبل یا بعدش بود اونم
    حذف می‌شه (که یه فاصله‌ی اضافه‌ی یتیم نمونه).
    خط‌هایی که بعد از حذف استیکر هنوز محتوای واقعی دارن (مثلاً یک هشتگ یا
    اسم)، نگه داشته می‌شن - فقط فاصله‌ی اضافیِ لبه‌ای که از جای خودِ استیکر
    مونده trim می‌شه، نه بیشتر.
    خط‌هایی که اصلاً استیکر ندارن، کاملاً دست‌نخورده می‌مونن (حتی اگه فاصله‌ی
    انتهایی داشته باشن - مثلاً بیت‌های شعر با فاصله‌ی عمدی در آخر خط).
    """
    raw_lines = (text or "").split("\n")
    processed = []
    should_drop = []
    for line in raw_lines:
        if not EMOJI_RE.search(line):
            should_drop.append(False)
            processed.append(line)  # هیچ استیکری نداره - کاملاً دست‌نخورده
            continue

        without_emoji = EMOJI_RE.sub("", line).strip()
        had_content = bool(line.strip())
        if had_content and not without_emoji:
            should_drop.append(True)
            processed.append(line)  # محتواش استفاده نمی‌شه، فقط جای‌نگه‌دار
        else:
            should_drop.append(False)
            processed.append(without_emoji)

    result = _drop_lines_and_collapse(processed, should_drop)
    return "\n".join(result)


# ---------------------------------------------------------------------------
# اعمالِ تصمیم‌های هوش مصنوعی روی هشتگ‌ها + چیدمانِ نهاییِ منبع/هشتگ‌های جدید
# ---------------------------------------------------------------------------

def apply_hashtag_actions(text: str, actions: list) -> str:
    """
    actions: [{"tag": "#...", "action": "remove"}, یا
              {"tag": "#...", "action": "convert_to_name", "name": "..."}]
    برای ایمنی: اگه tag واقعاً توی متن نباشه، اون تصمیم نادیده گرفته می‌شه
    (هرگز چیزی که هوش مصنوعی اشتباهی پیشنهاد داده رو با زور اعمال نمی‌کنیم).
    """
    if not actions or not text:
        return text or ""

    lines = text.split("\n")
    for act in actions:
        tag = (act.get("tag") or "").strip()
        kind = act.get("action")
        if not tag or tag not in text:
            continue  # ایمنی: هشتگی که واقعاً توی متن نیست، دست‌نخورده رد می‌شه

        if kind == "convert_to_name":
            name = (act.get("name") or "").strip()
            if not name:
                continue
            lines = [ln.replace(tag, name) for ln in lines]
            text = "\n".join(lines)

        elif kind == "remove":
            new_lines = []
            should_drop = []
            for ln in lines:
                if tag in ln:
                    had_content = bool(ln.strip())
                    replaced = ln.replace(tag, "").strip()
                    if had_content and not replaced:
                        should_drop.append(True)
                        new_lines.append(ln)
                    else:
                        should_drop.append(False)
                        new_lines.append(replaced)
                else:
                    should_drop.append(False)
                    new_lines.append(ln)
            lines = _drop_lines_and_collapse(new_lines, should_drop)
            text = "\n".join(lines)

    return text


def strip_exact_block(text: str, block: str) -> str:
    """
    اگه دقیقاً همین بلوکِ متن (یک یا چند خطِ پشت‌سرهم) عیناً توی text پیدا
    بشه، حذفش می‌کنه (و خط خالیِ همسایه‌ش رو هم جمع می‌کنه، مثل حذف استیکر).
    اگه پیدا نشه، متن کاملاً دست‌نخورده برمی‌گرده - برای ایمنی، هیچ‌وقت
    چیزی رو حدسی/تقریبی حذف نمی‌کنیم؛ فراخوان باید این حالت رو تشخیص بده
    (با مقایسه‌ی قبل/بعد) و طبق اون تصمیم بگیره.
    """
    if not block or not block.strip():
        return text
    block_lines = block.strip("\n").split("\n")
    lines = text.split("\n")
    n, m = len(lines), len(block_lines)
    for start in range(0, n - m + 1):
        if lines[start:start + m] == block_lines:
            should_drop = [False] * n
            for i in range(start, start + m):
                should_drop[i] = True
            return "\n".join(_drop_lines_and_collapse(lines, should_drop))
    return text  # پیدا نشد - دست‌نخورده برگردون (ایمنی)


def build_final_body(clean_body: str, source_text: str = "", hashtags: list = None) -> str:
    """
    ترتیب نهایی: متنِ پاک‌شده -> (خط فاصله + 📚 منبع: ... اگه بود) ->
    (هشتگ‌های جدید، بدون خط فاصله از قسمت قبلی) - امضای 🏛️ خارج از این
    تابع و با یک خط فاصله اضافه می‌شه (توی finalize_clean_text).
    """
    parts = [(clean_body or "").strip()]

    if source_text and source_text.strip():
        parts.append("")
        parts.append(f"📚 منبع: {source_text.strip()}")

    if hashtags:
        valid_tags = [h for h in hashtags if h and h.startswith("#") and " " not in h][:5]
        if valid_tags:
            parts.append(" ".join(valid_tags))

    return "\n".join(parts)


def mechanical_pre_clean(raw_text: str) -> str:
    """
    مرحله‌ی مکانیکی و صد در صد امن (بدون هوش مصنوعی): حذف استیکر (+ فاصله/خط
    خالیِ اطرافش)، حذف بلوکِ امضای انتهایی (آیدی، اینستاگرام، لینک t.me،
    خط‌های برچسب‌مانند)، و حذف mention/لینکِ تلگرامیِ باقی‌مونده وسط متن.
    همینه که هم برای فرستادن به هوش مصنوعی استفاده می‌شه هم پایه‌ی نهاییِ
    اعمالِ تصمیم‌های هوش مصنوعی (منبع/هشتگ) قرار می‌گیره - تا هشتگ‌هایی که
    هوش مصنوعی دیده، دقیقاً با همون شکل توی متنِ نهایی هم پیدا بشن.
    """
    text = strip_sticker_lines(raw_text or "")
    text = strip_trailing_signature_block(text, treat_hashtag_as_signature=False)
    text = USERNAME_RE.sub("", text)
    text = TME_LINK_RE.sub("", text)
    return text.strip()


def clean_and_assemble_post_text(raw_text: str, ai_extra: dict = None) -> str:
    """
    خروجیِ نهاییِ متنِ ساده (بدون بولد، بدون امضا) که باید در صف پست ذخیره
    بشه. ai_extra اختیاریه (دیکشنری با کلیدهای hashtag_actions/source_text/
    suggested_hashtags) - اگه هوش مصنوعی برای این پست شکست خورده باشه،
    None پاس داده می‌شه و فقط پاک‌سازیِ مکانیکیِ استیکر/امضا انجام می‌شه،
    بدون اضافه‌کردن منبع یا هشتگ جدید (برای جلوگیری از خطا، حداقلِ کار انجام
    می‌شه نه حداکثر حدس‌وگمان).
    """
    text = mechanical_pre_clean(raw_text)

    source_text = ""
    hashtags = []
    if ai_extra:
        actions = ai_extra.get("hashtag_actions") or []
        text = apply_hashtag_actions(text, actions).strip()

        raw_source_text = (ai_extra.get("source_text") or "").strip()
        source_line_raw = (ai_extra.get("source_line_raw") or "").strip()

        if raw_source_text and source_line_raw:
            stripped_text = strip_exact_block(text, source_line_raw)
            if stripped_text != text:
                # خط اصلیِ منبع دقیقاً پیدا و حذف شد - حالا با فرمت استاندارد اضافه می‌کنیم
                text = stripped_text.strip()
                source_text = raw_source_text
            # اگه پیدا نشد: برای جلوگیری از تکرار/دوگانگیِ منبع، این‌بار از
            # اضافه‌کردن منبعِ استاندارد صرف‌نظر می‌کنیم و متن دست‌نخورده می‌مونه
            # (خطِ منبعِ اصلیِ خودِ کانال - اگه بوده - همون‌طور که بود باقی می‌مونه)

        hashtags = ai_extra.get("suggested_hashtags") or []

    return build_final_body(text, source_text, hashtags)


def finalize_clean_text(already_clean_text: str) -> str:
    """
    وقتی متن از قبل (توی مرحله‌ی اسکن، با clean_and_assemble_post_text)
    کامل پاک و آماده شده، این تابع فقط بولدش می‌کنه و امضای نهایی رو اضافه
    می‌کنه - عمداً دوباره strip_trailing_signature_block رو صدا نمی‌زنه، چون
    اون تابع هر خطی که با # شروع بشه رو امضا حساب می‌کنه و هشتگ‌های تازه‌ای
    که خودمون اضافه کردیم رو پاک می‌کرد.
    """
    bold_text = f"<b>{html.escape((already_clean_text or '').strip())}</b>"
    return f"{bold_text}\n\n{config.SIGNATURE}"


# ---------------------------------------------------------------------------
# تشخیص دسته‌بندی محتوا
# ---------------------------------------------------------------------------

def _estimate_telegram_lines(text: str) -> int:
    """
    تعداد خطی که این متن توی تلگرام واقعاً اشغال می‌کنه رو تخمین می‌زنه -
    هم خط‌های واقعی (\\n) رو حساب می‌کنه، هم اینکه هر پاراگراف طولانی خودش
    به چند خط شکسته می‌شه (بر اساس عرض معمول صفحه‌ی موبایل).
    """
    text = text or ""
    paragraphs = text.split("\n")
    total_lines = 0
    for para in paragraphs:
        if not para.strip():
            total_lines += 1
            continue
        wrapped = -(-len(para) // CHARS_PER_TELEGRAM_LINE)  # سقف تقسیم
        total_lines += max(1, wrapped)
    return total_lines


def _is_short_photo_caption(text: str) -> bool:
    """
    آیا این متن شبیه یک کپشن کوتاه (۱ تا ۳ خطِ واقعیِ تلگرام، نه فقط \\n)
    برای یک عکس تاریخیه؟ این‌جور پست‌ها معمولاً یک توضیح کوتاهن، با اشاره
    به سال (شمسی/قمری/میلادی) و/یا مکان (شهر، کشور، موزه...).
    """
    return _estimate_telegram_lines(text) <= 3


def _mentions_year_or_place(text: str) -> bool:
    text = text or ""
    if YEAR_PATTERN.search(text):
        return True
    return any(keyword in text for keyword in PLACE_KEYWORDS)


def _looks_like_narrative_dialogue(text: str) -> bool:
    """
    آیا این متن شبیه یک حکایت/جوک روایی با دیالوگه؟ (بدون اسم خاص یا کلمه‌ی
    صریح «حکایت»). این‌جور متن‌ها معمولاً طولانی‌ان و چند تا دونقطه/گیومه دارن.
    """
    text = text or ""
    if len(text) < 200:
        return False
    dialogue_markers = text.count(":") + text.count("«") + text.count("»") + text.count('"')
    return dialogue_markers >= 2


def detect_category(text: str, has_media: bool = False) -> str:
    text = text or ""

    for keyword in config.CATEGORY_KEYWORDS[config.CATEGORY_HEKAYAT]:
        if keyword in text:
            return config.CATEGORY_HEKAYAT

    for keyword in config.CATEGORY_KEYWORDS[config.CATEGORY_SOKHAN_BOZORGAN]:
        if keyword in text:
            return config.CATEGORY_SOKHAN_BOZORGAN

    if has_media and _is_short_photo_caption(text):
        return config.CATEGORY_AKS_IRAN_QADIM

    if not has_media and _looks_like_narrative_dialogue(text):
        return config.CATEGORY_HEKAYAT

    return config.CATEGORY_GENERAL


# ---------------------------------------------------------------------------
# پردازش کپشن کتاب‌ها
# ---------------------------------------------------------------------------

PERSIAN_ORDINAL_TO_DIGIT = {
    "اول": "1", "یک": "1", "یکم": "1",
    "دوم": "2", "دو": "2",
    "سوم": "3", "سه": "3",
    "چهارم": "4", "چهار": "4",
    "پنجم": "5", "پنج": "5",
    "ششم": "6", "شش": "6",
    "هفتم": "7", "هفت": "7",
    "هشتم": "8", "هشت": "8",
    "نهم": "9", "نه": "9",
    "دهم": "10", "ده": "10",
    "یازدهم": "11", "دوازدهم": "12", "سیزدهم": "13", "چهاردهم": "14",
    "پانزدهم": "15", "شانزدهم": "16", "هفدهم": "17", "هجدهم": "18",
    "نوزدهم": "19", "بیستم": "20",
}

LINE1_RE = re.compile(r"^(?P<sticker>\S+?)(?P<space>\s*)(?P<title>.+)$")
VOLUME_RE = re.compile(r"جلد\s+([آ-یa-zA-Z0-9]+)")
AUTHOR_RE = re.compile(r"^(?P<sticker>\S+)?\s*(?P<label>تال?ی?ف)\s*:\s*(?P<name>.+)$")
TRANSLATOR_RE = re.compile(r"^(?P<sticker>\S+)?\s*ترجمه\s*:\s*(?P<name>.+)$")
CHANNEL_ID_RE = re.compile(r"^@\w+$")


def _fix_sticker_spacing(sticker: str, rest: str) -> str:
    return f"{sticker} {rest.strip()}"


def _persian_ordinal_to_digit(word: str) -> str:
    word = word.strip()
    if word.isdigit():
        return word
    return PERSIAN_ORDINAL_TO_DIGIT.get(word, word)


def _strip_ketab_prefix(title: str) -> str:
    """برای اسم فایل: اگه اسم کتاب با کلمه‌ی «کتاب» شروع شده، فقط همون‌جا حذفش می‌کنه (کپشن دست‌نخورده می‌مونه)."""
    t = title.strip()
    if t.startswith("کتاب "):
        rest = t[len("کتاب "):].strip()
        return rest if rest else t
    return t


def process_book_caption(raw_caption: str):
    lines = [ln for ln in (raw_caption or "").split("\n")]

    book_title = None
    author_line = None
    translator_line = None
    volume_number = None
    first_line = None

    for raw_line in lines:
        line = raw_line.strip()

        if not line:
            continue

        if CHANNEL_ID_RE.match(line):
            continue

        vol_match = VOLUME_RE.search(line)
        if vol_match and ("جلد" in line):
            volume_number = _persian_ordinal_to_digit(vol_match.group(1))
            continue

        author_match = AUTHOR_RE.match(line)
        translator_match = TRANSLATOR_RE.match(line)

        if translator_match:
            sticker = "✍️"
            name = translator_match.group("name").strip()
            translator_line = _fix_sticker_spacing(sticker, f"ترجمه: {name}")
            continue

        if author_match:
            sticker = author_match.group("sticker") or "👤"
            name = author_match.group("name").strip()
            author_line = _fix_sticker_spacing(sticker, f"تالیف: {name}")
            continue

        if book_title is None:
            m = LINE1_RE.match(line)
            if m:
                sticker = m.group("sticker")
                title = m.group("title").strip()
                book_title = title
                line = _fix_sticker_spacing(sticker, title)
            first_line = line
            continue

    body_lines = [ln for ln in [first_line if book_title else None, author_line, translator_line] if ln]
    if volume_number:
        body_lines.append(f"📕 جلد {volume_number}")

    bold_body = f"<b>{html.escape(chr(10).join(body_lines))}</b>"
    clean_caption = f"{bold_body}\n\n{config.SIGNATURE}"

    return {
        "clean_caption": clean_caption,
        "book_title": book_title or "کتاب بدون‌نام",
        "filename_title": _strip_ketab_prefix(book_title or "کتاب بدون‌نام"),
        "volume_number": volume_number,
        "author_line": author_line,
        "translator_line": translator_line,
    }


def build_book_filename(book_title: str, volume_number, original_extension: str) -> str:
    ext = original_extension.lstrip(".")
    base = f"(@Tarikhgan) {book_title}"
    if volume_number:
        base = f"{base} {volume_number}"
    base = re.sub(r'[\\/:*?"<>|]', "", base)
    return f"{base}.{ext}"
