# Check if running in deploy directory
if (!(Test-Path "docker-compose.yml")) {
    Write-Host "❌ Error: Please run this script from the 'deploy' directory where docker-compose.yml is located." -ForegroundColor Red
    exit 1
}

# Auto-start dashboard if not running
if (!(docker ps -q -f name=dashboard)) {
    Write-Host "⚠️  Dashboard container is not running. Starting it..." -ForegroundColor Yellow
    docker compose start dashboard
    Start-Sleep -Seconds 3
}

Write-Host "========================================================"
Write-Host "   🚀 Hummingbot Strategy Optimizer (Maestro)"
Write-Host "========================================================"
Write-Host "1. 🔍 Discovery Mode (Find New Strategies)"
Write-Host "   - Randomly explores large parameter space"
Write-Host "   - Best for finding new opportunities"
Write-Host ""
Write-Host "2. 💎 Refinement Mode (Optimize Existing)"
Write-Host "   - Fine-tunes around known good parameters"
Write-Host "   - Best for improving stability/yield"
Write-Host "========================================================"

$option = Read-Host "Select Option [1/2]"

if ($option -eq "1") {
    $MODE = "discovery"
    $DAYS = Read-Host "Enter number of days check (default 360)"
    if ([string]::IsNullOrWhiteSpace($DAYS)) { $DAYS = "360" }
    $ITER = Read-Host "Enter iterations per coin (default 20)"
    if ([string]::IsNullOrWhiteSpace($ITER)) { $ITER = "20" }
    $TOKENS = "ALL"
} elseif ($option -eq "2") {
    $MODE = "refine"
    $DAYS = Read-Host "Enter number of days check (default 180)"
    if ([string]::IsNullOrWhiteSpace($DAYS)) { $DAYS = "180" }
    $ITER = Read-Host "Enter iterations per coin (default 50)"
    if ([string]::IsNullOrWhiteSpace($ITER)) { $ITER = "50" }
    $TOKENS = Read-Host "Enter target tokens (e.g. ETH-USDT,BTC-USDT or ALL)"
    if ([string]::IsNullOrWhiteSpace($TOKENS)) { $TOKENS = "ALL" }
} else {
    Write-Host "Invalid option"
    exit 1
}

# Turbo Mode is enabled by default for maximum performance
$TURBO_FLAG = "--turbo"
Write-Host "⚡ Turbo Mode ENABLED" -ForegroundColor Green

Write-Host ""
Write-Host "🚀 Starting detailed analysis..." -ForegroundColor Cyan
Write-Host "--------------------------------------------------------"

# Execute inside Docker
$HOST_PATH = (Get-Item .).FullName
docker exec -e HOST_PATH="$HOST_PATH" -t dashboard /opt/conda/envs/dashboard/bin/python3 `
    /home/dashboard/custom_strategies/StrategyOptimizer.py `
    --mode $MODE `
    --days $DAYS `
    --iter $ITER `
    --tokens $TOKENS `
    $TURBO_FLAG

Write-Host ""
Write-Host "✅ Done! Reports saved in: custom_strategies\optimization_reports\" -ForegroundColor Green
Read-Host "Press Enter to exit"
