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

from hummingbot.client import settings
from hummingbot.core.data_type.common import LazyDict, TradeType
from hummingbot.data_feed.candles_feed.data_types import CandlesConfig
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
        self.allow_download = True  # Milestone Fix: Enable download by default to support 1h indicators

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
        await self.backtesting_data_provider.initialize_trading_rules(controller_config.connector_name)
        self.controller = controller_class(config=controller_config, market_data_provider=self.backtesting_data_provider,
                                           actions_queue=None)
        self.backtesting_resolution = backtesting_resolution
        await self.initialize_backtesting_data_provider()
        performance_report["data_initialization"] = time.perf_counter() - start_init
        
        # Phase 2: Processed Data (Strategy Logic)
        start_processed = time.perf_counter()
        await self.controller.update_processed_data()
        performance_report["processed_data_calc"] = time.perf_counter() - start_processed
        
        # Phase 3: Execution Simulation
        start_sim = time.perf_counter()
        executors_info = await self.simulate_execution(trade_cost=trade_cost)
        performance_report["execution_simulation"] = time.perf_counter() - start_sim
        
        # Phase 4: Summarize
        results = self.summarize_results(executors_info, controller_config.total_amount_quote)
        
        performance_report["total_time"] = time.perf_counter() - start_time_total
        performance_report["kline_count"] = len(self.controller.processed_data.get("features", []))
        
        return {
            "executors": executors_info,
            "results": results,
            "processed_data": self.controller.processed_data,
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

    async def _get_candles_with_cache(self, config: CandlesConfig):
        """Helper to get candles with greedy delta-filling and time-capping."""
        import hummingbot
        import time
        from hummingbot.data_feed.candles_feed.candles_base import CandlesBase
        from hummingbot.data_feed.candles_feed.data_types import HistoricalCandlesConfig

        cache_dir = Path(hummingbot.data_path()) / "candles"
        cache_dir.mkdir(parents=True, exist_ok=True)
        
        filename = f"{config.connector}_{config.trading_pair}_{config.interval}.csv"
        cache_file = cache_dir / filename
        
        # Determine current time to cap end_time (avoiding future requests)
        current_ts = int(time.time())
        # Determine strict needed range including strategy buffer
        candles_buffer = config.max_records * CandlesBase.interval_to_seconds[config.interval]
        needed_start = int(self.backtesting_data_provider.start_time - candles_buffer)
        needed_end = int(self.backtesting_data_provider.end_time)
        
        # CAP: We cannot fetch data that hasn't happened yet.
        # We use a 60s buffer from "now" to ensure the latest candle is likely closed.
        effective_needed_end = min(needed_end, current_ts - 60)
        
        # CACHE CHECK
        if not cache_file.exists():
            if not self.allow_download:
                raise ValueError(f"❌ [NO CACHE] {filename} does not exist. Please sync data first via 'Download Candles' page.")
            print(f"📥 [MISSING CACHE] {filename} not found. Attempting download...")
        elif self.allow_download:
            # Check for range sufficiency even if file exists
            try:
                # Fast check for first and last line
                with open(cache_file, 'rb') as f:
                    header = f.readline()
                    first_line = f.readline()
                    f.seek(-1024, 2)
                    last_lines = f.read().splitlines()
                    
                    if first_line and last_lines:
                        m_ts = int(float(first_line.split(b',')[0]))
                        x_ts = int(float(last_lines[-1].split(b',')[0]))
                        
                        if m_ts <= needed_start and x_ts >= effective_needed_end - 86400:
                             # PERFECT CACHE HIT
                             full_df = pd.read_csv(cache_file)
                             result_df = full_df[(full_df["timestamp"] >= needed_start) & (full_df["timestamp"] <= needed_end)].copy()
                             key = self.backtesting_data_provider._generate_candle_feed_key(config)
                             self.backtesting_data_provider.candles_feeds[key] = result_df
                             return result_df
            except:
                pass # Fallback to standard download/sync logic below

        # DOWNLOAD / SYNC FALLBACK
        if self.allow_download:
            try:
                print(f"📥 [SYNCING] Fetching {config.trading_pair} ({config.interval}) for backtest...")
                merged_df = await self.backtesting_data_provider.get_candles_feed(config)
                if merged_df is not None and not merged_df.empty:
                    merged_df.to_csv(cache_file, index=False)
                    result_df = merged_df[(merged_df["timestamp"] >= needed_start) & (merged_df["timestamp"] <= needed_end)].copy()
                    key = self.backtesting_data_provider._generate_candle_feed_key(config)
                    self.backtesting_data_provider.candles_feeds[key] = result_df
                    return result_df
            except Exception as e:
                if "NO_LOG" not in str(e):
                    print(f"❌ [DOWNLOAD FAILED] {filename}: {e}")
        
        # Final failure if no download allowed or failed
        if not cache_file.exists():
            raise ValueError(f"❌ [DOWNLOAD REQUIRED] {filename} is missing and could not be fetched.")
        
        try:
            full_df = pd.read_csv(cache_file)
        except Exception as e:
            raise ValueError(f"❌ [CACHE READ ERROR] Failed to read {filename}: {e}")
        
        if full_df.empty:
            raise ValueError(f"❌ [EMPTY CACHE] {filename} is empty. Please sync data first.")
        
        min_ts = int(full_df["timestamp"].min())
        max_ts = int(full_df["timestamp"].max())
        
        # Check if cache covers the needed range (with 24h tolerance for end)
        if min_ts > needed_start:
            raise ValueError(f"❌ [CACHE INSUFFICIENT] {filename} starts at {min_ts}, but need {needed_start}. Please sync more historical data.")
        
        if max_ts < effective_needed_end - 86400:  # 24h tolerance
            raise ValueError(f"❌ [CACHE OUTDATED] {filename} ends at {max_ts}, but need {effective_needed_end}. Please sync to update data.")
        
        print(f"✅ [CACHE HIT] Using local {filename} ({len(full_df):,} rows, {min_ts}-{max_ts})")
        result_df = full_df[(full_df["timestamp"] >= needed_start) & (full_df["timestamp"] <= needed_end)].copy()
        key = self.backtesting_data_provider._generate_candle_feed_key(config)
        self.backtesting_data_provider.candles_feeds[key] = result_df
        return result_df

    async def simulate_execution(self, trade_cost: float) -> list:
        """
        Simulates market making strategy over historical data using Event-Driven Jump-Ahead.
        """
        processed_features = self.prepare_market_data()
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
            row = processed_features.loc[current_ts]
            self.controller.market_data_provider.prices = {connector_key: row.close_bt_decimal}
            self.controller.market_data_provider._time = current_ts
            self.controller.processed_data.update(row.to_dict())
            
            # Update executors and collect finished ones
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
            for action in self.controller.determine_executor_actions():
                if isinstance(action, CreateExecutorAction):
                    current_idx = ts_to_idx[current_ts]
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
        
        backtesting_candles["close_bt_decimal"] = backtesting_candles["close"].apply(Decimal)
        backtesting_candles.dropna(inplace=True)
        self.controller.processed_data["features"] = backtesting_candles
        return backtesting_candles

    def simulate_executor(self, config: Union[PositionExecutorConfig, DCAExecutorConfig], df: pd.DataFrame,
                          trade_cost: float) -> Optional[ExecutorSimulation]:
        """
        Simulates the execution of a trading strategy given a configuration.

        Args:
            config (PositionExecutorConfig): The configuration of the executor.
            df (pd.DataFrame): DataFrame containing the market data from the start time.
            trade_cost (float): The cost per trade.

        Returns:
            ExecutorSimulation: The results of the simulation.
        """
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
 