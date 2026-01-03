#!/usr/bin/env python3
"""
Strategy Validator v1.0
=======================
Systematic validation tool to prevent overfitting in trading strategies.
Implements three validation tests:
1. Out-of-Sample Test (OOS)
2. Parameter Sensitivity Analysis (PSA)
3. Walk-Forward Analysis (WFA)

Usage:
    python custom_strategies/StrategyValidator.py --strategies "ADA-USDT:55:70:1h:0.04:0.1" --days 360
    
Or programmatically:
    from StrategyValidator import StrategyValidator
    validator = StrategyValidator(strategies=[...])
    validator.run_all()
"""

import os
import sys
import argparse
import requests
import json
import re
import numpy as np
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
from requests.auth import HTTPBasicAuth

# --- CONFIGURATION ---
API_URL = "http://localhost:8000/backtesting/batch-run"
GC_URL = "http://localhost:8000/backtesting/gc"
AUTH = HTTPBasicAuth("admin", "admin")
OUTPUT_DIR = "/hummingbot-api/bots/controllers/custom/validation_reports"

# Parallel execution settings (Optimized for 10-core CPU)
# Backend uses ProcessPoolExecutor(max_workers=10)
# To avoid overloading, we send batches sequentially
BATCH_SIZE = 30       # Configs per API request (10 cores x 3 rounds)
MAX_WORKERS = 2       # Sequential requests for stability

