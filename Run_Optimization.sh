#!/bin/bash

# Check if running in deploy directory
if [ ! -f "docker-compose.yml" ]; then
    echo "❌ Error: Please run this script from the 'deploy' directory where docker-compose.yml is located."
    exit 1
fi

# Auto-start dashboard if not running
if [ ! "$(docker ps -q -f name=dashboard)" ]; then
    echo "⚠️  Dashboard container is not running. Starting it..."
    docker compose start dashboard
    sleep 3 # Wait for startup
fi

echo "========================================================"
echo "   🚀 Hummingbot Strategy Optimizer (Maestro)"
echo "========================================================"
echo "1. 🔍 Discovery Mode (Find New Strategies)"
echo "   - Randomly explores large parameter space"
echo "   - Best for finding new opportunities"
echo ""
echo "2. 💎 Refinement Mode (Optimize Existing)"
echo "   - Fine-tunes around known good parameters"
echo "   - Best for improving stability/yield"
echo "========================================================"
read -p "Select Option [1/2]: " option

case $option in
    1)
        MODE="discovery"
        read -p "Enter number of days check (default 90): " DAYS
        DAYS=${DAYS:-90}
        read -p "Enter iterations per coin (default 20): " ITER
        ITER=${ITER:-20}
        TOKENS="ALL"
        ;;
    2)
        MODE="refine"
        read -p "Enter number of days check (default 180): " DAYS
        DAYS=${DAYS:-180}
        read -p "Enter iterations per coin (default 50): " ITER
        ITER=${ITER:-50}
        read -p "Enter target tokens (e.g. ETH-USDT,BTC-USDT or ALL): " TOKENS
        TOKENS=${TOKENS:-ALL}
        ;;
    *)
        echo "Invalid option"
        exit 1
        ;;
esac

echo ""
echo "🚀 Starting detailed analysis..."
echo "--------------------------------------------------------"

# Execute inside Docker
docker exec -t dashboard /opt/conda/envs/dashboard/bin/python3 \
    /home/dashboard/custom_strategies/StrategyOptimizer.py \
    --mode $MODE \
    --days $DAYS \
    --iter $ITER \
    --tokens $TOKENS

echo ""
echo "✅ Done! Reports saved in: custom_strategies/optimization_reports/"
