import os
import pandas as pd
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import List, Optional, Union, Dict

class LakeStorage:
    """
    分片存储管理器 (Partitioned Storage Manager)
    负责将行情数据按天存储在独立的文件中，防止并发冲突。
    目录结构: base_path/{exchange}/{trading_pair}/{interval}/{year}/{month}/{date}.csv
    """
    def __init__(self, base_path: str = "data/lake"):
        # ⚡ 自动探测路径：优先使用挂载的持久化卷
        import hummingbot
        default_base = Path(hummingbot.data_path()) / "lake"
        self.base_path = default_base
        self.base_path.mkdir(parents=True, exist_ok=True)

    def get_partition_path(self, exchange: str, trading_pair: str, interval: str, day: Union[date, str]) -> Path:
        """获取特定日期的分片文件路径"""
        if isinstance(day, str):
            day = datetime.strptime(day, "%Y-%m-%d").date()
            
        year = str(day.year)
        month = f"{day.month:02d}"
        filename = f"{day.isoformat()}.csv"
        
        path = self.base_path / exchange / trading_pair / interval / year / month / filename
        return path

    def save_day_data(self, df: pd.DataFrame, exchange: str, trading_pair: str, interval: str, day: date):
        """原子性保存一整天的数据"""
        path = self.get_partition_path(exchange, trading_pair, interval, day)
        path.parent.mkdir(parents=True, exist_ok=True)
        
        # 使用临时文件写入再重命名，确保原子性
        tmp_path = path.with_suffix(".tmp")
        df.to_csv(tmp_path, index=False)
        tmp_path.replace(path)

    def load_day_data(self, exchange: str, trading_pair: str, interval: str, day: date) -> Optional[pd.DataFrame]:
        """加载特定日期的数据"""
        path = self.get_partition_path(exchange, trading_pair, interval, day)
        if not path.exists():
            return None
        return pd.read_csv(path)

    def list_existing_days(self, exchange: str, trading_pair: str, interval: str) -> List[date]:
        """列出已存在的所有天 (优化版：避免 glob)"""
        dir_path = self.base_path / exchange / trading_pair / interval
        if not dir_path.exists():
            return []
            
        existing_days = []
        # 按照 YYYY/MM 结构遍历
        for year_dir in dir_path.iterdir():
            if not year_dir.is_dir(): continue
            for month_dir in year_dir.iterdir():
                if not month_dir.is_dir(): continue
                for file in month_dir.iterdir():
                    if file.suffix == ".csv":
                        try:
                            day_str = file.stem
                            day = datetime.strptime(day_str, "%Y-%m-%d").date()
                            existing_days.append(day)
                        except: continue
        return sorted(existing_days)

    def get_missing_days(self, exchange: str, trading_pair: str, interval: str) -> List[date]:
        """获取已有范围内的所有缺失日期"""
        days = self.list_existing_days(exchange, trading_pair, interval)
        if not days:
            return []
        
        start, end = days[0], days[-1]
        all_days = set()
        curr = start
        while curr <= end:
            all_days.add(curr)
            curr += timedelta(days=1)
            
        existing = set(days)
        missing = sorted(list(all_days - existing))
        return missing

    def get_coverage(self, exchange: str, trading_pair: str, interval: str) -> List[int]:
        """
        获取数据的覆盖图谱 (Health Ribbon) - 优化版
        """
        days = self.list_existing_days(exchange, trading_pair, interval)
        if not days:
            return [0] * 100
            
        start, end = days[0], days[-1]
        total_dist = (end - start).days + 1
        
        if total_dist <= 1:
            return [1] * 100
            
        points = [0] * 100
        day_set = set(days)
        
        for i in range(100):
            target_day_count = int(i * (total_dist - 1) / 99)
            target_day = start + timedelta(days=target_day_count)
            if target_day in day_set:
                points[i] = 1
        
        return points

    def get_summary(self, fast: bool = True, audit: bool = False) -> Dict:
        """
        获取存储层统计信息。
        fast=True 时跳过行数统计，仅统计文件数和大小。
        audit=True 时执行深度审计，检查文件的物理行数。
        """
        stats = {
            "total_files": 0,
            "total_size_mb": 0.0,
            "pairs": {}
        }
        
        if not self.base_path.exists():
            return stats
            
        # 优化遍历逻辑
        for file in self.base_path.rglob("*.csv"):
            stats["total_files"] += 1
            try:
                f_stat = file.stat()
                file_size_mb = f_stat.st_size / (1024 * 1024)
                stats["total_size_mb"] += file_size_mb
                
                parts = file.relative_to(self.base_path).parts
                if len(parts) >= 3:
                    exch, pair, interval = parts[0], parts[1], parts[2]
                    key = f"{exch}:{pair}:{interval}"
                    if key not in stats["pairs"]:
                        stats["pairs"][key] = {
                            "count": 0, 
                            "start": None, "end": None, 
                            "total_rows": 0,
                            "missing_days": 0,
                            "incomplete_days": 0,  # 新增：检测行数不足的天数
                            "gap_segments": 0,
                            "days_list": set(),
                            "incomplete_list": []  # 新增：记录具体的异常日期
                        }
                    
                    p_stats = stats["pairs"][key]
                    p_stats["count"] += 1
                    
                    # 定义预期行数
                    expected_rows = 1440 if interval == "1m" else 24 if interval == "1h" else 0
                    
                    actual_rows = 0
                    if not fast or audit:
                        try:
                            # 深度审计：物理读取行数
                            actual_rows = len(pd.read_csv(file, usecols=[0]))
                            p_stats["total_rows"] += actual_rows
                            
                            # 质量校验：如果行数不达标，标记为异常
                            if expected_rows > 0 and actual_rows < expected_rows:
                                p_stats["incomplete_days"] += 1
                                day_str = file.stem
                                p_stats["incomplete_list"].append(day_str)
                        except: pass
                    else:
                        # 快速模式下使用文件大小估算 (针对 1m, 1h)
                        # 如果文件太小 (例如 < 10KB 每分钟数据)，可能也是异常，但这里暂不强制
                        if interval == "1m": p_stats["total_rows"] += 1440
                        elif interval == "1h": p_stats["total_rows"] += 24
                        else: p_stats["total_rows"] += 100
                        
                    # 解析日期
                    day_str = file.stem
                    try:
                        day = datetime.strptime(day_str, "%Y-%m-%d").date()
                        p_stats["days_list"].add(day)
                        if p_stats["start"] is None or day < p_stats["start"]: p_stats["start"] = day
                        if p_stats["end"] is None or day > p_stats["end"]: p_stats["end"] = day
                    except: continue
            except: continue
        
        # 计算缺口统计
        for key, p_stats in stats["pairs"].items():
            if p_stats["start"] and p_stats["end"]:
                total_days = (p_stats["end"] - p_stats["start"]).days + 1
                p_stats["missing_days"] = max(0, total_days - p_stats["count"])
                
                sorted_days = sorted(list(p_stats["days_list"]))
                gap_segments = 0
                if sorted_days:
                    for i in range(len(sorted_days) - 1):
                        if (sorted_days[i+1] - sorted_days[i]).days > 1:
                            gap_segments += 1
                p_stats["gap_segments"] = gap_segments
            
            if "days_list" in p_stats:
                del p_stats["days_list"]
                
        stats["total_size_mb"] = round(stats["total_size_mb"], 2)
        return stats
