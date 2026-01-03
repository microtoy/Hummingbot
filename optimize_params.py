
import requests
import json
import time
from datetime import datetime, timedelta
import pandas as pd

API_URL = "http://localhost:8000/backtesting/batch-run"

# Target Tokens
TOP_10_TOKENS = [
    "BTC-USDT", "ETH-USDT", "BNB-USDT", "XRP-USDT", "SOL-USDT",
    "ADA-USDT", "AVAX-USDT", "DOGE-USDT", "LINK-USDT", "DOT-USDT"
]

# Time Range: Last 3 Months
end_dt = datetime.now() - timedelta(days=1)
start_dt = end_dt - timedelta(days=90)

START_TS = int(start_dt.timestamp())
END_TS = int(end_dt.timestamp())

# Optimization Phases
PHASES = {
    "Scalping": {
        "interval": "15m",
        "fast_ma": 5,
        "slow_ma": 20,
        "sl": 0.01,
        "tp": 0.02
    },
    "Momentum": {
        "interval": "1h",
        "fast_ma": 10,
        "slow_ma": 30,
        "sl": 0.02,
        "tp": 0.05
    },
    "Trend Following": {
        "interval": "4h",
        "fast_ma": 20,
        "slow_ma": 50,
        "sl": 0.035,
        "tp": 0.10
    }
}

def run_phase(phase_name, settings):
    print(f"\n🚀 Running Phase: {phase_name} ({settings['interval']})...")
    
    batch_configs = []
    for pair in TOP_10_TOKENS:
        config = {
            "connector_name": "binance",
            "trading_pair": pair,
            "fast_ma": settings["fast_ma"],
            "slow_ma": settings["slow_ma"],
            "indicator_interval": settings["interval"],
            "stop_loss": settings["sl"],
            "take_profit": settings["tp"],
            "time_limit": 21600,
            "total_amount_quote": 100,
            "controller_name": "ma_cross_strategy"
        }
        batch_configs.append({
            "config": config,
            "start_time": START_TS,
            "end_time": END_TS,
            "backtesting_resolution": settings["interval"],
            "trade_cost": 0.0006
        })

    try:
        response = requests.post(API_URL, json=batch_configs)
        response.raise_for_status()
        results = response.json().get("results", [])
        return results
    except Exception as e:
        print(f"❌ Error in phase {phase_name}: {e}")
        return []

def main():
    all_results = []
    
    for phase_name, settings in PHASES.items():
        results = run_phase(phase_name, settings)
        for res in results:
            if "error" in res:
                print(f"  ⚠️ {res['trading_pair']}: {res['error']}")
                continue
            
            all_results.append({
                "Phase": phase_name,
                "Pair": res["trading_pair"],
                "Net PnL %": res["net_pnl"] * 100,
                "PnL ($)": res["net_pnl_quote"],
                "Win Rate %": res["accuracy"] * 100,
                "Max DD %": res["max_drawdown_pct"] * 100,
                "Sharpe": res["sharpe_ratio"],
                "Positions": res["total_positions"]
            })
        
        # Avoid overwhelming the API
        time.sleep(2)

    if not all_results:
        print("❌ No results collected.")
        return

    df = pd.DataFrame(all_results)
    
    # Save results
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"optimization_results_{timestamp}.csv"
    df.to_csv(filename, index=False)
    print(f"\n✅ Optimization complete! Results saved to {filename}")
    
    # Summary Analysis
    print("\n--- Top 5 Performers by Net PnL % ---")
    print(df.sort_values("Net PnL %", ascending=False).head(5).to_string(index=False))
    
    print("\n--- Best Phase by Avg PnL % ---")
    print(df.groupby("Phase")["Net PnL %"].mean().sort_values(ascending=False).to_string())

if __name__ == "__main__":
    main()
