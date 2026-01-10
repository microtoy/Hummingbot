import pandas as pd
from pathlib import Path
from datetime import datetime, date, timedelta
from concurrent.futures import ThreadPoolExecutor
from typing import List, Optional
import hummingbot

class LakeLoader:
    """
    Data Lake V2 高性能加载器
    直接从分片存储中读取数据，支持并行 IO 以最大化吞吐量。
    """
    def __init__(self, base_path: Optional[str] = None):
        if base_path:
            self.base_path = Path(base_path)
        else:
            # ⚡ Turbo 模式补丁: Turbo Worker 会将 data_path 设为 /tmp/hbot_data
            # 但 Lake 数据通常很大，不会被镜像到 tmp，所以我们需要检测并回退。
            hbot_path = Path(hummingbot.data_path())
            lake_path = hbot_path / "lake"
            
            if not lake_path.exists():
                # 尝试从库安装路径寻找原始数据
                try:
                    original_base = Path(hummingbot.prefix_path()) / "data" / "lake"
                    if original_base.exists():
                        lake_path = original_base
                except:
                    pass
            
            self.base_path = lake_path

    def _get_path(self, exchange: str, pair: str, interval: str, day: date) -> Path:
        """根据存储规则构建路径"""
        path = self.base_path / exchange / pair / interval / str(day.year) / f"{day.month:02d}" / f"{day.isoformat()}.csv"
        return path

    def get_data(self, exchange: str, pair: str, interval: str, start_ts: int, end_ts: int, workers: int = 8) -> pd.DataFrame:
        """
        核心方法：并行读取指定范围的数据
        """
        start_date = datetime.fromtimestamp(start_ts).date()
        end_date = datetime.fromtimestamp(end_ts).date()
        
        # 1. 生成日期序列
        target_days = []
        curr = start_date
        while curr <= end_date:
            target_days.append(curr)
            curr += timedelta(days=1)
            
        # 2. 筛选存在的文件
        tasks = []
        is_synthesized = False
        
        for i, day in enumerate(target_days):
            path = self._get_path(exchange, pair, interval, day)
            exists = path.exists()
            if i < 3:
                print(f"DEBUG: Checking {path} -> {exists}")
            if exists:
                tasks.append(path)
                
        # 🌟 SPECIAL: 如果没找到 4h 数据，且有 1h 数据，则自动进行降采样合成
        if not tasks and interval == "4h":
            print(f"⚠️ [LOADER SYNTHESIS] No 4h shards found for {exchange}:{pair}. Trying to synthesize from 1h.")
            for day in target_days:
                path = self._get_path(exchange, pair, "1h", day)
                if path.exists():
                    tasks.append(path)
            if tasks:
                is_synthesized = True

        if not tasks:
            print(f"❌ [LOADER DEBUG] No tasks generated for {exchange}:{pair}:{interval}. Total days checked: {len(target_days)}. First path: {self._get_path(exchange, pair, interval, target_days[0]) if target_days else 'N/A'}")
            return pd.DataFrame()
            
        # 3. 并行读取
        def read_one(path):
            try:
                df = pd.read_csv(path)
                # 🔥 FIX: Normalize timestamp immediately (early data uses milliseconds)
                if 'timestamp' in df.columns and df['timestamp'].max() > 1e11:
                    df['timestamp'] = df['timestamp'] / 1000.0
                return df
            except Exception as e:
                print(f"⚠️ [LOADER ERROR] Failed to read {path}: {e}")
                return None

        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(read_one, tasks))
            
        # 4. 合并并过滤精确范围
        dfs = [df for df in results if df is not None and not df.empty]
        if not dfs:
            return pd.DataFrame()
            
        full_df = pd.concat(dfs, ignore_index=True)
        
        # 5. 排序与去重
        full_df = full_df.sort_values("timestamp").drop_duplicates(subset=["timestamp"])

        # 6. 计算降采样 (如果需要)
        if is_synthesized and interval == "4h":
            full_df = self._resample_1h_to_4h(full_df)

        # 7. 过滤精确范围
        mask = (full_df["timestamp"] >= start_ts) & (full_df["timestamp"] <= end_ts)
        full_df = full_df[mask]
        
        return full_df.reset_index(drop=True)

    def _resample_1h_to_4h(self, df: pd.DataFrame) -> pd.DataFrame:
        """从 1h K线降采样合成 4h K线"""
        if df.empty:
            return df
        
        # 转换为 datetime 以便 resample
        df = df.copy()
        df['datetime'] = pd.to_datetime(df['timestamp'], unit='s')
        df = df.set_index('datetime')
        
        # 标准 OHLCV 聚合
        resampled = df.resample('4h', label='left', closed='left').agg({
            'timestamp': 'first',
            'open': 'first',
            'high': 'max',
            'low': 'min',
            'close': 'last',
            'volume': 'sum'
        }).dropna()
        
        # 确保 timestamp 依然是整数
        resampled['timestamp'] = resampled['timestamp'].astype(int)
        
        return resampled.reset_index(drop=True)
