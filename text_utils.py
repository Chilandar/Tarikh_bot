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


def _looks_like_signature_line(line: str) -> bool:
    """
    آیا این خط شبیه یک خط امضا/تبلیغ (آیدی کانال، اینستاگرام، هشتگ اسم
    کانال، یا برچسب‌هایی مثل «کانال تلگرام ...») هست؟
    """
    s = line.strip()
    if not s:
        return True  # خط خالی - در فرآیند حذفِ انتهای پیام، ردش می‌کنیم
    lower = s.lower()
    if "@" in s:
        return True
    if "instagram" in lower or "t.me/" in lower:
        return True
    if s.startswith("#"):
        return True
    for sym in SIGNATURE_MARKER_SYMBOLS:
        if s.startswith(sym) and len(s) <= 60:
            return True
    return False


def strip_trailing_signature_block(text: str) -> str:
    """
    از انتهای متن، خط‌به‌خط جلو میره و هر خطی که شبیه امضا/تبلیغ (آیدی کانال،
    اینستاگرام، هشتگ، خط‌های برچسب‌مانند) یا خالی باشه رو حذف می‌کنه، تا به
    اولین خط واقعیِ محتوا برسه.
    """
    lines = text.split("\n")
    while lines and not lines[-1].strip():
        lines.pop()
    while lines and _looks_like_signature_line(lines[-1]):
        lines.pop()
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


def clean_channel_post_text(original_text: str) -> str:
    """
    آیدی/یوزرنیم و لینک‌های t.me رو از متن پست حذف می‌کنه، متن اصلی رو بولد
    می‌کنه، و امضای خودمون رو (بدون بولد) با یک خط فاصله‌ی کامل در انتها اضافه می‌کنه.
    """
    text = original_text or ""
    text = strip_trailing_signature_block(text)
    text = USERNAME_RE.sub("", text)
    text = TME_LINK_RE.sub("", text)
    text = text.rstrip()
    bold_text = f"<b>{html.escape(text)}</b>"
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
