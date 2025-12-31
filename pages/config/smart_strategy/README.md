# Smart Strategy - 智能策略管理

自动扫描和配置自定义交易策略。

## 功能

1. **策略扫描** - 自动扫描 `custom_strategies/` 目录中的 Python 策略文件
2. **参数解析** - 使用 AST 解析策略类的参数定义
3. **动态 UI** - 根据参数类型自动生成配置界面
4. **版本管理** - 支持保存和加载不同版本的配置
5. **一键部署** - 直接部署机器人实例

## 使用方法

1. 将策略文件放入 `custom_strategies/` 目录
2. 在此页面选择策略
3. 配置参数
4. 保存配置版本
5. 部署机器人

## 策略文件格式

```python
from hummingbot.strategy.script_strategy_base import ScriptStrategyBase
from decimal import Decimal

class MyStrategy(ScriptStrategyBase):
    # 交易市场配置
    markets = {"binance_paper_trade": {"BTC-USDT"}}
    
    # 策略参数 (会自动生成配置界面)
    spread = Decimal("0.001")
    order_amount = Decimal("0.01")
    refresh_time = 30
    
    def on_tick(self):
        # 策略逻辑
        pass
```

## 参数类型支持

- `int` - 整数输入框
- `float` / `Decimal` - 浮点数输入框
- `bool` - 复选框
- `str` - 文本输入框
- `dict` - 文本框 (JSON 格式)
