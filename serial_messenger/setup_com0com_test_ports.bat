@echo off
setlocal EnableExtensions
title Install and configure com0com

rem This driver installation requires an elevated Command Prompt.
net session >nul 2>&1
if errorlevel 1 (
    echo Requesting administrator privileges...
    powershell -NoProfile -Command "Start-Process -FilePath '%ComSpec%' -ArgumentList '/c ""%~f0""' -Verb RunAs"
    exit /b
)

echo.
echo Installing the com0com virtual serial-port driver...
echo.

rem Prefer winget when a manifest is available on this machine.
where winget >nul 2>&1
if errorlevel 1 (
    echo winget is not available. Using the official download instead.
) else (
    echo Trying winget...
    winget install --exact --id com0com.com0com --silent --accept-package-agreements --accept-source-agreements
    if errorlevel 1 (
        echo winget could not install com0com. Using the official download instead.
    ) else (
        goto :installed
    )
)

where curl >nul 2>&1
if errorlevel 1 (
    echo ERROR: Neither winget nor curl is available on this computer.
    echo Install App Installer or use a current Windows 10/11 build, then run this script again.
    pause
    exit /b 1
)

set "WORK_DIR=%TEMP%\com0com-install"
set "ARCHIVE=%WORK_DIR%\com0com.zip"
set "DOWNLOAD_URL=https://sourceforge.net/projects/com0com/files/com0com/3.0.0.0/com0com-3.0.0.0-i386-and-x64-signed.zip/download"

if exist "%WORK_DIR%" rmdir /s /q "%WORK_DIR%"
mkdir "%WORK_DIR%"

echo Downloading the signed com0com 3.0.0.0 package...
curl -L --fail --show-error -o "%ARCHIVE%" "%DOWNLOAD_URL%"
if errorlevel 1 goto :download_failed

powershell -NoProfile -Command "Expand-Archive -LiteralPath '%ARCHIVE%' -DestinationPath '%WORK_DIR%' -Force"
if errorlevel 1 goto :download_failed

if /i "%PROCESSOR_ARCHITECTURE%"=="AMD64" (
    set "INSTALLER=%WORK_DIR%\Setup_com0com_v3.0.0.0_W7_x64_signed.exe"
) else (
    set "INSTALLER=%WORK_DIR%\Setup_com0com_v3.0.0.0_W7_x86_signed.exe"
)

echo Starting the com0com installer. Complete the setup wizard, then return here.
start /wait "" "%INSTALLER%"
if errorlevel 1 (
    echo ERROR: The com0com installer returned an error.
    pause
    exit /b 1
)

:installed
call :find_setupc
if not defined SETUPC (
    echo ERROR: com0com was installed, but setupc.exe was not found.
    echo Open the com0com Setup Command Prompt and run:
    echo   install PortName=COM10 PortName=COM11
    pause
    exit /b 1
)

call :pair_exists
if "%PAIR_EXISTS%"=="1" (
    echo The COM10 ^<-> COM11 pair already exists.
) else (
    echo Creating the virtual null-modem pair COM10 ^<-> COM11...
    "%SETUPC%" install PortName=COM10 PortName=COM11
    if errorlevel 1 (
        echo ERROR: com0com could not create COM10 and COM11.
        echo One of these port names may be in use. Current configuration:
        "%SETUPC%" list
        pause
        exit /b 1
    )
)

echo.
echo com0com is ready. The virtual pair is COM10 ^<-> COM11.
echo Start two copies of main.py, select COM10 in one and COM11 in the other,
echo then choose the same stop-bit setting in both windows.
pause
exit /b 0

:pair_exists
set "PAIR_EXISTS=0"
"%SETUPC%" list | findstr /i /c:"PortName=COM10" >nul
if errorlevel 1 exit /b 0
"%SETUPC%" list | findstr /i /c:"PortName=COM11" >nul
if errorlevel 1 exit /b 0
set "PAIR_EXISTS=1"
exit /b 0

:find_setupc
set "SETUPC="
for %%P in (
    "%ProgramFiles(x86)%\com0com\setupc.exe"
    "%ProgramFiles%\com0com\setupc.exe"
    "%WORK_DIR%\setupc.exe"
) do (
    if not defined SETUPC if exist "%%~fP" set "SETUPC=%%~fP"
)
if not defined SETUPC (
    for /f "delims=" %%P in ('where setupc.exe 2^>nul') do (
        if not defined SETUPC set "SETUPC=%%P"
    )
)
exit /b 0

:download_failed
echo ERROR: The official com0com package could not be downloaded or extracted.
echo Check the Internet connection and run this script again.
pause
exit /b 1
