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
    """Get the status of cached candles on the server."""
    try:
        cache_dir = Path(hummingbot.data_path()) / "candles"
        if not cache_dir.exists():
            return {"cached_files": []}
            
        files = []
        for f in cache_dir.glob("*.csv"):
            try:
                df = pd.read_csv(f, usecols=["timestamp"])
                if not df.empty:
                    files.append({
                        "file": f.name,
                        "count": len(df),
                        "start": int(df["timestamp"].min()),
                        "end": int(df["timestamp"].max())
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
        # 1. Determine controller config
        if isinstance(backtesting_config.config, dict) and "connector_name" in backtesting_config.config:
            controller_config = SimpleNamespace(
                connector_name=backtesting_config.config["connector_name"],
                trading_pair=backtesting_config.config["trading_pair"],
                candles_config=backtesting_config.config.get("candles_config", [])
            )
        elif isinstance(backtesting_config.config, str):
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
            
        # 2. Pad the start time with buffer (Generous 2000 candles to ensure hits)
        from hummingbot.data_feed.candles_feed.candles_base import CandlesBase
        interval = backtesting_config.backtesting_resolution
        buffer_seconds = 2000 * CandlesBase.interval_to_seconds.get(interval, 60)
        padded_start = int(backtesting_config.start_time - buffer_seconds)
        
        # 3. Trigger initialization (which hits the smart cache in engine)
        backtesting_engine.backtesting_data_provider.update_backtesting_time(
            padded_start, int(backtesting_config.end_time))
        backtesting_engine.controller = SimpleNamespace(config=controller_config)
        backtesting_engine.backtesting_resolution = interval
        
        await backtesting_engine.initialize_backtesting_data_provider()
        
        return {"status": "success", "message": f"Synced with {buffer_seconds/3600:.1f}h buffer padding"}
    except Exception as e:
        return {"error": str(e)}

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
    Run multiple backtesting simulations in TRUE PARALLEL using asyncio.gather.
    This maximizes Mac Studio performance by running all backtests concurrently.
    """
    import asyncio
    
    async def run_single_backtest(config: BacktestingConfig):
        """Run a single backtest and return simplified results."""
        try:
            # Create a fresh engine for each concurrent task to avoid state conflicts
            engine = BacktestingEngineBase()
            
            if isinstance(config.config, str):
                controller_config = engine.get_controller_config_instance_from_yml(
                    config_path=config.config,
                    controllers_conf_dir_path=settings.app.controllers_path,
                    controllers_module=settings.app.controllers_module
                )
            else:
                controller_config = engine.get_controller_config_instance_from_dict(
                    config_data=config.config,
                    controllers_module=settings.app.controllers_module
                )
            
            bt_result = await engine.run_backtesting(
                controller_config=controller_config,
                trade_cost=config.trade_cost,
                start=int(config.start_time),
                end=int(config.end_time),
                backtesting_resolution=config.backtesting_resolution
            )
            
            summary = bt_result["results"]
            return {
                "trading_pair": controller_config.trading_pair,
                "net_pnl": summary.get("net_pnl", 0),
                "net_pnl_quote": summary.get("net_pnl_quote", 0),
                "accuracy": summary.get("accuracy", 0),
                "sharpe_ratio": summary.get("sharpe_ratio", 0) or 0,
                "max_drawdown_pct": summary.get("max_drawdown_pct", 0),
                "profit_factor": summary.get("profit_factor", 0),
                "total_positions": summary.get("total_positions", 0),
                "performance": bt_result.get("performance", {})
            }
        except Exception as e:
            return {
                "trading_pair": config.config.get("trading_pair", "Unknown") if isinstance(config.config, dict) else "Unknown",
                "error": str(e),
                "net_pnl": 0, "net_pnl_quote": 0, "accuracy": 0, "sharpe_ratio": 0,
                "max_drawdown_pct": 0, "profit_factor": 0, "total_positions": 0
            }
    
    # Launch ALL backtests concurrently - true parallelism!
    tasks = [run_single_backtest(config) for config in batch_configs]
    results = await asyncio.gather(*tasks)
    
    return {"results": list(results)}
