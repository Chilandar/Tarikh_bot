# -*- coding: utf-8 -*-
"""
دسته‌بندی هوشمند پست‌ها با Gemini (رایگان). اگه GEMINI_API_KEY تنظیم نباشه
یا درخواست شکست بخوره، None برمی‌گرده و کد اصلی به روش کلیدواژه‌ای برمی‌گرده.
"""

import json
import requests

GEMINI_MODEL = "gemini-3.5-flash"
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"

VALID_CATEGORIES = {"sokhan_bozorgan", "aks_iran_qadim", "hekayat_dastan_sher", "general"}

PROMPT_TEMPLATE = """متن زیر یک پست تلگرامی تاریخی/فرهنگیه. باید دقیقاً یکی از این ۴ دسته رو براش تعیین کنی:
- sokhan_bozorgan: نقل‌قول یا سخن منسوب به یک شخص معروف (فیلسوف، نویسنده، سیاستمدار، دانشمند، شخصیت تاریخی...)
- aks_iran_qadim: توضیح کوتاه (۱ تا ۳ خط) همراه با یک عکس یا فیلم تاریخی، معمولاً با اشاره به سال و/یا مکان (شهر، کشور، موزه)
- hekayat_dastan_sher: حکایت، داستان کوتاه روایی (حتی بدون اسم نویسنده)، جوک روایی، یا شعر
- general: هر چیز دیگه‌ای که در بالا نگنجه

این پست {media_desc}.

متن پست:
\"\"\"{text}\"\"\"

فقط و فقط یک JSON خام (بدون ```json و بدون هیچ توضیح اضافه‌ای) دقیقاً با این فرمت برگردون:
{{"category": "یکی از چهار مقدار بالا", "is_good": true یا false}}

is_good یعنی: آیا این پست از نظر محتوا برای بازنشر توی یک کانال تاریخی جذاب، معنادار، و باکیفیته؟ (نه تبلیغاتی، نه بی‌محتوا، نه ناقص)"""


def classify_with_gemini(api_key: str, text: str, has_media: bool):
    if not api_key:
        return None

    media_desc = "عکس یا فیلم دارد" if has_media else "عکس یا فیلم ندارد"
    prompt = PROMPT_TEMPLATE.format(media_desc=media_desc, text=text)

    try:
        resp = requests.post(
            GEMINI_URL,
            headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
            json={"contents": [{"parts": [{"text": prompt}]}]},
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        raw_text = data["candidates"][0]["content"]["parts"][0]["text"]
        raw_text = raw_text.strip()
        if raw_text.startswith("```"):
            raw_text = raw_text.strip("`")
            raw_text = raw_text.replace("json", "", 1).strip()

        parsed = json.loads(raw_text)
        category = parsed.get("category")
        is_good = parsed.get("is_good", True)

        if category not in VALID_CATEGORIES:
            return None

        return {"category": category, "is_good": bool(is_good)}
    except Exception as e:
        print(f"⚠️ دسته‌بندی با Gemini شکست خورد: {e} - به روش کلیدواژه‌ای برمی‌گردیم.")
        return None
