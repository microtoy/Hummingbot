from fastapi import APIRouter
from hummingbot.data_feed.candles_feed.candles_factory import CandlesFactory
from hummingbot.strategy_v2.backtesting.backtesting_engine_base import BacktestingEngineBase

from config import settings
from models.backtesting import BacktestingConfig
from pathlib import Path
import pandas as pd
import hummingbot

router = APIRouter(tags=["Backtesting"], prefix="/backtesting")
candles_factory = CandlesFactory()
backtesting_engine = BacktestingEngineBase()


@router.get("/candles/status")
async def get_candles_status():
    """Get the status of cached candles on the server, including internal gaps (holes)."""
    try:
        from hummingbot.data_feed.candles_feed.candles_base import CandlesBase
        cache_dir = Path(hummingbot.data_path()) / "candles"
        if not cache_dir.exists():
            return {"cached_files": []}
            
        files = []
        for f in cache_dir.glob("*.csv"):
            try:
                # Extract interval from filename: connector_pair_interval.csv
                parts = f.stem.split("_")
                interval = parts[-1]
                interval_sec = CandlesBase.interval_to_seconds.get(interval, 60)

                df = pd.read_csv(f, usecols=["timestamp"])
                if not df.empty:
                    df = df.sort_values("timestamp")
                    ts = df["timestamp"].values
                    start_ts = int(ts[0])
                    end_ts = int(ts[-1])
                    
                    # DETECT INTERNAL GAPS (Holes)
                    # A gap exists if the difference between consecutive timestamps > interval_sec
                    gaps = []
                    # Optimization: Only check if count is significantly less than expected
                    expected_count = (end_ts - start_ts) // interval_sec + 1
                    if len(ts) < expected_count:
                        diffs = ts[1:] - ts[:-1]
                        gap_indices = (diffs > interval_sec * 1.5).nonzero()[0] # 1.5 for tolerance
                        for idx in gap_indices:
                            gaps.append({
                                "start": int(ts[idx]),
                                "end": int(ts[idx+1])
                            })
                    
                    files.append({
                        "file": f.name,
                        "count": len(df),
                        "start": start_ts,
                        "end": end_ts,
                        "holes": gaps
                    })
            except:
                continue
        return {"cached_files": files}
    except Exception as e:
        return {"error": str(e)}

@router.post("/candles/sync")
async def sync_candles(backtesting_config: BacktestingConfig):
    """Prefetch and cache candles with automatic buffer padding."""
    try:
        from types import SimpleNamespace
        from pathlib import Path
        import hummingbot
        
        # USE LOCAL INSTANCE to avoid race conditions during parallel syncs
        engine = BacktestingEngineBase()
        
        # 1. Determine controller config
        if isinstance(backtesting_config.config, dict) and "connector_name" in backtesting_config.config:
            controller_config = SimpleNamespace(
                connector_name=backtesting_config.config["connector_name"],
                trading_pair=backtesting_config.config["trading_pair"],
                candles_config=backtesting_config.config.get("candles_config", [])
            )
        elif isinstance(backtesting_config.config, str):
            controller_config = engine.get_controller_config_instance_from_yml(
                config_path=backtesting_config.config,
                controllers_conf_dir_path=settings.app.controllers_path,
                controllers_module=settings.app.controllers_module
            )
        else:
            controller_config = engine.get_controller_config_instance_from_dict(
                config_data=backtesting_config.config,
                controllers_module=settings.app.controllers_module
            )
            
        # 2. Use exact range for sync (No heavy padding needed here as engine handles its own buffer if needed)
        # But for sync, we just want to fill the requested chunk.
        padded_start = int(backtesting_config.start_time)
        interval = backtesting_config.backtesting_resolution
        
        # 3. Trigger initialization (which hits the smart cache in engine)
        engine.backtesting_data_provider.update_backtesting_time(
            padded_start, int(backtesting_config.end_time))
        engine.controller = SimpleNamespace(config=controller_config)
        engine.backtesting_resolution = interval
        
        # Allow download for sync operation
        engine.allow_download = True
        await engine.initialize_backtesting_data_provider()
        
        # 4. Get row count FAST (Binary line count)
        cache_dir = Path(hummingbot.data_path()) / "candles"
        filename = f"{controller_config.connector_name}_{controller_config.trading_pair}_{interval}.csv"
        cache_file = cache_dir / filename
        
        row_count = 0
        if cache_file.exists():
            try:
                with open(cache_file, 'rb') as f:
                    row_count = sum(1 for _ in f) - 1 # Faster than pandas for count
            except:
                row_count = 0
        
        return {
            "status": "success", 
            "message": f"Synced {row_count:,} rows",
            "rows": row_count
        }
    except Exception as e:
        return {"error": str(e)}

