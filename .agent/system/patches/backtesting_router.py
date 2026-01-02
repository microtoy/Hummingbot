from fastapi import APIRouter
from hummingbot.data_feed.candles_feed.candles_factory import CandlesFactory
from hummingbot.strategy_v2.backtesting.backtesting_engine_base import BacktestingEngineBase

from config import settings
from models.backtesting import BacktestingConfig
from pathlib import Path
import pandas as pd
import hummingbot

router = APIRouter(tags=["Backtesting"], prefix="/backtesting")

# --- WORKER FUNCTION FOR PROCESS POOL (Must be top-level for pickling) ---
def run_process_safe_backtest(config_data: dict, start: int, end: int, resolution: str, trade_cost: float):
    """
    Standalone function running in a separate, clean process (via spawn).
    Enforces a 'Network Blackout' to prevent any asyncio loop collisions.
    """
    # 1. NETWORK BLACKOUT: Mock all network-related modules BEFORE any HB imports
    from unittest.mock import MagicMock, AsyncMock
    import sys
    import asyncio
    
    # Mocking aiohttp to be async-friendly
    mock_aiohttp = MagicMock()
    mock_session = AsyncMock()
    mock_session.__aenter__.return_value = mock_session
    mock_aiohttp.ClientSession.return_value = mock_session
    sys.modules['aiohttp'] = mock_aiohttp
    
    # Mocking HB Networking components - MUST use AsyncMock for things that are awaited!
    mock_sync_mod = MagicMock()
    mock_sync_class = MagicMock()
    mock_sync_instance = AsyncMock() 
    mock_sync_class.get_instance.return_value = mock_sync_instance
    mock_sync_class.return_value = mock_sync_instance
    mock_sync_mod.TimeSynchronizer = mock_sync_class
    sys.modules['hummingbot.connector.time_synchronizer'] = mock_sync_mod
    
    # [NEW] Mocking MarketDataProvider and OrderBookTracker to stop background threads/tasks
    sys.modules['hummingbot.data_feed.market_data_provider'] = MagicMock()
    sys.modules['hummingbot.core.data_type.order_book_tracker'] = MagicMock()
    
    # Mocking CandlesFactory - candles feeds often have awaited start/stop/get methods
    mock_factory_mod = MagicMock()
    mock_factory_class = MagicMock()
    mock_factory_class.get_candle.return_value = AsyncMock()
    mock_factory_mod.CandlesFactory = mock_factory_class
    sys.modules['hummingbot.data_feed.candles_feed.candles_factory'] = mock_factory_mod

    # 2. Delayed imports of HB logic (now safe)
    from hummingbot.strategy_v2.backtesting.backtesting_engine_base import BacktestingEngineBase
    from config import settings

    async def _async_run():
        local_engine = BacktestingEngineBase()
        local_engine.allow_download = False # ENFORCE LOCAL ONLY
        
        # Reconstruct controller config
        if isinstance(config_data, str):
            controller_config = local_engine.get_controller_config_instance_from_yml(
                config_path=config_data,
                controllers_conf_dir_path=settings.app.controllers_path,
                controllers_module=settings.app.controllers_module
            )
        else:
            controller_config = local_engine.get_controller_config_instance_from_dict(
                config_data=config_data,
                controllers_module=settings.app.controllers_module
            )
            
        try:
            bt_result = await local_engine.run_backtesting(
                controller_config=controller_config,
                trade_cost=trade_cost,
                start=start,
                end=end,
                backtesting_resolution=resolution
            )
            
            # Format result for visualization (with downsampling)
            processed_data_dict = bt_result.get("processed_data")
            processed_data_json = {}
            if processed_data_dict and "features" in processed_data_dict:
                df = processed_data_dict["features"].fillna(0)
                if len(df) > 5000:
                    step = len(df) // 5000
                    df = df.iloc[::step]
                if "timestamp" in df.columns:
                    # reset_index(drop=True) avoids ambiguity if 'timestamp' is also an index level
                    df = df.reset_index(drop=True).sort_values("timestamp")
                processed_data_json = df.to_dict()
            
            executors_info = [e.to_dict() for e in bt_result["executors"]]
            summary = bt_result["results"]
            
            # Convert results to native types for pickling
            return {
                "trading_pair": controller_config.trading_pair,
                "net_pnl": float(summary.get("net_pnl", 0)),
                "net_pnl_quote": float(summary.get("net_pnl_quote", 0)),
                "accuracy": float(summary.get("accuracy", 0)),
                "sharpe_ratio": float(summary.get("sharpe_ratio", 0) or 0),
                "max_drawdown_pct": float(summary.get("max_drawdown_pct", 0)),
                "profit_factor": float(summary.get("profit_factor", 0)),
                "total_positions": int(summary.get("total_positions", 0)),
                "processed_data": processed_data_json,
                "executors": executors_info,
                "config": config_data if isinstance(config_data, dict) else {},
                "performance": bt_result.get("performance", {})
            }
        finally:
            # Cleanup any lingering tasks
            try:
                tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
                for task in tasks: task.cancel()
            except: pass

    # Run in a fresh loop in this process
    return asyncio.run(_async_run())


