"""Собирает закладку из save_auction_page.js.

    python3 tools/bookmarklet/build.py

Пишет рядом два файла:
  bookmarklet.txt — строка «javascript:…» для ручного создания закладки;
  install.html    — страница установки: кнопку перетащить на панель закладок.
"""

from __future__ import annotations

import html
import re
from pathlib import Path
from urllib.parse import quote

HERE = Path(__file__).resolve().parent


def build_code(source: str) -> str:
    code = re.sub(r"/\*[\s\S]*?\*/", " ", source)          # в исходнике только /* */-комментарии
    code = " ".join(line.strip() for line in code.splitlines() if line.strip())
    return re.sub(r"\s{2,}", " ", code)


def build_url(source: str) -> str:
    return "javascript:" + quote(build_code(source), safe="()';,!*~-_.=:[]{}?&+/<>|\"")


INSTALL_PAGE = """<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Закладка «Сохранить для анализа»</title>
<style>
  :root {{ --bg:#f7f7f5; --fg:#1d1d1b; --muted:#5f5f5a; --card:#fff; --line:#e3e3de; --accent:#1f6f43; }}
  @media (prefers-color-scheme: dark) {{ :root {{ --bg:#161615; --fg:#ededea; --muted:#a3a39d; --card:#1f1f1d; --line:#33332f; --accent:#4fb37f; }} }}
  body {{ margin:0; background:var(--bg); color:var(--fg); font:16px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif; }}
  main {{ max-width:720px; margin:0 auto; padding:32px 16px 48px; }}
  h1 {{ font-size:26px; margin:0 0 8px; }}
  p.lead {{ color:var(--muted); margin:0 0 28px; }}
  .card {{ background:var(--card); border:1px solid var(--line); border-radius:12px; padding:20px; margin-bottom:16px; }}
  .bm {{ display:inline-block; padding:12px 20px; border-radius:10px; background:var(--accent); color:#fff; text-decoration:none; font-weight:600; cursor:grab; }}
  ol {{ padding-left:22px; margin:8px 0 0; }} li {{ margin:6px 0; }}
  code, textarea {{ font:13px/1.4 ui-monospace,Menlo,Consolas,monospace; }}
  textarea {{ width:100%; box-sizing:border-box; height:90px; background:var(--bg); color:var(--fg); border:1px solid var(--line); border-radius:8px; padding:8px; }}
  .muted {{ color:var(--muted); font-size:14px; }}
</style></head>
<body><main>
<h1>Закладка «Сохранить для анализа»</h1>
<p class="lead">Один клик на странице аукциона — и страница сохраняется в «Загрузки» файлом для таблицы с потолками ставок.</p>

<div class="card">
  <strong>1. Установка</strong>
  <ol>
    <li>Включите панель закладок: <code>Ctrl+Shift+B</code> (Mac: <code>Cmd+Shift+B</code>).</li>
    <li>Перетащите зелёную кнопку на панель закладок:</li>
  </ol>
  <p style="margin:16px 0"><a class="bm" href="{url}" onclick="alert('Не нажимайте здесь — перетащите кнопку на панель закладок.'); return false;">💾 Сохранить для анализа</a></p>
  <p class="muted">Не перетаскивается? Создайте закладку вручную (правый клик по панели → «Добавить страницу»), назовите «Сохранить для анализа» и вставьте в поле адреса весь текст ниже:</p>
  <textarea readonly onclick="this.select()">{url_text}</textarea>
</div>

<div class="card">
  <strong>KBB без кликов: расширение Chrome «Lot Analyzer KBB» (один раз, 1 минута)</strong>
  <ol>
    <li>В Chrome откройте адрес <code>chrome://extensions</code>.</li>
    <li>Справа вверху включите «Режим разработчика» (Developer mode).</li>
    <li>Нажмите «Загрузить распакованное» (Load unpacked) и выберите папку <code>tools/kbb_extension</code> в папке программы.</li>
    <li>Готово: в окне программы «получить KBB ↗» или «KBB для лучших 15» — вкладка KBB откроется и всё сделает сама, цены появятся в окне. Закладку на KBB нажимать не нужно.</li>
  </ol>
  <p class="muted">Расширение работает на kbb.com (только на вкладках, открытых из окна программы; по окончании вкладка закрывается сама) и на carmaxauctions.com: машинам с KBB вписывает в Notes строку «KBB …$» и расчёт программы (строки «LA …»; ваш текст заметки остаётся). Данные берёт только у окна программы на вашем компьютере (127.0.0.1) и никуда не отправляет. После обновления программы: на <code>chrome://extensions</code> у «Lot Analyzer KBB» нажмите ⟳ (или выберите папку новой версии).</p>
</div>

<div class="card">
  <strong>2. Использование</strong>
  <ol>
    <li>Откройте watch list на CarMax Auctions (или карточку лота на ACV, Manheim, ADESA) и дождитесь, пока всё загрузится.</li>
    <li>Если хотите, откройте карточку одной машины — её повреждения и протектор тоже сохранятся.</li>
    <li>Manheim, результаты поиска на нескольких страницах: закладка спросит «собрать ВСЕ страницы?» — «ОК», и она сама пролистает страницы (около 3 секунд на страницу) и сохранит один файл. Весь список площадки проще взять кнопкой экспорта CSV на Manheim — окно читает и его.</li>
    <li>Нажмите закладку «💾 Сохранить для анализа». Внизу справа появится зелёная плашка с именем файла.</li>
    <li>Файл <code>CarMax_Member_Dashboard_2026-09-29_0815.html</code> — в «Загрузках». Пришлите его в чат или положите в <code>samples/</code> и запустите <code>python3 -m lot_analyzer samples/</code>.</li>
  </ol>
</div>

<div class="card">
  <strong>Что сохраняется</strong>
  <p class="muted">Страница такой, какой вы её видите, включая текст ваших заметок (KBB, MP, FB, история). Скрипты, стили и значки выбрасываются: файл меньше, и в нём не остаётся служебных данных сессии. Пароли и скрытые поля не сохраняются. Закладка работает только в вашем браузере и никуда ничего не отправляет.</p>
</div>
</main></body></html>
"""


def main() -> None:
    source = (HERE / "save_auction_page.js").read_text(encoding="utf-8")
    url = build_url(source)
    (HERE / "bookmarklet.txt").write_text(url + "\n", encoding="utf-8")
    page = INSTALL_PAGE.format(url=html.escape(url, quote=True), url_text=html.escape(url))
    (HERE / "install.html").write_text(page, encoding="utf-8")
    # Расширение Chrome: тот же код, но запускается само на вкладке kbb.com, открытой из окна программы.
    extension = HERE.parent / "kbb_extension"
    extension.mkdir(exist_ok=True)
    (extension / "content.js").write_text(
        "/* Собрано из tools/bookmarklet/save_auction_page.js (python3 tools/bookmarklet/build.py) — не править вручную. */\n"
        "window.lotAnalyzerAuto = true;\n" + source, encoding="utf-8")
    print(f"bookmarklet.txt: {len(url)} символов\ninstall.html готов\nkbb_extension/content.js готов")


if __name__ == "__main__":
    main()
