from decimal import Decimal
from typing import List, Optional

from pydantic import ConfigDict, Field

from hummingbot.data_feed.candles_feed.data_types import CandlesConfig
from hummingbot.strategy_v2.controllers.directional_trading_controller_base import (
    DirectionalTradingControllerBase,
    DirectionalTradingControllerConfigBase,
)
from hummingbot.strategy_v2.executors.position_executor.data_types import PositionExecutorConfig


class MACrossStrategyConfig(DirectionalTradingControllerConfigBase):
    """
    Configuration for a Simple Moving Average Cross Strategy with Risk Management and Compounding.
    """
    model_config = ConfigDict(extra='allow', arbitrary_types_allowed=True)
    
    controller_name: str = "ma_cross_strategy"
    
    # Strategy specific parameters
    fast_ma: int = Field(default=5, description="Fast MA period")
    slow_ma: int = Field(default=10, description="Slow MA period")
    
    # Standard directional fields
    connector_name: str = Field(default="binance", description="The connector to use")
    trading_pair: str = Field(default="XRP-USDT", description="The trading pair to trade")
    total_amount_quote: Decimal = Field(default=Decimal("100"), description="Initial amount in quote per trade")
    
    # Risk Management (Triple Barrier)
    stop_loss: Optional[Decimal] = Field(default=Decimal("0.02"), description="Stop loss threshold (e.g., 0.02 for 2%)")
    take_profit: Optional[Decimal] = Field(default=Decimal("0.05"), description="Take profit threshold (e.g., 0.05 for 5%)")
    time_limit: Optional[int] = Field(default=21600, description="Time limit in seconds (e.g., 21600 for 6h)")
    
    # [NEW] Compounding
    use_compounding: bool = Field(default=False, description="Reinvest profits by scaling trade size based on PNL")
    
    # We need candles for the MA calculation
    candles_config: List[CandlesConfig] = Field(default=[])

    def update_markets(self, markets):
        markets[self.connector_name] = {self.trading_pair}
        return markets


class MACrossStrategyController(DirectionalTradingControllerBase):
    """
    Implementation of an MA Cross controller with Triple Barrier and optional Compounding.
    """
    def __init__(self, config: MACrossStrategyConfig, *args, **kwargs):
        if not config.candles_config:
            config.candles_config = [
                CandlesConfig(connector=config.connector_name, trading_pair=config.trading_pair, interval="1h", max_records=500)
            ]
        
        super().__init__(config, *args, **kwargs)
        self.config = config

    async def update_processed_data(self):
        """
        Calculates MAs and determines signal for the whole dataset.
        IMPORTANT: Shifts timestamps to avoid Look-Ahead Bias.
        """
        from hummingbot.data_feed.candles_feed.candles_base import CandlesBase
        
        interval = self.config.candles_config[0].interval if self.config.candles_config else "1h"
        candles = self.market_data_provider.get_candles_df(self.config.connector_name, self.config.trading_pair, interval).copy()
        
        if len(candles) < self.config.slow_ma:
            candles["signal"] = 0
            self.processed_data = {"signal": 0, "features": candles}
            return

        # --------------------------------------------------------------------------
        # [CRITICAL AUDIT FIX] Timestamp Safety Shift
        # Original: Timestamp 13:00 means "Open Time".
        # Problem: Backtesting at 13:01 would merge with 13:00 and see the 13:59 close price.
        # Fix: Shift timestamp by interval so 13:00 Open Time becomes 14:00 Available Time.
        # --------------------------------------------------------------------------
        shift_seconds = CandlesBase.interval_to_seconds[interval]
        candles["timestamp"] = candles["timestamp"] + shift_seconds

        candles["fast_ma"] = candles["close"].rolling(window=self.config.fast_ma).mean()
        candles["slow_ma"] = candles["close"].rolling(window=self.config.slow_ma).mean()
        
        candles["signal"] = 0
        candles.loc[(candles["fast_ma"] > candles["slow_ma"]) & (candles["fast_ma"].shift(1) <= candles["slow_ma"].shift(1)), "signal"] = 1
        candles.loc[(candles["fast_ma"] < candles["slow_ma"]) & (candles["fast_ma"].shift(1) >= candles["slow_ma"].shift(1)), "signal"] = -1

        self.processed_data = {
            "signal": int(candles["signal"].iloc[-1]),
            "features": candles
        }

    def get_executor_config(self, trade_type, price, amount):
        """
        Override to implement compounding logic if enabled.
        """
        actual_amount = amount
        
        if self.config.use_compounding:
            # Calculate total PNL from closed executors to determine current 'virtual balance'
            total_net_pnl_quote = sum([e.net_pnl_quote for e in self.executors_info if not e.is_active], Decimal("0"))
            profit_factor = (self.config.total_amount_quote + total_net_pnl_quote) / self.config.total_amount_quote
            if profit_factor > 0:
                actual_amount = amount * profit_factor

        return super().get_executor_config(trade_type, price, actual_amount)
