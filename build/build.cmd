@echo off
rem Сборка Zapret Desk в переносимый EXE. Зависимость ставится в отдельный venv
rem внутри проекта; системный Python ничего не получает.
setlocal
cd /d "%~dp0.."
set "ZAPRET_NAME=zapret-discord-youtube-1.10.4"
if not exist "..\%ZAPRET_NAME%\bin\winws.exe" (
  echo Сборка "%ZAPRET_NAME%" не найдена рядом с проектом.
  exit /b 1
)

if not exist ".venv-build" (
  python -m venv ".venv-build" || goto :fail
)
rem Генерируем модуль zapret_info.py: имя встроенной сборки для runtime-папки.
".venv-build\Scripts\python.exe" build\make_zapret_info.py >build\zapret_info.log 2>&1 || goto :fail
".venv-build\Scripts\python.exe" -m pip install pyinstaller==6.22.3 >build\pip.log 2>&1 || goto :fail
".venv-build\Scripts\pyinstaller.exe" --clean --noconfirm --distpath dist --workpath build\work ^
  build\zapret_desk.spec >build\pyinstaller.log 2>&1 || goto :fail

if exist "dist\ZapretDesk.exe" (
  for %%I in ("dist\ZapretDesk.exe") do echo Готово: %%~fI ^(%%~zI байт^)
  exit /b 0
)
:fail
echo Сборка не удалась, подробности: build\pip.log, build\pyinstaller.log
exit /b 1
