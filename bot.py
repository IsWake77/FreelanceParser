"""
Telegram-бот поверх парсера фриланс-заказов (Kwork, FL.ru, Freelance.ru...).

Настройка (один раз):
    1) Создай бота у @BotFather -> получишь токен вида 123456:ABC-DEF...
    2) Впиши токен в config.json -> "telegram" -> "bot_token"
       (или отправь боту команду: /token <токен>)
    3) python bot.py

Дальше в чате с ботом:
    /start — меню с кнопками: проверка сейчас, автопроверка (пуш новых
    заказов), статистика, настройки (источники, интервал, ключевые слова).
"""
import sys
import os
import time
import html
import asyncio

try:
    if os.name == 'nt':
        os.system('chcp 65001 >nul 2>&1')
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass

from telegram import (Update, InlineKeyboardButton, InlineKeyboardMarkup,
                      LinkPreviewOptions)
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler,
                          MessageHandler, filters, ContextTypes)
from telegram.request import HTTPXRequest

import core
import favorites
import estimator

CONFIG_PATH = os.environ.get('PARSER_CONFIG', 'config.json')
MAX_CARDS = 20
AUTO_TICK = 15

PENDING = {}
AUTO_TASK = None
CYCLE_LOCK = asyncio.Lock()
CARD_CACHE = {}
CARD_CACHE_MAX = 1000

SOURCE_ORDER = list(core.SOURCES.keys())
SOURCE_LABELS = {k: v[1] for k, v in core.SOURCES.items()}
INTERVALS = [5, 10, 15, 30, 60]

def save_config(cfg):
    core.save_config(cfg, CONFIG_PATH)

def ensure_telegram_section(cfg):
    tg = cfg.setdefault('telegram', {})
    tg.setdefault('bot_token', '')
    tg.setdefault('admin_ids', [])
    tg.setdefault('auto_chats', [])
    return tg

def is_admin(cfg, user_id):
    return user_id in (cfg.get('telegram') or {}).get('admin_ids', [])

def main_kb(cfg, chat_id):
    auto = chat_id in (cfg.get('telegram') or {}).get('auto_chats', [])
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('🔍 Проверить сейчас', callback_data='check')],
        [InlineKeyboardButton(
            f"🤖 Автопроверка: {'🟢 вкл' if auto else '🔴 выкл'}",
            callback_data='auto')],
        [InlineKeyboardButton('⭐ Избранное', callback_data='favs'),
         InlineKeyboardButton('📋 Последние', callback_data='latest')],
        [InlineKeyboardButton('📊 Статистика', callback_data='stats')],
        [InlineKeyboardButton('🗂 Все заказы', callback_data='all'),
         InlineKeyboardButton('♻️ Актуальность', callback_data='act')],
        [InlineKeyboardButton('⚙️ Настройки', callback_data='settings')],
    ])

def settings_kb(cfg):
    rows = []
    for key in SOURCE_ORDER:
        en = ((cfg.get('sources') or {}).get(key) or {}).get('enabled', False)
        rows.append([InlineKeyboardButton(
            f"{SOURCE_LABELS[key]}: {'🟢 вкл' if en else '🔴 выкл'}",
            callback_data=f'src:{key}')])
    rows.append([InlineKeyboardButton(
        f"⏱ Интервал: {cfg.get('interval_minutes', 10)} мин",
        callback_data='interval')])
    rows.append([InlineKeyboardButton('🔑 Ключевые слова', callback_data='kw')])
    rows.append([InlineKeyboardButton('↩️ Назад', callback_data='menu')])
    return InlineKeyboardMarkup(rows)

def interval_kb(cfg):
    cur = cfg.get('interval_minutes', 10)
    buttons = []
    for v in INTERVALS:
        buttons.append(InlineKeyboardButton(
            f"{'✅ ' if v == cur else ''}{v}", callback_data=f'iv:{v}'))
    return InlineKeyboardMarkup([buttons, [InlineKeyboardButton('↩️ Назад', callback_data='settings')]])

def kw_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('➕ Добавить', callback_data='kw:add'),
         InlineKeyboardButton('🗑 Удалить', callback_data='kw:del')],
        [InlineKeyboardButton('↩️ Настройки', callback_data='settings')],
    ])

