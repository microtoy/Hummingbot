from decimal import Decimal
from typing import List, Optional

from pydantic import Field

from hummingbot.data_feed.candles_feed.data_types import CandlesConfig
from hummingbot.strategy_v2.controllers.market_making_controller_base import (
    MarketMakingControllerBase,
    MarketMakingControllerConfigBase,
)
from hummingbot.strategy_v2.executors.position_executor.data_types import PositionExecutorConfig


class ExamplePMMStrategyConfig(MarketMakingControllerConfigBase):
    """
    Configuration for ExamplePMMStrategy.
    Uses standard V2 fields for full compatibility with backtesting.
    """
    controller_name: str = "example_pmm_strategy"
    
    # Standard V2 Fields with explicit defaults to avoid NoneType errors
    buy_spreads: List[float] = Field(default=[0.001], description="Spread for buy orders")
    sell_spreads: List[float] = Field(default=[0.001], description="Spread for sell orders")
    buy_amounts_pct: List[Decimal] = Field(default=[Decimal("1")], description="Weight for buy orders")
    sell_amounts_pct: List[Decimal] = Field(default=[Decimal("1")], description="Weight for sell orders")
    
    # Custom parameter (can still be added)
    order_refresh_time: int = Field(default=30, description="Time in seconds to refresh orders")


class ExamplePMMStrategyController(MarketMakingControllerBase):
    """
    A simple Pure Market Making controller.
    """
    def __init__(self, config: ExamplePMMStrategyConfig, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
        self.config = config

    def get_executor_config(self, level_id: str, price: Decimal, amount: Decimal):
        """
        Creates position executor config using standard triple barrier settings.
        """
        trade_type = self.get_trade_type_from_level_id(level_id)
        return PositionExecutorConfig(
            timestamp=self.market_data_provider.time(),
            level_id=level_id,
            connector_name=self.config.connector_name,
            trading_pair=self.config.trading_pair,
            entry_price=price,
            amount=amount,
            triple_barrier_config=self.config.triple_barrier_config,
            leverage=self.config.leverage,
            side=trade_type,
        )
