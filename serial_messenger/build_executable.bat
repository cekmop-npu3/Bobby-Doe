@echo off
setlocal

cd /d "%~dp0"
py -m PyInstaller --noconfirm --clean --windowed --onefile --name SerialMessenger --paths "%CD%" --collect-submodules serial main.py

if errorlevel 1 exit /b %errorlevel%

echo.
echo Build complete: dist\SerialMessenger.exe
