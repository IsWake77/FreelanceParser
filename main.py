# -*- coding: utf-8 -*-
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

try:  # чтобы кириллица корректно печаталась в консоли Windows
    if os.name == 'nt':
        os.system('chcp 65001 >nul 2>&1')
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass

import core


def main():
    ap = argparse.ArgumentParser(description='Парсер фриланс-заказов')
    ap.add_argument('--config', default='config.json', help='путь к config.json')
    ap.add_argument('--loop', action='store_true', help='работать в цикле')
    ap.add_argument('--interval', type=int, help='интервал цикла в минутах (переопределяет конфиг)')
    args = ap.parse_args()

    cfg = core.load_config(args.config)
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
