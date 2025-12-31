# 自定义策略目录

这个目录用于存放你的自定义 Hummingbot 交易策略。

## 文件说明

- `example_pmm_strategy.py` - 示例做市策略
- `__init__.py` - Python 包初始化文件

## 创建新策略

1. 在此目录创建新的 `.py` 文件
2. 继承 `ScriptStrategyBase` 类
3. 实现 `on_tick()` 方法
4. 配置 `markets` 字典

## 示例代码结构

```python
from hummingbot.strategy.script_strategy_base import ScriptStrategyBase

class MyStrategy(ScriptStrategyBase):
    markets = {"binance_paper_trade": {"BTC-USDT"}}
    
    def on_tick(self):
        # 你的策略逻辑
        pass
```

## 部署流程

1. 编写策略代码
2. Git commit: `git add . && git commit -m "feat: add my strategy"`
3. Git push: `git push origin main`
4. 等待云端自动同步 (最多 5 分钟)
5. 在 Dashboard 部署

## 参考资源

- [Hummingbot 策略文档](https://hummingbot.org/strategies/)
- [Script 策略开发](https://hummingbot.org/scripts/)
