#!/bin/bash
# ==============================================
#  🛡️ Strategy Validation Launcher v2.0
# ==============================================
# Validates ALL discovered strategies from report
# against overfitting using three tests:
# 1. Out-of-Sample Test (2021-2022 vs 2023-2024)
# 2. Parameter Sensitivity Analysis
# 3. Walk-Forward Analysis (6-year, 12-fold)
# ==============================================

set -e
cd "$(dirname "$0")"

echo ""
echo "========================================================"
echo "   🛡️ Strategy Validation Tool v2.0"
echo "========================================================"
echo "Validating ALL strategies from report_discovery_20260102_1154.md"
echo "========================================================"
echo ""

# ALL strategies from the latest discovery report
# Format: PAIR:FAST:SLOW:INTERVAL:SL:TP:ORIGINAL_PNL

# Holy Grails (11 strategies)
HOLY_GRAILS=(
    "ADA-USDT:55:70:1h:0.04:0.1:71.85"
    "XRP-USDT:50:70:1h:0.04:0.2:71.04"
    "XRP-USDT:45:70:1h:0.05:0.08:70.56"
    "AVAX-USDT:55:70:1h:0.02:0.2:68.83"
    "AVAX-USDT:45:60:1h:0.02:0.15:66.94"
    "LINK-USDT:5:160:1h:0.04:0.2:63.88"
    "ADA-USDT:50:70:1h:0.02:0.08:61.56"
    "XRP-USDT:45:70:1h:0.02:0.2:58.90"
    "LINK-USDT:5:160:1h:0.1:0.2:57.89"
    "AVAX-USDT:55:110:1h:0.05:0.05:57.72"
    "ADA-USDT:45:70:1h:0.07:0.15:70.35"
)

# Low Risk Gems (10 strategies)
LOW_RISK=(
    "ADA-USDT:35:180:4h:0.1:0.15:18.74"
    "ADA-USDT:35:180:4h:0.1:0.1:18.74"
    "DOGE-USDT:10:120:4h:0.05:0.15:14.83"
    "BNB-USDT:40:140:4h:0.01:0.02:15.03"
    "AVAX-USDT:20:190:4h:0.1:0.1:12.02"
    "ADA-USDT:40:120:4h:0.1:0.02:26.41"
    "DOGE-USDT:40:60:4h:0.07:0.2:31.70"
    "ADA-USDT:40:120:4h:0.07:0.15:20.79"
    "ADA-USDT:40:120:4h:0.1:0.2:20.79"
    "DOGE-USDT:20:130:4h:0.01:0.1:10.24"
)

echo "📋 Strategy counts:"
echo "   - Holy Grails: ${#HOLY_GRAILS[@]}"
echo "   - Low Risk Gems: ${#LOW_RISK[@]}"
echo ""

echo "Select which strategies to validate:"
echo ""
echo "1. All Holy Grails (11 high PnL strategies)"
echo "2. All Low Risk Gems (10 stable strategies)"
echo "3. ALL strategies (21 total)"
echo "4. Custom selection"
echo ""
read -p "Select Option [1/2/3/4]: " OPTION

case $OPTION in
    1)
        STRATEGIES=$(IFS=,; echo "${HOLY_GRAILS[*]}")
        echo "📌 Validating 11 Holy Grail strategies..."
        ;;
    2)
        STRATEGIES=$(IFS=,; echo "${LOW_RISK[*]}")
        echo "📌 Validating 10 Low Risk Gem strategies..."
        ;;
    3)
        ALL=("${HOLY_GRAILS[@]}" "${LOW_RISK[@]}")
        STRATEGIES=$(IFS=,; echo "${ALL[*]}")
        echo "📌 Validating ALL 21 strategies..."
        ;;
    4)
        echo ""
        echo "Enter strategies in format: PAIR:FAST:SLOW:INTERVAL:SL:TP:ORIGINAL_PNL"
        echo "Separate multiple with commas"
        read -p "Strategies: " STRATEGIES
        ;;
    *)
        STRATEGIES=$(IFS=,; echo "${HOLY_GRAILS[*]}")
        echo "📌 Default: Validating 11 Holy Grail strategies..."
        ;;
esac

echo ""
echo "🚀 Starting validation..."
echo "⏱️  This may take 30-60 minutes for large sets."
echo "   Status will be updated as each strategy completes."
echo ""

# Run validator inside Docker container
docker exec -it hummingbot-api python /hummingbot-api/bots/controllers/custom/StrategyValidator.py \
    --strategies "$STRATEGIES" \
    --days 730

echo ""
echo "✅ Validation complete!"
echo "📝 Check the report in: custom_strategies/validation_reports/"
echo ""
