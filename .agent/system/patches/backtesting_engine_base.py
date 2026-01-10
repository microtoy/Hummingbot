import importlib
import inspect
import os
import hashlib
from pathlib import Path
from decimal import Decimal
from typing import Dict, List, Optional, Type, Union

import numpy as np
import pandas as pd
import yaml
import json
import time

from hummingbot.client import settings
from hummingbot.core.data_type.common import LazyDict, TradeType

# ⚡ MEGA-TURBO: Persistent mounted data path for fallback
MOUNTED_DATA_PATH = "/opt/conda/envs/hummingbot-api/lib/python3.12/site-packages/data"
from hummingbot.data_feed.candles_feed.data_types import CandlesConfig
from data.data_lake.loader import LakeLoader
from hummingbot.exceptions import InvalidController
from hummingbot.strategy_v2.backtesting.backtesting_data_provider import BacktestingDataProvider
from hummingbot.strategy_v2.backtesting.executor_simulator_base import ExecutorSimulation
from hummingbot.strategy_v2.backtesting.executors_simulator.dca_executor_simulator import DCAExecutorSimulator
from hummingbot.strategy_v2.backtesting.executors_simulator.position_executor_simulator import PositionExecutorSimulator
from hummingbot.strategy_v2.controllers.controller_base import ControllerBase, ControllerConfigBase
from hummingbot.strategy_v2.controllers.directional_trading_controller_base import (
    DirectionalTradingControllerConfigBase,
)
from hummingbot.strategy_v2.controllers.market_making_controller_base import MarketMakingControllerConfigBase
from hummingbot.strategy_v2.executors.dca_executor.data_types import DCAExecutorConfig
from hummingbot.strategy_v2.executors.position_executor.data_types import PositionExecutorConfig
from hummingbot.strategy_v2.models.base import RunnableStatus
from hummingbot.strategy_v2.models.executor_actions import CreateExecutorAction, StopExecutorAction
from hummingbot.strategy_v2.models.executors import CloseType
from hummingbot.strategy_v2.models.executors_info import ExecutorInfo


