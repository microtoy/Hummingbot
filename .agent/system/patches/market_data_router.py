import asyncio
import time

from fastapi import APIRouter, Request, HTTPException, Depends, Query
from hummingbot.data_feed.candles_feed.data_types import HistoricalCandlesConfig, CandlesConfig
from hummingbot.data_feed.candles_feed.candles_factory import CandlesFactory

from models.market_data import CandlesConfigRequest
from services.market_data_feed_manager import MarketDataFeedManager
from models import (
    PriceRequest, PricesResponse, FundingInfoRequest, FundingInfoResponse,
    OrderBookRequest, OrderBookResponse, OrderBookLevel,
    VolumeForPriceRequest, PriceForVolumeRequest, QuoteVolumeForPriceRequest,
    PriceForQuoteVolumeRequest, VWAPForVolumeRequest, OrderBookQueryResult
)
from deps import get_market_data_feed_manager

router = APIRouter(tags=["Market Data"], prefix="/market-data")


@router.post("/candles")
async def get_candles(request: Request, candles_config: CandlesConfigRequest):
    """Get real-time candles data for a specific trading pair."""
    try:
        market_data_feed_manager: MarketDataFeedManager = request.app.state.market_data_feed_manager
        candles_cfg = CandlesConfig(
            connector=candles_config.connector_name, trading_pair=candles_config.trading_pair,
            interval=candles_config.interval, max_records=candles_config.max_records)
        candles_feed = market_data_feed_manager.get_candles_feed(candles_cfg)
        
        while not candles_feed.ready:
            await asyncio.sleep(0.1)
        
        df = candles_feed.candles_df
        if df is not None and not df.empty:
            df = df.tail(candles_config.max_records)
            df = df.drop_duplicates(subset=["timestamp"], keep="last")
            return df.to_dict(orient="records")
        else:
            return {"error": "No candles data available"}
    except Exception as e:
        return {"error": str(e)}


@router.post("/historical-candles")
async def get_historical_candles_post(request: Request, config: HistoricalCandlesConfig):
    """Get historical candles data (POST method)."""
    return await _get_historical_candles_impl(request, config)


@router.get("/historical-candles")
async def get_historical_candles_get(
    request: Request,
    connector_name: str = Query(...),
    trading_pair: str = Query(...),
    interval: str = Query("1m"),
    start_time: int = Query(...),
    end_time: int = Query(...)
):
    """
    Get historical candles data (GET method).
    This endpoint supports the dashboard's default GET requests.
    """
    config = HistoricalCandlesConfig(
        connector_name=connector_name,
        trading_pair=trading_pair,
        interval=interval,
        start_time=start_time,
        end_time=end_time
    )
    return await _get_historical_candles_impl(request, config)


async def _get_historical_candles_impl(request: Request, config: HistoricalCandlesConfig):
    """Shared implementation for historical candles retrieval."""
    try:
        market_data_feed_manager: MarketDataFeedManager = request.app.state.market_data_feed_manager
        
        candles_config = CandlesConfig(
            connector=config.connector_name,
            trading_pair=config.trading_pair,
            interval=config.interval
        )
        
        candles = market_data_feed_manager.get_candles_feed(candles_config)
        historical_data = await candles.get_historical_candles(config=config)
        
        if historical_data is not None and not historical_data.empty:
            return historical_data.to_dict(orient="records")
        else:
            return {"error": "No historical data available"}
    except Exception as e:
        return {"error": str(e)}


# ===== Keeping remaining endpoints from original file =====

