
import argparse
import requests
import json
import random
import time
import os
import sys
import threading
import concurrent.futures
import concurrent.futures
from datetime import datetime, timedelta
import pandas as pd
from requests.auth import HTTPBasicAuth

# --- CONFIGURATION ---
API_HOST = os.getenv("API_HOST", "localhost")
API_URL = f"http://{API_HOST}:8000/backtesting/batch-run"
API_URL_TURBO = f"http://{API_HOST}:8000/backtesting/batch-run-turbo"
AUTH = HTTPBasicAuth("admin", "admin")
OUTPUT_DIR = "./custom_strategies/optimization_reports"
TOP_10_TOKENS = [
    "BTC-USDT", "ETH-USDT", "BNB-USDT", "XRP-USDT", "SOL-USDT",
    "ADA-USDT", "AVAX-USDT", "DOGE-USDT", "LINK-USDT", "TRX-USDT"
]

class StrategyOptimizer:
    def __init__(self, mode, days=730, iterations=30, batch_size=None, workers=None, turbo=False):
        self.mode = mode
        self.turbo = turbo  # Use high-performance turbo endpoint
        self.days = days
        self.iterations = iterations
        
        # Auto-detect CPU power
        cpu_count = os.cpu_count() or 4
        
        # ============ PERFORMANCE TUNING GUIDE ============
        # Backend API uses ProcessPoolExecutor(max_workers=10)
        # Each API request spawns 10 parallel processes, each processing batch_size/10 sims
        #
        # Key Formula: Total Concurrent Processes = workers * 10
        # - workers=1 -> 10 processes (optimal CPU utilization for 10-core)
        # - workers=2 -> 20 processes (may cause contention)
        # - workers=4 -> 40 processes (severe slowdown due to context switching)
        #
        # RECOMMENDED SETTINGS for 10-core / 64GB RAM:
        # - workers=1, batch_size=50  -> 10 parallel sims, sequential batches (~60s/batch)
        # - workers=2, batch_size=30  -> 20 parallel sims, slight overlap (~45s/batch)
        #
        # Your test results:
        # - workers=1, batch_size=50: ~57s/batch (OPTIMAL - no contention)
        # - workers=4, batch_size=50: ~205s/batch (4x slower due to 40 processes fighting for 10 cores)
        # ===================================================
        
        # ============ TURBO MODE DEFAULTS (48-core optimized) ============
        # NEW ARCHITECTURE: Moderate parallelism + large batches
        # - Client sends 4 parallel HTTP requests (pipelining)
        # - Each request contains 500 configs  
        # - Server splits each batch into 48 chunks for workers
        # - Total: 4 * 500 = 2000 configs in flight → 48 workers busy
        # ===================================================================
        if turbo:
            # ⚡ OPTIMAL CONFIG for 48-core, 56GB RAM:
            # - batch_size=200: Maximizes cache reuse (same pairs grouped together)
            # - workers=4: Prevents API timeout, keeps all 48 cores busy via server pool
            self.batch_size = batch_size if batch_size else 200
            self.workers = workers if workers else 4
        else:
            # Legacy mode: Conservative settings 
            self.batch_size = batch_size if batch_size else 50
            self.workers = workers if workers else max(1, cpu_count // 4)
        
        self.start_ts, self.end_ts = self._get_time_range(days)
        self.report_id = datetime.now().strftime("%Y%m%d_%H%M")
        
        # Ensure output dir exists
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        self.csv_filename = f"{OUTPUT_DIR}/opt_{self.mode}_{self.report_id}.csv"
        self.results_cache = []
        
        # Heartbeat Status Tracking
        self.stats = {
            "batches_sent": 0,
            "batches_received": 0,
            "batches_success": 0,
            "batches_error": 0,
            "sims_completed": 0,
            "sims_error": 0,
            "best_pnl": -999.0,
            "best_pair": "N/A",
            "last_activity": time.time(),
        }
        self.stats_lock = threading.Lock()
        self._heartbeat_running = False
        
    def _get_time_range(self, days, offset_days=365):
        """
        Get start/end timestamps for backtesting.
        
        Default: Use 730 days of data, offset by 365 days
        This means: 2023-01-01 ~ 2024-12-31 (leaving 2025 for final validation)
        
        Timeline:
        - 2018-2020: Walk-Forward validation (historical)
        - 2021-2022: OOS validation (completely unseen)
        - 2023-2024: Parameter optimization ◄── DEFAULT WINDOW
        - 2025-2026: Reserved for final validation
        """
        end_dt = datetime.now() - timedelta(days=offset_days)  # Skip most recent 'offset_days'
        start_dt = end_dt - timedelta(days=days)
        return int(start_dt.timestamp()), int(end_dt.timestamp())

    def generate_discovery_config(self, pair):
        """Variance: High"""
        interval = random.choice(["1h", "4h"])
        fast = random.choice(list(range(5, 60, 5)))
        slow = random.choice([s for s in range(20, 200, 10) if s > fast + 10])
        return {
            "connector_name": "binance",
            "controller_name": "ma_cross_strategy",
            "controller_type": "custom",
            "trading_pair": pair,
            "fast_ma": fast,
            "slow_ma": slow,
            "indicator_interval": interval,
            "stop_loss": random.choice([0.01, 0.02, 0.03, 0.04, 0.05, 0.07, 0.10]),
            "take_profit": random.choice([0.02, 0.05, 0.08, 0.10, 0.15, 0.20]),
            "time_limit": 21600,
            "total_amount_quote": 100,
            "use_compounding": True
        }

    def generate_refinement_config(self, pair):
        """Variance: Low (Local Search)"""
        # Baseline Center
        base_fast, base_slow = 5, 10
        base_sl, base_tp = 0.02, 0.04
        
        fast = base_fast + random.choice([-1, 0, 1, 2, 3])
        slow = base_slow + random.choice([0, 2, 4, 6])
        if slow <= fast: slow = fast + 5
        
        return {
            "connector_name": "binance",
            "controller_name": "ma_cross_strategy",
            "controller_type": "custom",
            "trading_pair": pair,
            "fast_ma": fast,
            "slow_ma": slow,
            "indicator_interval": "1h",
            "stop_loss": base_sl + random.choice([-0.005, 0, 0.005, 0.01]),
            "take_profit": base_tp + random.choice([-0.01, 0, 0.01, 0.02, 0.03]),
            "time_limit": random.choice([21600, 43200, 86400]),
            "total_amount_quote": 100,
            "use_compounding": True
        }

    def process_batch(self, batch_data):
        """Worker function to process a single batch via API"""
        batch_id, batch_configs = batch_data
        batch_num = batch_id // self.batch_size + 1
        start_time = time.time()
        
        # Update stats: batch sent
        with self.stats_lock:
            self.stats["batches_sent"] += 1
            self.stats["last_activity"] = time.time()
        
        print(f"\n   Batch {batch_num} sending ({len(batch_configs)} sims)...", flush=True)
        
        try:
            # Select endpoint based on turbo mode
            url = API_URL_TURBO if self.turbo else API_URL
            response = requests.post(url, json=batch_configs, auth=AUTH, timeout=1200)  # 20 min timeout
            elapsed = time.time() - start_time
            
            if response.status_code != 200:
                with self.stats_lock:
                    self.stats["batches_error"] += 1
                    self.stats["last_activity"] = time.time()
                print(f"\n   Batch {batch_num}: API Error {response.status_code} ({elapsed:.1f}s)", flush=True)
                return []
            
            valid_results = response.json().get("results", [])
            error_count = sum(1 for r in valid_results if "error" in r)
            success_count = len([r for r in valid_results if "error" not in r])
            
            # Update stats: batch received
            with self.stats_lock:
                self.stats["batches_received"] += 1
                self.stats["batches_success"] += 1
                self.stats["sims_completed"] += success_count
                self.stats["sims_error"] += error_count
                self.stats["last_activity"] = time.time()
                
                # Track best result
                for r in valid_results:
                    if "error" not in r:
                        pnl = float(r.get("net_pnl", 0))
                        if pnl > self.stats["best_pnl"]:
                            self.stats["best_pnl"] = pnl
                            self.stats["best_pair"] = r.get("trading_pair", "N/A")
            
            print(f"\n   Batch {batch_num} received: {success_count} ok, {error_count} errors ({elapsed:.1f}s)", flush=True)
            
            # Debug: Check for errors in the results
            for r in valid_results:
                if "error" in r:
                    print(f"\n      {r.get('trading_pair', 'Unknown')}: {str(r['error'])[:60]}...", flush=True)

            return [r for r in valid_results if "error" not in r]
        except requests.exceptions.Timeout:
            elapsed = time.time() - start_time
            with self.stats_lock:
                self.stats["batches_error"] += 1
            print(f"\n   Batch {batch_num}: Timeout after {elapsed:.1f}s", flush=True)
            return []
        except Exception as e:
            elapsed = time.time() - start_time
            with self.stats_lock:
                self.stats["batches_error"] += 1
            print(f"\n   Batch {batch_num}: Exception {str(e)[:50]}... ({elapsed:.1f}s)", flush=True)
            return []

    def _heartbeat_monitor(self, total_batches, run_start_time):
        """Background thread that prints status every 30 seconds"""
        heartbeat_interval = 30  # seconds
        last_sent = 0
        last_received = 0
        
        while self._heartbeat_running:
            time.sleep(heartbeat_interval)
            if not self._heartbeat_running:
                break
                
            with self.stats_lock:
                stats = self.stats.copy()
            
            elapsed = time.time() - run_start_time
            elapsed_min = int(elapsed // 60)
            elapsed_sec = int(elapsed % 60)
            
            # Calculate rates
            sent_delta = stats["batches_sent"] - last_sent
            recv_delta = stats["batches_received"] - last_received
            last_sent = stats["batches_sent"]
            last_received = stats["batches_received"]
            
            # Time since last activity
            idle_time = time.time() - stats["last_activity"]
            
            # ETA calculation
            if stats["batches_received"] > 0:
                avg_time = elapsed / stats["batches_received"]
                remaining = total_batches - stats["batches_received"]
                eta_sec = remaining * avg_time
                eta_min = int(eta_sec // 60)
            else:
                eta_min = -1
            
            # Print heartbeat status
            print(f"\n{'='*60}", flush=True)
            print(f"HEARTBEAT [{datetime.now().strftime('%H:%M:%S')}] - Elapsed: {elapsed_min}m {elapsed_sec}s", flush=True)
            print(f"├─ Sent:     {stats['batches_sent']}/{total_batches} batches (+{sent_delta} in last 30s)", flush=True)
            print(f"├─ Received: {stats['batches_received']}/{total_batches} batches (+{recv_delta} in last 30s)", flush=True)
            print(f"├─ Success:  {stats['sims_completed']} sims | Errors: {stats['sims_error']} sims", flush=True)
            print(f"├─ Best:     {stats['best_pair']} (PnL: {stats['best_pnl']:.2f}%)", flush=True)
            print(f"├─ Idle:     {idle_time:.1f}s since last activity", flush=True)
            if eta_min >= 0:
                print(f"└─ ETA:      ~{eta_min}m remaining", flush=True)
            else:
                print(f"└─ ETA:      calculating...", flush=True)
            print(f"{'='*60}", flush=True)
            
            # Warn if idle too long
            if idle_time > 120:
                print(f"\nWARNING: No activity for {idle_time:.0f}s - backend may be stuck!", flush=True)

    def run(self, target_tokens=TOP_10_TOKENS):
        version = "v4.0 (TURBO Edition)" if self.turbo else "v3.0 (Parallel Edition)"
        print(f"\nSTRATEGY OPTIMIZER {version}")
        print(f"==========================================")
        print(f"Mode:       {self.mode.upper()}")
        print(f"Turbo:      {'ENABLED' if self.turbo else 'DISABLED'}")
        print(f"Concurrency:{self.workers} Workers")
        print(f"Parallel:   {self.batch_size} Simultaneous Sims per Req")
        print(f"Report ID:  {self.report_id}")
        print(f"==========================================\n")
        
        # CSV Header
        with open(self.csv_filename, 'w') as f:
            f.write("Pair,Interval,Fast,Slow,SL,TP,TL,Net PnL %,Max DD %,Win Rate %,Sharpe\n")
            
        total_configs = []
        for pair in target_tokens:
            for _ in range(self.iterations):
                if self.mode == "refine": cfg = self.generate_refinement_config(pair)
                else: cfg = self.generate_discovery_config(pair)
                
                total_configs.append({
                    "config": cfg,
                    "start_time": self.start_ts,
                    "end_time": self.end_ts,
                    "backtesting_resolution": "1m", # High precision
                    "trade_cost": 0.0006
                })
        
        random.shuffle(total_configs)
        
        # Prepare Batches
        batches = []
        for i in range(0, len(total_configs), self.batch_size):
            batches.append((i, total_configs[i:i+self.batch_size]))
            
        total_sims = len(total_configs)
        print(f"Dispatching {len(batches)} batches across {self.workers} threads...", flush=True)
        print(f"Total simulations: {total_sims} | Tokens: {len(target_tokens)} | Iters/Token: {self.iterations}", flush=True)
        print(f"Start time: {datetime.now().strftime('%H:%M:%S')}", flush=True)
        print(f"{'='*60}", flush=True)

        # --- PARALLEL EXECUTION LOOP ---
        run_start_time = time.time()
        completed_batches = 0
        
        # Start heartbeat monitor thread
        self._heartbeat_running = True
        heartbeat_thread = threading.Thread(
            target=self._heartbeat_monitor,
            args=(len(batches), run_start_time),
            daemon=True
        )
        heartbeat_thread.start()
        print(f"Heartbeat monitor started (reports every 30s)", flush=True)
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.workers) as executor:
            # map returns an iterator in order, but we can't show progress easily with map
            # use as_completed
            futures = {executor.submit(self.process_batch, b): b for b in batches}
            
            for future in concurrent.futures.as_completed(futures):
                results = future.result()
                completed_batches += 1
                
                # Save & Cache Results
                rows = []
                for res in results:
                    if not res or not isinstance(res, dict):
                        continue
                        
                    c = res.get("config", {})
                    if not c:
                        continue
                        
                    pnl_pct = res.get('net_pnl', 0) * 100
                    dd_pct = res.get('max_drawdown_pct', 0) * 100
                    
                    self.results_cache.append({
                        "Pair": res.get('trading_pair', 'Unknown'),
                        "Config": f"Fast {c.get('fast_ma')}/Slow {c.get('slow_ma')}/{c.get('indicator_interval')} (SL {c.get('stop_loss')}/TP {c.get('take_profit')})",
                        "Interval": c.get('indicator_interval'),
                        "Fast": c.get('fast_ma'),
                        "Slow": c.get('slow_ma'),
                        "SL": c.get('stop_loss'),
                        "TP": c.get('take_profit'),
                        "PnL": pnl_pct,
                        "Drawdown": dd_pct,
                        "Sharpe": res.get('sharpe_ratio', 0),
                        "Accuracy": res.get('accuracy', 0),
                    })
                    
                    row = f"{res.get('trading_pair')},{c.get('indicator_interval')},{c.get('fast_ma')},{c.get('slow_ma')},{c.get('stop_loss')},{c.get('take_profit')},{c.get('time_limit')},{pnl_pct:.2f},{dd_pct:.2f},{res.get('accuracy', 0)*100:.2f},{res.get('sharpe_ratio', 0):.2f}"
                    rows.append(row)
                
                if rows:
                    with open(self.csv_filename, 'a') as f:
                        for r in rows: f.write(r + "\n")
                
                # Calculate timing
                elapsed_total = time.time() - run_start_time
                avg_batch_time = elapsed_total / completed_batches if completed_batches > 0 else 0
                remaining_batches = len(batches) - completed_batches
                eta_seconds = remaining_batches * avg_batch_time
                eta_min = int(eta_seconds // 60)
                eta_sec = int(eta_seconds % 60)
                
                # [NEW] Real-time Batch Feedback
                best_roi = -999.0
                best_pair = "N/A"
                if results:
                    for res in results:
                        pnl = float(res.get("net_pnl", 0))
                        if pnl > best_roi:
                            best_roi = pnl
                            best_pair = res.get("trading_pair", "N/A")
                            
                    print(f"\n   Batch {completed_batches}/{len(batches)} Done: {len(results)} sims. Best: {best_pair} (PnL {best_roi:.2f})")
                
                pct = completed_batches / len(batches) * 100
                bar_len = 30
                filled = int(bar_len * pct / 100)
                bar = '█' * filled + '-' * (bar_len - filled)
                
                # Show progress with ETA
                print(f"\nProgress: [{bar}] {pct:.1f}% ({completed_batches}/{len(batches)}) | ETA: {eta_min}m {eta_sec}s", flush=True)
                print(f"Elapsed: {int(elapsed_total//60)}m {int(elapsed_total%60)}s | Avg: {avg_batch_time:.1f}s/batch", flush=True)

                # Periodic Memory Cleanup (Every 1 batches)
                if completed_batches % 1 == 0:
                    self.clean_server_memory()
        
        # Stop heartbeat monitor
        self._heartbeat_running = False
        print(f"\nHeartbeat monitor stopped", flush=True)
        
        print(f"\nOptimization Complete.")
        self.generate_report(self.results_cache)

    def clean_server_memory(self):
        """Trigger backend garbage collection"""
        try:
            requests.post(API_URL.replace("/batch-run", "/gc"), auth=AUTH, timeout=2)
        except: pass

    def generate_report(self, results):
        if not results:
            print("No results to report.")
            return

        df = pd.DataFrame(results)
        
        # Find "Holy Grails" (>15% PnL, <20% DD)
        grails = df[(df["PnL"] > 15) & (df["Drawdown"].abs() < 20)].sort_values("PnL", ascending=False)
        
        # Find "Stable Gems" (PnL > 0, DD < 10%, Sharpe > 1.5)
        gems = df[(df["PnL"] > 0) & (df["Drawdown"].abs() < 10) & (df["Sharpe"] > 1.5)].sort_values("Sharpe", ascending=False)

        md_filename = f"{OUTPUT_DIR}/report_{self.mode}_{self.report_id}.md"
        
        with open(md_filename, 'w') as f:
            f.write(f"# Strategy Optimization Report: {self.mode.title()}\n\n")
            f.write(f"**Date**: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
            f.write(f"**Analysis Window**: Last {self.days} days\n")
            f.write(f"**Resolution**: 1m (High Precision)\n\n")
            
            f.write("## Top Performers (Holy Grails)\n")
            f.write("> **Criteria**: PnL > 15% , Max Drawdown < 20%\n\n")
            if not grails.empty:
                try:
                    f.write(grails.head(10)[["Pair", "Config", "PnL", "Drawdown", "Sharpe"]].to_markdown(index=False, floatfmt=".2f"))
                except ImportError:
                    # Fallback if tabulate not installed
                    f.write("```\n")
                    f.write(grails.head(10)[["Pair", "Config", "PnL", "Drawdown", "Sharpe"]].to_string(index=False))
                    f.write("\n```")
            else:
                f.write("*No strategies met the strict 'Holy Grail' criteria in this run.*")
            
            f.write("\n\n## Sweet Spot Analysis (Robust Parameter Clusters)\n")
            f.write("> **Why this matters**: Individual peaks are often overfitting. We look for 'Sweet Spots'—ranges of parameters that perform consistently well across multiple tests.\n\n")
            
            # Analyze MA Sweet Spots
            ma_analysis = df.groupby(["Pair", "Interval", "Fast", "Slow"])["PnL"].agg(["mean", "count", "std"]).reset_index()
            # Filter for clusters (at least 2 identical configs or very similar ones)
            # Since discovery is random, we bucket MAs
            df_bucketed = df.copy()
            df_bucketed["Fast_Bucket"] = (df_bucketed["Fast"] // 5) * 5
            df_bucketed["Slow_Bucket"] = (df_bucketed["Slow"] // 20) * 20
            
            cluster = df_bucketed.groupby(["Pair", "Interval", "Fast_Bucket", "Slow_Bucket"])["PnL"].agg(["mean", "count"]).reset_index()
            cluster = cluster[cluster["count"] >= 2].sort_values("mean", ascending=False).head(10)
            
            if not cluster.empty:
                f.write("### Top MA Range Clusters\n")
                f.write(cluster.to_markdown(index=False, floatfmt=".2f"))
            else:
                f.write("*Not enough overlapping data for cluster analysis. Run more iterations.*")

            f.write("\n\n## Parameter Instructions\n")
            f.write("Apply these best params in **Dashboard -> Smart Strategy**.\n")
            
        print(f"Report generated: {md_filename}")
        if not grails.empty:
            print("\nTop Result Preview:")
            print(grails.head(3).to_string(index=False))

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Hummingbot V2 Strategy Optimizer")
    parser.add_argument("--mode", type=str, choices=["discovery", "refine"], default="discovery")
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--iter", type=int, default=10)
    parser.add_argument("--tokens", type=str, default="ALL")
    parser.add_argument("--batch_size", type=int, default=None, help="Backtest scenarios per API call")
    parser.add_argument("--workers", type=int, default=None, help="Parallel API connections")
    parser.add_argument("--turbo", action="store_true", help="Use high-performance turbo endpoint (48-core optimized)")
    
    args = parser.parse_args()
    tokens = TOP_10_TOKENS if args.tokens == "ALL" else args.tokens.split(",")
    
    optimizer = StrategyOptimizer(mode=args.mode, days=args.days, iterations=args.iter, batch_size=args.batch_size, workers=args.workers, turbo=args.turbo)
    optimizer.run(target_tokens=tokens)
