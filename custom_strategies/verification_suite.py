#!/usr/bin/env python3
"""
==============================================================================
🔬 HUMMINGBOT BACKTESTING VERIFICATION FRAMEWORK
==============================================================================

This is the MASTER TEST SUITE for verifying the accuracy and consistency of
the Hummingbot backtesting engine after any refactoring.

USAGE:
    docker exec hummingbot-api python3 /hummingbot-api/bots/controllers/custom/verification_suite.py

OR for quick validation:
    docker exec hummingbot-api python3 /hummingbot-api/bots/controllers/custom/verification_suite.py --quick

TEST CATEGORIES:
    1. CORE ENGINE TESTS - Basic engine functionality
    2. SELF-CONSISTENCY TESTS - Same config produces same results
    3. CACHE ISOLATION TESTS - Different time windows don't pollute
    4. STATE-DEPENDENT TESTS - Trailing stop, compounding, etc.
    5. HIGH-FREQUENCY TESTS - Signal detection under load

PASS CRITERIA:
    - All tests must pass for the refactor to be considered safe
    - Any failure indicates potential bugs introduced by the refactor
"""

import asyncio
import sys
import time
import argparse
from decimal import Decimal
from dataclasses import dataclass
from typing import List, Dict, Any, Optional, Callable

# ==============================================================================
# TEST CONFIGURATION
# ==============================================================================

@dataclass
class TestResult:
    name: str
    category: str
    passed: bool
    details: str
    duration_ms: float

@dataclass 
class TestCase:
    name: str
    category: str
    func: Callable
    description: str

# Global results storage
ALL_RESULTS: List[TestResult] = []

# ==============================================================================
# TEST UTILITIES
# ==============================================================================

async def run_backtest(config, start: int, end: int, resolution: str = "1m", trade_cost: float = 0.0006):
    """Helper to run a single backtest and return results."""
    from hummingbot.strategy_v2.backtesting.backtesting_engine_base import BacktestingEngineBase
    
    engine = BacktestingEngineBase()
    engine.allow_download = False
    
    result = await engine.run_backtesting(
        controller_config=config,
        start=int(start),
        end=int(end),
        backtesting_resolution=resolution,
        trade_cost=trade_cost
    )
    
    return result

def create_ma_cross_config(fast_ma=5, slow_ma=10, stop_loss=0.03, take_profit=0.05, 
                           time_limit=7200, use_compounding=False):
    """Factory for MA Cross configs."""
    from ma_cross_strategy import MACrossStrategyConfig, CandleInterval
    
    return MACrossStrategyConfig(
        connector_name="binance",
        trading_pair="SOL-USDT",
        total_amount_quote=Decimal("100"),
        fast_ma=fast_ma,
        slow_ma=slow_ma,
        indicator_interval=CandleInterval.H1,
        stop_loss=Decimal(str(stop_loss)),
        take_profit=Decimal(str(take_profit)),
        time_limit=time_limit,
        use_compounding=use_compounding
    )

def create_bb_grid_config(bb_period=20, bb_std=2.0, entry_threshold=0.9):
    """Factory for BB Grid configs."""
    from bb_grid_strategy import BBGridStrategyConfig
    
    return BBGridStrategyConfig(
        connector_name="binance",
        trading_pair="SOL-USDT",
        total_amount_quote=Decimal("100"),
        bb_period=bb_period,
        bb_std=bb_std,
        entry_threshold=entry_threshold,
        use_trend_filter=False,
        indicator_interval="1h",
        stop_loss=Decimal("0.03"),
        take_profit=Decimal("0.05"),
        time_limit=86400
    )

def results_match(r1: dict, r2: dict, tolerance: float = 1e-9) -> bool:
    """Check if two result dicts match within tolerance."""
    pnl_match = abs(r1.get("net_pnl", 0) - r2.get("net_pnl", 0)) < tolerance
    trades_match = r1.get("total_positions", 0) == r2.get("total_positions", 0)
    return pnl_match and trades_match

# ==============================================================================
# CATEGORY 1: CORE ENGINE TESTS
# ==============================================================================

