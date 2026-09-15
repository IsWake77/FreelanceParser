"""
Фриланс-парсер заказов: kwork, fl.ru, freelance.ru, workzilla, profi.ru.

Запуск:
    python main.py            # один проход: собрать заказы и дописать в файлы
    python main.py --loop     # вечный цикл (проверка раз в interval минут)
    python main.py --config my_config.json

Результаты:
    orders.json — все найденные заказы (с дедупликацией между запусками)
    orders.csv  — те же заказы для Excel (дописывается по мере находок)

Telegram-бот с кнопками: python bot.py
"""
import sys
import os
import time
import argparse

try:
    if os.name == 'nt':
        os.system('chcp 65001 >nul 2>&1')
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass

import core

def list_orders(cfg):
    import storage
    store = storage.OrderStore(cfg['output']['json'], cfg['output']['csv'])
    orders = store.orders
    if not orders:
        print('База пуста — запусти сбор заказов.')
        return
    icons = {'actual': '✅', 'closed': '⛔️'}
    print(f'Всего в базе: {len(orders)} (✅ актуально, ⛔️ неактуально, ❔ не проверено)\n')
    for i, o in enumerate(orders, 1):
        st = icons.get(o.get('status'), '❔')
        price = o.get('price') or 'цена не указана'
        dt = o.get('date') or o.get('found_at') or ''
        print(f"{i:4}. {st} [{o.get('source', '?')}] {o.get('title', '')[:70]}")
        print(f"      {price} | {dt[:16]}")
        print(f"      {o.get('url', '')}")
        if o.get('status_note'):
            print(f"      статус: {o.get('status')} — {o['status_note']}")
    by = {'actual': 0, 'closed': 0, 'none': 0}
    for o in orders:
        s = o.get('status')
        by[s if s in ('actual', 'closed') else 'none'] += 1
    print(f"\nИтог: ✅ {by['actual']} · ⛔️ {by['closed']} · ❔ {by['none']}")

def main():
    ap = argparse.ArgumentParser(description='Парсер фриланс-заказов')
    ap.add_argument('--config', default='config.json', help='путь к config.json')
    ap.add_argument('--loop', action='store_true', help='работать в цикле')
    ap.add_argument('--interval', type=int, help='интервал цикла в минутах (переопределяет конфиг)')
    ap.add_argument('--list', action='store_true', help='показать все заказы в базе')
    ap.add_argument('--check', action='store_true', help='проверить актуальность заказов в базе')
    ap.add_argument('--remove-closed', action='store_true',
                    help='вместе с --check: удалить неактуальные заказы из базы')
    ap.add_argument('--limit', type=int, help='сколько заказов проверять за проход (для --check)')
    args = ap.parse_args()

    cfg = core.load_config(args.config)

    if args.list:
        list_orders(cfg)
        return
    if args.check:
        core.check_actuality(cfg, remove_closed=args.remove_closed,
                             limit=args.limit)
        return

    interval = args.interval or cfg.get('interval_minutes', 10)

    if not args.loop:
        core.run_cycle(cfg)
        return
    print(f'Режим цикла: проверка каждые {interval} мин. Ctrl+C — остановить.')
    while True:
        try:
            core.run_cycle(cfg)
        except Exception as e:
            print(f'Ошибка цикла: {e!r}')
        time.sleep(interval * 60)

if __name__ == '__main__':
    main()
