"""
Общая логика цикла сбора заказов.

Используется и консольным main.py, и Telegram-ботом (bot.py),
чтобы не дублировать код. run_cycle() возвращает список новых заказов
и хранилище, а весь вывод идёт через колбэк log (по умолчанию print).
"""
import datetime
import html as htmllib
import importlib
import json
import re
import time

import filters
import storage

_TAG_RE = re.compile(r'<[^>]+>')
_WS_RE = re.compile(r'\s+')

def clean_text(s):
    """Убирает HTML-теги (<b>...</b> и пр.) и лишние пробелы из текста."""
    if not s:
        return ''
    s = _TAG_RE.sub(' ', str(s))
    s = htmllib.unescape(s)
    return _WS_RE.sub(' ', s).strip()

def load_config(path='config.json'):
    import os
    if not os.path.exists(path):
        env_path = os.environ.get('CONFIG_PATH') or os.environ.get('PARSER_CONFIG')
        if env_path and os.path.exists(env_path):
            path = env_path
        elif os.environ.get('BOT_TOKEN'):
            base = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                'config.example.json')
            with open(base, encoding='utf-8') as f:
                cfg = json.load(f)
            cfg.setdefault('telegram', {})
            cfg['telegram']['bot_token'] = os.environ['BOT_TOKEN'].strip()
            ids = [int(x) for x in re.findall(r'\d+', os.environ.get('ADMIN_IDS', ''))]
            if ids:
                cfg['telegram']['admin_ids'] = ids
            print('config.json не найден: использую BOT_TOKEN/ADMIN_IDS '
                  'из переменных окружения, остальные настройки — из config.example.json')
            return cfg
        else:
            raise FileNotFoundError(
                f'Файл {path} не найден. Варианты: скопируй config.example.json '
                'в config.json и впиши реальный bot_token; задай переменные '
                'окружения BOT_TOKEN и ADMIN_IDS; либо укажи путь через --config '
                'или переменную окружения CONFIG_PATH.'
            )
    with open(path, encoding='utf-8') as f:
        return json.load(f)

def save_config(cfg, path='config.json'):
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    import os
    os.replace(tmp, path)

SOURCES = {
    'kwork': ('sources.kwork', 'Kwork'),
    'flru': ('sources.flru', 'FL.ru'),
    'freelanceru': ('sources.freelanceru', 'Freelance.ru'),
    'workzilla': ('sources.workzilla', 'Workzilla'),
    'profiru': ('sources.profiru', 'Profi.ru'),
}

def run_cycle(cfg, log=print):
    """Один проход по всем включённым источникам.

    Возвращает (fresh, store): fresh — только новые заказы,
    store — OrderStore со всей базой после дописывания.
    """
    kf = filters.KeywordFilter(
        cfg['keywords'],
        cfg.get('exclude_keywords'),
        cfg.get('min_matches', 1),
    )
    store = storage.OrderStore(cfg['output']['json'], cfg['output']['csv'])
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    log(f'=== Цикл {now} ===')
    total_new = []
    stats = []
    for key, (module, label) in SOURCES.items():
        scfg = (cfg.get('sources') or {}).get(key) or {}
        if not scfg.get('enabled', False):
            log(f'-- {label}: выключен в конфиге')
            continue
        log(f'-- {label}: собираю...')
        try:
            mod = importlib.import_module(module)
            orders = mod.fetch(scfg)
        except Exception as e:
            log(f'  [{label}] непредвиденная ошибка: {e!r}')
            continue
        matched = []
        for o in orders:
            for field in ('title', 'description', 'category'):
                o[field] = clean_text(o.get(field))
            ok, n, hits = kf.match(o.get('title', ''), o.get('description', ''))
            if ok:
                o['found_at'] = now
                matched.append(o)
        log(f'   получено {len(orders)}, подошло по ключевым словам: {len(matched)}')
        stats.append(f'• {label}: новых {len(matched)} из {len(orders)}')
        total_new.extend(matched)
        time.sleep(float(cfg.get('pause_between_sources', 3)))
    fresh = store.add(total_new)
    log(f'Новых заказов: {len(fresh)} (всего в базе: {len(store.orders)})')
    for o in fresh:
        price = o.get('price') or 'цена не указана'
        log(f"  + [{o['source']}] {o['title']} | {price} | {o['url']}")
    return fresh, store, stats
