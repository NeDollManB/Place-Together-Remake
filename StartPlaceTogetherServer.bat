@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul 2>&1
set "PYTHONIOENCODING=utf-8"
title PlaceTogether 3.0 Server Launcher

rem ============================================================
rem  PlaceTogether 3.0 - Universal Server Launcher
rem  - Uses Python + server.py if available, or falls back to PlaceTogetherServer.exe
rem  - Automatic firewall rule setup (when run as Admin)
rem  - Works with Radmin VPN, Hamachi, ZeroTier, LAN, ngrok, Porthole
rem ============================================================

set "PORT=8787"
set "SRV=%~dp0server.py"
set "EXE=%~dp0PlaceTogetherServer.exe"
set "DATA=%~dp0data.json"

rem --- Find Python: PATH, py launcher, portable folder --------
set "PY_CMD="

python --version >nul 2>&1
if not errorlevel 1 set "PY_CMD=python"

if not defined PY_CMD (
    py -3 --version >nul 2>&1
    if not errorlevel 1 set "PY_CMD=py -3"
)

if not defined PY_CMD if exist "%~dp0python_embed\python.exe" (
    set "PY_CMD=%~dp0python_embed\python.exe"
)

rem --- Check if we have either Python+server.py or PlaceTogetherServer.exe ---
set "RUN_MODE="
if defined PY_CMD if exist "%SRV%" (
    set "RUN_MODE=PYTHON"
)
if not defined RUN_MODE if exist "%EXE%" (
    set "RUN_MODE=EXE"
)

if not defined RUN_MODE (
    echo.
    echo   ====================================================
    echo    [ERROR] Neither Python+server.py nor PlaceTogetherServer.exe found!
    echo   ====================================================
    echo.
    echo   Place server.py or PlaceTogetherServer.exe in:
    echo     %~dp0
    echo.
    pause
    exit /b 1
)

rem --- Detect network IPs (errors suppressed) -----------------
set "RADMIN_IP="
set "HAMACHI_IP="
set "ZEROTIER_IP="
set "LAN_IP="

for /f "usebackq tokens=* delims=" %%a in (`powershell -NoProfile -Command "try { (Get-NetIPAddress -InterfaceAlias '*Radmin*' -AddressFamily IPv4 -ErrorAction Stop | Select-Object -First 1).IPAddress } catch { '' }" 2^>nul`) do set "RADMIN_IP=%%a"

for /f "usebackq tokens=* delims=" %%a in (`powershell -NoProfile -Command "try { (Get-NetIPAddress -InterfaceAlias '*Hamachi*' -AddressFamily IPv4 -ErrorAction Stop | Select-Object -First 1).IPAddress } catch { '' }" 2^>nul`) do set "HAMACHI_IP=%%a"

for /f "usebackq tokens=* delims=" %%a in (`powershell -NoProfile -Command "try { (Get-NetIPAddress -InterfaceAlias '*ZeroTier*' -AddressFamily IPv4 -ErrorAction Stop | Select-Object -First 1).IPAddress } catch { '' }" 2^>nul`) do set "ZEROTIER_IP=%%a"

for /f "usebackq tokens=* delims=" %%a in (`powershell -NoProfile -Command "try { (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction Stop | Where-Object { $_.InterfaceAlias -match 'Ethernet|Wi-Fi|Беспроводная|Сеть' -and $_.IPAddress -notmatch '^127\.' -and $_.IPAddress -notmatch '^169\.254\.' } | Select-Object -First 1).IPAddress } catch { '' }" 2^>nul`) do set "LAN_IP=%%a"

rem --- Try to open firewall port (optional, needs admin) ------
set "FWOK=0"
powershell -NoProfile -Command "try { if (-not (Get-NetFirewallRule -DisplayName 'PlaceTogether %PORT%' -ErrorAction SilentlyContinue)) { New-NetFirewallRule -DisplayName 'PlaceTogether %PORT%' -Direction Inbound -Action Allow -Protocol TCP -LocalPort %PORT% -Profile Any -ErrorAction Stop | Out-Null }; exit 0 } catch { exit 1 }" >nul 2>&1 && set "FWOK=1"

echo.
echo   ============================================================
echo     PlaceTogether 3.0 Server - Ultra-Fast Co-op Engine
echo   ============================================================
echo.
if "%RUN_MODE%"=="PYTHON" (
    echo   Runtime      : !PY_CMD! ^("%SRV%"^)
) else (
    echo   Runtime      : Standalone EXE ^("%EXE%"^)
)
echo   Port         : %PORT%
echo   Data Storage : %DATA%
echo   Backups Dir  : %~dp0backups
echo.

if defined RADMIN_IP   echo   [Radmin VPN]  : http://!RADMIN_IP!:%PORT%
if defined HAMACHI_IP  echo   [Hamachi]     : http://!HAMACHI_IP!:%PORT%
if defined ZEROTIER_IP echo   [ZeroTier]    : http://!ZEROTIER_IP!:%PORT%
if defined LAN_IP      echo   [LAN / Wi-Fi] : http://!LAN_IP!:%PORT%
echo   [Localhost]   : http://127.0.0.1:%PORT%
echo.
echo   ------------------------------------------------------------
echo   WHAT TO PUT INTO THE ROBLOX STUDIO PLUGIN (Settings Tab):
echo   - Radmin VPN users  : copy the [Radmin VPN] address
echo   - Hamachi users     : copy the [Hamachi] address
echo   - ngrok / Porthole  : paste the public tunnel address
echo   - Same PC / Host    : http://127.0.0.1:%PORT%
echo   ------------------------------------------------------------
echo.

if "%FWOK%"=="0" (
    echo   [i] Firewall rule was NOT created automatically.
    echo       If friends cannot connect, run this .bat as Administrator once.
    echo.
)

echo   Server is running. Auto-save & backups enabled. Press Ctrl+C to stop.
echo.

if "%RUN_MODE%"=="PYTHON" (
    !PY_CMD! "%SRV%" --port %PORT% --data "%DATA%"
) else (
    "%EXE%" --port %PORT% --data "%DATA%"
)
set "EXITCODE=%errorlevel%"

echo.
echo   ============================================================
echo     Server stopped. Exit code: %EXITCODE%
echo   ============================================================
echo.
if not "%EXITCODE%"=="0" (
    echo   The server exited with an error.
    echo   Scroll up to read the traceback above.
    echo.
)
pause
endlocal
