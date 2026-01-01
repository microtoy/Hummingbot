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
    """Prefetch and cache candles without running a full backtest."""
    try:
        from types import SimpleNamespace
        # We only need the connector and range info
        if isinstance(backtesting_config.config, dict) and "connector_name" in backtesting_config.config:
            # Bypass full validation for sync
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
            
        backtesting_engine.backtesting_data_provider.update_backtesting_time(
            int(backtesting_config.start_time), int(backtesting_config.end_time))
        backtesting_engine.controller = SimpleNamespace(config=controller_config)
        
        # This will trigger the smart cache logic
        await backtesting_engine.initialize_backtesting_data_provider()
        
        return {"status": "success", "message": "Candles synced to server cache"}
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
