# -*- coding: utf-8 -*-
"""
توابع پردازش متن: پاک‌سازی کپشن پست‌های کانال‌ها، پردازش کپشن کتاب‌ها،
تشخیص دسته‌بندی محتوا، و تشخیص/تبدیل شماره‌ی جلد.
"""

import re
import config

def normalize_text_for_dedupe(text: str) -> str:
    """
    متن رو برای مقایسه‌ی «آیا این پست تکراریه؟» ساده‌سازی می‌کنه: فاصله‌های
    اضافه، ایموجی، و علائم نگارشی رو حذف می‌کنه تا نسخه‌های کمی متفاوت از
    یک متنِ کپی‌شده هم به‌عنوان یکی شناسایی بشن.
    """
    text = text or ""
    text = re.sub(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]", "", text)
    text = re.sub(r"[^\w\s]", "", text)
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text


def text_hash_for_dedupe(text: str) -> str:
    import hashlib
    normalized = normalize_text_for_dedupe(text)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# پاک‌سازی پست‌های استخراج‌شده از کانال‌های دیگر
# ---------------------------------------------------------------------------

USERNAME_RE = re.compile(r"@\w+")
TME_LINK_RE = re.compile(r"(https?://)?t\.me/\S+")


def clean_channel_post_text(original_text: str) -> str:
    text = original_text or ""
    text = USERNAME_RE.sub("", text)
    text = TME_LINK_RE.sub("", text)
    text = text.rstrip()
    return f"{text}\n\n{config.SIGNATURE}"


# ---------------------------------------------------------------------------
# تشخیص دسته‌بندی محتوا بر اساس کلیدواژه
# ---------------------------------------------------------------------------

def detect_category(text: str, has_media: bool = False) -> str:
    text = text or ""
    for category, keywords in config.CATEGORY_KEYWORDS.items():
        if any(keyword in text for keyword in keywords):
            return category
    if has_media and len(text) <= 150:
        return config.CATEGORY_AKS_IRAN_QADIM
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


def process_book_caption(raw_caption: str):
    lines = [ln for ln in (raw_caption or "").split("\n")]

    book_title = None
    author_line = None
    translator_line = None
    volume_number = None
    signature_seen = False
    first_line = None

    for raw_line in lines:
        line = raw_line.strip()

        if not line:
            continue

        if CHANNEL_ID_RE.match(line):
            signature_seen = True
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

    clean_caption = "\n".join(body_lines) + f"\n\n{config.SIGNATURE}"

    return {
        "clean_caption": clean_caption,
        "book_title": book_title or "کتاب بدون‌نام",
        "volume_number": volume_number,
    }


def build_book_filename(book_title: str, volume_number, original_extension: str) -> str:
    ext = original_extension.lstrip(".")
    base = f"(@Tarikhgan) {book_title}"
    if volume_number:
        base = f"{base} {volume_number}"
    base = re.sub(r'[\\/:*?"<>|]', "", base)
    return f"{base}.{ext}"
