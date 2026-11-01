# Запуск, сборка и проверка

Проверенное локальное окружение: Windows, Python 3.14, PySide6 6.12.0, PyInstaller 6.22.0.

## Запуск исходников

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app_qt.py
```

Исходный код не включает сторонние бинарные компоненты zapret. Выберите совместимую папку сборки в интерфейсе или импортируйте ZIP. Это требование относится к запуску исходников; готовый EXE включает компоненты внутри.

## Сборка EXE

Поместите совместимую сборку `zapret-discord-youtube-1.10.4` рядом с папкой проекта. Ожидается наличие `bin/winws.exe`, компонентов WinDivert, списков и `general*.bat`. Пользовательские списки в сборке перед упаковкой должны быть очищены от личных данных.

```powershell
python -m venv .venv-build
.\.venv-build\Scripts\python.exe -m pip install -r requirements-build.txt
.\build\build_qt.cmd
```

Результат: `dist\Zapret-GUI-0.8-beta6.exe`. Скрипт встраивает содержимое сторонней сборки в EXE. Перед распространением сохраните требуемые лицензии и уведомления сторонних компонентов. Файлы старого Tk-интерфейса оставлены для совместимости; актуальная точка входа — `app_qt.py`, упаковка — `launch_qt.py`.

## Тесты

Часть интеграционных тестов использует ту же папку `zapret-discord-youtube-1.10.4` рядом с проектом.

```powershell
.\.venv-build\Scripts\python.exe -m unittest discover -q
```

Самопроверка собранного приложения без подключения движка:

```powershell
$env:LOCALAPPDATA = Join-Path $PWD ('build\smoke-' + [guid]::NewGuid().ToString('N'))
Start-Process -FilePath '.\dist\Zapret-GUI-0.8-beta6.exe' -ArgumentList '--smoke' -Wait
Get-Content (Join-Path $env:LOCALAPPDATA 'ZapretDesk\smoke_qt.json')
```

Выполняйте пример в отдельном PowerShell: переменная LOCALAPPDATA изменяется для этой сессии. Успешная самопроверка содержит `errors: []`; она не подтверждает доступность сервисов у конкретного провайдера.
