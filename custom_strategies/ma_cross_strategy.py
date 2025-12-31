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
    
    # We need candles for the MA calculation
    candles_config: List[CandlesConfig] = Field(default=[])

    def update_markets(self, markets):
        markets[self.connector_name] = {self.trading_pair}
        return markets


class MACrossStrategyController(DirectionalTradingControllerBase):
    """
    Minimal implementation of an MA Cross controller for testing.
    """
    def __init__(self, config: MACrossStrategyConfig, *args, **kwargs):
        # Add the required candles config if not present
        if not config.candles_config:
            config.candles_config = [
                CandlesConfig(connector=config.connector_name, trading_pair=config.trading_pair, interval="1m", max_records=200)
            ]
        super().__init__(config, *args, **kwargs)
        self.config = config

    async def update_processed_data(self):
        """
        Calculates MAs and determines signal.
        """
        candles = self.market_data_provider.get_candles_df(self.config.connector_name, self.config.trading_pair, "1m")
        if len(candles) < self.config.slow_ma:
            self.processed_data = {"signal": 0, "features": candles}
            return

        fast_ma = candles["close"].rolling(window=self.config.fast_ma).mean()
        slow_ma = candles["close"].rolling(window=self.config.slow_ma).mean()
        
        last_fast = fast_ma.iloc[-1]
        last_slow = slow_ma.iloc[-1]
        prev_fast = fast_ma.iloc[-2]
        prev_slow = slow_ma.iloc[-2]

        signal = 0
        if prev_fast <= prev_slow and last_fast > last_slow:
            signal = 1
        elif prev_fast >= prev_slow and last_fast < last_slow:
            signal = -1

        self.processed_data = {
            "signal": signal,
            "fast_ma": float(last_fast),
            "slow_ma": float(last_slow),
            "features": candles
        }
