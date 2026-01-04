#!/bin/bash
# 🔬 Robust Strategy Optimizer Launch Script
# Tests parameters across 4 years (2021-2024) to find truly robust strategies

cd "$(dirname "$0")"

# Default: Test with small sample first  
ITER=${1:-20}
TOKENS=${2:-"LINK-USDT"}

echo "=========================================="
echo "🔬 ROBUST STRATEGY OPTIMIZER"
echo "=========================================="
echo "Iterations per token: $ITER"
echo "Tokens: $TOKENS"
echo "Years: 2021, 2022, 2023, 2024"
echo "=========================================="

# Run inside Docker container
docker exec \
    -e BACKEND_API_HOST=hummingbot-api \
    -t dashboard \
    /opt/conda/envs/dashboard/bin/python3 \
    /home/dashboard/custom_strategies/RobustStrategyOptimizer.py \
    --iter $ITER \
    --tokens "$TOKENS" \
    --years "2021,2022,2023,2024" \
    --turbo

echo ""
echo "✅ Optimization complete!"
echo "View reports in: custom_strategies/optimization_reports/"
