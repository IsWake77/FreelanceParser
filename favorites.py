# -*- coding: utf-8 -*-
"""Избранные заказы (favorites.json). Хэш от URL используется в callback-кнопках."""
import json
import hashlib
import datetime

PATH = 'favorites.json'


def key(order):
    """Короткий стабильный идентификатор заказа (для callback_data)."""
    return hashlib.md5(str(order.get('url', '')).encode('utf-8')).hexdigest()[:12]


def _load():
    if not __import__('os').path.exists(PATH):
        return []
    try:
        with open(PATH, encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def _save(favs):
    tmp = PATH + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(favs, f, ensure_ascii=False, indent=2)
    import os
    os.replace(tmp, PATH)


def all_orders():
    return _load()


def has(order):
    k = key(order)
    return any(key(f) == k for f in _load())


def add(order):
    """Добавляет заказ. Возвращает True, если добавлен (не был в избранном)."""
    favs = _load()
    k = key(order)
    if any(key(f) == k for f in favs):
        return False
    o = dict(order)
    o['faved_at'] = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    favs.append(o)
    _save(favs)
    return True


def remove_by_key(k):
    """Удаляет по короткому хэшу. Возвращает True, если удалили."""
    favs = _load()
    rest = [f for f in favs if key(f) != k]
    if len(rest) == len(favs):
        return False
    _save(rest)
    return True


def get_by_key(k):
    """Возвращает заказ по хэшу (избранное или None)."""
    for f in _load():
        if key(f) == k:
            return f
    return None
