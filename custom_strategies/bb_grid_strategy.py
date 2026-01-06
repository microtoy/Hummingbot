"""
Bollinger Band Grid Strategy

A high-frequency mean reversion strategy that:
1. Uses multiple Bollinger Band levels to enter positions (Grid style)
2. Exits at the BB Mid-line or via Triple Barrier (SL/TP/Time)
3. Ensures high signal frequency to provide robust statistical samples for AWFO.

Signal Logic:
- LONG: Price < BB Mid - (level * BB Std)
- SHORT: Price > BB Mid + (level * BB Std)
"""
from decimal import Decimal
from typing import List, Optional

from pydantic import ConfigDict, Field

from hummingbot.data_feed.candles_feed.data_types import CandlesConfig
from hummingbot.strategy_v2.controllers.directional_trading_controller_base import (
    DirectionalTradingControllerBase,
    DirectionalTradingControllerConfigBase,
)


class BBGridStrategyConfig(DirectionalTradingControllerConfigBase):
    """
    Configuration for Bollinger Band Grid Strategy.
    """
    model_config = ConfigDict(extra='allow', arbitrary_types_allowed=True)
    
    controller_name: str = "bb_grid_strategy"
    
    # BB Parameters
    bb_period: int = Field(default=20, description="Bollinger Bands period")
    bb_std: float = Field(default=2.0, description="Bollinger Bands standard deviation multiplier")
    
    # Grid Parameters
    # Instead of one entry, we can have a wider 'activation zone'
    entry_threshold: float = Field(default=0.9, description="Multiplier for BB Std for entry (e.g. 0.9 = 90% of BB Band)")
    
    # Trend Filter
    use_trend_filter: bool = Field(default=True, description="Use trend MA to filter signals")
    trend_ma_period: int = Field(default=100, description="Trend filter MA period")
    
    # Candle Interval
    indicator_interval: str = Field(default="1h", description="Candles interval")
    
    # Standard directional fields
    connector_name: str = Field(default="binance", description="The connector to use")
    trading_pair: str = Field(default="SOL-USDT", description="The trading pair to trade")
    total_amount_quote: Decimal = Field(default=Decimal("100"), description="Initial amount in quote per trade")
    
    # Risk Management
    stop_loss: Optional[Decimal] = Field(default=Decimal("0.03"), description="Stop loss")
    take_profit: Optional[Decimal] = Field(default=Decimal("0.05"), description="Take profit")
    time_limit: Optional[int] = Field(default=86400, description="Time limit in seconds (24h)")
    
    # We need candles for indicator calculation
    candles_config: List[CandlesConfig] = Field(default=[])

    def update_markets(self, markets):
        markets[self.connector_name] = {self.trading_pair}
        return markets


class BBGridStrategyController(DirectionalTradingControllerBase):
    """
    Bollinger Band Grid Controller.
    """
    def __init__(self, config: BBGridStrategyConfig, *args, **kwargs):
        if not config.candles_config:
            config.candles_config = [
                CandlesConfig(
                    connector=config.connector_name,
                    trading_pair=config.trading_pair,
                    interval=config.indicator_interval,
                    max_records=500
                )
            ]
        
        super().__init__(config, *args, **kwargs)
        self.config = config

    async def update_processed_data(self):
        """
        Calculate Bollinger Bands and optional trend MA.
        Generates signals when:
        - Price enters our 'Grid zone' (between Mid and Outer Band)
        """
        import pandas as pd
        import numpy as np
        
        interval = str(self.config.indicator_interval)
        candles = self.market_data_provider.get_candles_df(self.config.connector_name, self.config.trading_pair, interval)
        
        if candles is None or candles.empty:
            self.processed_data = {"signal": 0, "features": pd.DataFrame()}
            return
        
        candles = candles.copy()
        
        # 1. Bollinger Bands
        candles["bb_mid"] = candles["close"].rolling(window=self.config.bb_period).mean()
        candles["bb_std"] = candles["close"].rolling(window=self.config.bb_period).std()
        
        # Entry zone boundaries
        # Instead of a single line, we trade the 'outer' region
        candles["bb_upper"] = candles["bb_mid"] + (self.config.bb_std * candles["bb_std"])
        candles["bb_lower"] = candles["bb_mid"] - (self.config.bb_std * candles["bb_std"])
        
        # Grid trigger line (e.g. at 90% of the band)
        candles["grid_buy_line"] = candles["bb_mid"] - (self.config.bb_std * self.config.entry_threshold * candles["bb_std"])
        candles["grid_sell_line"] = candles["bb_mid"] + (self.config.bb_std * self.config.entry_threshold * candles["bb_std"])
        
        # 2. Trend Filter
        if self.config.use_trend_filter:
            candles["trend_ma"] = candles["close"].rolling(window=self.config.trend_ma_period).mean()
        else:
            candles["trend_ma"] = 0
        
        # 3. Signals
        candles["signal"] = 0
        
        # Entry Logic:
        # LONG: Price < grid_buy_line AND (if trend: price > trend_ma)
        long_cond = (candles["close"] <= candles["grid_buy_line"])
        if self.config.use_trend_filter:
            long_cond = long_cond & (candles["close"] > candles["trend_ma"])
            
        # SHORT: Price > grid_sell_line AND (if trend: price < trend_ma)
        short_cond = (candles["close"] >= candles["grid_sell_line"])
        if self.config.use_trend_filter:
            short_cond = short_cond & (candles["close"] < candles["trend_ma"])
            
        candles.loc[long_cond, "signal"] = 1
        candles.loc[short_cond, "signal"] = -1
        
        # Anti-look-ahead
        candles["signal"] = candles["signal"].shift(1).fillna(0).astype(int)
        
        self.processed_data = {
            "signal": int(candles["signal"].iloc[-1]),
            "features": candles
        }
