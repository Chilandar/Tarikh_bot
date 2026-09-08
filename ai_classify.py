# -*- coding: utf-8 -*-
"""
دسته‌بندی و ارزیابی کیفیت پست‌ها با Gemini (رایگان). اگه GEMINI_API_KEY تنظیم
نباشه یا درخواست شکست بخوره، None برمی‌گرده و کد اصلی به روش کلیدواژه‌ای
(ضعیف‌تر) برمی‌گرده.

برای جلوگیری از محدودیت نرخ Google، به‌جای یک درخواست به‌ازای هر پست، چند
پست با هم در یک درخواست فرستاده می‌شن (classify_batch_with_gemini). تابع
تکی (classify_with_gemini) هنوز هست و برای جاهای دیگه (مثل quiz_extract) کار
می‌کنه، ولی scan_history.py از نسخه‌ی دسته‌ای استفاده می‌کنه.
"""

import json
import time
import requests

GEMINI_MODEL = "gemini-3.5-flash"
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"

VALID_CATEGORIES = {"sokhan_bozorgan", "aks_iran_qadim", "hekayat_dastan_sher", "general"}

BATCH_SIZE = 15  # چند پست در هر درخواست به Gemini فرستاده بشه

_last_call_time = [0.0]
MIN_SECONDS_BETWEEN_CALLS = 5.0  # حدود ۱۲ درخواست در دقیقه - زیر سقف رایگان


def call_gemini_raw(api_key: str, prompt: str, timeout: int = 30, max_retries: int = 3):
    """یک درخواست به Gemini، با فاصله‌ی خودکار بین تماس‌ها و تلاش مجدد در صورت ۴۲۹."""
    for attempt in range(max_retries):
        elapsed = time.time() - _last_call_time[0]
        if elapsed < MIN_SECONDS_BETWEEN_CALLS:
            time.sleep(MIN_SECONDS_BETWEEN_CALLS - elapsed)

        resp = requests.post(
            GEMINI_URL,
            headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
            json={"contents": [{"parts": [{"text": prompt}]}]},
            timeout=timeout,
        )
        _last_call_time[0] = time.time()

        if resp.status_code == 429:
            wait = 20 * (attempt + 1)
            print(f"⚠️ محدودیت نرخ Gemini (429) - {wait} ثانیه صبر می‌کنیم...")
            time.sleep(wait)
            continue

        resp.raise_for_status()
        return resp.json()

    raise RuntimeError("Gemini API: بعد از چند تلاش هنوز ۴۲۹ می‌ده")