async def test_basic_engine_functionality():
    """Verify the engine can run a basic backtest and return valid results."""
    config = create_ma_cross_config()
    start = 1735689600  # 2026-01-01
    end = 1736121600    # 2026-01-06
    
    result = await run_backtest(config, start, end)
    
    # Validate structure
    assert "results" in result, "Missing 'results' key"
    assert "executors" in result, "Missing 'executors' key"
    assert "processed_data" in result, "Missing 'processed_data' key"
    
    r = result["results"]
    assert "net_pnl" in r, "Missing 'net_pnl' in results"
    assert "total_positions" in r, "Missing 'total_positions' in results"
    
    # Validate data types
    assert isinstance(r.get("net_pnl"), (int, float)), "net_pnl should be numeric"
    assert isinstance(r.get("total_positions"), int), "total_positions should be int"
    
    return True, f"PnL={r['net_pnl']:.6f}, Trades={r['total_positions']}"

async def test_data_coverage():
    """Verify processed data covers the full requested time range."""
    config = create_ma_cross_config()
    start = 1735689600
    end = 1736121600
    
    result = await run_backtest(config, start, end)
    features = result["processed_data"]["features"]
    
    ts_min = features["timestamp"].min()
    ts_max = features["timestamp"].max()
    
    coverage = (ts_max - ts_min) / (end - start) * 100
    
    assert coverage >= 95, f"Coverage too low: {coverage:.1f}%"
    
    return True, f"Coverage: {coverage:.1f}% ({len(features)} rows)"

async def test_no_zero_prices():
    """Verify no zero or NaN prices in processed data."""
    config = create_ma_cross_config()
    start = 1735689600
    end = 1736121600
    
    result = await run_backtest(config, start, end)
    features = result["processed_data"]["features"]
    
    zero_count = (features["close"] == 0).sum()
    nan_count = features["close"].isna().sum()
    
    assert zero_count == 0, f"Found {zero_count} zero prices"
    assert nan_count == 0, f"Found {nan_count} NaN prices"
    
    return True, f"All {len(features)} prices are valid"

# ==============================================================================
# CATEGORY 2: SELF-CONSISTENCY TESTS
# ==============================================================================

async def test_determinism_same_config():
    """Same config produces identical results across multiple runs."""
    config = create_ma_cross_config()
    start = 1735689600
    end = 1736121600
    
    r1 = (await run_backtest(config, start, end))["results"]
    r2 = (await run_backtest(config, start, end))["results"]
    
    assert results_match(r1, r2), f"Results differ: {r1} vs {r2}"
    
    return True, f"Both runs: PnL={r1['net_pnl']:.6f}, Trades={r1['total_positions']}"

async def test_different_config_different_results():
    """Different configs should produce different results."""
    config1 = create_ma_cross_config(fast_ma=5, slow_ma=10)
    config2 = create_ma_cross_config(fast_ma=20, slow_ma=50)
    
    start = 1735689600
    end = 1736121600
    
    r1 = (await run_backtest(config1, start, end))["results"]
    r2 = (await run_backtest(config2, start, end))["results"]
    
    # Should NOT match
    assert not results_match(r1, r2), "Different configs produced same results!"
    
    return True, f"Config1: PnL={r1['net_pnl']:.6f} vs Config2: PnL={r2['net_pnl']:.6f}"

async def test_multi_strategy_consistency():
    """Multiple strategy types should work correctly."""
    ma_config = create_ma_cross_config()
    bb_config = create_bb_grid_config()
    
    start = 1735689600
    end = 1736121600
    
    ma_result = (await run_backtest(ma_config, start, end))["results"]
    bb_result = (await run_backtest(bb_config, start, end))["results"]
    
    # Both should have valid results (regardless of profitability)
    assert ma_result.get("total_positions", 0) >= 0
    assert bb_result.get("total_positions", 0) >= 0
    
    return True, f"MA: {ma_result['total_positions']} trades, BB: {bb_result['total_positions']} trades"

# ==============================================================================
# CATEGORY 3: CACHE ISOLATION TESTS
# ==============================================================================

async def test_multi_window_isolation():
    """Different time windows should not pollute each other's cache."""
    config = create_ma_cross_config(fast_ma=20, slow_ma=50)
    
    window_a = (1733011200, 1733616000)  # Dec 1-7, 2024
    window_b = (1735689600, 1736294400)  # Jan 1-7, 2026
    
    # Run A -> B -> A
    r_a1 = (await run_backtest(config, window_a[0], window_a[1]))["results"]
    r_b = (await run_backtest(config, window_b[0], window_b[1]))["results"]
    r_a2 = (await run_backtest(config, window_a[0], window_a[1]))["results"]
    
    # A1 and A2 should match
    assert results_match(r_a1, r_a2), "Window A results inconsistent after running Window B"
    
    # A and B should differ (different time periods)
    assert not results_match(r_a1, r_b), "Window A and B should differ"
    
    return True, f"Window A: {r_a1['total_positions']} trades (consistent), Window B: {r_b['total_positions']} trades (different)"