def main_text():
    return ('👋 Бот-парсер фриланс-заказов\n\n'
            '🔍 <b>Проверить сейчас</b> — собрать заказы прямо сейчас\n'
            '🤖 <b>Автопроверка</b> — присылать новые заказы автоматически\n'
            '⭐ <b>Избранное</b> — заказы, отмеченные звёздочкой\n'
            '📋 <b>Последние заказы</b> — свежие из базы, листай страницами\n'
            '🗂 <b>Все заказы</b> — вся база с статусами (✅/⛔️/❔)\n'
            '♻️ <b>Актуальность</b> — проверить, живы ли заказы в базе\n'
            '📊 <b>Статистика</b> — что уже в базе\n'
            '⚙️ <b>Настройки</b> — источники, интервал, ключевые слова\n\n'
            'На каждой карточке заказа: ⭐ — в избранное, 🔗 — открыть, '
            '🏠 — вернуться в меню. ⏱ на карточке — ИИ-оценка, '
            'сколько примерно часов займёт работа.')

def _store(cfg):
    import storage
    return storage.OrderStore(cfg['output']['json'], cfg['output']['csv'])

ALL_PAGE = 12

def all_view(cfg, page=0):
    """Экран «Все заказы»: вся база со статусами, с пагинацией."""
    store = _store(cfg)
    orders = store.orders
    if not orders:
        return ('🗂 <b>База пуста</b>\n\nНажми «🔍 Проверить сейчас».',
                InlineKeyboardMarkup([[InlineKeyboardButton(
                    '🏠 Меню', callback_data='menu')]]))
    total_pages = (len(orders) + ALL_PAGE - 1) // ALL_PAGE
    page = max(0, min(page, total_pages - 1))
    chunk = orders[page * ALL_PAGE:(page + 1) * ALL_PAGE]
    icons = {'actual': '✅', 'closed': '⛔️'}
    lines = [f'🗂 <b>Все заказы — {len(orders)}</b>\n']
    for o in chunk:
        st = icons.get(o.get('status'), '❔')
        title = esc(o.get('title') or '(без названия)')[:60]
        price = esc(o.get('price') or '—')
        dt = fmt_dt(o.get('date') or o.get('found_at'))
        url = o.get('url') or ''
        link = f'<a href="{url}">🔗</a>' if url else ''
        lines.append(f'{st} <b>{title}</b>\n  {price} · {dt} · {link}')
    rows = []
    if total_pages > 1:
        rows.append([
            InlineKeyboardButton('⬅️', callback_data=f'allp:{page - 1}'),
            InlineKeyboardButton(f'{page + 1}/{total_pages}', callback_data='noop'),
            InlineKeyboardButton('➡️', callback_data=f'allp:{page + 1}'),
        ])
    rows.append([InlineKeyboardButton('♻️ Проверить актуальность',
                                      callback_data='act')])
    rows.append([InlineKeyboardButton('🏠 Меню', callback_data='menu')])
    return '\n'.join(lines)[:4000], InlineKeyboardMarkup(rows)

LAST_PAGE = 10

def latest_view(cfg, page=0):
    """Экран «Последние»: свежие заказы из базы, с пагинацией."""
    store = _store(cfg)
    orders = store.orders
    if not orders:
        return ('📋 <b>База пуста</b>\n\nНажми «🔍 Проверить сейчас».',
                InlineKeyboardMarkup([[InlineKeyboardButton(
                    '🏠 Меню', callback_data='menu')]]))
    total_pages = (len(orders) + LAST_PAGE - 1) // LAST_PAGE
    page = max(0, min(page, total_pages - 1))
    chunk = orders[page * LAST_PAGE:(page + 1) * LAST_PAGE]
    lines = [f'📋 <b>Последние заказы — {len(orders)}</b>\n']
    for o in chunk:
        title = esc(o.get('title') or '(без названия)')[:70]
        price = esc(o.get('price') or '—')
        dt = fmt_dt(o.get('date') or o.get('found_at'))
        url = o.get('url') or ''
        link = f'<a href="{url}">🔗 открыть</a>' if url else ''
        lines.append(f'• <b>{title}</b>\n  {price} · {dt} · {link}')
    rows = []
    if total_pages > 1:
        rows.append([
            InlineKeyboardButton('⬅️', callback_data=f'latp:{page - 1}'),
            InlineKeyboardButton(f'{page + 1}/{total_pages}', callback_data='noop'),
            InlineKeyboardButton('➡️', callback_data=f'latp:{page + 1}'),
        ])
    rows.append([InlineKeyboardButton('🗂 Все заказы', callback_data='all'),
                 InlineKeyboardButton('🏠 Меню', callback_data='menu')])
    return '\n'.join(lines)[:4000], InlineKeyboardMarkup(rows)

