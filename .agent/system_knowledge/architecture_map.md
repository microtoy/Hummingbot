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
- **NoneType Summation Error**: If `buy_amounts_pct` or `sell_amounts_pct` are `None`, the backend `sum()` will fail.
  - *Fix*: Always initialize these lists in the `Config` class with defaults like `[Decimal("1")]`.
- **Empty Backtest Error**: If 0 trades occur, `close_types` returns an `int` 0.
  - *Fix*: The Dashboard app normalizes this to `{}` before rendering metrics.

## Performance Note
For MA Cross and Directional strategies, ensure `update_processed_data` calculates a full time-series `signal` column in the `features` dataframe for the backtesting engine to process history.
