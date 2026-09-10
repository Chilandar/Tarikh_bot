# -*- coding: utf-8 -*-
"""
ساخت سوال کوییز تاریخی از مقالات واقعیِ ویکی‌پدیای فارسی درباره‌ی تاریخ -
عمدتاً تاریخ ایران (شاهان، دودمان‌ها، جنگ‌ها، ایران باستان)، و بخشی هم
تاریخ جهان. هر بار چند مقاله‌ی تصادفی از رده‌های تاریخیِ واقعیِ ویکی‌پدیا
انتخاب و *از متن واقعیِ همون مقاله‌ها* سوال ساخته می‌شه - نه از تخیل آزاد مدل.

درباره‌ی هوش مصنوعی: به‌جای یک درخواست به‌ازای هر کوییز، چند مقاله با هم در
یک درخواستِ دسته‌ای (batch) به AI فرستاده می‌شن - دقیقاً مثل روشی که برای
دسته‌بندی پست‌ها (ai_classify.py) استفاده می‌شه، ولی کاملاً جدا و مستقل از
اون - این‌جا یه درخواستِ مخصوصِ خودِ کوییزهاست، قاطیِ درخواستِ دسته‌بندیِ
پست‌ها نمی‌شه.

برای جلوگیری از تکرارِ زیادِ یک موضوع، عنوان مقالاتی که قبلاً استفاده شدن
توی state/quiz_used_topics.json نگه‌داری می‌شه (فقط آخرین چند صد تا، تا
فایل بزرگ نشه - بعد از اون، تکرار دوباره اشکالی نداره).
"""

import json
import random
import re
import requests

import config
from telegram_client import load_json, save_json
from ai_providers import ask_ai, strip_json_fence

WIKI_API = "https://fa.wikipedia.org/w/api.php"

# این‌ها رده‌های واقعیِ ویکی‌پدیای فارسی‌ان (رده:...) - تعمداً چندتا و متنوع
# انتخاب شدن تا اگه یکی خالی/کوچیک بود، بقیه جبران کنن.
IRAN_HISTORY_CATEGORIES = [
    "تاریخ ایران", "شاهان ایران", "ایران باستان",
    "تاریخ نظامی ایران", "تاریخ سیاسی ایران",
]
WORLD_HISTORY_CATEGORIES = [
    "تاریخ", "امپراتوری‌ها", "دوره‌های تاریخی", "تاریخ معاصر",
]

# چقدر از کوییزها از تاریخ ایران باشن در مقابل تاریخ جهان (طبق خواسته‌ی شما: بیشتر ایران)
IRAN_WEIGHT = 0.75

USED_TOPICS_FILE = config.STATE_DIR + "/quiz_used_topics.json"

# پنجره‌ی «تنوعِ موضوع»: یه مقاله تا این‌قدر رکورد اخیر، دوباره انتخاب نمی‌شه
# (روزی ۲ کوییز یعنی ~۱۵۰ روز/۵ ماه فاصله)
MAX_RECENT_FOR_EXCLUSION = 300

# پنجره‌ی «جلوگیری از تکرارِ عینِ سوال»: بزرگ‌تر از بالاست، چون حتی بعد از
# اینکه یه مقاله دوباره قابل‌انتخاب شد، هنوز یادمونه قبلاً چه سوالی ازش
# پرسیده بودیم تا سوال جدید زاویه‌ی متفاوتی داشته باشه
MAX_RECORDS_REMEMBERED = 1000

# حداکثر چند مقاله با هم در یک درخواستِ دسته‌ای به AI فرستاده بشن
QUIZ_BATCH_SIZE = 8
# به‌ازای هر کوییزِ واقعاً لازم، این‌قدر مقاله‌ی کاندید جمع می‌کنیم (چون بعضی
# مقاله‌ها skip می‌خورن) - سقفش هم QUIZ_BATCH_SIZE است تا همیشه یک درخواست کافی باشه
CANDIDATE_MULTIPLIER = 3

