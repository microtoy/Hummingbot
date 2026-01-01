# Hummingbot Dashboard V2 Controller Bridge Architecture

This document describes the custom bridge implemented to enable real backtesting for custom scripts on the Hummingbot Dashboard.

## Core Problem
Standard Hummingbot "Scripts" do not support the native V2 Backtesting engine directly via the API. They require conversion into "Controllers".

## Our Solution: The "Custom Bridge"
We established a mechanism where files in the `custom_strategies/` directory are treated as standard V2 Controllers by both the Dashboard (for UI) and the Hummingbot-API (for execution).

### 1. File Structure & Package Recognition
- **Directory**: `/custom_strategies/` (Workspace)
- **Requirement**: `__init__.py` must exist in this directory to make it a valid Python package.
- **Backend Path**: Mapped to `/hummingbot-api/bots/controllers/custom/` via Docker volumes.

### 2. Deployment Architecture (Docker)
| Service | Mount Path | Purpose |
| :--- | :--- | :--- |
| **Dashboard** | `./custom_strategies:/home/dashboard/custom_strategies` | Dynamic UI Parsing (AST) & Config Upload |
| **Hummingbot-API** | `./custom_strategies:/hummingbot-api/bots/controllers/custom` | Strategy Execution & Backtesting Engine |

### 3. Logic Bridge (`pages/config/smart_strategy/app.py`)
- **AST Parser**: Extracts Pydantic `Config` classes from `.py` files to build the UI form.
- **Controller Type**: Must be set to `custom` when sending requests to the API.
- **Backtesting Engine**: The API loads the controller via `bots.controllers.custom.<filename>`.

### 4. Known Data Normalization Fixes
- **Backtesting Engine JSON Crash**: When Sharpe ratio is `inf` (std=0), `json.dumps` fails with `ValueError: Out of range float values...`.
  - *Fix*: Patched `hummingbot/strategy_v2/backtesting/backtesting_engine_base.py` at line 286 to check `returns.std() != 0`.
  - *Persistence*: The patched file is stored in `.agent/system/patches/` and mounted back into the container via `docker-compose.yml`.
- **Pandas SettingWithCopyWarning**: Modifying candle slices in `update_processed_data` caused warnings and log noise.
  - *Fix*: Use `.copy()` on dataframes returned by `market_data_provider`.
- **Pydantic "Extra inputs are not permitted"**: Subclass fields (like `use_compounding`) were rejected due to parent class `extra='forbid'`.
  - *Fix*: Explicitly set `model_config = ConfigDict(extra='allow')` in the custom strategy Config class.

## Performance Note
## 5. 回测性能优化经验汇总 (2026-01-01)

### A. 核心瓶颈与教训
*   **网络初始化陷阱**：原生环境每次回测都会重新从交易所抓取数据，耗时 20s+。
    *   *方案*：必须实现本地磁盘缓存，读取时间可降至 **<0.1s**。
*   **历史 Buffer 陷阱**：策略（如 MA Cross）计算指标时需要额外的历史数据（如 500 条）。如果同步范围没包含这部分，会导致缓存失效。
    *   *方案*：同步时必须自动附加 **2000 条 K 线左右的余量**。

### B. "Greedy Delta" 增量架构
*   **痛点**：微小的误差（如少了一分钟数据）曾导致整个缓存弃用重下。
*   **方案**：实现“哪里不会补哪里”。系统识别缺失的时间段（前缀/后缀），仅下载增量并与本地 CSV 合并。
*   **今天的数据对齐 (Today Cap)**：请求“今天”时，自动封顶至 `当前时间 - 1分钟`，并允许 **24 小时内的容差**，彻底消除未来数据的 API 超时问题。

### C. 调试方法
*   **容器内诊断**：使用 `cache_diagnostic.py` 在容器内直连数据文件，通过打印 `min_ts/max_ts` 确诊未命中原因。
*   **透明可视化**：控制台必须打印 `✅ [CACHE HIT]` 或 `📥 [DELTA START]` 等日志，建立可观测性。
