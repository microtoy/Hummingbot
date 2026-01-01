from typing import Optional, List, Dict, Any
import aiohttp
from .routers import (
    AccountsRouter,
    ArchivedBotsRouter,
    BacktestingRouter,
    BotOrchestrationRouter,
    ConnectorsRouter,
    ControllersRouter,
    DockerRouter,
    MarketDataRouter,
    PortfolioRouter,
    ScriptsRouter,
    TradingRouter
)

class PatchedBacktestingRouter(BacktestingRouter):
    """Extended BacktestingRouter with cache management features."""
    
    async def get_candles_status(self) -> Dict[str, Any]:
        """Get the status of cached candles on the server."""
        return await self._get("/backtesting/candles/status")

    async def sync_candles(
        self,
        start_time: int,
        end_time: int,
        backtesting_resolution: str = "1m",
        trade_cost: float = 0.0006,
        config: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Prefetch and cache candles on the server."""
        payload = {
            "start_time": start_time,
            "end_time": end_time,
            "backtesting_resolution": backtesting_resolution,
            "trade_cost": trade_cost,
            "config": config or {}
        }
        return await self._post("/backtesting/candles/sync", json=payload)


class PatchedMarketDataRouter(MarketDataRouter):
    """Extended MarketDataRouter with increased timeout for historical candles."""
    
    async def get_historical_candles(
        self,
        connector_name: str,
        trading_pair: str,
        interval: str = "1m",
        start_time: Optional[int] = None,
        end_time: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """Get historical candles with a generous timeout."""
        params = {
            "connector_name": connector_name,
            "trading_pair": trading_pair,
            "interval": interval,
            "start_time": start_time,
            "end_time": end_time
        }
        # Explicitly use a long timeout for this specific call if needed, 
        # though the session timeout should handle it now.
        return await self._get("/market-data/historical-candles", params=params)


class HummingbotAPIClient:
    def __init__(
        self, 
        base_url: str = "http://localhost:8000",
        username: str = "admin",
        password: str = "admin",
        timeout: Optional[aiohttp.ClientTimeout] = None
    ):
        self.base_url = base_url.rstrip('/')
        self.auth = aiohttp.BasicAuth(username, password)
        # Increase default timeout for operations like historical candles
        # 30 minutes (1800s) should be plenty for 1 year of 1m data
        self.timeout = timeout or aiohttp.ClientTimeout(total=1800)
        self._session: Optional[aiohttp.ClientSession] = None
        self._accounts: Optional[AccountsRouter] = None
        self._archived_bots: Optional[ArchivedBotsRouter] = None
        self._backtesting: Optional[BacktestingRouter] = None
        self._bot_orchestration: Optional[BotOrchestrationRouter] = None
        self._connectors: Optional[ConnectorsRouter] = None
        self._controllers: Optional[ControllersRouter] = None
        self._docker: Optional[DockerRouter] = None
        self._market_data: Optional[MarketDataRouter] = None
        self._portfolio: Optional[PortfolioRouter] = None
        self._scripts: Optional[ScriptsRouter] = None
        self._trading: Optional[TradingRouter] = None
    
    async def init(self) -> None:
        """Initialize the client session and routers."""
        if self._session is None:
            # FIX: Remove timeout from session initialization to avoid "Timeout context manager" error
            # in Streamlit's async/sync hybrid environment.
            # We will use the timeout in the individual requests if needed, 
            # or rely on the fact that we've set a large default in the class.
            self._session = aiohttp.ClientSession(
                auth=self.auth,
                timeout=self.timeout
            )
            self._accounts = AccountsRouter(self._session, self.base_url)
            self._archived_bots = ArchivedBotsRouter(self._session, self.base_url)
            self._backtesting = PatchedBacktestingRouter(self._session, self.base_url)
            self._bot_orchestration = BotOrchestrationRouter(self._session, self.base_url)
            self._connectors = ConnectorsRouter(self._session, self.base_url)
            self._controllers = ControllersRouter(self._session, self.base_url)
            self._docker = DockerRouter(self._session, self.base_url)
            self._market_data = PatchedMarketDataRouter(self._session, self.base_url)
            self._portfolio = PortfolioRouter(self._session, self.base_url)
            self._scripts = ScriptsRouter(self._session, self.base_url)
            self._trading = TradingRouter(self._session, self.base_url)
    
    async def close(self) -> None:
        """Close the client session."""
        if self._session:
            await self._session.close()
            self._session = None
            self._accounts = None
            self._archived_bots = None
            self._backtesting = None
            self._bot_orchestration = None
            self._connectors = None
            self._controllers = None
            self._docker = None
            self._market_data = None
            self._portfolio = None
            self._scripts = None
            self._trading = None
    
    @property
    def accounts(self) -> AccountsRouter:
        """Access the accounts router."""
        if self._accounts is None:
            raise RuntimeError("Client not initialized. Call await client.init() first.")
        return self._accounts
    
    @property
    def archived_bots(self) -> ArchivedBotsRouter:
        """Access the archived bots router."""
        if self._archived_bots is None:
            raise RuntimeError("Client not initialized. Call await client.init() first.")
        return self._archived_bots
    
    @property
    def backtesting(self) -> BacktestingRouter:
        """Access the backtesting router."""
        if self._backtesting is None:
            raise RuntimeError("Client not initialized. Call await client.init() first.")
        return self._backtesting
    
    @property
    def bot_orchestration(self) -> BotOrchestrationRouter:
        """Access the bot orchestration router."""
        if self._bot_orchestration is None:
            raise RuntimeError("Client not initialized. Call await client.init() first.")
        return self._bot_orchestration
    
    @property
    def connectors(self) -> ConnectorsRouter:
        """Access the connectors router."""
        if self._connectors is None:
            raise RuntimeError("Client not initialized. Call await client.init() first.")
        return self._connectors
    
    @property
    def controllers(self) -> ControllersRouter:
        """Access the controllers router."""
        if self._controllers is None:
            raise RuntimeError("Client not initialized. Call await client.init() first.")
        return self._controllers
    
    @property
    def docker(self) -> DockerRouter:
        """Access the docker router."""
        if self._docker is None:
            raise RuntimeError("Client not initialized. Call await client.init() first.")
        return self._docker
    
    @property
    def market_data(self) -> MarketDataRouter:
        """Access the market data router."""
        if self._market_data is None:
            raise RuntimeError("Client not initialized. Call await client.init() first.")
        return self._market_data
    
    @property
    def portfolio(self) -> PortfolioRouter:
        """Access the portfolio router."""
        if self._portfolio is None:
            raise RuntimeError("Client not initialized. Call await client.init() first.")
        return self._portfolio
    
    @property
    def scripts(self) -> ScriptsRouter:
        """Access the scripts router."""
        if self._scripts is None:
            raise RuntimeError("Client not initialized. Call await client.init() first.")
        return self._scripts
    
    @property
    def trading(self) -> TradingRouter:
        """Access the trading router."""
        if self._trading is None:
            raise RuntimeError("Client not initialized. Call await client.init() first.")
        return self._trading
    
    async def __aenter__(self):
        """Async context manager entry."""
        await self.init()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.close()