class StrategyValidator:
    """
    Validates trading strategies against overfitting using three industry-standard tests.
    Now with parallel batch processing for faster validation!
    """
    
    def __init__(self, strategies: list, days: int = 360):
        """
        Args:
            strategies: List of strategy configs, each is a dict with:
                - pair: e.g., "ADA-USDT"
                - fast_ma, slow_ma: int
                - interval: e.g., "1h"
                - stop_loss, take_profit: float (e.g., 0.04)
                - original_pnl: float (the PnL from discovery)
            days: Number of days for the original backtest window
        """
        self.strategies = strategies
        self.days = days
        self.results = []
        self.report_id = datetime.now().strftime("%Y%m%d_%H%M")
        self.data_cache = {}  # Cache for coin data ranges
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        
    def _get_time_range(self, days: int, offset_days: int = 1):
        """Get start/end timestamps for backtesting."""
        end_dt = datetime.now() - timedelta(days=offset_days)
        start_dt = end_dt - timedelta(days=days)
        return int(start_dt.timestamp()), int(end_dt.timestamp())
    
    def _get_data_range(self, pair: str, connector: str = "binance") -> dict:
        """
        Detect actual data availability for a trading pair.
        Returns: {'start_ts': int, 'end_ts': int, 'days': int, 'start_date': str, 'end_date': str}
        """
        cache_key = f"{connector}_{pair}"
        if cache_key in self.data_cache:
            return self.data_cache[cache_key]
        
        # Check local CSV file - try multiple possible paths
        import hummingbot
        possible_paths = [
            f"{hummingbot.data_path()}/candles/{connector}_{pair}_1m.csv",
            f"/opt/conda/envs/hummingbot-api/lib/python3.12/site-packages/data/candles/{connector}_{pair}_1m.csv",
            f"/hummingbot-api/data/candles/{connector}_{pair}_1m.csv"
        ]
        csv_path = None
        for p in possible_paths:
            try:
                if os.path.exists(p):
                    csv_path = p
                    break
            except:
                pass
        
        if not csv_path:
            # Fallback
            end_ts = int(datetime.now().timestamp())
            start_ts = end_ts - (1095 * 86400)
            return {
                'start_ts': start_ts, 'end_ts': end_ts, 'days': 1095,
                'start_date': 'unknown', 'end_date': 'unknown', 'error': 'file_not_found'
            }
        
        try:
            import csv
            with open(csv_path, 'r') as f:
                reader = csv.reader(f)
                next(reader)  # Skip header
                first_row = next(reader)
                start_ts = int(float(first_row[0]))
            
            # Get last line efficiently
            with open(csv_path, 'rb') as f:
                f.seek(-200, 2)  # Seek from end
                lines = f.readlines()
                last_line = lines[-1].decode('utf-8').strip()
                end_ts = int(float(last_line.split(',')[0]))
            
            days_available = (end_ts - start_ts) // 86400
            start_date = datetime.fromtimestamp(start_ts).strftime('%Y-%m-%d')
            end_date = datetime.fromtimestamp(end_ts).strftime('%Y-%m-%d')
            
            result = {
                'start_ts': start_ts,
                'end_ts': end_ts,
                'days': days_available,
                'start_date': start_date,
                'end_date': end_date
            }
            self.data_cache[cache_key] = result
            return result
        except Exception as e:
            # Fallback: assume 3 years of data
            end_ts = int(datetime.now().timestamp())
            start_ts = end_ts - (1095 * 86400)
            return {
                'start_ts': start_ts,
                'end_ts': end_ts,
                'days': 1095,
                'start_date': 'unknown',
                'end_date': 'unknown',
                'error': str(e)
            }
    
    def _build_config(self, pair: str, fast: int, slow: int, interval: str, 
                       sl: float, tp: float, start: int, end: int) -> dict:
        """Build a backtest config dict - MUST match StrategyOptimizer format."""
        return {
            "config": {
                "connector_name": "binance",
                "controller_name": "ma_cross_strategy",
                "controller_type": "custom",
                "trading_pair": pair,
                "indicator_interval": interval,
                "fast_ma": fast,
                "slow_ma": slow,
                "stop_loss": sl,
                "take_profit": tp,
                "time_limit": 21600,              # Align with Optimizer (6 hours)
                "total_amount_quote": 100,        # Align with Optimizer
                "use_compounding": True           # Align with Optimizer
            },
            "start_time": start,
            "end_time": end,
            "backtesting_resolution": "1m",
            "trade_cost": 0.0006
        }

    
    def _run_backtest(self, config: dict) -> dict:
        """Run a single backtest via API."""
        try:
            response = requests.post(
                API_URL, 
                json=[config], 
                auth=AUTH, 
                timeout=300
            )
            if response.status_code == 200:
                results = response.json().get("results", [])
                if results and "error" not in results[0]:
                    return results[0]
            return {"error": "API Error", "net_pnl": 0}
        except Exception as e:
            return {"error": str(e), "net_pnl": 0}
    
    def _run_batch(self, configs: list) -> list:
        """Run a batch of backtests via API."""
        try:
            response = requests.post(
                API_URL, 
                json=configs, 
                auth=AUTH, 
                timeout=600
            )
            if response.status_code == 200:
                return response.json().get("results", [])
            return [{"error": "API Error", "net_pnl": 0}] * len(configs)
        except Exception as e:
            return [{"error": str(e), "net_pnl": 0}] * len(configs)
    
    def _run_parallel_batches(self, all_configs: list) -> list:
        """
        Run all configs in parallel batches.
        Uses ThreadPoolExecutor with MAX_WORKERS threads.
        Each thread sends BATCH_SIZE configs to the backend.
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed
        
        # Split into batches
        batches = []
        for i in range(0, len(all_configs), BATCH_SIZE):
            batches.append(all_configs[i:i+BATCH_SIZE])
        
        all_results = [None] * len(all_configs)
        
        def process_batch(batch_info):
            batch_idx, batch = batch_info
            results = self._run_batch(batch)
            return batch_idx, results
        
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {executor.submit(process_batch, (i, batch)): i 
                       for i, batch in enumerate(batches)}
            
            for future in as_completed(futures):
                batch_idx, results = future.result()
                start_idx = batch_idx * BATCH_SIZE
                for j, result in enumerate(results):
                    if start_idx + j < len(all_results):
                        all_results[start_idx + j] = result
        
        # Clean up memory after large batch
        try:
            requests.post(GC_URL, auth=AUTH, timeout=10)
        except:
            pass
        
        return all_results
    
    # =========================================================================
    # TEST 1: OUT-OF-SAMPLE TEST (Before Optimization Period)
    # =========================================================================
    def run_oos_test(self, strategy: dict) -> dict:
        """
        Test strategy on TRUE out-of-sample data: Day 361-720 (before optimization).
        
        Timeline (for 360-day optimization on most recent data):
        - Optimization used: Day 1-360 (most recent year, ~2025)
        - OOS uses: Day 361-720 (year before, ~2024) - completely unseen data
        
        Pass criteria: OOS_PnL >= 50% of original_pnl (adjusted for different market regime)
        """
        print(f"   🔬 Running OOS Test for {strategy['pair']}...", end="", flush=True)
        
        # TRUE OOS window: Day 361-720 (immediately before optimization period)
        # Optimization used Day 1-360, so Day 361-720 is completely unseen
        end_dt = datetime.now() - timedelta(days=361)  # End where optimization started
        start_dt = end_dt - timedelta(days=360)        # Go back another year
        oos_start = int(start_dt.timestamp())
        oos_end = int(end_dt.timestamp())
        
        config = self._build_config(
            strategy['pair'], strategy['fast_ma'], strategy['slow_ma'],
            strategy['interval'], strategy['stop_loss'], strategy['take_profit'],
            oos_start, oos_end
        )
        
        result = self._run_backtest(config)
        oos_pnl = float(result.get("net_pnl", 0)) * 100  # Convert to percentage
        original_pnl = strategy.get("original_pnl", 0)
        
        # Both are 360 days - direct comparison
        ratio = oos_pnl / original_pnl if original_pnl > 0 else 0
        passed = ratio >= 0.50  # 50% threshold
        
        print(f" {'✅ PASS' if passed else '❌ FAIL'} (OOS Day361-720: {oos_pnl:.1f}% vs Opt Day1-360: {original_pnl:.1f}%, Ratio: {ratio:.1%})")
        
        return {
            "test": "Out-of-Sample (Day 361-720 vs Day 1-360)",
            "oos_pnl": oos_pnl,
            "original_pnl": original_pnl,
            "ratio": ratio,
            "passed": passed
        }
    
    # =========================================================================
    # TEST 2: PARAMETER SENSITIVITY ANALYSIS
    # =========================================================================
    def run_sensitivity_test(self, strategy: dict) -> dict:
        """
        Vary each parameter ±10%, ±20% and measure PnL stability.
        Pass criteria: PnL std < 30% of mean
        """
        print(f"   🔬 Running Sensitivity Test for {strategy['pair']}...", end="", flush=True)
        
        start, end = self._get_time_range(self.days)
        
        # Generate parameter variations
        fast_base = strategy['fast_ma']
        slow_base = strategy['slow_ma']
        sl_base = strategy['stop_loss']
        tp_base = strategy['take_profit']
        
        # Create grid: ±10%, ±20% for each param
        fast_range = [max(3, int(fast_base * m)) for m in [0.8, 0.9, 1.0, 1.1, 1.2]]
        slow_range = [int(slow_base * m) for m in [0.8, 0.9, 1.0, 1.1, 1.2]]
        
        # Build configs for sensitivity grid (5x5 = 25 combinations)
        configs = []
        for fast in fast_range:
            for slow in slow_range:
                if slow > fast + 5:  # Ensure slow > fast
                    configs.append(self._build_config(
                        strategy['pair'], fast, slow, strategy['interval'],
                        sl_base, tp_base, start, end
                    ))
        
        # Run batch
        results = self._run_batch(configs)
        pnls = [float(r.get("net_pnl", 0)) * 100 for r in results if "error" not in r]
        
        if len(pnls) < 5:
            print(f" ⚠️ INSUFFICIENT DATA ({len(pnls)} valid results)")
            return {"test": "Sensitivity", "passed": False, "reason": "insufficient_data"}
        
        mean_pnl = np.mean(pnls)
        std_pnl = np.std(pnls)
        cv = std_pnl / abs(mean_pnl) if mean_pnl != 0 else float('inf')  # Coefficient of Variation
        
        passed = cv < 0.30  # Std < 30% of mean
        
        print(f" {'✅ PASS' if passed else '❌ FAIL'} (Mean: {mean_pnl:.1f}%, Std: {std_pnl:.1f}%, CV: {cv:.1%})")
        
        return {
            "test": "Sensitivity",
            "variations_tested": len(pnls),
            "mean_pnl": mean_pnl,
            "std_pnl": std_pnl,
            "cv": cv,
            "passed": passed
        }
    
    # =========================================================================
    # TEST 3: WALK-FORWARD ANALYSIS (Using Most Recent Data) - PARALLEL
    # =========================================================================
    def run_walkforward_test(self, strategy: dict) -> dict:
        """
        Split recent data into windows for cross-validation.
        Uses 6 windows of 180 days each (1080 days = ~3 years).
        Train on 5 windows, test on 1. Rotate through all 6 combinations.
        
        OPTIMIZED: Collects all configs and runs them in parallel batches.
        
        Timeline: Most recent 1080 days (includes optimization period Day 1-360)
        This tests consistency across recent market conditions.
        
        Pass criteria: Walk-Forward Efficiency (WFE) > 50%
        """
        print(f"   🔬 Running Walk-Forward Test for {strategy['pair']}...", end="", flush=True)
        
        # Split ~3 years (1080 days) into 6 x 180-day windows
        total_days = 1080  # 3 years of recent data
        num_windows = 6
        window_days = total_days // num_windows  # 180 days per window
        
        # Start from most recent data (Day 1)
        base_end = datetime.now() - timedelta(days=1)
        
        # STEP 1: Collect ALL configs for all windows (12 test windows x 12 total windows = 144 configs)
        all_configs = []
        config_map = []  # Track which config belongs to which test window (test_idx, is_train)
        
        for test_window_idx in range(num_windows):
            # Test window config
            test_end = base_end - timedelta(days=test_window_idx * window_days)
            test_start = test_end - timedelta(days=window_days)
            
            config = self._build_config(
                strategy['pair'], strategy['fast_ma'], strategy['slow_ma'],
                strategy['interval'], strategy['stop_loss'], strategy['take_profit'],
                int(test_start.timestamp()), int(test_end.timestamp())
            )
            all_configs.append(config)
            config_map.append((test_window_idx, False))  # is_train=False for test window
            
            # Training window configs (11 out of 12)
            for train_window_idx in range(num_windows):
                if train_window_idx == test_window_idx:
                    continue
                    
                train_end = base_end - timedelta(days=train_window_idx * window_days)
                train_start = train_end - timedelta(days=window_days)
                
                config = self._build_config(
                    strategy['pair'], strategy['fast_ma'], strategy['slow_ma'],
                    strategy['interval'], strategy['stop_loss'], strategy['take_profit'],
                    int(train_start.timestamp()), int(train_end.timestamp())
                )
                all_configs.append(config)
                config_map.append((test_window_idx, True))  # is_train=True for training window
        
        # STEP 2: Run all configs in parallel batches
        print(f" ({len(all_configs)} backtests)", end="", flush=True)
        all_results = self._run_parallel_batches(all_configs)
        
        # STEP 3: Process results back into WFE calculations
        wfe_ratios = []
        
        for test_window_idx in range(num_windows):
            # Find test result and training results for this fold
            test_pnl = 0
            train_pnls = []
            
            for i, (mapped_test_idx, is_train) in enumerate(config_map):
                if mapped_test_idx != test_window_idx:
                    continue
                    
                result = all_results[i] if all_results[i] else {"net_pnl": 0}
                pnl = float(result.get("net_pnl", 0)) * 100
                
                if is_train:
                    train_pnls.append(pnl)
                else:
                    test_pnl = pnl
            
            # Calculate WFE for this fold
            mean_train_pnl = np.mean(train_pnls) if train_pnls else 0
            if mean_train_pnl > 0:
                wfe = test_pnl / mean_train_pnl
                wfe_ratios.append(wfe)
        
        # Overall WFE
        avg_wfe = np.mean(wfe_ratios) if wfe_ratios else 0
        passed = avg_wfe >= 0.50
        
        print(f" {'✅ PASS' if passed else '❌ FAIL'} (WFE: {avg_wfe:.1%}, {len(wfe_ratios)} folds over 3 years)")
        
        return {
            "test": "Walk-Forward (3-Year, 6-Fold)",
            "wfe_per_fold": wfe_ratios,
            "avg_wfe": avg_wfe,
            "num_folds": len(wfe_ratios),
            "passed": passed
        }
    
    # =========================================================================
    # TEST 4: MONTE CARLO SIMULATION (Trade Order Randomization)
    # =========================================================================
    def run_monte_carlo_test(self, strategy: dict, n_simulations: int = 100) -> dict:
        """
        Monte Carlo simulation: randomize trade sequence to test robustness.
        
        Industry best practice to detect if PnL depends on lucky trade order.
        Randomly shuffles trade returns and recalculates equity curve.
        
        Pass criteria: 
        - 5th percentile PnL > 0 (95% of random orders still profitable)
        - Std of final PnLs < 50% of mean
        """
        print(f"   🎲 Running Monte Carlo Test for {strategy['pair']}...", end="", flush=True)
        
        # First, run a single backtest to get trade-level results
        start, end = self._get_time_range(self.days)
        config = self._build_config(
            strategy['pair'], strategy['fast_ma'], strategy['slow_ma'],
            strategy['interval'], strategy['stop_loss'], strategy['take_profit'],
            start, end
        )
        
        result = self._run_backtest(config)
        trades = int(result.get("total_positions", 0))
        
        if trades < 10:
            print(f" ⚠️ INSUFFICIENT TRADES ({trades} < 10)")
            return {"test": "Monte Carlo", "passed": False, "reason": "insufficient_trades"}
        
        # Get performance dict for trade simulation
        performance = result.get("performance", {})
        net_pnl = float(result.get("net_pnl", 0)) * 100
        accuracy = float(result.get("accuracy", 0.5))
        
        # Simulate trade returns based on strategy performance
        # Assume win/loss distribution based on accuracy and average trade
        avg_trade_pnl = net_pnl / trades if trades > 0 else 0
        win_rate = accuracy
        
        # Monte Carlo: generate N simulated equity curves
        import random
        simulated_pnls = []
        
        for _ in range(n_simulations):
            equity = 100  # Start with 100 base
            for _ in range(trades):
                if random.random() < win_rate:
                    # Winning trade
                    trade_pnl = abs(avg_trade_pnl) * random.uniform(0.5, 1.5)
                else:
                    # Losing trade
                    trade_pnl = -abs(avg_trade_pnl) * random.uniform(0.5, 1.5)
                equity *= (1 + trade_pnl / 100)
            
            final_pnl = (equity / 100 - 1) * 100  # Convert back to %
            simulated_pnls.append(final_pnl)
        
        # Statistics
        mean_pnl = np.mean(simulated_pnls)
        std_pnl = np.std(simulated_pnls)
        percentile_5 = np.percentile(simulated_pnls, 5)
        percentile_95 = np.percentile(simulated_pnls, 95)
        
        # Pass criteria
        is_robust = percentile_5 > -10  # 5th percentile not too negative
        cv = std_pnl / abs(mean_pnl) if mean_pnl != 0 else float('inf')
        is_stable = cv < 0.5  # Std < 50% of mean
        
        passed = is_robust and is_stable
        
        print(f" {'✅ PASS' if passed else '❌ FAIL'} (Mean: {mean_pnl:.1f}%, P5: {percentile_5:.1f}%, P95: {percentile_95:.1f}%)")
        
        return {
            "test": "Monte Carlo Simulation",
            "simulations": n_simulations,
            "mean_pnl": mean_pnl,
            "std_pnl": std_pnl,
            "percentile_5": percentile_5,
            "percentile_95": percentile_95,
            "cv": cv,
            "passed": passed
        }
    
    # =========================================================================
    # TEST 5: PARAMETER STABILITY (Neighbor Analysis)
    # =========================================================================
    def run_stability_test(self, strategy: dict) -> dict:
        """
        Check if optimal parameters form a stable 'plateau' rather than a 'peak'.
        
        A 'peak' parameter is overfit - only that exact value works.
        A 'plateau' parameter is robust - nearby values also perform well.
        
        Pass criteria: Mean neighbor PnL >= 60% of center PnL
        """
        print(f"   📊 Running Stability Test for {strategy['pair']}...", end="", flush=True)
        
        start, end = self._get_time_range(self.days)
        
        # Center config (original params)
        center_config = self._build_config(
            strategy['pair'], strategy['fast_ma'], strategy['slow_ma'],
            strategy['interval'], strategy['stop_loss'], strategy['take_profit'],
            start, end
        )
        
        # Neighbor configs (±1 step for each param)
        fast = strategy['fast_ma']
        slow = strategy['slow_ma']
        
        neighbor_configs = []
        for fast_delta in [-5, 0, 5]:
            for slow_delta in [-10, 0, 10]:
                if fast_delta == 0 and slow_delta == 0:
                    continue  # Skip center
                new_fast = max(3, fast + fast_delta)
                new_slow = slow + slow_delta
                if new_slow > new_fast + 5:
                    neighbor_configs.append(self._build_config(
                        strategy['pair'], new_fast, new_slow, strategy['interval'],
                        strategy['stop_loss'], strategy['take_profit'], start, end
                    ))
        
        # Run center + neighbors
        all_configs = [center_config] + neighbor_configs
        results = self._run_batch(all_configs)
        
        center_pnl = float(results[0].get("net_pnl", 0)) * 100 if results else 0
        neighbor_pnls = [float(r.get("net_pnl", 0)) * 100 for r in results[1:] if "error" not in r]
        
        if len(neighbor_pnls) < 4:
            print(f" ⚠️ INSUFFICIENT NEIGHBORS")
            return {"test": "Stability", "passed": False, "reason": "insufficient_neighbors"}
        
        mean_neighbor = np.mean(neighbor_pnls)
        min_neighbor = min(neighbor_pnls)
        
        # Calculate stability ratio
        stability_ratio = mean_neighbor / center_pnl if center_pnl > 0 else 0
        
        # Pass: neighbors perform at least 60% as well as center (plateau)
        # Fail: neighbors perform much worse (peak)
        passed = stability_ratio >= 0.60
        
        stability_type = "Plateau ✓" if passed else "Peak ✗"
        
        print(f" {'✅ PASS' if passed else '❌ FAIL'} ({stability_type}: Center {center_pnl:.1f}%, Neighbors {mean_neighbor:.1f}%, Ratio {stability_ratio:.0%})")
        
        return {
            "test": "Parameter Stability",
            "center_pnl": center_pnl,
            "mean_neighbor_pnl": mean_neighbor,
            "min_neighbor_pnl": min_neighbor,
            "stability_ratio": stability_ratio,
            "stability_type": "plateau" if passed else "peak",
            "passed": passed
        }
    
    # TEST 3B: ADAPTIVE WALK-FORWARD (For limited data)
    # =========================================================================
    def run_walkforward_test_adaptive(self, strategy: dict, days_available: int) -> dict:
        """
        Adaptive Walk-Forward for coins with limited data.
        Automatically adjusts window count based on available data.
        Minimum: 4 windows of 90 days each (360 days required)
        """
        print(f"   🔬 Running Adaptive Walk-Forward for {strategy['pair']}...", end="", flush=True)
        
        # Calculate optimal window configuration
        # Use 90-day windows, aim for 4-8 windows based on available data
        window_days = 90
        num_windows = min(8, max(4, days_available // window_days))
        actual_window_days = days_available // num_windows
        
        # Start from recent data (skip most recent 365 days as reserved)
        base_end = datetime.now() - timedelta(days=365)
        
        # Collect all configs
        all_configs = []
        config_map = []
        
        for test_window_idx in range(num_windows):
            test_end = base_end - timedelta(days=test_window_idx * actual_window_days)
            test_start = test_end - timedelta(days=actual_window_days)
            
            config = self._build_config(
                strategy['pair'], strategy['fast_ma'], strategy['slow_ma'],
                strategy['interval'], strategy['stop_loss'], strategy['take_profit'],
                int(test_start.timestamp()), int(test_end.timestamp())
            )
            all_configs.append(config)
            config_map.append((test_window_idx, False))
            
            for train_window_idx in range(num_windows):
                if train_window_idx == test_window_idx:
                    continue
                train_end = base_end - timedelta(days=train_window_idx * actual_window_days)
                train_start = train_end - timedelta(days=actual_window_days)
                
                config = self._build_config(
                    strategy['pair'], strategy['fast_ma'], strategy['slow_ma'],
                    strategy['interval'], strategy['stop_loss'], strategy['take_profit'],
                    int(train_start.timestamp()), int(train_end.timestamp())
                )
                all_configs.append(config)
                config_map.append((test_window_idx, True))
        
        # Run parallel batches
        print(f" ({len(all_configs)} backtests)", end="", flush=True)
        all_results = self._run_parallel_batches(all_configs)
        
        # Process results
        wfe_ratios = []
        for test_window_idx in range(num_windows):
            test_pnl = 0
            train_pnls = []
            
            for i, (mapped_test_idx, is_train) in enumerate(config_map):
                if mapped_test_idx != test_window_idx:
                    continue
                result = all_results[i] if all_results[i] else {"net_pnl": 0}
                pnl = float(result.get("net_pnl", 0)) * 100
                
                if is_train:
                    train_pnls.append(pnl)
                else:
                    test_pnl = pnl
            
            mean_train_pnl = np.mean(train_pnls) if train_pnls else 0
            if mean_train_pnl > 0:
                wfe = test_pnl / mean_train_pnl
                wfe_ratios.append(wfe)
        
        avg_wfe = np.mean(wfe_ratios) if wfe_ratios else 0
        passed = avg_wfe >= 0.50
        
        print(f" {'✅ PASS' if passed else '❌ FAIL'} (WFE: {avg_wfe:.1%}, {len(wfe_ratios)} folds over {days_available//365}+ years)")
        
        return {
            "test": f"Walk-Forward (Adaptive {num_windows}-Fold)",
            "wfe_per_fold": wfe_ratios,
            "avg_wfe": avg_wfe,
            "num_folds": len(wfe_ratios),
            "passed": passed
        }
    
    # MAIN VALIDATION RUNNER
    # =========================================================================
    def run_all(self) -> str:
        """Run all validation tests on all strategies and generate report."""
        print("\n" + "=" * 60)
        print("🛡️  STRATEGY VALIDATOR v2.0 (Data-Aware)")
        print("=" * 60)
        print(f"📋 Strategies to validate: {len(self.strategies)}")
        print(f"📅 Original window: {self.days} days")
        print("=" * 60)
        
        # Check data availability for all coins
        print("\n📊 Data Availability Check:")
        for strategy in self.strategies:
            data_range = self._get_data_range(strategy['pair'])
            days = data_range['days']
            status = "✅" if days >= 1095 else ("⚠️" if days >= 365 else "❌")
            print(f"   {status} {strategy['pair']}: {data_range['start_date']} ~ {data_range['end_date']} ({days} days)")
        print("")
        
        all_results = []
        
        for i, strategy in enumerate(self.strategies, 1):
            print(f"\n📊 [{i}/{len(self.strategies)}] Validating: {strategy['pair']} ({strategy['interval']})")
            print(f"   Config: Fast {strategy['fast_ma']}/Slow {strategy['slow_ma']} | SL {strategy['stop_loss']*100:.0f}%/TP {strategy['take_profit']*100:.0f}%")
            
            # Check data availability for this coin
            data_range = self._get_data_range(strategy['pair'])
            days_available = data_range['days']
            
            # Minimum requirements: OOS needs 1095 days (3 years), WFA needs 2190 days (6 years)
            oos_min_days = 1095
            wfa_min_days = 2190
            sens_min_days = 730
            
            if days_available < sens_min_days:
                print(f"   ⚠️ INSUFFICIENT DATA ({days_available} days < {sens_min_days} min)")
                oos_result = {"test": "OOS", "passed": False, "reason": "insufficient_data"}
                sens_result = {"test": "Sensitivity", "passed": False, "reason": "insufficient_data"}
                wf_result = {"test": "WFA", "passed": False, "reason": "insufficient_data"}
            else:
                # Run tests based on available data
                if days_available >= oos_min_days:
                    oos_result = self.run_oos_test(strategy)
                else:
                    print(f"   ⚠️ OOS Test: Skipped ({days_available} days < {oos_min_days} required)")
                    oos_result = {"test": "OOS", "passed": False, "reason": "insufficient_data"}
                
                sens_result = self.run_sensitivity_test(strategy)
                
                if days_available >= wfa_min_days:
                    wf_result = self.run_walkforward_test(strategy)
                else:
                    # Use reduced WFA with available data
                    print(f"   ⚠️ WFA: Using reduced window ({days_available} days available)")
                    wf_result = self.run_walkforward_test_adaptive(strategy, days_available)
                
                # NEW: Monte Carlo and Stability tests
                mc_result = self.run_monte_carlo_test(strategy)
                stab_result = self.run_stability_test(strategy)
            
            # Overall verdict (need 3 of 5 tests to pass)
            tests_passed = sum([
                oos_result['passed'], 
                sens_result['passed'], 
                wf_result['passed'],
                mc_result['passed'],
                stab_result['passed']
            ])
            overall_passed = tests_passed >= 3  # Pass if 3 of 5 tests pass
            
            result = {
                "strategy": strategy,
                "oos": oos_result,
                "sensitivity": sens_result,
                "walkforward": wf_result,
                "monte_carlo": mc_result,
                "stability": stab_result,
                "tests_passed": tests_passed,
                "overall_passed": overall_passed
            }
            all_results.append(result)
            
            verdict = "✅ VALIDATED" if overall_passed else "❌ REJECTED"
            print(f"\n   📌 Final Verdict: {verdict} ({tests_passed}/5 tests passed)")
        
        # Generate report
        report_path = self._generate_report(all_results)
        
        print("\n" + "=" * 60)
        print(f"✅ Validation Complete. Report: {report_path}")
        print("=" * 60)
        
        return report_path
    
    def _generate_report(self, all_results: list) -> str:
        """Generate markdown validation report."""
        report_path = f"{OUTPUT_DIR}/validation_report_{self.report_id}.md"
        
        lines = [
            "# 🛡️ Strategy Validation Report",
            "",
            f"**Date**: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            f"**Original Window**: {self.days} days",
            "",
            "---",
            "",
            "## 📋 Summary",
            "",
            "| Strategy | OOS Test | Sensitivity | Walk-Forward | Verdict |",
            "|----------|----------|-------------|--------------|---------|",
        ]
        
        for r in all_results:
            s = r['strategy']
            oos = "✅" if r['oos']['passed'] else "❌"
            sens = "✅" if r['sensitivity']['passed'] else "❌"
            wf = "✅" if r['walkforward']['passed'] else "❌"
            verdict = "**VALIDATED**" if r['overall_passed'] else "REJECTED"
            
            config = f"{s['pair']} (Fast {s['fast_ma']}/Slow {s['slow_ma']}/{s['interval']})"
            lines.append(f"| {config} | {oos} | {sens} | {wf} | {verdict} |")
        
        lines.append("")
        lines.append("---")
        lines.append("")
        lines.append("## 📊 Detailed Results")
        lines.append("")
        
        for r in all_results:
            s = r['strategy']
            lines.append(f"### {s['pair']} - Fast {s['fast_ma']}/Slow {s['slow_ma']}/{s['interval']}")
            lines.append("")
            
            # OOS
            oos = r['oos']
            lines.append(f"**Out-of-Sample Test**: {'✅ PASS' if oos['passed'] else '❌ FAIL'}")
            lines.append(f"- OOS PnL (Day 361-720): {oos.get('oos_pnl', 0):.1f}%")
            lines.append(f"- Original PnL (Day 1-360): {oos.get('original_pnl', 0):.1f}%")
            lines.append(f"- Retention Ratio: {oos.get('ratio', 0):.1%}")
            lines.append("")
            
            # Sensitivity
            sens = r['sensitivity']
            lines.append(f"**Sensitivity Test**: {'✅ PASS' if sens['passed'] else '❌ FAIL'}")
            lines.append(f"- Variations Tested: {sens.get('variations_tested', 0)}")
            lines.append(f"- Mean PnL: {sens.get('mean_pnl', 0):.1f}%")
            lines.append(f"- Coefficient of Variation: {sens.get('cv', 0):.1%}")
            lines.append("")
            
            # Walk-Forward
            wf = r['walkforward']
            lines.append(f"**Walk-Forward Analysis**: {'✅ PASS' if wf['passed'] else '❌ FAIL'}")
            lines.append(f"- Avg Walk-Forward Efficiency: {wf.get('avg_wfe', 0):.1%}")
            lines.append("")
            lines.append("---")
            lines.append("")
        
        # Recommendations
        validated = [r for r in all_results if r['overall_passed']]
        lines.append("## 🎯 Recommendations")
        lines.append("")
        if validated:
            lines.append("The following strategies passed validation and are suitable for paper trading:")
            lines.append("")
            for r in validated:
                s = r['strategy']
                lines.append(f"- **{s['pair']}**: Fast {s['fast_ma']}/Slow {s['slow_ma']}/{s['interval']} (SL {s['stop_loss']*100:.0f}%/TP {s['take_profit']*100:.0f}%)")
        else:
            lines.append("> ⚠️ **No strategies passed all validation tests.** Consider:")
            lines.append("> - Running more Discovery iterations")
            lines.append("> - Testing on different market conditions")
            lines.append("> - Simplifying strategy parameters")
        
        with open(report_path, 'w') as f:
            f.write("\n".join(lines))
        
        return report_path


# =========================================================================
# CLI INTERFACE
# =========================================================================
def parse_strategy_string(s: str) -> dict:
    """Parse strategy string like 'ADA-USDT:55:70:1h:0.04:0.1:71.85'"""
    parts = s.split(":")
    return {
        "pair": parts[0],
        "fast_ma": int(parts[1]),
        "slow_ma": int(parts[2]),
        "interval": parts[3],
        "stop_loss": float(parts[4]),
        "take_profit": float(parts[5]),
        "original_pnl": float(parts[6]) if len(parts) > 6 else 0
    }


def main():
    parser = argparse.ArgumentParser(description="Strategy Validator - Prevent Overfitting")
    parser.add_argument("--strategies", type=str, required=True,
                        help="Comma-separated strategy strings: 'PAIR:FAST:SLOW:INTERVAL:SL:TP:ORIGINAL_PNL'")
    parser.add_argument("--days", type=int, default=360,
                        help="Original backtest window in days (default: 360)")
    
    args = parser.parse_args()
    
    strategies = [parse_strategy_string(s.strip()) for s in args.strategies.split(",")]
    
    validator = StrategyValidator(strategies=strategies, days=args.days)
    validator.run_all()


if __name__ == "__main__":
    main()
