"""Хранилище заказов: orders.json (всё, с дедупом) + orders.csv (Excel)."""
import csv
import json
import os

FIELDS = ['found_at', 'source', 'title', 'price', 'url', 'date',
          'category', 'responses', 'description']

class OrderStore:
    def __init__(self, json_path, csv_path, max_orders=5000):
        self.json_path = json_path
        self.csv_path = csv_path
        self.max_orders = max_orders
        self.orders = self._load_json()

    def _load_json(self):
        if not os.path.exists(self.json_path):
            return []
        try:
            with open(self.json_path, encoding='utf-8') as f:
                data = json.load(f)
            return data if isinstance(data, list) else []
        except (json.JSONDecodeError, OSError):
            return []

    @staticmethod
    def _key(order):
        return f"{order.get('source')}|{order.get('url')}"

    def _save_json(self):
        self.orders.sort(key=lambda o: o.get('found_at', ''), reverse=True)
        self.orders = self.orders[:self.max_orders]
        tmp = self.json_path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(self.orders, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.json_path)

    def _append_csv(self, new_orders):
        need_header = not os.path.exists(self.csv_path) or os.path.getsize(self.csv_path) == 0
        with open(self.csv_path, 'a', encoding='utf-8-sig', newline='') as f:
            w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction='ignore')
            if need_header:
                w.writeheader()
            for o in new_orders:
                w.writerow({k: o.get(k, '') for k in FIELDS})

    def add(self, orders):
        """Добавляет только новые заказы. Возвращает список новых."""
        seen = {self._key(o) for o in self.orders}
        fresh = []
        for o in orders:
            if not o.get('url'):
                continue
            k = self._key(o)
            if k in seen:
                continue
            seen.add(k)
            fresh.append(o)
        if fresh:
            self.orders.extend(fresh)
            self._save_json()
            self._append_csv(fresh)
        return fresh
