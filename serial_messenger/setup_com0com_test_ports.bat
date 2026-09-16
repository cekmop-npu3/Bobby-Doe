@echo off
setlocal EnableExtensions EnableDelayedExpansion
title Install and configure com0com

rem ============================================================
rem Require Administrator privileges
rem ============================================================

net session >nul 2>&1
if errorlevel 1 (
    echo Requesting administrator privileges...
    powershell -NoProfile -Command ^
        "Start-Process -FilePath '%ComSpec%' -ArgumentList '/c ""%~f0""' -Verb RunAs"
    exit /b
)

echo.
echo ============================================================
echo Installing/configuring com0com...
echo ============================================================
echo.

rem ============================================================
rem First check whether setupc.exe already exists
rem ============================================================

call :find_setupc

if defined SETUPC (
    echo Found existing com0com installation:
    echo   "%SETUPC%"
    echo.
    goto :configure
)

echo No usable existing com0com installation was found.
echo.

rem ============================================================
rem Check for curl
rem ============================================================

where curl.exe >nul 2>&1
if errorlevel 1 (
    echo ERROR: curl.exe was not found.
    echo.
    echo Windows 10/11 normally includes curl.exe.
    echo Install/update Windows and run this file again.
    pause
    exit /b 1
)

rem ============================================================
rem Prepare temporary download directory
rem ============================================================

set "WORK_DIR=%TEMP%\com0com-install"
set "ARCHIVE=%WORK_DIR%\com0com.zip"

set "DOWNLOAD_URL=https://sourceforge.net/projects/com0com/files/com0com/3.0.0.0/com0com-3.0.0.0-i386-and-x64-signed.zip/download"

if exist "%WORK_DIR%" (
    rmdir /s /q "%WORK_DIR%"
)

mkdir "%WORK_DIR%"

if errorlevel 1 (
    echo ERROR: Could not create temporary directory:
    echo   "%WORK_DIR%"
    pause
    exit /b 1
)

rem ============================================================
rem Download com0com
rem ============================================================

echo Downloading official signed com0com package...
echo.

curl.exe -L --fail --show-error --retry 3 ^
    -o "%ARCHIVE%" "%DOWNLOAD_URL%"

if errorlevel 1 goto :download_failed

if not exist "%ARCHIVE%" goto :download_failed

rem ============================================================
rem Extract ZIP
rem ============================================================

echo.
echo Extracting com0com...
echo.

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "Expand-Archive -LiteralPath '%ARCHIVE%' -DestinationPath '%WORK_DIR%' -Force"

if errorlevel 1 goto :download_failed

rem ============================================================
rem Find the correct installer
rem ============================================================

set "INSTALLER="

rem Prefer x64 on 64-bit Windows.
if /i "%PROCESSOR_ARCHITECTURE%"=="AMD64" (
    for /f "delims=" %%F in ('where /r "%WORK_DIR%" *x64*signed.exe 2^>nul') do (
        if not defined INSTALLER (
            if exist "%%~fF" set "INSTALLER=%%~fF"
        )
    )
)

rem Covers 32-bit cmd.exe running on 64-bit Windows.
if /i "%PROCESSOR_ARCHITEW6432%"=="AMD64" (
    if not defined INSTALLER (
        for /f "delims=" %%F in ('where /r "%WORK_DIR%" *x64*signed.exe 2^>nul') do (
            if not defined INSTALLER (
                if exist "%%~fF" set "INSTALLER=%%~fF"
            )
        )
    )
)

rem Fall back to x86 installer.
if not defined INSTALLER (
    for /f "delims=" %%F in ('where /r "%WORK_DIR%" *x86*signed.exe 2^>nul') do (
        if not defined INSTALLER (
            if exist "%%~fF" set "INSTALLER=%%~fF"
        )
    )
)

rem Final fallback: find any Setup_com0com executable.
if not defined INSTALLER (
    for /f "delims=" %%F in ('where /r "%WORK_DIR%" Setup_com0com*.exe 2^>nul') do (
        if not defined INSTALLER (
            if exist "%%~fF" set "INSTALLER=%%~fF"
        )
    )
)

