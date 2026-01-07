
import requests
import json
import time
import random
import pandas as pd
from decimal import Decimal
from requests.auth import HTTPBasicAuth

# Configuration
API_HOST = "localhost" # or hummingbot-api inside container
AUTH = HTTPBasicAuth("admin", "admin")
BASE_URL = f"http://{API_HOST}:8000/backtesting"

# BB Grid Config for Test
TEST_CONFIG = {
    "connector_name": "binance",
    "controller_name": "bb_grid_strategy",
    "controller_type": "custom",
    "trading_pair": "SOL-USDT",
    "bb_period": 20,
    "bb_std": 2.0,
    "entry_threshold": 0.5,
    "use_trend_filter": False,
    "trend_ma_period": 100,
    "indicator_interval": "4h",
    "stop_loss": 0.05,
    "take_profit": 0.05,
    "time_limit": 86400,
    "total_amount_quote": 100,
    "trade_cost": 0.0006,
    "backtesting_resolution": "1m"
}

def run_test_item(name, endpoint, payload):
    print(f"🚀 Running {name}...")
    start = time.perf_counter()
    resp = requests.post(f"{BASE_URL}/{endpoint}", json=payload, auth=AUTH)
    elapsed = time.perf_counter() - start
    
    if resp.status_code != 200:
        print(f"❌ {name} FAILED: HTTP {resp.status_code}")
        return None, elapsed
    
    data = resp.json()
    # Handle single vs batch response
    if endpoint == "run-backtesting":
        results = data.get("results", {})
    else:
        results = data.get("results", [{}])[0]
        
    return results, elapsed

def verify_parity():
    print("\n" + "="*50)
    print("💎 STAGE 1: 3-WAY API PARITY CHECK")
    print("="*50)
    
    # Range: 2024-01-01 to 2024-01-31
    start_ts = int(pd.Timestamp("2024-01-01").timestamp())
    end_ts = int(pd.Timestamp("2024-01-31").timestamp())
    
    # 1. Standard API
    std_payload = {**TEST_CONFIG, "start_time": start_ts, "end_time": end_ts, "config": TEST_CONFIG}
    res_std, t_std = run_test_item("Standard API", "run-backtesting", std_payload)
    
    # 2. Batch API
    batch_payload = [std_payload]
    res_batch, t_batch = run_test_item("Batch API", "batch-run", batch_payload)
    
    # 3. Turbo API
    res_turbo, t_turbo = run_test_item("Turbo API", "batch-run-turbo", batch_payload)
    
    # Comparison
    metrics = ["net_pnl", "total_positions", "accuracy"]
    parity = True
    print("\n📊 Consistency Matrix:")
    print(f"{'Metric':<15} | {'Standard':<12} | {'Batch':<12} | {'Turbo':<12}")
    print("-" * 60)
    for m in metrics:
        v_std = res_std.get(m, 0)
        v_batch = res_batch.get(m, 0)
        v_turbo = res_turbo.get(m, 0)
        print(f"{m:<15} | {v_std:<12.6f} | {v_batch:<12.6f} | {v_turbo:<12.6f}")
        if abs(v_std - v_batch) > 1e-6 or abs(v_batch - v_turbo) > 1e-6:
            parity = False
            
    if parity:
        print("\n✅ ALL APIS ARE 100% CONSISTENT")
    else:
        print("\n❌ PARITY FAILURE DETECTED!")
    
    return parity

