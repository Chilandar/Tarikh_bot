# -*- coding: utf-8 -*-
"""
استخراج گلچین از کتاب‌ها: هر صفحه رو می‌خونه (اگه متن قابل‌کپی داشت مستقیم،
وگرنه با OCR رایگان فارسی/Tesseract)، بعد از Gemini می‌خواد جمله/پاراگراف‌های
جالب رو استخراج کنه. تعداد پست‌های خروجی ثابت نیست - هرچی Gemini پیدا کرد.

نکته‌ی امنیتی/حق‌نشر: این ماژول فقط چند جمله‌ی کوتاه از هر صفحه استخراج
می‌کنه (نه کل صفحه یا کل کتاب) و خودِ فایل کتاب هرگز وارد ریپو نمی‌شه.
"""

import html
import json
import requests

import config
from ai_classify import GEMINI_URL

try:
    import PyPDF2
except ImportError:
    PyPDF2 = None

try:
    from pdf2image import convert_from_path
    import pytesseract
except ImportError:
    convert_from_path = None
    pytesseract = None


def get_book_pages(pdf_path: str, max_pages: int = None):
    """
    متن هر صفحه رو برمی‌گردونه: [(شماره_صفحه, متن), ...]
    اول تلاش می‌کنه متن رو مستقیم از PDF بکشه (سریع و دقیق)؛ اگه صفحه
    عملاً متن نداشت (یعنی اسکن‌شده/عکسیه)، با Tesseract OCR (فارسی) می‌خونتش.
    """
    if PyPDF2 is None:
        print("⚠️ PyPDF2 نصب نیست - نمی‌شه کتاب رو خوند.")
        return []

    max_pages = max_pages or config.BOOK_EXCERPT_MAX_PAGES

    try:
        reader = PyPDF2.PdfReader(pdf_path)
        total_pages = len(reader.pages)
    except Exception as e:
        print(f"⚠️ خطا در باز‌کردن PDF: {e}")
        return []

    pages_to_process = min(total_pages, max_pages)
    pages = []

    for i in range(pages_to_process):
        page_num = i + 1
        text = ""
        try:
            text = reader.pages[i].extract_text() or ""
        except Exception:
            text = ""

        if len(text.strip()) < config.BOOK_EXCERPT_MIN_TEXT_PER_PAGE:
            if convert_from_path and pytesseract:
                try:
                    images = convert_from_path(pdf_path, first_page=page_num, last_page=page_num, dpi=200)
                    if images:
                        text = pytesseract.image_to_string(images[0], lang="fas")
                except Exception as e:
                    print(f"⚠️ OCR صفحه {page_num} شکست خورد: {e}")
            else:
                print("⚠️ ابزار OCR (pdf2image/pytesseract) نصب نیست - این صفحه رد شد.")

        if text.strip():
            pages.append((page_num, text.strip()))

    return pages


def extract_excerpts_from_page(api_key: str, page_num: int, page_text: str, book_title: str):
    if not api_key or not page_text:
        return []

    prompt = f"""این متنِ صفحه‌ی {page_num} از کتاب «{book_title}» است:

\"\"\"{page_text}\"\"\"

اگه توی این متن جمله یا پاراگراف کوتاهِ جالب، تاریخی، یا جذابی هست که بشه
به‌تنهایی (بدون نیاز به بقیه‌ی کتاب) به‌عنوان یک پست مستقل توی یک کانال
تاریخی منتشر کرد، اون رو دقیقاً و کلمه‌به‌کلمه (نه خلاصه یا بازنویسی) از
همین متن استخراج کن. ممکنه هیچی جالب نباشه، ممکنه چند مورد باشه.

فقط و فقط یک JSON خام (بدون ```json و بدون هیچ توضیح اضافه) با این فرمت
برگردون: {{"excerpts": ["نقل قول اول (اگه بود)", "نقل قول دوم (اگه بود)"]}}
اگه هیچی مناسب نبود: {{"excerpts": []}}"""

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
        parsed = json.loads(raw)
        return parsed.get("excerpts", [])
    except Exception as e:
        print(f"⚠️ استخراج گلچین از صفحه {page_num} شکست خورد: {e}")
        return []


def build_excerpt_caption(quote: str, book_meta: dict, page_num: int) -> str:
    lines = [quote.strip(), "", f"📚 {book_meta.get('book_title', '')}"]
    if book_meta.get("author_line"):
        lines.append(book_meta["author_line"])
    if book_meta.get("translator_line"):
        lines.append(book_meta["translator_line"])
    lines.append(f"📄 صفحه {page_num}")
    lines.append("تدوین تاریخگان")

    body = "\n".join(lines)
    bold = f"<b>{html.escape(body)}</b>"
    return f"{bold}\n\n{config.SIGNATURE}"


def generate_excerpt_items(api_key: str, pdf_path: str, book_meta: dict, pages=None):
    """
    کل فرآیند: صفحات کتاب رو می‌خونه، از هرکدوم گلچین می‌گیره، و لیست
    آیتم‌های آماده برای اضافه‌شدن به صف پست رو برمی‌گردونه. تعداد خروجی
    متغیره - هرچی واقعاً جالب پیدا بشه.

    اگه `pages` از بیرون داده بشه (مثلاً چون همون صفحات برای ساخت کوییز هم
    لازمه)، دوباره OCR/خوندن PDF انجام نمی‌شه.
    """
    if not api_key:
        print("GEMINI_API_KEY تنظیم نشده - استخراج گلچین از کتاب رد شد.")
        return []

    if pages is None:
        pages = get_book_pages(pdf_path)
    items = []

    for page_num, page_text in pages:
        excerpts = extract_excerpts_from_page(api_key, page_num, page_text, book_meta.get("book_title", ""))
        for quote in excerpts:
            if not quote or not quote.strip():
                continue
            caption = build_excerpt_caption(quote, book_meta, page_num)
            items.append({
                "category": config.CATEGORY_BOOK_EXCERPT,
                "source_type": "book_excerpt",
                "caption": caption,
                "score": len(quote.strip()),
                "used": False,
            })

    print(f"📖 از {len(pages)} صفحه‌ی بررسی‌شده، {len(items)} گلچین استخراج شد.")
    return items
          
