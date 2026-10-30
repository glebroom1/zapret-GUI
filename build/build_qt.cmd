@echo off
rem Сборка Zapret Desk Qt в переносимый EXE. Отдельный venv; системный Python не меняется.
rem Tk-версия 0.4 собирается прежним build.cmd и НЕ перезаписывается.
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
".venv-build\Scripts\python.exe" -m pip install PySide6==6.12.0 >build\pip-pyside.log 2>&1 || goto :fail
".venv-build\Scripts\pyinstaller.exe" --clean --noconfirm --distpath dist --workpath build\work ^
  build\zapret_desk_qt.spec >build\pyinstaller-qt.log 2>&1 || goto :fail

if exist "dist\ZapretDeskQt.exe" (
  for %%I in ("dist\ZapretDeskQt.exe") do echo Готово: %%~fI ^(%%~zI байт^)
  exit /b 0
)
:fail
echo Сборка не удалась, подробности: build\pip-pyside.log, build\pyinstaller-qt.log
exit /b 1
