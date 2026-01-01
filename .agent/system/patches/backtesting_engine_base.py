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

    __controller_class_cache = LazyDict[str, Type[ControllerBase]]()

    def __init__(self):
        self.controller = None
        self.backtesting_resolution = None
        self.backtesting_data_provider = BacktestingDataProvider(connectors={})
        self.position_executor_simulator = PositionExecutorSimulator()
        self.dca_executor_simulator = DCAExecutorSimulator()
        self.allow_download = False  # Default: Cache-Only Mode

    # ... [Skipped methods remain unchanged] ...

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
        
        full_df = pd.DataFrame()
        if cache_file.exists():
            try:
                full_df = pd.read_csv(cache_file)
                if not full_df.empty:
                    min_ts = int(full_df["timestamp"].min())
                    max_ts = int(full_df["timestamp"].max())
                    
                    # 1. PERFECT HIT CHECK (with generous 24-hour tolerance for the end)
                    if min_ts <= needed_start and max_ts >= effective_needed_end - 86400:
                        print(f"✅ [CACHE HIT] Using local {filename} ({len(full_df):,} rows, {min_ts}-{max_ts})")
                        result_df = full_df[(full_df["timestamp"] >= needed_start) & (full_df["timestamp"] <= needed_end)].copy()
                        key = self.backtesting_data_provider._generate_candle_feed_key(config)
                        self.backtesting_data_provider.candles_feeds[key] = result_df
                        return result_df
                    else:
                        print(f"🔄 [CACHE PARTIAL] Local ({min_ts}-{max_ts}) vs Needed ({needed_start}-{effective_needed_end})")
            except Exception as e:
                print(f"⚠️ [CACHE LOAD ERROR] {e}")

        # CACHE-ONLY MODE CHECK
        if not self.allow_download:
            if full_df.empty:
                 raise ValueError(f"❌ [NO CACHE] {filename} missing/empty. Please sync data first.")
            min_ts = int(full_df["timestamp"].min())
            max_ts = int(full_df["timestamp"].max())
            if min_ts > needed_start:
                raise ValueError(f"❌ [CACHE INSUFFICIENT] Start {min_ts} > {needed_start}. Please sync more data.")
            if max_ts < effective_needed_end - 86400:
                raise ValueError(f"❌ [CACHE OUTDATED] End {max_ts} < {effective_needed_end}. Please sync more data.")
            # If we are here, it's a partial hit that wasn't perfect but allows us to proceed? 
            # Actually, perfect hit check above already returned. So here means insufficient cache.
            # But just in case, let's allow "close enough" hits in strict mode if they cover the CORE range.
            # For now, strict mode means strict.
            raise ValueError(f"❌ [CACHE MISMATCH] Cache exists but coverage insufficient for requested range.")

        # 2. GREEDY DELTA FILLING (Only if allow_download=True)
        download_needed = True
        merged_df = full_df
        
        if not merged_df.empty:
            min_ts = int(merged_df["timestamp"].min())
            max_ts = int(merged_df["timestamp"].max())
            
            # Case A: Missing start (Buffer)
            if min_ts > needed_start:
                print(f"📥 [DELTA START] Fetching missing prefix: {needed_start} to {min_ts}")
                candle_feed = hummingbot.data_feed.candles_feed.candles_factory.CandlesFactory.get_candle(config)
                prefix_df = await candle_feed.get_historical_candles(config=HistoricalCandlesConfig(
                    connector_name=config.connector, trading_pair=config.trading_pair,
                    interval=config.interval, start_time=needed_start, end_time=min_ts
                ))
                if prefix_df is not None and not prefix_df.empty:
                    merged_df = pd.concat([merged_df, prefix_df]).drop_duplicates(subset=["timestamp"]).sort_values("timestamp")
            
            # Case B: Missing end (but not beyond "now")
            if max_ts < effective_needed_end - 300:
                print(f"📥 [DELTA END] Fetching missing suffix: {max_ts} to {effective_needed_end}")
                candle_feed = hummingbot.data_feed.candles_feed.candles_factory.CandlesFactory.get_candle(config)
                suffix_df = await candle_feed.get_historical_candles(config=HistoricalCandlesConfig(
                    connector_name=config.connector, trading_pair=config.trading_pair,
                    interval=config.interval, start_time=max_ts, end_time=effective_needed_end
                ))
                if suffix_df is not None and not suffix_df.empty:
                    merged_df = pd.concat([merged_df, suffix_df]).drop_duplicates(subset=["timestamp"]).sort_values("timestamp")
            
            download_needed = False

        if download_needed:
            print(f"📥 [FULL DOWNLOAD] Fetching range for {config.trading_pair} up to {effective_needed_end}")
            merged_df = await self.backtesting_data_provider.get_candles_feed(config)

        if merged_df is not None and not merged_df.empty:
            try:
                merged_df.to_csv(cache_file, index=False)
                print(f"💾 [CACHE SAVED] {filename} is now {len(merged_df)} rows")
            except Exception as e:
                print(f"❌ [SAVE FAILED] {e}")
            
            result_df = merged_df[(merged_df["timestamp"] >= needed_start) & (merged_df["timestamp"] <= needed_end)].copy()
            key = self.backtesting_data_provider._generate_candle_feed_key(config)
            self.backtesting_data_provider.candles_feeds[key] = result_df
            return result_df
        
        return merged_df


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
 