def act_view(cfg):
    """Экран «Актуальность»: сводка и действия."""
    store = _store(cfg)
    orders = store.orders
    total = len(orders)
    closed = sum(1 for o in orders if o.get('status') == 'closed')
    actual = sum(1 for o in orders if o.get('status') == 'actual')
    unknown = total - closed - actual
    text = (f'♻️ <b>Актуальность заказов</b>\n\n'
            f'Всего в базе: <b>{total}</b>\n'
            f'✅ актуальных: <b>{actual}</b>\n'
            f'⛔️ неактуальных: <b>{closed}</b>\n'
            f'❔ не проверено: <b>{unknown}</b>\n\n'
            'Проверка открывает страницу каждого заказа и смотрит, жива ли она. '
            'За один заход проверяется до 30 самых свежих заказов — '
            'если в базе больше, нажми проверку ещё раз.')
    rows = [
        [InlineKeyboardButton('♻️ Проверить (до 30)', callback_data='act:check')],
        [InlineKeyboardButton(f'🗑 Удалить неактуальные ({closed})',
                              callback_data='act:del')],
        [InlineKeyboardButton('🗂 Все заказы', callback_data='all')],
        [InlineKeyboardButton('🏠 Меню', callback_data='menu')],
    ]
    return text, InlineKeyboardMarkup(rows)

def kw_text(cfg):
    kws = cfg.get('keywords') or []
    exc = cfg.get('exclude_keywords') or []
    return (f"🔑 <b>Ключевые слова</b> ({len(kws)}):\n"
            f"<code>{esc(', '.join(kws)) if kws else '—'}</code>\n\n"
            f"🚫 <b>Исключения</b> ({len(exc)}):\n"
            f"<code>{esc(', '.join(exc)) if exc else '—'}</code>\n\n"
            "➕ Добавить — отправь слова через запятую\n"
            "🗑 Удалить — отправь слово (можно с *, например «бот*»)")

def fmt_dt(s):
    if not s:
        return ''
    return str(s)[:16]

def esc(s):
    return html.escape(str(s or ''), quote=False)

def order_text(o, est=None):
    price = esc(o.get('price') or '—')
    title = esc(o.get('title') or '(без названия)')
    lines = [f"🆕 [{esc(o.get('source'))}] <b>{title}</b>", f"💰 {price}"]
    st = o.get('status')
    if st == 'actual':
        lines.append(f"✅ Актуально (проверено {fmt_dt(o.get('checked_at'))})")
    elif st == 'closed':
        lines.append(f"⛔️ Неактуально (проверено {fmt_dt(o.get('checked_at'))})")
    dt = fmt_dt(o.get('date') or o.get('found_at'))
    if dt:
        lines.append(f"🕘 {dt}")
    desc = (o.get('description') or '').strip()
    if desc:
        if len(desc) > 300:
            desc = desc[:297] + '…'
        lines.append('')
        lines.append(f"📝 {esc(desc)}")
    if est:
        lines.append(f"⏱ Работа: {esc(est)} (ИИ-оценка)")
    return '\n'.join(lines)[:4000]

def card_kb(o):
    """Кнопки карточки заказа: избранное + ссылка + меню."""
    k = favorites.key(o)
    marked = favorites.has(o)
    rows = [[
        InlineKeyboardButton('✅ В избранном' if marked else '⭐ В избранное',
                             callback_data=f'fav:{k}'),
    ]]
    if o.get('url'):
        rows[0].append(InlineKeyboardButton('🔗 Открыть', url=o['url']))
    rows.append([InlineKeyboardButton('🏠 Меню', callback_data='menu')])
    return InlineKeyboardMarkup(rows)

