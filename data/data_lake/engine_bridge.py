import asyncio
import pandas as pd
import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)

async def fetch_candles_range(
    exchange: str,
    trading_pair: str,
    interval: str,
    start_ts: int,  # Unix timestamp (seconds)
    end_ts: int,    # Unix timestamp (seconds)
    proxy_config: Optional[Dict] = None
) -> Optional[pd.DataFrame]:
    """
    极速抓取特定范围的 K 线数据。
    现在直接调用轻量级 Fetcher，避开 Hummingbot 繁重的引擎依赖和 asyncio 冲突。
    """
    from .fetcher import fetch_candles_direct
    return await fetch_candles_direct(trading_pair, interval, start_ts, end_ts, proxy_config)
