# -*- coding: utf-8 -*-
"""
لایه‌ی مشترکِ تماس با مدل‌های هوش مصنوعی (Gemini از گوگل و Grok از xAI).

می‌تونی چند کلید API (حتی از چند اکانت مختلف) برای هر کدوم از این دو
سرویس تنظیم کنی. ترتیب اولویت رو config.AI_PROVIDER_CHAIN مشخص می‌کنه -
پیش‌فرض: اول کلیدهای Gemini به‌ترتیب، بعد کلیدهای Grok به‌ترتیب.

منطق کار: برای هر درخواست، اولین موردِ لیست امتحان می‌شه. اگه شکست بخوره
(خطای شبکه، ۴۲۹/۵xx، تایم‌اوت، یا پاسخِ قابل‌پارس نبود) بلافاصله می‌ریم
سراغ موردِ بعدی - بدون صبر طولانی روی همون کلید، چون کل هدف از داشتن چند
کلید همینه که گیر نکنیم. فقط یک فاصله‌ی کوتاه بین دو تماسِ پیاپی روی
*همون* کلید رعایت می‌شه تا به سقف رایگان نخوریم.

استفاده:
    from ai_providers import ask_ai, strip_json_fence
    raw = ask_ai(prompt)          # None اگه همه‌ی کلیدها/سرویس‌ها شکست بخورن
    raw = strip_json_fence(raw)   # حذف ```json ... ``` اگه مدل دورش گذاشته باشه
"""

import time
import requests

import config

GEMINI_MODEL_CANDIDATES = ["gemini-flash-latest", "gemini-3.5-flash"]
GROK_MODEL_CANDIDATES = ["grok-4.3", "grok-4.5", "grok-4-0709"]

# حداقل فاصله‌ی زمانی (ثانیه) بین دو تماسِ پیاپی با یک کلیدِ یکسان
MIN_SECONDS_BETWEEN_CALLS = {
    "gemini": 5.0,
    "grok": 2.0,
}

_last_call_time = {}  # (provider_type, api_key) -> timestamp


def _wait_if_needed(provider_type: str, api_key: str):
    key_id = (provider_type, api_key)
    min_gap = MIN_SECONDS_BETWEEN_CALLS.get(provider_type, 2.0)
    elapsed = time.time() - _last_call_time.get(key_id, 0.0)
    if elapsed < min_gap:
        time.sleep(min_gap - elapsed)


def _mark_called(provider_type: str, api_key: str):
    _last_call_time[(provider_type, api_key)] = time.time()


def _call_gemini(api_key: str, prompt: str, timeout: int) -> str:
    """
    چند مدلِ Gemini رو به‌ترتیب امتحان می‌کنه (اول نام‌مستعارِ خودکارِ
    "flash-latest" که خودِ گوگل به‌روز نگهش می‌داره). فقط وقتی خطا نشون‌دهنده‌ی
    نامعتبربودن/بازنشسته‌شدنِ خودِ مدله (۴۰۰/۴۰۴) سراغ مدلِ بعدی می‌ریم؛ برای
    ۴۲۹ (سقفِ نرخ) فوراً بالا می‌دیم تا زنجیره بره سراغِ کلیدِ بعدی، نه مدلِ
    بعدیِ همین کلید (چون تعویضِ مدل مشکلِ سقفِ نرخ رو حل نمی‌کنه).
    """
    last_err = None
    for model in GEMINI_MODEL_CANDIDATES:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        resp = requests.post(
            url,
            headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
            json={"contents": [{"parts": [{"text": prompt}]}]},
            timeout=timeout,
        )
        if resp.status_code == 429:
            raise RuntimeError("۴۲۹ - محدودیت نرخ Gemini")
        if resp.status_code in (400, 404):
            last_err = RuntimeError(f"{resp.status_code} با مدل {model} - {resp.text[:200]}")
            continue
        if not resp.ok:
            raise RuntimeError(f"{resp.status_code} - {resp.text[:300]}")
        data = resp.json()
        return data["candidates"][0]["content"]["parts"][0]["text"].strip()
    raise last_err or RuntimeError("هیچ‌کدوم از مدل‌های Gemini جواب ندادن")


def _call_grok(api_key: str, prompt: str, timeout: int) -> str:
    """همون منطقِ بالا، برای مدل‌های Grok (بدون نام‌مستعارِ خودکار، پس چند
    مدلِ واقعیِ فعلی رو دستی پشتِ‌سرِهم امتحان می‌کنیم)."""
    # xAI (Grok) یک API سازگار با فرمت چت OpenAI ارائه می‌ده
    url = "https://api.x.ai/v1/chat/completions"
    last_err = None
    for model in GROK_MODEL_CANDIDATES:
        resp = requests.post(
            url,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=timeout,
        )
        if resp.status_code == 429:
            raise RuntimeError("۴۲۹ - محدودیت نرخ Grok")
        if resp.status_code in (400, 404):
            last_err = RuntimeError(f"{resp.status_code} با مدل {model} - {resp.text[:200]}")
            continue
        if not resp.ok:
            raise RuntimeError(f"{resp.status_code} - {resp.text[:300]}")
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()
    raise last_err or RuntimeError("هیچ‌کدوم از مدل‌های Grok جواب ندادن")


_CALLERS = {
    "gemini": _call_gemini,
    "grok": _call_grok,
}


def ask_ai(prompt: str, timeout: int = 30) -> str | None:
    """
    پرامپت رو به ترتیبِ config.AI_PROVIDER_CHAIN امتحان می‌کنه. به محضِ
    اولین پاسخِ موفق، متنِ خامِ پاسخ (بدون هیچ پردازشی) برگردونده می‌شه.
    اگه همه‌ی مدل‌ها/کلیدها شکست بخورن، None برمی‌گردونه.
    """
    chain = config.AI_PROVIDER_CHAIN
    if not chain:
        print("⚠️ هیچ کلید هوش مصنوعی‌ای (GEMINI_API_KEY / GROK_API_KEY) تنظیم نشده.")
        return None

    for provider_type, api_key in chain:
        caller = _CALLERS.get(provider_type)
        if not caller or not api_key:
            continue
        label = f"{provider_type}(…{api_key[-4:]})"
        try:
            _wait_if_needed(provider_type, api_key)
            text = caller(api_key, prompt, timeout)
            _mark_called(provider_type, api_key)
            return text
        except Exception as e:
            _mark_called(provider_type, api_key)
            print(f"⚠️ {label} شکست خورد: {e} - می‌ریم سراغ گزینه‌ی بعدی.")
            continue

    print("⚠️ همه‌ی مدل‌های هوش مصنوعی (تمام کلیدها) شکست خوردن.")
    return None


def strip_json_fence(raw: str) -> str:
    """اگه مدل پاسخ رو داخل ```json ... ``` گذاشته باشه، پاکش می‌کنه."""
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = raw.replace("json", "", 1).strip()
    return raw
