# -*- coding: utf-8 -*-
"""Profi.ru — заявки.

Особенность: profi.ru жёстко блокирует не-браузерные клиенты (TLS-отпечаток),
а список заявок исполнителям доступен только после входа и оплаты каталога.
Поэтому адаптер работает в двух режимах:
  * без cookies — попытка скачать https://profi.ru/orders и честное сообщение,
    если площадка блокирует;
  * с cookies из браузера (config -> sources.profi.cookies) — парсинг ссылок
    на заявки (best effort).

По умолчанию источник выключен (enabled: false).
"""
import re
import html as htmllib

from .base import HttpClient, HttpError


def fetch(config):
    cookies = (config.get('cookies') or '').strip()
    client = HttpClient(retries=0, raw_cookie=cookies, timeout=20)
    try:
        page = client.text('https://profi.ru/orders',
                           headers={'Referer': 'https://profi.ru/'})
    except HttpError as e:
        print('  [profi.ru] недоступен без браузера (антибот). '
              'Смотрите README: как включить через cookies или расширение.')
        return []
    orders = []
    seen = set()
    for m in re.finditer(r'<a[^>]+href="(/orders/\d+[^"]*)"[^>]*>(.*?)</a>', page, re.S):
        href, inner = m.group(1), m.group(2)
        title = re.sub(r'\s+', ' ', htmllib.unescape(re.sub(r'<[^>]+>', ' ', inner))).strip()
        if not title or href in seen:
            continue
        seen.add(href)
        orders.append({
            'source': 'profi.ru',
            'title': title[:300],
            'url': 'https://profi.ru' + href,
            'price': None,
            'date': None,
            'category': 'заявка',
            'description': '',
            'responses': None,
        })
    if not orders:
        print('  [profi.ru] заявок не найдено (нужны cookies из браузера)')
    return orders