CATEGORY_RULES = """تو دستیار دسته‌بندی محتوا برای یک کانال تلگرامی تاریخی/فرهنگی به اسم
«تاریخگان» هستی. این کانال پست‌هایی درباره‌ی تاریخ، فرهنگ، ادبیات و تصاویر
قدیمی منتشر می‌کنه. باید هر متنی که میدم رو بر اساس تعریف‌های دقیقی که میدم
بررسی کنی و دو تصمیم بگیری: این پست به کدوم دسته تعلق داره، و آیا اصلاً ارزش
بازنشر داره یا نه.

=== دسته‌ها (دقیقاً به همین ترتیب اولویت بررسی کن) ===

۱. sokhan_bozorgan (سخن بزرگان):
هر پستی که یک نقل‌قول یا جمله‌ی حکیمانه/فکری رو به یک شخص واقعی و مشخص
(نه لزوماً ایرانی) نسبت می‌ده - فیلسوف، نویسنده، شاعر معاصر (نه شاعر
کلاسیک فارسی - اونا حکایت/شعرن)، سیاست‌مدار، دانشمند، مصلح اجتماعی، هنرمند،
یا هر چهره‌ی شناخته‌شده‌ی دیگه، از هر ملیت و هر دوره‌ای (باستان تا معاصر).
تو خودت دانش گسترده‌ای از افراد مشهور تاریخ جهان داری - محدود به هیچ لیست
کوچیکی نیستی؛ اگه اسم شخصی اومد که می‌دونی واقعاً کیه (حتی اگه خیلی معروف
نباشه) و جمله‌ای به‌عنوان سخن او نقل شده، این دسته‌ست. الگوی رایج: ابتدا
خودِ جمله میاد، در پایان اسم گوینده.

۲. hekayat_dastan_sher (حکایت / داستان / شعر):
- هر حکایت یا داستان کوتاه روایی، حتی بدون نام نویسنده و بدون کلمه‌ی
  صریح «حکایت» - این‌ها رو از روی لحن و ساختار روایی تشخیص بده: شروع با
  معرفی یک موقعیت یا شخصیت، پیشرفت یک ماجرا (اغلب با دیالوگ مستقیم بین دو
  یا چند نفر با علامت «:» یا گیومه)، و رسیدن به یک نتیجه/نکته/طنز در پایان.
  این‌ها ممکنه یک لطیفه یا حکایت اخلاقی باشن، نه لزوماً یک متن رسمی کلاسیک.
- شعر: هر قطعه شعر، از هر شاعری (کلاسیک یا معاصر، فارسی یا ترجمه).
- سخنانی که به شاعران/عارفان/حکایت‌گویان کلاسیک فارسی نسبت داده شده
  (حافظ، سعدی، مولوی، خیام، عبید زاکانی، خواجه عبدالله انصاری، عطار،
  و مشابه این‌ها) هم همینجا قرار می‌گیره، نه در سخن بزرگان.

۳. aks_iran_qadim (تصویر تاریخی):
توجه: این دسته فقط مخصوص ایران نیست - هر عکس یا فیلم تاریخی از هر تمدن،
دوره، یا کشوری (مثلاً یک کوزه‌ی ایلامی، یک زن دوره‌ی قاجار، یک بنای باستانی
مصری، یک خیابان قدیمی اروپایی) در این دسته می‌گنجه. ویژگی اصلی‌اش اینه که
پست حتماً عکس/فیلم داره و متنِ همراهش کوتاهه (۱ تا ۳ خط، نه یک پاراگراف
کامل) و صرفاً توصیف‌کننده‌ی همون تصویره - می‌گه این چیه، مال کِیه، کجاست
(مثلاً اسم یک موزه، شهر، کشور، دوره‌ی تاریخی، یا یک سال مشخص). اگه پستی
عکس داره ولی متنش طولانی و روایی/تحلیلیه (نه صرفاً توصیف تصویر)، اینجا
جاش نیست - عمومی حساب می‌شه.

۴. general (عمومی):
هر چیز دیگه‌ای که در بالا نگنجه - مثلاً یک متن تحلیلی/توضیحی بلندتر
درباره‌ی یک رویداد یا موضوع تاریخی، بدون نقل‌قول مشخص و بدون ساختار روایی
حکایت‌گونه.

=== ارزیابی کیفیت (is_good) ===
پست رو false علامت بزن اگه:
- عملاً بدون توضیح معناداره (فقط عکس با یک کلمه یا بدون هیچ متنی).
- تبلیغاتیه (فروش، لینک خرید، تبلیغ کانال/کسب‌وکار دیگه).
- محتوای مذهبی/انگیزشیِ عمومی و کلیشه‌ای بدون ربط مشخص به تاریخ/فرهنگ.
- متن OCR/تایپی به‌قدری خراب یا بی‌معنیه که قابل‌فهم نیست.
- تکراری/کلیشه‌ای به‌نظر می‌رسه (چیزی که هزاران بار در کانال‌های مشابه دیده
  شده، بدون هیچ زاویه یا جزئیات خاص).
در غیر این صورت، اگه محتوا معنادار، جذاب، و مرتبط با روحیه‌ی یک کانال
تاریخی/فرهنگیه، true بذار."""