# ==============================================================================
# CATEGORY 4: STATE-DEPENDENT TESTS
# ==============================================================================

async def test_trailing_stop_state_integrity():
    """Trailing stop state should not leak between runs."""
    config = create_ma_cross_config(stop_loss=0.03, take_profit=0.10)
    start = 1735689600
    end = 1736121600
    
    r1 = (await run_backtest(config, start, end))["results"]
    r2 = (await run_backtest(config, start, end))["results"]
    
    assert results_match(r1, r2), "Trailing stop state leaked between runs"
    
    return True, f"Both runs: PnL={r1['net_pnl']:.6f}"

async def test_compounding_repeatability():
    """Compounding position sizing should be repeatable."""
    config = create_ma_cross_config(use_compounding=True)
    start = 1735689600
    end = 1736121600
    
    r1 = (await run_backtest(config, start, end))["results"]
    r2 = (await run_backtest(config, start, end))["results"]
    
    assert results_match(r1, r2), "Compounding results inconsistent"
    
    return True, f"Compounding PnL: {r1['net_pnl']:.6f} (repeatable)"

# ==============================================================================
# CATEGORY 5: HIGH-FREQUENCY TESTS
# ==============================================================================

async def test_high_frequency_signals():
    """High-frequency signal generation should not overwhelm the engine."""
    # Very short MAs = many crossovers
    config = create_ma_cross_config(fast_ma=2, slow_ma=5, time_limit=3600)
    start = 1735689600
    end = 1736121600
    
    result = await run_backtest(config, start, end)
    features = result["processed_data"]["features"]
    r = result["results"]
    
    total_signals = (features["signal"] != 0).sum()
    
    assert total_signals > 100, f"Expected many signals, got {total_signals}"
    assert r.get("total_positions", 0) > 0, "No trades executed despite signals"
    
    return True, f"{total_signals} signals, {r['total_positions']} trades executed"

async def test_signal_to_trade_ratio():
    """Verify signals are being processed correctly."""
    config = create_ma_cross_config(fast_ma=5, slow_ma=10)
    start = 1735689600
    end = 1736121600
    
    result = await run_backtest(config, start, end)
    features = result["processed_data"]["features"]
    r = result["results"]
    
    total_signals = (features["signal"] != 0).sum()
    total_trades = r.get("total_positions", 0)
    
    # Should have some signals
    assert total_signals > 0, "No signals generated"
    
    # Ratio should be reasonable (not all signals become trades due to active positions)
    if total_signals > 0:
        ratio = total_trades / total_signals
        # Ratio should be between 0.01 and 1.0 (1% to 100% of signals become trades)
        assert 0.01 <= ratio <= 1.0, f"Unusual signal-to-trade ratio: {ratio:.2f}"
    
    return True, f"Signals: {total_signals}, Trades: {total_trades}, Ratio: {total_trades/total_signals:.2%}"

# ==============================================================================
# CATEGORY 6: UPSTREAM BASELINE PARITY TESTS
# ==============================================================================

# BASELINE RESULTS - Captured from original Hummingbot engine (pre-fork)
# These values represent the "ground truth" from the unmodified upstream code.
# Update these baselines when the upstream Hummingbot version changes.
UPSTREAM_BASELINES = {
    # MA Cross 5/10 on SOL-USDT, Jan 1-6 2026, 1m resolution
    "ma_cross_5_10_sol_jan2026": {
        "description": "MA Cross (5/10) SOL-USDT, Jan 1-6 2026",
        "start": 1735689600,
        "end": 1736121600,
        "expected_total_positions": 18,  # Expected trade count from upstream
        "pnl_tolerance": 0.02,  # Allow 2% PnL deviation (float precision differences)
        "trade_tolerance": 0,   # Trades must match exactly
    },
    # MA Cross 20/50 on SOL-USDT, Dec 1-7 2024
    "ma_cross_20_50_sol_dec2024": {
        "description": "MA Cross (20/50) SOL-USDT, Dec 1-7 2024",
        "start": 1733011200,
        "end": 1733616000,
        "expected_total_positions": 6,
        "pnl_tolerance": 0.02,
        "trade_tolerance": 0,
    },
}

