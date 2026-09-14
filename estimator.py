"""
Оценка времени на выполнение заказа.

Два режима:
1. Эвристический «ИИ-анализатор» (всегда работает, без интернета):
   взвешенные правила по типу задачи + модификаторы сложности.
2. Настоящий ИИ через OpenAI (если в config.json задан openai_api_key):
   gpt-4o-mini оценивает текст заказа. Результаты кэшируются в estimates.json.
"""
import json
import os
import re
import hashlib
import urllib.request

CACHE_PATH = 'estimates.json'
OPENAI_URL = 'https://api.openai.com/v1/chat/completions'
OPENAI_MODEL = 'gpt-4o-mini'

RULES = [
    (r'интернет[- ]?магазин|маркетплейс|\bcrm\b|\berp\b', 70),
    (r'мини[- ]?приложени|mini app|marketplace', 50),
    (r'дашборд|dashboard|админ[- ]?панел|личный кабинет|портал', 35),
    (r'соцсети|парсер\s+телеграм|массов', 30),
    (r'сайт|веб|web|сервис|платформ|приложени|\bapp\b|кабинет', 30),
    (r'бот|bot|telegram|телеграм', 25),
    (r'нейросет|gpt|openai|ии[- ]?агент|ai[- ]?агент|чат[- ]?gpt', 25),
    (r'\bapi\b|интеграц|webhook|автоматизац|интегрировать', 15),
    (r'верстк|лендинг|landing|визитк|тильда|tilda|wordpress|\bwp\b', 12),
    (r'парсер|парсинг|скрап|скрипт|бот-парсер', 10),
]
RE_SMALL = re.compile(r'правк|доработ|поправ|исправ|\bбаг|ошибк|мелк|небольш|'
                      r'космет|донастро|доделать|подправить')
RE_BIG = re.compile(r'с нуля|под ключ|полноценн|комплексн|крупн|продвинут|'
                    r'профессиональн|масштаб|многостранич|с нуля до')

def estimate_hours(order):
    """Эвристика: возвращает (мин_часов, макс_часов)."""
    text = ' '.join([order.get('title') or '', order.get('description') or '']).lower()
    base = 10
    for pat, hours in RULES:
        if re.search(pat, text):
            base = max(base, hours)
    if RE_SMALL.search(text):
        base *= 0.4
    elif RE_BIG.search(text):
        base *= 1.6
    if len(text) > 800:
        base *= 1.25
    lo = max(1, round(base * 0.7))
    hi = max(2, round(base * 1.5))
    return lo, hi

def fmt_hours(lo, hi):
    if hi <= 1:
        return '~1 ч'
    return f'~{lo}–{hi} ч'

def _cache_key(order):
    raw = f"{order.get('url')}|{order.get('title')}"
    return hashlib.md5(raw.encode('utf-8')).hexdigest()[:16]

def _cache_load():
    try:
        with open(CACHE_PATH, encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}

def _cache_save(cache):
    tmp = CACHE_PATH + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(cache, f, ensure_ascii=False, indent=1)
    os.replace(tmp, CACHE_PATH)

def ai_estimate(order, api_key, timeout=15):
    """Запрос к OpenAI. Возвращает строку вида '~12–20 ч' или None при ошибке."""
    prompt = (
        'Ты — опытный фрилансер-разработчик. Оцени, сколько часов работы '
        '(чистой разработки) потребует заказ. Ответь СТРОГО в формате "N–M" '
        '(два целых числа через длинное тире или дефис), без пояснений.\n\n'
        f"Заказ: {order.get('title', '')}\n\n"
        f"Описание: {(order.get('description') or '')[:1500]}"
    )
    body = json.dumps({
        'model': OPENAI_MODEL,
        'messages': [{'role': 'user', 'content': prompt}],
        'temperature': 0.2,
        'max_tokens': 20,
    }).encode('utf-8')
    req = urllib.request.Request(
        OPENAI_URL, data=body, method='POST',
        headers={'Content-Type': 'application/json',
                 'Authorization': f'Bearer {api_key}'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        text = (data.get('choices') or [{}])[0].get('message', {}).get('content', '')
        m = re.search(r'(\d+)\s*[–—-]\s*(\d+)', text)
        if m:
            lo, hi = int(m.group(1)), int(m.group(2))
            if lo > hi:
                lo, hi = hi, lo
            hi = min(hi, 500)
            return fmt_hours(lo, hi)
    except Exception:
        return None
    return None

def estimate(order, cfg=None, use_ai=True):
    """Главная функция: строка оценки с учётом кэша и (опционально) OpenAI."""
    ck = _cache_key(order)
    cache = _cache_load()
    if ck in cache:
        return cache[ck]

    result = None
    key = (cfg or {}).get('openai_api_key') if isinstance(cfg, dict) else None
    if use_ai and key:
        result = ai_estimate(order, key)
    if result is None:
        lo, hi = estimate_hours(order)
        result = fmt_hours(lo, hi)

    cache[ck] = result
    if len(cache) > 3000:
        cache = dict(list(cache.items())[-1500:])
    try:
        _cache_save(cache)
    except OSError:
        pass
    return result