RULES_TEXT = """۱. سطح سوال متوسط باشه - نه خیلی بدیهی و همگانی، ولی نه پیچیده،
   گمراه‌کننده یا نیازمندِ دانستنِ جزئیاتِ خیلی ریز. یک نفر که علاقه‌مند به
   تاریخه (نه لزوماً متخصص) باید با کمی فکر بتونه جوابش رو پیدا کنه.
۲. سوال فقط حول یک فکتِ مشخص بچرخه (مثلاً یک تاریخ، یک اسم، یک مکان، یک
   نتیجه). خیلی به‌ندرت (نه هر بار) اگه دو فکتِ نزدیک به‌هم طبیعی به‌نظر
   می‌رسید می‌تونی ترکیب کنی، ولی هرگز بیشتر از دو تا - و حتی اون‌موقع هم
   سوال باید کوتاه و یک‌جمله‌ای بمونه، نه چندجزئی و طولانی.
۳. مهم‌ترین قانون: سوال باید مثل یک سوالِ مستقلِ کوییز خونده بشه - انگار
   همین‌طوری از حفظ درباره‌ی تاریخ می‌پرسی، نه از روی یک متنِ خاص. سوال باید
   مستقیماً و بدون هیچ مقدمه‌ای با خودِ پرسش شروع بشه.

   مثالِ غلط (هرگز این‌طور شروع نکن):
     «بر اساس این روایت، پادشاه صفوی که در سال ۱۵۰۱ به قدرت رسید چه کسی بود؟»
     (غلطه چون با «بر اساس این روایت» شروع شده - یعنی به وجودِ یک متن/منبع اشاره کرده)

   مثالِ درست (دقیقاً همین شکل):
     «کدام پادشاه صفوی در سال ۱۵۰۱ به قدرت رسید؟»
     (درسته چون مستقیم و بدون هیچ اشاره‌ای به متن/منبع/روایت، با خودِ پرسش شروع شده)

   عبارت‌هایی مثل «بر اساس متن»، «طبق روایت»، «بر اساس این داستان»، «طبق
   مقاله»، «در این متن آمده که»، «با توجه به منبع» یا هر عبارتِ مشابهی که
   به وجودِ یک متن/منبع/روایت/مقاله/داستانِ مرجع اشاره کنه، از همون کلمه‌ی
   اول ممنوعه.
۴. سوال و همه‌ی گزینه‌ها کوتاه و به فارسیِ روان باشن؛ هر گزینه چند کلمه، نه
   یک جمله.
۵. دقیقاً ۴ گزینه، فقط یکی درست. گزینه‌های غلط معقول و نزدیک به موضوع باشن،
   نه واضح و مسخره غلط."""

QUESTION_PROMPT = """این خلاصه‌ای واقعی از مقاله‌ی ویکی‌پدیا درباره‌ی «{title}» است
(ویکی‌پدیا منبعی است که برای اطلاعات تاریخیِ عمومی به‌طور گسترده مورد ارجاع
قرار می‌گیره):

\"\"\"{extract}\"\"\"

بر اساس *فقط* اطلاعاتی که در همین متن آمده (بدون اضافه‌کردن جزئیاتی که اینجا
نیومده و بدون حدس‌زدن)، یک سوال کوییز تاریخیِ چهارگزینه‌ای درباره‌ی «{title}»
بساز. قوانین:

{rules}

اگه این متن اطلاعات کافی برای یک سوال دقیق و معنادار نداره (مثلاً خیلی کوتاه
یا کلیه)، دقیقاً همین را برگردان: {{"skip": true}}

در غیر این صورت فقط یک JSON خام (بدون ```json و بدون هیچ توضیح اضافه)
دقیقاً با این فرمت برگردون:
{{"question": "متن سوال (حداکثر ۲۰۰ کاراکتر)",
  "options": ["گزینه ۱", "گزینه ۲", "گزینه ۳", "گزینه ۴"],
  "correct_index": 0}}

correct_index اندیس صفرمبنای گزینه‌ی درست در آرایه‌ی options است."""

PRIOR_QUESTIONS_NOTE = """

نکته‌ی مهم: قبلاً یک یا چند سوال درباره‌ی همین مقاله ساخته شده. سوال جدید
باید از نظر فکت/زاویه‌ی اصلی با همه‌ی سوال‌های زیر واقعاً متفاوت باشه (نه
فقط بازنویسیِ همون سوال با کلمات دیگه):
{prior_list}"""