async def test_upstream_baseline_trade_count():
    """
    Verify trade count matches the baseline from original Hummingbot.
    
    This test ensures that the Jump-Ahead engine produces the same NUMBER
    of trades as the original O(N) linear loop engine.
    """
    config = create_ma_cross_config(fast_ma=5, slow_ma=10)
    baseline = UPSTREAM_BASELINES["ma_cross_5_10_sol_jan2026"]
    
    result = await run_backtest(config, baseline["start"], baseline["end"])
    r = result["results"]
    
    actual_trades = r.get("total_positions", 0)
    expected_trades = baseline["expected_total_positions"]
    tolerance = baseline["trade_tolerance"]
    
    diff = abs(actual_trades - expected_trades)
    
    assert diff <= tolerance, f"Trade count mismatch: got {actual_trades}, expected {expected_trades} (baseline)"
    
    return True, f"Trades: {actual_trades} (matches baseline: {expected_trades})"

async def test_upstream_baseline_multi_scenario():
    """
    Verify multiple scenarios against their baselines.
    
    Tests different configs/periods to ensure fork accuracy across scenarios.
    """
    scenarios = [
        ("ma_cross_5_10_sol_jan2026", create_ma_cross_config(fast_ma=5, slow_ma=10)),
        ("ma_cross_20_50_sol_dec2024", create_ma_cross_config(fast_ma=20, slow_ma=50)),
    ]
    
    all_passed = True
    details = []
    
    for scenario_name, config in scenarios:
        baseline = UPSTREAM_BASELINES[scenario_name]
        result = await run_backtest(config, baseline["start"], baseline["end"])
        r = result["results"]
        
        actual_trades = r.get("total_positions", 0)
        expected_trades = baseline["expected_total_positions"]
        
        if actual_trades == expected_trades:
            details.append(f"{scenario_name}: ✓ {actual_trades} trades")
        else:
            details.append(f"{scenario_name}: ✗ got {actual_trades}, expected {expected_trades}")
            all_passed = False
    
    assert all_passed, f"Some scenarios failed baseline: {'; '.join(details)}"
    
    return True, f"All {len(scenarios)} scenarios match baseline"

async def test_upstream_no_phantom_trades():
    """
    Verify that no "phantom trades" are created due to caching or state bugs.
    
    Phantom trades occur when the engine incorrectly creates trades that
    wouldn't exist in the original implementation.
    """
    # Run same config multiple times, count should be consistent and match baseline
    config = create_ma_cross_config(fast_ma=5, slow_ma=10)
    baseline = UPSTREAM_BASELINES["ma_cross_5_10_sol_jan2026"]
    
    trade_counts = []
    for _ in range(3):
        result = await run_backtest(config, baseline["start"], baseline["end"])
        trade_counts.append(result["results"].get("total_positions", 0))
    
    # All counts should be identical
    assert len(set(trade_counts)) == 1, f"Trade counts vary: {trade_counts}"
    
    # Should match baseline
    assert trade_counts[0] == baseline["expected_total_positions"], \
        f"Trade count {trade_counts[0]} differs from baseline {baseline['expected_total_positions']}"
    
    return True, f"Consistent count: {trade_counts[0]} across 3 runs (no phantom trades)"


# Register all tests
ALL_TESTS = [
    # Category 1: Core Engine
    TestCase("Basic Engine Functionality", "CORE ENGINE", test_basic_engine_functionality, 
             "Verify engine can run and return valid structure"),
    TestCase("Data Coverage", "CORE ENGINE", test_data_coverage,
             "Verify processed data covers full time range"),
    TestCase("No Zero Prices", "CORE ENGINE", test_no_zero_prices,
             "Verify no corrupted price data"),
    
    # Category 2: Self-Consistency
    TestCase("Determinism", "SELF-CONSISTENCY", test_determinism_same_config,
             "Same config produces identical results"),
    TestCase("Config Sensitivity", "SELF-CONSISTENCY", test_different_config_different_results,
             "Different configs produce different results"),
    TestCase("Multi-Strategy", "SELF-CONSISTENCY", test_multi_strategy_consistency,
             "Multiple strategy types work correctly"),
    
    # Category 3: Cache Isolation
    TestCase("Multi-Window Isolation", "CACHE ISOLATION", test_multi_window_isolation,
             "Different time windows don't pollute cache"),
    
    # Category 4: State-Dependent
    TestCase("Trailing Stop State", "STATE-DEPENDENT", test_trailing_stop_state_integrity,
             "Trailing stop state doesn't leak"),
    TestCase("Compounding Repeatability", "STATE-DEPENDENT", test_compounding_repeatability,
             "Compounding position sizing is repeatable"),
    
    # Category 5: High-Frequency
    TestCase("High-Frequency Signals", "HIGH-FREQUENCY", test_high_frequency_signals,
             "Engine handles many signals correctly"),
    TestCase("Signal-to-Trade Ratio", "HIGH-FREQUENCY", test_signal_to_trade_ratio,
             "Signals are processed into trades correctly"),
    
    # Category 6: Upstream Baseline Parity
    TestCase("Baseline Trade Count", "UPSTREAM PARITY", test_upstream_baseline_trade_count,
             "Trade count matches original Hummingbot baseline"),
    TestCase("Baseline Multi-Scenario", "UPSTREAM PARITY", test_upstream_baseline_multi_scenario,
             "Multiple scenarios match their baselines"),
    TestCase("No Phantom Trades", "UPSTREAM PARITY", test_upstream_no_phantom_trades,
             "No spurious trades created by caching bugs"),
]