# Global state for tracking active sync tasks
ACTIVE_SYNC_TASKS = set()

@router.get("/candles/status")
async def get_candles_status():
    """Get the status of cached candles on the server using fast binary probing."""
    try:
        cache_dir = Path(hummingbot.data_path()) / "candles"
        if not cache_dir.exists():
            return {"cached_files": []}
            
        files = []
        for f in cache_dir.glob("*.csv"):
            try:
                # Optimized metadata probe
                min_ts, max_ts, row_count = None, None, 0
                file_size = f.stat().st_size
                if file_size == 0: continue
                
                # Extract pair/interval from filename: binance_BTC-USDT_1m.csv
                parts = f.stem.split("_")
                if len(parts) >= 3:
                    connector = parts[0]
                    interval = parts[-1]
                    trading_pair = "_".join(parts[1:-1])
                else:
                    connector, trading_pair, interval = "unknown", f.stem, "unknown"

                with open(f, 'rb') as fh:
                    # 1. Start TS (skip header)
                    fh.readline()
                    first_line = fh.readline()
                    if first_line:
                        min_ts = int(float(first_line.split(b',')[0]))
                    
                    # 2. End TS (last 1KB)
                    fh.seek(0, 2)
                    fh.seek(max(0, file_size - 1024), 0)
                    last_lines = fh.read().splitlines()
                    for line in reversed(last_lines):
                        if line.strip() and b',' in line:
                            try:
                                max_ts = int(float(line.split(b',')[0]))
                                break
                            except: continue
                    
                    # 3. Fast Row Count
                    fh.seek(0)
                    row_count = sum(line.count(b'\n') for line in iter(lambda: fh.read(1024 * 1024), b''))

                files.append({
                    "file": f.name,
                    "connector": connector,
                    "trading_pair": trading_pair,
                    "interval": interval,
                    "count": row_count,
                    "start": min_ts,
                    "end": max_ts
                })
            except:
                continue
        return {"cached_files": sorted(files, key=lambda x: x['file'])}
    except Exception as e:
        return {"error": str(e)}

@router.get("/candles/csv")
async def download_candles_csv(connector: str, trading_pair: str, interval: str):
    """Download the raw CSV file for a specific pair and interval."""
    try:
        from pathlib import Path
        import hummingbot
        from fastapi.responses import FileResponse
        
        cache_dir = Path(hummingbot.data_path()) / "candles"
        filename = f"{connector}_{trading_pair}_{interval}.csv"
        cache_file = cache_dir / filename
        
        if not cache_file.exists():
            return {"error": f"File {filename} not found on server."}
            
        return FileResponse(
            path=cache_file,
            filename=filename,
            media_type='text/csv'
        )
    except Exception as e:
        return {"error": str(e)}

