"""Проверка актуальности заказов: жива ли ещё страница заказа на бирже.

Статусы:
    actual  — страница заказа открывается;
    closed  — заказ удалён/закрыт (HTTP 404/410, редирект со страницы
              заказа или характерный заголовок страницы);
    unknown — сеть, капча, требование логина: статус не меняем.
"""
import re

from sources.base import HttpClient, HttpError

_TITLE_RE = re.compile(r'<title[^>]*>(.*?)</title>', re.I | re.S)
_CLOSED_RE = re.compile(
    r'\b(404|не найден\w*|удален\w*|удалён\w*|закрыт|снят\w* с публикации|'
    r'неактивен\w*|истекл\w*|выполнен\w*|деактивирован\w*|deactivated|removed)\b')
_LOGIN_RE = re.compile(r'\b(вход|войти|авторизация|sign in|log in)\b')

def _norm_url(url):
    url = re.sub(r'^https?://', '', (url or '').lower())
    url = re.sub(r'^www\.', '', url)
    return url.split('?')[0].rstrip('/')

def check_order(o, cookies=''):
    """Возвращает (status, note) для одного заказа из базы."""
    url = (o.get('url') or '').strip()
    if not url:
        return 'unknown', 'нет ссылки'
    client = HttpClient(retries=1, pause=1.5, raw_cookie=(cookies or '').strip())
    try:
        code, raw = client.request(url)
    except HttpError as e:
        m = re.search(r'HTTP (\d+)', str(e))
        if m and m.group(1) in ('404', '410'):
            return 'closed', 'страница удалена (HTTP 404)'
        return 'unknown', 'ошибка сети или доступа'
    if code in (404, 410):
        return 'closed', 'страница удалена (HTTP 404)'
    final = getattr(client, 'last_url', None)
    if final and not _norm_url(final).startswith(_norm_url(url)):
        return 'closed', 'страницы заказа больше нет (редирект)'
    page = raw.decode('utf-8', 'ignore')
    m = _TITLE_RE.search(page)
    title = re.sub(r'<[^>]+>', ' ', m.group(1)) if m else ''
    title = re.sub(r'\s+', ' ', title).strip().lower()
    if _LOGIN_RE.search(title):
        return 'unknown', 'страница требует входа (cookies устарели?)'
    if _CLOSED_RE.search(title):
        return 'closed', f'закрыт: «{title[:70]}»'
    return 'actual', 'страница доступна'
