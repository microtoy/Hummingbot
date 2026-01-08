import asyncio
import logging
import pandas as pd
from datetime import date, datetime, timedelta
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field

from .storage import LakeStorage
from .fetcher import BinanceFetcher

logger = logging.getLogger(__name__)

@dataclass
class LakeDownloadTask:
    trading_pair: str
    interval: str
    day: date
    exchange: str = "binance"
    status: str = "pending"  # pending, downloading, completed, failed
    rows_downloaded: int = 0
    error: Optional[str] = None
    proxy_id: Optional[str] = None
    start_time: float = field(default_factory=datetime.now().timestamp)

class DailyDownloader:
    """
    负责执行单个原子任务：下载特定币种、特定周期、特定日期的数据。
    """
    def __init__(self, storage: LakeStorage):
        self.storage = storage
        self._fetcher_cache: Dict[str, BinanceFetcher] = {} # 缓存不同代理配置的 fetcher

    def _get_fetcher(self, proxy_config: Optional[Any]) -> BinanceFetcher:
        proxy_key = str(proxy_config)
        if proxy_key not in self._fetcher_cache:
            self._fetcher_cache[proxy_key] = BinanceFetcher(proxy_config)
        return self._fetcher_cache[proxy_key]

    async def download_day(self, task: LakeDownloadTask, proxy_config: Optional[Dict] = None):
        """执行单日下载"""
        task.status = "downloading"
        task.start_time = datetime.now().timestamp()
        
        try:
            # 计算这一天的起止时间戳 (ms)
            start_dt = datetime.combine(task.day, datetime.min.time())
            end_dt = start_dt + timedelta(days=1)
            
            start_ts = int(start_dt.timestamp())
            end_ts = int(end_dt.timestamp())
            
            # 抓取数据
            fetcher = self._get_fetcher(proxy_config)
            df, error = await fetcher.fetch_klines(
                symbol=task.trading_pair,
                interval=task.interval,
                start_time_ms=start_ts * 1000,
                end_time_ms=end_ts * 1000
            )
            
            if df is not None and not df.empty:
                # 过滤可能超出的数据 (Binance API 可能会返回范围外的数据)
                df = df[(df['timestamp'] >= start_ts * 1000) & (df['timestamp'] < end_ts * 1000)]
                
                # 保存到 Lake
                self.storage.save_day_data(df, task.exchange, task.trading_pair, task.interval, task.day)
                
                task.rows_downloaded = len(df)
                task.status = "completed"
                logger.info(f"✅ Success: {task.trading_pair} {task.interval} {task.day} ({len(df)} rows)")
            else:
                task.status = "failed"
                task.error = error or "No data returned from API"
                logger.warning(f"⚠️ {task.error}: {task.trading_pair} {task.interval} {task.day}")
                
        except Exception as e:
            task.status = "failed"
            task.error = str(e)
            logger.error(f"❌ Error downloading {task.trading_pair} {task.day}: {e}")

