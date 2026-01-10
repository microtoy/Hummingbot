#!/bin/bash

# ==========================================
# 🏛️ INSTITUTIONAL-GRADE AWFO DISCOVERY (3H)
# ==========================================
# Method: Anchored Walk-Forward Optimization
# Mode:   AWFO (Rolling IS/OOS)
# Goal:   Discover strategies that generalize!
# ==========================================

# 1. Configuration (Flexible via Arguments)
# Usage: ./Run_RobustOptimization.sh [HOURS] [TOKENS] [MODE]
# Example: ./Run_RobustOptimization.sh 3 "BTC-USDT,ETH-USDT" awfo

HOURS=${1:-3}
TOKENS=${2:-"ALL"}
MODE=${3:-"awfo"}
STRATEGY=${4:-"ma_cross"}

# Expansion and counting (Handles both Comma and Space)
if [ "$TOKENS" == "ALL" ]; then
    TOKENS="ADA-USDT,AVAX-USDT,DOGE-USDT,LINK-USDT,SOL-USDT,TRX-USDT,XRP-USDT"
fi

# Count tokens correctly (replaces comma with space, then counts words)
NUM_TOKENS=$(echo $TOKENS | tr ',' ' ' | wc -w | xargs)

# 2. Performance Calibration
WORKERS=6
BATCH_SIZE=50

# Calculate Iterations based on target duration (using bc for float support)
TOTAL_SECONDS=$(echo "$HOURS * 3600" | bc)

if [ "$MODE" == "awfo" ]; then
    SEC_PER_CAND=1
else
    SEC_PER_CAND=0.26
fi

# Iterations per token = (Total Sec / Sec per Cand) / Num Tokens
ITERATIONS=$(echo "$TOTAL_SECONDS / $SEC_PER_CAND / $NUM_TOKENS" | bc)

echo "=========================================="
echo "🔬 FLEXIBLE STRATEGY OPTIMIZATION"
echo "=========================================="
echo "Start Time:  $(date)"
echo "Target:      $HOURS Hours"
echo "Mode:        $MODE"
echo "Strategy:    $STRATEGY"
echo "Tokens:      $TOKENS ($NUM_TOKENS total)"
echo "Scaling:     ~$ITERATIONS iterations/token"
echo "=========================================="

# 3. Execution (Using Docker Backend)
# We use docker exec to run inside the existing environment
docker exec -e BACKEND_API_HOST=hummingbot-api -t dashboard /opt/conda/envs/dashboard/bin/python3 /home/dashboard/custom_strategies/RobustStrategyOptimizer.py \
    --mode "$MODE" \
    --iter "$ITERATIONS" \
    --tokens "$TOKENS" \
    --batch_size $BATCH_SIZE \
    --workers $WORKERS \
    --strategy "$STRATEGY"

echo "=========================================="
echo "✅ AWFO Discovery Complete!"
echo "End Time: $(date)"
echo "=========================================="
