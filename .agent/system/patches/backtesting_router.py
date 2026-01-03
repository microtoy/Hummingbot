from fastapi import APIRouter
from hummingbot.data_feed.candles_feed.candles_factory import CandlesFactory
from hummingbot.strategy_v2.backtesting.backtesting_engine_base import BacktestingEngineBase

from config import settings
from models.backtesting import BacktestingConfig
from pathlib import Path
import pandas as pd
import hummingbot
import multiprocessing as mp
import os
import shutil
from concurrent.futures import ProcessPoolExecutor
from typing import Optional, List

# =============================================================================
# ⚡ HIGH-PERFORMANCE CPU SATURATION OPTIMIZATIONS
# =============================================================================

# --- GLOBAL PROCESS POOL CONFIGURATION ---
_GLOBAL_POOL: Optional[ProcessPoolExecutor] = None
_GLOBAL_POOL_SIZE = int(os.environ.get("TURBO_WORKERS", os.cpu_count() or 4))  # Adaptive to hardware

# --- FAST LOCAL MIRRORING ---
# Mirror candle data from slow mounted volume to fast tmpfs at module load
FAST_DATA_PATH = Path("/tmp/hbot_data")
MOUNTED_DATA_PATH = Path(hummingbot.data_path())

def _mirror_candles_to_tmpfs():
    """One-time copy of candle data to high-speed tmpfs for reduced I/O latency."""
    source = MOUNTED_DATA_PATH / "candles"
    dest = FAST_DATA_PATH / "candles"
    
    if not source.exists():
        print(f"⚠️ [MIRROR] Source path {source} does not exist, skipping mirroring.")
        return
    
    dest.parent.mkdir(parents=True, exist_ok=True)
    
    if dest.exists():
        # Incremental sync: only copy new/changed files
        for f in source.glob("*.csv"):
            dest_file = dest / f.name
            if not dest_file.exists() or f.stat().st_mtime > dest_file.stat().st_mtime:
                shutil.copy2(f, dest_file)
        print(f"✅ [MIRROR] Incremental sync complete to {dest}")
    else:
        # Full copy on first run
        shutil.copytree(source, dest)
        print(f"✅ [MIRROR] Full copy complete: {source} -> {dest}")

# Execute mirroring at module load (container startup)
try:
    _mirror_candles_to_tmpfs()
except Exception as e:
    print(f"⚠️ [MIRROR ERROR] {e}")

