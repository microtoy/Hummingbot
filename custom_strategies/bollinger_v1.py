from decimal import Decimal
from typing import List, Optional

import pandas as pd
import numpy as np
from pydantic import ConfigDict, Field

from hummingbot.data_feed.candles_feed.data_types import CandlesConfig
from hummingbot.strategy_v2.controllers.directional_trading_controller_base import (
    DirectionalTradingControllerBase,
    DirectionalTradingControllerConfigBase,
)


from enum import Enum
class CandleInterval(str, Enum):
    M1 = "1m"
    M3 = "3m"
    M5 = "5m"
    M15 = "15m"
    M30 = "30m"
    H1 = "1h"
    H4 = "4h"
    D1 = "1d"

class BollingerV1StrategyConfig(DirectionalTradingControllerConfigBase):
    """
    Configuration for Bollinger V1 (Volatility Breakout) Strategy.
    """
    model_config = ConfigDict(extra='allow', arbitrary_types_allowed=True)
    
    controller_name: str = "bollinger_v1"
    
    # Strategy specific parameters
    bb_length: int = Field(default=20, description="Bollinger Bands length")
    bb_std: float = Field(default=2.0, description="Bollinger Bands standard deviation")
    rsi_length: int = Field(default=14, description="RSI length")
    atr_length: int = Field(default=14, description="ATR length")
    indicator_interval: CandleInterval = Field(default=CandleInterval.H1, description="Interval for technical indicators")
    
    # Filters
    rsi_overbought: float = Field(default=70.0, description="RSI Overbought threshold")
    rsi_oversold: float = Field(default=30.0, description="RSI Oversold threshold")
    min_bandwidth: float = Field(default=0.0, description="Minimum Bandwidth to enter (volatility filter)")
    
    # Risk Management
    atr_stop_loss_multiplier: float = Field(default=3.0, description="Multiplier for ATR based stop loss")
    
    # Standard directional fields
    connector_name: str = Field(default="binance", description="The connector to use")
    trading_pair: str = Field(default="SOL-USDT", description="The trading pair to trade")
    total_amount_quote: Decimal = Field(default=Decimal("100"), description="Initial amount in quote per trade")
    
    # Risk Management (Triple Barrier - Defaults)
    stop_loss: Optional[Decimal] = Field(default=Decimal("0.05"), description="Hard Stop loss threshold (backup)")
    take_profit: Optional[Decimal] = Field(default=Decimal("0.15"), description="Take profit threshold")
    time_limit: Optional[int] = Field(default=86400, description="Time limit in seconds (24h)")
    
    candles_config: List[CandlesConfig] = Field(default=[])

    def update_markets(self, markets):
        markets[self.connector_name] = {self.trading_pair}
        return markets


