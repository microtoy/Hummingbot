"""
示例做市策略 - Pure Market Making (PMM)
在买卖两侧挂单,赚取价差，测试更改
"""
from decimal import Decimal
from hummingbot.strategy.script_strategy_base import ScriptStrategyBase

class ExamplePMMStrategy(ScriptStrategyBase):
    """
    简单的做市策略
    功能:
    - 在中间价上下挂买卖单
    - 自动调整订单价格
    - 赚取买卖价差
    """
    
    # 配置交易对
    markets = {
        "binance_paper_trade": {"BTC-USDT"}
    }
    
    # 策略参数
    bid_spread = Decimal("0.001")      # 买单价差 0.1%
    ask_spread = Decimal("0.001")      # 卖单价差 0.1%
    order_amount = Decimal("0.001")    # 订单数量 (BTC)
    order_refresh_time = 30            # 订单刷新时间(秒)
    
    def __init__(self):
        super().__init__()
        self.last_timestamp = 0
    
    def on_tick(self):
        """
        每个 tick 执行一次
        """
        # 获取当前时间戳
        current_timestamp = self.current_timestamp
        
        # 检查是否需要刷新订单
        if current_timestamp - self.last_timestamp < self.order_refresh_time:
            return
        
        self.last_timestamp = current_timestamp
        
        # 获取当前中间价
        mid_price = self.connectors["binance_paper_trade"].get_mid_price("BTC-USDT")
        
        if mid_price is None:
            self.logger().warning("无法获取中间价")
            return
        
        # 计算买卖价格
        bid_price = mid_price * (1 - self.bid_spread)
        ask_price = mid_price * (1 + self.ask_spread)
        
        # 取消所有现有订单
        self.cancel_all_orders()
        
        # 下买单
        self.buy(
            connector_name="binance_paper_trade",
            trading_pair="BTC-USDT",
            amount=self.order_amount,
            order_type="limit",
            price=bid_price
        )
        
        # 下卖单
        self.sell(
            connector_name="binance_paper_trade",
            trading_pair="BTC-USDT",
            amount=self.order_amount,
            order_type="limit",
            price=ask_price
        )
        
        # 打印日志
        self.logger().info(
            f"订单已更新 | "
            f"中间价: {mid_price:.2f} | "
            f"买单: {bid_price:.2f} | "
            f"卖单: {ask_price:.2f}"
        )