@router.post("/candles/sync")
async def sync_candles(backtesting_config: BacktestingConfig):
    """Prefetch and cache candles with automatic buffer padding."""
    import asyncio
    current_task = asyncio.current_task()
    try:
        from types import SimpleNamespace
        from pathlib import Path
        import hummingbot
        import time
        from hummingbot.data_feed.candles_feed.candles_base import CandlesBase

        interval = backtesting_config.backtesting_resolution
        start_time = int(backtesting_config.start_time)
        end_time = int(backtesting_config.end_time)
        
        # 1. Determine controller config
        if isinstance(backtesting_config.config, dict) and "connector_name" in backtesting_config.config:
            connector_name = backtesting_config.config["connector_name"]
            trading_pair = backtesting_config.config["trading_pair"]
            controller_config = SimpleNamespace(
                connector_name=connector_name,
                trading_pair=trading_pair,
                candles_config=backtesting_config.config.get("candles_config", [])
            )
        else:
            # Fallback for complex configs (less common for direct sync)
            engine = BacktestingEngineBase()
            if isinstance(backtesting_config.config, str):
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
            connector_name = controller_config.connector_name
            trading_pair = controller_config.trading_pair

        # --- FAST PRE-CHECK (Ultra Lazy) ---
        cache_dir = Path(hummingbot.data_path()) / "candles"
        filename = f"{connector_name}_{trading_pair}_{interval}.csv"
        cache_file = cache_dir / filename
        
        if cache_file.exists() and cache_file.stat().st_size > 0:
            try:
                min_ts, max_ts, row_count = None, None, 0
                with open(cache_file, 'rb') as f:
                    # Start TS
                    f.readline() # Header
                    first_line = f.readline()
                    if first_line: min_ts = int(float(first_line.split(b',')[0]))
                    
                    # End TS & Fast Count Estimate
                    f.seek(0, 2)
                    file_size = f.tell()
                    f.seek(max(0, file_size - 1024), 0)
                    last_lines = f.read().splitlines()
                    for line in reversed(last_lines):
                        if line.strip() and b',' in line:
                            try:
                                max_ts = int(float(line.split(b',')[0]))
                                break
                            except: continue
                
                # If cache hit (covers range plus small buffer for safety), return immediately
                # Logic: if existing data covers start and is close enough to end (within 1 hour for old data)
                effective_end = min(end_time, int(time.time()) - 60)
                if min_ts is not None and max_ts is not None:
                    if min_ts <= start_time and max_ts >= effective_end - 3600:
                        # Full count only once if really needed, otherwise estimate
                        row_count = int(file_size / (len(first_line) if first_line else 100)) # Approximation
                        return {
                            "status": "success",
                            "message": f"Verified [SMART HIT]: Already covering {start_time} to {max_ts}",
                            "rows": row_count
                        }
            except: pass

        # 2. Start Task Tracking
        ACTIVE_SYNC_TASKS.add(current_task)
        
        # 3. Trigger incremental padding check via Engine
        buffer_seconds = 2000 * CandlesBase.interval_to_seconds.get(interval, 60)
        padded_start = int(start_time - buffer_seconds)
        
        engine = BacktestingEngineBase()
        engine.allow_download = True 
        engine.backtesting_data_provider.max_records = 0 # Prevent TypeError in some logic
        engine.backtesting_data_provider.update_backtesting_time(padded_start, end_time)
        engine.controller = SimpleNamespace(config=controller_config)
        engine.backtesting_resolution = interval
        
        await engine.initialize_backtesting_data_provider()
        
        row_count = 0
        if cache_file.exists():
            try:
                with open(cache_file, 'rb') as f:
                    row_count = sum(line.count(b'\n') for line in iter(lambda: f.read(1024 * 1024), b''))
            except: row_count = -1
        
        return {
            "status": "success", 
            "message": f"Incremental sync complete. Current rows: {row_count:,}",
            "rows": row_count
        }
    except asyncio.CancelledError:
        print(f"🛑 [SYNC CANCELLED] Task stopped for {backtesting_config.config.get('trading_pair', 'unknown')}")
        return {"status": "cancelled", "message": "Sync was force stopped by user."}
    except Exception as e:
        return {"error": str(e)}
    finally:
        ACTIVE_SYNC_TASKS.discard(current_task)

