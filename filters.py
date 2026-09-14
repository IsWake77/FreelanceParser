"""Фильтр заказов по ключевым словам (стемы, исключения)."""
import re

def _normalize(text):
    """Нижний регистр + ё->е + схлопывание пробелов."""
    t = (text or '').lower().replace('ё', 'е')
    return re.sub(r'\s+', ' ', t)

class KeywordFilter:
    """Заказ проходит, если найдено >= min_matches ключевых слов и ни одного
    исключающего. Ключевые слова можно писать со * для усечённого сравнения:
    'бот*' найдёт 'бота', 'ботов', 'боты'."""

    def __init__(self, keywords, exclude=None, min_matches=1):
        self.keywords = [self._compile(k) for k in (keywords or [])]
        self.exclude = [self._compile(k) for k in (exclude or [])]
        self.min_matches = max(1, int(min_matches or 1))

    @staticmethod
    def _compile(word):
        w = _normalize(word)
        if w.endswith('*'):
            return ('prefix', w[:-1])
        return ('exact', w)

    @staticmethod
    def _hit(kind, word, text):
        pat = r'(?<![a-zа-я0-9])' + re.escape(word)
        if kind == 'prefix':
            return re.search(pat, text) is not None
        return re.search(pat + r'(?![a-zа-я0-9])', text) is not None

    def match(self, title, description=''):
        text = _normalize(f'{title} {description}')
        if any(self._hit(k, w, text) for k, w in self.exclude):
            return False, 0, []
        hits = [w for k, w in self.keywords if self._hit(k, w, text)]
        return len(hits) >= self.min_matches, len(hits), hits
