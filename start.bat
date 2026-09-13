@echo off
cd /d "%~dp0"
title WeChat Style Workshop
where python >nul 2>nul
if %errorlevel%==0 (
  python serve.py
  goto end
)
where py >nul 2>nul
if %errorlevel%==0 (
  py serve.py
  goto end
)
if exist "E:\Python-3.11.13 (2)\python.exe" (
  "E:\Python-3.11.13 (2)\python.exe" serve.py
  goto end
)
echo Python not found. Please install Python 3 first.
pause
:end