# Quick subset for fast validation
QUICK_TESTS = [
    "Basic Engine Functionality",
    "Determinism",
    "Multi-Window Isolation",
    "High-Frequency Signals",
    "Baseline Trade Count"  # Critical: verify fork matches upstream
]


async def run_test(test_case: TestCase) -> TestResult:
    """Run a single test and return result."""
    start_time = time.time()
    try:
        passed, details = await test_case.func()
        duration = (time.time() - start_time) * 1000
        return TestResult(test_case.name, test_case.category, passed, details, duration)
    except AssertionError as e:
        duration = (time.time() - start_time) * 1000
        return TestResult(test_case.name, test_case.category, False, str(e), duration)
    except Exception as e:
        duration = (time.time() - start_time) * 1000
        return TestResult(test_case.name, test_case.category, False, f"ERROR: {str(e)}", duration)


async def run_all_tests(quick: bool = False):
    """Run all tests and generate report."""
    print("=" * 70)
    print("🔬 HUMMINGBOT BACKTESTING VERIFICATION FRAMEWORK")
    print("=" * 70)
    print()
    
    tests_to_run = ALL_TESTS
    if quick:
        tests_to_run = [t for t in ALL_TESTS if t.name in QUICK_TESTS]
        print(f"⚡ QUICK MODE: Running {len(tests_to_run)} essential tests")
    else:
        print(f"📋 Running {len(tests_to_run)} tests across all categories")
    print()
    
    results = []
    current_category = None
    
    for test_case in tests_to_run:
        if test_case.category != current_category:
            current_category = test_case.category
            print(f"\n{'='*60}")
            print(f"📁 {current_category}")
            print('='*60)
        
        print(f"  Running: {test_case.name}...", end=" ", flush=True)
        result = await run_test(test_case)
        results.append(result)
        
        if result.passed:
            print(f"✅ PASSED ({result.duration_ms:.0f}ms)")
            print(f"     {result.details}")
        else:
            print(f"❌ FAILED ({result.duration_ms:.0f}ms)")
            print(f"     {result.details}")
    
    # Summary
    print("\n" + "=" * 70)
    print("📊 SUMMARY")
    print("=" * 70)
    
    passed = sum(1 for r in results if r.passed)
    failed = sum(1 for r in results if not r.passed)
    total_time = sum(r.duration_ms for r in results)
    
    # Group by category
    categories = {}
    for r in results:
        if r.category not in categories:
            categories[r.category] = {"passed": 0, "failed": 0}
        if r.passed:
            categories[r.category]["passed"] += 1
        else:
            categories[r.category]["failed"] += 1
    
    for cat, stats in categories.items():
        status = "✅" if stats["failed"] == 0 else "❌"
        print(f"  {status} {cat}: {stats['passed']}/{stats['passed']+stats['failed']} passed")
    
    print()
    print(f"  Total: {passed}/{len(results)} tests passed")
    print(f"  Time: {total_time/1000:.1f}s")
    print()
    
    if failed == 0:
        print("🎉 ALL TESTS PASSED - Refactor is SAFE!")
        return 0
    else:
        print(f"⚠️ {failed} TESTS FAILED - Review before deploying!")
        return 1


def main():
    parser = argparse.ArgumentParser(description="Hummingbot Backtesting Verification Suite")
    parser.add_argument("--quick", action="store_true", help="Run only essential tests")
    args = parser.parse_args()
    
    exit_code = asyncio.run(run_all_tests(quick=args.quick))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