BATCH_HEADER = """شما یک طراح سوال کوییز تاریخی هستید. در ادامه {n} مقاله‌ی جداگانه از
ویکی‌پدیا می‌آد (هرکدوم با یک شماره). برای *هرکدوم به‌طور مستقل* (بدون
ربط‌دادن به بقیه)، طبق این قوانین، یک سوال کوییز چهارگزینه‌ی تاریخی بساز:

{rules}

اگه متنِ یک مورد اطلاعات کافی نداشت، برای همون مورد {{"skip": true}} بذار.

=== ورودی ===
{items_block}

=== خروجی ===
فقط و فقط یک آرایه‌ی JSON خام برگردون (بدون ```json و بدون هیچ توضیح اضافه) -
دقیقاً {n} عضو، دقیقاً به همون ترتیبِ شماره‌های بالا. هر عضو یا
{{"question": "...", "options": ["...","...","...","..."], "correct_index": 0}}
یا {{"skip": true}} است."""


def _truncate(s, limit):
    s = (s or "").strip()
    return s if len(s) <= limit else s[: limit - 1].rstrip() + "…"


# محافظِ نهایی (مستقل از دستورِ پرامپت): اگه سوال با اشاره به وجودِ متن/مقاله/
# روایت/داستان/منبع شروع بشه، رد می‌شه. عمداً anchored به ابتدای سواله (نه
# هرجای سوال) تا سوال‌های معتبری که مثلاً کلمه‌ی «داستان» رو وسطِ خودشون
# دارن (مثلاً «قهرمان کدام داستان...») اشتباهی رد نشن.
LEAK_START_PATTERN = re.compile(
    r"^\s*(بر\s*اساس|طبق|با\s*توجه\s*به|بنا\s*بر|بنابر|به\s*گفته|بر\s*پایه|بر\s*مبنای|"
    r"همان‌?طور\s*که\s*در|در\s*این)\s*"
    r"(این\s*)?"
    r"(متن|روایت|داستان|مقاله|منبع|نوشته)\b"
)


def _leaks_text_reference(question: str) -> bool:
    return bool(LEAK_START_PATTERN.search(question or ""))


def _fetch_category_articles(category: str, limit: int = 50, timeout: int = 20):
    """
    برای یک رده‌ی ویکی‌پدیا، لیستی از {"title", "extract"} صفحات عضوش رو
    برمی‌گردونه - در یک درخواست (generator=categorymembers + prop=extracts).
    این درخواست‌ها به ویکی‌پدیاست، نه به AI - ربطی به سهمیه‌ی AI نداره.
    """
    params = {
        "action": "query",
        "format": "json",
        "generator": "categorymembers",
        "gcmtitle": f"رده:{category}",
        "gcmlimit": limit,
        "gcmtype": "page",
        "prop": "extracts",
        "exintro": 1,
        "explaintext": 1,
        "exchars": 1200,
    }
    try:
        resp = requests.get(WIKI_API, params=params, timeout=timeout,
                             headers={"User-Agent": "TarikhganBot/1.0"})
        resp.raise_for_status()
        pages = resp.json().get("query", {}).get("pages", {})
    except Exception as e:
        print(f"⚠️ گرفتن رده‌ی «{category}» از ویکی‌پدیا شکست خورد: {e}")
        return []

    articles = []
    for page in pages.values():
        title = page.get("title", "")
        extract = (page.get("extract") or "").strip()
        if title and len(extract) > 150:
            articles.append({"title": title, "extract": extract})
    return articles


def _pick_category() -> str:
    pool = IRAN_HISTORY_CATEGORIES if random.random() < IRAN_WEIGHT else WORLD_HISTORY_CATEGORIES
    return random.choice(pool)


def _pick_fresh_article(excluded_titles: set, max_attempts: int = 6):
    for _ in range(max_attempts):
        articles = _fetch_category_articles(_pick_category())
        random.shuffle(articles)
        for article in articles:
            if article["title"] not in excluded_titles:
                return article
    return None


def _finalize_quiz(result: dict):
    """خروجیِ خام مدل رو برای یک آیتم اعتبارسنجی و آماده می‌کنه، یا None برمی‌گردونه."""
    if not isinstance(result, dict) or result.get("skip"):
        return None
    try:
        question = result["question"]
        options = list(result["options"])
        correct_index = int(result["correct_index"])
        assert len(options) == 4 and 0 <= correct_index < 4
    except Exception as e:
        print(f"⚠️ فرمت سوالِ کوییز نامعتبر بود: {e} -> {result}")
        return None

    if _leaks_text_reference(question):
        print(f"⚠️ سوال با اشاره به متن/روایت/منبع شروع شده بود، رد شد: {question}")
        return None

    order = list(range(4))
    random.shuffle(order)
    shuffled_options = [options[i] for i in order]
    shuffled_correct_index = order.index(correct_index)

    return {
        "question": _truncate(question, 200),
        "options": [_truncate(o, 60) for o in shuffled_options],
        "correct_index": shuffled_correct_index,
    }