def remember_card(o):
    k = favorites.key(o)
    if len(CARD_CACHE) >= CARD_CACHE_MAX:
        CARD_CACHE.pop(next(iter(CARD_CACHE)))
    CARD_CACHE[k] = o
    return k

def favs_view():
    """Текст и клавиатура списка избранного."""
    favs = favorites.all_orders()
    if not favs:
        return ('⭐ <b>Избранное пусто</b>\n\n'
                'На карточке заказа жми «⭐ В избранное» — и заказ '
                'будет здесь, даже когда уйдёт с биржи.',
                InlineKeyboardMarkup([[InlineKeyboardButton(
                    '🏠 Меню', callback_data='menu')]]))
    lines = [f'⭐ <b>Избранное — {len(favs)}</b>\n']
    rows = []
    for f in favs[:20]:
        k = favorites.key(f)
        title = esc(f.get('title') or '(без названия)')
        price = esc(f.get('price') or '—')
        url = f.get('url') or ''
        link = f'<a href="{url}">🔗 открыть</a>' if url else ''
        est = f.get('estimate')
        est_s = f' · ⏱ {esc(est)}' if est else ''
        lines.append(f'• <b>{title[:60]}</b>\n  {price}{est_s} · {link}')
        rows.append([InlineKeyboardButton(f'❌ {title[:28]}',
                                          callback_data=f'unfav:{k}')])
    rows.append([InlineKeyboardButton('🏠 Меню', callback_data='menu')])
    return '\n'.join(lines)[:4000], InlineKeyboardMarkup(rows)

async def get_estimate(o, cfg):
    """Оценка времени: ИИ (OpenAI), если задан ключ, иначе эвристика."""
    try:
        if (cfg.get('openai_api_key') or '').strip():
            return await asyncio.to_thread(estimator.estimate, o, cfg, True)
        return estimator.estimate(o, cfg, use_ai=False)
    except Exception:
        return None

async def send_orders(app, chat_id, orders):
    """Карточки заказов с кнопками (не больше MAX_CARDS за раз)."""
    cfg = app.bot_data['cfg']
    shown = orders[:MAX_CARDS]
    for o in shown:
        remember_card(o)
        est = await get_estimate(o, cfg)
        try:
            await app.bot.send_message(
                chat_id, order_text(o, est), reply_markup=card_kb(o),
                link_preview_options=LinkPreviewOptions(is_disabled=True))
            await asyncio.sleep(0.4)
        except Exception as e:
            print(f'  [bot] не удалось отправить заказ в {chat_id}: {e!r}')
    if len(orders) > MAX_CARDS:
        await app.bot.send_message(
            chat_id,
            f'…и ещё {len(orders) - MAX_CARDS} новых заказов — '
            f'полный список в orders.json / orders.csv')

async def run_check(app, chat_id, status_msg=None):
    """Один цикл сбора. Возвращает (fresh, store). Сообщения — в чат."""
    cfg = app.bot_data['cfg']
    async with CYCLE_LOCK:
        if status_msg is not None:
            try:
                await status_msg.edit_text('🔎 Проверяю источники, это займёт минуту…')
            except Exception:
                pass
        lines = []
        try:
            fresh, store, stats = await asyncio.to_thread(
                core.run_cycle, cfg, lines.append)
        except Exception as e:
            text = f'⚠️ Ошибка во время проверки: {e!r}'
            if status_msg is not None:
                try:
                    await status_msg.edit_text(text)
                    return [], None
                except Exception:
                    await app.bot.send_message(chat_id, text)
                    return [], None
            raise
        summary = '🔎 <b>Результат проверки</b>\n' + '\n'.join(stats)
        if fresh:
            summary = (f'✅ <b>Новых заказов: {len(fresh)}</b>\n'
                       f'(в базе всего: {len(store.orders)})\n\n' + summary)
        else:
            summary += ('\n\nНовых заказов нет — всё уже было в базе.\n'
                        '«📋 Последние» — посмотреть свежие из базы.')
        if status_msg is not None:
            try:
                await status_msg.edit_text(
                    summary, parse_mode='HTML',
                    reply_markup=main_kb(cfg, chat_id))
            except Exception:
                pass
        if fresh:
            await send_orders(app, chat_id, fresh)
        return fresh, store

