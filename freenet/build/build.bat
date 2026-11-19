@echo off
rem ================= FREENET — сборка EXE =================
rem Требования: Python 3.10+ в PATH, интернет.
pip install --yes --upgrade pip >nul 2>&1
pip install flask requests pywebview pyinstaller

if not exist build mkdir build

pyinstaller ^
  --noconfirm --clean --onefile --windowed ^
  --name FREENET ^
  --icon assets\freenet.ico ^
  --uac-admin ^
  --add-data "ui;ui" ^
  --hidden-import webview ^
  --hidden-import flask ^
  --hidden-import requests ^
  main.py

echo.
echo Готово: dist\FREENET.exe
pause
