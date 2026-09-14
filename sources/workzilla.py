"""Workzilla — лента заданий https://workzilla.com/quests.

Гостям список заданий недоступен: нужен cookie-файл из браузера.
Как получить (README, раздел Workzilla):
  1) войдите на workzilla.com в браузере;
  2) F12 -> Network -> обновите страницу -> первый запрос к workzilla.com
     -> Headers -> Request -> cookie: скопируйте всю строку;
  3) вставьте её в config.json -> sources.workzilla.cookies.

По умолчанию источник выключен (enabled: false).
"""
import re
import html as htmllib

from .base import HttpClient, HttpError

def fetch(config):
    cookies = (config.get('cookies') or '').strip()
    if not cookies:
        print('  [workzilla] пропущен: не заданы cookies (см. README)')
        return []
    client = HttpClient(retries=1, raw_cookie=cookies)
    max_items = int(config.get('max_items', 30))
    try:
        page = client.text('https://workzilla.com/quests',
                           headers={'Referer': 'https://workzilla.com/'})
    except HttpError as e:
        print(f'  [workzilla] ошибка: {e} (cookies устарели?)')
        return []
    if 'Войти' in page and 'quests' not in page:
        print('  [workzilla] cookies не сработали — заданий не видно')
    orders = []
    seen = set()
    for m in re.finditer(
            r'<a[^>]+href="/quests/(\d+)"[^>]*>(.*?)</a>', page, re.S):
        qid, inner = m.group(1), htmllib.unescape(re.sub(r'<[^>]+>', ' ', m.group(2)))
        title = re.sub(r'\s+', ' ', inner).strip()
        if not title or qid in seen:
            continue
        seen.add(qid)
        orders.append({
            'source': 'workzilla',
            'title': title[:300],
            'url': f'https://workzilla.com/quests/{qid}',
            'price': None,
            'date': None,
            'category': 'задание',
            'description': '',
            'responses': None,
        })
        if len(orders) >= max_items:
            break
    if not orders:
        print('  [workzilla] заданий не найдено — проверьте cookies')
    return orders