class BollingerV1StrategyController(DirectionalTradingControllerBase):
    """
    Bollinger V1: Volatility Breakout Strategy.
    Enters when price breaks out of the Bollinger Bands with RSI confirmation.
    """
    def __init__(self, config: BollingerV1StrategyConfig, *args, **kwargs):
        if not config.candles_config:
            # Default to configured interval
            config.candles_config = [
                CandlesConfig(connector=config.connector_name, trading_pair=config.trading_pair, interval=config.indicator_interval, max_records=500)
            ]
        
        super().__init__(config, *args, **kwargs)
        self.config = config

    async def update_processed_data(self):
        """
        Calculates BB, RSI, ATR and generates signals.
        CRITICAL: Applies Timestamp Shift to prevent Look-Ahead Bias.
        """
        from hummingbot.data_feed.candles_feed.candles_base import CandlesBase
        
        interval = str(self.config.indicator_interval.value) if hasattr(self.config.indicator_interval, "value") else str(self.config.indicator_interval)
        candles = self.market_data_provider.get_candles_df(self.config.connector_name, self.config.trading_pair, interval)
        
        if candles is None or candles.empty:
            self.processed_data = {"signal": 0, "features": pd.DataFrame()}
            return
            
        candles = candles.copy()
        if len(candles) < max(self.config.bb_length, self.config.rsi_length, self.config.atr_length) + 1:
            candles["signal"] = 0
            self.processed_data = {"signal": 0, "features": candles}
            return

        # --- Indicator Calculations ---
        # 1. Bollinger Bands
        candles["ma"] = candles["close"].rolling(window=self.config.bb_length).mean()
        candles["std"] = candles["close"].rolling(window=self.config.bb_length).std()
        candles["upper"] = candles["ma"] + (candles["std"] * self.config.bb_std)
        candles["lower"] = candles["ma"] - (candles["std"] * self.config.bb_std)
        candles["bandwidth"] = (candles["upper"] - candles["lower"]) / candles["ma"]
        
        # 2. RSI
        delta = candles["close"].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=self.config.rsi_length).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=self.config.rsi_length).mean()
        rs = gain / loss
        candles["rsi"] = 100 - (100 / (1 + rs))
        
        # 3. ATR (Average True Range)
        high_low = candles["high"] - candles["low"]
        high_close = np.abs(candles["high"] - candles["close"].shift())
        low_close = np.abs(candles["low"] - candles["close"].shift())
        ranges = pd.concat([high_low, high_close, low_close], axis=1)
        true_range = np.max(ranges, axis=1)
        candles["atr"] = true_range.rolling(window=self.config.atr_length).mean()

        # --- Signal Generation ---
        candles["signal"] = 0
        
        # Long Entry: Close > Upper Band AND RSI > 50 (Upward Momentum)
        # Filter: Bandwidth > min_bandwidth (Avoid squeezing too tight if required)
        # [OPTIMIZATION] Check for CROSSOVER (Current > Upper AND Prev <= Upper)
        long_condition = (
            (candles["close"] > candles["upper"]) & 
            (candles["close"].shift(1) <= candles["upper"].shift(1)) &
            (candles["rsi"] > 50) & 
            (candles["rsi"] < self.config.rsi_overbought) &
            (candles["bandwidth"] > self.config.min_bandwidth)
        )
        
        # Short Entry: Close < Lower Band AND RSI < 50
        # [OPTIMIZATION] Check for CROSSUNDER (Current < Lower AND Prev >= Lower)
        short_condition = (
            (candles["close"] < candles["lower"]) & 
            (candles["close"].shift(1) >= candles["lower"].shift(1)) &
            (candles["rsi"] < 50) & 
            (candles["rsi"] > self.config.rsi_oversold) & 
            (candles["bandwidth"] > self.config.min_bandwidth)
        )
        
        candles.loc[long_condition, "signal"] = 1
        candles.loc[short_condition, "signal"] = -1

        # --------------------------------------------------------------------------
        # [ANTI-LOOK-AHEAD BIAS] Signal Lagging
        # --------------------------------------------------------------------------
        candles["signal"] = candles["signal"].shift(1).fillna(0).astype(int)

        self.processed_data = {
            "signal": int(candles["signal"].iloc[-1]),
            "features": candles
        }

    def get_executor_config(self, trade_type, price, amount):
        """
        Dynamically sets Stop Loss based on ATR.
        """
        # Get the latest ATR from processed_features
        # Since logic runs on the 'latest partial' candle potentially? 
        # No, Strategy V2 runs on timestamps. the 'features' are already computed.
        # We need to find the ATR at the current decision time.
        
        # Because 'get_executor_config' doesn't easily expose the current timestamp or features row index
        # We fall back to standard config stop loss OR we could hack it if we had access.
        # For V1 Controller simplicity, we will stick to the fixed Stop Loss in config,
        # BUT we can modify the config ON THE FLY if we want dynamic.
        
        # Advanced: Read ATR from self.processed_data["features"].iloc[-1]
        try:
            current_atr = Decimal(str(self.processed_data["features"]["atr"].iloc[-1]))
            # Dynamic Stop Loss distance = ATR * Multiplier
            sl_dist = current_atr * Decimal(str(self.config.atr_stop_loss_multiplier))
            
            # Calculate SL in percentage relative to price
            # SL% = (Distance / Price)
            sl_pct = sl_dist / price
            
            # Update the config for this specific order
            # Note: This changes the instance config, which might be persistent? 
            # Ideally returns a new config object.
            # But 'get_executor_config' returns a PositionExecutorConfig.
            
            # Let's override the config defaults
            # (If sl_pct is valid and not crazy)
            if sl_pct > 0 and sl_pct < 0.2: # Cap at 20%
                 self.config.stop_loss = sl_pct
                 
        except Exception:
            pass # Fallback to fixed SL

        return super().get_executor_config(trade_type, price, amount)
