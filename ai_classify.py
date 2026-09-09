# -*- coding: utf-8 -*-
"""
دسته‌بندی و ارزیابی کیفیت پست‌ها با هوش مصنوعی (Gemini/Grok - رایگان). اگه
هیچ کلیدی تنظیم نباشه یا همه‌ی مدل‌ها شکست بخورن، None برمی‌گرده و کد اصلی
به روش کلیدواژه‌ای (ضعیف‌تر) برمی‌گرده.

تماسِ واقعی با مدل‌ها (و fallback بین چند کلید/سرویس) داخل ai_providers.py
انجام می‌شه - این فایل فقط پرامپت‌ها و پردازش خروجی رو نگه می‌داره.

برای جلوگیری از محدودیت نرخ، به‌جای یک درخواست به‌ازای هر پست، چند پست با
هم در یک درخواست فرستاده می‌شن (classify_batch_with_ai). تابع تکی
(classify_with_ai) هنوز هست و برای جاهای دیگه (مثل quiz_extract) کار
می‌کنه، ولی scan_history.py از نسخه‌ی دسته‌ای استفاده می‌کنه.
"""

import json

from ai_providers import ask_ai, strip_json_fence

VALID_CATEGORIES = {"sokhan_bozorgan", "aks_iran_qadim", "hekayat_dastan_sher", "general"}

BATCH_SIZE = 15  # چند پست در هر درخواست فرستاده بشه


CATEGORY_RULES = """تو دستیار دسته‌بندی و پاک‌سازیِ محتوا برای یک کانال تلگرامی تاریخی/فرهنگی
به اسم «تاریخگان» هستی. این کانال پست‌هایی درباره‌ی تاریخ، فرهنگ، ادبیات و
تصاویر قدیمی منتشر می‌کنه. باید هر متنی که میدم رو بر اساس تعریف‌های دقیقی
که میدم بررسی کنی.

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
تاریخی/فرهنگیه، true بذار.

=== وظیفه‌ی دوم: تحلیلِ هشتگ‌ها، منبع، و پیشنهادِ هشتگ (خیلی مهم، دقیق باش) ===

توجه: متنی که بهت می‌دم از قبل استیکر/ایموجی‌هاش حذف شده (این کار قبلاً
مکانیکی انجام شده، لازم نیست بهش فکر کنی). فقط باید این تصمیم‌ها رو بگیری:

الف) هشتگ‌ها (hashtag_actions):
هر هشتگی (کلمه‌ای که با # شروع می‌شه، هرجای متن - چه ابتدا چه انتها) که
توی متن می‌بینی رو بررسی کن و دقیقاً یکی از این دو کار رو براش مشخص کن:
  - "remove": اگه این هشتگ صرفاً یک برچسبِ تزئینی/دسته‌بندیه (مثل نام یک
    ژانر، یک شعار، یا آیدیِ کانال) و هیچ اطلاعات معناداری اضافه نمی‌کنه.
  - "convert_to_name": اگه این هشتگ در واقع اسمِ یک شخص واقعیه (مثلاً
    نویسنده یا شاعر یا کسی که این جمله/سخن از اونه) که بجای نوشتن ساده‌ی
    اسمش، هشتگ شده - در این حالت باید فیلد "name" رو هم بدی: همون اسم،
    بدون # و بدون آندرلاین (آندرلاین‌ها رو با فاصله عوض کن). مثلاً
    #فرانتس_کافکا باید بشه name="فرانتس کافکا".
دقیقاً همون رشته‌ی هشتگ (با # و همون شکل که توی متنِ ورودی هست) رو توی
فیلد "tag" بذار تا بتونیم پیداش کنیم. هرگز به خودِ متنِ اصلی/شعر/حکایت/
نقل‌قول دست نزن - فقط درباره‌ی هشتگ‌ها تصمیم بگیر. اگه هیچ هشتگی نبود،
لیست خالی بده.

ب) منبع (source_text و source_line_raw):
گاهی در متن (معمولاً نزدیک انتها) جمله‌ای هست که می‌گه این محتوا از کجا
گرفته شده - مثلاً با کلماتی مثل «منبع:»، «برگرفته از:»، «به نقل از:» یا
مشابه. اگه همچین چیزی دیدی:
  - source_text: فقط خودِ توضیحِ منبع (بدون کلمه‌ی «منبع»، بدون هیچ نماد).
  - source_line_raw: دقیقاً و عیناً (کلمه‌به‌کلمه، بدون هیچ تغییری) همون
    خط یا خط‌هایی از متنِ ورودی که این منبع توشون نوشته شده - این رو
    می‌خوایم تا خطِ قدیمی رو حذف کنیم و با فرمتِ استاندارد جایگزینش کنیم.
اگه پست منبعی نداشت، هر دو رو خالی ("") بذار - هرگز منبع الکی نساز.
توجه مهم: اگه پست از نوع «سخن بزرگان» است و صرفاً اسمِ گوینده در انتهاش
اومده (چه هشتگ چه ساده)، اون اسمِ گوینده «منبع» حساب نمی‌شه - بخشی از
خودِ متنه (با قانون «الف» در موردش تصمیم گرفتی). source_text فقط برای
جایی‌ه که واقعاً از منبعِ محتوا (کتاب/سایت/کانال دیگه) حرف زده شده.

پ) هشتگ‌های پیشنهادی (suggested_hashtags):
حداکثر ۵ هشتگ فارسیِ مرتبط با موضوع همین پست بساز (با آندرلاین به‌جای
فاصله، مثل #فرانتس_کافکا یا #سخن_بزرگان). باید واقعاً به محتوای همین پست
مرتبط باشن، نه عمومی و بی‌ربط."""


