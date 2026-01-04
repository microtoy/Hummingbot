
import argparse
import requests
import json
import random
import time
import os
import sys
import threading
import concurrent.futures
import numpy as np
from datetime import datetime, timedelta
import pandas as pd
from requests.auth import HTTPBasicAuth

# --- CONFIGURATION ---
API_HOST = os.getenv("API_HOST", os.getenv("BACKEND_API_HOST", "localhost"))
API_URL = f"http://{API_HOST}:8000/backtesting/batch-run"
API_URL_TURBO = f"http://{API_HOST}:8000/backtesting/batch-run-turbo"
AUTH = HTTPBasicAuth("admin", "admin")
OUTPUT_DIR = "./custom_strategies/optimization_reports"
TOP_10_TOKENS = [
    "BTC-USDT", "ETH-USDT", "BNB-USDT", "XRP-USDT", "SOL-USDT",
    "ADA-USDT", "AVAX-USDT", "DOGE-USDT", "LINK-USDT", "TRX-USDT"
]

class RobustStrategyOptimizer:
    """
    🔬 Professional-Grade Robust Strategy Optimizer
    
    Implements industry best practices:
    1. Anchored Walk-Forward Optimization (AWFO)
    2. Multi-Year Consistency Validation
    3. Parameter Neighborhood Robustness Testing
    4. Walk-Forward Efficiency (WFE) Scoring
    
    Based on methodologies used by quantitative hedge funds.
    """
    
    def __init__(self, mode="robust", days=365, iterations=100, batch_size=250, workers=4, turbo=True):
        self.mode = mode
        self.turbo = turbo
        self.days = days
        self.iterations = iterations
        self.batch_size = batch_size
        self.workers = workers
        self.report_id = datetime.now().strftime("%Y%m%d_%H%M")
        self.results_cache = []
        
        # API Endpoints
        self.api_url = API_URL_TURBO if turbo else API_URL
        self.gc_url = f"http://{API_HOST}:8000/backtesting/gc"
        
        # Statistics tracking
        self.stats = {
            "batches_sent": 0,
            "batches_received": 0,
            "sims_completed": 0,
            "sims_error": 0,
            "best_robust_score": -999.0,
            "best_pair": "N/A",
        }
        self.stats_lock = threading.Lock()
        
        os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    # =========================================================================
    # TIME WINDOW GENERATORS
    # =========================================================================
    
    def get_multi_year_windows(self, periods=4, period_days=365):
        """
        Generate time windows using RELATIVE OFFSETS from current date.
        
        This approach works because the backtesting engine uses relative
        time windows from current date, not absolute calendar dates.
        
        Example with periods=4, period_days=365:
        Period 1: offset 365-730 days ago   (roughly Y-1)
        Period 2: offset 730-1095 days ago  (roughly Y-2)
        Period 3: offset 1095-1460 days ago (roughly Y-3)
        Period 4: offset 1460-1825 days ago (roughly Y-4)
        """
        now = datetime.now()
        windows = []
        
        for i in range(periods):
            # Each period is offset further back in time
            end_offset = 365 + (i * period_days)  # Start from 1 year ago
            start_offset = end_offset + period_days
            
            end_dt = now - timedelta(days=end_offset)
            start_dt = now - timedelta(days=start_offset)
            
            windows.append({
                "label": f"Period_{i+1}",
                "period": i + 1,
                "start_ts": int(start_dt.timestamp()),
                "end_ts": int(end_dt.timestamp()),
                "description": f"{start_dt.strftime('%Y-%m')} to {end_dt.strftime('%Y-%m')}"
            })
        
        return windows
    
    def get_anchored_wfo_windows(self, anchor_year=2021, step_months=6):
        """
        Generate Anchored Walk-Forward windows.
        
        Anchor: Fixed start point (2021-01-01)
        Training: Expands by 6 months each step
        Testing: Next 6 months after training
        
        Example:
        Step 1: Train [2021-01 to 2021-12], Test [2022-01 to 2022-06]
        Step 2: Train [2021-01 to 2022-06], Test [2022-07 to 2022-12]
        Step 3: Train [2021-01 to 2022-12], Test [2023-01 to 2023-06]
        ...
        """
        anchor_date = datetime(anchor_year, 1, 1)
        current_date = datetime.now()
        
        windows = []
        train_end = datetime(anchor_year, 12, 31)  # First training period: full year
        
        step = 0
        while train_end < current_date - timedelta(days=180):  # Need 6 months for testing
            test_start = train_end + timedelta(days=1)
            test_end = test_start + timedelta(days=180)  # 6 months testing
            
            if test_end > current_date:
                break
            
            windows.append({
                "step": step + 1,
                "train_start_ts": int(anchor_date.timestamp()),
                "train_end_ts": int(train_end.timestamp()),
                "test_start_ts": int(test_start.timestamp()),
                "test_end_ts": int(test_end.timestamp()),
                "train_label": f"{anchor_date.strftime('%Y-%m')} to {train_end.strftime('%Y-%m')}",
                "test_label": f"{test_start.strftime('%Y-%m')} to {test_end.strftime('%Y-%m')}"
            })
            
            # Expand training window by 6 months
            train_end = train_end + timedelta(days=180)
            step += 1
        
        return windows
    
    # =========================================================================
    # ROBUSTNESS SCORING
    # =========================================================================
    
    def calculate_wfe(self, in_sample_pnl, out_of_sample_pnl):
        """
        Walk-Forward Efficiency: OOS performance / IS performance
        
        WFE > 0.5: Good (strategy generalizes well)
        WFE < 0.3: Poor (likely overfitting)
        """
        if in_sample_pnl <= 0:
            return 0.0
        return min(1.0, out_of_sample_pnl / in_sample_pnl) if in_sample_pnl > 0 else 0.0
    
    def calculate_robust_score(self, period_results: dict):
        """
        Calculate robustness score based on multi-period performance.
        
        Args:
            period_results: dict like {1: {"pnl": 15.2, "trades": 50, ...}, 2: {...}}
        
        Returns:
            (robust_score, is_valid, stats)
        """
        # Extract PnLs from period results
        pnls = [pr["pnl"] for pr in period_results.values()]
        trades = [pr["trades"] for pr in period_results.values()]
        accuracies = [pr["accuracy"] for pr in period_results.values() if pr["trades"] > 0]
        sharpes = [pr["sharpe"] for pr in period_results.values() if pr["trades"] > 0]
        max_dds = [pr["max_dd"] for pr in period_results.values() if pr["trades"] > 0]
        
        n_periods = len(pnls)
        
        if n_periods == 0:
            return 0, False, {}
        
        profitable_periods = sum(1 for p in pnls if p > 0)
        mean_pnl = np.mean(pnls)
        std_pnl = np.std(pnls) if len(pnls) > 1 else 0
        min_pnl = np.min(pnls)
        max_pnl = np.max(pnls)
        total_trades = sum(trades)
        
        # Filter: Must be profitable in at least 75% of periods
        consistency_ratio = profitable_periods / n_periods
        if consistency_ratio < 0.75:
            return 0, False, {"reason": f"Only {profitable_periods}/{n_periods} periods profitable"}
        
        # Stability penalty (lower variance = better)
        if abs(mean_pnl) > 0.001:
            cv = std_pnl / abs(mean_pnl)  # Coefficient of variation
            stability = max(0, 1 - cv)
        else:
            stability = 0
        
        # Final score: Mean * Consistency * Stability
        score = mean_pnl * np.sqrt(consistency_ratio) * (0.5 + 0.5 * stability)
        
        # Aggregate stats
        stats = {
            "mean_pnl": mean_pnl,
            "std_pnl": std_pnl,
            "min_pnl": min_pnl,
            "max_pnl": max_pnl,
            "total_trades": total_trades,
            "avg_trades": total_trades / n_periods if n_periods > 0 else 0,
            "mean_accuracy": np.mean(accuracies) if accuracies else 0,
            "mean_sharpe": np.mean(sharpes) if sharpes else 0,
            "max_drawdown": np.max(np.abs(max_dds)) if max_dds else 0,
            "profitable_periods": profitable_periods,
            "consistency": consistency_ratio,
            "stability": stability
        }
        
        return score, True, stats
    
    def check_parameter_robustness(self, base_results, neighbor_results):
        """
        Check if parameter is in a 'plateau' region (neighbors also profitable).
        
        Returns:
            (is_robust, robustness_ratio)
        """
        if not neighbor_results:
            return False, 0.0
        
        profitable_neighbors = sum(1 for r in neighbor_results if r.get("pnl", 0) > 0)
        robustness_ratio = profitable_neighbors / len(neighbor_results)
        
        # Robust if >60% of neighbors are also profitable
        return robustness_ratio > 0.6, robustness_ratio
    
    # =========================================================================
    # BACKTESTING EXECUTION
    # =========================================================================
    
    def _build_config(self, pair, fast, slow, interval, sl, tp):
        """Build a standard backtest configuration."""
        return {
            "connector_name": "binance",
            "controller_name": "ma_cross_strategy",
            "controller_type": "custom",
            "trading_pair": pair,
            "fast_ma": fast,
            "slow_ma": slow,
            "indicator_interval": interval,
            "stop_loss": sl,
            "take_profit": tp,
            "time_limit": 21600,
            "total_amount_quote": 100,
            "use_compounding": True,
            "leverage": 1,
            "max_executors_per_side": 2,
            "cooldown_time": 300
        }
    
    def _run_batch(self, configs):
        """Run batch via API."""
        try:
            with self.stats_lock:
                self.stats["batches_sent"] += 1
            
            response = requests.post(self.api_url, json=configs, auth=AUTH, timeout=1200)
            
            if response.status_code != 200:
                with self.stats_lock:
                    self.stats["sims_error"] += len(configs)
                return [{"error": f"HTTP {response.status_code}"}] * len(configs)
            
            results = response.json().get("results", [])
            
            with self.stats_lock:
                self.stats["batches_received"] += 1
                self.stats["sims_completed"] += sum(1 for r in results if "error" not in r)
                self.stats["sims_error"] += sum(1 for r in results if "error" in r)
            
            return results
        except Exception as e:
            with self.stats_lock:
                self.stats["sims_error"] += len(configs)
            return [{"error": str(e)}] * len(configs)
    
    def run_multi_period_backtest(self, config, periods=4):
        """
        Run same config across multiple time periods using Turbo API.
        
        Returns:
            period_results: dict with full stats per period
            windows: list of window definitions
        """
        windows = self.get_multi_year_windows(periods=periods)
        
        # Build batch with individual time windows per config
        batch_configs = []
        for window in windows:
            batch_configs.append({
                "config": config,
                "start_time": window["start_ts"],
                "end_time": window["end_ts"],
                "backtesting_resolution": "1m",
                "trade_cost": 0.0006
            })
        
        # Use Turbo API batch (now fixed to support individual time windows)
        results = self._run_batch(batch_configs)
        
        period_results = {}
        for i, window in enumerate(windows):
            if i < len(results) and "error" not in results[i]:
                r = results[i]
                period_results[window["period"]] = {
                    "pnl": float(r.get("net_pnl", 0)) * 100,
                    "trades": int(r.get("total_positions", 0)),
                    "accuracy": float(r.get("accuracy", 0)) * 100,
                    "sharpe": float(r.get("sharpe_ratio", 0) or 0),
                    "max_dd": float(r.get("max_drawdown_pct", 0)) * 100,
                    "profit_factor": float(r.get("profit_factor", 0)),
                }
            else:
                period_results[window["period"]] = {
                    "pnl": 0.0, "trades": 0, "accuracy": 0.0,
                    "sharpe": 0.0, "max_dd": 0.0, "profit_factor": 0.0
                }
        
        return period_results, windows
    
    def generate_random_config(self, pair):
        """Generate a random parameter configuration for discovery."""
        interval = random.choice(["1h", "4h"])
        fast = random.choice(list(range(5, 60, 5)))
        slow = random.choice([s for s in range(20, 200, 10) if s > fast + 10])
        sl = random.choice([0.02, 0.03, 0.04, 0.05, 0.07, 0.10])
        tp = random.choice([0.02, 0.05, 0.08, 0.10, 0.15])
        
        return self._build_config(pair, fast, slow, interval, sl, tp)
    
    # =========================================================================
    # MAIN OPTIMIZATION LOOP
    # =========================================================================
    
    def run_robust_discovery(self, target_tokens=TOP_10_TOKENS, periods=4):
        """
        🚀 ULTRA-SCALE ROBUST DISCOVERY
        - Generates all candidates for all tokens first
        - Flattens into massive global batch
        - Processes in parallel using ThreadPoolExecutor for max throughput
        """
        sample_windows = self.get_multi_year_windows(periods=periods)
        
        print(f"\n{'='*70}")
        print("🚀 ULTRA-SCALE ROBUST OPTIMIZER v2.0")
        print(f"{'='*70}")
        print(f"Mode:       ROBUST DISCOVERY")
        print(f"Total Iter: {self.iterations * len(target_tokens):,d} ({self.iterations:,d} per token)")
        print(f"Workers:    {self.workers} | Batch Size: {self.batch_size}")
        print(f"Periods:    {periods} | Total Backtests: {self.iterations * len(target_tokens) * periods:,d}")
        print(f"Report ID:  {self.report_id}")
        print(f"{'='*70}\n")
        
        start_time = time.time()
        
        # 1. GENERATE ALL CANDIDATES
        print(f"🛠️  Generating {self.iterations * len(target_tokens):,d} candidates...")
        all_candidates = []
        for pair in target_tokens:
            for _ in range(self.iterations):
                all_candidates.append({
                    "pair": pair,
                    "config": self.generate_random_config(pair)
                })
        
        # 2. FLATTEN INTO BACKTEST TASKS
        print(f"📦 Squashing into {len(all_candidates) * periods:,d} backtest tasks...")
        all_tasks = []
        windows = self.get_multi_year_windows(periods=periods)
        
        for cand_idx, cand in enumerate(all_candidates):
            for window in windows:
                all_tasks.append({
                    "cand_idx": cand_idx,
                    "period": window["period"],
                    "start_time": window["start_ts"],
                    "end_time": window["end_ts"],
                    "config": cand["config"],
                    "backtesting_resolution": "1m",
                    "trade_cost": 0.0006
                })
        
        # 3. PROCESS IN PARALLEL BATCHES
        print(f"⚡ Dispatched to {self.workers} workers. Processing...")
        
        # Divide all_tasks into chunks for Turbo API
        task_chunks = [all_tasks[i:i + self.batch_size] for i in range(0, len(all_tasks), self.batch_size)]
        
        raw_results = [None] * len(all_tasks)
        processed_batches = 0
        total_batches = len(task_chunks)
        
        def process_one_chunk(chunk_idx):
            chunk = task_chunks[chunk_idx]
            results = self._run_batch(chunk)
            # Map results back to raw_results based on index
            start_pos = chunk_idx * self.batch_size
            for i, res in enumerate(results):
                if start_pos + i < len(raw_results):
                    raw_results[start_pos + i] = res
            return len(results)

        with concurrent.futures.ThreadPoolExecutor(max_workers=self.workers) as executor:
            futures = {executor.submit(process_one_chunk, i): i for i in range(total_batches)}
            
            for future in concurrent.futures.as_completed(futures):
                processed_batches += 1
                if processed_batches % (max(1, total_batches // 20)) == 0 or processed_batches == total_batches:
                    elapsed = time.time() - start_time
                    progress_pct = (processed_batches / total_batches) * 100
                    eta = (elapsed / processed_batches) * (total_batches - processed_batches)
                    print(f"   Progress: {progress_pct:.1f}% | Batches: {processed_batches}/{total_batches} | ETA: {int(eta//60)}m", flush=True)

        # 4. RECONSTRUCT CANDIDATE RESULTS
        print(f"\n🧩 Reconstructing results and scoring...")
        candidate_results = [{} for _ in range(len(all_candidates))]
        
        for i, task in enumerate(all_tasks):
            cand_idx = task["cand_idx"]
            period = task["period"]
            res = raw_results[i]
            
            if res and "error" not in res:
                candidate_results[cand_idx][period] = {
                    "pnl": float(res.get("net_pnl", 0)) * 100,
                    "trades": int(res.get("total_positions", 0)),
                    "accuracy": float(res.get("accuracy", 0)) * 100,
                    "sharpe": float(res.get("sharpe_ratio", 0) or 0),
                    "max_dd": float(res.get("max_drawdown_pct", 0)) * 100,
                    "profit_factor": float(res.get("profit_factor", 0)),
                }
            else:
                candidate_results[cand_idx][period] = {
                    "pnl": 0.0, "trades": 0, "accuracy": 0.0,
                    "sharpe": 0.0, "max_dd": 0.0, "profit_factor": 0.0
                }

        # 5. SCORING AND FILTERING
        robust_results = []
        csv_file = f"{OUTPUT_DIR}/robust_{self.report_id}.csv"
        
        with open(csv_file, 'w') as f:
            period_headers = ','.join([f"P{i+1}_PnL" for i in range(periods)])
            f.write(f"Pair,Interval,Fast,Slow,SL,TP,{period_headers},Mean_PnL,Trades,Accuracy,Sharpe,MaxDD,Score,Valid\n")
            
            for i, cand in enumerate(all_candidates):
                period_pnls = candidate_results[i]
                score, is_valid, stats = self.calculate_robust_score(period_pnls)
                
                result = {
                    "pair": cand["pair"],
                    "config": cand["config"],
                    "period_results": period_pnls,
                    "windows": windows,
                    "robust_score": score,
                    "is_valid": is_valid,
                    "stats": stats
                }
                
                # Write to CSV
                pair = cand["pair"]
                config = cand["config"]
                pnl_values = ','.join([f"{period_pnls.get(j+1, {}).get('pnl', 0):.2f}" for j in range(periods)])
                row = (f"{pair},{config['indicator_interval']},{config['fast_ma']},"
                       f"{config['slow_ma']},{config['stop_loss']},{config['take_profit']},"
                       f"{pnl_values},"
                       f"{stats.get('mean_pnl', 0):.2f},{stats.get('total_trades', 0)},"
                       f"{stats.get('mean_accuracy', 0):.1f},{stats.get('mean_sharpe', 0):.2f},"
                       f"{stats.get('max_drawdown', 0):.1f},{score:.2f},{is_valid}\n")
                f.write(row)
                
                if is_valid:
                    robust_results.append(result)
                    with self.stats_lock:
                        if score > self.stats["best_robust_score"]:
                            self.stats["best_robust_score"] = score
                            self.stats["best_pair"] = pair

        # Sort and cleanup
        robust_results.sort(key=lambda x: x["robust_score"], reverse=True)
        try: requests.post(self.gc_url, auth=AUTH, timeout=5)
        except: pass
        
        elapsed = time.time() - start_time
        
        # Generate report
        self._generate_robust_report(robust_results, elapsed, periods)
        
        return robust_results
    
    def _generate_robust_report(self, results, elapsed, periods):
        """Generate markdown report for robust discovery."""
        report_file = f"{OUTPUT_DIR}/robust_report_{self.report_id}.md"
        
        # Get window info for report
        sample_windows = self.get_multi_year_windows(periods=periods)
        
        with open(report_file, 'w') as f:
            f.write("# 🔬 Robust Strategy Discovery Report\n\n")
            f.write(f"**Generated**: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
            f.write(f"**Periods Tested**: {periods}\n")
            for w in sample_windows:
                f.write(f"- {w['label']}: {w['description']}\n")
            f.write(f"\n**Total Candidates**: {self.iterations * len(TOP_10_TOKENS)}\n")
            f.write(f"**Valid Robust Strategies**: {len(results)}\n")
            f.write(f"**Runtime**: {int(elapsed//60)}m {int(elapsed%60)}s\n\n")
            
            f.write("---\n\n")
            f.write("## Methodology\n\n")
            f.write("Each candidate was tested on ALL specified periods independently.\n")
            f.write("**Criteria for 'Valid'**: Profitable in >= 75% of periods\n")
            f.write("**Scoring**: `Mean_PnL × √Consistency × (0.5 + 0.5×Stability)`\n\n")
            
            f.write("---\n\n")
            f.write("## 🏆 Top Robust Strategies\n\n")
            
            if results:
                # Table with detailed stats
                f.write("| Rank | Pair | Interval | Fast/Slow | SL/TP | Mean PnL | Trades | Accuracy | Sharpe | MaxDD | Score |\n")
                f.write("|------|------|----------|-----------|-------|----------|--------|----------|--------|-------|-------|\n")
                
                for i, r in enumerate(results[:20]):
                    cfg = r["config"]
                    s = r["stats"]
                    f.write(f"| {i+1} | {r['pair']} | {cfg['indicator_interval']} | "
                           f"{cfg['fast_ma']}/{cfg['slow_ma']} | "
                           f"{cfg['stop_loss']*100:.0f}%/{cfg['take_profit']*100:.0f}% | "
                           f"{s.get('mean_pnl', 0):+.1f}% | {s.get('total_trades', 0)} | "
                           f"{s.get('mean_accuracy', 0):.1f}% | {s.get('mean_sharpe', 0):.2f} | "
                           f"{s.get('max_drawdown', 0):.1f}% | {r['robust_score']:.1f} |\n")
                
                # Period detail table for top 5
                f.write("\n### 📈 Period Detail for Top 5\n\n")
                for i, r in enumerate(results[:5]):
                    cfg = r["config"]
                    pr = r["period_results"]
                    f.write(f"\n**{i+1}. {r['pair']} {cfg['indicator_interval']} Fast={cfg['fast_ma']}/Slow={cfg['slow_ma']}**\n\n")
                    f.write("| Period | PnL | Trades | Accuracy | Sharpe | MaxDD |\n")
                    f.write("|--------|-----|--------|----------|--------|-------|\n")
                    for win in r["windows"]:
                        p = win["period"]
                        ps = pr.get(p, {})
                        f.write(f"| {win['description']} | {ps.get('pnl', 0):+.1f}% | {ps.get('trades', 0)} | "
                               f"{ps.get('accuracy', 0):.1f}% | {ps.get('sharpe', 0):.2f} | {ps.get('max_dd', 0):.1f}% |\n")
            else:
                f.write("❌ **No strategies met the robustness criteria!**\n\n")
                f.write("This indicates:\n")
                f.write("- MA Cross strategy may not work well across all market conditions\n")
                f.write("- Consider testing more parameter combinations\n")
                f.write("- Consider different strategy types\n")
            
            f.write("\n---\n\n")
            f.write("## 📊 Statistics\n\n")
            f.write(f"- **API Calls**: {self.stats['batches_sent']}\n")
            f.write(f"- **Sims Completed**: {self.stats['sims_completed']}\n")
            f.write(f"- **Sims Error**: {self.stats['sims_error']}\n")
            f.write(f"- **Best Score**: {self.stats['best_robust_score']:.2f} ({self.stats['best_pair']})\n")
        
        print(f"\n✅ Report saved: {report_file}")
        print(f"✅ CSV saved: {OUTPUT_DIR}/robust_{self.report_id}.csv")
        
        if results:
            print(f"\n🏆 Top 3 Robust Strategies:")
            for i, r in enumerate(results[:3]):
                cfg = r["config"]
                print(f"   {i+1}. {r['pair']} {cfg['indicator_interval']} "
                      f"Fast={cfg['fast_ma']}/Slow={cfg['slow_ma']} | "
                      f"Score={r['robust_score']:.1f} | "
                      f"Mean={r['stats'].get('mean_pnl', 0):.1f}%")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Robust Strategy Optimizer")
    parser.add_argument("--mode", type=str, choices=["robust"], default="robust")
    parser.add_argument("--iter", type=int, default=50, help="Candidates per token")
    parser.add_argument("--periods", type=int, default=4, help="Number of annual periods to test")
    parser.add_argument("--tokens", type=str, default="ALL", help="Tokens to test")
    parser.add_argument("--batch_size", type=int, default=250)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--turbo", action="store_true", default=True)
    
    args = parser.parse_args()
    
    tokens = TOP_10_TOKENS if args.tokens == "ALL" else args.tokens.split(",")
    
    optimizer = RobustStrategyOptimizer(
        mode=args.mode,
        iterations=args.iter,
        batch_size=args.batch_size,
        workers=args.workers,
        turbo=args.turbo
    )
    
    optimizer.run_robust_discovery(target_tokens=tokens, periods=args.periods)
