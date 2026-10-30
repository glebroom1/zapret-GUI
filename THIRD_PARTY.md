# Сторонние компоненты

Исходный репозиторий оболочки не включает бинарную сборку zapret. При создании EXE используются сторонние компоненты:

| Компонент | Источник |
|---|---|
| zapret | https://github.com/bol-van/zapret |
| zapret-discord-youtube | https://github.com/Flowseal/zapret-discord-youtube |
| WinDivert | https://github.com/basil00/WinDivert |
| Cygwin (cygwin1.dll) | https://www.cygwin.com/ |
| PySide6 / Qt | https://www.qt.io/qt-for-python |
| PyInstaller | https://pyinstaller.org/ |
| Antigravity (визуальный референс) | https://github.com/confeden/Antigravity |

Этот список не заменяет тексты лицензий. До публичного распространения готового EXE требуется сверить фактический состав встроенной сборки и приложить лицензии и обязательные уведомления соответствующих версий. Код оболочки распространяется по лицензии MIT (см. LICENSE); она не заменяет лицензии сторонних компонентов.

## WinDivert

WinDivert распространяется по выбору получателя по лицензии **GNU LGPL версии 3 или GNU GPL версии 2**. MIT-лицензия оболочки не заменяет эти условия и не распространяется на WinDivert.

- Исходный проект, исходный код и условия лицензирования: https://github.com/basil00/WinDivert
- Уведомление в сборке Flowseal: https://github.com/Flowseal/zapret-discord-youtube/blob/main/LICENSE.txt

Для публичного пакета EXE необходимо приложить соответствующие тексты лицензий и выполнить условия распространения фактически включённой версии. Одной этой ссылки и краткого указания лицензии недостаточно, чтобы считать оформление бинарного релиза завершённым.

