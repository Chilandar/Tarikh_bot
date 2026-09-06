# -*- coding: utf-8 -*-
"""
ساخت سوال کوییز تاریخی از صفحات یک کتاب.

عمداً صفحات رو خودش نمی‌خونه (تا OCR تکراری نشه) - همون لیست صفحاتی که
book_excerpts.get_book_pages() قبلاً برای گلچین‌گیری ساخته، از بیرون بهش
داده می‌شه.
"""

import json
import random
import requests

from ai_classify import GEMINI_URL

QUESTION_PROMPT = """این متنِ صفحه‌ی {page_num} از کتاب «{book_title}» است:

\"\"\"{page_text}\"\"\"

اگه توی این متن اطلاعات کافی برای ساخت یک سوال چهارگزینه‌ی تاریخیِ *ساده و
عمومی* هست (چیزی که یک مخاطب عادیِ علاقه‌مند به تاریخ هم بتونه جواب بده، نه
جزئیات ریز و فرعی)، اون رو بساز. فقط از همین متن استفاده کن، هیچ اطلاعات
خارجی اضافه نکن. اگه غلط تایپی/OCR واضحی توی متن دیدی، در ذهنت تصحیحش کن.

اگه متن مناسب نبود (فهرست مطالب، مقدمه، منابع و امثالش)، دقیقاً همین را
برگردان: {{"skip": true}}

در غیر این صورت فقط یک JSON خام (بدون ```json و بدون هیچ توضیح اضافه) با این
فرمت برگردون:
{{"question": "متن سوال (حداکثر ۲۰۰ کاراکتر)",
  "options": ["گزینه ۱", "گزینه ۲", "گزینه ۳", "گزینه ۴"],
  "correct_index": 0,
  "explanation": "توضیح خیلی کوتاه که چرا این جواب درسته (حداکثر ۱۵۰ کاراکتر)"}}

دقیقاً ۴ گزینه، فقط یکی درست، گزینه‌های غلط معقول و نزدیک به موضوع.
correct_index اندیس صفرمبنای گزینه‌ی درست در آرایه‌ی options است."""


def _truncate(s, limit):
    s = (s or "").strip()
    return s if len(s) <= limit else s[: limit - 1].rstrip() + "…"


def extract_question_from_page(api_key: str, page_num: int, page_text: str, book_title: str):
    if not api_key or not page_text:
        return None

    prompt = QUESTION_PROMPT.format(page_num=page_num, book_title=book_title, page_text=page_text)

    try:
        resp = requests.post(
            GEMINI_URL,
            headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
            json={"contents": [{"parts": [{"text": prompt}]}]},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        raw = data["candidates"][0]["content"]["parts"][0]["text"].strip()
        if raw.startswith("```"):
            raw = raw.strip("`").replace("json", "", 1).strip()
        result = json.loads(raw)
    except Exception as e:
        print(f"⚠️ ساخت سوال از صفحه {page_num} شکست خورد: {e}")
        return None

    if result.get("skip"):
        return None

    try:
        question = result["question"]
        options = list(result["options"])
        correct_index = int(result["correct_index"])
        explanation = result.get("explanation", "")
        assert len(options) == 4 and 0 <= correct_index < 4
    except Exception as e:
        print(f"⚠️ فرمت سوال صفحه {page_num} نامعتبر بود: {e} -> {result}")
        return None

    # ترتیب گزینه‌ها رو به‌هم می‌ریزیم تا جای گزینه‌ی درست همیشه تصادفی باشه
    order = list(range(4))
    random.shuffle(order)
    shuffled_options = [options[i] for i in order]
    shuffled_correct_index = order.index(correct_index)

    return {
        "question": _truncate(question, 290),
        "options": [_truncate(o, 95) for o in shuffled_options],
        "correct_index": shuffled_correct_index,
        "explanation": _truncate(explanation, 195),
        "book_title": book_title,
        "used": False,
    }


def generate_quiz_items(api_key: str, pages, book_title: str):
    """
    pages: [(شماره_صفحه, متن), ...] - همون خروجی book_excerpts.get_book_pages
    """
    if not api_key:
        print("GEMINI_API_KEY تنظیم نشده - ساخت کوییز از کتاب رد شد.")
        return []

    items = []
    for page_num, page_text in pages:
        item = extract_question_from_page(api_key, page_num, page_text, book_title)
        if item:
            items.append(item)

    print(f"🧩 از {len(pages)} صفحه‌ی بررسی‌شده، {len(items)} سوال کوییز ساخته شد.")
    return items