class BacktestingEngineBase:
    __controller_class_cache = LazyDict[str, Type[ControllerBase]]()

    def __init__(self):
        self.controller = None
        self.backtesting_resolution = None
        self.backtesting_data_provider = BacktestingDataProvider(connectors={})
        self.position_executor_simulator = PositionExecutorSimulator()
        self.dca_executor_simulator = DCAExecutorSimulator()
        self.allow_download = False  # Milestone Fix: Enable download by default to support 1h indicators

    @classmethod
    def load_controller_config(cls,
                               config_path: str,
                               controllers_conf_dir_path: str = settings.CONTROLLERS_CONF_DIR_PATH) -> Dict:
        full_path = os.path.join(controllers_conf_dir_path, config_path)
        with open(full_path, 'r') as file:
            config_data = yaml.safe_load(file)
        return config_data

    @classmethod
    def get_controller_config_instance_from_yml(cls,
                                                config_path: str,
                                                controllers_conf_dir_path: str = settings.CONTROLLERS_CONF_DIR_PATH,
                                                controllers_module: str = settings.CONTROLLERS_MODULE) -> ControllerConfigBase:
        config_data = cls.load_controller_config(config_path, controllers_conf_dir_path)
        return cls.get_controller_config_instance_from_dict(config_data, controllers_module)

    @classmethod
    def get_controller_config_instance_from_dict(cls,
                                                 config_data: dict,
                                                 controllers_module: str = settings.CONTROLLERS_MODULE) -> ControllerConfigBase:
        controller_type = config_data.get('controller_type')
        controller_name = config_data.get('controller_name')

        if not controller_type or not controller_name:
            raise ValueError("Missing controller_type or controller_name in the configuration.")

        module_path = f"{controllers_module}.{controller_type}.{controller_name}"
        module = importlib.import_module(module_path)

        config_class = next((member for member_name, member in inspect.getmembers(module)
                             if inspect.isclass(member) and member not in [ControllerConfigBase,
                                                                           MarketMakingControllerConfigBase,
                                                                           DirectionalTradingControllerConfigBase]
                             and (issubclass(member, ControllerConfigBase))), None)
        if not config_class:
            raise InvalidController(f"No configuration class found in the module {controller_name}.")

        return config_class(**config_data)

    async def run_backtesting(self,
                              controller_config: ControllerConfigBase,
                              start: int, end: int,
                              backtesting_resolution: str = "1m",
                              trade_cost=0.0006):
        import time
        performance_report = {}
        
        start_time_total = time.perf_counter()
        
        controller_class = self.__controller_class_cache.get_or_add(controller_config.controller_name, controller_config.get_controller_class)
        
        # Phase 1: Data Initialization
        start_init = time.perf_counter()
        self.backtesting_data_provider.update_backtesting_time(start, end)
        try:
            await self.backtesting_data_provider.initialize_trading_rules(controller_config.connector_name)
        except Exception as e:
            print(f"⚠️ [OFFLINE MODE] Could not fetch trading rules from {controller_config.connector_name}: {e}. Using defaults.")
        self.controller = controller_class(config=controller_config, market_data_provider=self.backtesting_data_provider,
                                           actions_queue=None)
        self.backtesting_resolution = backtesting_resolution
        await self.initialize_backtesting_data_provider()
        performance_report["data_initialization"] = time.perf_counter() - start_init
        
        # Phase 2: Processed Data (Strategy Logic)
        start_processed = time.perf_counter()
        if hasattr(self.controller, "update_processed_data"):
            await self.controller.update_processed_data()
        performance_report["processed_data_calc"] = time.perf_counter() - start_processed
        
        # Phase 3: Execution Simulation
        start_sim = time.perf_counter()
        executors_info = await self.simulate_execution(trade_cost=trade_cost)
        performance_report["execution_simulation"] = time.perf_counter() - start_sim
        
        # Phase 4: Summarize
        results = self.summarize_results(executors_info, controller_config.total_amount_quote)
        
        performance_report["total_time"] = time.perf_counter() - start_time_total
        processed_data = getattr(self.controller, "processed_data", None)
        features = processed_data.get("features", []) if isinstance(processed_data, dict) else []
        performance_report["kline_count"] = len(features)
        
        return {
            "executors": executors_info,
            "results": results,
            "processed_data": getattr(self.controller, "processed_data", None),
            "performance": performance_report
        }

    async def initialize_backtesting_data_provider(self):
        # Main candle feed
        backtesting_config = CandlesConfig(
            connector=self.controller.config.connector_name,
            trading_pair=self.controller.config.trading_pair,
            interval=self.backtesting_resolution
        )
        await self._get_candles_with_cache(backtesting_config)
        
        # Additional candle feeds (if any)
        for config in self.controller.config.candles_config:
            await self._get_candles_with_cache(config)

    def _load_candle_slice(self, cache_file: Path, needed_start: int, needed_end: int) -> pd.DataFrame:
        """Loads only the required slice of a CSV or .npy file using indexed seeking logic."""
        import numpy as np
        import json
        
        npy_file = cache_file.with_suffix(".npy")
        meta_file = cache_file.with_suffix(".json")
        
        # ⚡ MEGA-TURBO: Use .npy binary format if available
        if npy_file.exists() and meta_file.exists():
            try:
                # 1. Load metadata
                with open(meta_file, 'r') as f:
                    meta = json.load(f)
                
                # 2. Memory-map the numpy array (zero-copy loading)
                data = np.load(str(npy_file), mmap_mode='r')
                
                # 3. Find slice indices
                ts_col_idx = meta["columns"].index("timestamp")
                timestamps = data[:, ts_col_idx]
                
                mask = (timestamps >= needed_start) & (timestamps <= needed_end)
                if not mask.any():
                    return pd.DataFrame()
                
                start_idx = np.flatnonzero(mask)[0]
                end_idx = np.flatnonzero(mask)[-1] + 1
                
                # 4. Create DataFrame from slice
                df = pd.DataFrame(data[start_idx:end_idx], columns=meta["columns"])
                
                # 5. Restore dtypes
                for col, dtype in meta["dtypes"].items():
                    try:
                        df[col] = df[col].astype(dtype)
                    except:
                        pass
                
                return df
            except Exception as e:
                print(f"⚠️ [NPY LOAD ERROR] {e}. Falling back to CSV.")

        # Fallback to CSV slicing (legacy)
        try:
            ts_df = pd.read_csv(cache_file, usecols=["timestamp"])
            mask = (ts_df["timestamp"] >= needed_start) & (ts_df["timestamp"] <= needed_end)
            if not mask.any():
                return pd.DataFrame()
            
            start_idx = int(mask.idxmax())
            count = int(mask.sum())
            return pd.read_csv(cache_file, header=0, skiprows=range(1, start_idx + 1), nrows=count)
        except Exception as e:
            print(f"⚠️ [SLICE ERROR] Full load: {e}")
            full_df = pd.read_csv(cache_file)
            return full_df[(full_df["timestamp"] >= needed_start) & (full_df["timestamp"] <= needed_end)].copy()

    async def _get_candles_with_cache(self, config: CandlesConfig) -> pd.DataFrame:
        """Helper to get candles with extreme efficiency using binary metadata checks and append mode."""
        import hummingbot
        import time
        from hummingbot.data_feed.candles_feed.candles_base import CandlesBase
        from hummingbot.data_feed.candles_feed.data_types import HistoricalCandlesConfig

        cache_dir = Path(hummingbot.data_path()) / "candles"
        cache_dir.mkdir(parents=True, exist_ok=True)
        
        filename = f"{config.connector}_{config.trading_pair}_{config.interval}.csv"
        cache_file = cache_dir / filename
        actual_read_path = cache_file # Default to local cache
        
        # Determine strict needed range
        candles_buffer = config.max_records * CandlesBase.interval_to_seconds[config.interval]
        needed_start = int(self.backtesting_data_provider.start_time - candles_buffer)
        needed_end = int(self.backtesting_data_provider.end_time)
        effective_needed_end = min(needed_end, int(time.time()) - 60)
        
        # --- FAST BINARY METADATA CHECK ---
        min_ts, max_ts = None, None
        
        # 1. Check for mirrored JSON metadata first (Turbo Mode)
        meta_file = cache_file.with_suffix(".json")
        if meta_file.exists():
            try:
                with open(meta_file, 'r') as jf:
                    meta = json.load(jf)
                    min_ts = meta.get("min_ts")
                    max_ts = meta.get("max_ts")
                if min_ts is not None and max_ts is not None:
                    print(f"📊 [META] Loaded coverage from JSON for {filename}: {min_ts} -> {max_ts}")
            except Exception as e:
                print(f"⚠️ [META ERROR] {meta_file}: {e}")
        
        # 2. Fallback to reading the file itself (CSV or mirroring incomplete)
        if min_ts is None or max_ts is None:
            # If cache_file (in tmpfs) doesn't exist, try the original mounted path
            search_file = cache_file
            if not search_file.exists():
                # Try falling back to mounted data path directly if mirror is missing CSV
                from hummingbot import data_path
                # Construct original path (mounted)
                mounted_file = Path(MOUNTED_DATA_PATH) / "candles" / filename
                if mounted_file.exists():
                    search_file = mounted_file
                    actual_read_path = mounted_file # 🛡️ FIX: Track that we found it on mount
                else:
                    search_file = None

            if search_file and search_file.exists() and search_file.suffix == ".csv":
                try:
                    with open(search_file, 'rb') as f:
                        # Header
                        f.readline()
                        # First data line
                        first_line = f.readline()
                        if first_line:
                            min_ts = int(float(first_line.split(b',')[0]))
                        
                        # Last data line (Last 1KB)
                        f.seek(0, 2)
                        f_size = f.tell()
                        f.seek(max(0, f_size - 1024), 0)
                        last_lines = f.read().splitlines()
                        if last_lines:
                            for line in reversed(last_lines):
                                if line.strip():
                                    max_ts = int(float(line.split(b',')[0]))
                                    break
                    print(f"📊 [DISK] Read coverage from {search_file.name}: {min_ts} -> {max_ts}")
                except Exception as e:
                    print(f"⚠️ [DISK ERROR] {e}")

        if min_ts is not None and max_ts is not None and not getattr(self, "force_download", False):
            # 🛡️ FIX: Ensure the file actually exists before we claim a hit
            # ⚡ OPTIMIZATION: Check for NPY first, then CSV
            npy_path = cache_file.with_suffix(".npy")
            if min_ts <= needed_start and max_ts >= effective_needed_end - 86400:
                if npy_path.exists():
                     print(f"✅ [CACHE HIT] {npy_path.name} (NPY) covers requirements. Slicing...")
                     result_df = self._load_candle_slice(npy_path, needed_start, needed_end)
                     key = self.backtesting_data_provider._generate_candle_feed_key(config)
                     self.backtesting_data_provider.candles_feeds[key] = result_df
                     return result_df
                elif actual_read_path.exists():
                    print(f"✅ [CACHE HIT] {filename} (CSV) covers requirements. Slicing...")
                    # 🛡️ FIX: Use the path where data was actually found (could be mount)
                    result_df = self._load_candle_slice(actual_read_path, needed_start, needed_end)
                    key = self.backtesting_data_provider._generate_candle_feed_key(config)
                    self.backtesting_data_provider.candles_feeds[key] = result_df
                    return result_df

        # --- NEW: V2 LAKE DIRECT LOAD ---
        # If cache is missing or insufficient, try pulling directly from our partitioned Lake V2
        try:
            print(f"🌊 [V2 LAKE] Attempting direct load for {config.connector}:{config.trading_pair}:{config.interval}")
            print(f"📅 [V2 RANGE] {needed_start} -> {effective_needed_end}")
            loader = LakeLoader()
            lake_df = loader.get_data(
                exchange=config.connector, 
                pair=config.trading_pair, 
                interval=config.interval, 
                start_ts=needed_start, 
                end_ts=effective_needed_end
            )
            
            if lake_df is not None and not lake_df.empty:
                print(f"✅ [V2 LAKE HIT] Loaded {len(lake_df)} rows from lake for {config.trading_pair}")
                
                # Merge with existing local CSV if present
                if cache_file.exists():
                    try:
                        old_df = pd.read_csv(cache_file)
                        lake_df = pd.concat([old_df, lake_df]).drop_duplicates(subset=["timestamp"]).sort_values("timestamp")
                        print(f"🔄 [V2 MERGE] Merged lake data with existing cache. Total: {len(lake_df)} rows")
                    except Exception as me:
                        print(f"⚠️ [V2 MERGE ERROR] {me}")
                
                # Update persistent storage
                # ⚡ OPTIMIZATION: SKIP CSV saving to reduce I/O overhead
                # lake_df.to_csv(cache_file, index=False)  <-- REMOVED
                self._save_binary_cache(cache_file, lake_df)
                
                # Return slice
                result_df = lake_df[(lake_df["timestamp"] >= needed_start) & (lake_df["timestamp"] <= needed_end)].copy()
                if not result_df.empty:
                    key = self.backtesting_data_provider._generate_candle_feed_key(config)
                    self.backtesting_data_provider.candles_feeds[key] = result_df
                    return result_df
                else:
                    print(f"⚠️ [V2 SLICE EMPTY] Lake DF had data, but slice {needed_start}->{needed_end} was empty!")
            else:
                print(f"❌ [V2 LAKE MISS] No data found in lake for {config.connector}:{config.trading_pair} in range {needed_start}->{effective_needed_end}")
        except Exception as e:
            print(f"⚠️ [V2 LAKE ERROR] {e}")
            import traceback
            traceback.print_exc()

        # 2. DOWNLOAD / SYNC logic (Only if allowed)
        if not self.allow_download:
             raise ValueError(f"❌ [CACHE INSUFFICIENT] {filename} coverage: {min_ts}->{max_ts} vs Needed: {needed_start}->{effective_needed_end}")

        # CASE A: APPEND MODE (Only suffix needed)
        if max_ts is not None and min_ts is not None and min_ts <= needed_start:
             if max_ts < effective_needed_end - 300:
                print(f"📥 [APPEND MODE] Fetching suffix for {config.trading_pair}...")
                candle_feed = hummingbot.data_feed.candles_feed.candles_factory.CandlesFactory.get_candle(config)
                suffix_df = await candle_feed.get_historical_candles(config=HistoricalCandlesConfig(
                    connector_name=config.connector, trading_pair=config.trading_pair,
                    interval=config.interval, start_time=max_ts + 1, end_time=effective_needed_end
                ))
                if suffix_df is not None and not suffix_df.empty:
                    suffix_df.to_csv(cache_file, mode='a', header=False, index=False)
                    print(f"💾 [APPENDED] {len(suffix_df)} rows to {filename}")
                    # Indexed reload
                    result_df = self._load_candle_slice(cache_file, needed_start, needed_end)
                    key = self.backtesting_data_provider._generate_candle_feed_key(config)
                    self.backtesting_data_provider.candles_feeds[key] = result_df
                    return result_df
             else:
                # Close enough, indexed reload
                result_df = self._load_candle_slice(cache_file, needed_start, needed_end)
                key = self.backtesting_data_provider._generate_candle_feed_key(config)
                self.backtesting_data_provider.candles_feeds[key] = result_df
                return result_df

        # CASE B: FULL FALLBACK (Merge or First Download)
        print(f"📥 [FULL SYNC] Fetching {config.trading_pair}")
        merged_df = await self.backtesting_data_provider.get_candles_feed(config)
        if merged_df is not None and not merged_df.empty:
             if cache_file.exists():
                 try:
                     old_df = pd.read_csv(cache_file)
                     merged_df = pd.concat([old_df, merged_df]).drop_duplicates(subset=["timestamp"]).sort_values("timestamp")
                 except: pass
             merged_df.to_csv(cache_file, index=False)
             result_df = merged_df[(merged_df["timestamp"] >= needed_start) & (merged_df["timestamp"] <= needed_end)].copy()
             key = self.backtesting_data_provider._generate_candle_feed_key(config)
             self.backtesting_data_provider.candles_feeds[key] = result_df
             return result_df
        
        return pd.DataFrame()

    def _save_binary_cache(self, cache_file: Path, df: pd.DataFrame):
        """Generates .npy and .json cache for ultra-fast loading."""
        try:
            import numpy as np
            import json
            
            # Save raw numpy array
            npy_file = cache_file.with_suffix(".npy")
            np.save(str(npy_file), df.to_numpy())
            
            # Save metadata
            meta = {
                "columns": df.columns.tolist(),
                "dtypes": {col: str(dtype) for col, dtype in df.dtypes.items()},
                "min_ts": int(df["timestamp"].min()),
                "max_ts": int(df["timestamp"].max()),
                "count": len(df)
            }
            with open(cache_file.with_suffix(".json"), 'w') as f:
                json.dump(meta, f)
                
            print(f"⚡ [CACHE GEN] Generated binary cache for {cache_file.name}")
        except Exception as e:
            print(f"⚠️ [CACHE GEN ERROR] {e}")

    async def simulate_execution(self, trade_cost: float) -> list:
        """
        Simulates market making strategy over historical data using Event-Driven Jump-Ahead.
        """
        processed_features = self.prepare_market_data()
        
        # 🛡️ ROBUST FIX: Handle empty data (e.g., missing historical periods in AWFO)
        if processed_features.empty:
            print(f"⚠️ [SIMULATION SKIP] No data available for {self.controller.config.trading_pair} in this period.")
            return []

        self.active_executor_simulations: List[ExecutorSimulation] = []
        self.stopped_executors_info: List[ExecutorInfo] = []
        
        # 1. Pre-identify all Signal Events (where signal is non-zero)
        # This allows us to skip thousands of 'silent' ticks.
        signal_events = processed_features[processed_features['signal'] != 0].index.tolist()
        signal_ptr = 0
        num_signals = len(signal_events)
        
        # 2. Pre-calculate indices for fast lookups
        all_timestamps = processed_features.index.tolist()
        ts_to_idx = {ts: i for i, ts in enumerate(all_timestamps)}
        
        connector_key = f"{self.controller.config.connector_name}_{self.controller.config.trading_pair}"
        last_ts = all_timestamps[-1]
        current_ts = all_timestamps[0]
        
        # Event Loop
        while current_ts <= last_ts:
            # --- PROCESS CURRENT TICK ---
            # Update state for the current jump target
            # ⚡ MEGA-TURBO: Avoid .loc[] and .to_dict() in hot loop
            # Use raw numpy values for max speed
            current_idx = ts_to_idx[current_ts]
            row_data = processed_features.iloc[current_idx]
            
            # Update provider with float price (avoid Decimal overhead)
            self.controller.market_data_provider.prices = {connector_key: row_data['close_bt']}
            self.controller.market_data_provider._time = current_ts
            
            # Minimal update to processed_data
            if isinstance(self.controller.processed_data, dict):
                self.controller.processed_data.update(row_data)
            
            # Update executors and collect finished ones
            # ⚡ Optimized iteration
            active_executors_info = []
            simulations_to_remove = []
            for executor in self.active_executor_simulations:
                executor_info = executor.get_executor_info_at_timestamp(current_ts)
                if executor_info.status == RunnableStatus.TERMINATED:
                    self.stopped_executors_info.append(executor_info)
                    simulations_to_remove.append(executor.config.id)
                else:
                    active_executors_info.append(executor_info)
            
            if simulations_to_remove:
                self.active_executor_simulations = [es for es in self.active_executor_simulations if es.config.id not in simulations_to_remove]
            
            self.controller.executors_info = active_executors_info
            
            # Determine Actions
            # ⚡ Avoid deep copies or complex objects where possible
            for action in self.controller.determine_executor_actions():
                if isinstance(action, CreateExecutorAction):
                    executor_simulation = self.simulate_executor(action.executor_config, processed_features.iloc[current_idx:], trade_cost)
                    if executor_simulation is not None and executor_simulation.close_type != CloseType.FAILED:
                        self.manage_active_executors(executor_simulation)
                elif isinstance(action, StopExecutorAction):
                    self.handle_stop_action(action, current_ts)

            # --- JUMP AHEAD LOGIC ---
            # We want to jump to the MINIMUM of:
            # A) Next Signal Event
            # B) Next Executor Termination
            # C) If no events, jump to the very end
            
            # A. Find next signal
            while signal_ptr < num_signals and signal_events[signal_ptr] <= current_ts:
                signal_ptr += 1
            next_signal_ts = signal_events[signal_ptr] if signal_ptr < num_signals else last_ts + 1
            
            # B. Find next closure
            next_closure_ts = last_ts + 1
            for es in self.active_executor_simulations:
                # executor_simulation.index.max() is the close timestamp
                es_close_ts = es.executor_simulation.index.max()
                if es_close_ts > current_ts:
                    next_closure_ts = min(next_closure_ts, es_close_ts)
            
            # The next significant moment
            next_ts = min(next_signal_ts, next_closure_ts)
            
            # If we are at the end or no more events, move out of loop
            if next_ts > last_ts or next_ts == current_ts:
                # If next_ts is same as current (tiny resolution issues), move by 1 tick to avoid infinite loop
                if next_ts == current_ts:
                    idx = ts_to_idx[current_ts]
                    if idx + 1 < len(all_timestamps):
                        current_ts = all_timestamps[idx + 1]
                    else:
                        break
                else:
                    break
            else:
                current_ts = next_ts

        # FINAL MERGE
        return self.controller.executors_info + self.stopped_executors_info

    async def update_state(self, row):
        key = f"{self.controller.config.connector_name}_{self.controller.config.trading_pair}"
        self.controller.market_data_provider.prices = {key: Decimal(row["close_bt"])}
        self.controller.market_data_provider._time = row["timestamp"]
        self.controller.processed_data.update(row)
        self.update_executors_info(row["timestamp"])

    def update_executors_info(self, timestamp: float):
        active_executors_info = []
        simulations_to_remove = []
        for executor in self.active_executor_simulations:
            executor_info = executor.get_executor_info_at_timestamp(timestamp)
            if executor_info.status == RunnableStatus.TERMINATED:
                self.stopped_executors_info.append(executor_info)
                simulations_to_remove.append(executor.config.id)
            else:
                active_executors_info.append(executor_info)
        self.active_executor_simulations = [es for es in self.active_executor_simulations if es.config.id not in simulations_to_remove]
        self.controller.executors_info = active_executors_info + self.stopped_executors_info

    async def update_processed_data(self, row: pd.Series):
        """
        Updates processed data in the controller with the current price and timestamp.

        Args:
            row (pd.Series): The current row of market data.
        """
        raise NotImplementedError("update_processed_data method must be implemented in a subclass.")

    def prepare_market_data(self) -> pd.DataFrame:
        """
        Prepares market data by merging candle data with strategy features.
        Optimized for idempotency and speed.
        """
        # If already prepared and contains our simulation columns, skip
        if "close_bt_decimal" in self.controller.processed_data.get("features", pd.DataFrame()).columns:
            return self.controller.processed_data["features"]

        backtesting_candles = self.controller.market_data_provider.get_candles_df(
            connector_name=self.controller.config.connector_name,
            trading_pair=self.controller.config.trading_pair,
            interval=self.backtesting_resolution
        ).add_suffix("_bt")

        if "features" not in self.controller.processed_data or self.controller.processed_data["features"].empty:
            backtesting_candles["reference_price"] = backtesting_candles["close_bt"]
            backtesting_candles["spread_multiplier"] = 1
            backtesting_candles["signal"] = 0
        else:
            features_df = self.controller.processed_data["features"].copy()
            # Remove potentially conflicting columns from features if they came from a previous join
            cols_to_drop = [c for c in features_df.columns if c.endswith("_bt") and c != "timestamp_bt"]
            if cols_to_drop:
                features_df.drop(columns=cols_to_drop, inplace=True)

            if features_df.index.name == "timestamp":
                features_df.index.name = None
            
            backtesting_candles = pd.merge_asof(backtesting_candles, features_df,
                                                left_on="timestamp_bt", right_on="timestamp",
                                                direction="backward")

        # Standardize columns
        if "timestamp_bt" in backtesting_candles.columns:
            backtesting_candles["timestamp"] = backtesting_candles["timestamp_bt"]
        
        backtesting_candles = BacktestingDataProvider.ensure_epoch_index(backtesting_candles)
        backtesting_candles.index.name = "timestamp"
        
        for base_col in ["open", "high", "low", "close", "volume"]:
            bt_col = f"{base_col}_bt"
            if bt_col in backtesting_candles.columns:
                backtesting_candles[base_col] = backtesting_candles[bt_col]
        
        backtesting_candles["close_bt_decimal"] = backtesting_candles["close"] # Float Path: Keep as float
        
        # ⚡ FIX: Use ffill for indicators and prices to prevent 0-value gaps which break charts.
        # But do NOT ffill signals to avoid "phantom triggers".
        cols_to_ffill = [c for c in backtesting_candles.columns if c != "signal"]
        backtesting_candles[cols_to_ffill] = backtesting_candles[cols_to_ffill].ffill()
        backtesting_candles.fillna(0, inplace=True)
       
        # ⚡ MEGA-TURBO: Force entire DataFrame to numeric to avoid Decimal leakage from features
        for col in backtesting_candles.columns:
            if col != "timestamp":
                try:
                    # Force conversion to float64, even for Decimal objects
                    backtesting_candles[col] = backtesting_candles[col].astype(float)
                except Exception as e:
                    # Fallback for columns that really aren't numeric
                    try:
                        backtesting_candles[col] = pd.to_numeric(backtesting_candles[col], errors='coerce')
                    except: pass
        
        self.controller.processed_data["features"] = backtesting_candles
        return backtesting_candles

    def _floatify_config(self, config):
        """Recursively converts all Decimal fields in a config object to floats, 
        bypassing Pydantic validation to prevent casting back to Decimal."""
        from decimal import Decimal
        from types import MappingProxyType
        if config is None:
            return None
            
        # 1. Handle objects (Pydantic models, etc.)
        if hasattr(config, "__dict__"):
            # Skip objects with immutable __dict__ (e.g., frozen dataclasses, certain enums)
            if isinstance(config.__dict__, MappingProxyType):
                return config
            try:
                # Update __dict__ directly to avoid triggering Pydantic validation
                for key, value in list(config.__dict__.items()):
                    if isinstance(value, Decimal):
                        config.__dict__[key] = float(value)
                    elif isinstance(value, (list, tuple)):
                        new_list = [float(v) if isinstance(v, Decimal) else self._floatify_config(v) for v in value]
                        config.__dict__[key] = new_list
                    elif hasattr(value, "__dict__") or isinstance(value, dict):
                        self._floatify_config(value)
            except TypeError:
                # If __dict__ is not modifiable, skip this object
                pass
        
        # 2. Handle dicts
        elif isinstance(config, dict):
            for key, value in config.items():
                if isinstance(value, Decimal):
                    config[key] = float(value)
                elif isinstance(value, (list, tuple)):
                    config[key] = [float(v) if isinstance(v, Decimal) else self._floatify_config(v) for v in value]
                elif isinstance(value, dict) or hasattr(value, "__dict__"):
                    self._floatify_config(value)
        return config

    def simulate_executor(self, config: Union[PositionExecutorConfig, DCAExecutorConfig], df: pd.DataFrame,
                          trade_cost: float) -> Optional[ExecutorSimulation]:
        """
        ⚡ MEGA-TURBO: Ensure all inputs are floats to avoid Decimal type collisions.
        """
        config = self._floatify_config(config)
        trade_cost = float(trade_cost)
        
        if isinstance(config, DCAExecutorConfig):
            return self.dca_executor_simulator.simulate(df, config, trade_cost)
        elif isinstance(config, PositionExecutorConfig):
            return self.position_executor_simulator.simulate(df, config, trade_cost)
        return None

    def manage_active_executors(self, simulation: ExecutorSimulation):
        """
        Manages the list of active executors based on the simulation results.

        Args:
            simulation (ExecutorSimulation): The simulation results of the current executor.
            active_executors (list): The list of active executors.
        """
        if not simulation.executor_simulation.empty:
            self.active_executor_simulations.append(simulation)

    def handle_stop_action(self, action: StopExecutorAction, timestamp: float):
        """
        Handles stop actions for executors, terminating them as required.

        Args:
            action (StopExecutorAction): The action indicating which executor to stop.
            active_executors (list): The list of active executors.
            timestamp (pd.Timestamp): The current timestamp.
        """
        for executor in self.active_executor_simulations:
            executor_info = executor.get_executor_info_at_timestamp(timestamp)
            if executor_info.config.id == action.executor_id:
                executor_info.status = RunnableStatus.TERMINATED
                executor_info.close_type = CloseType.EARLY_STOP
                executor_info.is_active = False
                executor_info.close_timestamp = timestamp
                self.stopped_executors_info.append(executor_info)
                self.active_executor_simulations.remove(executor)

    @staticmethod
    def summarize_results(executors_info: List, total_amount_quote: float = 1000):
        if len(executors_info) > 0:
            executors_df = pd.DataFrame([ei.to_dict() for ei in executors_info])
            
            # Check for compounding flag in the controller config
            use_compounding = False
            if not executors_df.empty and "config" in executors_df.columns:
                try:
                    # Look deep into the config dictionary
                    conf_dict = executors_df["config"].iloc[0]
                    if isinstance(conf_dict, dict):
                        use_compounding = conf_dict.get("controller_config", {}).get("use_compounding", False)
                except Exception:
                    pass

            if use_compounding:
                # Geometric PNL calculation
                executors_df["return_pct"] = executors_df["net_pnl_quote"] / executors_df["filled_amount_quote"]
                executors_df["account_multiplier"] = (1 + executors_df["return_pct"]).cumprod()
                final_multiplier = executors_df["account_multiplier"].iloc[-1]
                net_pnl_quote = total_amount_quote * (final_multiplier - 1)
                cumulative_returns = total_amount_quote * (executors_df["account_multiplier"] - 1)
            else:
                net_pnl_quote = executors_df["net_pnl_quote"].sum()
                cumulative_returns = executors_df["net_pnl_quote"].cumsum()
                
            total_executors = executors_df.shape[0]
            executors_with_position = executors_df[executors_df["net_pnl_quote"] != 0]
            total_executors_with_position = executors_with_position.shape[0]
            total_volume = executors_with_position["filled_amount_quote"].sum()
            total_long = (executors_with_position["side"] == TradeType.BUY).sum()
            total_short = (executors_with_position["side"] == TradeType.SELL).sum()
            correct_long = ((executors_with_position["side"] == TradeType.BUY) & (executors_with_position["net_pnl_quote"] > 0)).sum()
            correct_short = ((executors_with_position["side"] == TradeType.SELL) & (executors_with_position["net_pnl_quote"] > 0)).sum()
            accuracy_long = correct_long / total_long if total_long > 0 else 0
            accuracy_short = correct_short / total_short if total_short > 0 else 0
            executors_df["close_type_name"] = executors_df["close_type"].apply(lambda x: x.name)
            close_types = executors_df.groupby("close_type_name")["timestamp"].count().to_dict()
            executors_with_position = executors_df[executors_df["net_pnl_quote"] != 0].copy()
            # Additional metrics
            total_positions = executors_with_position.shape[0]
            win_signals = executors_with_position[executors_with_position["net_pnl_quote"] > 0]
            loss_signals = executors_with_position[executors_with_position["net_pnl_quote"] < 0]
            accuracy = (win_signals.shape[0] / total_positions) if total_positions else 0.0
            
            # Use the calculated cumulative_returns (from compounding or simple sum)
            if use_compounding:
                # Need to filter the already calculated series for positions only
                cumulative_returns = total_amount_quote * (executors_with_position["account_multiplier"] - 1)
            else:
                cumulative_returns = executors_with_position["net_pnl_quote"].cumsum()
                
            executors_with_position["cumulative_returns"] = cumulative_returns
            executors_with_position["cumulative_volume"] = executors_with_position["filled_amount_quote"].cumsum()
            executors_with_position["inventory"] = total_amount_quote + cumulative_returns

            peak = np.maximum.accumulate(cumulative_returns)
            drawdown = (cumulative_returns - peak)
            max_draw_down = np.min(drawdown)
            max_drawdown_pct = max_draw_down / executors_with_position["inventory"].iloc[0]
            returns = pd.to_numeric(
                executors_with_position["cumulative_returns"] / executors_with_position["cumulative_volume"])
            sharpe_ratio = returns.mean() / returns.std() if len(returns) > 1 and returns.std() != 0 else 0
            total_won = win_signals.loc[:, "net_pnl_quote"].sum()
            total_loss = - loss_signals.loc[:, "net_pnl_quote"].sum()
            profit_factor = total_won / total_loss if total_loss > 0 else 1
            net_pnl_pct = net_pnl_quote / total_amount_quote

            return {
                "net_pnl": float(net_pnl_pct),
                "net_pnl_quote": float(net_pnl_quote),
                "total_executors": int(total_executors),
                "total_executors_with_position": int(total_executors_with_position),
                "total_volume": float(total_volume),
                "total_long": int(total_long),
                "total_short": int(total_short),
                "close_types": close_types,
                "accuracy_long": float(accuracy_long),
                "accuracy_short": float(accuracy_short),
                "total_positions": int(total_positions),
                "accuracy": float(accuracy),
                "max_drawdown_usd": float(max_draw_down),
                "max_drawdown_pct": float(max_drawdown_pct),
                "sharpe_ratio": float(sharpe_ratio),
                "profit_factor": float(profit_factor),
                "win_signals": int(win_signals.shape[0]),
                "loss_signals": int(loss_signals.shape[0]),
            }
        return {
            "net_pnl": 0,
            "net_pnl_quote": 0,
            "total_executors": 0,
            "total_executors_with_position": 0,
            "total_volume": 0,
            "total_long": 0,
            "total_short": 0,
            "close_types": 0,
            "accuracy_long": 0,
            "accuracy_short": 0,
            "total_positions": 0,
            "accuracy": 0,
            "max_drawdown_usd": 0,
            "max_drawdown_pct": 0,
            "sharpe_ratio": 0,
            "profit_factor": 0,
            "win_signals": 0,
            "loss_signals": 0,
        }
 