PROMPT_TEMPLATE = CATEGORY_RULES + """

=== ورودی ===
این پست {media_desc}.

متن پست:
\"\"\"{text}\"\"\"

=== خروجی ===
فقط و فقط یک JSON خام (بدون ```json و بدون هیچ توضیح اضافه‌ای) دقیقاً با
این فرمت برگردون:
{{"category": "یکی از چهار مقدار: sokhan_bozorgan / aks_iran_qadim / hekayat_dastan_sher / general",
  "is_good": true یا false,
  "hashtag_actions": [{{"tag": "#...", "action": "remove"}}, {{"tag": "#...", "action": "convert_to_name", "name": "..."}}],
  "source_text": "",
  "source_line_raw": "",
  "suggested_hashtags": ["#...", "#..."]}}"""


BATCH_PROMPT_TEMPLATE = CATEGORY_RULES + """

=== ورودی ===
در ادامه {n} پست جداگانه میاد، هرکدوم با یک شماره. برای *هرکدوم به‌طور
مستقل* (بدون ربط‌دادن به بقیه) تمام تصمیم‌های بالا رو طبق همون قوانین
تعیین کن.

{items_block}

=== خروجی ===
فقط و فقط یک آرایه‌ی JSON خام برگردون (بدون ```json و بدون هیچ توضیح
اضافه‌ای) - دقیقاً {n} عضو، دقیقاً به همون ترتیب شماره‌های بالا. هر عضو باید
دقیقاً این شکل باشه:
[{{"category": "...", "is_good": true,
   "hashtag_actions": [{{"tag": "#...", "action": "remove"}}],
   "source_text": "", "source_line_raw": "",
   "suggested_hashtags": ["#...", "#..."]}}, ...]"""


MAX_HASHTAG_ACTIONS = 12
MAX_TAG_LEN = 60
MAX_NAME_LEN = 80
MAX_SOURCE_TEXT_LEN = 300
MAX_SOURCE_LINE_RAW_LEN = 400
MAX_SUGGESTED_HASHTAGS = 5
MAX_SUGGESTED_TAG_LEN = 40


def _sanitize_hashtag_actions(raw) -> list:
    if not isinstance(raw, list):
        return []
    cleaned = []
    for act in raw[:MAX_HASHTAG_ACTIONS]:
        if not isinstance(act, dict):
            continue
        tag = str(act.get("tag", "")).strip()
        kind = act.get("action")
        if not tag.startswith("#") or len(tag) > MAX_TAG_LEN:
            continue
        if kind == "remove":
            cleaned.append({"tag": tag, "action": "remove"})
        elif kind == "convert_to_name":
            name = str(act.get("name", "")).strip()
            if name and len(name) <= MAX_NAME_LEN:
                cleaned.append({"tag": tag, "action": "convert_to_name", "name": name})
    return cleaned