@router.post("/candles/stop-all")
async def stop_all_sync_tasks():
    """Force stop all active candle sync background tasks."""
    count = len(ACTIVE_SYNC_TASKS)
    for task in list(ACTIVE_SYNC_TASKS):
        task.cancel()
    return {"status": "success", "cancelled_count": count, "message": f"Signaled cancellation for {count} tasks."}

@router.post("/run-backtesting")
async def run_backtesting(backtesting_config: BacktestingConfig):
    """
    Run a single backtesting simulation.
    """
    try:
        engine = BacktestingEngineBase()
        engine.allow_download = False  # ENFORCE LOCAL ONLY for simulation (as per user request)
        if isinstance(backtesting_config.config, str):
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
            
        backtesting_results = await engine.run_backtesting(
            controller_config=controller_config,
            trade_cost=backtesting_config.trade_cost,
            start=int(backtesting_config.start_time),
            end=int(backtesting_config.end_time),
            backtesting_resolution=backtesting_config.backtesting_resolution
        )
        
        # Format response for frontend with optimization
        processed_data_dict = backtesting_results.get("processed_data")
        if processed_data_dict and "features" in processed_data_dict:
            df = processed_data_dict["features"].fillna(0)
            # Optimization: Downsample if too many rows to avoid JSON serialization timeouts
            if len(df) > 5000:
                step = len(df) // 5000
                df = df.iloc[::step]
            
            # Ensure timestamp is included and sorted for visualization
            if "timestamp" in df.columns:
                # reset_index(drop=True) avoids ambiguity if 'timestamp' is also an index level
                df = df.reset_index(drop=True).sort_values("timestamp")
            
            import asyncio
            from concurrent.futures import ThreadPoolExecutor
            loop = asyncio.get_event_loop()
            # Perform heavy CPU-bound to_dict in a thread to keep event loop responsive
            with ThreadPoolExecutor() as pool:
                processed_data_json = await loop.run_in_executor(pool, df.to_dict)
        else:
            processed_data_json = {}
        
        executors_info = [e.to_dict() for e in backtesting_results["executors"]]
        
        return {
            "executors": executors_info,
            "processed_data": processed_data_json,
            "results": backtesting_results["results"],
            "performance": backtesting_results.get("performance", {})
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {
            "error": str(e),
            "executors": [],
            "processed_data": {},
            "results": {"net_pnl": 0, "total_positions": 0, "close_types": {}}
        }

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
    semaphore = asyncio.Semaphore(5)
    
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
                engine.allow_download = True # Explicitly allow network download for batch-sync endpoint
                
                await engine.initialize_backtesting_data_provider()
                
                # Get row count
                cache_dir = Path(hummingbot.data_path()) / "candles"
                filename = f"{controller_config.connector_name}_{controller_config.trading_pair}_{interval}.csv"
                cache_file = cache_dir / filename
                row_count = 0
                if cache_file.exists():
                    try:
                        with open(cache_file, 'rb') as f:
                            row_count = sum(line.count(b'\n') for line in iter(lambda: f.read(1024 * 1024), b''))
                    except:
                        row_count = -1
                
                # Short delay between requests
                await asyncio.sleep(0.2)
                
                return {"pair": controller_config.trading_pair, "interval": interval, "status": "success", "rows": row_count}
            except Exception as e:
                pair = config.config.get("trading_pair", "Unknown") if isinstance(config.config, dict) else "Unknown"
                interval = config.backtesting_resolution
                return {"pair": pair, "interval": interval, "status": "error", "message": str(e)}
    
    # Launch ALL sync tasks (semaphore will limit actual concurrency to 3)
    tasks = [sync_single_pair(config) for config in batch_configs]
    results = await asyncio.gather(*tasks)
    
    success_count = sum(1 for r in results if r.get("status") == "success")
    return {"results": list(results), "success": success_count, "total": len(batch_configs)}

@router.post("/batch-run")
async def batch_run_backtesting(batch_configs: list[BacktestingConfig]):
    """
    Super-Accelerated Batch Backtesting:
    Phase 1: Concurrent Cache Check (Low Latency)
    Phase 2: Multi-Process Execution (ProcessPoolExecutor) for true 10-core parallelism.
    """
    import asyncio
    import multiprocessing as mp
    import os
    from concurrent.futures import ProcessPoolExecutor
    from pathlib import Path
    import hummingbot
    
    # Phase 1: FAST Cache Verification (Skip redundant memory loading in main process)
    async def warm_cache(config: BacktestingConfig):
        try:
            if not isinstance(config.config, dict): return False
            
            trading_pair = config.config.get("trading_pair", "BTC-USDT")
            connector = config.config.get("connector_name", "binance")
            interval = config.backtesting_resolution
            
            cache_dir = Path(hummingbot.data_path()) / "candles"
            filename = f"{connector}_{trading_pair}_{interval}.csv"
            cache_file = cache_dir / filename
            
            # [LATENCY OPTIMIZATION]
            # If the file exists, we DON'T load it in the main process.
            # We trust the child processes to handle their own IO if the file is already there.
            if cache_file.exists() and cache_file.stat().st_size > 0:
                return True
                
            # Only if file is missing, we use the engine to potentially download (if allowed)
            engine = BacktestingEngineBase()
            engine.allow_download = False # STRICT-LOCAL for backtesting warm-up
            # engine.allow_download is user-controlled via config or global flag
            # For now, we respect the engine's default or the last user set value
            engine.backtesting_data_provider.update_backtesting_time(
                int(config.start_time), int(config.end_time))
            engine.backtesting_resolution = interval
            
            from types import SimpleNamespace
            engine.controller = SimpleNamespace(config=SimpleNamespace(
                connector_name=connector, trading_pair=trading_pair, candles_config=[]
            ))
            # This only runs if the cache check above failed
            await engine.initialize_backtesting_data_provider()
            return True
        except: return False

    # Launch cache checks. With local data, this will return almost instantly.
    await asyncio.gather(*[warm_cache(cfg) for cfg in batch_configs])
    
    # Phase 2: CPU Bound Backtesting - Using ProcessPool with 'spawn' context
    loop = asyncio.get_running_loop()
    spawn_ctx = mp.get_context('spawn')
    
    # Limit max_workers to 8 to avoid saturation or descriptor exhaustion
    # Also provides more stability than dynamic CPU count
    with ProcessPoolExecutor(max_workers=10, mp_context=spawn_ctx) as pool:
        tasks = []
        for config in batch_configs:
            task = loop.run_in_executor(
                pool,
                run_process_safe_backtest,
                config.config,
                int(config.start_time),
                int(config.end_time),
                config.backtesting_resolution,
                config.trade_cost
            )
            tasks.append(task)
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
    
    final_results = []
    for i, res in enumerate(results):
        if isinstance(res, Exception):
            cfg = batch_configs[i]
            pair = cfg.config.get("trading_pair", "Unknown") if isinstance(cfg.config, dict) else "Unknown"
            final_results.append({
                "trading_pair": pair, "error": str(res),
                "net_pnl": 0, "net_pnl_quote": 0, "accuracy": 0, "sharpe_ratio": 0,
                "max_drawdown_pct": 0, "profit_factor": 0, "total_positions": 0, "performance": {}
            })
        else:
            final_results.append(res)

    return {"results": final_results}
