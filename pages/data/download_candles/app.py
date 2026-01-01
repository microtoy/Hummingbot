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
    from concurrent.futures import ThreadPoolExecutor, as_completed
    
    start_datetime = datetime.combine(start_date, time.min)
    end_datetime = datetime.combine(end_date, time.max)
    
    st.info("💡 增量下载：系统会检查本地缓存，只下载缺失的部分数据。")
    
    # Create UI elements for live updates
    progress_bar = st.progress(0, text="Starting parallel sync...")
    status_table = st.empty()
    
    # Track status for each coin
    coin_status = {pair: {"status": "⏳ Waiting...", "rows": "-"} for pair in TOP_10_PAIRS}
    progress_tracker = [0]
    total = len(TOP_10_PAIRS)
    
    def update_display():
        """Update the status table."""
        df_status = pd.DataFrame([
            {"Coin": pair, "Status": info["status"], "Rows": info["rows"]}
            for pair, info in coin_status.items()
        ])
        status_table.dataframe(df_status, use_container_width=True, hide_index=True)
    
    update_display()
    
    # Capture current selections
    selected_connector = connector
    selected_interval = interval
    
    # 1. Fetch CURRENT cache status to assess gaps
    try:
        current_cache = backend_api_client.backtesting.get_candles_status()
        cached_files = {f["file"]: f for f in current_cache.get("cached_files", [])}
    except Exception:
        cached_files = {}

    # Run syncs in parallel
    import asyncio
    import aiohttp
    
    # Extract config
    api_url = "http://hummingbot-api:8000"
    api_auth = aiohttp.BasicAuth("admin", "admin")
    
    try:
        if hasattr(backend_api_client, "base_url"):
            api_url = backend_api_client.base_url
        if hasattr(backend_api_client, "auth") and backend_api_client.auth:
            api_auth = aiohttp.BasicAuth(backend_api_client.auth.login, backend_api_client.auth.password)
    except Exception:
        pass

    async def sync_single_coin(session: aiohttp.ClientSession, pair: str, sem: asyncio.Semaphore, c_val: str, i_val: str):
        """Sync a single coin with smart gap detection."""
        try:
            target_start = int(start_datetime.timestamp())
            target_end = int(end_datetime.timestamp())
            
            # Check if we have this file in cache
            filename = f"{c_val}_{pair}_{i_val}.csv"
            file_cache = cached_files.get(filename)
            
            # Identify Gaps
            gaps = []
            if not file_cache:
                # Log why it's downloading
                # coin_status[pair]["status"] = "🔍 No Cache File found"
                gaps.append((target_start, target_end))
            else:
                c_start = int(file_cache["start"])
                c_end = int(file_cache["end"])
                
                # Check for prefix gap (User wants data before what we have)
                # Use 24h tolerance to avoid timezone issues
                if target_start < c_start - 86400: 
                    gaps.append((target_start, c_start))
                
                # Check for suffix gap (User wants data after what we have)
                effective_now = int(datetime.now().timestamp()) - 120
                real_target_end = min(target_end, effective_now)
                
                # Use 1h tolerance for suffix
                if real_target_end > c_end + 3600: 
                    gaps.append((c_end, real_target_end))
                
                # CHECK FOR INTERNAL HOLES (Stitching interrupted downloads)
                if "holes" in file_cache and file_cache["holes"]:
                    for hole in file_cache["holes"]:
                        h_start = int(hole["start"])
                        h_end = int(hole["end"])
                        # Only fill holes that intersect with our target range
                        if h_end > target_start and h_start < target_end:
                            gaps.append((max(h_start, target_start), min(h_end, target_end)))
            
            if not gaps:
                # Just show the actual row count we found
                coin_status[pair]["status"] = "✅ Cached"
                if file_cache:
                    coin_status[pair]["rows"] = f"{file_cache['count']:,}"
                update_display()
                return

            # If we are here, we are downloading. Let's show why in the first update if possible.
            # But we follow the chunking logic.
            async with sem:
                # Add a brief diagnostic if downloading
                # if file_cache:
                #     print(f"DEBUG: {pair} Need Download. Cache: {c_start}-{c_end}, Target: {target_start}-{target_end}")
                total_duration = sum(g_end - g_start for g_start, g_end in gaps)
                duration_synced = 0
                last_rows = file_cache["count"] if file_cache else 0
                
                # Each coin might have 1 or 2 gaps (prefix and/or suffix)
                for g_start, g_end in gaps:
                    # Dynamic chunk: 30% of gap duration, min 5 days (in seconds)
                    gap_duration = g_end - g_start
                    CHUNK_SIZE = max(5 * 24 * 3600, int(gap_duration * 0.3))
                    curr = g_start
                    
                    while curr < g_end:
                        chunk_end = min(curr + CHUNK_SIZE, g_end)
                        
                        # Real-time UI progress based on total gap duration
                        pct = int((duration_synced + (curr - g_start)) / total_duration * 100)
                        d1 = datetime.fromtimestamp(curr).strftime('%m/%d')
                        d2 = datetime.fromtimestamp(chunk_end).strftime('%m/%d')
                        coin_status[pair]["status"] = f"📥 {d1}→{d2} ({pct}%)"
                        update_display()
                        
                        payload = {
                            "start_time": curr, "end_time": chunk_end,
                            "backtesting_resolution": i_val, "trade_cost": 0.0006,
                            "config": {
                                "controller_name": "Generic", "connector_name": c_val,
                                "trading_pair": pair, "candles_config": []
                            }
                        }
                        
                        async with session.post(f"{api_url}/backtesting/candles/sync", json=payload, timeout=600) as resp:
                            if resp.status == 200:
                                res = await resp.json()
                                if res.get("status") == "success":
                                    last_rows = res.get("rows", last_rows)
                                    coin_status[pair]["rows"] = f"{last_rows:,} 📈"
                                else:
                                    coin_status[pair]["status"] = f"❌ {str(res.get('error'))[:20]}"
                                    update_display()
                                    return
                            else:
                                coin_status[pair]["status"] = f"❌ HTTP {resp.status}"
                                update_display()
                                return
                        
                        curr = chunk_end
                    duration_synced += (g_end - g_start)

                coin_status[pair]["status"] = "✅ Done"
                coin_status[pair]["rows"] = f"{last_rows:,}"
                progress_tracker[0] += 1
                progress_bar.progress(progress_tracker[0] / total, text=f"Completed {progress_tracker[0]}/{total} coins")
                update_display()
                
        except Exception as e:
            coin_status[pair]["status"] = f"❌ {str(e)[:25]}..."
            update_display()

    async def run_parallel_sync():
        sem = asyncio.Semaphore(3)
        async with aiohttp.ClientSession(auth=api_auth, timeout=aiohttp.ClientTimeout(total=900)) as session:
            tasks = [sync_single_coin(session, pair, sem, selected_connector, selected_interval) for pair in TOP_10_PAIRS]
            await asyncio.gather(*tasks)

    # Run the async loop
    asyncio.run(run_parallel_sync())
    
    # Final summary
    success_count = sum(1 for info in coin_status.values() if "✅" in info["status"])
    progress_bar.progress(1.0, text=f"✅ Sync Finished! {success_count}/{total} ready.")
    st.success(f"🎉 全部增量同步完成！成功: {success_count}/{total}")



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