if not defined INSTALLER (
    echo ERROR: Could not locate the com0com installer.
    echo.
    echo Extracted files are in:
    echo   "%WORK_DIR%"
    echo.
    pause
    exit /b 1
)

echo Found installer:
echo   "%INSTALLER%"
echo.

rem ============================================================
rem Run installer
rem ============================================================

echo Starting the com0com installer...
echo.
echo Complete the setup wizard normally.
echo After it finishes, this script will continue automatically.
echo.

start "" /wait "%INSTALLER%"

set "INSTALL_RESULT=%ERRORLEVEL%"

if not "%INSTALL_RESULT%"=="0" (
    echo.
    echo WARNING: The installer returned code %INSTALL_RESULT%.
    echo The script will still check whether setupc.exe was installed.
    echo.
)

rem ============================================================
rem Locate setupc.exe after installation
rem ============================================================

call :find_setupc

if not defined SETUPC (
    echo.
    echo ============================================================
    echo ERROR: setupc.exe was not found after installation.
    echo ============================================================
    echo.
    echo Searching common locations for diagnostic purposes...
    echo.

    if defined ProgramFiles(x86) (
        where /r "%ProgramFiles(x86)%" setupc.exe 2>nul
    )

    where /r "%ProgramFiles%" setupc.exe 2>nul

    if defined ProgramW6432 (
        where /r "%ProgramW6432%" setupc.exe 2>nul
    )

    echo.
    echo com0com may not have installed correctly.
    echo.
    echo Check these folders manually:
    echo   C:\Program Files\com0com
    echo   C:\Program Files (x86)\com0com
    echo.
    pause
    exit /b 1
)

rem ============================================================
rem Configure COM10 / COM11
rem ============================================================

:configure

echo.
echo Using com0com command-line utility:
echo   "%SETUPC%"
echo.

rem Make absolutely sure it still exists.
if not exist "%SETUPC%" (
    echo ERROR: setupc.exe does not exist at:
    echo   "%SETUPC%"
    echo.
    pause
    exit /b 1
)

rem ============================================================
rem Display current configuration
rem ============================================================

echo Current com0com configuration:
echo ------------------------------------------------------------
"%SETUPC%" list
echo ------------------------------------------------------------
echo.

rem ============================================================
rem Check whether COM10 and COM11 already exist
rem ============================================================

call :pair_exists

if "%PAIR_EXISTS%"=="1" (
    echo COM10 and COM11 are already configured in com0com.
    goto :success
)

rem ============================================================
rem Create COM10 / COM11 pair
rem ============================================================

echo Creating virtual null-modem pair:
echo   COM10 ^<---^> COM11
echo.

"%SETUPC%" install PortName=COM10 PortName=COM11

if errorlevel 1 (
    echo.
    echo ============================================================
    echo ERROR: com0com could not create COM10 and COM11.
    echo ============================================================
    echo.
    echo COM10 or COM11 may already be assigned to another device.
    echo.
    echo Current com0com configuration:
    echo ------------------------------------------------------------
    "%SETUPC%" list
    echo ------------------------------------------------------------
    echo.
    echo You can also check Windows ports with:
    echo   mode
    echo.
    pause
    exit /b 1
)

rem ============================================================
rem Verify after creation
rem ============================================================

echo.
echo Verifying configuration...
echo.

call :pair_exists

if not "%PAIR_EXISTS%"=="1" (
    echo WARNING: The install command completed, but COM10 and COM11
    echo were not both detected in the com0com configuration.
    echo.
    echo Current configuration:
    "%SETUPC%" list
    echo.
    pause
    exit /b 1
)

rem ============================================================
rem Success
rem ============================================================

:success

echo.
echo ============================================================
echo SUCCESS
echo ============================================================
echo.
echo com0com is ready.
echo.
echo Virtual serial pair:
echo.
echo     COM10 ^<====================^> COM11
echo.
echo Data written to COM10 will appear on COM11 and vice versa.
echo.

echo Current com0com configuration:
echo ------------------------------------------------------------
"%SETUPC%" list
echo ------------------------------------------------------------
echo.