@router.post("/prices", response_model=PricesResponse)
async def get_prices(
    request: PriceRequest,
    market_data_manager: MarketDataFeedManager = Depends(get_market_data_feed_manager)
):
    try:
        prices = await market_data_manager.get_prices(
            request.connector_name, 
            request.trading_pairs
        )
        
        if "error" in prices:
            raise HTTPException(status_code=500, detail=prices["error"])
            
        return PricesResponse(
            connector=request.connector_name,
            prices=prices["prices"],
            timestamp=prices["timestamp"]
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching prices: {str(e)}")


@router.post("/funding-info", response_model=FundingInfoResponse)
async def get_funding_info(
    request: FundingInfoRequest,
    market_data_manager: MarketDataFeedManager = Depends(get_market_data_feed_manager)
):
    try:
        if "_perpetual" not in request.connector_name.lower():
            raise HTTPException(status_code=400, detail="Funding info is only available for perpetual trading pairs.")
        funding_info = await market_data_manager.get_funding_info(
            request.connector_name, 
            request.trading_pair
        )
        
        if "error" in funding_info:
            if "not supported" in funding_info["error"]:
                raise HTTPException(status_code=400, detail=funding_info["error"])
            else:
                raise HTTPException(status_code=500, detail=funding_info["error"])
            
        return FundingInfoResponse(**funding_info)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching funding info: {str(e)}")


@router.post("/order-book", response_model=OrderBookResponse)
async def get_order_book(
    request: OrderBookRequest,
    market_data_manager: MarketDataFeedManager = Depends(get_market_data_feed_manager)
):
    try:
        order_book_data = await market_data_manager.get_order_book_data(
            request.connector_name,
            request.trading_pair,
            request.depth
        )
        
        if "error" in order_book_data:
            raise HTTPException(status_code=500, detail=order_book_data["error"])
            
        bids = [OrderBookLevel(price=bid[0], amount=bid[1]) for bid in order_book_data["bids"]]
        asks = [OrderBookLevel(price=ask[0], amount=ask[1]) for ask in order_book_data["asks"]]
        
        return OrderBookResponse(
            trading_pair=order_book_data["trading_pair"],
            bids=bids,
            asks=asks,
            timestamp=order_book_data["timestamp"]
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching order book: {str(e)}")


@router.post("/order-book/price-for-volume", response_model=OrderBookQueryResult)
async def get_price_for_volume(
    request: PriceForVolumeRequest,
    market_data_manager: MarketDataFeedManager = Depends(get_market_data_feed_manager)
):
    try:
        result = await market_data_manager.get_order_book_query_result(
            request.connector_name,
            request.trading_pair,
            request.is_buy,
            volume=request.volume
        )
        if "error" in result:
            raise HTTPException(status_code=500, detail=result["error"])
        return OrderBookQueryResult(**result)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error in order book query: {str(e)}")


@router.post("/order-book/volume-for-price", response_model=OrderBookQueryResult)
async def get_volume_for_price(
    request: VolumeForPriceRequest,
    market_data_manager: MarketDataFeedManager = Depends(get_market_data_feed_manager)
):
    try:
        result = await market_data_manager.get_order_book_query_result(
            request.connector_name,
            request.trading_pair,
            request.is_buy,
            price=request.price
        )
        if "error" in result:
            raise HTTPException(status_code=500, detail=result["error"])
        return OrderBookQueryResult(**result)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error in order book query: {str(e)}")


@router.post("/order-book/price-for-quote-volume", response_model=OrderBookQueryResult)
async def get_price_for_quote_volume(
    request: PriceForQuoteVolumeRequest,
    market_data_manager: MarketDataFeedManager = Depends(get_market_data_feed_manager)
):
    try:
        result = await market_data_manager.get_order_book_query_result(
            request.connector_name,
            request.trading_pair,
            request.is_buy,
            quote_volume=request.quote_volume
        )
        if "error" in result:
            raise HTTPException(status_code=500, detail=result["error"])
        return OrderBookQueryResult(**result)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error in order book query: {str(e)}")


@router.post("/order-book/quote-volume-for-price", response_model=OrderBookQueryResult)
async def get_quote_volume_for_price(
    request: QuoteVolumeForPriceRequest,
    market_data_manager: MarketDataFeedManager = Depends(get_market_data_feed_manager)
):
    try:
        result = await market_data_manager.get_order_book_query_result(
            request.connector_name,
            request.trading_pair,
            request.is_buy,
            quote_price=request.price
        )
        if "error" in result:
            raise HTTPException(status_code=500, detail=result["error"])
        return OrderBookQueryResult(**result)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error in order book query: {str(e)}")


@router.post("/order-book/vwap-for-volume", response_model=OrderBookQueryResult)
async def get_vwap_for_volume(
    request: VWAPForVolumeRequest,
    market_data_manager: MarketDataFeedManager = Depends(get_market_data_feed_manager)
):
    try:
        result = await market_data_manager.get_order_book_query_result(
            request.connector_name,
            request.trading_pair,
            request.is_buy,
            vwap_volume=request.volume
        )
        if "error" in result:
            raise HTTPException(status_code=500, detail=result["error"])
        return OrderBookQueryResult(**result)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error in order book query: {str(e)}")
