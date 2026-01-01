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
  - *Persistence*: The patched file is stored in `scripts/patches/` and mounted back into the container via `docker-compose.yml`.
- **Pandas SettingWithCopyWarning**: Modifying candle slices in `update_processed_data` caused warnings and log noise.
  - *Fix*: Use `.copy()` on dataframes returned by `market_data_provider`.

## Performance Note
For MA Cross and Directional strategies, ensure `update_processed_data` calculates a full time-series `signal` column in the `features` dataframe for the backtesting engine to process history.
