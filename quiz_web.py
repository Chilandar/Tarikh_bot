# -*- coding: utf-8 -*-
"""
ساخت سوال کوییز تاریخی از مقالات واقعیِ ویکی‌پدیای فارسی درباره‌ی تاریخ -
عمدتاً تاریخ ایران (شاهان، دودمان‌ها، جنگ‌ها، ایران باستان)، و بخشی هم
تاریخ جهان. هر بار یک مقاله‌ی تصادفی از یکی از رده‌های تاریخیِ واقعیِ
ویکی‌پدیا انتخاب و *از متن واقعیِ همون مقاله* سوال ساخته می‌شه - نه از
تخیل آزاد مدل.

برای جلوگیری از تکرارِ زیادِ یک موضوع، عنوان مقالاتی که قبلاً استفاده شدن
توی state/quiz_used_topics.json نگه‌داری می‌شه (فقط آخرین چند صد تا، تا
فایل بزرگ نشه - بعد از اون، تکرار دوباره اشکالی نداره).
"""

import json
import random
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

PRIOR_QUESTIONS_NOTE = """

نکته‌ی مهم: قبلاً یک یا چند سوال درباره‌ی همین مقاله ساخته شده. سوال جدید
باید از نظر فکت/زاویه‌ی اصلی با همه‌ی سوال‌های زیر واقعاً متفاوت باشه (نه
فقط بازنویسیِ همون سوال با کلمات دیگه):
{prior_list}"""

QUESTION_PROMPT = """این خلاصه‌ای واقعی از مقاله‌ی ویکی‌پدیا درباره‌ی «{title}» است
(ویکی‌پدیا منبعی است که برای اطلاعات تاریخیِ عمومی به‌طور گسترده مورد ارجاع
قرار می‌گیره):

\"\"\"{extract}\"\"\"

بر اساس *فقط* اطلاعاتی که در همین متن آمده (بدون اضافه‌کردن جزئیاتی که اینجا
نیومده و بدون حدس‌زدن)، یک سوال کوییز تاریخیِ چهارگزینه‌ای با سطح دشواریِ
متوسط رو به سخت درباره‌ی «{title}» بساز - سوالی که دانستنش نیاز به دقت و
اطلاعات واقعی داره، نه یک سوال خیلی بدیهی و همگانی. سوال و همه‌ی گزینه‌ها
باید به فارسیِ روان باشن.

اگه این متن اطلاعات کافی برای یک سوال دقیق و معنادار نداره (مثلاً خیلی کوتاه
یا کلیه)، دقیقاً همین را برگردان: {{"skip": true}}

در غیر این صورت فقط یک JSON خام (بدون ```json و بدون هیچ توضیح اضافه)
دقیقاً با این فرمت برگردون:
{{"question": "متن سوال (حداکثر ۲۰۰ کاراکتر)",
  "options": ["گزینه ۱", "گزینه ۲", "گزینه ۳", "گزینه ۴"],
  "correct_index": 0}}

دقیقاً ۴ گزینه، فقط یکی درست. گزینه‌های غلط باید معقول، نزدیک به موضوع، و
واقعاً گمراه‌کننده باشن (نه واضح و مسخره غلط) - چون سطح سوال باید متوسط به
سخت باشه، نه ساده. correct_index اندیس صفرمبنای گزینه‌ی درست در آرایه‌ی
options است."""


def _truncate(s, limit):
    s = (s or "").strip()
    return s if len(s) <= limit else s[: limit - 1].rstrip() + "…"


def _fetch_category_articles(category: str, limit: int = 50, timeout: int = 20):
    """
    برای یک رده‌ی ویکی‌پدیا، لیستی از {"title", "extract"} صفحات عضوش رو
    برمی‌گردونه - در یک درخواست (generator=categorymembers + prop=extracts).
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


def build_quiz_from_article(article: dict, prior_questions: list = None):
    prompt = QUESTION_PROMPT.format(title=article["title"], extract=article["extract"])
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

    if result.get("skip"):
        return None

    try:
        question = result["question"]
        options = list(result["options"])
        correct_index = int(result["correct_index"])
        assert len(options) == 4 and 0 <= correct_index < 4
    except Exception as e:
        print(f"⚠️ فرمت سوالِ کوییز نامعتبر بود: {e} -> {result}")
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
    }


def build_daily_quizzes(day, count: int = 2):
    """
    `count` کوییز از `count` مقاله‌ی متفاوت (که اخیراً استفاده نشدن) درباره‌ی
    تاریخ (بیشتر ایران، بخشی جهان) می‌سازه. `day` فقط برای هماهنگی با بقیه‌ی
    کد نگه داشته شده - منبع محتوا دیگه به تاریخ روز وابسته نیست.

    برای هر مقاله، اگه قبلاً (حتی خارج از پنجره‌ی تنوعِ موضوع) سوالی ازش
    ساخته شده باشه، اون سوال(ها) به مدل داده می‌شه تا سوال جدید تکرار نباشه.
    """
    records = load_json(USED_TOPICS_FILE, [])
    recent_titles = {r["title"] for r in records[-MAX_RECENT_FOR_EXCLUSION:]}

    prior_questions_by_title = {}
    for r in records:
        if r.get("question"):
            prior_questions_by_title.setdefault(r["title"], []).append(r["question"])

    quizzes = []
    new_records = []
    excluded = set(recent_titles)

    attempts = 0
    while len(quizzes) < count and attempts < count * 4:
        attempts += 1
        article = _pick_fresh_article(excluded)
        if not article:
            break
        excluded.add(article["title"])  # همین اجرا دوباره سراغش نریم

        prior = prior_questions_by_title.get(article["title"], [])
        quiz = build_quiz_from_article(article, prior_questions=prior)
        # چه موفق بشه چه نه، ثبت می‌شه - تا برای دفعه‌ی بعد دوباره سراغ همین
        # مقاله‌ی نامناسب نریم (question=None یعنی فقط برای پنجره‌ی تنوع
        # موضوع حساب می‌شه، نه برای یادآوریِ سوال قبلی)
        new_records.append({"title": article["title"], "question": quiz["question"] if quiz else None})
        if quiz:
            quizzes.append(quiz)

    save_json(USED_TOPICS_FILE, (records + new_records)[-MAX_RECORDS_REMEMBERED:])

    return quizzes
