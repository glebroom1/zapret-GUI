# Запуск, сборка и проверка

## Текущий кандидат Windows 0.9.0

Актуальный воспроизводимый путь сборки (остальные примеры ниже — исторические):

```powershell
python -m venv .venv-build
.\.venv-build\Scripts\python.exe -m pip install -r requirements-build.txt
.\.venv-build\Scripts\python.exe build/run_beta5.py
.\.venv-build\Scripts\python.exe build/qa_worker_recovery.py
.\.venv-build\Scripts\python.exe build/final_checks5.py
```

Результат: `dist/Zapret-GUI-0.9.0.exe`. Рядом с проектом нужна исходная сборка Flowseal1.10.4; `prepare_payload.py` копирует её и обнуляет только личные списки в копии. Исходная сборка не меняется. Не запускайте старые package_beta4/build_beta4 для этого кандидата. После дополнительных сетевых проверок и отчёта упаковка: `python build/package_beta5.py`. Скрипт не публикует файлы. Состав исходников: `dist/PUBLISH-FILES.json`.

Сетевая приёмка с реальным WinDivert требует административного процесса и отдельно зафиксированного маршрута. `--smoke` не подтверждает обход или видео. Ограничения этого кандидата: [RELEASE-NOTES-0.9.0.md](RELEASE-NOTES-0.9.0.md).

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

Результат: `dist\Zapret-GUI-0.8.1-beta4.exe`. Скрипт встраивает содержимое сторонней сборки в EXE. Перед распространением сохраните требуемые лицензии и уведомления сторонних компонентов. Файлы старого Tk-интерфейса оставлены для совместимости; актуальная точка входа — `app_qt.py`, упаковка — `launch_qt.py`.

## Тесты

Часть интеграционных тестов использует ту же папку `zapret-discord-youtube-1.10.4` рядом с проектом.

```powershell
.\.venv-build\Scripts\python.exe -m unittest discover -q
```

Самопроверка собранного приложения без подключения движка:

```powershell
$env:LOCALAPPDATA = Join-Path $PWD ('build\smoke-' + [guid]::NewGuid().ToString('N'))
Start-Process -FilePath '.\dist\Zapret-GUI-0.8.1-beta4.exe' -ArgumentList '--smoke' -Wait
Get-Content (Join-Path $env:LOCALAPPDATA 'ZapretDesk\smoke_qt.json')
```

Выполняйте пример в отдельном PowerShell: переменная LOCALAPPDATA изменяется для этой сессии. Успешная самопроверка содержит `errors: []`; она не подтверждает доступность сервисов у конкретного провайдера.


Для этой беты можно использовать build/build_beta4.ps1 либо выполнить те же
команды в своём venv. Перед PyInstaller запустите build/prepare_payload.py и
задайте ZAPRET_SOURCE=build/engine-payload и ZAPRET_NAME=zapret-discord-youtube-1.10.4.
Это исключает пользовательские списки и резервные копии из встраиваемого движка.
Релизный пакет формирует build/package_beta4.py после верификации конечного EXE.
Для updater прикрепите сгенерированный update-windows.json и отдельный EXE
к тому же GitHub Release. Подробный формат и поведение: RELEASE-NOTES-0.8.1-beta4.md.
