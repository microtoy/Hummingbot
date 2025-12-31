from decimal import Decimal
from typing import List, Optional

from pydantic import Field

from hummingbot.data_feed.candles_feed.data_types import CandlesConfig
from hummingbot.strategy_v2.controllers.directional_trading_controller_base import (
    DirectionalTradingControllerBase,
    DirectionalTradingControllerConfigBase,
)
from hummingbot.strategy_v2.executors.position_executor.data_types import PositionExecutorConfig


class MACrossStrategyConfig(DirectionalTradingControllerConfigBase):
    """
    Configuration for a Simple Moving Average Cross Strategy.
    """
    controller_name: str = "ma_cross_strategy"
    
    # Strategy specific parameters
    fast_ma: int = Field(default=20, description="Fast MA period")
    slow_ma: int = Field(default=50, description="Slow MA period")
    
    # Standard directional fields
    connector_name: str = Field(default="binance", description="The connector to use")
    trading_pair: str = Field(default="BTC-USDT", description="The trading pair to trade")
    total_amount_quote: Decimal = Field(default=Decimal("100"), description="Total amount in quote")
    
    # Standard V2 fields
    buy_spreads: List[float] = Field(default=[0.001])
    sell_spreads: List[float] = Field(default=[0.001])
    
    # We need candles for the MA calculation
    candles_config: List[CandlesConfig] = Field(default=[])

    def update_markets(self, markets):
        markets[self.connector_name] = {self.trading_pair}
        return markets


class MACrossStrategyController(DirectionalTradingControllerBase):
    """
    Implementation of an MA Cross controller that calculates signals for backtesting.
    """
    def __init__(self, config: MACrossStrategyConfig, *args, **kwargs):
        # Add the required candles config if not present
        if not config.candles_config:
            config.candles_config = [
                CandlesConfig(connector=config.connector_name, trading_pair=config.trading_pair, interval="1h", max_records=500)
            ]
        super().__init__(config, *args, **kwargs)
        self.config = config

    async def update_processed_data(self):
        """
        Calculates MAs and determines signal for the whole dataset for backtesting.
        """
        # Use the first candles config interval
        interval = self.config.candles_config[0].interval if self.config.candles_config else "1h"
        candles = self.market_data_provider.get_candles_df(self.config.connector_name, self.config.trading_pair, interval)
        
        if len(candles) < self.config.slow_ma:
            candles["signal"] = 0
            self.processed_data = {"signal": 0, "features": candles}
            return

        # Time-series calculation for backtesting engine to pick up signals at each timestamp
        candles["fast_ma"] = candles["close"].rolling(window=self.config.fast_ma).mean()
        candles["slow_ma"] = candles["close"].rolling(window=self.config.slow_ma).mean()
        
        # Determine signals across the entire dataframe
        candles["signal"] = 0
        # Buy signal: Fast MA crosses above Slow MA
        candles.loc[(candles["fast_ma"] > candles["slow_ma"]) & (candles["fast_ma"].shift(1) <= candles["slow_ma"].shift(1)), "signal"] = 1
        # Sell signal: Fast MA crosses below Slow MA
        candles.loc[(candles["fast_ma"] < candles["slow_ma"]) & (candles["fast_ma"].shift(1) >= candles["slow_ma"].shift(1)), "signal"] = -1

        # Store results. The 'features' dataframe is what the backtesting engine merges into the main simulation.
        self.processed_data = {
            "signal": int(candles["signal"].iloc[-1]),
            "features": candles
        }
