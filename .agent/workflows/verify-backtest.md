---
description: 运行回测验证测试套件 (Backtesting Verification Suite)
---

# 回测验证测试套件

本工作流用于在重构后验证回测引擎的精度和一致性。

## 快速验证 (Quick Mode)

运行 4 个核心测试，约 30 秒完成：

// turbo
```bash
docker exec hummingbot-api /opt/conda/envs/hummingbot-api/bin/python3 /hummingbot-api/bots/controllers/custom/verification_suite.py --quick
```

## 完整验证 (Full Suite)

运行全部 14 个测试，覆盖所有边界条件，约 90 秒完成：

```bash
docker exec hummingbot-api /opt/conda/envs/hummingbot-api/bin/python3 /hummingbot-api/bots/controllers/custom/verification_suite.py
```

## 测试类别说明

| 类别 | 测试数 | 目的 |
| :--- | :---: | :--- |
| **CORE ENGINE** | 3 | 基础功能：结构、覆盖率、数据完整性 |
| **SELF-CONSISTENCY** | 3 | 确定性：相同配置产生相同结果 |
| **CACHE ISOLATION** | 1 | 缓存隔离：不同时间窗口不污染 |
| **STATE-DEPENDENT** | 2 | 状态依赖：Trailing Stop、复利仓位 |
| **HIGH-FREQUENCY** | 2 | 高频压力：大量信号检测 |
| **UPSTREAM PARITY** | 3 | Fork 一致性：与原版 Hummingbot 基准对比 |

## 何时运行

1. **每次重构后** - 确保修改没有破坏核心逻辑
2. **添加新策略后** - 验证新策略与引擎兼容
3. **修改缓存逻辑后** - 验证缓存隔离正确
4. **部署前** - 最终验证

## 通过标准

- ✅ 所有测试通过 = 重构安全
- ❌ 任何测试失败 = 需要检查代码
