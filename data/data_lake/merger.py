import os
import pandas as pd
from datetime import date, timedelta, datetime
from typing import List, Optional
from pathlib import Path
import logging

from .storage import LakeStorage

logger = logging.getLogger(__name__)

class DataMerger:
    """
    负责将分片数据 (Partitions) 合并为单一 CSV 文件，以兼容 Hummingbot。
    """
    def __init__(self, storage: LakeStorage):
        self.storage = storage

    def merge_to_legacy(self, exchange: str, trading_pair: str, interval: str, 
                        output_path: str, start_date: date, end_date: date):
        """
        将选定日期范围的数据合并并导出为 Hummingbot 格式。
        """
        all_dfs = []
        current = start_date
        while current <= end_date:
            df = self.storage.load_day_data(exchange, trading_pair, interval, current)
            if df is not None:
                all_dfs.append(df)
            current += timedelta(days=1)
            
        if not all_dfs:
            logger.warning(f"No data found in lake for {trading_pair} between {start_date} and {end_date}")
            return False
            
        # 合并并去重
        merged_df = pd.concat(all_dfs, ignore_index=True)
        merged_df.drop_duplicates(subset=['timestamp'], keep='last', inplace=True)
        merged_df.sort_values(by='timestamp', inplace=True)
        
        # 原子写入
        out_p = Path(output_path)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        
        tmp_p = out_p.with_suffix(".tmp_merge")
        merged_df.to_csv(tmp_p, index=False)
        tmp_p.replace(out_p)
        
        logger.info(f"✅ Merged {len(merged_df)} rows for {trading_pair} into {output_path}")
        return True

    def auto_merge_full_history(self, exchange: str, trading_pair: str, interval: str, output_path: str):
        """
        自动发现该币种在 Lake 中的所有数据并合并。
        """
        existing_days = self.storage.list_existing_days(exchange, trading_pair, interval)
        if not existing_days:
            return False
            
        return self.merge_to_legacy(
            exchange, trading_pair, interval, output_path,
            start_date=existing_days[0],
            end_date=existing_days[-1]
        )
