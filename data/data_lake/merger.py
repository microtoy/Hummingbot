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
            
        # 1. 合并
        merged_df = pd.concat(all_dfs, ignore_index=True)
        
        # 2. 归一化校验 (处理可能存在的旧格式分片)
        if 'timestamp' in merged_df.columns:
            if merged_df['timestamp'].max() > 1e11: # 毫秒检测
                merged_df['timestamp'] = merged_df['timestamp'] / 1000.0
            
            # 兼容性映射：处理旧格式分片中的长列名
            compat_map = {
                "number_of_trades": "n_trades",
                "taker_buy_base_asset_volume": "taker_buy_base_volume",
                "taker_buy_quote_asset_volume": "taker_buy_quote_volume"
            }
            for old_col, new_col in compat_map.items():
                if old_col in merged_df.columns and new_col not in merged_df.columns:
                    merged_df[new_col] = merged_df[old_col]
            
            # 统一列名为 V1 规范
            v1_columns = [
                "timestamp", "open", "high", "low", "close", "volume",
                "quote_asset_volume", "n_trades", "taker_buy_base_volume", "taker_buy_quote_volume"
            ]
            # 仅保留存在的 V1 列并补齐
            existing_v1 = [c for c in v1_columns if c in merged_df.columns]
            merged_df = merged_df[existing_v1]
            for col in v1_columns:
                if col not in merged_df.columns:
                    merged_df[col] = 0.0
            merged_df = merged_df[v1_columns] # 强制顺序

        # 3. 去重与物理排序
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