PROMPT_TEMPLATE = CATEGORY_RULES + """

=== ورودی ===
این پست {media_desc}.

متن پست:
\"\"\"{text}\"\"\"

=== خروجی ===
فقط و فقط یک JSON خام (بدون ```json و بدون هیچ توضیح اضافه‌ای) دقیقاً با
این فرمت برگردون:
{{"category": "یکی از چهار مقدار: sokhan_bozorgan / aks_iran_qadim / hekayat_dastan_sher / general", "is_good": true یا false}}"""


BATCH_PROMPT_TEMPLATE = CATEGORY_RULES + """

=== ورودی ===
در ادامه {n} پست جداگانه میاد، هرکدوم با یک شماره. برای *هرکدوم به‌طور
مستقل* (بدون ربط‌دادن به بقیه) دسته و is_good رو طبق همون قوانین بالا تعیین
کن.

{items_block}

=== خروجی ===
فقط و فقط یک آرایه‌ی JSON خام برگردون (بدون ```json و بدون هیچ توضیح
اضافه‌ای) - دقیقاً {n} عضو، دقیقاً به همون ترتیب شماره‌های بالا. هر عضو باید
دقیقاً این شکل باشه:
[{{"category": "...", "is_good": true}}, {{"category": "...", "is_good": false}}, ...]"""


def _parse_result(parsed) -> dict | None:
    category = parsed.get("category")
    is_good = parsed.get("is_good", True)
    if category not in VALID_CATEGORIES:
        return None
    return {"category": category, "is_good": bool(is_good)}


def classify_with_gemini(api_key: str, text: str, has_media: bool):
    """دسته‌بندی یک پست تکی - یک درخواست کامل به Gemini."""
    if not api_key:
        return None

    media_desc = "عکس یا فیلم دارد" if has_media else "عکس یا فیلم ندارد"
    prompt = PROMPT_TEMPLATE.format(media_desc=media_desc, text=text)

    try:
        data = call_gemini_raw(api_key, prompt, timeout=20)
        raw_text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
        if raw_text.startswith("```"):
            raw_text = raw_text.strip("`")
            raw_text = raw_text.replace("json", "", 1).strip()

        parsed = json.loads(raw_text)
        return _parse_result(parsed)
    except Exception as e:
        print(f"⚠️ دسته‌بندی با Gemini شکست خورد: {e} - به روش کلیدواژه‌ای برمی‌گردیم.")
        return None


def classify_batch_with_gemini(api_key: str, items: list):
    """
    items: [{"text": ..., "has_media": bool}, ...] (حداکثر BATCH_SIZE تا)
    خروجی: لیستی هم‌طول با items، هرکدوم یا {"category":..., "is_good":...}
    یا None (اگه پارس یه عضو خاص شکست بخوره یا کل درخواست شکست بخوره).
    یک درخواست برای کل دسته - نه یکی به‌ازای هر پست.
    """
    if not api_key or not items:
        return [None] * len(items)

    lines = []
    for i, it in enumerate(items, start=1):
        media_desc = "عکس یا فیلم دارد" if it["has_media"] else "عکس یا فیلم ندارد"
        lines.append(f'{i}. [{media_desc}]\n"""{it["text"]}"""')
    items_block = "\n\n".join(lines)

    prompt = BATCH_PROMPT_TEMPLATE.format(n=len(items), items_block=items_block)

    try:
        data = call_gemini_raw(api_key, prompt, timeout=60)
        raw_text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
        if raw_text.startswith("```"):
            raw_text = raw_text.strip("`")
            raw_text = raw_text.replace("json", "", 1).strip()

        parsed_list = json.loads(raw_text)
        if not isinstance(parsed_list, list):
            raise ValueError("خروجی مدل آرایه نبود")

        results = []
        for i in range(len(items)):
            if i < len(parsed_list) and isinstance(parsed_list[i], dict):
                results.append(_parse_result(parsed_list[i]))
            else:
                results.append(None)
        return results
    except Exception as e:
        print(f"⚠️ دسته‌بندیِ دسته‌ای با Gemini شکست خورد: {e} - همه‌ی این دسته به روش کلیدواژه‌ای می‌رن.")
        return [None] * len(items)