def _sanitize_short_text(raw, max_len) -> str:
    if not isinstance(raw, str):
        return ""
    s = raw.strip()
    if len(s) > max_len:
        s = s[:max_len].rstrip()
    return s


def _sanitize_suggested_hashtags(raw) -> list:
    if not isinstance(raw, list):
        return []
    cleaned = []
    seen = set()
    for tag in raw:
        if not isinstance(tag, str):
            continue
        t = tag.strip()
        if not t.startswith("#") or " " in t or len(t) > MAX_SUGGESTED_TAG_LEN:
            continue
        if t in seen:
            continue
        seen.add(t)
        cleaned.append(t)
        if len(cleaned) >= MAX_SUGGESTED_HASHTAGS:
            break
    return cleaned


def _parse_result(parsed) -> dict | None:
    category = parsed.get("category")
    is_good = parsed.get("is_good", True)
    if category not in VALID_CATEGORIES:
        return None
    return {
        "category": category,
        "is_good": bool(is_good),
        "hashtag_actions": _sanitize_hashtag_actions(parsed.get("hashtag_actions")),
        "source_text": _sanitize_short_text(parsed.get("source_text"), MAX_SOURCE_TEXT_LEN),
        "source_line_raw": _sanitize_short_text(parsed.get("source_line_raw"), MAX_SOURCE_LINE_RAW_LEN),
        "suggested_hashtags": _sanitize_suggested_hashtags(parsed.get("suggested_hashtags")),
    }


def classify_with_ai(text: str, has_media: bool):
    """دسته‌بندی + پاک‌سازیِ یک پست تکی - یک درخواست کامل به زنجیره‌ی مدل‌ها."""
    media_desc = "عکس یا فیلم دارد" if has_media else "عکس یا فیلم ندارد"
    prompt = PROMPT_TEMPLATE.format(media_desc=media_desc, text=text)

    raw_text = ask_ai(prompt, timeout=20)
    if raw_text is None:
        return None

    try:
        parsed = json.loads(strip_json_fence(raw_text))
        return _parse_result(parsed)
    except Exception as e:
        print(f"⚠️ دسته‌بندی با هوش مصنوعی شکست خورد: {e} - به روش کلیدواژه‌ای برمی‌گردیم.")
        return None


def classify_batch_with_ai(items: list):
    """
    items: [{"text": ..., "has_media": bool}, ...] (حداکثر BATCH_SIZE تا)
    نکته: "text" باید از قبل با text_utils.mechanical_pre_clean پاک‌سازیِ
    مکانیکی شده باشه (استیکر/امضا حذف شده)، تا هشتگ‌هایی که مدل برمی‌گردونه
    دقیقاً با متنِ نهایی هم‌خوانی داشته باشن.

    خروجی: لیستی هم‌طول با items، هرکدوم یا دیکشنری کامل تصمیم‌ها یا None
    (اگه پارس یه عضو خاص شکست بخوره یا کل درخواست شکست بخوره - در این
    حالت، اون پست فقط با پاک‌سازیِ مکانیکی و بدون منبع/هشتگِ جدید منتشر
    می‌شه، نه اینکه رد بشه یا خراب بشه).
    یک درخواست برای کل دسته - نه یکی به‌ازای هر پست.
    """
    if not items:
        return []

    lines = []
    for i, it in enumerate(items, start=1):
        media_desc = "عکس یا فیلم دارد" if it["has_media"] else "عکس یا فیلم ندارد"
        lines.append(f'{i}. [{media_desc}]\n"""{it["text"]}"""')
    items_block = "\n\n".join(lines)

    prompt = BATCH_PROMPT_TEMPLATE.format(n=len(items), items_block=items_block)

    raw_text = ask_ai(prompt, timeout=90)
    if raw_text is None:
        return [None] * len(items)

    try:
        parsed_list = json.loads(strip_json_fence(raw_text))
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
        print(f"⚠️ دسته‌بندیِ دسته‌ای با هوش مصنوعی شکست خورد: {e} - همه‌ی این دسته به روش کلیدواژه‌ای می‌رن.")
        return [None] * len(items)