def build_quizzes_batch(items: list):
    """
    items: [{"title", "extract", "prior_questions": [...]}, ...]
    یک درخواستِ AI برای کلِ دسته - خروجی: لیستی هم‌طول با items، هرکدوم یا
    dict آماده‌ی کوییز یا None.
    """
    if not items:
        return []

    blocks = []
    for i, it in enumerate(items, start=1):
        block = f'{i}. عنوان: «{it["title"]}»\nمتن: """{it["extract"]}"""'
        if it.get("prior_questions"):
            prior_list = "\n".join(f"  - {q}" for q in it["prior_questions"])
            block += f"\nسوال(های) قبلیِ همین موضوع (سوال جدید باید متفاوت باشه):\n{prior_list}"
        blocks.append(block)
    items_block = "\n\n".join(blocks)

    prompt = BATCH_HEADER.format(n=len(items), rules=RULES_TEXT, items_block=items_block)

    raw = ask_ai(prompt, timeout=60)
    if not raw:
        return [None] * len(items)

    try:
        parsed_list = json.loads(strip_json_fence(raw))
        if not isinstance(parsed_list, list):
            raise ValueError("خروجی مدل آرایه نبود")
    except Exception as e:
        print(f"⚠️ پاسخِ دسته‌ایِ کوییز، JSON معتبر نبود: {e} -> {raw}")
        return [None] * len(items)

    results = []
    for i in range(len(items)):
        raw_item = parsed_list[i] if i < len(parsed_list) else None
        results.append(_finalize_quiz(raw_item))
    return results


def build_quiz_from_article(article: dict, prior_questions: list = None):
    """ساخت یک کوییزِ تکی (بدون batch) - برای استفاده‌ی جداگانه در صورت نیاز."""
    prompt = QUESTION_PROMPT.format(title=article["title"], extract=article["extract"], rules=RULES_TEXT)
    if prior_questions:
        prior_list = "\n".join(f"- {q}" for q in prior_questions)
        prompt += PRIOR_QUESTIONS_NOTE.format(prior_list=prior_list)

    raw = ask_ai(prompt, timeout=30)
    if not raw:
        return None
    try:
        result = json.loads(strip_json_fence(raw))
    except Exception as e:
        print(f"⚠️ پاسخ مدل JSON معتبر نبود: {e} -> {raw}")
        return None
    return _finalize_quiz(result)


def build_daily_quizzes(day, count: int = 2):
    """
    `count` کوییز از `count` مقاله‌ی متفاوت (که اخیراً استفاده نشدن) درباره‌ی
    تاریخ (بیشتر ایران، بخشی جهان) می‌سازه - همه‌شون با یک درخواستِ دسته‌ای
    به AI (نه یکی‌یکی)، تا سهمیه‌ی AI زودتر تموم نشه. `day` فقط برای هماهنگی
    با بقیه‌ی کد نگه داشته شده - منبع محتوا دیگه به تاریخ روز وابسته نیست.
    """
    records = load_json(USED_TOPICS_FILE, [])
    recent_titles = {r["title"] for r in records[-MAX_RECENT_FOR_EXCLUSION:]}

    prior_questions_by_title = {}
    for r in records:
        if r.get("question"):
            prior_questions_by_title.setdefault(r["title"], []).append(r["question"])

    excluded = set(recent_titles)
    candidates = []
    target = min(count * CANDIDATE_MULTIPLIER, QUIZ_BATCH_SIZE)
    while len(candidates) < target:
        article = _pick_fresh_article(excluded)
        if not article:
            break
        excluded.add(article["title"])
        candidates.append(article)

    if not candidates:
        return []

    items = [
        {
            "title": a["title"],
            "extract": a["extract"],
            "prior_questions": prior_questions_by_title.get(a["title"], []),
        }
        for a in candidates
    ]
    results = build_quizzes_batch(items)  # یک درخواستِ AI برای کلِ این دسته

    quizzes = []
    new_records = []
    for article, quiz in zip(candidates, results):
        new_records.append({"title": article["title"], "question": quiz["question"] if quiz else None})
        if quiz and len(quizzes) < count:
            quizzes.append(quiz)

    save_json(USED_TOPICS_FILE, (records + new_records)[-MAX_RECORDS_REMEMBERED:])

    return quizzes
