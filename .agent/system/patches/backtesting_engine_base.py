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
        """Helper to get candles with simplified 'File-First' caching."""
        import hummingbot
        from hummingbot.data_feed.candles_feed.candles_base import CandlesBase

        cache_dir = Path(hummingbot.data_path()) / "candles"
        cache_dir.mkdir(parents=True, exist_ok=True)
        
        filename = f"{config.connector}_{config.trading_pair}_{config.interval}.csv"
        cache_file = cache_dir / filename
        
        # Determine strict needed range including strategy buffer
        candles_buffer = config.max_records * CandlesBase.interval_to_seconds[config.interval]
        needed_start = int(self.backtesting_data_provider.start_time - candles_buffer)
        needed_end = int(self.backtesting_data_provider.end_time)
        
        if cache_file.exists():
            try:
                df = pd.read_csv(cache_file)
                if not df.empty:
                    min_ts = df["timestamp"].min()
                    max_ts = df["timestamp"].max()
                    
                    # Check if local file FULLY covers the requested period
                    if min_ts <= needed_start and max_ts >= needed_end:
                        print(f"🚀 [CACHE HIT] Using local data for {config.trading_pair} ({len(df)} rows)")
                        result_df = df[(df["timestamp"] >= needed_start) & (df["timestamp"] <= needed_end)].copy()
                        key = self.backtesting_data_provider._generate_candle_feed_key(config)
                        self.backtesting_data_provider.candles_feeds[key] = result_df
                        return result_df
                    else:
                        print(f"🔄 [CACHE MISS] Local file range ({min_ts}-{max_ts}) does not cover target ({needed_start}-{needed_end})")
            except Exception as e:
                print(f"⚠️ [CACHE ERROR] {e}")

        # If we reach here, download and OVERWRITE
        print(f"📥 [DOWNLOADING] Fetching fresh data for {config.trading_pair}...")
        new_df = await self.backtesting_data_provider.get_candles_feed(config)
        
        if new_df is not None and not new_df.empty:
            try:
                # Simple overwrite - no complex merging
                new_df.to_csv(cache_file, index=False)
                print(f"💾 [SAVED] {filename} updated with {len(new_df)} rows")
            except Exception as e:
                print(f"❌ [SAVE FAILED] {e}")
            
            # Return sliced result
            result_df = new_df[(new_df["timestamp"] >= needed_start) & (new_df["timestamp"] <= needed_end)].copy()
            key = self.backtesting_data_provider._generate_candle_feed_key(config)
            self.backtesting_data_provider.candles_feeds[key] = result_df
            return result_df
        
        return new_df

    async def simulate_execution(self, trade_cost: float) -> list:
        """
        Simulates market making strategy over historical data, considering trading costs.

        Args:
            trade_cost (float): The cost per trade.

        Returns:
            List[ExecutorInfo]: List of executor information objects detailing the simulation results.
        """
        processed_features = self.prepare_market_data()
        self.active_executor_simulations: List[ExecutorSimulation] = []
        self.stopped_executors_info: List[ExecutorInfo] = []
        
        # Optimization: iterrows() is very slow. Use to_dict('records') for faster iteration.
        records = processed_features.to_dict('records')
        for i, row in enumerate(records):
            await self.update_state(row)
            for action in self.controller.determine_executor_actions():
                if isinstance(action, CreateExecutorAction):
                    # For optimization, we slice the records instead of the dataframe
                    # Note: simulate_executor expects a dataframe slice, but we can pass records if we update it
                    # However, for minimum change, we'll keep the dataframe slice for now but optimized iteration
                    executor_simulation = self.simulate_executor(action.executor_config, processed_features.iloc[i:], trade_cost)
                    if executor_simulation is not None and executor_simulation.close_type != CloseType.FAILED:
                        self.manage_active_executors(executor_simulation)
                elif isinstance(action, StopExecutorAction):
                    self.handle_stop_action(action, row["timestamp"])

        return self.controller.executors_info

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
        Prepares market data by merging candle data with strategy features, filling missing values.

        Returns:
            pd.DataFrame: The prepared market data with necessary features.
        """
        backtesting_candles = self.controller.market_data_provider.get_candles_df(
            connector_name=self.controller.config.connector_name,
            trading_pair=self.controller.config.trading_pair,
            interval=self.backtesting_resolution
        ).add_suffix("_bt")

        if "features" not in self.controller.processed_data:
            backtesting_candles["reference_price"] = backtesting_candles["close_bt"]
            backtesting_candles["spread_multiplier"] = 1
            backtesting_candles["signal"] = 0
        else:
            backtesting_candles = pd.merge_asof(backtesting_candles, self.controller.processed_data["features"],
                                                left_on="timestamp_bt", right_on="timestamp",
                                                direction="backward")

        backtesting_candles["timestamp"] = backtesting_candles["timestamp_bt"]
        # Set timestamp as index to allow index slicing for performance
        backtesting_candles = BacktestingDataProvider.ensure_epoch_index(backtesting_candles)
        backtesting_candles["open"] = backtesting_candles["open_bt"]
        backtesting_candles["high"] = backtesting_candles["high_bt"]
        backtesting_candles["low"] = backtesting_candles["low_bt"]
        backtesting_candles["close"] = backtesting_candles["close_bt"]
        backtesting_candles["volume"] = backtesting_candles["volume_bt"]
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
 