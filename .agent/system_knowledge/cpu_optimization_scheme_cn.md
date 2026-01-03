# Hummingbot 高性能 CPU 饱和优化方案

## 🎯 目标
在 48 核服务器上进行 Hummingbot 策略挖掘时，实现 >90% 的 CPU 利用率，消除 I/O 和 IPC 瓶颈。

## 🏆 最终成果
*   **CPU 利用率**: **~4600%** (约占 48 核计算能力的 **95%**)
*   **吞吐量**: **~2.6 次模拟/秒** (每秒处理约 135 万根 K 线数据)
*   **稳定性**: 在 **200 模拟/批次** (Batch Size) 和 **24 并发进程** (Workers) 下长期稳定运行。

## 🛠️ 核心优化技术实施

### 1. ⚡ 快速本地镜像 (Fast Local Mirroring)
**问题**: Windows/WSL 文件挂载读取大量 K 线小文件时，I/O 延迟极高，导致 CPU 等待。
**解决方案**:
在 `backtesting_router.py` 启动时，利用 `shutil` 和 `rsync` 逻辑，自动将挂载卷中的 K 线数据 (`./data/candles`) 镜像复制到容器内的高速本地路径 (`/tmp/hbot_data`)。
**代码实现**: Monkeypatch 了 `hummingbot.data_path` 指向临时目录。

### 2. 🚀 全局进程池与一次性初始化 (Global Worker Pool & Single Init)
**问题**: 标准实现中，每个任务或每批任务都会重新 Fork 进程并导入 Hummingbot 庞大的 Python 库，导致巨大的上下文切换和启动开销。
**解决方案**:
*   **全局池**: 维护一个持久化的 48 进程 `ProcessPoolExecutor`。
*   **Worker Init**: 使用 `initializer` 参数，确保每个 Worker 进程生命周期内只执行一次繁重的 `import` 和 Mock 操作。

### 3. 📦 智能分块与极速序列化 (Smart Chunking & UltraJSON)
**问题**: 主进程分发 1000+ 任务时，Python 的 `pickle`/JSON 序列化成为瓶颈，且大量小请求导致网络拥塞。
**解决方案**:
*   **智能分块**: 客户端将 200 个配置打包为一个请求 (Chunk)。
*   **服务端处理**: 服务端接收 Chunk 后，在 Worker 内部进行循环回测，最后统一返回结果。
*   **UltraJSON**: 替换标准 `json` 库，提升序列化速度。

### 4. 🛑 网络静默 (Networking Blackout)
**问题**: 回测引擎中残留的 DNS 查询和时间同步请求会产生不确定的网络延迟。
**解决方案**:
在 `sys.modules` 层面强力 Mock 了 `aiohttp`, `time_synchronizer` 等组件，确保回测纯本地化。

## 📊 最佳性能配置参考

在 `StrategyOptimizer.py` 中推荐使用以下“稳定高性能”配置：

```python
# [STABLE-PERFORMANCE] 
# 平衡了 CPU 饱和度与单次请求负载，避免超时
self.batch_size = 200   # 每个请求包含 200 个策略参数组合
self.workers = 24       # 并发 24 个请求 (总计 4800 个并发模拟任务)
```

## 📝 验证数据 (Docker Stats)

API 容器能够持续吃满计算资源：

```text
NAME                  CPU %      MEM USAGE / LIMIT
hummingbot-api        4572.20%   16.27GiB / 31.27GiB
hummingbot-broker     104.90%    306.4MiB / 31.27GiB
```