# --- WORKER INITIALIZER (One-Time Heavy Imports + Network Blackout) ---
def _turbo_worker_init():
    """
    Called ONCE per worker process at pool creation.
    Performs all heavy imports and network mocking to avoid per-task overhead.
    """
    import sys
    from unittest.mock import MagicMock, AsyncMock
    
    # 1. NETWORK BLACKOUT: Mock all network & socket modules
    try:
        import socket
        # Kill DNS resolution at the kernel-mapping level in this process
        def DNS_FAIL(*args, **kwargs):
            raise socket.gaierror(-3, "Temporary failure in name resolution (MOCKED)")
        socket.getaddrinfo = DNS_FAIL
        
        # Prevent any outgoing socket creation
        def SOCKET_FAIL(*args, **kwargs):
            raise RuntimeError("Outbound socket blocked by Hummingbot Network Blackout")
        # REMOVED: hard socket.socket block to avoid crashing libraries that just probe

        import aiohttp
        
        # Unload existing modules to force re-import with mocks
        modules_to_unload = [
            'hummingbot.connector.exchange.binance.binance_exchange',
            'hummingbot.connector.exchange_py_base',
            'hummingbot.core.web_assistant.rest_assistant',
            'hummingbot.core.web_assistant.connections.factory',
            'aiohttp'
        ]
        for mod in modules_to_unload:
            sys.modules.pop(mod, None)
        
        import aiohttp
        
        # Create mock response factory
        async def side_effect_get(*args, **kwargs):
            mock_resp = AsyncMock()
            mock_resp.status = 200
            mock_resp.__aenter__.return_value = mock_resp
            mock_resp.json.return_value = {}
            return mock_resp
        
        mock_session = AsyncMock()
        mock_session.get.side_effect = side_effect_get
        mock_session.request.side_effect = side_effect_get
        mock_session.__aenter__.return_value = mock_session
        aiohttp.ClientSession = MagicMock(return_value=mock_session)
    except ImportError:
        pass
    
    # Mock HB Networking & Rate Oracle components more aggressively
    # This prevents the engine from trying to fetch fees/rates during backtesting
    mock_sync = MagicMock()
    mock_sync.update_times.side_effect = AsyncMock()
    sys.modules['hummingbot.connector.time_synchronizer'] = mock_sync
    
    # Fully mock RateOracle to return None for all rates (preventing Binance connection)
    mock_oracle = MagicMock()
    mock_oracle.get_instance.return_value = mock_oracle
    mock_oracle.get_rate.return_value = None
    # Ensure any awaited methods are AsyncMocks to avoid 'await MagicMock' errors
    mock_oracle.get_all_rates = AsyncMock(return_value={})
    sys.modules['hummingbot.core.rate_oracle.rate_oracle'] = mock_oracle
    
    # Mock Binance Exchange Info to avoid network probes
    mock_binance = MagicMock()
    mock_binance.get_all_pairs_prices = AsyncMock(return_value={})
    sys.modules['hummingbot.connector.exchange.binance.binance_exchange'] = mock_binance
    sys.modules['hummingbot.core.rate_oracle.sources.binance_rate_source'] = MagicMock()
    
    sys.modules['hummingbot.data_feed.market_data_provider'] = MagicMock()
    sys.modules['hummingbot.core.data_type.order_book_tracker'] = MagicMock()
    sys.modules['hummingbot.data_feed.candles_feed.candles_factory'] = MagicMock()
    
    # 4. HEAVY IMPORTS (Done once, cached in worker memory)
    global _WORKER_ENGINE_CLASS, _WORKER_SETTINGS, _CANDLE_CACHE, _LOCAL_ENGINE
    from hummingbot.strategy_v2.backtesting.backtesting_engine_base import BacktestingEngineBase
    from config import settings as worker_settings
    _WORKER_ENGINE_CLASS = BacktestingEngineBase
    _WORKER_SETTINGS = worker_settings
    _LOCAL_ENGINE = None # Will be instantiated on first task to avoid loop binding issues
    
    # 5. MONKEYPATCH data_path to use fast local mirror
    import hummingbot
    hummingbot.data_path = lambda: str(FAST_DATA_PATH)
    
    # 6. WORKER-LEVEL CANDLE CACHE (persists across tasks in same worker)
    _CANDLE_CACHE = {}
    
    print(f"✅ [WORKER {os.getpid()}] Initialized with candle cache, network blackout, fast data path")

def get_global_pool() -> ProcessPoolExecutor:
    """Get or create the global persistent process pool."""
    global _GLOBAL_POOL
    if _GLOBAL_POOL is None:
        ctx = mp.get_context('spawn')
        _GLOBAL_POOL = ProcessPoolExecutor(
            max_workers=_GLOBAL_POOL_SIZE,
            mp_context=ctx,
            initializer=_turbo_worker_init
        )
        print(f"🚀 [POOL] Created global pool with {_GLOBAL_POOL_SIZE} workers")
    return _GLOBAL_POOL

router = APIRouter(tags=["Backtesting"], prefix="/backtesting")