class LakeTaskScheduler:
    """
    任务调度器：负责根据用户的选择生成任务列表，并管理并发。
    """
    def __init__(self, storage: LakeStorage, max_workers: int = 5):
        self.storage = storage
        self.downloader = DailyDownloader(storage)
        self.max_workers = max_workers
        self.tasks: List[LakeDownloadTask] = []
        self._slots: List[Optional[str]] = [None] * max_workers
        self._running = False
        self._stop_signal = False
        self._pause_event = asyncio.Event()
        self._pause_event.set() # 默认不暂停
        self._loop: Optional[asyncio.AbstractEventLoop] = None # 记录运行时的 loop

    def cancel_tasks(self):
        """全面清空下载任务 (终止)"""
        self._stop_signal = True
        self.tasks = []
        self._pause_event.set() # 确保不被卡死在暂停状态
        self._running = False
        logger.info("🛑 Download terminated: task list cleared.")

    def pause_tasks(self):
        """暂停下载 (线程安全)"""
        if self._loop:
            self._loop.call_soon_threadsafe(self._pause_event.clear)
        else:
            self._pause_event.clear()
        logger.info("⏸️ Download paused.")

    def resume_tasks(self):
        """恢复下载 (线程安全)"""
        if self._loop:
            self._loop.call_soon_threadsafe(self._pause_event.set)
        else:
            self._pause_event.set()
        logger.info("▶️ Download resumed.")

    def add_tasks(self, pairs: List[str], intervals: List[str], start_date: date, end_date: date):
        """生成任务列表 (优化版：单交易对单次扫描)"""
        # 启动新批次时重置停止和暂停信号
        self._stop_signal = False
        self._pause_event.set()
        
        if not self._running:
            self.tasks = []
            
        logger.info(f"Adding tasks for {pairs} {intervals} from {start_date} to {end_date}")
        
        for pair in pairs:
            for interval in intervals:
                # 获取该币种在该周期已存在的天 (现在每个 pair-interval 只调用一次扫描)
                existing = set(self.storage.list_existing_days("binance", pair, interval))
                
                current = start_date
                while current <= end_date:
                    if self._stop_signal: return # 允许在生成任务时中断
                    if current not in existing:
                        # 检查任务列表中是否已存在 (避免重复添加)
                        is_duplicate = any(t.trading_pair == pair and t.interval == interval and t.day == current for t in self.tasks)
                        if not is_duplicate:
                            self.tasks.append(LakeDownloadTask(trading_pair=pair, interval=interval, day=current))
                    current += timedelta(days=1)
        
        logger.info(f"Task generation complete. Total tasks: {len(self.tasks)}")

    async def auto_fill_gaps(self, pairs: List[str], intervals: List[str], years_back: int = 3):
        """
        自动补齐逻辑：从 N 年前到现在，找出所有缺失的天并加入任务。
        """
        self._stop_signal = False
        self._pause_event.set()
        end_date = date.today()
        start_date = end_date - timedelta(days=365 * years_back)
        self.add_tasks(pairs, intervals, start_date, end_date)

    async def run(self):
        """启动调度执行"""
        if self._running:
            return
        self._running = True
        self._stop_signal = False
        self._loop = asyncio.get_running_loop() # 捕获当前 loop
        
        semaphore = asyncio.Semaphore(self.max_workers)
        
        # 定义待处理任务
        pending_tasks = [t for t in self.tasks if t.status == "pending"]
        
        async def worker(task: LakeDownloadTask):
            if self._stop_signal: return
            
            # 💡 暂停检查点
            await self._pause_event.wait()
            
            async with semaphore:
                if self._stop_signal: return
                await self._pause_event.wait() # 进入临界区前再次检查
                
                # 寻找空槽位分配 Proxy (模拟 logic)
                slot_idx = -1
                for i in range(self.max_workers):
                    if self._slots[i] is None:
                        self._slots[i] = f"S{i+1}"
                        slot_idx = i
                        break
                
                if slot_idx != -1: 
                    task.proxy_id = self._slots[slot_idx]
                    try:
                        await self.downloader.download_day(task)
                    finally:
                        self._slots[slot_idx] = None

        if pending_tasks:
            await asyncio.gather(*(worker(t) for t in pending_tasks))
        self._running = False

    def get_progress(self) -> Dict:
        """获取综合进度"""
        if not self.tasks:
            return {"total": 0, "completed": 0, "failed": 0, "percent": 0, "details": {}}

        total = len(self.tasks)
        completed = len([t for t in self.tasks if t.status == "completed"])
        failed = len([t for t in self.tasks if t.status == "failed"])
        downloading = len([t for t in self.tasks if t.status == "downloading"])
        
        # 详细进度 (按 pair-interval 分组)
        details = {}
        for t in self.tasks:
            key = f"{t.trading_pair}:{t.interval}"
            if key not in details:
                details[key] = {"total": 0, "completed": 0, "failed": 0, "downloading": 0, "error": None}
            
            details[key]["total"] += 1
            if t.status == "completed": details[key]["completed"] += 1
            elif t.status == "failed": 
                details[key]["failed"] += 1
                if t.error and not details[key]["error"]:
                    details[key]["error"] = t.error # 记录该组任务遇到的第一个错误
            elif t.status == "downloading": details[key]["downloading"] += 1

        # 计算详细进度的百分比
        for k in details:
            total_k = details[k]["total"]
            details[k]["percent"] = (details[k]["completed"] / total_k * 100) if total_k > 0 else 0

        return {
            "total": total,
            "completed": completed,
            "failed": failed,
            "downloading": downloading,
            "percent": (completed / total * 100) if total > 0 else 0,
            "details": details
        }
