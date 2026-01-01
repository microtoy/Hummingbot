from datetime import datetime, time, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from frontend.st_utils import get_backend_api_client, initialize_st_page

# Initialize Streamlit page
initialize_st_page(title="Download Candles", icon="💾")
backend_api_client = get_backend_api_client()

# Sidebar: Server Cache Status
st.sidebar.write("### 🖥️ Server Cache Status")
try:
    cache_status = backend_api_client.backtesting.get_candles_status()
    if "cached_files" in cache_status and len(cache_status["cached_files"]) > 0:
        for f in cache_status["cached_files"]:
            start_dt = datetime.fromtimestamp(f["start"]).strftime('%Y-%m-%d %H:%M')
            end_dt = datetime.fromtimestamp(f["end"]).strftime('%Y-%m-%d %H:%M')
            with st.sidebar.expander(f"📦 {f['file']}"):
                st.write(f"**Rows:** {f['count']:,}")
                st.write(f"**From:** {start_dt}")
                st.write(f"**To:** {end_dt}")
    else:
        st.sidebar.info("Cache is empty.")
except Exception as e:
    st.sidebar.error("Ready to sync to server.")

# Top 10 coins by market cap (Jan 2026, excluding stablecoins)
TOP_10_PAIRS = ["BTC-USDT", "ETH-USDT", "BNB-USDT", "XRP-USDT", "SOL-USDT", "TRX-USDT", "DOGE-USDT", "ADA-USDT", "AVAX-USDT", "LINK-USDT"]

c1, c2, c3, c4 = st.columns([2, 2, 2, 1.5])
with c1:
    connector = st.selectbox("Exchange",
                             ["binance", "binance_perpetual", "gate_io", "gate_io_perpetual", "kucoin", "ascend_ex"],
                             index=0)
    trading_pair = st.text_input("Trading Pair", value="BTC-USDT")
with c2:
    interval = st.selectbox("Interval", options=["1m", "3m", "5m", "15m", "1h", "4h", "1d", "1s"])
with c3:
    start_date = st.date_input("Start Date", value=datetime.now() - timedelta(days=7))
    end_date = st.date_input("End Date", value=datetime.now() - timedelta(days=1))
with c4:
    get_data_button = st.button("Get Candles! (Browser)")
    sync_to_server = st.button("Sync to Server Cache 🚀")
    sync_top10 = st.button("🔥 Sync Top 10 Coins")

if sync_to_server:
    start_datetime = datetime.combine(start_date, time.min)
    end_datetime = datetime.combine(end_date, time.max)
    with st.spinner("Syncing candles to server cache..."):
        dummy_config = {
            "controller_name": "Generic",
            "connector_name": connector,
            "trading_pair": trading_pair,
            "candles_config": []
        }
        res = backend_api_client.backtesting.sync_candles(
            start_time=int(start_datetime.timestamp()),
            end_time=int(end_datetime.timestamp()),
            backtesting_resolution=interval,
            config=dummy_config
        )
        
        if interval != "1h":
            backend_api_client.backtesting.sync_candles(
                start_time=int(start_datetime.timestamp()),
                end_time=int(end_datetime.timestamp()),
                backtesting_resolution="1h",
                config=dummy_config
            )
            
        if "error" in res:
            st.error(f"Sync failed: {res['error']}")
        else:
            st.success("✅ 数据同步完成 (包括预热 1h 间隔)！")
            st.rerun()

