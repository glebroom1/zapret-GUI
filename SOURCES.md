# Исходники сторонних компонентов

Архив [Zapret-GUI-0.7-third-party-sources.zip](https://github.com/glebroom1/zapret-GUI/releases/download/v0.7/Zapret-GUI-0.7-third-party-sources.zip) в релизе v0.7 содержит исходники перечисленных LGPL-библиотек тех же версий, используемых в 0.8.0-beta.6. Библиотеки не модифицировались.
Сведения об исходных загрузках и SHA-256 приведены в source-manifest.json.

Оболочка и инструкции пересборки: https://github.com/glebroom1/zapret-GUI
Для замены динамических Qt/PySide6 библиотек установите совместимые изменённые пакеты в окружение сборки и пересоберите EXE по PACKAGING.md. Проверка подписи библиотек и запрет их изменения не применяются.
WinDivert.dll, WinDivert64.sys и cygwin1.dll распаковываются в %LOCALAPPDATA%\ZapretDesk\runtime и могут быть заменены пользователем совместимыми версиями при остановленном движке.
Обратная разработка, необходимая для отладки изменений LGPL-компонентов, не запрещается.

Официальная встроенная сборка Flowseal 1.10.4: https://github.com/Flowseal/zapret-discord-youtube/releases/tag/1.10.4
Её SHA-256: 291af673bd0238506f9548e0e94ca0abf244000f4c9a06960544fb01c5f97b34

Источники архивов:
- WinDivert-2.2.2-source.tar.gz: https://codeload.github.com/basil00/WinDivert/tar.gz/refs/tags/v2.2.2
- Cygwin-3.4.10-source.tar.gz: https://codeload.github.com/mirror/newlib-cygwin/tar.gz/89a1e4a573d40b233011402f42630b970ff71291
- PySide6-6.12.0-source.tar.xz: https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.12.0-src/pyside-setup-everywhere-src-6.12.0.tar.xz
- QtBase-6.12.0-source.tar.gz: https://codeload.github.com/qt/qtbase/tar.gz/refs/tags/v6.12.0
- QtSvg-6.12.0-source.tar.gz: https://codeload.github.com/qt/qtsvg/tar.gz/refs/tags/v6.12.0
