# Сторонние компоненты 0.8

Лицензия оболочки — MIT. Полные тексты лицензий и copyright notices находятся в [licenses/](licenses/), включая уведомления из исходников точных версий. Сторонние библиотеки не модифицировались.

| Компонент | Лицензия | Источник |
|---|---|---|
| zapret / Flowseal 1.10.4 | MIT | https://github.com/Flowseal/zapret-discord-youtube/releases/tag/1.10.4 ; https://github.com/bol-van/zapret |
| WinDivert 2.2.2 | LGPL-3.0 или GPL-2.0 по выбору | https://github.com/basil00/WinDivert/tree/v2.2.2 |
| Cygwin 3.4.10 (cygwin1.dll) | LGPL-3.0-or-later с исключением linking; отдельные части BSD | https://github.com/mirror/newlib-cygwin/tree/89a1e4a573d40b233011402f42630b970ff71291 |
| PySide6 / QtBase / QtSvg 6.12.0 | LGPL-3.0; дополнительные уведомления отдельных компонентов в licenses/source-notices | https://download.qt.io/official_releases/QtForPython/pyside6/ ; https://github.com/qt/qtbase/tree/v6.12.0 ; https://github.com/qt/qtsvg/tree/v6.12.0 |
| Python 3.14 | PSF и включённые уведомления | https://www.python.org/downloads/ |
| PyInstaller 6.22.0 | GPL-2.0-or-later с исключением для bootloader | https://github.com/pyinstaller/pyinstaller/tree/v6.22.0 |
| zlib | zlib | https://zlib.net/ |
| psutil 7.2.2 | BSD-3-Clause | https://github.com/giampaolo/psutil/tree/release-7.2.2 |
| tg-ws-proxy, commit 6690b788432fd0f59b0f38e228494bcd6bede1ca | MIT | https://github.com/Flowseal/tg-ws-proxy/tree/6690b788432fd0f59b0f38e228494bcd6bede1ca |
| cryptography 46.0.5 | Apache-2.0 или BSD-3-Clause; включённые уведомления | https://github.com/pyca/cryptography |
| certifi 2026.10.23 | MPL-2.0 | https://github.com/certifi/python-certifi |
| httpx 0.28.1 / httpcore 1.0.9 | BSD-3-Clause | https://github.com/encode/httpx ; https://github.com/encode/httpcore |
| h2 4.4.1 / hpack 4.2.0 / hyperframe 6.1.0 | MIT | https://github.com/python-hyper |
| anyio 4.15.1 / cffi 2.1.1 | MIT | https://github.com/agronholm/anyio ; https://foss.heptapod.net/pypy/cffi |
| idna 3.20 / pycparser 3.12 | BSD-3-Clause | https://github.com/kjd/idna ; https://github.com/eliben/pycparser |

Тексты лицензий зависимостей Telegram: `licenses/telegram-dependencies/`.
Копия ядра tg-ws-proxy и точная версия находятся в `vendor/tg_ws_proxy/`.

Архивы исходников LGPL-компонентов доступны по ссылкам в [SOURCES.md](SOURCES.md); там же SHA-256, порядок замены библиотек и пересборки. Обратная разработка для отладки изменений LGPL-компонентов разрешается.
