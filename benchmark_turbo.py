import os
import time
import subprocess
import json
# import pandas as pd  # Removed dependency

# Benchmark Configuration
TEST_CASES = [
    {"workers": 1, "batch_size": 10},
    {"workers": 1, "batch_size": 100},
    {"workers": 2, "batch_size": 50},
    {"workers": 2, "batch_size": 100},
    {"workers": 2, "batch_size": 250},
    {"workers": 4, "batch_size": 100},
]

TOKENS = "BTC-USDT"
DAYS = 30
MODE = "discovery"

results = []

print(f"========================================================")
print(f"⚡ TURBO PERFORMANCE BENCHMARK (10-CORE)")
print(f"========================================================")

for case in TEST_CASES:
    w = case["workers"]
    bs = case["batch_size"]
    total_sims = bs * w
    
    print(f"\n▶ Testing: Workers={w}, BatchSize={bs} (Total {total_sims} sims)...")
    
    start_time = time.time()
    
    # Run a single batch test directly via StrategyOptimizer
    # We use a smaller 'iter' to make it faster but still measurable
    cmd = [
        "docker", "exec", "-t", "dashboard", 
        "/opt/conda/envs/dashboard/bin/python3",
        "/home/dashboard/custom_strategies/StrategyOptimizer.py",
        "--mode", MODE,
        "--days", str(DAYS),
        "--tokens", TOKENS,
        "--iter", str(total_sims),
        "--batch_size", str(bs),
        "--workers", str(w),
        "--turbo"
    ]
    
    # Force the internal StrategyOptimizer to use our test workers/batch_size
    # We'll inject these via environment variables or just run the command
    # Actually, it's easier to just run and capture the output timing
    
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
        elapsed = time.time() - start_time
        
        # Parse metrics from output if possible, or just use our timer
        throughput = total_sims / elapsed
        
        print(f"  Done in {elapsed:.2f}s | Throughput: {throughput:.2f} sims/sec")
        
        results.append({
            "workers": w,
            "batch_size": bs,
            "total_sims": total_sims,
            "elapsed": elapsed,
            "throughput": throughput
        })
    except Exception as e:
        print(f"  ❌ Error: {e}")

print(f"\n========================================================")
print(f"📊 BENCHMARK RESULTS")
print(f"========================================================")
print(f"{'Workers':<10} {'BatchSize':<12} {'Sims':<8} {'Elapsed':<10} {'Sims/Sec':<10}")

results.sort(key=lambda x: x["throughput"], reverse=True)
for r in results:
    print(f"{r['workers']:<10} {r['batch_size']:<12} {r['total_sims']:<8} {r['elapsed']:<10.2f} {r['throughput']:<10.2f}")

best = results[0]
print(f"\n🏆 WINNER: Workers={int(best['workers'])}, BatchSize={int(best['batch_size'])}")
print(f"Max Throughput: {best['throughput']:.2f} sims/sec")
print(f"========================================================")