if sync_top10:
    start_datetime = datetime.combine(start_date, time.min)
    end_datetime = datetime.combine(end_date, time.max)
    
    st.info("� 增量下载：系统会检查本地缓存，只下载缺失的部分数据。首次下载较慢，后续只需更新增量。")
    
    progress_bar = st.progress(0, text="Preparing...")
    status_container = st.container()
    
    success_count = 0
    total = len(TOP_10_PAIRS)
    
    for i, pair in enumerate(TOP_10_PAIRS):
        progress_bar.progress((i) / total, text=f"🔄 Syncing {pair}... ({i+1}/{total})")
        
        try:
            config = {
                "controller_name": "Generic",
                "connector_name": connector,
                "trading_pair": pair,
                "candles_config": []
            }
            result = backend_api_client.backtesting.sync_candles(
                start_time=int(start_datetime.timestamp()),
                end_time=int(end_datetime.timestamp()),
                backtesting_resolution=interval,
                config=config
            )
            
            if result.get("status") == "success":
                status_container.write(f"✅ {pair} - {result.get('message', 'Done')}")
                success_count += 1
            else:
                status_container.write(f"❌ {pair} - {result.get('error', 'Unknown error')}")
        except Exception as e:
            status_container.write(f"❌ {pair} - {e}")
    
    progress_bar.progress(1.0, text=f"✅ Complete! {success_count}/{total} succeeded")
    st.success(f"🎉 Top 10 同步完成！成功: {success_count}/{total}")


if get_data_button:
    start_datetime = datetime.combine(start_date, time.min)
    end_datetime = datetime.combine(end_date, time.max)
    if end_datetime < start_datetime:
        st.error("End Date should be greater than Start Date.")
        st.stop()

    # ======= Chunked Download with Progress Bar =======
    total_days = (end_date - start_date).days + 1
    all_candles = []
    
    progress_bar = st.progress(0, text="Preparing download...")
    status_text = st.empty()
    
    for i, day_offset in enumerate(range(total_days)):
        current_day = start_date + timedelta(days=day_offset)
        chunk_start = datetime.combine(current_day, time.min)
        chunk_end = datetime.combine(current_day, time.max)
        
        # Update progress
        progress = (i + 1) / total_days
        progress_bar.progress(progress, text=f"Downloading {current_day.strftime('%Y-%m-%d')}...")
        status_text.text(f"📥 Fetching day {i+1}/{total_days}: {current_day.strftime('%Y-%m-%d')}")
        
        try:
            chunk = backend_api_client.market_data.get_historical_candles(
                connector_name=connector,
                trading_pair=trading_pair,
                interval=interval,
                start_time=int(chunk_start.timestamp()),
                end_time=int(chunk_end.timestamp())
            )
            
            if isinstance(chunk, list) and len(chunk) > 0:
                all_candles.extend(chunk)
            elif isinstance(chunk, dict) and "error" in chunk:
                st.warning(f"⚠️ Day {current_day}: {chunk['error']}")
        except Exception as e:
            st.warning(f"⚠️ Failed to fetch {current_day}: {str(e)}")
            continue
    
    progress_bar.progress(1.0, text="Download complete!")
    status_text.text(f"✅ Downloaded {len(all_candles):,} candles across {total_days} days.")
    
    if len(all_candles) == 0:
        st.error("No data retrieved. Please check your parameters.")
        st.stop()
    
    candles_df = pd.DataFrame(all_candles)
    candles_df = candles_df.drop_duplicates(subset=["timestamp"], keep="last")
    candles_df = candles_df.sort_values("timestamp")
    candles_df.index = pd.to_datetime(candles_df["timestamp"], unit='s')

    # Plotting the candlestick chart
    fig = go.Figure(data=[go.Candlestick(
        x=candles_df.index,
        open=candles_df['open'],
        high=candles_df['high'],
        low=candles_df['low'],
        close=candles_df['close']
    )])
    fig.update_layout(
        height=800,
        title=f"{trading_pair} Candlesticks",
        xaxis_title="Time",
        yaxis_title="Price",
        template="plotly_dark",
        showlegend=False
    )
    fig.update_xaxes(rangeslider_visible=False)
    fig.update_yaxes(title_text="Price")
    st.plotly_chart(fig, use_container_width=True)

    # Generating CSV and download button
    csv = candles_df.to_csv(index=False)
    filename = f"{connector}_{trading_pair}_{start_date.strftime('%Y%m%d')}_{end_date.strftime('%Y%m%d')}.csv"
    st.download_button(
        label="Download CSV to Browser",
        data=csv,
        file_name=filename,
        mime='text/csv',
    )
