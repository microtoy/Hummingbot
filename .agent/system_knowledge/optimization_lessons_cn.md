# 🚀 Mac Studio 性能优化实战指引 (Performance Sprint)

本文档记录了 2026 年 1 月性能专项优化中，针对 Apple Silicon (M2/M3 Ultra) 架构实现的各项技术突破，旨在彻底榨干 Mac Studio 的多核与统一内存性能。

## 1. 多进程架构：`spawn` 绝对准则

### **核心挑战**：
Hummingbot 大量使用 `asyncio`，在 macOS 下使用默认的进程创建方式容易导致“事件循环冲突”或“进程池损坏 (BrokenProcessPool)”。

### **实战经验**：
- **强制使用 `spawn` 上下文**：在 macOS 上，必须显式使用 `multiprocessing.get_context('spawn')`。相比 `fork`，它更干净但也更严格。
- **顶层 Worker 函数**：Worker 函数必须定义在模块的最顶层，否则无法被序列化（Pickle）。
- **延迟加载 (Lazy Load)**：绝不能在模块顶层创建重型实例（如 `BacktestingEngineBase`）。因为 `spawn` 会重新导入模块，这会导致主从进程冲突。

## 2. “网络戒断” (Network Blackout) 隔离策略

### **核心挑战**：
子进程在后台执行回测时，如果触发了联网同步或心跳协程，会导致回测挂起或报错。

### **实战经验**：
- **深度 Mock (Stub Everything)**：在子进程入口，物理切断并 Mock 掉 `aiohttp`、`TimeSynchronizer` 和 `MarketDataProvider`。
- **AsyncMock 必选**：如果代码中包含对 Mock 方法的 `await`，必须使用 `unittest.mock.AsyncMock`，否则会触发“对象不可被 await”的报错。
- **权限边界**：数据同步接口 (Sync) 明确开启联网权限，但所有回测模拟 (Run) 强制锁定为 `allow_download = False`。

## 3. IO 革命：智能切片 (Smart Slicing)

### **核心挑战**：
回测 1 周数据却要读取半年乃至数 GB 的 CSV，导致 5-10 秒的初始化白屏。

### **实战经验**：
- **两阶段读取法**：
    1. **极速扫描**：仅读取 `timestamp` 列（跳过所有非必要解析）。
    2. **物理定位**：确定目标行号后，利用 `skiprows` 和 `nrows` 直接从磁盘读取目标数据块。
- **成果**：初始化耗时从 **5.1s 骤降至 <0.1s**，实现了“瞬间启动”。

## 4. 逻辑加速：跳跃式引擎 (Jump-Ahead)

### **核心挑战**：
在没有交易的时间段，逐秒/逐分钟遍历 K 线极大浪费了 CPU。

### **实战经验**：
- **事件驱动跳跃**：在没有待成交订单或信号触发的“空闲时段”，引擎直接跳过无效 K 线，只在关键节点执行逻辑。
- **成果**：在保持 100% 精度前提下，计算效率提升了 **50-100 倍**。

## 5. 数据严谨性：T-1 偏移 (Safety Shift)

### **核心挑战**：
“未来函数”隐患。回测中极易在看到 12:00 的收盘价后，在 12:00 瞬间成交，导致虚高收益。

### **实战经验**：
- **强制偏移**：所有信号数据必须向后偏移 1 个周期。12:00 结束的 K 线生成的信号，最早只能在 12:01 执行。
- **准则**：如果回测胜率超过 90%，第一步永远是检查时间戳对齐逻辑。

---
## 6. 极限压榨：48 核服务器满载准则 (CPU Saturation)

### **核心挑战**：
在 48 核高性能服务器上，传统的 IPC (进程间通信) 开销和 I/O 波动会迅速成为瓶颈，导致 CPU 利用率无法突破 500%。

### **实战经验**：
- **In-Worker Batching (批处理闭环)**：放弃“主进程发任务，子进程领任务”的频繁通信。改为将配置分块（Chunks），子进程拿到 Chunk 后在内部进行 `for` 循环批量处理。
- **Library Monkeypatching (库级打桩)**：子进程必须在导入后立即 Patch `hummingbot.data_path`。否则，即便主进程 Patch 了，子进程也会因为 `spawn` 的重新导入特性而读回慢速的磁盘数据。
- **UltraJSON (ujson)**：大规模并发下，标准 `json` 的序列化开销不可忽视。改用 `ujson` 后，响应速度提升了约 30%。

## 7. 稳定性深水区：Loop 绑定与属性寻址

### **核心挑战**：
`spawn` 模式下预先创建的 Engine 实例在进入 `asyncio.run` 时会报“事件循环冲突”，且不同版本的 Hummingbot 对回测 DataFrame 的属性命名不同。

### **实战经验**：
- **Loop 内实例化**：`BacktestingEngineBase()` 的创建必须放在 `async def` 函数的第一行。这确保了 Engine 使用的是子进程当前活动的那个 Event Loop。
- **属性命名适配**：在最新的 V2 引擎中，回测 DataProvider 的 K 线存储属性名为 `candles_feeds` 而非 `candles_data`。如果缓存失效导致回测变慢或报错，首选检查此属性名。

---
**当前状态**：🏆 48 核满载优化达成 (4668%) | tmpfs 内存镜像同步 | In-Worker 批处理闭环 | AI 自动化性能调优完成。
