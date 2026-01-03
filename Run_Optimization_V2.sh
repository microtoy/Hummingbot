#!/bin/bash
# ==============================================
#  🧠 Optuna Strategy Optimizer V2 Launcher
# ==============================================
# Uses Bayesian optimization (TPE) instead of random search
# 5x more efficient parameter discovery
# ==============================================

set -e
cd "$(dirname "$0")"

echo ""
echo "========================================================"
echo "   🧠 OPTUNA STRATEGY OPTIMIZER V2"
echo "========================================================"
echo "Bayesian optimization using TPE (Tree-structured Parzen Estimator)"
echo "5x more efficient than random search"
echo "========================================================"
echo ""

echo "📋 Select optimization mode:"
echo ""
echo "1. Single Pair (200 trials)"
echo "2. All Top 10 Tokens (100 trials each)"
echo "3. Quick Test (50 trials)"
echo ""
read -p "Select Option [1/2/3]: " OPTION

case $OPTION in
    1)
        echo ""
        echo "Enter trading pair (e.g., ADA-USDT):"
        read -p "Pair: " PAIR
        echo ""
        echo "🚀 Starting optimization for $PAIR..."
        docker exec -it hummingbot-api python /hummingbot-api/bots/controllers/custom/StrategyOptimizerV2.py \
            --pair "$PAIR" \
            --n_trials 200 \
            --days 360
        ;;
    2)
        echo ""
        echo "🚀 Starting optimization for ALL top 10 tokens..."
        echo "⏱️  Estimated time: ~2 hours"
        docker exec -it hummingbot-api python /hummingbot-api/bots/controllers/custom/StrategyOptimizerV2.py \
            --all \
            --n_trials 100 \
            --days 360
        ;;
    3)
        echo ""
        echo "Enter trading pair (e.g., ADA-USDT):"
        read -p "Pair: " PAIR
        echo ""
        echo "🚀 Quick test for $PAIR (50 trials)..."
        docker exec -it hummingbot-api python /hummingbot-api/bots/controllers/custom/StrategyOptimizerV2.py \
            --pair "$PAIR" \
            --n_trials 50 \
            --days 360
        ;;
    *)
        echo "❌ Invalid option"
        exit 1
        ;;
esac

echo ""
echo "✅ Optimization complete!"
echo "📝 Check reports in: custom_strategies/optimization_reports_v2/"
echo ""
