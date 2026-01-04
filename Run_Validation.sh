#!/bin/bash
# ==============================================
#  🛡️ Strategy Validation Launcher v2.1
# ==============================================
# Validates strategies from the LATEST report:
#   report_discovery_20260103_1354.md
# Tests: OOS, Sensitivity, Walk-Forward, Monte Carlo
# ==============================================

set -e
cd "$(dirname "$0")"

echo ""
echo "========================================================"
echo "   🛡️ Strategy Validation Tool v2.1"
echo "========================================================"
echo "Validating strategies from report_discovery_20260103_1354.md"
echo "========================================================"
echo ""

# TOP STRATEGIES from latest discovery (100K sims, 360 days)
# Format: PAIR:FAST:SLOW:INTERVAL:SL:TP:ORIGINAL_PNL

# Holy Grails - Top 10 (PnL > 60%, DD < 20%)
HOLY_GRAILS=(
    "ADA-USDT:15:30:1h:0.02:0.15:79.34"
    "ADA-USDT:15:30:1h:0.02:0.02:77.08"
    "ADA-USDT:15:30:1h:0.02:0.05:72.15"
    "ADA-USDT:15:30:1h:0.1:0.15:65.17"
    "ADA-USDT:15:30:1h:0.04:0.02:64.46"
    "ADA-USDT:15:30:1h:0.1:0.2:63.56"
    "ADA-USDT:15:30:1h:0.04:0.2:60.58"
)

# Sweet Spot Clusters (Robust multi-count strategies)
SWEET_SPOTS=(
    "ADA-USDT:15:20:1h:0.03:0.10:56.69"
    "SOL-USDT:15:20:1h:0.03:0.10:42.19"
    "SOL-USDT:20:60:1h:0.04:0.15:40.84"
    "SOL-USDT:10:20:1h:0.03:0.10:38.71"
    "LINK-USDT:10:20:1h:0.03:0.10:34.21"
    "LINK-USDT:55:100:1h:0.05:0.15:32.62"
    "DOGE-USDT:10:140:1h:0.04:0.10:32.23"
)

echo "📋 Strategy counts:"
echo "   - Holy Grails: ${#HOLY_GRAILS[@]}"
echo "   - Sweet Spots: ${#SWEET_SPOTS[@]}"
echo ""

echo "Select which strategies to validate:"
echo ""
echo "1. Holy Grails only (7 top PnL strategies)"
echo "2. Sweet Spots only (7 robust cluster strategies)"
echo "3. ALL strategies (14 total)"
echo "4. Custom selection"
echo ""
read -p "Select Option [1/2/3/4]: " OPTION

case $OPTION in
    1)
        STRATEGIES=$(IFS=,; echo "${HOLY_GRAILS[*]}")
        echo "📌 Validating 7 Holy Grail strategies..."
        ;;
    2)
        STRATEGIES=$(IFS=,; echo "${SWEET_SPOTS[*]}")
        echo "📌 Validating 7 Sweet Spot strategies..."
        ;;
    3)
        ALL=("${HOLY_GRAILS[@]}" "${SWEET_SPOTS[@]}")
        STRATEGIES=$(IFS=,; echo "${ALL[*]}")
        echo "📌 Validating ALL 14 strategies..."
        ;;
    4)
        echo ""
        echo "Enter strategies in format: PAIR:FAST:SLOW:INTERVAL:SL:TP:ORIGINAL_PNL"
        echo "Separate multiple with commas"
        read -p "Strategies: " STRATEGIES
        ;;
    *)
        STRATEGIES=$(IFS=,; echo "${HOLY_GRAILS[*]}")
        echo "📌 Default: Validating 7 Holy Grail strategies..."
        ;;
esac

echo ""
echo "🚀 Starting validation..."
echo "⏱️  Estimated time: 5-15 minutes (Turbo Mode enabled)"
echo ""

# Run validator inside Dashboard container (where Python + packages exist)
# Use HOST_PATH for clickable report links
docker exec -e HOST_PATH="$(pwd)" -t dashboard \
    /opt/conda/envs/dashboard/bin/python3 \
    /home/dashboard/custom_strategies/StrategyValidator.py \
    --strategies "$STRATEGIES" \
    --days 360

echo ""
echo "✅ Validation complete!"
echo "📝 Check the report in: custom_strategies/validation_reports/"
echo ""
