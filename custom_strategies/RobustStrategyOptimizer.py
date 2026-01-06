
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
    
    def __init__(self, mode="robust", days=365, iterations=100, batch_size=250, workers=4, turbo=True, strategy="ma_cross"):
        self.mode = mode
        self.turbo = turbo
        self.days = days
        self.iterations = iterations
        self.batch_size = batch_size
        self.workers = workers
        self.strategy = strategy  # "ma_cross" or "rsi_reversion"
        self.report_id = datetime.now().strftime("%Y%m%d_%H%M")
        self.results_cache = []
        
        # API Endpoints
        self.api_url = API_URL_TURBO if turbo else API_URL
        self.gc_url = f"http://{API_HOST}:8000/backtesting/gc"
        
        self.stats = {
            "batches_sent": 0,
            "batches_received": 0,
            "sims_completed": 0,
            "sims_error": 0,
            "total_tasks": 0,
            "best_robust_score": -999.0,
            "best_pair": "N/A",
            "start_time": time.time(),
        }
        self.stats_lock = threading.Lock()
        self.stop_event = threading.Event()
        
        os.makedirs(OUTPUT_DIR, exist_ok=True)

    def status_reporter(self, interval=300):
        """Background thread to report status every 5 minutes."""
        while not self.stop_event.is_set():
            time.sleep(interval)
            if self.stop_event.is_set(): break
            
            with self.stats_lock:
                elapsed = time.time() - self.stats["start_time"]
                completed = self.stats["sims_completed"]
                total = self.stats["total_tasks"]
                progress = (completed / total * 100) if total > 0 else 0
                throughput = (completed / elapsed * 60) if elapsed > 0 else 0
                
                print(f"\n📢 [STATUS] {datetime.now().strftime('%H:%M:%S')}")
                print(f"   Progress: {progress:.1f}% ({completed}/{total})")
                print(f"   Throughput: {throughput:.1f} sims/min")
                print(f"   Errors: {self.stats['sims_error']}")
                print(f"   Best Score: {self.stats['best_robust_score']:.2f} ({self.stats['best_pair']})\n")
    
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
        Period 3: offset 1095-1460 days ago (roughly Y-3)f v
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
                "period": step + 1,
                "step": step + 1,
                "train_start_ts": int(anchor_date.timestamp()),
                "train_end_ts": int(train_end.timestamp()),
                "test_start_ts": int(test_start.timestamp()),
                "test_end_ts": int(test_end.timestamp()),
                "train_label": f"{anchor_date.strftime('%Y-%m')} to {train_end.strftime('%Y-%m')}",
                "test_label": f"{test_start.strftime('%Y-%m')} to {test_end.strftime('%Y-%m')}",
                "description": f"OOS {test_start.strftime('%Y-%m')} to {test_end.strftime('%Y-%m')}"
            })
            
            # Expand training window by 6 months
            train_end = train_end + timedelta(days=180)
            step += 1
        
        return windows
    
    # =========================================================================
    # ROBUSTNESS SCORING
    # =========================================================================
    
    def calculate_wfe(self, is_pnl, oos_pnl):
        """
        Walk-Forward Efficiency: OOS performance / Annualized IS performance
        WFE > 0.5: Good stability
        """
        if is_pnl <= 0: return 0.0
        # Simple ratio: How much of the 'promise' did it deliver in OOS?
        eff = oos_pnl / is_pnl if is_pnl > 0 else 0
        return max(0, min(1.2, eff))

    def calculate_robust_score(self, period_results: dict, mode="robust"):
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
        
        # AWFO Specific Logic: Average WFE
        wfe_scores = [pr.get("wfe", 0) for pr in period_results.values() if "wfe" in pr]
        avg_wfe = np.mean(wfe_scores) if wfe_scores else 1.0
        
        # Consistency Filter
        consistency_ratio = profitable_periods / n_periods

        # Stability penalty (lower variance = better)
        if abs(mean_pnl) > 0.001:
            cv = std_pnl / abs(mean_pnl)  # Coefficient of variation
            stability = max(0, 1 - cv)
        else:
            stability = 0

        # Final score: Mean * Consistency * Stability * WFE
        score = mean_pnl * np.sqrt(consistency_ratio) * (0.5 + 0.5 * stability)
        if mode == "awfo":
            score *= (0.2 + 0.8 * avg_wfe)
        
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
            "stability": stability,
            "avg_wfe": avg_wfe
        }
        
        # Check robustness criteria
        is_valid = consistency_ratio >= 0.50
        if not is_valid:
            stats["reason"] = f"Only {profitable_periods}/{n_periods} periods profitable"

        return score if is_valid else 0, is_valid, stats
    
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
        """Build a standard MA Cross backtest configuration."""
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
    
    def _build_rsi_config(self, pair, rsi_period, rsi_oversold, rsi_overbought, bb_period, bb_std, use_trend_filter, trend_ma_period, interval, sl, tp):
        # Scale time_limit with interval (e.g., 24 bars)
        limit_seconds = 86400 if interval == "1h" else 345600  # 24h or 96h
        
        return {
            "connector_name": "binance",
            "controller_name": "rsi_reversion_strategy",
            "controller_type": "custom",
            "trading_pair": pair,
            "rsi_period": rsi_period,
            "rsi_oversold": rsi_oversold,
            "rsi_overbought": rsi_overbought,
            "bb_period": bb_period,
            "bb_std": bb_std,
            "use_trend_filter": use_trend_filter,
            "trend_ma_period": trend_ma_period,
            "indicator_interval": interval,
            "stop_loss": sl,
            "take_profit": tp,
            "time_limit": limit_seconds,
            "total_amount_quote": 100,
            "use_compounding": True,
            "leverage": 1,
            "max_executors_per_side": 2,
            "cooldown_time": 300,
            "candles_config": []
        }
    
    def _build_grid_config(self, pair, bb_period, bb_std, entry_threshold, use_trend_filter, trend_ma_period, interval, sl, tp):
        """Build BB Grid strategy configuration."""
        limit_seconds = 86400 if interval == "1h" else 345600
        return {
            "connector_name": "binance",
            "controller_name": "bb_grid_strategy",
            "controller_type": "custom",
            "trading_pair": pair,
            "bb_period": bb_period,
            "bb_std": bb_std,
            "entry_threshold": entry_threshold,
            "use_trend_filter": use_trend_filter,
            "trend_ma_period": trend_ma_period,
            "indicator_interval": interval,
            "stop_loss": sl,
            "take_profit": tp,
            "time_limit": limit_seconds,
            "total_amount_quote": 100,
            "use_compounding": True,
            "leverage": 1,
            "max_executors_per_side": 2,
            "cooldown_time": 300,
            "candles_config": []
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
                completed = sum(1 for r in results if "error" not in r)
                errors = sum(1 for r in results if "error" in r)
                self.stats["sims_completed"] += completed
                self.stats["sims_error"] += errors
                
                if errors > 0 and self.stats["sims_error"] < 1000: # Only print first few errors to avoid log spam
                    for r in results:
                        if "error" in r:
                            print(f"\n❌ API Error Sample: {r['error'][:500]}...")
                            break
            
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
        if self.strategy == "rsi_reversion":
            return self._generate_rsi_config(pair)
        elif self.strategy == "bb_grid":
            return self._generate_grid_config(pair)
        else:
            return self._generate_ma_cross_config(pair)

    def _generate_grid_config(self, pair):
        """Generate random BB Grid configuration."""
        interval = random.choice(["1h", "4h"])
        # Relaxed settings to ensure more trades
        bb_period = random.choice([20, 30, 50, 100])
        bb_std = random.choice([1.0, 1.5, 2.0, 2.5])
        entry_threshold = random.choice([0.3, 0.5, 0.7, 0.9])
        use_trend_filter = random.choice([True, False, False]) # Prefer False
        trend_ma_period = random.choice([100, 200, 300])
        sl = random.choice([0.03, 0.05, 0.07])
        tp = random.choice([0.02, 0.03, 0.05, 0.08]) # Lower TP for grid consistency
        
        return self._build_grid_config(pair, bb_period, bb_std, entry_threshold, use_trend_filter, trend_ma_period, interval, sl, tp)
    
    def _generate_ma_cross_config(self, pair):
        """Generate random MA Cross configuration."""
        interval = random.choice(["1h", "4h"])
        fast = random.choice(list(range(5, 60, 5)))
        slow = random.choice([s for s in range(20, 200, 10) if s > fast + 10])
        sl = random.choice([0.02, 0.03, 0.04, 0.05, 0.07, 0.10])
        tp = random.choice([0.02, 0.05, 0.08, 0.10, 0.15])
        
        return self._build_config(pair, fast, slow, interval, sl, tp)
    
    def _generate_rsi_config(self, pair):
        """Generate random RSI Reversion configuration."""
        interval = random.choice(["1h", "4h"])
        rsi_period = random.choice([7, 10, 14, 21])
        rsi_oversold = random.choice([20, 25, 30, 35])
        rsi_overbought = random.choice([65, 70, 75, 80])
        bb_period = random.choice([15, 20, 25])
        bb_std = random.choice([1.5, 2.0, 2.5])
        use_trend_filter = random.choice([True, False])
        trend_ma_period = random.choice([50, 100, 150, 200])
        sl = random.choice([0.02, 0.03, 0.04, 0.05])
        tp = random.choice([0.03, 0.05, 0.07, 0.10])
        
        return self._build_rsi_config(pair, rsi_period, rsi_oversold, rsi_overbought, bb_period, bb_std, use_trend_filter, trend_ma_period, interval, sl, tp)
    
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
        all_tasks = []
        if self.mode == "awfo":
            windows = self.get_anchored_wfo_windows(anchor_year=2021)
            print(f"📦 Squashing into {len(all_candidates) * len(windows) * 2:,d} tasks (AWFO: IS+OOS)...")
            for cand_idx, cand in enumerate(all_candidates):
                for win in windows:
                    # In-Sample Task
                    all_tasks.append({
                        "cand_idx": cand_idx, "step": win["step"], "type": "is",
                        "start_time": win["train_start_ts"], "end_time": win["train_end_ts"],
                        "config": cand["config"], "backtesting_resolution": "1m", "trade_cost": 0.0006
                    })
                    # Out-of-Sample Task
                    all_tasks.append({
                        "cand_idx": cand_idx, "step": win["step"], "type": "oos",
                        "start_time": win["test_start_ts"], "end_time": win["test_end_ts"],
                        "config": cand["config"], "backtesting_resolution": "1m", "trade_cost": 0.0006
                    })
        else:
            windows = self.get_multi_year_windows(periods=periods)
            print(f"📦 Squashing into {len(all_candidates) * periods:,d} backtest tasks...")
            for cand_idx, cand in enumerate(all_candidates):
                for window in windows:
                    all_tasks.append({
                        "cand_idx": cand_idx, "period": window["period"], "type": "normal",
                        "start_time": window["start_ts"], "end_time": window["end_ts"],
                        "config": cand["config"], "backtesting_resolution": "1m", "trade_cost": 0.0006
                    })
        
        # 3. PROCESS IN PARALLEL BATCHES
        print(f"⚡ Dispatched to {self.workers} workers. Processing...")
        self.stats["total_tasks"] = len(all_tasks)
        self.stats["start_time"] = time.time()
        
        # Start background status reporter
        reporter_thread = threading.Thread(target=self.status_reporter, args=(300,))
        reporter_thread.daemon = True
        reporter_thread.start()
        
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

        # Stop reporter
        self.stop_event.set()

        # 4. RECONSTRUCT CANDIDATE RESULTS
        print(f"\n🧩 Reconstructing results and scoring...")
        candidate_results = [{} for _ in range(len(all_candidates))]
        
        for i, task in enumerate(all_tasks):
            cand_idx = task["cand_idx"]
            res = raw_results[i]
            
            if not res or "error" in res: continue
            
            p_data = {
                "pnl": float(res.get("net_pnl", 0)) * 100,
                "trades": int(res.get("total_positions", 0)),
                "accuracy": float(res.get("accuracy", 0)) * 100,
                "sharpe": float(res.get("sharpe_ratio", 0) or 0),
                "max_dd": float(res.get("max_drawdown_pct", 0)) * 100,
                "profit_factor": float(res.get("profit_factor", 0)),
            }
            
            if self.mode == "awfo":
                step = task["step"]
                if step not in candidate_results[cand_idx]: candidate_results[cand_idx][step] = {"is": {}, "oos": {}}
                candidate_results[cand_idx][step][task["type"]] = p_data
            else:
                candidate_results[cand_idx][task["period"]] = p_data

        # 4.5 AWFO POST-PROCESSING: Calculate WFE
        if self.mode == "awfo":
            for cand_idx in range(len(all_candidates)):
                for step, step_data in candidate_results[cand_idx].items():
                    is_pnl = step_data["is"].get("pnl", 0)
                    oos_pnl = step_data["oos"].get("pnl", 0)
                    step_data["pnl"] = oos_pnl # Standardizing for robust scorer to use OOS
                    step_data["trades"] = step_data["oos"].get("trades", 0)
                    step_data["accuracy"] = step_data["oos"].get("accuracy", 0)
                    step_data["sharpe"] = step_data["oos"].get("sharpe", 0)
                    step_data["max_dd"] = step_data["oos"].get("max_dd", 0)
                    step_data["wfe"] = self.calculate_wfe(is_pnl, oos_pnl)

        # 5. SCORING AND FILTERING
        robust_results = []
        csv_file = f"{OUTPUT_DIR}/robust_{self.report_id}.csv"
        
        num_periods = len(windows)
        with open(csv_file, 'w') as f:
            period_headers = ','.join([f"P{i+1}_PnL" for i in range(num_periods)])
            if self.strategy == "rsi_reversion":
                f.write(f"Pair,Interval,RSI_Period,RSI_Oversold,RSI_Overbought,BB_Period,Trend_MA,SL,TP,{period_headers},Mean_PnL,Trades,Accuracy,Sharpe,MaxDD,Score,Valid\n")
            elif self.strategy == "bb_grid":
                f.write(f"Pair,Interval,BB_Period,BB_Std,Threshold,Trend_MA,SL,TP,{period_headers},Mean_PnL,Trades,Accuracy,Sharpe,MaxDD,Score,Valid\n")
            else:
                f.write(f"Pair,Interval,Fast,Slow,SL,TP,{period_headers},Mean_PnL,Trades,Accuracy,Sharpe,MaxDD,Score,Valid\n")
            
            for i, cand in enumerate(all_candidates):
                period_pnls = candidate_results[i]
                score, is_valid, stats = self.calculate_robust_score(period_pnls, mode=self.mode)
                
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
                pnl_values = ','.join([f"{period_pnls.get(j+1, {}).get('pnl', 0):.2f}" for j in range(num_periods)])
                
                if self.strategy == "rsi_reversion":
                    trend_val = config['trend_ma_period'] if config['use_trend_filter'] else 0
                    row = (f"{pair},{config['indicator_interval']},{config['rsi_period']},"
                           f"{config['rsi_oversold']},{config['rsi_overbought']},{config['bb_period']},"
                           f"{trend_val},{config['stop_loss']},{config['take_profit']},"
                           f"{pnl_values},"
                           f"{stats.get('mean_pnl', 0):.2f},{stats.get('total_trades', 0)},"
                           f"{stats.get('mean_accuracy', 0):.1f},{stats.get('mean_sharpe', 0):.2f},"
                           f"{stats.get('max_drawdown', 0):.1f},{score:.2f},{is_valid}\n")
                elif self.strategy == "bb_grid":
                    trend_val = config['trend_ma_period'] if config['use_trend_filter'] else 0
                    row = (f"{pair},{config['indicator_interval']},{config['bb_period']},"
                           f"{config['bb_std']},{config['entry_threshold']},"
                           f"{trend_val},{config['stop_loss']},{config['take_profit']},"
                           f"{pnl_values},"
                           f"{stats.get('mean_pnl', 0):.2f},{stats.get('total_trades', 0)},"
                           f"{stats.get('mean_accuracy', 0):.1f},{stats.get('mean_sharpe', 0):.2f},"
                           f"{stats.get('max_drawdown', 0):.1f},{score:.2f},{is_valid}\n")
                else:
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
        self._generate_robust_report(robust_results, elapsed, periods, target_tokens, windows)
        
        return robust_results
    
    def _generate_robust_report(self, results, elapsed, periods, tokens, windows):
        """Generate markdown report for robust discovery."""
        report_file = f"{OUTPUT_DIR}/robust_report_{self.report_id}.md"
        
        # Get window info for report
        sample_windows = windows
        
        with open(report_file, 'w') as f:
            f.write("# 🔬 Robust Strategy Discovery Report\n\n")
            f.write(f"**Generated**: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
            f.write(f"**Optimization Mode**: {self.mode.upper()}\n")
            f.write(f"**Periods Tested**: {len(sample_windows)}\n")
            for w in sample_windows:
                if self.mode == "awfo":
                    f.write(f"- Step {w['step']}: IS ({w['train_label']}) -> OOS ({w['test_label']})\n")
                else:
                    f.write(f"- {w['label']}: {w['description']}\n")
            f.write(f"\n**Total Candidates**: {self.iterations * len(tokens)}\n")
            f.write(f"**Total Backtests**: {self.stats['sims_completed'] + self.stats['sims_error']}\n")
            f.write(f"**Valid Robust Strategies**: {len(results)}\n")
            f.write(f"**Runtime**: {int(elapsed//60)}m {int(elapsed%60)}s\n")
            f.write(f"**Avg Time/Backtest**: {(elapsed / max(1, self.stats['sims_completed'] + self.stats['sims_error'])):.4f}s\n")
            f.write(f"**Throughput**: {((self.stats['sims_completed'] + self.stats['sims_error']) / max(1, elapsed)):.2f} sims/sec\n\n")
            
            f.write("---\n\n")
            f.write("## Methodology\n\n")
            if self.mode == "awfo":
                f.write("Using **Anchored Walk-Forward Optimization (AWFO)**.\n")
                f.write("- **IS**: In-Sample (Training) data.\n")
                f.write("- **OOS**: Out-of-Sample (Validation) data. Results below are based on OOS.\n")
                f.write("- **WFE**: Walk-Forward Efficiency (OOS PnL / IS PnL).\n")
                f.write("**Criteria for 'Valid'**: Profitable in >= 50% of rolling OOS periods.\n")
                f.write("**Scoring**: `Mean_OOS_PnL × √Consistency × Stability × WFE_Factor`\n\n")
            else:
                f.write("Each candidate was tested on ALL specified periods independently.\n")
                f.write("**Criteria for 'Valid'**: Profitable in >= 50% of periods\n")
                f.write("**Scoring**: `Mean_PnL × √Consistency × (0.5 + 0.5×Stability)`\n\n")
            
            f.write("---\n\n")
            f.write("## 🏆 Top Robust Strategies\n\n")
            
            if results:
                # Table with detailed stats
                if self.mode == "awfo":
                    f.write("| Rank | Pair | Interval | Config (Short) | SL/TP | Mean OOS | WFE | Trades | Accuracy | Sharpe | Score |\n")
                    f.write("|------|------|----------|----------------|-------|----------|-----|--------|----------|--------|-------|\n")
                else:
                    f.write("| Rank | Pair | Interval | Config (Short) | SL/TP | Mean PnL | Trades | Accuracy | Sharpe | MaxDD | Score |\n")
                    f.write("|------|------|----------|----------------|-------|----------|--------|----------|--------|-------|-------|\n")
                
                for i, r in enumerate(results[:20]):
                    cfg = r["config"]
                    s = r["stats"]
                    
                    if self.strategy == "rsi_reversion":
                        config_str = f"RSI{cfg['rsi_period']} ({cfg['rsi_oversold']}/{cfg['rsi_overbought']})"
                    elif self.strategy == "bb_grid":
                        config_str = f"BB{cfg['bb_period']}/{cfg['bb_std']} (T:{cfg['entry_threshold']})"
                    else:
                        config_str = f"MA{cfg['fast_ma']}/{cfg['slow_ma']}"
                        
                    if self.mode == "awfo":
                        f.write(f"| {i+1} | {r['pair']} | {cfg['indicator_interval']} | "
                               f"{config_str} | "
                               f"{cfg['stop_loss']*100:.0f}%/{cfg['take_profit']*100:.0f}% | "
                               f"{s.get('mean_pnl', 0):+.1f}% | {s.get('avg_wfe', 0):.2f} | "
                               f"{s.get('total_trades', 0)} | {s.get('mean_accuracy', 0):.1f}% | "
                               f"{s.get('mean_sharpe', 0):.2f} | {r['robust_score']:.1f} |\n")
                    else:
                        f.write(f"| {i+1} | {r['pair']} | {cfg['indicator_interval']} | "
                               f"{config_str} | "
                               f"{cfg['stop_loss']*100:.0f}%/{cfg['take_profit']*100:.0f}% | "
                               f"{s.get('mean_pnl', 0):+.1f}% | {s.get('total_trades', 0)} | "
                               f"{s.get('mean_accuracy', 0):.1f}% | {s.get('mean_sharpe', 0):.2f} | "
                               f"{s.get('max_drawdown', 0):.1f}% | {r['robust_score']:.1f} |\n")
                
                # Period detail table for top 5
                f.write("\n### 📈 Period Detail for Top 5\n\n")
                for i, r in enumerate(results[:5]):
                    cfg = r["config"]
                    pr = r["period_results"]
                    
                    if self.strategy == "rsi_reversion":
                        config_desc = f"RSI={cfg['rsi_period']} ({cfg['rsi_oversold']}/{cfg['rsi_overbought']}) BB={cfg['bb_period']}"
                    else:
                        config_desc = f"Fast={cfg['fast_ma']}/Slow={cfg['slow_ma']}"
                        
                    f.write(f"\n**{i+1}. {r['pair']} {cfg['indicator_interval']} {config_desc}**\n\n")
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
                f.write(f"- The selected strategy may not work well across all market conditions\n")
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
                if self.strategy == "rsi_reversion":
                    config_desc = f"RSI{cfg['rsi_period']}"
                else:
                    config_desc = f"MA{cfg['fast_ma']}/{cfg['slow_ma']}"
                    
                print(f"   {i+1}. {r['pair']} {cfg['indicator_interval']} "
                      f"{config_desc} | "
                      f"Score={r['robust_score']:.1f} | "
                      f"Mean={r['stats'].get('mean_pnl', 0):.1f}% | "
                      f"WFE={r['stats'].get('avg_wfe', 1.0):.2f}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Robust Strategy Optimizer")
    parser.add_argument("--mode", type=str, choices=["robust", "awfo"], default="robust")
    parser.add_argument("--iter", type=int, default=50, help="Candidates per token")
    parser.add_argument("--periods", type=int, default=4, help="Number of annual periods to test")
    parser.add_argument("--tokens", type=str, default="ALL", help="Tokens to test")
    parser.add_argument("--batch_size", type=int, default=250)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--turbo", action="store_true", default=True)
    parser.add_argument("--strategy", type=str, choices=["ma_cross", "rsi_reversion", "bb_grid"], default="ma_cross", help="Strategy to optimize")
    
    args = parser.parse_args()
    
    if args.tokens == "ALL":
        tokens = TOP_10_TOKENS
    elif "," in args.tokens:
        tokens = [t.strip() for t in args.tokens.split(",")]
    else:
        tokens = [t.strip() for t in args.tokens.split()]
    
    optimizer = RobustStrategyOptimizer(
        mode=args.mode,
        iterations=args.iter,
        batch_size=args.batch_size,
        workers=args.workers,
        turbo=args.turbo,
        strategy=args.strategy
    )
    
    optimizer.run_robust_discovery(target_tokens=tokens, periods=args.periods)