async def auto_loop(app):
    next_run = 0.0
    while True:
        try:
            cfg = app.bot_data['cfg']
            chats = list((cfg.get('telegram') or {}).get('auto_chats', []))
            interval = max(1, int(cfg.get('interval_minutes', 10))) * 60
            now = time.time()
            if chats and now >= next_run:
                print(f'[bot] автопроверка для чатов: {chats}')
                async with CYCLE_LOCK:
                    lines = []
                    fresh, store, _stats = await asyncio.to_thread(
                        core.run_cycle, cfg, lines.append)
                if fresh:
                    for ch in chats:
                        try:
                            await app.bot.send_message(
                                ch, f'🔔 <b>Новых заказов: {len(fresh)}</b>',
                                parse_mode='HTML')
                            await send_orders(app, ch, fresh)
                        except Exception as e:
                            print(f'  [bot] push в {ch} не удался: {e!r}')
                next_run = time.time() + interval
        except Exception as e:
            print(f'[bot] ошибка фонового цикла: {e!r}')
            next_run = time.time() + 120
        await asyncio.sleep(AUTO_TICK)

async def post_init(app):
    global AUTO_TASK
    AUTO_TASK = asyncio.create_task(auto_loop(app))

async def post_shutdown(app):
    if AUTO_TASK:
        AUTO_TASK.cancel()

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cfg = context.bot_data['cfg']
    tg = ensure_telegram_section(cfg)
    user = update.effective_user
    chat_id = update.effective_chat.id

    if not is_admin(cfg, user.id):
        if not tg['admin_ids']:
            tg['admin_ids'].append(user.id)
            save_config(cfg)
            await update.message.reply_text(
                f'✅ Привет, {user.first_name}! Ты назначен владельцем бота.')
        else:
            await update.message.reply_text(
                '⛔️ Бот приватный — доступ только для владельца.')
            return

    PENDING.pop(chat_id, None)
    await update.message.reply_text(
        main_text(), parse_mode='HTML',
        reply_markup=main_kb(cfg, chat_id))

