"""Извлечение пар «подпись → значение» из сохранённой HTML-страницы лота.

Разметку аукционов мы не контролируем, и она меняется. Поэтому вместо жёстких
CSS-селекторов страница разбирается четырьмя независимыми способами, а поля
затем ищутся по словарю синонимов подписей:

1. JSON-LD (<script type="application/ld+json">) — схема Vehicle/Product;
2. JSON, встроенный в <script> (__NEXT_DATA__, __PRELOADED_STATE__, dataLayer);
3. пары «подпись/значение» из разметки (dt/dd, th/td, data-uname, aria-label);
4. регулярные выражения по видимому тексту — последний рубеж.

Такой порядок означает: если аукцион переделает вёрстку, поля обычно продолжат
находиться следующим способом, а не потеряются молча.
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterator

from bs4 import BeautifulSoup

from .normalize import is_placeholder, squeeze

# Теги, текст которых не относится к данным лота.
_NOISE_TAGS = ("script", "style", "noscript", "template", "svg", "iframe")

_LABELISH = re.compile(r"label|key|title|name|caption|term|header|head", re.I)
_VALUEISH = re.compile(r"value|val|data|content|text|desc", re.I)


_METADATA_SUFFIXES = ("type", "code", "unit", "currency", "format", "id", "schema", "disclaimer")


def _looks_like_metadata(key: str) -> bool:
    """True, если ключ похож на описание поля, а не на само значение."""
    return key.endswith(_METADATA_SUFFIXES)


def normalize_label(label: str) -> str:
    """Подпись -> ключ для поиска: только строчные буквы и цифры.

    'Primary Damage:' и 'lotdetailPrimaryDamagevalue' сводятся к сопоставимому виду.
    """
    return re.sub(r"[^a-z0-9]+", "", squeeze(label).lower())


class LotDocument:
    """Разобранная страница лота: набор кандидатов на каждое поле."""

    def __init__(self, html: str, source_name: str = "") -> None:
        self.source_name = source_name
        self.raw_html = html
        self.soup = BeautifulSoup(html, "lxml")
        self.pairs: dict[str, list[str]] = {}
        # Значения-заглушки («No», «None», «N/A») — отдельно: их отдаёт только
        # find(allow_weak=True), чтобы они не подменяли реальные данные.
        self.weak: dict[str, list[str]] = {}
        self._collect_json_ld()
        self._collect_embedded_json()
        self._collect_markup_pairs()
        self.text = self._visible_text()

    # ------------------------------------------------------------ сбор

    def _add(self, label: str, value: Any) -> None:
        """Добавляет кандидата, отбрасывая заглушки и слишком длинные простыни."""
        key = normalize_label(label)
        if not key or len(key) > 60:
            return
        text = squeeze(str(value))
        if not text or len(text) > 4000:
            return
        store = self.weak if is_placeholder(text) else self.pairs
        bucket = store.setdefault(key, [])
        if text not in bucket:
            bucket.append(text)

    def _collect_json_ld(self) -> None:
        for tag in self.soup.find_all("script", attrs={"type": re.compile("ld\\+json", re.I)}):
            payload = tag.string or tag.get_text() or ""
            try:
                data = json.loads(payload)
            except (ValueError, TypeError):
                continue
            for label, value in _flatten(data):
                self._add(label, value)

    def _collect_embedded_json(self) -> None:
        """Ищет JSON-объекты внутри <script> и раскладывает их в плоские пары."""
        for tag in self.soup.find_all("script"):
            payload = tag.string or tag.get_text() or ""
            if len(payload) < 40 or "{" not in payload:
                continue
            for blob in _json_objects(payload):
                for label, value in _flatten(blob):
                    self._add(label, value)

    def _collect_markup_pairs(self) -> None:
        # data-uname / data-testid / aria-label — так подписаны поля у Copart и IAAI.
        for attr in ("data-uname", "data-testid", "data-qa", "data-field", "aria-label", "itemprop"):
            for tag in self.soup.select(f"[{attr}]"):
                name = tag.get(attr) or ""
                value = tag.get("content") or tag.get_text(" ", strip=True)
                self._add(name, value)

        # <meta name="..." content="...">
        for tag in self.soup.find_all("meta"):
            name = tag.get("property") or tag.get("name") or ""
            self._add(name, tag.get("content") or "")

        # Списки определений: <dt>подпись</dt><dd>значение</dd>
        for dt in self.soup.find_all("dt"):
            dd = dt.find_next_sibling("dd")
            if dd is not None:
                self._add(dt.get_text(" ", strip=True), dd.get_text(" ", strip=True))

        # Таблицы: <th>подпись</th><td>значение</td> и <td>подпись</td><td>значение</td>
        for row in self.soup.find_all("tr"):
            cells = row.find_all(["th", "td"], recursive=False) or row.find_all(["th", "td"])
            if len(cells) == 2:
                self._add(cells[0].get_text(" ", strip=True), cells[1].get_text(" ", strip=True))

        # Пары «элемент-подпись + соседний элемент-значение» по классам.
        for tag in self.soup.find_all(["span", "div", "p", "li", "label", "strong", "b", "h3", "h4"]):
            classes = " ".join(tag.get("class") or []) + " " + (tag.get("id") or "")
            own_text = tag.get_text(" ", strip=True)
            if not own_text or len(own_text) > 60:
                continue
            looks_like_label = bool(_LABELISH.search(classes)) or own_text.rstrip().endswith(":")
            if not looks_like_label:
                continue
            sibling = tag.find_next_sibling()
            if sibling is not None:
                self._add(own_text, sibling.get_text(" ", strip=True))
            # Вариант «<div class=row><span class=label>..</span><span class=value>..</span></div>»
            parent = tag.parent
            if parent is not None:
                for candidate in parent.find_all(True, recursive=False):
                    if candidate is tag:
                        continue
                    candidate_classes = " ".join(candidate.get("class") or [])
                    if _VALUEISH.search(candidate_classes):
                        self._add(own_text, candidate.get_text(" ", strip=True))

        # «Подпись: значение» внутри одного элемента.
        for tag in self.soup.find_all(["li", "p", "span", "div", "td"]):
            text = tag.get_text(" ", strip=True)
            if 3 < len(text) <= 200 and ":" in text:
                label, _, value = text.partition(":")
                if len(label) <= 40 and value.strip():
                    self._add(label, value)

    def _visible_text(self) -> str:
        soup = BeautifulSoup(self.raw_html, "lxml")
        for tag in soup.find_all(_NOISE_TAGS):
            tag.decompose()
        return squeeze(soup.get_text(" ", strip=True))

    # ------------------------------------------------------------ поиск

    def find(self, synonyms: tuple[str, ...] | list[str], allow_weak: bool = False) -> tuple[str, str]:
        """Первое значение по списку синонимов подписи. -> (значение, по какой подписи найдено).

        Сначала точное совпадение ключа, затем совпадение по вхождению —
        так 'vin' не перепутается с 'vinlookupdisclaimer'.

        allow_weak=True подключает значения-заглушки. Нужно там, где «No» или
        «None» — это ответ по существу (ключи, «на ходу»), а не отсутствие данных.
        """
        normalized = [normalize_label(s) for s in synonyms]
        sources = (self.pairs, self.weak) if allow_weak else (self.pairs,)
        pool: dict[str, list[str]] = {}
        for source in sources:
            for key, values in source.items():
                pool.setdefault(key, []).extend(values)
        for want in normalized:
            if want in pool and pool[want]:
                return pool[want][0], want
        for want in normalized:
            if not want:
                continue
            matches = [(key, values) for key, values in pool.items() if want in key and values]
            # Ключ вида 'priceCurrency' или 'damageTypeCode' описывает поле, а не
            # содержит его значение, поэтому такие совпадения проверяем последними.
            matches.sort(key=lambda pair: _looks_like_metadata(pair[0]))
            if matches:
                key, values = matches[0]
                return values[0], key
        return "", ""

    def find_all(self, synonyms: tuple[str, ...] | list[str]) -> list[str]:
        """Все значения по синонимам — для полей, которые встречаются несколько раз."""
        found: list[str] = []
        for want in (normalize_label(s) for s in synonyms):
            for key, values in self.pairs.items():
                if want and want in key:
                    for value in values:
                        if value not in found:
                            found.append(value)
        return found

    def search_text(self, pattern: str | re.Pattern[str], group: int = 1) -> str:
        """Регулярное выражение по видимому тексту страницы."""
        regex = re.compile(pattern, re.I) if isinstance(pattern, str) else pattern
        match = regex.search(self.text)
        if not match:
            return ""
        try:
            return squeeze(match.group(group))
        except IndexError:  # в шаблоне нет запрошенной группы — берём всё совпадение
            return squeeze(match.group(0))

    def image_urls(self, limit: int = 40) -> list[str]:
        """Ссылки на фотографии лота: <img src>, data-src, srcset, og:image."""
        urls: list[str] = []

        def push(candidate: str | None) -> None:
            url = squeeze(candidate or "")
            if not url or url.startswith("data:"):
                return
            if not re.search(r"\.(jpe?g|png|webp|avif)(\?|$)", url, re.I) and "image" not in url.lower():
                return
            if re.search(r"logo|sprite|icon|placeholder|banner|pixel|avatar|flag", url, re.I):
                return
            if url not in urls:
                urls.append(url)

        for tag in self.soup.find_all("img"):
            for attr in ("src", "data-src", "data-original", "data-lazy", "data-image"):
                push(tag.get(attr))
            srcset = tag.get("srcset") or ""
            for part in srcset.split(","):
                push(part.strip().split(" ")[0] if part.strip() else "")
        for tag in self.soup.find_all("meta", attrs={"property": re.compile("image", re.I)}):
            push(tag.get("content"))
        for key in (
            "ogimage", "image", "images", "imageurl", "imageurls", "photo", "photos",
            "photourl", "highresimage", "fullimage", "largeimage", "thumbnailurl",
        ):
            for value in self.pairs.get(key, []):
                push(value)
        return urls[:limit]


# ---------------------------------------------------------------- вспомогательное


def _flatten(data: Any, path: tuple[str, ...] = (), depth: int = 0) -> Iterator[tuple[str, Any]]:
    """Раскладывает вложенный JSON в пары «подпись → значение».

    Для каждого листа выдаётся и последний ключ, и склейка двух последних.
    Это нужно из-за обёрток вида {"mileageFromOdometer": {"value": 78452}}:
    по одному ключу "value" поле не опознать, а "mileageFromOdometervalue" —
    уже узнаваемая подпись.
    """
    if depth > 12:
        return
    if isinstance(data, dict):
        for key, value in data.items():
            yield from _flatten(value, path + (str(key),), depth + 1)
    elif isinstance(data, list):
        for item in data[:60]:
            yield from _flatten(item, path, depth + 1)
    elif data is not None and path:
        if any(_is_schema_noise(segment) for segment in path[-2:]):
            return  # @type, @context, unitCode и подобное — метаданные, а не поля лота
        value = "YES" if data is True else "NO" if data is False else data
        yield path[-1], value
        if len(path) >= 2:
            yield f"{path[-2]}{path[-1]}", value


# Служебные ключи JSON-LD и schema.org: значениями полей лота не бывают.
_SCHEMA_NOISE = {"unitcode", "unittext", "context", "graph", "type"}


def _is_schema_noise(segment: str) -> bool:
    if segment.startswith("@"):
        return True
    return segment.strip("_").lower() in _SCHEMA_NOISE


def _json_objects(payload: str, max_objects: int = 12) -> Iterator[Any]:
    """Вырезает из текста скрипта сбалансированные JSON-объекты и парсит их.

    Строковые литералы учитываются, чтобы '}' внутри строки не оборвал объект.
    """
    found = 0
    index = 0
    length = len(payload)
    while index < length and found < max_objects:
        if payload[index] != "{":
            index += 1
            continue
        depth = 0
        in_string = False
        escaped = False
        for position in range(index, length):
            char = payload[position]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    chunk = payload[index : position + 1]
                    if len(chunk) > 20:
                        try:
                            yield json.loads(chunk)
                            found += 1
                        except ValueError:
                            pass
                    index = position + 1
                    break
        else:
            return
