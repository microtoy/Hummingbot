---
description: 如何创建并回测一个新的自定义 V2 策略
---

# 自定义策略开发工作流

此工作流用于指导如何快速创建一个可在 Smart Strategy 页面回测并部署的新策略。

## 第一步：创建控制器文件
在 `custom_strategies/` 目录下创建一个新文件（如 `my_new_strategy.py`）。

## 第二步：编写代码 (参考模板)
必须包含两个类：`Config` 类和 `Controller` 类。

### 示例结构：
```python
from hummingbot.strategy_v2.controllers.market_making_controller_base import MarketMakingControllerBase, MarketMakingControllerConfigBase

class MyNewStrategyConfig(MarketMakingControllerConfigBase):
    controller_name: str = "my_new_strategy"
    # 定义你的参数，务必给初值
    my_param: float = 0.5 

class MyNewStrategyController(MarketMakingControllerBase):
    def __init__(self, config: MyNewStrategyConfig, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
```

## 第三步：同步至服务器
// turbo
1. 运行本地 Git 同步脚本：
```bash
cd /Users/microtoy/Documents/Projects/hummingbot-deploy/
./git_workflow.sh
```

2. 在云端服务器拉取更新：
```bash
ssh ubuntu@140.245.32.255 -i /Users/microtoy/Documents/VPS/Oracle/Key/oci_ed25519 "cd hummingbot-deploy && git pull"
```

## 第四步：回测验证
1. 访问 Dashboard -> Smart Strategy。
2. 选中 `My New Strategy`。
3. 调整参数并点击 **Run Backtesting**。

## 第五步：保存与部署
1. 点击 **Upload**。
2. 前往 **Bot Orchestration -> Deploy V2** 启动。
