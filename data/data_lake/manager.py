import os
import threading
import asyncio
import logging
from typing import Optional, List, Dict
from datetime import date
from .storage import LakeStorage
from .downloader import LakeTaskScheduler

logger = logging.getLogger(__name__)

class LakeManager:
    """
    Data Lake V2 管理中心
    聚合存储、下载、调度等功能。
    """
    _instance: Optional['LakeManager'] = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(LakeManager, cls).__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self):
        if self._initialized:
            return
            
        self.storage = LakeStorage()
        self.scheduler = LakeTaskScheduler(self.storage, max_workers=15)
        self._last_summary = None
        self._last_summary_time = 0
        self._initialized = True

    async def get_top_pairs(self, limit: int = 100, rank_type: str = "market_cap") -> List[str]:
        """获取市场排名交易对，支持市值 (market_cap) 和成交额 (volume)"""
        from .fetcher import BinanceFetcher
        # 这里默认尝试使用宿主机代理以防 API 连接失败
        fetcher = BinanceFetcher(proxy_config="http://host.docker.internal:7890")
        if rank_type == "market_cap":
            return await fetcher.get_market_cap_pairs(limit)
        else:
            return await fetcher.get_top_trading_pairs(limit)

    def start_download(self, pairs: List[str], intervals: List[str], start_date: date, end_date: date, use_proxy: bool = False, proxy_url: str = "http://host.docker.internal:7890"):
        """
        灵活下载入口：支持多币种、多周期、任意日期。
        """
        def _bg_run():
            self.scheduler.add_tasks(pairs, intervals, start_date, end_date)
            self._run_async_tasks(use_proxy, proxy_url)
            
        threading.Thread(target=_bg_run, daemon=True).start()

    def stop_download(self):
        """强制终止下载任务"""
        self.scheduler.cancel_tasks()

    def pause_download(self):
        """暂停下载"""
        self.scheduler.pause_tasks()

    def resume_download(self):
        """恢复下载"""
        self.scheduler.resume_tasks()

    def is_paused(self) -> bool:
        """检查是否处于暂停状态"""
        return not self.scheduler._pause_event.is_set()

    def auto_fill_history(self, pairs: List[str], intervals: List[str], years: int = 3, use_proxy: bool = False, proxy_url: str = "http://host.docker.internal:7890"):
        """
        一键补齐历史数据逻辑。
        """
        def _bg_run():
            # auto_fill_gaps 现在是同步的或简单的包装
            asyncio.run(self.scheduler.auto_fill_gaps(pairs, intervals, years))
            self._run_async_tasks(use_proxy, proxy_url)
            
        threading.Thread(target=_bg_run, daemon=True).start()

    def _trigger_background_run(self, use_proxy: bool = False, proxy_url: str = "http://host.docker.internal:7890"):
        """启动后台运行"""
        threading.Thread(target=self._run_async_tasks, args=(use_proxy, proxy_url), daemon=True).start()

    async def _run_scheduler(self, use_proxy: bool = False, proxy_url: str = "http://host.docker.internal:7890"):
        """异步执行调度"""
        try:
            await self.scheduler.run(use_proxy, proxy_url)
        except Exception as e:
            logger.error(f"Scheduler failed: {e}")

    def _run_async_tasks(self, use_proxy: bool = False, proxy_url: str = "http://host.docker.internal:7890"):
        """后台运行调度任务"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            # 使用 create_task 包装
            loop.run_until_complete(self._run_scheduler(use_proxy, proxy_url))
        finally:
            loop.close()

    def get_status(self, audit: bool = False) -> Dict:
        """获取系统全面状态 (带缓存避免阻塞 UI)"""
        import time
        # 强制审计或过期
        if audit or self._last_summary is None or (time.time() - self._last_summary_time > 10):
            self._last_summary = self.storage.get_summary(fast=not audit, audit=audit)
            self._last_summary_time = time.time()
            
        return {
            "storage": self._last_summary,
            "download": self.scheduler.get_progress(),
            "slots": self.scheduler._slots
        }

def get_lake_manager() -> LakeManager:
    """获取单例实例"""
    return LakeManager()