async def cmd_token(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cfg = context.bot_data['cfg']
    if not is_admin(cfg, update.effective_user.id):
        await update.message.reply_text('⛔️ Только для владельца.')
        return
    PENDING[update.effective_chat.id] = 'token'
    await update.message.reply_text(
        'Отправь токен сообщением (получил у @BotFather).\n'
        'После сохранения перезапусти бота.')

async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cfg = context.bot_data['cfg']
    tg = ensure_telegram_section(cfg)
    chat_id = update.effective_chat.id
    if chat_id in tg['auto_chats']:
        tg['auto_chats'].remove(chat_id)
        save_config(cfg)
    await update.message.reply_text(
        '⏹ Автопроверка выключена.', reply_markup=main_kb(cfg, chat_id))

async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    cfg = context.bot_data['cfg']
    chat_id = update.effective_chat.id
    if not is_admin(cfg, update.effective_user.id):
        await query.answer('⛔️ Нет доступа', show_alert=True)
        return
    data = query.data or ''
    await query.answer()

    tg = ensure_telegram_section(cfg)

    if data == 'menu':
        await query.edit_message_text(
            main_text(), parse_mode='HTML', reply_markup=main_kb(cfg, chat_id))

    elif data == 'settings':
        await query.edit_message_text(
            '⚙️ <b>Настройки</b>', parse_mode='HTML',
            reply_markup=settings_kb(cfg))

    elif data == 'interval':
        await query.edit_message_text(
            f"⏱ Текущий интервал автопроверки: <b>{cfg.get('interval_minutes', 10)} мин</b>\n"
            'Выбери новый:', parse_mode='HTML', reply_markup=interval_kb(cfg))

    elif data.startswith('iv:'):
        try:
            cfg['interval_minutes'] = max(1, int(data.split(':', 1)[1]))
        except ValueError:
            pass
        save_config(cfg)
        await query.answer(f"Интервал: {cfg['interval_minutes']} мин")
        await query.edit_message_text(
            f"⏱ Интервал автопроверки: <b>{cfg['interval_minutes']} мин</b>\n"
            'Выбери новый:', parse_mode='HTML', reply_markup=interval_kb(cfg))

    elif data.startswith('src:'):
        key = data.split(':', 1)[1]
        scfg = (cfg.get('sources') or {}).setdefault(key, {})
        scfg['enabled'] = not scfg.get('enabled', False)
        save_config(cfg)
        state = '🟢 включён' if scfg['enabled'] else '🔴 выключен'
        await query.answer(f'{SOURCE_LABELS.get(key, key)}: {state}')
        await query.edit_message_text(
            '⚙️ <b>Настройки</b>', parse_mode='HTML',
            reply_markup=settings_kb(cfg))

    elif data == 'auto':
        chats = tg.setdefault('auto_chats', [])
        if chat_id in chats:
            chats.remove(chat_id)
            save_config(cfg)
            await query.answer('Автопроверка выключена')
        else:
            chats.append(chat_id)
            save_config(cfg)
            interval = cfg.get('interval_minutes', 10)
            await query.answer(f'Включено! Первая проверка ≤ {interval} мин',
                               show_alert=True)
        await query.edit_message_text(
            main_text(), parse_mode='HTML', reply_markup=main_kb(cfg, chat_id))

    elif data == 'check':
        if CYCLE_LOCK.locked():
            await query.answer('⏳ Проверка уже идёт, подожди немного',
                               show_alert=True)
            return
        await run_check(app=context.application, chat_id=chat_id,
                        status_msg=query.message)

    elif data == 'menu':
        auto = bool(cfg.get('auto_check'))
        await query.edit_message_text(
            f'Главное меню\n\n'
            f'🤖 Автопроверка: {"включена" if auto else "выключена"}',
            reply_markup=main_kb(cfg, chat_id))

    elif data == 'favs':
        text, kb = favs_view()
        await query.edit_message_text(
            text, parse_mode='HTML', reply_markup=kb,
            link_preview_options=LinkPreviewOptions(is_disabled=True))

    elif data.startswith('unfav:'):
        k = data.split(':', 1)[1]
        ok = favorites.remove_by_key(k)
        await query.answer('➖ Удалено из избранного' if ok else 'Не найдено')
        text, kb = favs_view()
        await query.edit_message_text(
            text, parse_mode='HTML', reply_markup=kb,
            link_preview_options=LinkPreviewOptions(is_disabled=True))

    elif data.startswith('fav:'):
        k = data.split(':', 1)[1]
        if favorites.get_by_key(k):
            favorites.remove_by_key(k)
            await query.answer('➖ Убрано из избранного')
        else:
            o = CARD_CACHE.get(k)
            if o is None:
                import storage
                store = storage.OrderStore(cfg['output']['json'],
                                           cfg['output']['csv'])
                o = next((x for x in store.orders
                          if favorites.key(x) == k), None)
            if o:
                est = await get_estimate(o, cfg)
                if est:
                    o['estimate'] = est
                favorites.add(o)
                remember_card(o)
                await query.answer(f'⭐ В избранное! {est or ""}',
                                   show_alert=True)
            else:
                await query.answer('Заказ не найден — обнови проверку',
                                   show_alert=True)

    elif data == 'latest':
        text, kb = latest_view(cfg, page=0)
        await query.edit_message_text(
            text, parse_mode='HTML', reply_markup=kb,
            link_preview_options=LinkPreviewOptions(is_disabled=True))

    elif data.startswith('latp:'):
        try:
            page = int(data.split(':', 1)[1])
        except ValueError:
            page = 0
        text, kb = latest_view(cfg, page=page)
        await query.edit_message_text(
            text, parse_mode='HTML', reply_markup=kb,
            link_preview_options=LinkPreviewOptions(is_disabled=True))

    elif data == 'stats':
        import storage
        store = storage.OrderStore(cfg['output']['json'], cfg['output']['csv'])
        orders = store.orders
        if not orders:
            await query.answer('База пуста — нажми «Проверить сейчас»',
                               show_alert=True)
            return
        by_src = {}
        for o in orders:
            by_src[o.get('source', '?')] = by_src.get(o.get('source', '?'), 0) + 1
        last = orders[0]
        lines = ['📊 <b>Статистика</b>', f'Всего в базе: <b>{len(orders)}</b>']
        lines += [f'• {k}: {v}' for k, v in sorted(by_src.items(),
                                                   key=lambda x: -x[1])]
        lines.append(f"\nПоследний заказ: {fmt_dt(last.get('found_at'))}")
        lines.append(f"«{esc((last.get('title') or '')[:60])}»")
        await query.edit_message_text('\n'.join(lines), parse_mode='HTML',
                                      reply_markup=main_kb(cfg, chat_id))

    elif data == 'all':
        text, kb = all_view(cfg, page=0)
        await query.edit_message_text(
            text, parse_mode='HTML', reply_markup=kb,
            link_preview_options=LinkPreviewOptions(is_disabled=True))

    elif data.startswith('allp:'):
        try:
            page = int(data.split(':', 1)[1])
        except ValueError:
            page = 0
        text, kb = all_view(cfg, page=page)
        await query.edit_message_text(
            text, parse_mode='HTML', reply_markup=kb,
            link_preview_options=LinkPreviewOptions(is_disabled=True))

    elif data == 'act':
        text, kb = act_view(cfg)
        await query.edit_message_text(text, parse_mode='HTML', reply_markup=kb)

    elif data == 'act:check':
        if CYCLE_LOCK.locked():
            await query.answer('⏳ Проверка уже идёт, подожди немного',
                               show_alert=True)
            return
        await query.edit_message_text('♻️ Проверяю актуальность заказов…\n'
                                      'Это может занять пару минут.')
        async with CYCLE_LOCK:
            lines = []
            try:
                counts, _store = await asyncio.to_thread(
                    core.check_actuality, cfg, lines.append, False, 30)
            except Exception as e:
                await query.edit_message_text(f'⚠️ Ошибка проверки: {e!r}')
                return
        text = (f'♻️ <b>Проверка завершена</b>\n\n'
                f'✅ актуальных: <b>{counts["actual"]}</b>\n'
                f'⛔️ неактуальных: <b>{counts["closed"]}</b>\n'
                f'❔ не удалось проверить: <b>{counts["unknown"]}</b>\n\n'
                'Статусы обновлены в базе. Неактуальные можно удалить '
                'кнопкой ниже.')
        rows = [
            [InlineKeyboardButton('🗑 Удалить неактуальные',
                                  callback_data='act:del')],
            [InlineKeyboardButton('🗂 Все заказы', callback_data='all')],
            [InlineKeyboardButton('🏠 Меню', callback_data='menu')],
        ]
        await query.edit_message_text(
            text, parse_mode='HTML', reply_markup=InlineKeyboardMarkup(rows))

    elif data == 'act:del':
        store = _store(cfg)
        keys = [store._key(o) for o in store.orders
                if o.get('status') == 'closed']
        if not keys:
            await query.answer('⛔️ Неактуальных заказов в базе нет',
                               show_alert=True)
            return
        removed = await asyncio.to_thread(store.remove, keys)
        text, kb = act_view(cfg)
        await query.answer(f'🗑 Удалено: {len(removed)}')
        await query.edit_message_text(text, parse_mode='HTML', reply_markup=kb)

    elif data == 'noop':
        await query.answer()

    elif data == 'kw':
        PENDING.pop(chat_id, None)
        await query.edit_message_text(kw_text(cfg), parse_mode='HTML',
                                      reply_markup=kw_kb())

    elif data == 'kw:add':
        PENDING[chat_id] = 'kw_add'
        await query.message.reply_text(
            'Отправь ключевые слова через запятую.\n'
            'Можно со звёздочкой на конце: «бот*» найдёт «бота», «боты»…\n'
            '⚠️ Осторожно: короткие слова типа «сайт» дадут много мусора.')

    elif data == 'kw:del':
        PENDING[chat_id] = 'kw_del'
        await query.message.reply_text(
            'Отправь слово (или несколько через запятую), которое убрать '
            'из ключевых слов.\nНапример: «бот*» уберёт и «бот*», и «бот».')

async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cfg = context.bot_data['cfg']
    chat_id = update.effective_chat.id
    if not is_admin(cfg, update.effective_user.id):
        return
    mode = PENDING.pop(chat_id, None)
    if not mode:
        await update.message.reply_text(
            'Открываю меню 🤖',
            reply_markup=main_kb(context.application.bot_data.get('cfg', {}),
                                 chat_id))
        return
    text = (update.message.text or '').strip()

    if mode == 'token':
        tg = ensure_telegram_section(cfg)
        tg['bot_token'] = text
        save_config(cfg)
        await update.message.reply_text(
            '✅ Токен сохранён. Перезапусти бота (python bot.py).')

    elif mode == 'kw_add':
        added = []
        for word in [w.strip() for w in text.replace('\n', ',').split(',')]:
            if not word:
                continue
            lw = word.lower()
            if lw not in [k.lower() for k in cfg['keywords']]:
                cfg['keywords'].append(word)
                added.append(word)
        save_config(cfg)
        msg = (f'✅ Добавлено: <b>{", ".join(added)}</b>' if added
               else 'Такие слова уже есть в списке.')
        await update.message.reply_text(msg, parse_mode='HTML')
        await update.message.reply_text(kw_text(cfg), parse_mode='HTML',
                                        reply_markup=kw_kb())

    elif mode == 'kw_del':
        removed = []
        for word in [w.strip() for w in text.replace('\n', ',').split(',')]:
            if not word:
                continue
            lw = word.lower()
            if lw.endswith('*'):
                stem = lw[:-1]
                removed += [k for k in cfg['keywords'] if k.lower().startswith(stem)]
                cfg['keywords'] = [k for k in cfg['keywords']
                                   if not k.lower().startswith(stem)]
            else:
                removed += [k for k in cfg['keywords'] if k.lower() == lw]
                cfg['keywords'] = [k for k in cfg['keywords'] if k.lower() != lw]
        save_config(cfg)
        msg = (f'🗑 Удалено: <b>{", ".join(removed) or "—"}</b>' if removed
               else 'Таких слов в списке нет.')
        await update.message.reply_text(msg, parse_mode='HTML')
        await update.message.reply_text(kw_text(cfg), parse_mode='HTML',
                                        reply_markup=kw_kb())

def _make_request(proxy=''):
    """HTTPXRequest с увеличенным пулом; при заданном proxy — через прокси."""
    try:
        return HTTPXRequest(proxy=proxy or None, connection_pool_size=8)
    except TypeError:  # старые версии PTB (<20.7) использовали proxy_url
        return HTTPXRequest(proxy_url=proxy or None, connection_pool_size=8)

async def on_error(update, context):
    """Не даём разовым сетевым сбоям ронять бота — просто заметка в консоль."""
    err = context.error
    print(f'⚠️ {type(err).__name__}: {str(err).splitlines()[0][:160]}')

def main():
    cfg = core.load_config(CONFIG_PATH)
    ensure_telegram_section(cfg)
    token = cfg['telegram']['bot_token'] or os.environ.get('BOT_TOKEN', '')
    if not token:
        print('❌ Не задан токен бота!')
        print('   1) Создай бота у @BotFather — получишь токен')
        print('   2) Впиши его в config.json -> "telegram" -> "bot_token"')
        print('   3) Запусти: python bot.py')
        return

    proxy = (cfg['telegram'].get('proxy')
             or os.environ.get('TG_PROXY') or '').strip()

    app = (Application.builder()
           .token(token)
           .request(_make_request(proxy))
           .get_updates_request(_make_request(proxy))
           .post_init(post_init)
           .post_shutdown(post_shutdown)
           .build())
    app.bot_data['cfg'] = cfg

    app.add_handler(CommandHandler('start', cmd_start))
    app.add_handler(CommandHandler('help', cmd_start))
    app.add_handler(CommandHandler('token', cmd_token))
    app.add_handler(CommandHandler('stop', cmd_stop))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_error_handler(on_error)

    owners = cfg['telegram']['admin_ids'] or 'назначится по /start'
    print(f'🤖 Бот запущен. Владелец(ы): {owners}')
    print(f'   код: {os.path.abspath(__file__)}')
    if proxy:
        print(f'   прокси Telegram API: {proxy}')
    print('   меню: ' + ' / '.join(
        b.text for row in main_kb(cfg, 1).inline_keyboard for b in row))
    print('Остановить: Ctrl+C')
    app.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    main()