echo Start one copy of main.py and select COM10.
echo Start another copy of main.py and select COM11.
echo.
echo Use matching baud-rate / data-bit / parity / stop-bit settings
echo in both programs.
echo.

pause
exit /b 0


rem ============================================================
rem Find setupc.exe
rem
rem IMPORTANT:
rem Only set SETUPC when the file REALLY EXISTS.
rem ============================================================

:find_setupc

set "SETUPC="

rem ------------------------------------------------------------
rem Most common com0com installation location on 64-bit Windows
rem ------------------------------------------------------------

if defined ProgramFiles(x86) (
    if exist "%ProgramFiles(x86)%\com0com\setupc.exe" (
        set "SETUPC=%ProgramFiles(x86)%\com0com\setupc.exe"
        exit /b 0
    )
)

rem ------------------------------------------------------------
rem Standard Program Files location
rem ------------------------------------------------------------

if exist "%ProgramFiles%\com0com\setupc.exe" (
    set "SETUPC=%ProgramFiles%\com0com\setupc.exe"
    exit /b 0
)

rem ------------------------------------------------------------
rem Explicit 64-bit Program Files location
rem ------------------------------------------------------------

if defined ProgramW6432 (
    if exist "%ProgramW6432%\com0com\setupc.exe" (
        set "SETUPC=%ProgramW6432%\com0com\setupc.exe"
        exit /b 0
    )
)

rem ------------------------------------------------------------
rem Check PATH
rem ------------------------------------------------------------

for /f "delims=" %%F in ('where setupc.exe 2^>nul') do (
    if not defined SETUPC (
        if exist "%%~fF" (
            set "SETUPC=%%~fF"
        )
    )
)

if defined SETUPC exit /b 0

rem ------------------------------------------------------------
rem Search Program Files (x86)
rem
rem "where /r" only returns files that actually exist.
rem ------------------------------------------------------------

if defined ProgramFiles(x86) (
    for /f "delims=" %%F in ('where /r "%ProgramFiles(x86)%" setupc.exe 2^>nul') do (
        if not defined SETUPC (
            if exist "%%~fF" (
                set "SETUPC=%%~fF"
            )
        )
    )
)

if defined SETUPC exit /b 0

rem ------------------------------------------------------------
rem Search Program Files
rem ------------------------------------------------------------

for /f "delims=" %%F in ('where /r "%ProgramFiles%" setupc.exe 2^>nul') do (
    if not defined SETUPC (
        if exist "%%~fF" (
            set "SETUPC=%%~fF"
        )
    )
)

if defined SETUPC exit /b 0

rem ------------------------------------------------------------
rem Search ProgramW6432 if different
rem ------------------------------------------------------------

if defined ProgramW6432 (
    for /f "delims=" %%F in ('where /r "%ProgramW6432%" setupc.exe 2^>nul') do (
        if not defined SETUPC (
            if exist "%%~fF" (
                set "SETUPC=%%~fF"
            )
        )
    )
)

exit /b 0


rem ============================================================
rem Check whether both COM10 and COM11 exist in com0com
rem ============================================================

:pair_exists

set "PAIR_EXISTS=0"
set "HAS_COM10=0"
set "HAS_COM11=0"

for /f "usebackq delims=" %%L in (`"%SETUPC%" list 2^>nul`) do (
    echo(%%L | findstr /i /c:"PortName=COM10" >nul
    if not errorlevel 1 set "HAS_COM10=1"

    echo(%%L | findstr /i /c:"PortName=COM11" >nul
    if not errorlevel 1 set "HAS_COM11=1"
)

if "%HAS_COM10%"=="1" if "%HAS_COM11%"=="1" (
    set "PAIR_EXISTS=1"
)

exit /b 0


rem ============================================================
rem Download/extraction failure
rem ============================================================

:download_failed

echo.
echo ============================================================
echo ERROR: com0com download/extraction failed.
echo ============================================================
echo.
echo Check your Internet connection and try again.
echo.
echo Temporary directory:
echo   "%WORK_DIR%"
echo.
pause
exit /b 1