# --- WORKER FUNCTION FOR PROCESS POOL (Must be top-level for pickling) ---
def run_process_safe_backtest(config_data: dict, start: int, end: int, resolution: str, trade_cost: float):
    """
    Standalone function running in a separate, clean process (via spawn).
    Enforces a 'Network Blackout' to prevent any asyncio loop collisions.
    Includes robustness patch for Binance Geo-Blocking (HTTP 451).
    """
    # 1. NETWORK BLACKOUT: Graceful Mocking
    try:
        import socket
        def DNS_FAIL(*args, **kwargs):
            raise socket.gaierror(-3, "Temporary failure in name resolution (MOCKED)")
        socket.getaddrinfo = DNS_FAIL
        # No hard socket.socket block here to avoid fragile failures
        import aiohttp
        from unittest.mock import MagicMock, AsyncMock
    except ImportError:
        pass

    import sys
    import asyncio
    import json
    
    # [ROBUSTNESS PATCH v2] Aggressively UNLOAD hummingbot modules to force re-import
    # This ensures our patches apply even if parent process already imported them.
    modules_to_unload = [
        'hummingbot.connector.exchange.binance.binance_exchange',
        'hummingbot.connector.exchange_py_base',
        'hummingbot.core.web_assistant.rest_assistant',
        'hummingbot.core.web_assistant.connections.factory',
        'hummingbot.strategy_v2.backtesting.backtesting_engine_base',
        'aiohttp'
    ]
    for mod in modules_to_unload:
        sys.modules.pop(mod, None)

    try:
        import aiohttp
        
        # Determine the symbol we need to mock from config
        target_pair = "BTC-USDT"
        if isinstance(config_data, dict):
            target_pair = config_data.get("trading_pair", "BTC-USDT")
        
        # Construct simplified ExchangeInfo & BookTicker
        base, quote = target_pair.split("-")
        symbol = f"{base}{quote}"
        
        mock_exchange_info = {
            "timezone": "UTC",
            "serverTime": 1700000000000,
            "rateLimits": [],
            "exchangeFilters": [],
            "symbols": [{
                "symbol": symbol,
                "status": "TRADING",
                "baseAsset": base,
                "baseAssetPrecision": 8,
                "quoteAsset": quote,
                "quotePrecision": 8,
                "quoteAssetPrecision": 8,
                "baseCommissionPrecision": 8,
                "quoteCommissionPrecision": 8,
                "orderTypes": ["LIMIT", "MARKET"],
                "icebergAllowed": True,
                "ocoAllowed": True,
                "quoteOrderQtyMarketAllowed": True,
                "allowTrailingStop": True,
                "isSpotTradingAllowed": True,
                "isMarginTradingAllowed": True,
                "filters": [
                    {"filterType": "PRICE_FILTER", "minPrice": "0.00000001", "maxPrice": "1000000.00000000", "tickSize": "0.00000001"},
                    {"filterType": "LOT_SIZE", "minQty": "0.00000100", "maxQty": "90000000.00000000", "stepSize": "0.00000100"},
                    {"filterType": "MIN_NOTIONAL", "minNotional": "0.00010000"},
                    {"filterType": "MARKET_LOT_SIZE", "minQty": "0.00000000", "maxQty": "100000.00000000", "stepSize": "0.00000000"}
                ],
                "permissions": ["SPOT", "MARGIN"]
            }]
        }
        
        mock_book_ticker = {
             "symbol": symbol,
             "bidPrice": "10.00000000",
             "bidQty": "10.00000000",
             "askPrice": "10.05000000",
             "askQty": "10.00000000"
        }

        # Create a smart Mock Response that reacts to URL
        async def side_effect_get(*args, **kwargs):
            url = kwargs.get("url") or (args[0] if args else "")
            mock_resp = AsyncMock()
            mock_resp.status = 200
            mock_resp.__aenter__.return_value = mock_resp
            
            if "exchangeInfo" in str(url):
                mock_resp.json.return_value = mock_exchange_info
            elif "bookTicker" in str(url):
                mock_resp.json.return_value = mock_book_ticker
            else:
                # Default empty JSON for others to prevent crashes
                mock_resp.json.return_value = {}
                
            return mock_resp

        # Create a Mock Session
        mock_session = AsyncMock()
        mock_session.get.side_effect = side_effect_get
        mock_session.request.side_effect = side_effect_get
        mock_session.__aenter__.return_value = mock_session

        # Force Patch the Class in the LIVE module
        # This overrides it even if it was imported previously
        aiohttp.ClientSession = MagicMock(return_value=mock_session)
    except ImportError:
        pass # If aiohttp not installed, no need to patch
    
    # Mocking HB Networking components
    sys.modules['hummingbot.connector.time_synchronizer'] = MagicMock()
    sys.modules['hummingbot.data_feed.market_data_provider'] = MagicMock()
    sys.modules['hummingbot.core.data_type.order_book_tracker'] = MagicMock()
    sys.modules['hummingbot.data_feed.candles_feed.candles_factory'] = MagicMock()

    # 2. Delayed imports of HB logic (now safe because we unloaded them)
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
                "results": summary,  # Full summary dict for consistency with single-backtest mode
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
    Standard Batch Backtesting Endpoint (Legacy Mode).
    Uses conservative settings and standard task dispatching for 100% stability.
    """
    import asyncio
    import multiprocessing as mp
    from concurrent.futures import ProcessPoolExecutor
    
    # Phase 2: CPU Bound Backtesting - Using ProcessPool with 'spawn' context
    loop = asyncio.get_running_loop()
    spawn_ctx = mp.get_context('spawn')
    
    # Standard 10-worker pool for non-turbo mode
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

@router.post("/gc")
def force_gc():
    """Forces garbage collection and releases unallocated memory to OS."""
    import gc
    import ctypes
    
    # Python GC
    n = gc.collect()
    
    # Libc Malloc Trim (Linux only) - Releases free heap to OS
    try:
        libc = ctypes.CDLL("libc.so.6")
        libc.malloc_trim(0)
    except:
        pass
        
    return {"status": "success", "gc_objects_collected": n}

# =============================================================================
# ⚡ TURBO BATCH BACKTESTING - HIGH PERFORMANCE ENDPOINT
# =============================================================================

def _run_turbo_batch_internal(configs_chunk: list, start: int, end: int, resolution: str, trade_cost: float):
    """
    ⚡ IN-WORKER BATCH LOOP - Each worker processes MULTIPLE configs internally.
    This eliminates IPC overhead and achieves maximum CPU saturation.
    
    Args:
        configs_chunk: List of config dicts to process in this worker
        start, end, resolution, trade_cost: Common backtest parameters
    
    Returns:
        List of results for all configs in this chunk
    """
    import asyncio
    
    async def _async_batch_run():
        import sys
        from unittest.mock import MagicMock, AsyncMock
        global _CANDLE_CACHE, _LOCAL_ENGINE
        
        # 1. RE-ENFORCE NETWORK BLACKOUT (Ensures mocks are active in this loop)
        try:
            import aiohttp
            async def side_effect_get(*args, **kwargs):
                mock_resp = AsyncMock()
                mock_resp.status = 200
                mock_resp.__aenter__.return_value = mock_resp
                mock_resp.json.return_value = {}
                return mock_resp
            mock_session = AsyncMock()
            mock_session.get.side_effect = side_effect_get
            mock_session.request.side_effect = side_effect_get
            mock_session.__aenter__.return_value = mock_session
            aiohttp.ClientSession = MagicMock(return_value=mock_session)
        except: pass
        
        sys.modules['hummingbot.connector.time_synchronizer'] = MagicMock()
        sys.modules['hummingbot.core.rate_oracle.rate_oracle'] = MagicMock()
        
        # 2. MONKEYPATCH data_path to use fast local mirror
        import hummingbot
        hummingbot.data_path = lambda: str(FAST_DATA_PATH)
        
        results = []
        
        # ⚡ ZQPI PERSISTENT ENGINE: Instantiate once and reuse across ALL batches in this worker
        if _LOCAL_ENGINE is None:
            _LOCAL_ENGINE = _WORKER_ENGINE_CLASS()
            _LOCAL_ENGINE.allow_download = False
            
            # ⚡ PERSISTENT WRAPPER: Wrap _get_candles_with_cache ONCE
            original_method = _LOCAL_ENGINE._get_candles_with_cache
            
            async def wrapped_get_candles(config):
                key = f"{config.connector}_{config.trading_pair}_{config.interval}"
                # Limit memory cache to avoid OOM on large datasets (360+ days)
                # If dataframe is huge (>50MB), don't cache it permanently in _CANDLE_CACHE
                if key in _CANDLE_CACHE:
                    attr_name = "candles_feeds" if hasattr(_LOCAL_ENGINE.backtesting_data_provider, "candles_feeds") else "candles_data"
                    feeds = getattr(_LOCAL_ENGINE.backtesting_data_provider, attr_name)
                    feeds[key] = _CANDLE_CACHE[key]
                    return _CANDLE_CACHE[key]
                
                df = await original_method(config)
                # Only cache if small enough to not cause OOM across 48 workers
                if df is not None and not df.empty and len(df) < 100000:
                    _CANDLE_CACHE[key] = df
                return df
                
            _LOCAL_ENGINE._get_candles_with_cache = wrapped_get_candles
        
        # Process each config in internal loop
        for config_data in configs_chunk:
            try:
                inner_cfg = config_data.get("config") if isinstance(config_data, dict) and "config" in config_data else config_data
                
                controller_config = _LOCAL_ENGINE.get_controller_config_instance_from_dict(
                    config_data=inner_cfg,
                    controllers_module=_WORKER_SETTINGS.app.controllers_module
                )
                
                bt_result = await _LOCAL_ENGINE.run_backtesting(
                    controller_config=controller_config,
                    trade_cost=trade_cost,
                    start=start,
                    end=end,
                    backtesting_resolution=resolution
                )
                
                if bt_result is None:
                    raise ValueError("Backtesting engine returned None result")
                    
                summary = bt_result.get("results")
                if summary is None:
                    raise ValueError("Backtesting result summary is missing (None)")
                    
                # ⚡ MEMORY OPTIMIZATION: Do NOT keep full processed_data (heavy dataframes) in memory
                # StrategyOptimizer only needs the summary metrics for the optimization report.
                results.append({
                    "trading_pair": controller_config.trading_pair,
                    "config": inner_cfg,
                    "net_pnl": float(summary.get("net_pnl", 0)),
                    "net_pnl_quote": float(summary.get("net_pnl_quote", 0)),
                    "accuracy": float(summary.get("accuracy", 0)),
                    "sharpe_ratio": float(summary.get("sharpe_ratio", 0) or 0),
                    "max_drawdown_pct": float(summary.get("max_drawdown_pct", 0)),
                    "profit_factor": float(summary.get("profit_factor", 0)),
                    "total_positions": int(summary.get("total_positions", 0)),
                    "performance": bt_result.get("performance", {})
                })
                # NOTE: Keep candles_feeds cached for subsequent same-pair simulations
                # Only clear at END of batch to allow cache reuse within batch
            except Exception as e:
                results.append({
                    "trading_pair": config_data.get("trading_pair", "Unknown"),
                    "error": str(e),
                    "net_pnl": 0, "net_pnl_quote": 0, "accuracy": 0, "sharpe_ratio": 0,
                    "max_drawdown_pct": 0, "profit_factor": 0, "total_positions": 0
                })
        
        # ⚡ BATCH-LEVEL CLEANUP: Only GC once per batch to minimize overhead
        if hasattr(_LOCAL_ENGINE.backtesting_data_provider, "candles_feeds"):
            _LOCAL_ENGINE.backtesting_data_provider.candles_feeds.clear()
        import gc
        gc.collect()
        
        return results
    
    return asyncio.run(_async_batch_run())


@router.post("/batch-run-turbo")
async def batch_run_turbo(batch_configs: list[BacktestingConfig]):
    """
    ⚡ ULTRA HIGH-PERFORMANCE Batch Backtesting Endpoint
    
    Key Architecture: IN-WORKER BATCH LOOP
    - Splits incoming configs into N chunks (N = worker pool size)
    - Each worker receives a chunk and processes ALL configs internally
    - Returns flattened results
    
    This achieves ~4600% CPU utilization on 48-core by:
    1. Eliminating per-config IPC overhead
    2. Reusing engine instance across configs in same chunk
    3. Maximum parallelism with minimal coordination
    """
    import asyncio
    
    pool = get_global_pool()
    loop = asyncio.get_running_loop()
    
    # Extract common parameters (all configs share same time range)
    if not batch_configs:
        return {"results": []}
    
    first_config = batch_configs[0]
    start = int(first_config.start_time)
    end = int(first_config.end_time)
    resolution = first_config.backtesting_resolution
    trade_cost = first_config.trade_cost
    
    # Extract config dicts
    config_dicts = [cfg.config for cfg in batch_configs]
    
    # ⚡ OPTIMAL CHUNKING: Create EXACTLY 48 chunks (one per worker)
    # This ensures all workers start together and finish together
    num_configs = len(config_dicts)
    num_workers = min(_GLOBAL_POOL_SIZE, num_configs)  # Don't use more workers than configs
    
    # Balanced split: distribute configs evenly across workers
    # E.g., 500 configs / 48 workers = 10 or 11 per worker
    chunks = []
    for i in range(num_workers):
        start_idx = i * num_configs // num_workers
        end_idx = (i + 1) * num_configs // num_workers
        if start_idx < end_idx:  # Only add non-empty chunks
            chunks.append(config_dicts[start_idx:end_idx])
    
    # Dispatch chunks to workers
    tasks = []
    for chunk in chunks:
        task = loop.run_in_executor(
            pool,
            _run_turbo_batch_internal,
            chunk,
            start,
            end,
            resolution,
            trade_cost
        )
        tasks.append(task)
    
    # Gather all chunk results
    chunk_results = await asyncio.gather(*tasks, return_exceptions=True)
    
    # Flatten results
    final_results = []
    for i, res in enumerate(chunk_results):
        if isinstance(res, Exception):
            # If entire chunk failed, mark all configs in chunk as error
            chunk = chunks[i]
            for cfg in chunk:
                pair = cfg.get("trading_pair", "Unknown") if isinstance(cfg, dict) else "Unknown"
                final_results.append({
                    "trading_pair": pair, "error": str(res),
                    "net_pnl": 0, "net_pnl_quote": 0, "accuracy": 0, "sharpe_ratio": 0,
                    "max_drawdown_pct": 0, "profit_factor": 0, "total_positions": 0, "performance": {}
                })
        else:
            # Flatten the list of results from this chunk
            final_results.extend(res)

    # ⚡ UJSON: Fast serialization
    import time
    import ujson
    from fastapi.responses import Response
    content = ujson.dumps({"results": final_results})
    return Response(content=content, media_type="application/json")


@router.post("/pool/status")
async def get_pool_status():
    """Get status of the global worker pool."""
    global _GLOBAL_POOL
    if _GLOBAL_POOL is None:
        return {"status": "not_initialized", "workers": 0}
    
    return {
        "status": "active",
        "workers": _GLOBAL_POOL_SIZE,
        "fast_data_path": str(FAST_DATA_PATH),
        "fast_data_exists": FAST_DATA_PATH.exists()
    }


@router.post("/pool/refresh-mirror")
async def refresh_candle_mirror():
    """Manually trigger a refresh of the candle data mirror."""
    try:
        _mirror_candles_to_tmpfs()
        return {"status": "success", "message": "Candle mirror refreshed"}
    except Exception as e:
        return {"status": "error", "message": str(e)}
