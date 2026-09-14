# -*- coding: utf-8 -*-
"""Kwork — AJAX-лента проектов (POST https://kwork.ru/projects).

Ответ JSON: data.wants[] с полями id, name, priceLimit, date_create,
description, kwork_count. Ссылка на заказ: https://kwork.ru/projects/<id>.

ВАЖНО: Kwork агрессивно ограничивает частоту запросов (403 not_access).
Парсер ходит только на 1-ю страницу каждой категории и ставит паузы.
"""
import time
import datetime
import html as htmllib
import uuid

from .base import HttpClient, HttpError

# Категории Kwork (id можно посмотреть в URL: kwork.ru/projects?c=<id>):
#   11  — Боты и чаты
#   45  — Разработка (общая)
#   85  — Создание сайтов
# Список легко правится в config.json; пустой список = вся лента + ключевые слова.
DEFAULT_CATEGORIES = [11, 45, 85]


def _multipart(fields):
    boundary = '----' + uuid.uuid4().hex
    return boundary, fields


def fetch(config):
    client = HttpClient(retries=1, pause=4.0)
    categories = config.get('categories')
    if categories is None:
        categories = DEFAULT_CATEGORIES
    max_items = int(config.get('max_items', 40))
    requests_pause = float(config.get('pause_seconds', 6))
    # отсекаем старые заказы (в общей ленте kwork встречаются месячной давности)
    max_age_days = float(config.get('max_age_days', 3))
    min_dt = datetime.datetime.now() - datetime.timedelta(days=max_age_days)
    orders = []
    cats = [None] + list(categories)   # None = вся лента без фильтра
    for cat in cats:
        if len(orders) >= max_items:
            break
        try:
            data = client.json(
                'https://kwork.ru/projects',
                method='POST',
                as_multipart=_multipart([('c', str(cat))] if cat else []),
                headers={
                    'X-Requested-With': 'XMLHttpRequest',
                    'Referer': 'https://kwork.ru/projects',
                    'Origin': 'https://kwork.ru',
                    'Accept': 'application/json, text/plain, */*',
                })
        except HttpError as e:
            print(f'  [kwork] категория {cat}: {e} '
                  f'(возможен rate-limit, попробуйте позже)')
            time.sleep(requests_pause)
            continue
        time.sleep(requests_pause)
        wants = (data.get('data') or {}).get('wants') or []
        for w in wants:
            created = None
            try:
                created = datetime.datetime.strptime(w.get('date_create', ''),
                                                     '%Y-%m-%d %H:%M:%S')
            except (ValueError, TypeError):
                pass
            if created and created < min_dt:
                continue
            price = w.get('priceLimit')
            try:
                price_val = float(str(price).replace(' ', '').replace(',', '.'))
                price_str = f'{price_val:g} ₽'
            except (TypeError, ValueError):
                price_str = None
            orders.append({
                'source': 'kwork',
                'title': htmllib.unescape(w.get('name') or '').strip(),
                'url': f"https://kwork.ru/projects/{w.get('id')}",
                'price': price_str,
                'date': w.get('date_create'),
                'category': w.get('subcategory_name') or w.get('category_name') or
                            (f'c={cat}' if cat else 'вся лента'),
                'description': htmllib.unescape(w.get('description') or '')[:1500],
                'responses': w.get('kwork_count'),
            })
    return orders[:max_items]
