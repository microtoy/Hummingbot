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

c1, c2, c3, c4 = st.columns([2, 2, 2, 1.5])
with c1:
    connector = st.selectbox("Exchange",
                             ["binance_perpetual", "binance", "gate_io", "gate_io_perpetual", "kucoin", "ascend_ex"],
                             index=0)
    trading_pair = st.text_input("Trading Pair", value="BTC-USDT")
with c2:
    interval = st.selectbox("Interval", options=["1m", "3m", "5m", "15m", "1h", "4h", "1d", "1s"])
with c3:
    start_date = st.date_input("Start Date", value=datetime.now() - timedelta(days=2))
    end_date = st.date_input("End Date", value=datetime.now() - timedelta(days=1))
with c4:
    get_data_button = st.button("Get Candles! (Browser)")
    sync_to_server = st.button("Sync to Server Cache 🚀")

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
        if "error" in res:
            st.error(f"Sync failed: {res['error']}")
        else:
            st.success("✅ 数据已同步到服务器磁盘！")
            st.info("💡 **小技巧**：如果你的策略需要计算均线等指标，建议在这里同步时多选 1-2 天的数据作为 Buffer，这样回测时就能 100% 命中缓存实现秒开了。")
            st.rerun()

if get_data_button:
    start_datetime = datetime.combine(start_date, time.min)
    end_datetime = datetime.combine(end_date, time.max)
    if end_datetime < start_datetime:
        st.error("End Date should be greater than Start Date.")
        st.stop()

    candles = backend_api_client.market_data.get_historical_candles(
        connector_name=connector,
        trading_pair=trading_pair,
        interval=interval,
        start_time=int(start_datetime.timestamp()),
        end_time=int(end_datetime.timestamp())
    )

    if "error" in candles:
        st.error(candles["error"])
        st.stop()

    candles_df = pd.DataFrame(candles)
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
