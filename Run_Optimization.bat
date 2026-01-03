@echo off
setlocal enabledelayedexpansion

:: Check if running in deploy directory
if not exist "docker-compose.yml" (
    echo [31mError: Please run this script from the 'deploy' directory where docker-compose.yml is located.[0m
    exit /b 1
)

:: Auto-start dashboard if not running
docker ps -q -f name=dashboard > nul
if errorlevel 1 (
    echo [33mDashboard container is not running. Starting it...[0m
    docker compose start dashboard
    timeout /t 3 > nul
)

echo ========================================================
echo    🚀 Hummingbot Strategy Optimizer (Maestro)
echo ========================================================
echo 1. 🔍 Discovery Mode (Find New Strategies)
echo    - Randomly explores large parameter space
echo    - Best for finding new opportunities
echo.
echo 2. 💎 Refinement Mode (Optimize Existing)
echo    - Fine-tunes around known good parameters
echo    - Best for improving stability/yield
echo ========================================================
set /p option="Select Option [1/2]: "

if "%option%"=="1" (
    set MODE=discovery
    set /p DAYS="Enter number of days check (default 90): "
    if "!DAYS!"=="" set DAYS=90
    set /p ITER="Enter iterations per coin (default 20): "
    if "!ITER!"=="" set ITER=20
    set TOKENS=ALL
) else if "%option%"=="2" (
    set MODE=refine
    set /p DAYS="Enter number of days check (default 180): "
    if "!DAYS!"=="" set DAYS=180
    set /p ITER="Enter iterations per coin (default 50): "
    if "!ITER!"=="" set ITER=50
    set /p TOKENS="Enter target tokens (e.g. ETH-USDT,BTC-USDT or ALL): "
    if "!TOKENS!"=="" set TOKENS=ALL
) else (
    echo Invalid option
    exit /b 1
)

echo.
set /p use_turbo="🚀 Use Turbo Mode for 10x speed? (Requires 48+ cores) [y/N]: "
if /i "%use_turbo%"=="y" (
    set TURBO_FLAG=--turbo
    echo ⚡ Turbo Mode ENABLED
) else (
    set TURBO_FLAG=
    echo Legacy Mode enabled
)

echo.
echo 🚀 Starting detailed analysis...
echo --------------------------------------------------------

:: Execute inside Docker
:: In Windows, we use %CD% for current directory
docker exec -e HOST_PATH="%CD%" -t dashboard /opt/conda/envs/dashboard/bin/python3 ^
    /home/dashboard/custom_strategies/StrategyOptimizer.py ^
    --mode !MODE! ^
    --days !DAYS! ^
    --iter !ITER! ^
    --tokens !TOKENS! ^
    !TURBO_FLAG!

echo.
echo ✅ Done! Reports saved in: custom_strategies\optimization_reports\
pause