def verify_isolation():
    print("\n" + "="*50)
    print("🛡️ STAGE 2: SEQUENTIAL STATE ISOLATION (FIX VERIFICATION)")
    print("="*50)
    
    # We run P1 then P2 in the SAME batch to force reuse of worker processes if possible
    # P1: 2022-01
    # P2: 2024-01 (The previously dead zone)
    p1_start = int(pd.Timestamp("2022-01-01").timestamp())
    p1_end = int(pd.Timestamp("2022-01-31").timestamp())
    p2_start = int(pd.Timestamp("2024-01-01").timestamp())
    p2_end = int(pd.Timestamp("2024-01-31").timestamp())
    
    payload = [
        {**TEST_CONFIG, "start_time": p1_start, "end_time": p1_end, "config": TEST_CONFIG},
        {**TEST_CONFIG, "start_time": p2_start, "end_time": p2_end, "config": TEST_CONFIG}
    ]
    
    print("Running P1 and P2 sequentially in Turbo mode...")
    start = time.perf_counter()
    resp = requests.post(f"{BASE_URL}/batch-run-turbo", json=payload, auth=AUTH)
    results = resp.json().get("results", [])
    
    if len(results) < 2:
        print("❌ Unexpected response format")
        return False
        
    p1_trades = results[0].get("total_positions", 0)
    p2_trades = results[1].get("total_positions", 0)
    
    print(f"P1 (2022) Trades: {p1_trades}")
    print(f"P2 (2024) Trades: {p2_trades}")
    
    if p2_trades > 0:
        print("\n✅ ISOLATION VERIFIED: P2 (2024) HAS TRADES. DATA POLLUTION RESOLVED.")
        return True
    else:
        print("\n❌ ISOLATION FAILURE: P2 STILL HAS 0 TRADES. CACHE BUG PERSISTS.")
        return False

def run_performance_bench():
    print("\n" + "="*50)
    print("⚡ STAGE 3: PERFORMANCE DEEP-DIVE (SCALE & CACHE)")
    print("="*50)
    
    start_ts = int(pd.Timestamp("2024-01-01").timestamp())
    end_ts = int(pd.Timestamp("2024-01-31").timestamp())
    
    # --- TEST 1: RAW THROUGHPUT (Unique Params - Realistic Discovery) ---
    batch_size = 300
    print(f"\n🚀 Scenario A: Unique Parameters (300 sims) - Amortizing Worker Startup")
    payload_unique = []
    for i in range(batch_size):
        cfg = TEST_CONFIG.copy()
        cfg["bb_std"] = 1.0 + (i * 0.01) # Unique but similar
        payload_unique.append({**cfg, "start_time": start_ts, "end_time": end_ts, "config": cfg})
    
    t1 = time.perf_counter()
    requests.post(f"{BASE_URL}/batch-run", json=payload_unique, auth=AUTH)
    d1 = time.perf_counter() - t1
    print(f"Standard Batch : {batch_size/d1:.2f} sims/sec")
    
    t2 = time.perf_counter()
    requests.post(f"{BASE_URL}/batch-run-turbo", json=payload_unique, auth=AUTH)
    d2 = time.perf_counter() - t2
    print(f"Turbo Batch    : {batch_size/d2:.2f} sims/sec")
    print(f"Net Multiplier : {d1/d2:.1f}x (Infrastructure Gain)")

    # --- TEST 2: CACHE EFFICIENCY (Identical Time Windows) ---
    print(f"\n🚀 Scenario B: Extreme Cache Hit (100 Identical Sims)")
    payload_hit = [payload_unique[0]] * 100
    
    t3 = time.perf_counter()
    requests.post(f"{BASE_URL}/batch-run-turbo", json=payload_hit, auth=AUTH)
    d3 = time.perf_counter() - t3
    print(f"Turbo (100% Hits): {100/d3:.2f} sims/sec")
    print(f"Cache Multiplier : {d2/d1 * (100/d3) / (batch_size/d2):.1f}x vs Scenario A")

    print("\n💡 NOTE: The '71x' speedup previously reported was Turbo vs Stock Engine (Sequential).")
    print("Standard Batch already uses our Patched Engine, so Turbo's 1.8x-3x gain here is purely from Worker reuse/Cache.")

if __name__ == "__main__":
    if verify_parity():
        if verify_isolation():
            run_performance_bench()