@router.post("/candles/batch-sync")
async def batch_sync_candles(batch_configs: list[BacktestingConfig]):
    """
    Smart rate-limited parallel sync of multiple trading pairs' candle data.
    Uses semaphore to limit concurrent downloads while maximizing throughput.
    """
    import asyncio
    from types import SimpleNamespace
    from pathlib import Path
    import hummingbot
    from hummingbot.data_feed.candles_feed.candles_base import CandlesBase
    
    # Higher concurrency (3) with shorter delay (0.2s) for better throughput
    # Binance limit is 2400 req/min = 40 req/sec, we're well under that
    semaphore = asyncio.Semaphore(3)
    
    async def sync_single_pair(config: BacktestingConfig):
        """Sync a single trading pair's data with rate limiting."""
        async with semaphore:
            try:
                engine = BacktestingEngineBase()
                
                if isinstance(config.config, dict) and "connector_name" in config.config:
                    controller_config = SimpleNamespace(
                        connector_name=config.config["connector_name"],
                        trading_pair=config.config["trading_pair"],
                        candles_config=config.config.get("candles_config", [])
                    )
                else:
                    return {"pair": "Unknown", "status": "error", "message": "Invalid config"}
                
                interval = config.backtesting_resolution
                buffer_seconds = 2000 * CandlesBase.interval_to_seconds.get(interval, 60)
                padded_start = int(config.start_time - buffer_seconds)
                
                engine.backtesting_data_provider.update_backtesting_time(
                    padded_start, int(config.end_time))
                engine.controller = SimpleNamespace(config=controller_config)
                engine.backtesting_resolution = interval
                
                # Allow download for sync operation
                engine.allow_download = True
                await engine.initialize_backtesting_data_provider()
                
                # Get row count
                cache_dir = Path(hummingbot.data_path()) / "candles"
                filename = f"{controller_config.connector_name}_{controller_config.trading_pair}_{interval}.csv"
                cache_file = cache_dir / filename
                row_count = 0
                if cache_file.exists():
                    import pandas as pd
                    df = pd.read_csv(cache_file)
                    row_count = len(df)
                
                # Short delay between requests
                await asyncio.sleep(0.2)
                
                return {"pair": controller_config.trading_pair, "status": "success", "rows": row_count}
            except Exception as e:
                pair = config.config.get("trading_pair", "Unknown") if isinstance(config.config, dict) else "Unknown"
                return {"pair": pair, "status": "error", "message": str(e)}
    
    # Launch ALL sync tasks (semaphore will limit actual concurrency to 3)
    tasks = [sync_single_pair(config) for config in batch_configs]
    results = await asyncio.gather(*tasks)
    
    success_count = sum(1 for r in results if r.get("status") == "success")
    return {"results": list(results), "success": success_count, "total": len(batch_configs)}

@router.post("/run-backtesting")
async def run_backtesting(backtesting_config: BacktestingConfig):
    """
    Run a backtesting simulation with the provided configuration.
    
    Args:
        backtesting_config: Configuration for the backtesting including start/end time,
                          resolution, trade cost, and controller config
                          
    Returns:
        Dictionary containing executors, processed data, and results from the backtest
        
    Raises:
        Returns error dictionary if backtesting fails
    """
    try:
        if isinstance(backtesting_config.config, str):
            controller_config = backtesting_engine.get_controller_config_instance_from_yml(
                config_path=backtesting_config.config,
                controllers_conf_dir_path=settings.app.controllers_path,
                controllers_module=settings.app.controllers_module
            )
        else:
            controller_config = backtesting_engine.get_controller_config_instance_from_dict(
                config_data=backtesting_config.config,
                controllers_module=settings.app.controllers_module
            )
        backtesting_results = await backtesting_engine.run_backtesting(
            controller_config=controller_config, trade_cost=backtesting_config.trade_cost,
            start=int(backtesting_config.start_time), end=int(backtesting_config.end_time),
            backtesting_resolution=backtesting_config.backtesting_resolution)
        processed_data = backtesting_results["processed_data"]["features"].fillna(0)
        executors_info = [e.to_dict() for e in backtesting_results["executors"]]
        backtesting_results["processed_data"] = processed_data.to_dict()
        results = backtesting_results["results"]
        results["sharpe_ratio"] = results["sharpe_ratio"] if results["sharpe_ratio"] is not None else 0
        
        # Include performance report if available
        response = {
            "executors": executors_info,
            "processed_data": backtesting_results["processed_data"],
            "results": backtesting_results["results"],
        }
        if "performance" in backtesting_results:
            response["performance"] = backtesting_results["performance"]
            
        return response
    except Exception as e:
        return {"error": str(e)}
