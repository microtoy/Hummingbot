import aiohttp
import pandas as pd
import logging
from typing import Dict, Optional, List
import time

logger = logging.getLogger(__name__)

class BinanceFetcher:
    """
    轻量级 Binance API 抓取器，直接使用 aiohttp 避免复杂的类依赖。
    """
    BASE_URL = "https://api.binance.com/api/v3/klines"
    TICKER_24H_URL = "https://api.binance.com/api/v3/ticker/24hr"
    COINGECKO_URL = "https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&order=market_cap_desc&per_page=200&page=1"

    def _is_tradeable_asset(self, symbol: str) -> bool:
        """
        判断资产是否具有交易价值（过滤稳定币、包装币、质押币）。
        """
        # 清理并转大写
        asset = symbol.upper().strip()
        if "-" in asset: # 比如 USDT-USDT 这种
            asset = asset.split("-")[0]
            
        # 0. 基础噪音过滤 (防止非法代币或非常规 Ticker)
        # 正常 Ticker 很少超过 8 位，且不含下划线、点等
        if len(asset) > 8 or "_" in asset or "." in asset or any(c.isdigit() for c in asset[:2]):
            return False
            
        # 1. 过滤稳定币 (Stablecoins)
        stables = {"USDT", "USDC", "DAI", "FDUSD", "TUSD", "USDS", "BUSD", "PYUSD", "EUR", "GBP", "UST", "TRY"}
        
        # 2. 过滤包装币 (Wrapped Tokens) & 质押衍生币 (Liquid Staking)
        # 逻辑：以 W 开头 (WBTC, WETH) 或 st 开头 (stETH), wst 开头
        non_tradeable_prefixes = ("W", "ST", "WST", "WB", "RENETH", "BB")
        
        # 检查是否在稳定币列表中
        if asset in stables:
            return False
            
        # 检查是否是包装币/质押币
        for pref in non_tradeable_prefixes:
            if asset.startswith(pref) and asset != pref:
                # 排除一些正常的币，比如 SOL, SUI, SEI, STX, SNX 等
                if asset not in {"SOL", "SUI", "SEI", "SHIB", "STX", "SNX", "STORJ", "STRK", "SUSHI", "STEEM", "STG"}:
                    return False
        
        return True

    async def get_market_cap_pairs(self, limit: int = 100) -> List[str]:
        """获取按市值排序的 Top 交易对 (基于全球市值，映射到 Binance)"""
        async with aiohttp.ClientSession() as session:
            proxy = self.proxy_config if isinstance(self.proxy_config, str) else (self.proxy_config.get("http") if self.proxy_config else None)
            try:
                async with session.get(self.COINGECKO_URL, proxy=proxy, timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        top_pairs = []
                        for d in data:
                            symbol = d['symbol'].upper()
                            if self._is_tradeable_asset(symbol):
                                top_pairs.append(f"{symbol}-USDT")
                            if len(top_pairs) >= limit:
                                break
                        return top_pairs
                    else:
                        logger.warning(f"CoinGecko API returned {resp.status}, using curated fallback.")
            except Exception as e:
                logger.error(f"Error fetching market cap pairs: {e}")
        
        # 高质量市值预设列表 (Curated & Filtered)
        curated_v1 = ["BTC-USDT", "ETH-USDT", "SOL-USDT", "BNB-USDT", "XRP-USDT", "ADA-USDT", "DOGE-USDT", "TRX-USDT", "LINK-USDT", "DOT-USDT", 
                      "MATIC-USDT", "SHIB-USDT", "LTC-USDT", "BCH-USDT", "AVAX-USDT", "UNI-USDT", "ATOM-USDT", "ETC-USDT", "ICP-USDT", "FIL-USDT"]
        return curated_v1[:limit]

    async def get_top_trading_pairs(self, limit: int = 100) -> List[str]:
        """获取按 24h 成交额排序的 Top 交易对 (市场热度)"""
        async with aiohttp.ClientSession() as session:
            proxy = self.proxy_config if isinstance(self.proxy_config, str) else (self.proxy_config.get("http") if self.proxy_config else None)
            try:
                async with session.get(self.TICKER_24H_URL, proxy=proxy, timeout=10) as resp:
                    if resp.status != 200:
                        return ["BTC-USDT", "ETH-USDT", "SOL-USDT"]
                    
                    data = await resp.json()
                    # 过滤出 USDT 交易对逻辑
                    top_pairs = []
                    # 先排序
                    data.sort(key=lambda x: float(x['quoteVolume']), reverse=True)
                    
                    for p in data:
                        symbol = p['symbol']
                        if symbol.endswith('USDT'):
                            base = symbol[:-4]
                            if self._is_tradeable_asset(base):
                                top_pairs.append(f"{base}-USDT")
                        
                        if len(top_pairs) >= limit:
                            break
                    return top_pairs
            except Exception as e:
                logger.error(f"Error fetching top volume pairs: {e}")
                return ["BTC-USDT", "ETH-USDT", "SOL-USDT"]

    def __init__(self, proxy_config: Optional[Dict] = None):
        self.proxy_config = proxy_config

    async def fetch_klines(self, symbol: str, interval: str, start_time_ms: int, end_time_ms: int) -> tuple[Optional[pd.DataFrame], Optional[str]]:
        """抓取 K 线数据，并返回 (数据, 错误信息)"""
        params = {
            "symbol": symbol.replace("-", ""),
            "interval": interval,
            "startTime": start_time_ms,
            "endTime": end_time_ms,
            "limit": 1000  # Binance 单次最大 1000
        }
        
        all_data = []
        current_start = start_time_ms
        error_msg = None
        
        async with aiohttp.ClientSession() as session:
            while current_start < end_time_ms:
                params["startTime"] = current_start
                try:
                    proxy = None
                    if self.proxy_config:
                        if isinstance(self.proxy_config, dict):
                            proxy = self.proxy_config.get("http") or self.proxy_config.get("https")
                        else:
                            proxy = self.proxy_config
                        
                    async with session.get(self.BASE_URL, params=params, proxy=proxy, timeout=30) as resp:
                        if resp.status != 200:
                            err_text = await resp.text()
                            error_msg = f"Binance API Error {resp.status}"
                            logger.error(f"{error_msg}: {err_text}")
                            break
                            
                        data = await resp.json()
                        if not data:
                            break
                            
                        all_data.extend(data)
                        last_ts = data[-1][0]
                        if last_ts >= end_time_ms or last_ts == current_start:
                            break
                        current_start = last_ts + 1
                        
                except Exception as e:
                    error_msg = f"Connection Error: {str(e)}"
                    logger.error(f"Fetch Error: {e}")
                    break
                    
        if not all_data:
            # 如果没有数据且没有报错，说明是正常空数据 (时间太早)
            if error_msg is None:
                error_msg = "No data available (market not open or too early)"
            return None, error_msg
            
        # 转换为 DataFrame
        df = pd.DataFrame(all_data, columns=[
            "timestamp", "open", "high", "low", "close", "volume",
            "close_time", "quote_asset_volume", "number_of_trades",
            "taker_buy_base_asset_volume", "taker_buy_quote_asset_volume", "ignore"
        ])
        
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = df[col].astype(float)
        df["timestamp"] = df["timestamp"].astype(int)
        
        return df, None

async def fetch_candles_direct(symbol: str, interval: str, start_ts_s: int, end_ts_s: int, proxy_config: Optional[Dict] = None):
    """便捷入口"""
    fetcher = BinanceFetcher(proxy_config)
    df, error = await fetcher.fetch_klines(symbol, interval, start_ts_s * 1000, end_ts_s * 1000)
    return df