@router.post("/batch-run")
async def batch_run_backtesting(batch_configs: list[BacktestingConfig]):
    """
    Two-phase batch backtesting for maximum performance:
    Phase 1: Parallel cache warming (all coins download data concurrently)
    Phase 2: Sequential execution (each backtest is ~3ms after cache hit)
    """
    import asyncio
    from types import SimpleNamespace
    from hummingbot.data_feed.candles_feed.data_types import CandlesConfig as HBCandlesConfig
    
    # Phase 1: Pre-warm cache for ALL trading pairs in parallel
    async def warm_cache(config: BacktestingConfig):
        """Download and cache data for a single trading pair."""
        try:
            engine = BacktestingEngineBase()
            if isinstance(config.config, dict):
                trading_pair = config.config.get("trading_pair", "BTC-USDT")
                connector = config.config.get("connector_name", "binance")
            else:
                return  # Skip non-dict configs for warming
            
            # Set up minimal engine state for cache warming
            engine.backtesting_data_provider.update_backtesting_time(
                int(config.start_time), int(config.end_time))
            engine.backtesting_resolution = config.backtesting_resolution
            
            # Minimal controller config just for data loading
            engine.controller = SimpleNamespace(config=SimpleNamespace(
                connector_name=connector,
                trading_pair=trading_pair,
                candles_config=[]
            ))
            
            await engine.initialize_backtesting_data_provider()
            return f"✅ {trading_pair}"
        except Exception as e:
            return f"❌ {config.config.get('trading_pair', 'Unknown')}: {e}"
    
    # Launch ALL cache warming tasks concurrently
    cache_tasks = [warm_cache(config) for config in batch_configs]
    await asyncio.gather(*cache_tasks)
    
    # Phase 2: Sequential execution (now all data is cached, each takes ~3ms)
    results = []
    for config in batch_configs:
        try:
            if isinstance(config.config, str):
                controller_config = backtesting_engine.get_controller_config_instance_from_yml(
                    config_path=config.config,
                    controllers_conf_dir_path=settings.app.controllers_path,
                    controllers_module=settings.app.controllers_module
                )
            else:
                controller_config = backtesting_engine.get_controller_config_instance_from_dict(
                    config_data=config.config,
                    controllers_module=settings.app.controllers_module
                )
            
            bt_result = await backtesting_engine.run_backtesting(
                controller_config=controller_config,
                trade_cost=config.trade_cost,
                start=int(config.start_time),
                end=int(config.end_time),
                backtesting_resolution=config.backtesting_resolution
            )
            
            summary = bt_result["results"]
            results.append({
                "trading_pair": controller_config.trading_pair,
                "net_pnl": summary.get("net_pnl", 0),
                "net_pnl_quote": summary.get("net_pnl_quote", 0),
                "accuracy": summary.get("accuracy", 0),
                "sharpe_ratio": summary.get("sharpe_ratio", 0) or 0,
                "max_drawdown_pct": summary.get("max_drawdown_pct", 0),
                "profit_factor": summary.get("profit_factor", 0),
                "total_positions": summary.get("total_positions", 0),
                "performance": bt_result.get("performance", {})
            })
        except Exception as e:
            results.append({
                "trading_pair": config.config.get("trading_pair", "Unknown") if isinstance(config.config, dict) else "Unknown",
                "error": str(e),
                "net_pnl": 0, "net_pnl_quote": 0, "accuracy": 0, "sharpe_ratio": 0,
                "max_drawdown_pct": 0, "profit_factor": 0, "total_positions": 0
            })
    
    return {"results": results}
